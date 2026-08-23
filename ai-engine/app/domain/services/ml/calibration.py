"""
Conformal Prediction Calibration
Provides theoretically guaranteed confidence intervals for ML predictions.
Replaces legacy Platt Scaling with distribution-free Conformal Predictors.
"""

import numpy as np
import pandas as pd
from typing import Tuple, List

class ConformalPredictor:
    """
    Split Conformal Prediction for classification (Inductive Conformal Prediction).
    Guarantees that the true label is within the predicted set with probability 1-alpha,
    regardless of the underlying distribution or model accuracy.
    """
    def __init__(self, alpha: float = 0.1):
        """
        Args:
            alpha: Significance level (e.g., 0.1 means 90% confidence).
        """
        self.alpha = alpha
        self.q_hat = None
        self.is_calibrated = False
        
    def calibrate(self, calib_probs: np.ndarray, calib_labels: np.ndarray):
        """
        Calibrate using a held-out calibration set.
        
        Args:
            calib_probs: Predicted probabilities for the calibration set (shape: N x n_classes).
            calib_labels: True integer labels for the calibration set (shape: N).
        """
        n = len(calib_labels)
        
        # Calculate non-conformity scores
        # Score = 1 - predicted probability of the true class
        scores = np.zeros(n)
        for i in range(n):
            true_class = int(calib_labels[i])
            scores[i] = 1.0 - calib_probs[i, true_class]
            
        # We want the ceil((n+1)(1-alpha))/n empirical quantile
        val = np.ceil((n + 1) * (1 - self.alpha)) / n
        
        # If val > 1, we can't guarantee coverage with this n and alpha
        if val > 1.0:
            val = 1.0
            
        self.q_hat = np.quantile(scores, val, method='higher')
        self.is_calibrated = True
        
    def predict_set(self, test_probs: np.ndarray) -> List[List[int]]:
        """
        Returns a prediction set for each test instance.
        
        Args:
            test_probs: Predicted probabilities (shape: M x n_classes).
            
        Returns:
            List of lists containing the classes in the prediction set.
            If the set is empty (rare but possible), it means the model is very unconfident.
        """
        if not self.is_calibrated:
            raise ValueError("ConformalPredictor must be calibrated before predicting.")
            
        prediction_sets = []
        for i in range(len(test_probs)):
            # Include class c if its non-conformity score (1 - prob) is <= q_hat
            # Which is equivalent to prob >= 1 - q_hat
            valid_classes = np.where(test_probs[i] >= 1.0 - self.q_hat)[0].tolist()
            prediction_sets.append(valid_classes)
            
        return prediction_sets

# Singleton instance
conformal_predictor = ConformalPredictor(alpha=0.1) # 90% confidence
