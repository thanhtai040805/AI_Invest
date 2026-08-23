"""
HMM Regime Engine Evaluation
Walk-forward validation specifically designed for Regime Switching Models.
"""

import logging
import numpy as np
import pandas as pd
from typing import List, Tuple, Dict
from sklearn.metrics import brier_score_loss

from .hmm_regime_engine import RegimeEngineV2, MarketRegimeV2

logger = logging.getLogger(__name__)

def walk_forward_hmm_eval(
    df: pd.DataFrame, 
    initial_train_size: int = 1000, 
    step_size: int = 20, 
    n_components: int = 6
) -> Dict[str, float]:
    """
    Evaluate HMM using Walk-Forward methodology.
    
    Because HMM regimes are unsupervised, we can't use standard supervised accuracy.
    Instead, we measure:
    1. Volatility capture: Does the inferred regime accurately predict forward volatility?
    2. Regime stability: How often does the regime flip back and forth?
    
    Args:
        df: DataFrame containing the full history of required features.
        initial_train_size: Number of days for the initial training window.
        step_size: Retrain interval (e.g. 20 days = ~1 month).
        n_components: Number of regimes.
        
    Returns:
        Dict of evaluation metrics.
    """
    if len(df) < initial_train_size + step_size:
        logger.error("Data too short for walk-forward evaluation.")
        return {}
        
    engine = RegimeEngineV2(n_components=n_components)
    
    predictions = []
    actual_fwd_vols = []
    
    # Calculate 5-day forward volatility as a target metric
    df['fwd_ret'] = df['close'].pct_change().shift(-1)
    df['fwd_vol_5d'] = df['fwd_ret'].rolling(5).std().shift(-5)
    
    n_steps = (len(df) - initial_train_size) // step_size
    
    flips = 0
    total_preds = 0
    prev_regime = None
    
    for i in range(n_steps):
        train_end = initial_train_size + i * step_size
        train_df = df.iloc[:train_end].copy()
        
        # Fit engine on expanding window
        engine.fit(train_df)
        
        # Infer on the next step_size days
        test_start = train_end - 20 # Need history for inference features
        test_end = train_end + step_size
        
        if test_end > len(df):
            test_end = len(df)
            
        test_df = df.iloc[test_start:test_end].copy()
        
        # We need to run inference day by day to simulate reality
        for t in range(20, len(test_df)): # skip the first 20 days used for feature calc
            daily_slice = test_df.iloc[:t+1]
            probs = engine.infer_daily(daily_slice)
            
            # Most likely regime
            max_regime = max(probs, key=probs.get)
            predictions.append((probs, max_regime))
            
            if prev_regime is not None and max_regime != prev_regime:
                flips += 1
            prev_regime = max_regime
            total_preds += 1
            
            # Store target
            actual_vol = test_df.iloc[t]['fwd_vol_5d']
            actual_fwd_vols.append(actual_vol)
            
    # Metrics Calculation
    
    # 1. Stability (Turnover rate of regimes)
    flip_rate = flips / total_preds if total_preds > 0 else 0
    
    # 2. Volatility grouping capability
    # Do Bear Panic / Bear Grinding regimes actually have higher forward volatility?
    regime_vols = {r: [] for r in MarketRegimeV2.get_all()}
    
    for (probs, max_regime), vol in zip(predictions, actual_fwd_vols):
        if not pd.isna(vol):
            regime_vols[max_regime].append(vol)
            
    avg_vols = {r: np.mean(v) if len(v) > 0 else 0 for r, v in regime_vols.items()}
    
    # Expected ordering: PANIC > RECOVERY > MOMENTUM > DISTRIBUTION > GRINDING > RANGE_BOUND
    # This is rough, but PANIC should definitely have the highest.
    panic_vol = avg_vols.get(MarketRegimeV2.BEAR_PANIC, 0)
    range_vol = avg_vols.get(MarketRegimeV2.RANGE_BOUND, 0)
    
    vol_discrimination = panic_vol / range_vol if range_vol > 0 else 0
    
    metrics = {
        "flip_rate": flip_rate,
        "vol_discrimination_ratio": vol_discrimination,
        "avg_vol_bear_panic": panic_vol,
        "avg_vol_range_bound": range_vol
    }
    
    logger.info(f"Walk-forward evaluation complete: {metrics}")
    return metrics
