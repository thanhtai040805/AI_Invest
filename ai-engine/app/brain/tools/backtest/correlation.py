"""Cross-asset correlation matrix computation for Vietnam stocks.

Computes pairwise Pearson or Spearman correlation of daily returns
over a configurable lookback window.
"""

from __future__ import annotations

from typing import Dict, Literal

import pandas as pd
import numpy as np
from scipy.stats import spearmanr


def _rolling_correlation_matrix(
    price_series: Dict[str, pd.DataFrame],
    window: int,
    method: Literal["pearson", "spearman"],
) -> tuple[list[str], list[list[float]]]:
    """Compute correlation matrix for multiple price series."""
    if not price_series:
        return [], []

    codes = sorted(price_series.keys())

    returns_frames = []
    closes = {}
    for code, df in price_series.items():
        if df.empty:
            raise ValueError(f"Price series for '{code}' is empty")
        if "close" not in df.columns and "close" not in df.index.names:
            raise ValueError(f"No 'close' column in price series for '{code}'")
        if "trade_date" in df.index.names and "trade_date" not in df.columns:
            ts = df["close"]
        else:
            ts = df.set_index("trade_date")["close"]
        closes[code] = ts.sort_index()

    for code in codes:
        ts = closes[code]
        rets = ts.pct_change().dropna()
        rets.name = code
        returns_frames.append(rets)

    aligned = pd.concat(returns_frames, axis=1).dropna()
    if aligned.empty:
        raise ValueError("No overlapping return data between assets")

    if len(aligned) > window:
        aligned = aligned.iloc[-window:]

    n = len(aligned)
    if n < 2:
        raise ValueError("Not enough data points to compute correlation")

    labels = codes
    n_assets = len(labels)
    matrix = [[1.0] * n_assets for _ in range(n_assets)]

    for i in range(n_assets):
        for j in range(i + 1, n_assets):
            xi = aligned.iloc[:, i].values
            xj = aligned.iloc[:, j].values
            if method == "spearman":
                corr, _ = spearmanr(xi, xj)
            else:
                corr = np.corrcoef(xi, xj)[0, 1]
            if np.isnan(corr):
                corr = 0.0
            matrix[i][j] = round(corr, 4)
            matrix[j][i] = round(corr, 4)

    return labels, matrix


def compute_correlation_matrix(
    codes: list[str],
    days: int = 90,
    method: Literal["pearson", "spearman"] = "pearson",
) -> Dict[str, object]:
    """Fetch VN price data and compute correlation matrix.

    Args:
        codes: List of VN stock codes (e.g. ["FPT", "VCB", "HPG"]).
        days: Lookback window in days (default 90).
        method: Correlation method.

    Returns:
        Dict with keys: labels, matrix, window, method.
    """
    from datetime import datetime, timedelta

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days + 60)).strftime("%Y-%m-%d")

    from backtest.loaders.registry import resolve_loader

    price_series: Dict[str, pd.DataFrame] = {}

    try:
        loader = resolve_loader("vn_equity")
    except Exception as exc:
        raise ValueError(f"No VN data source available: {exc}")

    for code in codes:
        try:
            result = loader.fetch(
                codes=[code],
                start_date=start_date,
                end_date=end_date,
                interval="1D",
            )
            if code in result and not result[code].empty:
                price_series[code] = result[code]
        except Exception:
            continue

    if len(price_series) < 2:
        raise ValueError(
            f"Could not fetch price data for at least 2 assets. "
            f"Fetched: {list(price_series.keys())}"
        )

    labels, matrix = _rolling_correlation_matrix(price_series, days, method)
    return {
        "labels": labels,
        "matrix": matrix,
        "window": days,
        "method": method,
    }
