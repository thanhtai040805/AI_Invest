"""
Model Monitor
Tracks Information Coefficient (IC) decay and triggers retraining (AGENT-10 logic).
"""

import logging
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class ModelMonitor:
    """
    Monitors live model performance by calculating rolling Information Coefficient (IC).
    Triggers alerts for retraining if IC decays beyond thresholds.
    """
    def __init__(self, baseline_ic: float = 0.05, decay_threshold_pct: float = 0.5):
        self.baseline_ic = baseline_ic
        self.decay_threshold_pct = decay_threshold_pct
        
    def calculate_ic(self, predictions: pd.Series, actual_returns: pd.Series) -> float:
        """
        Rank Information Coefficient (Spearman correlation between predictions and forward returns).
        """
        if len(predictions) < 30:
            return 0.0
            
        # Align series
        df = pd.concat([predictions, actual_returns], axis=1).dropna()
        if len(df) < 30:
            return 0.0
            
        ic, p_val = spearmanr(df.iloc[:, 0], df.iloc[:, 1])
        return ic
        
    def check_decay(self, recent_ic: float) -> bool:
        """
        Check if the recent IC has decayed by more than threshold relative to baseline.
        Returns True if emergency retraining is needed.
        """
        if recent_ic <= 0:
            return True
            
        decay = (self.baseline_ic - recent_ic) / self.baseline_ic
        if decay > self.decay_threshold_pct:
            logger.warning(f"CRITICAL: IC decayed by {decay*100:.1f}%. Threshold is {self.decay_threshold_pct*100}%.")
            return True
            
        return False
        
    def run_daily_check(self, df_logs: pd.DataFrame) -> Dict[str, Any]:
        """
        Run the daily check using a DataFrame of historical predictions vs actuals.
        df_logs must have 'prediction' and 'fwd_return' columns.
        """
        if 'prediction' not in df_logs or 'fwd_return' not in df_logs:
            logger.error("Missing required columns in df_logs for monitoring.")
            return {"status": "ERROR"}
            
        # Calculate 20-day rolling IC
        recent_logs = df_logs.tail(20)
        recent_ic = self.calculate_ic(recent_logs['prediction'], recent_logs['fwd_return'])
        
        needs_retrain = self.check_decay(recent_ic)
        
        result = {
            "recent_ic": recent_ic,
            "baseline_ic": self.baseline_ic,
            "needs_retrain": needs_retrain,
            "status": "CRITICAL_DECAY" if needs_retrain else "HEALTHY"
        }
        
        logger.info(f"Model Monitor Check: {result}")
        return result

model_monitor = ModelMonitor()
