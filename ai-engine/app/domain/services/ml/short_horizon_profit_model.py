"""Estimate net strategy profitability from features available before entry.

Targets are decimal returns after the caller's entry, exit, settlement and cost
policy. They are neither relative ranks nor survival labels. Calibration uses a
strictly later historical block; callers must also purge overlapping label
windows when constructing those blocks. Predictions do not guarantee profit.
"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler


class ShortHorizonProfitModel:
    """Two fixed, regularized candidate families for chronological evaluation.

    ``linear`` supplies a scaled logistic/Ridge baseline. ``boosted`` adds
    nonlinear mean and quantile regression. The score is expected net return in
    decimal units, without normalization across the current candidate universe.
    A caller decides whether that edge and its uncertainty justify a trade.
    """

    ARTIFACT_VERSION = 1
    OUTPUT_COLUMNS = (
        "p_profit", "expected_net_return", "downside_q10", "score"
    )

    def __init__(
        self, family: str = "boosted", random_state: int = 42, n_jobs: int = 4
    ):
        if family not in {"linear", "boosted"}:
            raise ValueError("family must be 'linear' or 'boosted'")
        if not isinstance(n_jobs, int) or n_jobs == 0:
            raise ValueError("n_jobs must be a nonzero integer")
        self.family = family
        self.random_state = random_state
        self.n_jobs = n_jobs
        self.feature_cols: list[str] = []
        self.imputer = SimpleImputer(strategy="median")
        self.scaler = StandardScaler() if family == "linear" else None
        self.classifier = None
        self.regressor = None
        self.quantile_regressor = None
        self.probability_calibrator = None
        self.return_bias = 0.0
        self.quantile_offset = 0.0
        self.trained_through: str | None = None
        self.metadata: dict[str, Any] = {}
        self.model_version: str | None = None
        self.is_fitted = False

    @staticmethod
    def _features(
        frame: pd.DataFrame, feature_cols: list[str] | None = None
    ) -> np.ndarray:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("features must be a pandas DataFrame")
        if frame.columns.has_duplicates:
            raise ValueError("feature columns must be unique")
        columns = feature_cols if feature_cols is not None else frame.columns.tolist()
        if not columns or any(not isinstance(col, str) for col in columns):
            raise ValueError("feature columns must be nonempty string names")
        missing = [col for col in columns if col not in frame.columns]
        if missing:
            raise ValueError(f"missing required features: {missing}")
        selected = frame.loc[:, columns]
        invalid = [
            col for col in columns
            if not pd.api.types.is_numeric_dtype(selected[col])
            or pd.api.types.is_complex_dtype(selected[col])
        ]
        if invalid:
            raise ValueError(f"features must be real numeric values: {invalid}")
        values = selected.to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError("features contain infinity")
        if len(values) and np.isnan(values).all(axis=1).any():
            raise ValueError("a feature row has no observed values")
        return values

    @staticmethod
    def _target(values, frame: pd.DataFrame, name: str) -> np.ndarray:
        if isinstance(values, pd.Series) and not values.index.equals(frame.index):
            raise ValueError(f"{name} index must match feature index in order")
        target = np.asarray(values, dtype=float)
        if target.ndim != 1 or len(target) != len(frame) or not len(target):
            raise ValueError(f"{name} must have one value per nonempty feature row")
        if not np.isfinite(target).all():
            raise ValueError(f"{name} contains missing or nonfinite returns")
        if np.unique(target > 0).size != 2:
            raise ValueError(f"{name} requires profitable and nonprofitable examples")
        return target

    @staticmethod
    def _dates(frame: pd.DataFrame) -> pd.DatetimeIndex:
        index = frame.index.get_level_values(0)
        if not isinstance(index, pd.DatetimeIndex):
            raise ValueError("feature index level 0 must be a DatetimeIndex")
        if index.hasnans:
            raise ValueError("feature dates contain NaT")
        # Session dates retain their local calendar date when timezone-aware.
        return index.tz_localize(None).normalize()

    @staticmethod
    def _logits(probabilities: np.ndarray) -> np.ndarray:
        probabilities = np.clip(probabilities, 1e-6, 1.0 - 1e-6)
        return np.log(probabilities / (1.0 - probabilities)).reshape(-1, 1)

    def _transform(self, values: np.ndarray) -> np.ndarray:
        transformed = self.imputer.transform(values)
        if self.scaler is not None:
            transformed = self.scaler.transform(transformed)
        return transformed

    def _calibrate(self, transformed: np.ndarray, target: np.ndarray) -> str:
        probabilities = self.classifier.predict_proba(transformed)[:, 1]
        calibrator = LogisticRegression(
            C=10.0, max_iter=1000, random_state=self.random_state
        )
        calibrator.fit(self._logits(probabilities), (target > 0).astype(int))
        mean = self.regressor.predict(transformed)
        return_bias = float(np.mean(target - mean))
        if self.quantile_regressor is not None:
            lower = self.quantile_regressor.predict(transformed)
            quantile_method = "boosted_q10_with_calibration_residual_offset"
        else:
            lower = mean + return_bias
            quantile_method = "mean_plus_calibration_residual_q10"
        quantile_offset = float(np.quantile(target - lower, 0.10))
        if not np.isfinite([return_bias, quantile_offset]).all():
            raise ValueError("calibration produced nonfinite return adjustments")
        # Replace calibration only after all new adjustments are available.
        self.probability_calibrator = calibrator
        self.return_bias = return_bias
        self.quantile_offset = quantile_offset
        return quantile_method

    def fit(
        self,
        X: pd.DataFrame,
        y_net_return,
        calibration_X: pd.DataFrame,
        calibration_y,
        trained_through: str,
        metadata: dict[str, Any] | None = None,
    ) -> "ShortHorizonProfitModel":
        """Fit only on past data, then calibrate on a separate later past block.

        Series targets must have exactly the features' index; arrays are
        positional. Feature NaNs use medians learned exclusively from X.
        ``trained_through`` includes the latest date needed to observe labels,
        not merely the latest feature date. Caller policy metadata is saved.
        """
        self.is_fitted = False
        values = self._features(X)
        feature_cols = X.columns.tolist()
        calibration_values = self._features(calibration_X, feature_cols)
        target = self._target(y_net_return, X, "y_net_return")
        calibration_target = self._target(calibration_y, calibration_X, "calibration_y")
        train_dates = self._dates(X)
        calibration_dates = self._dates(calibration_X)
        cutoff = pd.Timestamp(trained_through)
        if pd.isna(cutoff):
            raise ValueError("trained_through must be a valid historical date")
        cutoff = cutoff.tz_localize(None).normalize()
        if train_dates.max() >= calibration_dates.min():
            raise ValueError("calibration dates must be strictly after all training dates")
        if calibration_dates.max() > cutoff:
            raise ValueError("calibration dates cannot exceed trained_through")
        all_missing = np.isnan(values).all(axis=0)
        if all_missing.any():
            raise ValueError(
                f"training features have no observations: {np.array(feature_cols)[all_missing].tolist()}"
            )

        transformed = self.imputer.fit_transform(values)
        if self.scaler is not None:
            transformed = self.scaler.fit_transform(transformed)
        transformed_calibration = self._transform(calibration_values)
        if self.family == "linear":
            self.classifier = LogisticRegression(
                C=1.0, max_iter=2000, random_state=self.random_state
            )
            self.regressor = Ridge(alpha=10.0)
            self.quantile_regressor = None
        else:
            parameters = dict(
                n_estimators=120, learning_rate=0.05, max_depth=3,
                num_leaves=7, min_child_samples=40, colsample_bytree=0.8,
                reg_alpha=0.1, reg_lambda=5.0, random_state=self.random_state,
                n_jobs=self.n_jobs, verbosity=-1, deterministic=True,
                force_col_wise=True,
            )
            self.classifier = lgb.LGBMClassifier(objective="binary", **parameters)
            # Squared loss estimates the mean needed for EV. Huber loss and
            # target winsorization would instead change the economic estimand.
            self.regressor = lgb.LGBMRegressor(objective="regression", **parameters)
            self.quantile_regressor = lgb.LGBMRegressor(
                objective="quantile", alpha=0.10, **parameters
            )
        self.classifier.fit(transformed, (target > 0).astype(int))
        self.regressor.fit(transformed, target)
        if self.quantile_regressor is not None:
            self.quantile_regressor.fit(transformed, target)
        quantile_method = self._calibrate(transformed_calibration, calibration_target)
        self.feature_cols = feature_cols
        self.trained_through = cutoff.date().isoformat()
        self.metadata = deepcopy(metadata or {})
        self.metadata["trained_through"] = self.trained_through
        self.metadata["fit_summary"] = {
            "target": "net_strategy_return", "return_unit": "decimal",
            "train_rows": len(X), "calibration_rows": len(calibration_X),
            "train_start": train_dates.min().date().isoformat(),
            "train_end": train_dates.max().date().isoformat(),
            "calibration_start": calibration_dates.min().date().isoformat(),
            "calibration_end": calibration_dates.max().date().isoformat(),
            "train_profit_fraction": float(np.mean(target > 0)),
            "calibration_profit_fraction": float(np.mean(calibration_target > 0)),
            "probability_calibration": "sigmoid_on_later_block",
            "mean_calibration": "additive_bias_on_later_block",
            "quantile_method": quantile_method,
        }
        self.model_version = None
        self.is_fitted = True
        return self

    def recalibrate(
        self,
        calibration_X: pd.DataFrame,
        calibration_y,
        trained_through: str,
    ) -> "ShortHorizonProfitModel":
        """Refresh calibration while freezing base estimators and preprocessing.

        Rows must be after the original feature-training block. The caller must
        supply only labels fully observed by trained_through and purge their
        outcome windows before any subsequent forecast block.
        """
        if not self.is_fitted:
            raise RuntimeError("ShortHorizonProfitModel must be fitted before recalibration")
        values = self._features(calibration_X, self.feature_cols)
        target = self._target(calibration_y, calibration_X, "calibration_y")
        dates = self._dates(calibration_X)
        cutoff = pd.Timestamp(trained_through)
        if pd.isna(cutoff):
            raise ValueError("trained_through must be a valid historical date")
        cutoff = cutoff.tz_localize(None).normalize()
        summary = self.metadata.get("fit_summary", {})
        train_end = summary.get("train_end")
        if not train_end or not self.trained_through:
            raise ValueError("model lacks the training dates required for recalibration")
        if dates.min() <= pd.Timestamp(train_end):
            raise ValueError("calibration dates must be strictly after the base training block")
        if dates.max() > cutoff:
            raise ValueError("calibration dates cannot exceed trained_through")
        if cutoff < pd.Timestamp(self.trained_through):
            raise ValueError("recalibration cannot roll trained_through backwards")
        quantile_method = self._calibrate(self._transform(values), target)
        self.trained_through = cutoff.date().isoformat()
        self.metadata["trained_through"] = self.trained_through
        summary.update({
            "calibration_rows": len(calibration_X),
            "calibration_start": dates.min().date().isoformat(),
            "calibration_end": dates.max().date().isoformat(),
            "calibration_profit_fraction": float(np.mean(target > 0)),
            "probability_calibration": "sigmoid_on_later_block",
            "mean_calibration": "additive_bias_on_later_block",
            "quantile_method": quantile_method,
        })
        self.model_version = None
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        """Return economic predictions with X's index, using frozen preprocessing.

        Extra columns are ignored; required feature names must be present.
        q10 is a return quantile estimate, not a confidence bound on mean return.
        The linear family's q10 assumes a shared calibration residual shape.
        """
        if not self.is_fitted:
            raise RuntimeError("ShortHorizonProfitModel must be fitted before predict")
        values = self._features(X, self.feature_cols)
        if not len(X):
            return pd.DataFrame(index=X.index, columns=self.OUTPUT_COLUMNS, dtype=float)
        if not self.trained_through or self._dates(X).min() <= pd.Timestamp(self.trained_through):
            raise ValueError("prediction dates must be strictly after trained_through")
        transformed = self._transform(values)
        raw_probabilities = self.classifier.predict_proba(transformed)[:, 1]
        probabilities = self.probability_calibrator.predict_proba(
            self._logits(raw_probabilities)
        )[:, 1]
        expected = self.regressor.predict(transformed) + self.return_bias
        lower = (
            self.quantile_regressor.predict(transformed)
            if self.quantile_regressor is not None else expected
        ) + self.quantile_offset
        predictions = np.column_stack((probabilities, expected, lower, expected))
        if not np.isfinite(predictions).all():
            raise ValueError("model produced nonfinite predictions")
        return pd.DataFrame(predictions, index=X.index, columns=self.OUTPUT_COLUMNS)

    def save(self, path: str | Path) -> None:
        """Freeze estimators, preprocessing, calibration, cutoff and policy metadata."""
        if not self.is_fitted:
            raise RuntimeError("cannot save an unfitted model")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"artifact_version": self.ARTIFACT_VERSION, "model": self}, destination)
        self.model_version = sha256(destination.read_bytes()).hexdigest()

    @classmethod
    def load(cls, path: str | Path) -> "ShortHorizonProfitModel":
        """Load a trusted local joblib artifact; incompatible contracts fail closed."""
        source = Path(path)
        bundle = joblib.load(source)
        if not isinstance(bundle, dict) or bundle.get("artifact_version") != cls.ARTIFACT_VERSION:
            raise ValueError("unsupported profit model artifact version")
        model = bundle.get("model")
        if not isinstance(model, cls) or not model.is_fitted or not model.feature_cols:
            raise ValueError("artifact does not contain a fitted profit model")
        model.model_version = sha256(source.read_bytes()).hexdigest()
        return model
