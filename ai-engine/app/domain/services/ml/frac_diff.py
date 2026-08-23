"""
Fractional Differentiation
Based on Marcos López de Prado's Advances in Financial Machine Learning.
Makes price series stationary while preserving maximum memory.
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional
from statsmodels.tsa.stattools import adfuller

logger = logging.getLogger(__name__)

def get_weights_ffd(d: float, threshold: float = 1e-5) -> np.ndarray:
    """
    Calculate fractional differentiation weights for a given d and threshold.
    Fixed-width window method (FFD).
    
    Args:
        d: fractional differentiation value (0 < d < 1)
        threshold: minimum weight value to keep in window
    Returns:
        Array of weights
    """
    w = [1.]
    k = 1
    while True:
        w_ = -w[-1] / k * (d - k + 1)
        if abs(w_) < threshold:
            break
        w.append(w_)
        k += 1
    return np.array(w[::-1]).reshape(-1, 1)

def frac_diff_ffd(series: pd.Series, d: float, threshold: float = 1e-5) -> pd.Series:
    """
    Apply fractional differentiation to a pandas Series using fixed-width window.
    
    Args:
        series: Original non-stationary time series (e.g. price)
        d: fractional differentiation value (0 < d < 1)
        threshold: minimum weight value
    Returns:
        Stationary time series
    """
    if d == 0.0:
        return series
    if d == 1.0:
        return series.diff()
        
    weights = get_weights_ffd(d, threshold)
    width = len(weights)
    
    if len(series) < width:
        # Not enough data for the window, fallback to standard difference
        logger.warning(f"Series length {len(series)} < required window {width} for d={d}. Falling back to standard diff.")
        return series.diff()
        
    # We use valid convolution to drop the first (width-1) NaN values
    res = np.convolve(series.values, weights.flatten(), mode='valid')
    
    # Pad the beginning with NaNs to align with original series index
    pad = np.empty(width - 1)
    pad[:] = np.nan
    
    padded_res = np.concatenate((pad, res))
    return pd.Series(padded_res, index=series.index)

def find_optimal_d(
    series: pd.Series, 
    d_range: np.ndarray = np.arange(0.0, 1.01, 0.05),
    pval_threshold: float = 0.05,
    weight_threshold: float = 1e-5
) -> float:
    """
    Find the minimum value of d that makes the series stationary according to ADF test.
    This preserves the maximum amount of memory.
    
    Args:
        series: Original time series
        d_range: Array of d values to test
        pval_threshold: Significance level for ADF test (default 5%)
        weight_threshold: Weight threshold for FFD
    Returns:
        Optimal d value
    """
    out_d = 1.0
    for d in d_range:
        if d == 0:
            diffed = series.dropna()
        elif d == 1:
            diffed = series.diff().dropna()
        else:
            diffed = frac_diff_ffd(series, d, weight_threshold).dropna()
            
        if len(diffed) < 20: # Not enough data for ADF test
            continue
            
        try:
            adf_pval = adfuller(diffed, maxlag=1, regression='c', autolag=None)[1]
            if adf_pval < pval_threshold:
                out_d = d
                break
        except Exception as e:
            logger.debug(f"ADF test failed for d={d}: {e}")
            continue
            
    return out_d
