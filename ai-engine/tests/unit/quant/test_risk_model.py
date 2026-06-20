import numpy as np
import pandas as pd
import pytest
from app.domain.services.quant.risk.risk_model import (
    compute_var,
    compute_cvar,
    compute_cornish_fisher_var,
    compute_herfindahl,
    compute_avg_pairwise_correlation,
    compute_adv_liquidity_score,
    detect_volatility_regime,
    RiskModel7Layers,
)


class TestComputeVar:
    def test_var_normal(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(1000) * 0.02)
        var = compute_var(r, 0.05)
        assert var < 0.0


class TestComputeCvar:
    def test_cvar_below_var(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(1000) * 0.02)
        cvar = compute_cvar(r, 0.05)
        var = compute_var(r, 0.05)
        assert cvar <= var


class TestComputeCornishFisherVar:
    def test_cf_var_normal(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(500) * 0.02)
        cf_var = compute_cornish_fisher_var(r, 0.05, 1)
        assert isinstance(cf_var, float)


class TestComputeHerfindahl:
    def test_equal_weights(self):
        w = pd.Series([0.25, 0.25, 0.25, 0.25])
        hhi = compute_herfindahl(w)
        assert pytest.approx(hhi, rel=1e-3) == 0.25

    def test_single_stock(self):
        w = pd.Series([1.0])
        assert compute_herfindahl(w) == 1.0


class TestComputeAvgPairwiseCorrelation:
    def test_identical_returns(self):
        r = pd.DataFrame({"A": [0.01, 0.02, 0.03], "B": [0.01, 0.02, 0.03]})
        assert pytest.approx(compute_avg_pairwise_correlation(r), abs=1e-2) == 1.0

    def test_opposite_returns(self):
        r = pd.DataFrame({"A": [0.01, 0.02], "B": [-0.01, -0.02]})
        assert compute_avg_pairwise_correlation(r) < 0

    def test_single_asset(self):
        r = pd.DataFrame({"A": [0.01, 0.02]})
        assert compute_avg_pairwise_correlation(r) == 0.0


class TestComputeADVLiquidityScore:
    def test_liquid_stock(self):
        days = compute_adv_liquidity_score(1000, 100000, 0.25)
        assert pytest.approx(days, rel=1e-2) == 0.04

    def test_illiquid_stock(self):
        days = compute_adv_liquidity_score(100000, 1000, 0.25)
        assert days > 1.0


class TestDetectVolatilityRegime:
    def test_normal_regime(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(300) * 0.015)
        regime = detect_volatility_regime(r)
        assert regime in ("normal_vol", "low_vol", "high_vol")


class TestRiskModel7Layers:
    def test_assess_returns_scores(self):
        np.random.seed(42)
        model = RiskModel7Layers()
        portfolio_returns = pd.Series(np.random.randn(100) * 0.02)
        weights = pd.Series({"A": 0.3, "B": 0.3, "C": 0.2, "D": 0.2})
        asset_returns = pd.DataFrame(
            np.random.randn(100, 4),
            columns=list("ABCD"),
        )
        scores = model.assess(portfolio_returns, weights, asset_returns)
        assert "var_95" in scores
        assert "cvar_95" in scores
        assert "herfindahl" in scores
        assert "avg_corr" in scores
        assert "regime" in scores
