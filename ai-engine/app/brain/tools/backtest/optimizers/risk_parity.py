"""Risk parity — delegates to app.quant.risk.portfolio."""
from typing import Any, Dict

import numpy as np
import pandas as pd

from backtest.optimizers.base import BaseOptimizer
from app.quant.risk.portfolio import risk_parity_weights


class RiskParityOptimizer(BaseOptimizer):
    """Equal risk contribution via new portfolio.risk_parity_weights."""

    def _calc_weights(self, ctx: Dict[str, Any]) -> np.ndarray:
        return risk_parity_weights(ctx["cov"])


def optimize(
    ret: pd.DataFrame,
    pos: pd.DataFrame,
    dates: pd.DatetimeIndex,
    lookback: int = 60,
) -> pd.DataFrame:
    return RiskParityOptimizer(lookback=lookback).optimize(ret, pos, dates)
