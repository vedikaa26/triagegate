"""SQLAlchemy ORM models for TriageGate."""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, Text, JSON
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class LogWindow(Base):
    """Represents a 5-second tumbling window of aggregated log metrics."""

    __tablename__ = "log_windows"

    id = Column(Integer, primary_key=True, index=True)
    window_start = Column(DateTime, nullable=False, index=True)
    window_end = Column(DateTime, nullable=False)

    # Aggregated features (Tier-1 input)
    log_count = Column(Integer, default=0)
    error_rate = Column(Float, default=0.0)
    p95_latency_ms = Column(Float, default=0.0)
    status_5xx_count = Column(Integer, default=0)
    unique_error_templates = Column(Integer, default=0)
    dominant_service = Column(String(64), nullable=True)

    # Tier-1 verdict
    is_anomaly = Column(Boolean, default=False)
    isolation_forest_score = Column(Float, nullable=True)   # raw decision score
    tier1_latency_ms = Column(Float, nullable=True)
    anomaly_reason = Column(String(256), nullable=True)

    # Relationship pointer
    incident_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Incident(Base):
    """A deduplicated, triaged anomaly event with Tier-2 LLM analysis."""

    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    signature_hash = Column(String(64), nullable=False, index=True)

    # Incident context
    affected_service = Column(String(64), nullable=False)
    top_error_template = Column(Text, nullable=True)
    window_id = Column(Integer, nullable=True)

    # Tier-2 LLM result
    severity = Column(String(16), nullable=True)           # LOW/MEDIUM/HIGH/CRITICAL
    root_cause = Column(Text, nullable=True)
    suggested_remediation = Column(JSON, nullable=True)    # list[str]
    confidence_score = Column(Float, nullable=True)
    llm_model_used = Column(String(64), nullable=True)
    llm_latency_ms = Column(Float, nullable=True)
    is_mock = Column(Boolean, default=False)
    was_deduplicated = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)


class CostMetric(Base):
    """Running telemetry counters for cost/ROI tracking."""

    __tablename__ = "cost_metrics"

    id = Column(Integer, primary_key=True, index=True)
    total_logs_processed = Column(Integer, default=0)
    windows_evaluated = Column(Integer, default=0)
    anomalies_detected = Column(Integer, default=0)
    llm_calls_made = Column(Integer, default=0)
    llm_calls_avoided = Column(Integer, default=0)
    duplicate_suppressed_count = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    estimated_cost_saved_usd = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
