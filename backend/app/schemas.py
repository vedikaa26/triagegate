"""Pydantic v2 schemas for TriageGate API request/response contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentScenario(str, Enum):
    DB_POOL_EXHAUSTION = "db_pool_exhaustion"
    AUTH_502_SPIKE = "auth_502_spike"
    MEMORY_LEAK_OOM = "memory_leak_oom"


# ---------------------------------------------------------------------------
# Inbound: raw log entry (from simulator or external sources)
# ---------------------------------------------------------------------------

class RawLogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp: datetime
    service: str
    level: str                      # DEBUG / INFO / WARN / ERROR / CRITICAL
    latency_ms: float = 0.0
    status_code: int = 200
    message: str
    trace_id: str = ""


# ---------------------------------------------------------------------------
# Tier-1 feature window
# ---------------------------------------------------------------------------

class WindowFeatures(BaseModel):
    log_count: int
    error_rate: float
    p95_latency_ms: float
    status_5xx_count: int
    unique_error_templates: int


class WindowResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    window_start: datetime
    window_end: datetime
    log_count: int
    error_rate: float
    p95_latency_ms: float
    status_5xx_count: int
    unique_error_templates: int
    dominant_service: Optional[str]
    is_anomaly: bool
    isolation_forest_score: Optional[float]
    tier1_latency_ms: Optional[float]
    anomaly_reason: Optional[str]
    incident_id: Optional[int]
    created_at: datetime


# ---------------------------------------------------------------------------
# Tier-2 LLM structured output
# ---------------------------------------------------------------------------

class LLMTriageReport(BaseModel):
    """Strict structured output enforced from the LLM response."""

    severity: SeverityLevel
    root_cause: str = Field(..., description="Concise 2-sentence technical root cause.")
    affected_service: str
    suggested_remediation: list[str] = Field(
        ..., min_length=1, max_length=5,
        description="3 concrete engineering actions."
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    signature_hash: str
    affected_service: str
    top_error_template: Optional[str]
    window_id: Optional[int]
    severity: Optional[str]
    root_cause: Optional[str]
    suggested_remediation: Optional[list[str]]
    confidence_score: Optional[float]
    llm_model_used: Optional[str]
    llm_latency_ms: Optional[float]
    is_mock: bool
    was_deduplicated: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Cost / ROI telemetry
# ---------------------------------------------------------------------------

class CostMetricResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_logs_processed: int
    windows_evaluated: int
    anomalies_detected: int
    llm_calls_made: int
    llm_calls_avoided: int
    duplicate_suppressed_count: int
    estimated_cost_usd: float
    estimated_cost_saved_usd: float
    cost_reduction_percent: float   # computed on the fly


# ---------------------------------------------------------------------------
# API responses
# ---------------------------------------------------------------------------

class SimulatorStatus(BaseModel):
    running: bool
    logs_per_second: float
    active_scenario: Optional[str]


class InjectIncidentRequest(BaseModel):
    scenario: IncidentScenario
    duration_seconds: int = Field(default=30, ge=5, le=300)


class InjectIncidentResponse(BaseModel):
    scenario: str
    message: str
    duration_seconds: int


class StatusResponse(BaseModel):
    status: str
    version: str
    simulator: SimulatorStatus
    metrics: CostMetricResponse


class RecentDataResponse(BaseModel):
    windows: list[WindowResult]
    incidents: list[IncidentResponse]


class HealthResponse(BaseModel):
    status: str
    tier1_model_fitted: bool
    db_connected: bool
