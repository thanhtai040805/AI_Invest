"""Walk-Forward Validator — K-Fold Walk-Forward Validation for Production ML Alpha Predictors."""

import logging
import numpy as np
import pandas as pd
from typing import Dict, Any

from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.model_selection import KFold

logger = logging.getLogger(__name__)


class WalkForwardValidator:
    def __init__(self, n_splits: int = 5):
        self.kf = KFold(n_splits=n_splits, shuffle=False)

    def validate_ranker(self, X: pd.DataFrame, y: pd.Series) -> Dict[str, float]:
        """Validate Cross-Sectional Alpha Ranker using Walk-Forward K-Fold splits."""
        logger.info("Starting Walk-Forward Validation for ML Alpha Ranker...")

        acc_scores = []
        prec_scores = []
        rec_scores = []

        for i, (train_idx, test_idx) in enumerate(self.kf.split(X)):
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

            ranker = CrossSectionalRanker()
            ranker.fit(X_train, y_train)
            preds = (ranker.predict(X_test) >= 0.5).astype(int)
            y_test_bin = (y_test >= 0.5).astype(int)

            acc_scores.append(accuracy_score(y_test_bin, preds))
            prec_scores.append(precision_score(y_test_bin, preds, zero_division=0))
            rec_scores.append(recall_score(y_test_bin, preds, zero_division=0))

        metrics = {
            "cv_accuracy_mean": float(np.mean(acc_scores)),
            "cv_accuracy_std": float(np.std(acc_scores)),
            "cv_precision_mean": float(np.mean(prec_scores)),
            "cv_recall_mean": float(np.mean(rec_scores)),
        }

        logger.info(f"CV Validation Complete: {metrics}")
        return metrics


walk_forward_validator = WalkForwardValidator()
