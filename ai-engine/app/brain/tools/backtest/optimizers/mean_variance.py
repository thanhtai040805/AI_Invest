"""Mean-variance (max Sharpe) optimizer — delegates to app.quant.risk.portfolio."""
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from backtest.optimizers.base import BaseOptimizer
from app.quant.risk.portfolio import max_sharpe_weights


class MeanVarianceOptimizer(BaseOptimizer):
    """Maximize Sharpe ratio subject to long-only simplex."""

    def __init__(self, lookback: int = 60, risk_free: float = 0.0, **kwargs: Any) -> None:
        super().__init__(lookback=lookback, **kwargs)
        self.risk_free = risk_free

    def _build_context(
        self, window: pd.DataFrame, active: List[str]
    ) -> "Dict[str, Any] | None":
        mu = window.mean().values
        cov = window.cov().values
        if np.isnan(cov).any() or np.isnan(mu).any():
            return None
        return {"cov": cov, "mu": mu}

    def _calc_weights(self, ctx: Dict[str, Any]) -> np.ndarray:
        return max_sharpe_weights(ctx["cov"], ctx["mu"], risk_free=self.risk_free, weight_bounds=(0.0, 1.0))


def optimize(
    ret: pd.DataFrame,
    pos: pd.DataFrame,
    dates: pd.DatetimeIndex,
    lookback: int = 60,
    risk_free: float = 0.0,
) -> pd.DataFrame:
    return MeanVarianceOptimizer(lookback=lookback, risk_free=risk_free).optimize(ret, pos, dates)
