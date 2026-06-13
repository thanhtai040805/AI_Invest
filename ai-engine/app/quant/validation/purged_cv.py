import warnings
from typing import Generator, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)


class PurgedWalkForwardCV:
    """
    Temporal cross-validation according to López de Prado (AFML Chapter 7).

    Args:
        n_splits: number of folds (recommended 5-10 for VN dataset)
        embargo_days: number of embargo days after train_end (>= horizon, default 5)
        horizon: forward return horizon in trading days

    HARD RULES:
    - No shuffling of any data
    - test index must NOT be in [train_end - embargo, train_end]
    - Feature normalization (z-score, winsorize, impute) must fit ONLY on train set
    - No statistics computed on the full dataset
    """

    def __init__(
        self,
        n_splits: int,
        embargo_days: int,
        horizon: int,
        min_train_size: int = 20,
    ):
        if embargo_days < horizon:
            raise ValueError(
                f"Embargo ({embargo_days}) must be >= horizon ({horizon}) "
                f"to prevent label overlap leakage"
            )
        if n_splits < 2:
            raise ValueError(f"n_splits must be >= 2, got {n_splits}")
        self.n_splits = n_splits
        self.embargo_days = embargo_days
        self.horizon = horizon
        self.min_train_size = min_train_size

    def split(
        self,
        X: pd.DataFrame,
        y: Optional[pd.Series] = None,
        groups: Optional[np.ndarray] = None,
    ) -> Generator[Tuple[np.ndarray, np.ndarray], None, None]:
        """
        Yield (train_idx, test_idx) tuples using expanding window.

        Each fold's test set is a contiguous block. The training set is
        everything before the test block minus the embargo window.
        The first test block starts after at least min_train_size samples.
        """
        n_samples = len(X)
        if n_samples < self.n_splits * 2:
            raise ValueError(
                f"Need at least {self.n_splits * 2} samples, got {n_samples}"
            )

        indices = np.arange(n_samples)
        test_size = (n_samples - self.min_train_size) // self.n_splits
        if test_size < 1:
            test_size = 1

        for fold in range(self.n_splits):
            test_start = self.min_train_size + fold * test_size
            test_end = (
                n_samples
                if fold == self.n_splits - 1
                else self.min_train_size + (fold + 1) * test_size
            )

            test_idx = indices[test_start:test_end]
            train_idx = indices[:test_start]

            if len(train_idx) < self.min_train_size:
                continue

            train_idx = self._apply_embargo(train_idx, test_idx, X.index)

            yield train_idx, test_idx

    def _apply_embargo(
        self,
        train_idx: np.ndarray,
        test_idx: np.ndarray,
        dates: pd.DatetimeIndex,
    ) -> np.ndarray:
        """
        Remove from train_idx any samples whose date falls within the embargo
        window [test_start - embargo, test_start).
        """
        if len(train_idx) == 0:
            return train_idx

        test_start_date = dates[test_idx[0]]
        embargo_start = test_start_date - pd.Timedelta(days=self.embargo_days)

        train_dates = dates[train_idx]
        mask = train_dates < embargo_start
        return train_idx[mask]

    @staticmethod
    def validate_no_leakage(
        train_idx: np.ndarray,
        test_idx: np.ndarray,
        dates: pd.DatetimeIndex,
        embargo_days: int,
    ) -> bool:
        """Unit test: no test index falls within the embargo window."""
        if len(train_idx) == 0:
            return True
        train_end = dates[train_idx[-1]]
        embargo_start = train_end - pd.Timedelta(days=embargo_days)
        test_dates = dates[test_idx]
        leaked = (test_dates >= embargo_start) & (test_dates <= train_end)
        return not leaked.any()

    @staticmethod
    def get_test_indices_map(
        n_samples: int,
        n_splits: int,
        min_train_size: int = 20,
    ) -> np.ndarray:
        """Return an array mapping each sample to its fold (-1 = never in test)."""
        fold_map = np.full(n_samples, -1, dtype=int)
        test_size = (n_samples - min_train_size) // n_splits
        if test_size < 1:
            test_size = 1
        for fold in range(n_splits):
            start = min_train_size + fold * test_size
            end = n_samples if fold == n_splits - 1 else min_train_size + (fold + 1) * test_size
            fold_map[start:end] = fold
        return fold_map
