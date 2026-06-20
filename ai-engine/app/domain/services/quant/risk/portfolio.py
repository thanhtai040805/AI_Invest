"""Portfolio Construction & Risk Model.

- Ledoit-Wolf covariance shrinkage
- Mean-Variance optimizer (max Sharpe, min vol, risk parity)
- Volatility targeting
- Kelly sizing
- Turnover penalization
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize


def ledoit_wolf_shrinkage(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf shrinkage covariance estimator.

    Returns (shrunk_cov, shrinkage_coefficient).
    """
    t, n = returns.shape
    sample_cov = returns.cov().values
    if n <= 1:
        return sample_cov, 0.0

    mean_returns = returns.mean().values
    var = returns.var().values

    centered = returns.values - mean_returns

    phi = np.zeros((n, n))
    for i in range(t):
        xi = centered[i]
        phi += np.outer(xi, xi) ** 2
    phi /= t

    pi_ij = phi - sample_cov ** 2
    pi_ij_diag = pi_ij.diagonal().copy()
    np.fill_diagonal(pi_ij, 0)
    pi = pi_ij.sum()

    theta = np.diag((phi.diagonal() - var ** 2) / t)
    rho = ((sample_cov - np.diag(var)) ** 2).sum()
    gamma = np.linalg.norm(sample_cov - np.diag(var), "fro") ** 2

    shrinkage = max(0, min(1, (pi - rho) / gamma)) if gamma > 0 else 0.0

    prior = np.diag(np.full(n, var.mean()))
    shrunk = shrinkage * prior + (1 - shrinkage) * sample_cov
    return shrunk, shrinkage


def max_sharpe_weights(
    cov: np.ndarray,
    expected_returns: np.ndarray,
    risk_free: float = 0.0,
    weight_bounds: tuple[float, float] = (0.0, 0.10),
    turnover_penalty: float = 0.0,
    prev_weights: np.ndarray | None = None,
) -> np.ndarray:
    """Maximize Sharpe ratio with optional turnover penalty."""
    n = len(expected_returns)

    def neg_sharpe(w):
        ret = w @ expected_returns - risk_free
        vol = np.sqrt(w @ cov @ w)
        penalty = 0.0
        if prev_weights is not None:
            penalty = turnover_penalty * np.sum(np.abs(w - prev_weights))
        return -(ret / vol) + penalty if vol > 0 else 0.0

    constraints = [
        {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
    ]
    bounds = [weight_bounds] * n
    init = np.full(n, 1.0 / n)
    result = minimize(neg_sharpe, init, method="SLSQP", bounds=bounds, constraints=constraints)
    if result.success:
        return result.x
    return init


def min_vol_weights(
    cov: np.ndarray,
    weight_bounds: tuple[float, float] = (0.0, 0.10),
) -> np.ndarray:
    """Minimum variance portfolio."""
    n = cov.shape[0]

    def portfolio_vol(w):
        return np.sqrt(w @ cov @ w)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [weight_bounds] * n
    init = np.full(n, 1.0 / n)
    result = minimize(portfolio_vol, init, method="SLSQP", bounds=bounds, constraints=constraints)
    if result.success:
        return result.x
    return init


def risk_parity_weights(cov: np.ndarray) -> np.ndarray:
    """Equal risk contribution (risk parity)."""
    n = cov.shape[0]

    def risk_parity_obj(w):
        portfolio_vol = np.sqrt(w @ cov @ w)
        rc = w * (cov @ w) / portfolio_vol
        rc_mean = rc.mean()
        return np.sum((rc - rc_mean) ** 2)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0.0, 1.0)] * n
    init = np.full(n, 1.0 / n)
    result = minimize(risk_parity_obj, init, method="SLSQP", bounds=bounds, constraints=constraints)
    if result.success:
        return result.x
    return init


def volatility_target_weights(
    weights: np.ndarray,
    cov: np.ndarray,
    target_vol: float = 0.15,
) -> np.ndarray:
    """Scale portfolio to target annualized volatility."""
    port_vol = np.sqrt(weights @ cov @ weights) * np.sqrt(252)
    if port_vol <= 0:
        return weights
    scale = target_vol / port_vol
    return weights * scale


def kelly_fraction(
    expected_return: float,
    variance: float,
    max_loss: float | None = None,
) -> float:
    """Kelly criterion fraction, bounded [0, 1].

    For continuous distribution: f* = mu / sigma^2.
    """
    if variance <= 0:
        return 0.0
    f = expected_return / variance
    if max_loss and max_loss > 0:
        f = min(f, max_loss)
    return float(max(0, min(f, 1.0)))


def compute_implied_alpha(
    weights: np.ndarray,
    cov: np.ndarray,
    risk_aversion: float = 2.0,
) -> np.ndarray:
    """Implied expected returns from current weights (reverse optimization)."""
    return risk_aversion * (cov @ weights)


def apply_weight_caps(
    weights: pd.Series,
    max_weight: float = 0.10,
    min_weight: float = 0.0,
) -> pd.Series:
    """Enforce min/max weight bounds, redistribute excess."""
    w = weights.copy()
    excess = w[w > max_weight].sum() - (w > max_weight).sum() * max_weight
    w[w > max_weight] = max_weight
    mask = (w > min_weight) & (w < max_weight)
    if mask.sum() > 0 and excess > 0:
        w[mask] += excess * w[mask] / w[mask].sum()
    w[w < min_weight] = 0.0
    return w / w.sum()
