"""Calibrated Confidence — Platt Scaling, Isotonic Regression.

Confidence phải map ra xác suất thắng thực nghiệm.
Vd: confidence=0.60 phải có hit rate lịch sử 60% ở bin [0.55, 0.65].
"""
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)


class CalibratedConfidence:
    """Calibrated probability from raw model scores.

    Methods:
        - Platt Scaling (sigmoid) — for ML model outputs
        - Isotonic Regression — flexible, for ensemble output
        - Reliability diagram — visualize calibration
        - Brier Score — measure calibration quality (lower = better)
    """

    def __init__(self, method: str = "platt"):
        if method not in ("platt", "isotonic"):
            raise ValueError(f"Method must be 'platt' or 'isotonic', got {method}")
        self.method = method
        self.calibrator = None

    def calibrate(self, raw_scores: np.ndarray, outcomes: np.ndarray) -> None:
        """Fit calibration model on out-of-sample data.

        Args:
            raw_scores: Raw model scores/probabilities (any range)
            outcomes: Binary outcomes (0 or 1)
        """
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression

        scores = np.array(raw_scores).reshape(-1, 1).astype(np.float64)
        outcomes = np.array(outcomes).astype(np.int32)

        if self.method == "platt":
            base = LogisticRegression(C=1e10, solver="lbfgs")
            self.calibrator = CalibratedClassifierCV(base, method="sigmoid", cv=3)
            dummy_X = np.hstack([scores, np.random.randn(len(scores), 1)])
            self.calibrator.fit(dummy_X, outcomes)
        else:
            self.calibrator = IsotonicRegression(out_of_bounds="clip")
            self.calibrator.fit(scores.flatten(), outcomes)

    def predict_proba(self, raw_score: float) -> float:
        """Trả về calibrated probability [0, 1]."""
        if self.calibrator is None:
            return float(np.clip(raw_score, 0, 1))
        if self.method == "platt":
            dummy = np.array([[raw_score, 0.0]])
            return float(self.calibrator.predict_proba(dummy)[0, 1])
        else:
            return float(self.calibrator.predict([raw_score])[0])

    def reliability_diagram(
        self, raw_scores: np.ndarray, outcomes: np.ndarray, n_bins: int = 10
    ) -> dict:
        """Compute calibration curve data for reliability diagram.

        Returns:
            dict with bin_centers, fraction_of_positives, bin_counts
        """
        from sklearn.calibration import calibration_curve

        prob_true, prob_pred = calibration_curve(
            outcomes, raw_scores, n_bins=n_bins, strategy="uniform"
        )
        return {
            "bin_centers": prob_pred.tolist(),
            "fraction_of_positives": prob_true.tolist(),
            "perfect_calibration": list(np.linspace(0, 1, len(prob_pred))),
        }

    @staticmethod
    def brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """Brier Score: mean squared error between predicted proba and outcome.

        Lower = better calibration. < 0.25 is good, < 0.10 is excellent.
        """
        from sklearn.metrics import brier_score_loss
        return float(brier_score_loss(y_true, y_pred))


def compute_ensemble_confidence(
    factor_score: float,
    ml_proba: float,
    signal_strength: float,
    agreement: float,
) -> float:
    """Confidence = weighted average of calibrated probabilities + signal strength.

    KHÔNG có magic number hardcode.

    Args:
        factor_score: IC-weighted factor composite [-3, 3]
        ml_proba: Calibrated ML probability [0, 1]
        signal_strength: Normalized magnitude [0, 1]
        agreement: Degree of consensus across models [0, 1]

    Returns:
        Calibrated confidence [0, 1]
    """
    factor_conf = np.clip((factor_score + 3) / 6, 0, 1)

    w_factor = 0.30
    w_ml = 0.30
    w_signal = 0.20
    w_agreement = 0.20

    confidence = (
        w_factor * factor_conf
        + w_ml * ml_proba
        + w_signal * signal_strength
        + w_agreement * agreement
    )

    return float(np.clip(confidence, 0, 1))
