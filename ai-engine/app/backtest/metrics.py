"""Backtest performance metrics suite."""
import numpy as np
import pandas as pd


def compute_sharpe(returns: pd.Series, annual_factor: float = 252) -> float:
    """Annualized Sharpe ratio."""
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(annual_factor))


def compute_sortino(
    returns: pd.Series, 
    target_return: float = 0.0, 
    annual_factor: float = 252
) -> float:
    """Sortino ratio (downside deviation only).
    
    Uses standard Root Mean Square of negative deviations across all N samples:
    sigma_d = sqrt(mean(min(0, returns - target)^2))
    """
    if len(returns) < 2:
        return 0.0
    downside_diff = np.minimum(0.0, returns - target_return)
    downside_dev = np.sqrt(np.mean(downside_diff ** 2))
    if downside_dev == 0.0:
        return 0.0
    return float((returns.mean() - target_return) / downside_dev * np.sqrt(annual_factor))


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
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

    Adjusts for multiple testing / selection bias and non-normality.
    Returns probability value in [0, 1] (p-value = 1 - DSR).
    """
    if n_samples < 2:
        return 0.0
    
    from scipy.stats import norm

    # Standard error of Sharpe ratio under null hypothesis (SR = 0)
    sigma_0 = np.sqrt(1.0 / (n_samples - 1.0))

    # Expected maximum Sharpe under null hypothesis (Euler-Mascheroni approximation)
    gamma = 0.57721566490153286
    if n_trials > 1:
        z1 = (1.0 - gamma) * norm.ppf(1.0 - 1.0 / n_trials)
        z2 = gamma * norm.ppf(1.0 - 1.0 / (n_trials * np.e))
        e_max_sr = sigma_0 * float(z1 + z2)
    else:
        e_max_sr = 0.0

    # Variance of Sharpe ratio estimation under non-normality
    sr_var = (1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * (sharpe ** 2)) / (n_samples - 1.0)
    if sr_var <= 0:
        return 0.0
        
    sr_std = np.sqrt(sr_var)
    z = (sharpe - e_max_sr) / (sr_std + 1e-9)
    return float(norm.cdf(z))


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
