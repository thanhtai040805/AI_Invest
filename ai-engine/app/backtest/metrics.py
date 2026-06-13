"""Backtest performance metrics suite."""
import numpy as np
import pandas as pd


def compute_sharpe(returns: pd.Series, annual_factor: float = 252) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(annual_factor))


def compute_sortino(returns: pd.Series, annual_factor: float = 252) -> float:
    """Sortino ratio (downside deviation only)."""
    neg = returns[returns < 0]
    if len(neg) < 1 or neg.std() == 0:
        return 0.0
    return float(returns.mean() / neg.std() * np.sqrt(annual_factor))


def compute_max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown as a fraction."""
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(dd.min())


def compute_calmar_ratio(returns: pd.Series, equity: pd.Series, annual_factor: float = 252) -> float:
    """Calmar ratio: CAGR / max drawdown."""
    n_years = len(returns) / annual_factor
    if n_years <= 0:
        return 0.0
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_years) - 1
    mdd = abs(compute_max_drawdown(equity))
    return float(cagr / mdd) if mdd > 0 else 0.0


def compute_hit_rate(returns: pd.Series) -> float:
    """Percentage of positive periods."""
    if len(returns) == 0:
        return 0.0
    return float((returns > 0).mean())


def compute_profit_factor(returns: pd.Series) -> float:
    """Gross profit / gross loss."""
    gains = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    return float(gains / losses) if losses > 0 else float("inf")


def compute_deflated_sharpe(
    sharpe: float,
    n_samples: int,
    n_trials: int = 1000,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado).

    Adjusts for multiple testing / selection bias.
    """
    if n_samples < 2:
        return 0.0
    e_max_sigma = np.sqrt((4 * n_trials - 4) / (n_trials - 2))
    sharpe_annual = sharpe * np.sqrt(252)
    numerator = sharpe_annual ** 2 - (n_samples - 1) / n_samples * e_max_sigma ** 2
    denominator = np.sqrt((n_samples - 1) / n_samples * (1 + sharpe_annual ** 2 / 4))
    return float(numerator / denominator) if denominator > 0 else 0.0


def compute_alpha_beta(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> tuple[float, float]:
    """Jensen's alpha and beta vs benchmark."""
    aligned = pd.concat([strategy_returns, benchmark_returns], axis=1).dropna()
    if len(aligned) < 5:
        return 0.0, 0.0
    if aligned.iloc[:, 1].nunique() < 2:
        return 0.0, 0.0
    strat = aligned.iloc[:, 0].values
    bm = aligned.iloc[:, 1].values
    cov = np.cov(strat, bm)
    beta = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0.0
    alpha = np.mean(strat) - beta * np.mean(bm)
    return float(alpha * 252), float(beta)
