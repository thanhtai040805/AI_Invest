"""
Triple Barrier Labeling & Meta-Labeling
Based on Marcos López de Prado's Advances in Financial Machine Learning.
Adapted for Vietnamese Stock Market (HOSE) with ±7% limits and T+2 settlement.
"""

import numpy as np
import pandas as pd
from typing import Tuple, List, Optional
import logging

logger = logging.getLogger(__name__)

def apply_pt_sl_on_t1(
    close: pd.Series,
    events: pd.DataFrame,
    pt_sl: List[float],
    molecule: pd.Index
) -> pd.DataFrame:
    """
    Apply profit taking and stop loss.
    Returns DataFrame with timestamps of when the barriers were touched.
    """
    out = events[['t1']].copy(deep=True)
    if pt_sl[0] > 0:
        pt = pt_sl[0] * events['trgt']
    else:
        pt = pd.Series(index=events.index, dtype=float)  # NaN

    if pt_sl[1] > 0:
        sl = -pt_sl[1] * events['trgt']
    else:
        sl = pd.Series(index=events.index, dtype=float)  # NaN

    for loc, t1 in events['t1'].fillna(close.index[-1]).items():
        if loc not in molecule:
            continue
        df0 = close[loc:t1]  # path prices
        if len(df0) <= 1:
            continue
            
        df0 = (df0 / close[loc] - 1)  # path returns
        
        # Upper barrier (profit taking)
        out.loc[loc, 'sl'] = df0[df0 < sl[loc]].index.min()
        out.loc[loc, 'pt'] = df0[df0 > pt[loc]].index.min()
        
    return out

def get_events(
    close: pd.Series,
    t_events: pd.Index,
    pt_sl: List[float],
    target: pd.Series,
    min_ret: float = 0.005,
    num_threads: int = 1,
    t1: bool = False,
    side: Optional[pd.Series] = None,
    t_settle: int = 2,
    hose_limit: float = 0.068  # slightly less than 0.07 to ensure capture
) -> pd.DataFrame:
    """
    Get Triple Barrier events.
    Args:
        close: A pandas series of prices.
        t_events: The pandas timeindex containing the timestamps that will seed every barrier.
        pt_sl: A list of two non-negative float values: [profit_take_mult, stop_loss_mult].
        target: A pandas series of targets (usually volatility), expressed in terms of absolute returns.
        min_ret: The minimum target return required for running a triple barrier search.
        t1: A pandas series with the timestamps of the vertical barriers. Pass False to disable.
        side: (Optional) Side of the bet (1 for long, -1 for short).
        t_settle: T+2 settlement lag. Barriers are only evaluated after T+settle days.
        hose_limit: Max daily move due to ceiling/floor limit.
    """
    # 1. Get target
    trgt = target.loc[t_events]
    trgt = trgt[trgt > min_ret]
    
    # 2. Get t1 (maximum holding period / vertical barrier)
    if t1 is False:
        t1 = pd.Series(pd.NaT, index=t_events)
    else:
        # If t1 is provided as an integer (e.g. 10 days), we create the vertical barriers
        if isinstance(t1, int):
            t1_series = pd.Series(index=t_events, dtype='datetime64[ns]')
            for i, idx in enumerate(t_events):
                pos = close.index.get_loc(idx)
                # + t_settle because we hold t1 days *after* settlement
                end_pos = min(pos + t_settle + t1, len(close) - 1)
                t1_series[idx] = close.index[end_pos]
            t1 = t1_series
            
    # 3. Form events object
    if side is None:
        side_ = pd.Series(1., index=trgt.index)  # Default long
    else:
        side_ = side.loc[trgt.index]
        
    events = pd.concat({'t1': t1, 'trgt': trgt, 'side': side_}, axis=1).dropna(subset=['trgt'])
    
    # Apply HOSE Limits to targets
    events['trgt'] = events['trgt'].clip(upper=hose_limit)
    
    # 4. Get profit taking and stop loss timestamps
    # Since VN requires T+2, we should ideally start checking path from T+2
    # For simplicity, we just shift the evaluation logic in a custom way
    out = pd.DataFrame(index=events.index)
    
    for loc in events.index:
        pos = close.index.get_loc(loc)
        start_eval_pos = min(pos + t_settle, len(close) - 1)
        start_eval_idx = close.index[start_eval_pos]
        end_eval_idx = events.loc[loc, 't1']
        
        if pd.isna(end_eval_idx):
            end_eval_idx = close.index[-1]
            
        path = close.loc[start_eval_idx:end_eval_idx]
        if len(path) <= 1:
            out.loc[loc, 'pt'] = pd.NaT
            out.loc[loc, 'sl'] = pd.NaT
            continue
            
        ret_path = (path / close.loc[loc]) - 1.0
        ret_path = ret_path * events.loc[loc, 'side'] # adjust for side
        
        pt_thresh = pt_sl[0] * events.loc[loc, 'trgt']
        sl_thresh = -pt_sl[1] * events.loc[loc, 'trgt']
        
        # Find first touch
        pt_hits = ret_path[ret_path >= pt_thresh]
        sl_hits = ret_path[ret_path <= sl_thresh]
        
        out.loc[loc, 'pt'] = pt_hits.index.min() if not pt_hits.empty else pd.NaT
        out.loc[loc, 'sl'] = sl_hits.index.min() if not sl_hits.empty else pd.NaT

    events['pt'] = out['pt']
    events['sl'] = out['sl']
    
    return events

