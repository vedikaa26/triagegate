"""
Shared application state container.
Avoids circular imports between main.py, routes.py, and services.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.ml.detector import AnomalyDetector
    from app.services.triage import TriageService
    from app.services.simulator import LogSimulator
    from app.schemas import RawLogEntry


class AppState:
    def __init__(self):
        self.detector: Optional["AnomalyDetector"] = None
        self.triage_service: Optional["TriageService"] = None
        self.simulator: Optional["LogSimulator"] = None
        self.log_buffer: deque = deque(maxlen=5000)
        self.total_logs_processed: int = 0
        self.windows_evaluated: int = 0
        self.anomalies_detected: int = 0


app_state = AppState()
