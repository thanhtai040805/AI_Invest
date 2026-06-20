import numpy as np
import pandas as pd
import pytest
from app.domain.services.quant.risk.evaluation import (
    walk_forward_oos_metrics,
    rolling_sharpe,
    rolling_max_drawdown,
    regime_drift_test,
    attribution_analysis,
)


class TestWalkForwardOOSMetrics:
    def test_returns_metrics_dict(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(100) * 0.02)
        metrics = walk_forward_oos_metrics(r)
        assert "sharpe" in metrics
        assert "cagr" in metrics
        assert "max_drawdown" in metrics

    def test_with_benchmark(self):
        np.random.seed(42)
        r = pd.Series(np.random.randn(100) * 0.02)
        bm = pd.Series(np.random.randn(100) * 0.015)
        metrics = walk_forward_oos_metrics(r, bm)
        assert "alpha" in metrics
        assert "beta" in metrics
        assert "information_ratio" in metrics


class TestRollingSharpe:
    def test_returns_series(self):
        r = pd.Series(np.random.randn(200) * 0.02)
        rs = rolling_sharpe(r, window=63)
        assert isinstance(rs, pd.Series)
        assert len(rs) == len(r)


class TestRollingMaxDrawdown:
    def test_returns_negative_values(self):
        eq = pd.Series(np.exp(np.cumsum(np.random.randn(200) * 0.01)))
        rdd = rolling_max_drawdown(eq, window=63)
        assert (rdd.dropna() <= 0).all()


class TestRegimeDriftTest:
    def test_same_distribution(self):
        np.random.seed(42)
        is_r = pd.Series(np.random.randn(200) * 0.02)
        oos_r = pd.Series(np.random.randn(100) * 0.02)
        result = regime_drift_test(is_r, oos_r)
        assert "ks_statistic" in result
        assert "ks_p_value" in result
        assert "drift_detected" in result

    def test_different_distributions(self):
        is_r = pd.Series(np.random.randn(200) * 0.02)
        oos_r = pd.Series(np.random.randn(100) * 0.02 + 0.01)
        result = regime_drift_test(is_r, oos_r)
        assert result["ks_p_value"] >= 0.0


class TestAttributionAnalysis:
    def test_returns_dataframe(self):
        w = pd.DataFrame({
            "A": [0.5, 0.4],
            "B": [0.5, 0.6],
        }, index=pd.date_range("2024-01-01", "2024-01-02", freq="B"))
        r = pd.DataFrame({
            "A": [0.01, 0.02],
            "B": [-0.005, 0.01],
        }, index=pd.date_range("2024-01-01", "2024-01-02", freq="B"))
        attr = attribution_analysis(w, r)
        assert isinstance(attr, pd.DataFrame)
        assert "contribution" in attr.columns
        assert "pct_of_total" in attr.columns
