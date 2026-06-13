import numpy as np
import pandas as pd
import pytest
from app.quant.risk.portfolio import (
    ledoit_wolf_shrinkage,
    max_sharpe_weights,
    min_vol_weights,
    risk_parity_weights,
    volatility_target_weights,
    kelly_fraction,
    compute_implied_alpha,
    apply_weight_caps,
)


class TestLedoitWolfShrinkage:
    def test_returns_cov_matrix(self):
        np.random.seed(42)
        r = pd.DataFrame(np.random.randn(100, 5))
        cov, shrinkage = ledoit_wolf_shrinkage(r)
        assert cov.shape == (5, 5)
        assert 0.0 <= shrinkage <= 1.0

    def test_single_asset(self):
        r = pd.DataFrame(np.random.randn(100, 1))
        cov, shrinkage = ledoit_wolf_shrinkage(r)
        assert cov.shape == (1, 1)
        assert shrinkage == 0.0


class TestMaxSharpeWeights:
    def test_sum_to_one(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        mu = np.array([0.001] * 5)
        w = max_sharpe_weights(cov, mu)
        assert pytest.approx(w.sum(), abs=1e-4) == 1.0
        assert all(w >= 0)

    def test_weight_bounds(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        mu = np.array([0.01, 0.005, 0.002, -0.001, 0.008])
        w = max_sharpe_weights(cov, mu, weight_bounds=(0.0, 0.30))
        assert all(w <= 0.30 + 1e-4)


class TestMinVolWeights:
    def test_sum_to_one(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        w = min_vol_weights(cov)
        assert pytest.approx(w.sum(), abs=1e-4) == 1.0


class TestRiskParityWeights:
    def test_sum_to_one(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        w = risk_parity_weights(cov)
        assert pytest.approx(w.sum(), abs=1e-4) == 1.0


class TestVolatilityTarget:
    def test_scales_down(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        w = np.full(5, 0.2)
        scaled = volatility_target_weights(w, cov, 0.10)
        assert scaled.sum() < w.sum()


class TestKellyFraction:
    def test_positive_fraction(self):
        f = kelly_fraction(0.001, 0.04 ** 2)
        assert 0 < f < 1

    def test_negative_return(self):
        f = kelly_fraction(-0.001, 0.02 ** 2)
        assert f == 0.0

    def test_zero_variance(self):
        assert kelly_fraction(0.001, 0) == 0.0


class TestComputeImpliedAlpha:
    def test_returns_array(self):
        np.random.seed(42)
        cov = np.random.randn(5, 5)
        cov = cov @ cov.T + np.eye(5) * 0.1
        w = np.full(5, 0.2)
        alpha = compute_implied_alpha(w, cov)
        assert len(alpha) == 5


class TestApplyWeightCaps:
    def test_enforces_max(self):
        w = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
        capped = apply_weight_caps(w, max_weight=0.4)
        assert all(capped <= 0.4 + 1e-4)

    def test_sum_to_one(self):
        w = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
        capped = apply_weight_caps(w, max_weight=0.4)
        assert pytest.approx(capped.sum(), abs=1e-4) == 1.0
