"""
FastAPI REST API routes for TriageGate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import LogWindow, Incident, CostMetric
from app.schemas import (
    CostMetricResponse,
    InjectIncidentRequest,
    InjectIncidentResponse,
    SimulatorStatus,
    StatusResponse,
    RecentDataResponse,
    WindowResult,
    IncidentResponse,
    HealthResponse,
)
from app.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_or_create_metrics(db: AsyncSession) -> CostMetric:
    result = await db.execute(select(CostMetric).limit(1))
    metric = result.scalar_one_or_none()
    if metric is None:
        metric = CostMetric()
        db.add(metric)
        await db.commit()
        await db.refresh(metric)
    return metric


async def _build_cost_response(db: AsyncSession) -> CostMetricResponse:
    metric = await _get_or_create_metrics(db)
    triage = app_state.triage_service
    llm_calls_avoided = metric.llm_calls_avoided + triage.llm_calls_avoided
    llm_calls_made = metric.llm_calls_made + triage.llm_calls_made
    total_avoided = llm_calls_avoided
    total_calls = total_avoided + llm_calls_made
    cost_reduction_pct = round(total_avoided / total_calls * 100, 1) if total_calls > 0 else 0.0
    return CostMetricResponse(
        total_logs_processed=metric.total_logs_processed,
        windows_evaluated=metric.windows_evaluated,
        anomalies_detected=metric.anomalies_detected,
        llm_calls_made=llm_calls_made,
        llm_calls_avoided=total_avoided,
        duplicate_suppressed_count=metric.duplicate_suppressed_count + triage.duplicate_suppressed_count,
        estimated_cost_usd=round(metric.estimated_cost_usd + triage.estimated_cost_usd, 6),
        estimated_cost_saved_usd=round(metric.estimated_cost_saved_usd, 4),
        cost_reduction_percent=cost_reduction_pct,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(func.count()).select_from(LogWindow))
        db_ok = True
    except Exception:
        db_ok = False
    return HealthResponse(
        status="ok",
        tier1_model_fitted=app_state.detector.is_fitted,
        db_connected=db_ok,
    )


@router.get("/status", response_model=StatusResponse)
async def status(db: AsyncSession = Depends(get_db)):
    sim = app_state.simulator
    metrics = await _build_cost_response(db)
    return StatusResponse(
        status="running",
        version="1.0.0",
        simulator=SimulatorStatus(
            running=sim.running if sim else False,
            logs_per_second=sim.logs_per_second if sim else 0.0,
            active_scenario=sim.active_scenario if sim else None,
        ),
        metrics=metrics,
    )


@router.get("/metrics", response_model=CostMetricResponse)
async def get_metrics(db: AsyncSession = Depends(get_db)):
    return await _build_cost_response(db)


@router.get("/windows", response_model=list[WindowResult])
async def get_windows(
    limit: int = 50,
    anomalies_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(LogWindow).order_by(LogWindow.id.desc()).limit(limit)
    if anomalies_only:
        stmt = select(LogWindow).where(LogWindow.is_anomaly.is_(True)).order_by(LogWindow.id.desc()).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [WindowResult.model_validate(r) for r in reversed(rows)]


@router.get("/incidents", response_model=list[IncidentResponse])
async def get_incidents(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).order_by(Incident.id.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [IncidentResponse.model_validate(r) for r in reversed(rows)]


@router.get("/recent", response_model=RecentDataResponse)
async def get_recent(
    window_limit: int = 60,
    incident_limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    win_result = await db.execute(
        select(LogWindow).order_by(LogWindow.id.desc()).limit(window_limit)
    )
    windows = [WindowResult.model_validate(r) for r in reversed(win_result.scalars().all())]

    inc_result = await db.execute(
        select(Incident).order_by(Incident.id.desc()).limit(incident_limit)
    )
    incidents = [IncidentResponse.model_validate(r) for r in reversed(inc_result.scalars().all())]

    return RecentDataResponse(windows=windows, incidents=incidents)


@router.post("/simulator/start")
async def simulator_start():
    sim = app_state.simulator
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulator not initialised.")
    if not sim.running:
        sim.start()
    return {"running": True}


@router.post("/simulator/stop")
async def simulator_stop():
    sim = app_state.simulator
    if sim and sim.running:
        sim.stop()
    return {"running": False}


@router.post("/inject", response_model=InjectIncidentResponse)
async def inject_incident(req: InjectIncidentRequest):
    sim = app_state.simulator
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulator not initialised.")
    if not sim.running:
        sim.start()   # auto-start if stopped
    sim.inject_scenario(req.scenario.value, duration_seconds=req.duration_seconds)
    return InjectIncidentResponse(
        scenario=req.scenario.value,
        message=f"Scenario '{req.scenario.value}' injected for {req.duration_seconds}s.",
        duration_seconds=req.duration_seconds,
    )


@router.delete("/reset")
async def reset_state(db: AsyncSession = Depends(get_db)):
    """Clear all stored data and reset metrics. Useful for demos."""
    await db.execute(LogWindow.__table__.delete())
    await db.execute(Incident.__table__.delete())
    await db.execute(CostMetric.__table__.delete())
    await db.commit()
    app_state.triage_service.reset_metrics()
    app_state.log_buffer.clear()
    return {"reset": True}
