"""
Tier-1 Anomaly Detection: IsolationForest + rolling Z-score/error-rate heuristics.

Architecture:
  - Receives raw log entries grouped into 5-second tumbling windows.
  - Extracts 5 numerical features per window.
  - Runs IsolationForest (trained on healthy baseline on startup).
  - Applies additional rule-based gates (error_rate, p95_latency).
  - Returns AnomalyResult with verdict, score, reasons, and inference latency.
"""

from __future__ import annotations

import time
import logging
import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from statistics import quantiles
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.schemas import WindowFeatures, RawLogEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (overridden via config in main.py if needed)
# ---------------------------------------------------------------------------
ERROR_RATE_THRESHOLD = 0.15
P95_LATENCY_THRESHOLD_MS = 1200.0
ISOLATION_FOREST_CONTAMINATION = 0.05   # 5 % of training windows expected anomalous
BASELINE_WINDOW_COUNT = 200             # synthetic healthy windows for initial fit


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AnomalyResult:
    is_anomaly: bool
    score: float                     # IsolationForest decision score (higher = healthier)
    reasons: list[str]
    tier1_latency_ms: float
    features: WindowFeatures
    top_error_template: str = ""
    dominant_service: str = ""


# ---------------------------------------------------------------------------
# Feature extraction helpers
# ---------------------------------------------------------------------------

ERROR_LEVELS = {"ERROR", "CRITICAL"}
_5XX_RANGE = range(500, 600)


def _error_template(message: str) -> str:
    """Coarse normalisation: strip numbers/UUIDs to group similar errors."""
    import re
    msg = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", message)
    msg = re.sub(r"\b\d+\b", "<N>", msg)
    return msg[:120]


def extract_features(logs: list[RawLogEntry]) -> tuple[WindowFeatures, str, str]:
    """
    Returns (WindowFeatures, top_error_template, dominant_service).
    Pure function; no side-effects.
    """
    if not logs:
        return WindowFeatures(
            log_count=0, error_rate=0.0, p95_latency_ms=0.0,
            status_5xx_count=0, unique_error_templates=0
        ), "", ""

    total = len(logs)
    error_logs = [l for l in logs if l.level.upper() in ERROR_LEVELS]
    error_rate = len(error_logs) / total

    latencies = [l.latency_ms for l in logs if l.latency_ms > 0]
    if latencies:
        sorted_lat = sorted(latencies)
        p95_idx = max(0, int(0.95 * len(sorted_lat)) - 1)
        p95_latency = sorted_lat[p95_idx]
    else:
        p95_latency = 0.0

    status_5xx = sum(1 for l in logs if l.status_code in _5XX_RANGE)

    error_templates = [_error_template(l.message) for l in error_logs]
    unique_templates = len(set(error_templates))
    top_template = max(set(error_templates), key=error_templates.count) if error_templates else ""

    # Dominant service by count
    from collections import Counter
    service_counts = Counter(l.service for l in logs)
    dominant = service_counts.most_common(1)[0][0] if service_counts else ""

    return (
        WindowFeatures(
            log_count=total,
            error_rate=round(error_rate, 4),
            p95_latency_ms=round(p95_latency, 2),
            status_5xx_count=status_5xx,
            unique_error_templates=unique_templates,
        ),
        top_template,
        dominant,
    )


def features_to_array(wf: WindowFeatures) -> np.ndarray:
    return np.array([[
        wf.log_count,
        wf.error_rate,
        wf.p95_latency_ms,
        wf.status_5xx_count,
        wf.unique_error_templates,
    ]], dtype=float)


# ---------------------------------------------------------------------------
# Synthetic healthy baseline generator (for initial model fit)
# ---------------------------------------------------------------------------

def _generate_healthy_baseline(n: int = BASELINE_WINDOW_COUNT) -> np.ndarray:
    """
    Generates n synthetic 'healthy' feature vectors to pre-train the model.
    Realistic healthy ranges:
      log_count:              20-120
      error_rate:             0.00-0.04
      p95_latency_ms:         50-400
      status_5xx_count:       0-2
      unique_error_templates: 0-2
    """
    rng = np.random.default_rng(seed=42)
    log_count         = rng.integers(20, 120, size=n).astype(float)
    error_rate        = rng.uniform(0.0, 0.04, size=n)
    p95_latency       = rng.uniform(50.0, 400.0, size=n)
    status_5xx        = rng.integers(0, 3, size=n).astype(float)
    unique_err_tmpl   = rng.integers(0, 3, size=n).astype(float)
    return np.column_stack([log_count, error_rate, p95_latency, status_5xx, unique_err_tmpl])


