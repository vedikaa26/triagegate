"""
Tier-2 LLM Root-Cause Triage Service.

Responsibilities:
  1. Deduplication gate: suppress duplicate incident signatures within cooldown.
  2. Build a compressed, structured prompt from the anomalous window.
  3. Call Gemini (or OpenAI-compatible) API, enforce JSON output.
  4. Fall back to a scenario-aware mock if no API key is configured.
  5. Record cost metrics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from app.schemas import LLMTriageReport, SeverityLevel, RawLogEntry, WindowFeatures
from app.ml.detector import AnomalyResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEDUP_COOLDOWN_SECONDS: int = int(os.getenv("DEDUP_COOLDOWN_SECONDS", "60"))
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
AVG_INPUT_TOKENS: int = int(os.getenv("AVG_INPUT_TOKENS_PER_CALL", "800"))
AVG_OUTPUT_TOKENS: int = int(os.getenv("AVG_OUTPUT_TOKENS_PER_CALL", "200"))
INPUT_COST_PER_1M: float = float(os.getenv("LLM_INPUT_COST_PER_1M", "0.075"))
OUTPUT_COST_PER_1M: float = float(os.getenv("LLM_OUTPUT_COST_PER_1M", "0.30"))

COST_PER_CALL = (AVG_INPUT_TOKENS * INPUT_COST_PER_1M + AVG_OUTPUT_TOKENS * OUTPUT_COST_PER_1M) / 1_000_000


# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------

class DedupCache:
    """In-memory cache mapping incident_signature -> last_seen UTC timestamp."""

    def __init__(self, cooldown_seconds: int = DEDUP_COOLDOWN_SECONDS):
        self._cache: dict[str, float] = {}
        self.cooldown = cooldown_seconds

    def is_duplicate(self, signature: str) -> bool:
        now = time.monotonic()
        last = self._cache.get(signature)
        if last is not None and (now - last) < self.cooldown:
            return True
        return False

    def mark_seen(self, signature: str) -> None:
        self._cache[signature] = time.monotonic()

    def clear(self) -> None:
        self._cache.clear()


dedup_cache = DedupCache()


# ---------------------------------------------------------------------------
# Signature hashing
# ---------------------------------------------------------------------------

def build_signature(service: str, top_error_template: str) -> str:
    raw = f"{service}::{top_error_template[:100]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """\
You are an expert SRE / Backend Engineer performing root-cause analysis.
Analyze the following anomalous log window and return ONLY valid JSON — no markdown, no code fences, no commentary.

=== ANOMALOUS WINDOW SUMMARY ===
Dominant service  : {service}
Window start      : {window_start}
Log count         : {log_count}
Error rate        : {error_rate:.1%}
P95 latency (ms)  : {p95_latency_ms:.0f}
5xx status count  : {status_5xx_count}
Unique error types: {unique_error_templates}
Anomaly reasons   : {reasons}

=== TOP 5 REPRESENTATIVE ERROR LOGS ===
{top_logs}

