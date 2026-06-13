"""Tests for CalibratedConfidence and ensemble confidence."""
import numpy as np
import pytest

from app.ml.calibration import CalibratedConfidence, compute_ensemble_confidence


class TestCalibratedConfidence:
    def test_platt_calibration(self):
        rng = np.random.RandomState(42)
        raw = rng.uniform(0, 1, 500)
        outcomes = (raw + rng.normal(0, 0.1, 500) > 0.5).astype(int)

        cal = CalibratedConfidence(method="platt")
        cal.calibrate(raw, outcomes)

        proba = cal.predict_proba(0.8)
        assert 0 <= proba <= 1

    def test_isotonic_calibration(self):
        rng = np.random.RandomState(42)
        raw = rng.uniform(0, 1, 500)
        outcomes = (raw + rng.normal(0, 0.1, 500) > 0.5).astype(int)

        cal = CalibratedConfidence(method="isotonic")
        cal.calibrate(raw, outcomes)

        proba = cal.predict_proba(0.8)
        assert 0 <= proba <= 1

    def test_invalid_method_raises(self):
        with pytest.raises(ValueError, match="Method"):
            CalibratedConfidence(method="invalid")

    def test_predict_proba_without_fit_returns_clipped(self):
        cal = CalibratedConfidence(method="platt")
        proba = cal.predict_proba(2.5)
        assert proba == 1.0
        proba = cal.predict_proba(-0.5)
        assert proba == 0.0

    def test_brier_score(self):
        y_true = np.array([1, 0, 1, 0, 1])
        y_pred = np.array([0.9, 0.1, 0.8, 0.2, 0.7])
        score = CalibratedConfidence.brier_score(y_true, y_pred)
        assert 0 <= score <= 1
        assert score < 0.25

    def test_reliability_diagram(self):
        rng = np.random.RandomState(42)
        raw = rng.uniform(0, 1, 1000)
        outcomes = (raw + rng.normal(0, 0.05, 1000) > 0.5).astype(int)

        cal = CalibratedConfidence(method="platt")
        diag = cal.reliability_diagram(raw, outcomes, n_bins=10)
        assert len(diag["bin_centers"]) > 0
        assert len(diag["fraction_of_positives"]) > 0
        assert len(diag["perfect_calibration"]) > 0


class TestEnsembleConfidence:
    def test_confidence_in_range(self):
        conf = compute_ensemble_confidence(
            factor_score=1.5,
            ml_proba=0.7,
            signal_strength=0.8,
            agreement=0.6,
        )
        assert 0 <= conf <= 1

    def test_strong_signal_high_confidence(self):
        strong = compute_ensemble_confidence(
            factor_score=2.5,
            ml_proba=0.9,
            signal_strength=0.9,
            agreement=0.8,
        )
        weak = compute_ensemble_confidence(
            factor_score=-2.0,
            ml_proba=0.1,
            signal_strength=0.1,
            agreement=0.1,
        )
        assert strong > weak

    def test_factor_score_clipped(self):
        conf = compute_ensemble_confidence(
            factor_score=10.0,
            ml_proba=0.5,
            signal_strength=0.5,
            agreement=0.5,
        )
        assert 0 <= conf <= 1

    def test_all_max_returns_high(self):
        conf = compute_ensemble_confidence(
            factor_score=3.0,
            ml_proba=1.0,
            signal_strength=1.0,
            agreement=1.0,
        )
        assert conf > 0.8

    def test_all_min_returns_low(self):
        conf = compute_ensemble_confidence(
            factor_score=-3.0,
            ml_proba=0.0,
            signal_strength=0.0,
            agreement=0.0,
        )
        assert conf < 0.2