def get_bins(events: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """
    Generate labels (+1, 0, -1) based on which barrier was touched first.
    """
    # 1. Determine which barrier is hit first
    events_ = events.dropna(subset=['t1'])
    
    # Compare timestamps (pt vs sl vs t1)
    # If pt/sl are NaT, they are not hit.
    px = events_.index
    for loc, t1 in events_['t1'].fillna(close.index[-1]).items():
        df0 = close[loc:t1]
        if len(df0) == 0: continue
    
    out = pd.DataFrame(index=events_.index)
    out['ret'] = np.nan
    out['bin'] = np.nan
    
    for loc, row in events_.iterrows():
        first_touch = pd.Series({
            'pt': row['pt'],
            'sl': row['sl'],
            't1': row['t1']
        }).dropna().min()
        
        if pd.isna(first_touch):
            continue
            
        ret = (close.loc[first_touch] / close.loc[loc]) - 1.0
        out.loc[loc, 'ret'] = ret
        
        if first_touch == row['pt']:
            out.loc[loc, 'bin'] = 1
        elif first_touch == row['sl']:
            out.loc[loc, 'bin'] = -1
        else:
            out.loc[loc, 'bin'] = 0
            
    # If meta-labeling (side is provided), label is 1 if side == bin, else 0
    if 'side' in events_.columns:
        out['meta_label'] = (np.sign(out['ret']) == events_['side']).astype(int)
        
    return out

class MetaLabeler:
    """
    Meta-labeling model for sizing bets.
    Trains a secondary model to predict the probability of success of the primary model.
    """
    def __init__(self, model):
        self.model = model # e.g., RandomForestClassifier, LGBMClassifier
        
    def fit(self, X: pd.DataFrame, primary_predictions: pd.Series, y_true: pd.Series):
        """
        X: features
        primary_predictions: {-1, 0, 1}
        y_true: actual triple barrier labels {-1, 0, 1}
        """
        # We only train meta-model on instances where primary model made a bet (not 0)
        idx = primary_predictions[primary_predictions != 0].index
        if len(idx) == 0:
            return
            
        X_train = X.loc[idx]
        prim_pred = primary_predictions.loc[idx]
        y_real = y_true.loc[idx]
        
        # Meta label: 1 if primary was right, 0 otherwise
        meta_y = (prim_pred == y_real).astype(int)
        
        # Feature matrix for meta-model includes the primary prediction
        X_meta = X_train.copy()
        X_meta['primary_pred'] = prim_pred
        
        self.model.fit(X_meta, meta_y)
        
    def predict_proba(self, X: pd.DataFrame, primary_predictions: pd.Series) -> pd.Series:
        X_meta = X.copy()
        X_meta['primary_pred'] = primary_predictions
        
        probs = self.model.predict_proba(X_meta)
        return pd.Series(probs[:, 1], index=X.index) # Prob of being correct

