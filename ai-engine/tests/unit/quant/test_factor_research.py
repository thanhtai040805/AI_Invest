import numpy as np
import pandas as pd
import pytest
from app.domain.services.quant.research.factor_research import (
    compute_ic,
    walk_forward_ic,
    factor_alpha_decay,
    regime_aware_ic,
    compute_quantile_returns,
)


class TestComputeIC:
    def test_perfect_rank_correlation(self):
        f = pd.Series({c: i for i, c in enumerate("ABCDEFGHIJ")})
        r = pd.Series({c: 0.01 * i for i, c in enumerate("ABCDEFGHIJ")})
        assert compute_ic(f, r) > 0.9

    def test_perfect_negative(self):
        f = pd.Series({c: i for i, c in enumerate("ABCDEFGHIJ")})
        r = pd.Series({c: 0.01 * (10 - i) for i, c in enumerate("ABCDEFGHIJ")})
        assert compute_ic(f, r) < -0.9

    def test_few_samples(self):
        f = pd.Series({"A": 1, "B": 2})
        r = pd.Series({"A": 0.01, "B": 0.02})
        assert compute_ic(f, r) == 0.0


class TestWalkForwardIC:
    def test_returns_series(self):
        dates = pd.date_range("2020-01-01", "2020-12-31", freq="B")
        np.random.seed(42)
        factor = pd.DataFrame(
            np.random.randn(len(dates), 5),
            index=dates,
            columns=list("ABCDE"),
        )
        price = pd.DataFrame(
            100 + np.cumsum(np.random.randn(len(dates), 5) * 0.5, axis=0),
            index=dates,
            columns=list("ABCDE"),
        )
        ic_series = walk_forward_ic(factor, price, window=60, hold=5)
        assert isinstance(ic_series, pd.Series)
        assert ic_series.name == "walk_forward_ic"


class TestFactorAlphaDecay:
    def test_returns_dataframe(self):
        dates = pd.date_range("2020-01-01", "2020-12-31", freq="B")
        np.random.seed(42)
        factor = pd.DataFrame(
            np.random.randn(len(dates), 5),
            index=dates,
            columns=list("ABCDE"),
        )
        price = pd.DataFrame(
            100 + np.cumsum(np.random.randn(len(dates), 5) * 0.5, axis=0),
            index=dates,
            columns=list("ABCDE"),
        )
        decay = factor_alpha_decay(factor, price, [1, 5, 21])
        assert len(decay) == 3
        assert "holding_period" in decay.columns
        assert "mean_ic" in decay.columns


class TestRegimeAwareIC:
    def test_returns_dict(self):
        dates = pd.date_range("2020-01-01", "2020-06-30", freq="B")
        np.random.seed(42)
        factor = pd.DataFrame(
            np.random.randn(len(dates), 5),
            index=dates,
            columns=list("ABCDE"),
        )
        price = pd.DataFrame(
            100 + 0.5 * np.cumsum(np.random.randn(len(dates), 5), axis=0),
            index=dates,
            columns=list("ABCDE"),
        )
        regimes = pd.Series(
            np.random.choice(["high_vol", "low_vol", "normal"], len(dates)),
            index=dates,
        )
        result = regime_aware_ic(factor, price, regimes, hold=5)
        assert isinstance(result, dict)
        for k in ["high_vol", "low_vol", "normal"]:
            assert k in result
            assert isinstance(result[k], float)


class TestComputeQuantileReturns:
    def test_returns_dataframe(self):
        dates = pd.date_range("2020-01-01", "2020-06-30", freq="B")
        np.random.seed(42)
        factor = pd.DataFrame(
            np.random.randn(len(dates), 20),
            index=dates,
            columns=[f"S{i}" for i in range(20)],
        )
        price = pd.DataFrame(
            100 + 0.5 * np.cumsum(np.random.randn(len(dates), 20), axis=0),
            index=dates,
            columns=[f"S{i}" for i in range(20)],
        )
        qret = compute_quantile_returns(factor, price, n_quantiles=5, hold=5)
        assert isinstance(qret, pd.DataFrame)
        assert "quantile" in qret.columns
        assert "return" in qret.columns
        assert set(qret["quantile"].unique()) == {1, 2, 3, 4, 5}
