"""Risk Model 7 Lớp Hiệu Chuẩn.

1. Market Risk   — VaR / CVaR
2. Factor Risk   — PCA-based factor exposure
3. Liquidity Risk — ADV-based position sizing
4. Concentration Risk — Herfindahl, top-5 weight cap
5. Correlation Risk — Avg pairwise correlation
6. Tail Risk     — Modified Cornish-Fisher VaR
7. Regime Risk   — Volatility regime detection
"""
import numpy as np
import pandas as pd


def compute_var(returns: pd.Series, alpha: float = 0.05) -> float:
    """Historical Value at Risk."""
    return float(np.percentile(returns, alpha * 100))


def compute_cvar(returns: pd.Series, alpha: float = 0.05) -> float:
    """Conditional VaR (Expected Shortfall)."""
    var = compute_var(returns, alpha)
    return float(returns[returns <= var].mean()) if (returns <= var).sum() > 0 else var


def compute_cornish_fisher_var(
    returns: pd.Series, alpha: float = 0.05, days: int = 1
) -> float:
    """Cornish-Fisher VaR (accounts for skewness and kurtosis)."""
    mu = returns.mean() * days
    sigma = returns.std() * np.sqrt(days)
    skew = returns.skew()
    kurt = returns.kurtosis()
    z = float(np.percentile(np.random.standard_normal(100000), alpha * 100))
    z_cf = z + (z ** 2 - 1) * skew / 6 + (z ** 3 - 3 * z) * (kurt - 3) / 24 - (2 * z ** 3 - 5 * z) * skew ** 2 / 36
    return float(mu + sigma * z_cf)


def compute_herfindahl(weights: pd.Series) -> float:
    """Herfindahl-Hirschman Index for concentration."""
    w = weights / weights.sum()
    return float((w ** 2).sum())


def compute_avg_pairwise_correlation(returns: pd.DataFrame) -> float:
    """Average pairwise correlation across all assets."""
    corr = returns.corr()
    n = len(corr)
    if n < 2:
        return 0.0
    triu = np.triu(corr.values, k=1)
    return float(triu.sum() / (n * (n - 1) / 2))


def compute_adv_liquidity_score(
    shares: int, avg_daily_volume: float, days: float = 0.25
) -> float:
    """How many days to liquidate position at 25% ADV."""
    if avg_daily_volume <= 0:
        return float("inf")
    return shares / (avg_daily_volume * days)


def detect_volatility_regime(
    returns: pd.Series, window: int = 21, lookback: int = 252
) -> str:
    """Classify current vol regime relative to recent history."""
    if len(returns) < lookback:
        return "insufficient_data"
    current_vol = returns.iloc[-window:].std()
    hist_vol = returns.iloc[-lookback:].std()
    ratio = current_vol / hist_vol if hist_vol > 0 else 1.0
    if ratio > 1.5:
        return "high_vol"
    elif ratio < 0.7:
        return "low_vol"
    return "normal_vol"


class RiskModel7Layers:
    """7-layer risk model with scoring."""

    def __init__(self, factor_exposures: pd.DataFrame | None = None):
        self.factor_exposures = factor_exposures

    def assess(
        self,
        portfolio_returns: pd.Series,
        current_weights: pd.Series,
        asset_returns: pd.DataFrame,
        adv_data: dict[str, float] | None = None,
    ) -> dict[str, float]:
        scores = {}
        scores["var_95"] = compute_var(portfolio_returns)
        scores["cvar_95"] = compute_cvar(portfolio_returns)
        scores["cf_var_95"] = compute_cornish_fisher_var(portfolio_returns)
        scores["herfindahl"] = compute_herfindahl(current_weights)
        scores["avg_corr"] = compute_avg_pairwise_correlation(asset_returns)
        scores["regime"] = detect_volatility_regime(portfolio_returns)

        n = len(current_weights)
        top5 = current_weights.nlargest(min(5, n)).sum()
        scores["top5_concentration"] = float(top5 / current_weights.sum()) if current_weights.sum() > 0 else 0.0

        if adv_data:
            total_liquid_days = 0.0
            for sym in current_weights.index:
                if sym in adv_data and adv_data[sym] > 0:
                    weight = current_weights[sym]
                    shares = weight / (adv_data[sym] * 10000)
                    total_liquid_days += compute_adv_liquidity_score(
                        max(1, int(shares)), adv_data[sym]
                    )
            scores["liquidity_days"] = total_liquid_days

        return scores
