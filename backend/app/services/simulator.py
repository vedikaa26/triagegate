"""
Synthetic Log Stream Simulator.

Generates realistic microservice JSON logs at a configurable rate.
Supports on-demand injection of three failure scenarios:
  - db_pool_exhaustion
  - auth_502_spike
  - memory_leak_oom
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from app.schemas import RawLogEntry, IncidentScenario

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERVICES = ["auth-service", "payment-api", "db-proxy", "checkout-worker"]
INFO_MESSAGES = [
    "Request processed successfully",
    "Cache hit for key user_session",
    "Database query completed",
    "JWT token validated",
    "Payment authorised",
    "Order created successfully",
    "Health check passed",
    "Session refreshed",
    "Metric collected",
    "Config reloaded",
]
WARN_MESSAGES = [
    "Slow query detected, latency exceeding SLO",
    "Cache miss — falling back to DB",
    "Retry attempt 1/3 for downstream call",
    "Rate limit approaching for client",
    "Memory usage at 75%, monitoring",
]
ERROR_MESSAGES = {
    "db_pool_exhaustion": [
        "POOL_TIMEOUT: could not obtain a database connection within 5000ms",
        "ConnectionPoolExhausted: all 20 connections in use",
        "SQLAlchemy pool pre-ping failed: connection refused",
        "ERROR: deadlock detected on table orders",
        "DB connection failed after 3 retries: timeout",
    ],
    "auth_502_spike": [
        "Upstream IdP returned 502 Bad Gateway",
        "OAuth2 token endpoint unreachable: connection refused",
        "HTTP 503 Service Unavailable from auth.internal",
        "JWT validation failed: issuer endpoint returned 502",
        "Auth service: upstream timeout after 30s",
    ],
    "memory_leak_oom": [
        "OOMKilled: container exceeded memory limit 512Mi",
        "GC pressure: heap usage at 95%, 847ms GC pause",
        "MemoryError: cannot allocate 128MB for batch buffer",
        "RSS growing unbounded: 2.1GB and rising",
        "Worker process killed: out of memory",
    ],
    "default": [
        "Internal server error: unhandled exception in handler",
        "NullPointerException in order processing pipeline",
        "Connection reset by peer",
        "Timeout waiting for upstream response",
    ],
}


# ---------------------------------------------------------------------------
# Log generators
# ---------------------------------------------------------------------------

def _healthy_log(service: str) -> RawLogEntry:
    level_weights = [("INFO", 85), ("WARN", 10), ("DEBUG", 5)]
    level = random.choices(
        [l for l, _ in level_weights],
        weights=[w for _, w in level_weights],
    )[0]
    message = random.choice(INFO_MESSAGES if level == "INFO" else WARN_MESSAGES)
    return RawLogEntry(
        timestamp=datetime.now(timezone.utc),
        service=service,
        level=level,
        latency_ms=random.gauss(120, 40),
        status_code=random.choices([200, 201, 204, 304], weights=[70, 10, 10, 10])[0],
        message=message,
        trace_id=str(uuid.uuid4()),
    )


def _scenario_log(service: str, scenario: str, elapsed_ratio: float) -> RawLogEntry:
    """Generate a log entry appropriate for the given scenario."""
    if scenario == IncidentScenario.DB_POOL_EXHAUSTION:
        # High latency + connection errors
        level = random.choices(["ERROR", "WARN", "INFO"], weights=[60, 25, 15])[0]
        latency = random.gauss(4500, 800) if level == "ERROR" else random.gauss(1800, 400)
        status = random.choices([500, 503, 200], weights=[50, 30, 20])[0]
        message = (
            random.choice(ERROR_MESSAGES["db_pool_exhaustion"]) if level == "ERROR"
            else "DB query queued, pool at capacity"
        )
        affected = "db-proxy"
    elif scenario == IncidentScenario.AUTH_502_SPIKE:
        # Sudden 502/503 errors from auth-service
        level = random.choices(["ERROR", "WARN", "INFO"], weights=[70, 20, 10])[0]
        latency = random.gauss(200, 50)
        status = random.choices([502, 503, 200], weights=[55, 25, 20])[0]
        message = (
            random.choice(ERROR_MESSAGES["auth_502_spike"]) if level == "ERROR"
            else "Auth retry in progress"
        )
        affected = "auth-service"
    elif scenario == IncidentScenario.MEMORY_LEAK_OOM:
        # Gradual latency creep + OOM crash near end
        crash_phase = elapsed_ratio > 0.75
        level = "CRITICAL" if crash_phase else random.choices(["WARN", "INFO"], weights=[60, 40])[0]
        latency = random.gauss(3000 * elapsed_ratio + 100, 200)
        status = 500 if crash_phase else 200
        message = (
            random.choice(ERROR_MESSAGES["memory_leak_oom"]) if crash_phase
            else f"Heap usage: {int(40 + elapsed_ratio * 55)}% — monitoring"
        )
        affected = "checkout-worker"
    else:
        return _healthy_log(service)

    return RawLogEntry(
        timestamp=datetime.now(timezone.utc),
        service=affected,
        level=level,
        latency_ms=max(0.0, latency),
        status_code=status,
        message=message,
        trace_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class LogSimulator:
    """
    Background async task that pushes logs into a shared asyncio.Queue.
    Rate: ~15 logs/second (healthy) → ~40 logs/second (incident).
    """

    def __init__(self, log_callback: Callable[[RawLogEntry], None]):
        self._callback = log_callback
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Scenario injection state
        self._active_scenario: Optional[str] = None
        self._scenario_start: float = 0.0
        self._scenario_duration: float = 30.0

        # Stats
        self.logs_per_second: float = 0.0
        self._log_count: int = 0
        self._last_rate_check: float = time.monotonic()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def active_scenario(self) -> Optional[str]:
        return self._active_scenario

    def inject_scenario(self, scenario: str, duration_seconds: float = 30.0) -> None:
        self._active_scenario = scenario
        self._scenario_start = time.monotonic()
        self._scenario_duration = duration_seconds
        logger.info("Injecting scenario '%s' for %ss", scenario, duration_seconds)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="log-simulator")
        logger.info("Log simulator started.")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("Log simulator stopped.")

    async def _run(self) -> None:
        try:
            while self._running:
                now = time.monotonic()

                # Check scenario expiry
                if self._active_scenario:
                    elapsed = now - self._scenario_start
                    if elapsed > self._scenario_duration:
                        logger.info("Scenario '%s' expired.", self._active_scenario)
                        self._active_scenario = None

                # Determine batch size (logs per 0.1s tick)
                batch = 4 if self._active_scenario else 2   # ~20/s or ~40/s

                for _ in range(batch):
                    service = random.choice(SERVICES)
                    if self._active_scenario:
                        elapsed = now - self._scenario_start
                        ratio = min(elapsed / self._scenario_duration, 1.0)
                        log = _scenario_log(service, self._active_scenario, ratio)
                    else:
                        # 5 % organic errors even in healthy mode
                        if random.random() < 0.05:
                            log = RawLogEntry(
                                timestamp=datetime.now(timezone.utc),
                                service=service,
                                level="ERROR",
                                latency_ms=random.gauss(600, 100),
                                status_code=random.choice([500, 502]),
                                message=random.choice(ERROR_MESSAGES["default"]),
                                trace_id=str(uuid.uuid4()),
                            )
                        else:
                            log = _healthy_log(service)

                    try:
                        self._callback(log)
                    except Exception as exc:
                        logger.warning("Simulator callback error: %s", exc)

                self._log_count += batch

                # Update rate estimate every second
                if now - self._last_rate_check >= 1.0:
                    self.logs_per_second = round(self._log_count / max(now - self._last_rate_check, 1e-9), 1)
                    self._log_count = 0
                    self._last_rate_check = now

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Log simulator crashed.")
