"""Factor Research Framework.

Gồm:
- Walk-Forward Information Coefficient (IC)
- Alpha Decay (Rank IC over holding periods)
- Regime-Aware IC (condition on volatility / momentum regime)
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def compute_ic(factor: pd.Series, forward_return: pd.Series) -> float:
    """Cross-sectional Spearman rank IC between factor and forward return."""
    combined = pd.concat([factor, forward_return], axis=1).dropna()
    if len(combined) < 10:
        return 0.0
    return float(spearmanr(combined.iloc[:, 0], combined.iloc[:, 1])[0])


def compute_group_ic(group: pd.DataFrame) -> float:
    """For a (factor, ret) 2-column DataFrame, compute one IC."""
    return compute_ic(group.iloc[:, 0], group.iloc[:, 1])


def walk_forward_ic(
    factor_df: pd.DataFrame,
    price_df: pd.DataFrame,
    window: int = 252,
    hold: int = 5,
) -> pd.Series:
    """Walk-forward information coefficient time series.

    Args:
        factor_df: index=date, columns=symbols, values=factor z-score
        price_df: index=date, columns=symbols, values=adjusted close
        window: estimation window in trading days
        hold: holding period in trading days for forward return

    Returns:
        pd.Series of IC values indexed by the prediction date
    """
    ret_df = price_df.pct_change(hold).shift(-hold)
    ic_values = {}
    dates = sorted(factor_df.index)
    for i in range(window, len(dates)):
        start = dates[i - window]
        mid = dates[i]
        train_factors = factor_df.loc[start:mid].iloc[-hold:].mean()
        train_returns = ret_df.loc[mid:mid].iloc[0]
        if len(train_factors) < 10:
            continue
        ic_values[mid] = compute_ic(train_factors, train_returns)
    return pd.Series(ic_values, name="walk_forward_ic")


def factor_alpha_decay(
    factor_df: pd.DataFrame,
    price_df: pd.DataFrame,
    holding_periods: list[int] | None = None,
) -> pd.DataFrame:
    """Alpha decay: IC vs holding period.

    Returns sorted IC for each holding period — decay means
    short-horizon IC > long-horizon IC.
    """
    hp = holding_periods or [1, 3, 5, 10, 21, 42, 63]
    results = {}
    for h in hp:
        ret = price_df.pct_change(h).shift(-h)
        aligned = factor_df.align(ret, join="inner")
        ic_list = [
            compute_ic(aligned[0].iloc[t], aligned[1].iloc[t])
            for t in range(len(aligned[0]))
            if len(aligned[0].iloc[t].dropna()) >= 10
        ]
        results[h] = float(np.mean(ic_list)) if ic_list else 0.0
    return pd.DataFrame(list(results.items()), columns=["holding_period", "mean_ic"])


def regime_aware_ic(
    factor_df: pd.DataFrame,
    price_df: pd.DataFrame,
    regime_series: pd.Series,
    hold: int = 5,
) -> dict[str, float]:
    """IC conditional on market regime.

    Args:
        regime_series: index=date, values=str regime label (e.g. "high_vol", "low_vol")

    Returns:
        dict mapping regime label to mean IC within that regime
    """
    ret = price_df.pct_change(hold).shift(-hold)
    ic_by_regime: dict[str, list[float]] = {}
    for t in range(len(factor_df)):
        dt = factor_df.index[t]
        if dt not in regime_series.index:
            continue
        reg = regime_series.loc[dt]
        ic = compute_ic(factor_df.iloc[t], ret.iloc[t])
        if np.isnan(ic) or abs(ic) > 1:
            continue
        ic_by_regime.setdefault(reg, []).append(ic)
    return {k: float(np.mean(v)) for k, v in ic_by_regime.items() if v}


def compute_quantile_returns(
    factor_df: pd.DataFrame,
    price_df: pd.DataFrame,
    n_quantiles: int = 5,
    hold: int = 5,
) -> pd.DataFrame:
    """Forward equal-weighted return per factor quantile."""
    ret = price_df.pct_change(hold).shift(-hold)
    dates = factor_df.index.intersection(ret.index)
    results = []
    for dt in dates:
        f = factor_df.loc[dt].dropna()
        r = ret.loc[dt]
        common = f.index.intersection(r.index)
        f = f[common]
        r = r[common]
        if len(f) < 20:
            continue
        q_labels = pd.qcut(f, n_quantiles, labels=list(range(1, n_quantiles + 1)))
        for q in range(1, n_quantiles + 1):
            mask = q_labels == q
            q_ret = r[mask].mean()
            results.append({"date": dt, "quantile": q, "return": q_ret})
    return pd.DataFrame(results)
