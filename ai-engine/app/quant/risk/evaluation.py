"""Evaluation & Monitoring Suite.

- Walk-forward out-of-sample metrics
- Strategy degradation monitoring
- Regime drift detection
- Benchmark comparison
- Attribution analysis
- Rolling metrics
"""
import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, wasserstein_distance


def walk_forward_oos_metrics(
    oos_returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    annual_factor: int = 252,
) -> dict[str, float]:
    """Comprehensive OOS metrics for walk-forward evaluation."""
    metrics: dict[str, float] = {}
    metrics["total_return"] = float((1 + oos_returns).prod() - 1)
    metrics["cagr"] = float((1 + metrics["total_return"]) ** (annual_factor / len(oos_returns)) - 1)
    metrics["volatility"] = float(oos_returns.std() * np.sqrt(annual_factor))
    metrics["sharpe"] = float(oos_returns.mean() / oos_returns.std() * np.sqrt(annual_factor)) if oos_returns.std() > 0 else 0.0
    metrics["max_drawdown"] = float((oos_returns.cumsum() - oos_returns.cumsum().cummax()).min())
    neg = oos_returns[oos_returns < 0]
    metrics["sortino"] = float(oos_returns.mean() / neg.std() * np.sqrt(annual_factor)) if len(neg) > 0 and neg.std() > 0 else 0.0
    metrics["hit_rate"] = float((oos_returns > 0).mean())
    metrics["profit_factor"] = float(abs(oos_returns[oos_returns > 0].sum() / oos_returns[oos_returns < 0].sum())) if oos_returns[oos_returns < 0].sum() != 0 else float("inf")
    if benchmark_returns is not None:
        aligned = pd.concat([oos_returns, benchmark_returns], axis=1).dropna()
        if len(aligned) > 5:
            strat = aligned.iloc[:, 0]
            bm = aligned.iloc[:, 1]
            cov = np.cov(strat, bm)
            metrics["beta"] = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else 0.0
            metrics["alpha"] = float((strat.mean() - metrics["beta"] * bm.mean()) * annual_factor)
            tracking_error = float((strat - bm).std() * np.sqrt(annual_factor))
            metrics["information_ratio"] = float((strat.mean() - bm.mean()) / (strat - bm).std() * np.sqrt(annual_factor)) if (strat - bm).std() > 0 else 0.0
    return metrics


def rolling_sharpe(
    returns: pd.Series, window: int = 63, annual_factor: int = 252
) -> pd.Series:
    """Rolling annualized Sharpe ratio."""
    roll_mean = returns.rolling(window).mean() * annual_factor
    roll_std = returns.rolling(window).std() * np.sqrt(annual_factor)
    return roll_mean / roll_std


def rolling_max_drawdown(equity: pd.Series, window: int = 252) -> pd.Series:
    """Rolling maximum drawdown within lookback window."""
    roll_max = equity.rolling(window).max()
    dd = (equity - roll_max) / roll_max
    return dd


def regime_drift_test(
    in_sample_returns: pd.Series,
    oos_returns: pd.Series,
) -> dict[str, float]:
    """Detect distribution shift between IS and OOS returns."""
    ks_stat, ks_pval = ks_2samp(in_sample_returns, oos_returns)
    w_dist = wasserstein_distance(in_sample_returns, oos_returns)
    return {
        "ks_statistic": float(ks_stat),
        "ks_p_value": float(ks_pval),
        "wasserstein_distance": float(w_dist),
        "drift_detected": bool(ks_pval < 0.05),
    }


def attribution_analysis(
    weights_history: pd.DataFrame,
    returns_history: pd.DataFrame,
) -> pd.DataFrame:
    """Simple Brinson-style attribution.

    Returns asset contribution to total return.
    """
    aligned = weights_history.align(returns_history, join="inner")
    w, r = aligned
    contribution = (w * r).sum(axis=0)
    total = contribution.sum()
    result = pd.DataFrame({
        "contribution": contribution,
        "weight": w.mean(),
        "return": r.mean(),
    })
    result["pct_of_total"] = result["contribution"] / total if total != 0 else 0.0
    return result.sort_values("contribution", ascending=False)
