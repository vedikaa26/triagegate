"""
FastAPI entry point for TriageGate.

Lifespan:
  1. Init DB tables.
  2. Fit IsolationForest on synthetic healthy baseline.
  3. Start log simulator background task.
  4. Start window processing loop (5-second tumbling windows).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import dotenv
dotenv.load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db, AsyncSessionLocal
from app.models import LogWindow, Incident, CostMetric
from app.ml.detector import detector
from app.services.triage import triage_service, build_signature, COST_PER_CALL
from app.services.simulator import LogSimulator
from app.state import app_state
from app.api.routes import router

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WINDOW_SECONDS: int = int(os.getenv("WINDOW_SECONDS", "5"))


# ---------------------------------------------------------------------------
# Window processing coroutine
# ---------------------------------------------------------------------------

async def _process_windows() -> None:
    """
    Every WINDOW_SECONDS, drain the log buffer, extract features,
    run Tier-1 detection, and conditionally trigger Tier-2 triage.
    """
    from sqlalchemy import select

    while True:
        await asyncio.sleep(WINDOW_SECONDS)

        window_start = datetime.now(timezone.utc)
        # Atomically drain the buffer
        batch = list(app_state.log_buffer)
        app_state.log_buffer.clear()

        if not batch:
            continue

        window_end = datetime.now(timezone.utc)
        app_state.total_logs_processed += len(batch)

        # --- Tier-1 ---
        anomaly_result = detector.score(batch)
        app_state.windows_evaluated += 1

        async with AsyncSessionLocal() as db:
            # Persist window
            win = LogWindow(
                window_start=window_start,
                window_end=window_end,
                log_count=anomaly_result.features.log_count,
                error_rate=anomaly_result.features.error_rate,
                p95_latency_ms=anomaly_result.features.p95_latency_ms,
                status_5xx_count=anomaly_result.features.status_5xx_count,
                unique_error_templates=anomaly_result.features.unique_error_templates,
                dominant_service=anomaly_result.dominant_service,
                is_anomaly=anomaly_result.is_anomaly,
                isolation_forest_score=anomaly_result.score,
                tier1_latency_ms=anomaly_result.tier1_latency_ms,
                anomaly_reason="; ".join(anomaly_result.reasons)[:256] if anomaly_result.reasons else None,
            )
            db.add(win)
            await db.flush()   # get win.id

            if anomaly_result.is_anomaly:
                app_state.anomalies_detected += 1

                # Grab top error logs for the prompt
                from app.schemas import RawLogEntry as Schema_RawLogEntry
                error_logs = [
                    l for l in batch
                    if l.level.upper() in {"ERROR", "CRITICAL"}
                ][:5]

                # --- Tier-2 ---
                scenario_hint = app_state.simulator.active_scenario if app_state.simulator else None
                report, model, llm_latency, is_mock, was_dedup = await triage_service.triage(
                    anomaly_result,
                    error_logs,
                    window_start,
                    scenario_hint=scenario_hint,
                )

                if not was_dedup and report is not None:
                    incident = Incident(
                        signature_hash=build_signature(
                            anomaly_result.dominant_service or "unknown",
                            anomaly_result.top_error_template,
                        ),
                        affected_service=report.affected_service,
                        top_error_template=anomaly_result.top_error_template,
                        window_id=win.id,
                        severity=report.severity.value,
                        root_cause=report.root_cause,
                        suggested_remediation=report.suggested_remediation,
                        confidence_score=report.confidence_score,
                        llm_model_used=model,
                        llm_latency_ms=llm_latency,
                        is_mock=is_mock,
                        was_deduplicated=False,
                    )
                    db.add(incident)
                    await db.flush()
                    win.incident_id = incident.id
                elif was_dedup:
                    # Record suppressed duplicate
                    dup_incident = Incident(
                        signature_hash="dedup-suppressed",
                        affected_service=anomaly_result.dominant_service or "unknown",
                        top_error_template=anomaly_result.top_error_template,
                        window_id=win.id,
                        severity=None,
                        root_cause=None,
                        suggested_remediation=None,
                        confidence_score=None,
                        llm_model_used=None,
                        llm_latency_ms=0.0,
                        is_mock=False,
                        was_deduplicated=True,
                    )
                    db.add(dup_incident)

            # Update global metrics
            result_m = await db.execute(select(CostMetric).limit(1))
            metric = result_m.scalar_one_or_none()
            if metric is None:
                metric = CostMetric()
                db.add(metric)

            metric.total_logs_processed = app_state.total_logs_processed
            metric.windows_evaluated = app_state.windows_evaluated
            metric.anomalies_detected = app_state.anomalies_detected
            metric.llm_calls_made = triage_service.llm_calls_made
            metric.llm_calls_avoided = triage_service.llm_calls_avoided
            metric.duplicate_suppressed_count = triage_service.duplicate_suppressed_count
            metric.estimated_cost_usd = triage_service.estimated_cost_usd

            # Cost saved = windows that were healthy (no Tier-2 needed) × cost/call
            healthy_windows = app_state.windows_evaluated - app_state.anomalies_detected
            metric.estimated_cost_saved_usd = round(
                (healthy_windows + triage_service.llm_calls_avoided) * COST_PER_CALL, 6
            )

            await db.commit()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("TriageGate starting up…")
    await init_db()

    # Wire singletons into app_state
    app_state.detector = detector
    app_state.triage_service = triage_service

    # Fit Tier-1 model
    detector.fit()

    # Start simulator
    def _on_log(log_entry):
        app_state.log_buffer.append(log_entry)

    sim = LogSimulator(log_callback=_on_log)
    sim.start()
    app_state.simulator = sim

    # Start window processor
    window_task = asyncio.create_task(_process_windows(), name="window-processor")

    logger.info("TriageGate ready. Simulator running, Tier-1 model fitted.")
    yield

    # --- Shutdown ---
    sim.stop()
    window_task.cancel()
    try:
        await window_task
    except asyncio.CancelledError:
        pass
    logger.info("TriageGate shutdown complete.")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TriageGate",
    description="Cost-Aware Log Anomaly Detection & Root-Cause Triage System",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {"service": "TriageGate", "version": "1.0.0", "docs": "/docs"}
