"""
Walk-Forward Validator
Runs Purged Combinatorial K-Fold (CPKF) to validate RAES and HMM engines robustly.
Ensures no information leakage across time steps.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Any

from app.domain.services.ml.purged_cv import PurgedCombinatorialKFold
from app.domain.services.ml.raes_engine import RAESEngine
from sklearn.metrics import accuracy_score, precision_score, recall_score

logger = logging.getLogger(__name__)

class WalkForwardValidator:
    def __init__(self):
        self.cpkf = PurgedCombinatorialKFold(n_splits=5, n_test_splits=2, embargo_td=pd.Timedelta(days=3))
        
    def validate_raes(self, X: pd.DataFrame, y: pd.Series, t1: pd.Series) -> Dict[str, float]:
        """
        Validate RAES engine using CPKF.
        """
        logger.info("Starting Purged Combinatorial K-Fold Validation for RAES...")
        
        acc_scores = []
        prec_scores = []
        rec_scores = []
        
        # In a real heavy pipeline, we would clone the engine. 
        # Here we just instantiate fresh inner models for evaluation purposes.
        for i, (train_idx, test_idx) in enumerate(self.cpkf.split(X, y, t1)):
            logger.info(f"CV Fold {i+1}...")
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
            
            # Temporary RAES engine for CV
            cv_engine = RAESEngine()
            cv_engine.fit(X_train, y_train) # Skipping sample weights in CV for speed
            
            # Predict (assuming uniform regime for isolation testing)
            uniform_regime = {r: 1.0/6.0 for r in ["BULL_MOMENTUM", "BULL_DISTRIBUTION", "RANGE_BOUND", "BEAR_GRINDING", "BEAR_PANIC", "RECOVERY_EARLY"]}
            
            preds = []
            for _, row in X_test.iterrows():
                # Extract single row dataframe
                row_df = pd.DataFrame([row])
                pred_class, _ = cv_engine.predict(row_df, uniform_regime)
                preds.append(pred_class)
                
            # y_test is -1, 0, 1. We mapped 1 -> 1, rest -> 0 for RAES.
            y_test_bin = (y_test == 1).astype(int)
            
            acc = accuracy_score(y_test_bin, preds)
            prec = precision_score(y_test_bin, preds, zero_division=0)
            rec = recall_score(y_test_bin, preds, zero_division=0)
            
            acc_scores.append(acc)
            prec_scores.append(prec)
            rec_scores.append(rec)
            
        metrics = {
            "cv_accuracy_mean": float(np.mean(acc_scores)),
            "cv_accuracy_std": float(np.std(acc_scores)),
            "cv_precision_mean": float(np.mean(prec_scores)),
            "cv_recall_mean": float(np.mean(rec_scores))
        }
        
        logger.info(f"CV Validation Complete: {metrics}")
        return metrics

walk_forward_validator = WalkForwardValidator()