=== REQUIRED JSON OUTPUT (strict schema) ===
{{
  "severity": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "root_cause": "<2-sentence technical explanation>",
  "affected_service": "<service name>",
  "suggested_remediation": ["<action 1>", "<action 2>", "<action 3>"],
  "confidence_score": <0.0-1.0>
}}
"""


def build_prompt(
    result: AnomalyResult,
    top_logs: list[RawLogEntry],
    window_start: datetime,
) -> str:
    log_lines = "\n".join(
        f"  [{l.level}] {l.service} | {l.status_code} | {l.latency_ms:.0f}ms | {l.message[:120]}"
        for l in top_logs[:5]
    )
    return PROMPT_TEMPLATE.format(
        service=result.dominant_service or "unknown",
        window_start=window_start.isoformat(),
        log_count=result.features.log_count,
        error_rate=result.features.error_rate,
        p95_latency_ms=result.features.p95_latency_ms,
        status_5xx_count=result.features.status_5xx_count,
        unique_error_templates=result.features.unique_error_templates,
        reasons="; ".join(result.reasons) or "IsolationForest score",
        top_logs=log_lines or "  (no error logs in window)",
    )


# ---------------------------------------------------------------------------
# Mock LLM fallback (scenario-aware)
# ---------------------------------------------------------------------------

_MOCK_REPORTS: dict[str, dict] = {
    "db_pool_exhaustion": {
        "severity": "CRITICAL",
        "root_cause": (
            "The database connection pool is fully exhausted, causing all new queries to block "
            "until the connection timeout is reached. This is evidenced by a sudden spike in "
            "latency and a cascade of POOL_TIMEOUT errors across all backend services."
        ),
        "affected_service": "db-proxy",
        "suggested_remediation": [
            "Immediately increase max_pool_size in SQLAlchemy/psycopg config from current value to 2×.",
            "Add circuit-breaker logic in db-proxy so saturated pool returns 503 immediately instead of blocking.",
            "Investigate long-running transactions in pg_stat_activity and TERMINATE any idle-in-transaction sessions.",
        ],
        "confidence_score": 0.94,
    },
    "auth_502_spike": {
        "severity": "HIGH",
        "root_cause": (
            "auth-service is returning 502/503 errors at an anomalous rate, indicating the upstream "
            "identity provider or token-validation endpoint is unreachable or rate-limiting requests. "
            "The error surge pattern is consistent with a dependency failure rather than an application bug."
        ),
        "affected_service": "auth-service",
        "suggested_remediation": [
            "Check health of upstream IdP / OAuth2 provider endpoint and verify network connectivity.",
            "Enable response caching for JWT validation results with a 30-second TTL to reduce IdP fan-out.",
            "Configure retry with exponential backoff (max 3 attempts, jitter) in the auth client library.",
        ],
        "confidence_score": 0.89,
    },
    "memory_leak_oom": {
        "severity": "CRITICAL",
        "root_cause": (
            "checkout-worker is experiencing a gradual memory leak — heap growth is unbounded and "
            "the process is ultimately OOMKilled by the container runtime. The latency creep prior "
            "to the crash is consistent with GC pressure from retained object graphs."
        ),
        "affected_service": "checkout-worker",
        "suggested_remediation": [
            "Attach a heap profiler (py-spy / memray) to a canary pod and capture a heap snapshot before next OOM.",
            "Add a Kubernetes memory limit and configure automatic pod restart on OOMKilled with alerting.",
            "Audit recent code changes for unbounded caches, global state accumulation, or missing __del__ hooks.",
        ],
        "confidence_score": 0.91,
    },
    "default": {
        "severity": "MEDIUM",
        "root_cause": (
            "Anomalous behaviour detected across the service mesh — elevated error rates and latency "
            "suggest a downstream dependency degradation or a recent deployment regression."
        ),
        "affected_service": "unknown",
        "suggested_remediation": [
            "Review recent deployments in the last 30 minutes and roll back the most recent change as a first step.",
            "Check downstream service SLOs and dependency health dashboards for correlated failures.",
            "Enable debug logging temporarily on the affected service and correlate trace IDs to pinpoint the failure path.",
        ],
        "confidence_score": 0.72,
    },
}


def _mock_triage(
    result: AnomalyResult,
    scenario_hint: Optional[str] = None,
) -> tuple[LLMTriageReport, str, float]:
    """
    Returns (LLMTriageReport, model_name, latency_ms).
    Picks the most fitting mock report based on scenario hint or heuristics.
    """
    t0 = time.perf_counter()

    key = "default"
    if scenario_hint:
        for k in _MOCK_REPORTS:
            if k in scenario_hint:
                key = k
                break
    elif result.dominant_service:
        svc = result.dominant_service.lower()
        if "db" in svc or "proxy" in svc:
            key = "db_pool_exhaustion"
        elif "auth" in svc:
            key = "auth_502_spike"
        elif "checkout" in svc or "worker" in svc:
            key = "memory_leak_oom"

    raw = dict(_MOCK_REPORTS[key])
    # Patch the affected service to match what we actually saw
    if result.dominant_service:
        raw["affected_service"] = result.dominant_service

    report = LLMTriageReport(**raw)
    latency = (time.perf_counter() - t0) * 1000
    return report, "mock-llm", round(latency, 2)


# ---------------------------------------------------------------------------
# Real LLM call (Gemini)
# ---------------------------------------------------------------------------

async def _gemini_triage(prompt: str) -> tuple[LLMTriageReport, str, float]:
    """Call Gemini API and parse structured JSON response."""
    t0 = time.perf_counter()
    try:
        import google.genai as genai  # type: ignore

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=512,
            ),
        )
        raw_text = response.text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        data = json.loads(raw_text)
        report = LLMTriageReport(**data)
        latency = (time.perf_counter() - t0) * 1000
        return report, GEMINI_MODEL, round(latency, 2)
    except Exception as exc:
        logger.warning("Gemini call failed (%s); falling back to mock.", exc)
        raise


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class TriageService:
    """
    Stateful triage service.  One instance shared app-wide.
    Thread-safe for the GIL; use asyncio-safe calls for async context.
    """

    def __init__(self):
        self.dedup = DedupCache()
        # In-memory metrics (also persisted to DB by the route handlers)
        self.llm_calls_made: int = 0
        self.llm_calls_avoided: int = 0
        self.duplicate_suppressed_count: int = 0
        self.estimated_cost_usd: float = 0.0

    async def triage(
        self,
        result: AnomalyResult,
        top_logs: list[RawLogEntry],
        window_start: datetime,
        scenario_hint: Optional[str] = None,
    ) -> tuple[Optional[LLMTriageReport], str, float, bool, bool]:
        """
        Returns:
            (report, model_used, llm_latency_ms, is_mock, was_deduplicated)
        """
        service = result.dominant_service or "unknown"
        signature = build_signature(service, result.top_error_template)

        # --- Deduplication gate ---
        if self.dedup.is_duplicate(signature):
            self.llm_calls_avoided += 1
            self.duplicate_suppressed_count += 1
            logger.debug("Duplicate incident suppressed: %s", signature)
            return None, "dedup-suppressed", 0.0, False, True

        # Mark as seen before the async call to prevent race duplicates
        self.dedup.mark_seen(signature)

        # --- Try real LLM, fallback to mock ---
        is_mock = False
        if GEMINI_API_KEY:
            try:
                prompt = build_prompt(result, top_logs, window_start)
                report, model, latency = await _gemini_triage(prompt)
            except Exception:
                report, model, latency = _mock_triage(result, scenario_hint)
                is_mock = True
        else:
            report, model, latency = _mock_triage(result, scenario_hint)
            is_mock = True

        self.llm_calls_made += 1
        self.estimated_cost_usd += COST_PER_CALL
        return report, model, latency, is_mock, False

    def reset_metrics(self) -> None:
        self.llm_calls_made = 0
        self.llm_calls_avoided = 0
        self.duplicate_suppressed_count = 0
        self.estimated_cost_usd = 0.0
        self.dedup.clear()


# Module-level singleton
triage_service = TriageService()
