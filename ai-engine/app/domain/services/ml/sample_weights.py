"""
Sample Uniqueness & Weights
Based on Marcos López de Prado's Advances in Financial Machine Learning.
Weights samples in overlapping time-series data based on their uniqueness
to avoid over-representation of highly overlapping periods.
"""

import numpy as np
import pandas as pd
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

def num_concurrent_events(t1: pd.Series, molecule: pd.DatetimeIndex) -> pd.Series:
    """
    Compute the number of concurrent events per bar.
    
    Args:
        t1: Series of event end times. Index is event start time.
        molecule: DatetimeIndex of the bars to compute concurrency for.
    Returns:
        Series of concurrent event counts per bar.
    """
    # 1. Find events that span across each bar
    t1 = t1.fillna(t1.index[-1])  # Unclosed events end at the last available bar
    t1 = t1[t1 >= molecule[0]]    # Filter out events ending before our period
    
    # 2. Count concurrent events
    events = pd.Series(0, index=molecule)
    for t_in, t_out in t1.items():
        # Events overlapping with molecule
        overlap = events.loc[t_in:t_out].index
        if len(overlap) > 0:
            events.loc[overlap] += 1
            
    return events

def get_average_uniqueness(t1: pd.Series, num_co_events: pd.Series, molecule: pd.DatetimeIndex) -> pd.Series:
    """
    Compute average uniqueness for each event.
    
    Args:
        t1: Series of event end times. Index is event start time.
        num_co_events: Series of concurrent event counts per bar (from num_concurrent_events).
        molecule: DatetimeIndex of the events to evaluate.
    Returns:
        Series of uniqueness scores [0, 1] for each event.
    """
    weight = pd.Series(index=molecule, dtype=float)
    for t_in, t_out in t1.loc[molecule].items():
        overlap = num_co_events.loc[t_in:t_out]
        if len(overlap) > 0 and overlap.sum() > 0:
            weight.loc[t_in] = (1. / overlap).mean()
        else:
            weight.loc[t_in] = 1.0  # Completely unique if no overlap data
            
    return weight

def get_sample_weights(
    t1: pd.Series, 
    num_co_events: pd.Series, 
    close: pd.Series, 
    molecule: pd.DatetimeIndex
) -> pd.Series:
    """
    Compute sample weights based on return attribution and uniqueness.
    
    Args:
        t1: Series of event end times.
        num_co_events: Series of concurrent event counts per bar.
        close: Price series.
        molecule: DatetimeIndex of the events to evaluate.
    Returns:
        Series of sample weights.
    """
    ret = np.log(close).diff()  # log-returns
    weight = pd.Series(index=molecule, dtype=float)
    
    for t_in, t_out in t1.loc[molecule].items():
        # returns for the duration of the event
        overlap_ret = ret.loc[t_in:t_out]
        overlap_co = num_co_events.loc[t_in:t_out]
        
        if len(overlap_ret) > 0 and len(overlap_co) > 0:
            # Absolute return attribution weighted by uniqueness
            weight.loc[t_in] = (overlap_ret.abs() / overlap_co).sum()
        else:
            weight.loc[t_in] = 0.0
            
    return weight

def apply_time_decay(
    weights: pd.Series, 
    clf_last_w: float = 1.0, 
    decay: float = 1.0
) -> pd.Series:
    """
    Apply time decay to sample weights.
    
    Args:
        weights: Series of sample weights (e.g. from get_sample_weights).
        clf_last_w: Weight of the most recent observation.
        decay: Decay factor. 
               c=1 (no decay), 
               0<c<1 (linear decay), 
               c=0 (step function decay), 
               c<0 (exponential decay)
    Returns:
        Time-decayed weights.
    """
    clf_w = weights.sort_index().cumsum()
    if clf_last_w >= 0:
        slope = (1. - clf_last_w) / clf_w.iloc[-1]
    else:
        slope = 1. / ((clf_last_w + 1) * clf_w.iloc[-1])
        
    decay_w = clf_last_w + slope * clf_w
    decay_w[decay_w < 0] = 0
    
    return decay_w

def compute_sample_weights_pipeline(
    t1: pd.Series,
    molecule: Optional[pd.DatetimeIndex] = None,
    time_decay: bool = False,
    c: float = 0.5
) -> pd.Series:
    """
    Convenience function to compute uniqueness weights for a panel of events.
    For Cross-Sectional models, this uniqueness helps prevent highly overlapping
    macro-driven periods from dominating the training loss.
    """
    if molecule is None:
        molecule = t1.index
        
    # Get overall timeline
    min_time = min(t1.index.min(), t1.min())
    max_time = max(t1.index.max(), t1.max())
    timeline = pd.date_range(start=min_time, end=max_time, freq='D')
    
    # 1. Concurrency
    num_co = num_concurrent_events(t1, timeline)
    
    # 2. Average Uniqueness
    uniqueness = get_average_uniqueness(t1, num_co, molecule)
    
    # Normalize weights so they sum to the number of samples
    weights = uniqueness * (len(uniqueness) / uniqueness.sum())
    
    # 3. Apply optional time decay (more recent = higher weight)
    if time_decay:
        weights = apply_time_decay(weights, clf_last_w=1.0, decay=c)
        
    return weights
