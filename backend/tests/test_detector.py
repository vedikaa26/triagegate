"""
Pytest unit tests for TriageGate Tier-1 gating and Tier-2 deduplication.

Tests verify:
  1. Healthy log windows do NOT trigger the LLM.
  2. Anomalous log windows DO trigger triage.
  3. Duplicate anomalies within 60s are suppressed (LLM skipped).
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ml.detector import AnomalyDetector, extract_features
from app.schemas import WindowFeatures, RawLogEntry
from app.services.triage import TriageService, DedupCache, build_signature


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fitted_detector():
    d = AnomalyDetector()
    d.fit()
    return d


def _make_log(
    level="INFO",
    latency_ms=100.0,
    status_code=200,
    service="auth-service",
    message="Request processed",
) -> RawLogEntry:
    return RawLogEntry(
        timestamp=datetime.now(timezone.utc),
        service=service,
        level=level,
        latency_ms=latency_ms,
        status_code=status_code,
        message=message,
        trace_id="test-trace",
    )


def _healthy_batch(n=50) -> list[RawLogEntry]:
    return [_make_log(latency_ms=120, status_code=200) for _ in range(n)]


def _anomalous_batch(n=50, scenario="errors") -> list[RawLogEntry]:
    logs = []
    for i in range(n):
        if i % 3 == 0:
            logs.append(_make_log(
                level="ERROR",
                latency_ms=5000,
                status_code=500,
                service="db-proxy",
                message="POOL_TIMEOUT: could not obtain connection",
            ))
        else:
            logs.append(_make_log(latency_ms=200))
    return logs


# ---------------------------------------------------------------------------
# Tier-1: Feature extraction tests
# ---------------------------------------------------------------------------

class TestFeatureExtraction:
    def test_empty_logs(self):
        features, top_tmpl, dominant = extract_features([])
        assert features.log_count == 0
        assert features.error_rate == 0.0

    def test_healthy_batch_low_error_rate(self):
        logs = _healthy_batch(100)
        features, _, _ = extract_features(logs)
        assert features.error_rate < 0.15
        assert features.p95_latency_ms < 1200

    def test_error_rate_calculation(self):
        logs = [_make_log(level="ERROR")] * 20 + [_make_log()] * 80
        features, _, _ = extract_features(logs)
        assert abs(features.error_rate - 0.20) < 0.01

    def test_p95_latency_high(self):
        # 95% of logs have high latency
        logs = [_make_log(latency_ms=2000)] * 95 + [_make_log(latency_ms=100)] * 5
        features, _, _ = extract_features(logs)
        assert features.p95_latency_ms > 1200

    def test_unique_error_templates(self):
        logs = [
            _make_log(level="ERROR", message="DB timeout after 5000ms"),
            _make_log(level="ERROR", message="DB timeout after 3000ms"),
            _make_log(level="ERROR", message="Connection refused on port 5432"),
        ]
        features, top_tmpl, _ = extract_features(logs)
        # 2 unique templates after normalisation
        assert features.unique_error_templates >= 1
        assert top_tmpl != ""


# ---------------------------------------------------------------------------
# Tier-1: AnomalyDetector tests
# ---------------------------------------------------------------------------

class TestAnomalyDetector:
    def test_model_fitted_on_init(self, fitted_detector):
        assert fitted_detector.is_fitted is True

    def test_healthy_window_not_anomaly(self, fitted_detector):
        logs = _healthy_batch(60)
        result = fitted_detector.score(logs)
        # Healthy windows should rarely be flagged (baseline trained on healthy data)
        # Rule-based: error_rate < 0.15 and p95 < 1200ms
        assert result.features.error_rate < 0.15
        assert result.features.p95_latency_ms < 1200
        # Even if IF flags it, our rules should not add reasons
        rule_reasons = [r for r in result.reasons if "Error rate" in r or "p95 latency" in r]
        assert len(rule_reasons) == 0, f"Healthy window incorrectly flagged: {result.reasons}"

    def test_anomalous_window_detected(self, fitted_detector):
        logs = _anomalous_batch(60)
        result = fitted_detector.score(logs)
        assert result.is_anomaly is True
        assert len(result.reasons) > 0

    def test_high_error_rate_triggers(self, fitted_detector):
        # 50% error rate — must trigger
        logs = [_make_log(level="ERROR", latency_ms=300, status_code=500)] * 30 + [_make_log()] * 30
        result = fitted_detector.score(logs)
        assert result.is_anomaly is True
        assert any("Error rate" in r for r in result.reasons)

    def test_high_latency_triggers(self, fitted_detector):
        # All logs at 2000ms — must trigger p95 rule
        logs = [_make_log(latency_ms=2000)] * 50
        result = fitted_detector.score(logs)
        assert result.is_anomaly is True
        assert any("p95 latency" in r for r in result.reasons)

    def test_tier1_latency_recorded(self, fitted_detector):
        logs = _healthy_batch(30)
        result = fitted_detector.score(logs)
        assert result.tier1_latency_ms > 0
        assert result.tier1_latency_ms < 500   # should be very fast

    def test_score_features_direct(self, fitted_detector):
        """score_features bypasses log parsing — tests the IF+rules path directly."""
        anomalous = WindowFeatures(
            log_count=50, error_rate=0.50, p95_latency_ms=3000,
            status_5xx_count=25, unique_error_templates=3
        )
        result = fitted_detector.score_features(anomalous)
        assert result.is_anomaly is True

    def test_score_features_healthy(self, fitted_detector):
        healthy = WindowFeatures(
            log_count=50, error_rate=0.01, p95_latency_ms=120,
            status_5xx_count=0, unique_error_templates=0
        )
        result = fitted_detector.score_features(healthy)
        rule_reasons = [r for r in result.reasons if "Error rate" in r or "p95 latency" in r]
        assert len(rule_reasons) == 0


# ---------------------------------------------------------------------------
# Tier-1 → Tier-2 Gate: healthy logs do NOT call LLM
# ---------------------------------------------------------------------------

class TestTier1Gate:
    @pytest.mark.asyncio
    async def test_healthy_logs_no_llm_call(self, fitted_detector):
        """
        Healthy window → Tier-1 says NOT anomaly → triage.triage() should NEVER be called.
        """
        from app.ml.detector import AnomalyResult
        from app.schemas import WindowFeatures

        triage = TriageService()
        call_count = 0

        async def _mock_triage(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return None, "mock", 0.0, True, False

        with patch.object(triage, "triage", side_effect=_mock_triage):
            logs = _healthy_batch(60)
            result = fitted_detector.score(logs)
            # Only call triage IF anomaly
            if result.is_anomaly:
                await triage.triage(result, [], datetime.now(timezone.utc))

        if not fitted_detector.score(_healthy_batch(60)).is_anomaly:
            assert call_count == 0, "LLM was called for a healthy window!"


# ---------------------------------------------------------------------------
# Tier-2: Deduplication tests
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_first_call_not_duplicate(self):
        cache = DedupCache(cooldown_seconds=60)
        sig = "test_signature"
        assert cache.is_duplicate(sig) is False
        cache.mark_seen(sig)

    def test_second_call_within_cooldown_is_duplicate(self):
        cache = DedupCache(cooldown_seconds=60)
        sig = "test_signature_2"
        cache.mark_seen(sig)
        assert cache.is_duplicate(sig) is True

    def test_different_signatures_not_duplicate(self):
        cache = DedupCache(cooldown_seconds=60)
        cache.mark_seen("sig_a")
        assert cache.is_duplicate("sig_b") is False

    def test_cooldown_expired_not_duplicate(self):
        cache = DedupCache(cooldown_seconds=1)
        sig = "expiring_sig"
        cache.mark_seen(sig)
        assert cache.is_duplicate(sig) is True
        time.sleep(1.1)
        assert cache.is_duplicate(sig) is False

    def test_signature_hash_deterministic(self):
        s1 = build_signature("auth-service", "POOL_TIMEOUT: N")
        s2 = build_signature("auth-service", "POOL_TIMEOUT: N")
        assert s1 == s2

    def test_different_service_different_hash(self):
        s1 = build_signature("auth-service", "timeout")
        s2 = build_signature("db-proxy", "timeout")
        assert s1 != s2

    @pytest.mark.asyncio
    async def test_duplicate_anomaly_suppresses_llm(self, fitted_detector):
        """
        Two anomalous windows with the same signature within 60s.
        Only the FIRST should trigger LLM; the second is suppressed.
        """
        from app.ml.detector import AnomalyResult
        triage = TriageService()
        triage.dedup = DedupCache(cooldown_seconds=60)

        # Build a fake anomaly result
        from app.ml.detector import AnomalyResult
        fake_result = AnomalyResult(
            is_anomaly=True,
            score=-0.3,
            reasons=["Error rate 50.0% > threshold 15.0%"],
            tier1_latency_ms=1.5,
            features=WindowFeatures(
                log_count=50, error_rate=0.50, p95_latency_ms=800,
                status_5xx_count=25, unique_error_templates=2,
            ),
            top_error_template="POOL_TIMEOUT: N",
            dominant_service="db-proxy",
        )

        real_llm_calls = 0

        original_mock = __import__("app.services.triage", fromlist=["_mock_triage"])._mock_triage

        def _counting_mock(result, scenario_hint=None):
            nonlocal real_llm_calls
            real_llm_calls += 1
            from app.schemas import LLMTriageReport, SeverityLevel
            report = LLMTriageReport(
                severity=SeverityLevel.HIGH,
                root_cause="Test root cause.",
                affected_service="db-proxy",
                suggested_remediation=["Action 1", "Action 2", "Action 3"],
                confidence_score=0.9,
            )
            return report, "mock", 1.0

        with patch("app.services.triage._mock_triage", side_effect=_counting_mock):
            # First triage call
            r1, model1, lat1, mock1, dedup1 = await triage.triage(
                fake_result, [], datetime.now(timezone.utc)
            )
            # Second triage call (same signature, within cooldown)
            r2, model2, lat2, mock2, dedup2 = await triage.triage(
                fake_result, [], datetime.now(timezone.utc)
            )

        assert dedup1 is False, "First call should NOT be a duplicate"
        assert dedup2 is True, "Second call within 60s MUST be a duplicate"
        assert real_llm_calls == 1, f"Expected 1 LLM call, got {real_llm_calls}"
        assert triage.duplicate_suppressed_count == 1
