"""Quantitative data cleaning and normalization pipeline.

Provides functions to clean raw market and fundamental panels:
1. Imputation: Fill NaNs via forward-fill (ffill) and backward-fill (bfill).
2. Winsorization: Cap extreme outlier values in each cross-section (row) at specific percentiles.
3. Normalization: Cross-sectional Z-score normalization.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def impute_panel(df: pd.DataFrame, method: str = "ffill_bfill") -> pd.DataFrame:
    """Impute missing values (NaNs) in the wide panel.

    Args:
        df: Wide DataFrame where index=date, columns=symbols.
        method: Imputation strategy ('ffill_bfill' | 'ffill' | 'bfill' | 'zero').

    Returns:
        Imputed DataFrame.
    """
    if df.empty:
        return df.copy()

    df_cleaned = df.copy()
    if method == "ffill_bfill":
        # Forward fill along time axis first (columns represent tickers)
        df_cleaned = df_cleaned.ffill(axis=0).bfill(axis=0)
    elif method == "ffill":
        df_cleaned = df_cleaned.ffill(axis=0)
    elif method == "bfill":
        df_cleaned = df_cleaned.bfill(axis=0)
    elif method == "zero":
        df_cleaned = df_cleaned.fillna(0.0)
    
    return df_cleaned


def winsorize_panel(df: pd.DataFrame, lower_quantile: float = 0.01, upper_quantile: float = 0.01) -> pd.DataFrame:
    """Cip extreme outlier values in each cross-section (row) of the panel.

    Args:
        df: Wide DataFrame (index=date, columns=symbols).
        lower_quantile: Bottom quantile to cap (e.g. 0.01 for 1st percentile).
        upper_quantile: Top quantile to cap (e.g. 0.01 for 99th percentile).

    Returns:
        Winsorized DataFrame.
    """
    if df.empty:
        return df.copy()

    def _winsorize_row(row: pd.Series) -> pd.Series:
        if row.isna().all():
            return row
        
        # Calculate quantiles ignoring NaNs
        q_low = row.quantile(lower_quantile)
        q_high = row.quantile(1.0 - upper_quantile)
        
        # Clip the row values within bounds
        return row.clip(lower=q_low, upper=q_high)

    # Apply row-wise (cross-sectional clipping per date)
    return df.apply(_winsorize_row, axis=1)


def normalize_zscore(df: pd.DataFrame, min_std: float = 1e-12) -> pd.DataFrame:
    """Perform cross-sectional Z-score normalization for each date (row-wise).

    Args:
        df: Wide DataFrame (index=date, columns=symbols).
        min_std: Minimum standard deviation limit to avoid zero division.

    Returns:
        Z-scored normalized DataFrame.
    """
    if df.empty:
        return df.copy()

    # Calculate row-wise mean and std (ignoring NaNs)
    row_mean = df.mean(axis=1, skipna=True)
    row_std = df.std(axis=1, ddof=1, skipna=True)
    
    # Handle zero/NaN standard deviations to avoid inf
    row_std = row_std.where(row_std > min_std, np.nan)
    
    # Center and scale
    centered = df.sub(row_mean, axis=0)
    z_scored = centered.div(row_std, axis=0)
    
    return z_scored.replace([np.inf, -np.inf], np.nan)


def clean_and_normalize_panel(
    df: pd.DataFrame,
    impute_method: str = "ffill_bfill",
    lower_quantile: float = 0.01,
    upper_quantile: float = 0.01
) -> pd.DataFrame:
    """Full data preparation pipeline for quantitative factor models.

    Applies Imputation -> Winsorization -> Z-score normalization.

    Args:
        df: Raw factor or return panel DataFrame.
        impute_method: Strategy to fill NaNs.
        lower_quantile: Lower bound for extreme capping.
        upper_quantile: Upper bound for extreme capping.

    Returns:
        Clean, winsorized, and normalized panel.
    """
    df_imputed = impute_panel(df, method=impute_method)
    df_winsorized = winsorize_panel(df_imputed, lower_quantile=lower_quantile, upper_quantile=upper_quantile)
    df_normalized = normalize_zscore(df_winsorized)
    return df_normalized
