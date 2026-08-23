"""
CSAD (Cross-Sectional Absolute Deviation) Herding Calculator
Quantifies market herd behavior on HOSE based on Chang, Cheng & Khorana (2000).

CSAD_t = (1/N) * sum(|R_{i,t} - R_{m,t}|)
Regression: CSAD_t = alpha + beta_1 * |R_{m,t}| + beta_2 * (R_{m,t})^2

Differentiates:
- Panic Selling: Market < 0, CSAD low, beta_2 < 0 -> Everyone sells indiscriminately (CRITICAL)
- Sector Rotation: Market < 0, CSAD high -> Dispersion, healthy rotation (INFO)
- FOMO Euphoria: Market > 0, CSAD low, beta_2 < 0 -> Buying frenzy (WARNING)
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple


class CSADCalculator:
    """
    Calculates Cross-Sectional Absolute Deviation (CSAD) and detects Herding vs Dispersion.
    """

    @staticmethod
    def compute_csad(stock_returns: pd.DataFrame, market_return: float) -> float:
        """
        Calculates raw CSAD for a single time period.
        stock_returns: Series or DataFrame column of individual stock returns R_{i,t}
        market_return: Market index return R_{m,t}
        """
        if stock_returns.empty:
            return 0.0
            
        returns_vec = stock_returns.values.flatten()
        valid_returns = returns_vec[~np.isnan(returns_vec)]
        
        if len(valid_returns) == 0:
            return 0.0

        csad = np.mean(np.abs(valid_returns - market_return))
        return float(csad)

    def analyze_herding(
        self,
        daily_returns_df: pd.DataFrame,
        market_returns_series: pd.Series,
        window: int = 60
    ) -> Dict[str, Any]:
        """
        Runs non-linear regression over a rolling window (default 60 days) to estimate beta_2.

        Returns:
            Dict containing current CSAD, beta_2 coefficient, regime classification, and description.
        """
        if len(daily_returns_df) < 20 or len(market_returns_series) < 20:
            return {
                "csad": 0.02,
                "beta_2": 0.0,
                "herding_status": "INSUFFICIENT_DATA",
                "is_herding": False,
                "reason": "Need at least 20 historical sessions for CSAD regression"
            }

        # Align data over window
        recent_market = market_returns_series.tail(window).values
        n_days = len(recent_market)

        csad_values = []
        for t in range(n_days):
            day_slice = daily_returns_df.iloc[-n_days + t]
            csad_t = self.compute_csad(pd.DataFrame(day_slice), recent_market[t])
            csad_values.append(csad_t)

        csad_arr = np.array(csad_values)
        abs_rm = np.abs(recent_market)
        sq_rm = recent_market ** 2

        # Quadratic OLS regression: CSAD = alpha + beta_1 * |Rm| + beta_2 * Rm^2
        X = np.column_stack([np.ones(n_days), abs_rm, sq_rm])
        try:
            params, _, _, _ = np.linalg.lstsq(X, csad_arr, rcond=None)
            alpha, beta_1, beta_2 = params[0], params[1], params[2]
        except Exception:
            alpha, beta_1, beta_2 = 0.02, 0.5, 0.0

        latest_csad = float(csad_arr[-1]) if len(csad_arr) > 0 else 0.02
        latest_rm = float(recent_market[-1]) if len(recent_market) > 0 else 0.0

        # Classify Herding Behavior
        if latest_rm < -0.01:
            if beta_2 < -0.5 or (latest_csad < 0.015 and beta_2 < 0):
                status = "PANIC_SELLING_HERDING"
                is_herding = True
                alert_level = "CRITICAL"
                reason = "Indiscriminate panic selling across market (Beta2 < 0, CSAD low)"
            else:
                status = "SECTOR_ROTATION"
                is_herding = False
                alert_level = "INFO"
                reason = "Market down but dispersion high (Healthy sector rotation)"
        elif latest_rm > 0.01:
            if beta_2 < -0.5 or (latest_csad < 0.015 and beta_2 < 0):
                status = "FOMO_EUPHORIA_HERDING"
                is_herding = True
                alert_level = "WARNING"
                reason = "Indiscriminate FOMO buying frenzy (Beta2 < 0, CSAD low)"
            else:
                status = "NORMAL_BULL_DISPERSION"
                is_herding = False
                alert_level = "NORMAL"
                reason = "Market up with healthy stock dispersion"
        else:
            status = "BALANCED_DISPERSION"
            is_herding = False
            alert_level = "NORMAL"
            reason = "Market sideways with normal return dispersion"

        return {
            "csad": round(latest_csad, 4),
            "beta_2": round(float(beta_2), 4),
            "market_return": round(latest_rm, 4),
            "herding_status": status,
            "alert_level": alert_level,
            "is_herding": is_herding,
            "reason": reason
        }


csad_calculator = CSADCalculator()