# ---------------------------------------------------------------------------
# Main Detector class
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    Thread-safe (GIL-protected for inference) Tier-1 detector.
    Call fit() once on startup, then score() for every window.
    """

    def __init__(
        self,
        error_rate_threshold: float = ERROR_RATE_THRESHOLD,
        p95_threshold: float = P95_LATENCY_THRESHOLD_MS,
        contamination: float = ISOLATION_FOREST_CONTAMINATION,
    ):
        self.error_rate_threshold = error_rate_threshold
        self.p95_threshold = p95_threshold
        self._model: Optional[IsolationForest] = None
        self._scaler: Optional[StandardScaler] = None
        self._fitted = False
        self._contamination = contamination
        # Rolling buffer of recent real windows (for online re-training hooks)
        self._window_buffer: deque[np.ndarray] = deque(maxlen=500)

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, X: Optional[np.ndarray] = None) -> None:
        """
        Fit IsolationForest.  If X is None, uses the synthetic healthy baseline.
        """
        if X is None:
            X = _generate_healthy_baseline()

        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X)

        self._model = IsolationForest(
            n_estimators=200,
            contamination=self._contamination,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X_scaled)
        self._fitted = True
        logger.info("IsolationForest fitted on %d windows.", len(X))

    def score(
        self, logs: list[RawLogEntry]
    ) -> AnomalyResult:
        """
        Full Tier-1 pipeline for a single window.
        Returns AnomalyResult with verdict, reasons, and latency.
        """
        t0 = time.perf_counter()

        features, top_template, dominant_service = extract_features(logs)
        x = features_to_array(features)

        # --- IsolationForest ---
        if self._fitted and self._scaler is not None and self._model is not None:
            x_scaled = self._scaler.transform(x)
            prediction = int(self._model.predict(x_scaled)[0])        # 1 or -1
            if_score = float(self._model.decision_function(x_scaled)[0])  # higher = healthier
        else:
            prediction = 1   # treat as normal if not fitted yet
            if_score = 0.0

        # Store in rolling buffer for potential re-fitting
        self._window_buffer.append(x[0])

        # --- Rule-based gates ---
        reasons: list[str] = []
        if prediction == -1:
            reasons.append(f"IsolationForest: anomalous (score={if_score:.3f})")
        if features.error_rate > self.error_rate_threshold:
            reasons.append(
                f"Error rate {features.error_rate:.1%} > threshold {self.error_rate_threshold:.1%}"
            )
        if features.p95_latency_ms > self.p95_threshold:
            reasons.append(
                f"p95 latency {features.p95_latency_ms:.0f}ms > threshold {self.p95_threshold:.0f}ms"
            )

        is_anomaly = bool(reasons)

        tier1_latency = (time.perf_counter() - t0) * 1000  # ms

        return AnomalyResult(
            is_anomaly=is_anomaly,
            score=if_score,
            reasons=reasons,
            tier1_latency_ms=round(tier1_latency, 3),
            features=features,
            top_error_template=top_template,
            dominant_service=dominant_service,
        )

    def score_features(self, features: WindowFeatures) -> AnomalyResult:
        """Score pre-extracted features (used in tests)."""
        # Build dummy logs list not needed; re-use internal path
        t0 = time.perf_counter()
        x = features_to_array(features)

        if self._fitted and self._scaler is not None and self._model is not None:
            x_scaled = self._scaler.transform(x)
            prediction = int(self._model.predict(x_scaled)[0])
            if_score = float(self._model.decision_function(x_scaled)[0])
        else:
            prediction = 1
            if_score = 0.0

        reasons: list[str] = []
        if prediction == -1:
            reasons.append(f"IsolationForest: anomalous (score={if_score:.3f})")
        if features.error_rate > self.error_rate_threshold:
            reasons.append(f"Error rate {features.error_rate:.1%} > threshold {self.error_rate_threshold:.1%}")
        if features.p95_latency_ms > self.p95_threshold:
            reasons.append(f"p95 latency {features.p95_latency_ms:.0f}ms > threshold {self.p95_threshold:.0f}ms")

        tier1_latency = (time.perf_counter() - t0) * 1000

        return AnomalyResult(
            is_anomaly=bool(reasons),
            score=if_score,
            reasons=reasons,
            tier1_latency_ms=round(tier1_latency, 3),
            features=features,
        )


# Module-level singleton (initialised in main.py lifespan)
detector = AnomalyDetector()
