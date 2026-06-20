import numpy as np
import pandas as pd
import pytest
from app.domain.services.backtest.metrics import (
    compute_sharpe,
    compute_sortino,
    compute_max_drawdown,
    compute_calmar_ratio,
    compute_hit_rate,
    compute_profit_factor,
    compute_deflated_sharpe,
    compute_alpha_beta,
)


class TestComputeSharpe:
    def test_positive_returns(self):
        r = pd.Series([0.001] * 252)
        sharpe = compute_sharpe(r)
        assert sharpe > 0.0

    def test_zero_returns(self):
        r = pd.Series([0.0] * 252)
        assert compute_sharpe(r) == 0.0

    def test_negative_returns(self):
        r = pd.Series([-0.001] * 252)
        assert compute_sharpe(r) < 0.0


class TestComputeSortino:
    def test_no_negative_returns(self):
        r = pd.Series([0.001] * 252)
        assert compute_sortino(r) == 0.0

    def test_with_negatives(self):
        r = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015])
        sortino = compute_sortino(r)
        assert isinstance(sortino, float)


class TestComputeMaxDrawdown:
    def test_strictly_increasing(self):
        eq = pd.Series([1.0, 1.1, 1.2, 1.3])
        assert compute_max_drawdown(eq) == 0.0

    def test_decline(self):
        eq = pd.Series([1.0, 1.2, 0.8, 1.1])
        dd = compute_max_drawdown(eq)
        assert dd < 0.0
        assert pytest.approx(dd, rel=1e-2) == -0.333


class TestComputeCalmarRatio:
    def test_positive_cagr(self):
        r = pd.Series([0.001] * 504)
        eq = pd.Series([1.0] + list(np.cumprod(1 + np.array([0.001] * 503))))
        calmar = compute_calmar_ratio(r, eq)
        assert isinstance(calmar, float)


class TestComputeHitRate:
    def test_all_positive(self):
        assert compute_hit_rate(pd.Series([0.01, 0.02, 0.03])) == 1.0

    def test_half_positive(self):
        assert compute_hit_rate(pd.Series([0.01, -0.01, 0.02, -0.02])) == 0.5


class TestComputeProfitFactor:
    def test_symmetrical(self):
        r = pd.Series([0.01, -0.01, 0.02, -0.02])
        pf = compute_profit_factor(r)
        assert pf > 0

    def test_all_gains(self):
        assert compute_profit_factor(pd.Series([0.01, 0.02])) == float("inf")


class TestComputeDeflatedSharpe:
    def test_simple_case(self):
        dsr = compute_deflated_sharpe(0.5, 100, 100)
        assert isinstance(dsr, float)

    def test_few_samples(self):
        assert compute_deflated_sharpe(0.5, 1, 100) == 0.0


class TestComputeAlphaBeta:
    def test_perfect_correlation(self):
        strat = pd.Series([0.01, -0.005, 0.02, 0.015, -0.01, 0.008])
        bm = pd.Series([0.005, -0.0025, 0.01, 0.008, -0.005, 0.004])
        alpha, beta = compute_alpha_beta(strat, bm)
        assert beta > 0

    def test_insufficient_data(self):
        alpha, beta = compute_alpha_beta(pd.Series([0.01]), pd.Series([0.005]))
        assert alpha == 0.0 and beta == 0.0
