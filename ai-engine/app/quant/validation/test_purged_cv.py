import numpy as np
import pandas as pd
import pytest

from app.quant.validation.purged_cv import PurgedWalkForwardCV


def _make_test_data(n_samples: int = 200):
    dates = pd.date_range("2020-01-01", periods=n_samples, freq="B")
    X = pd.DataFrame(
        {"feature_1": np.random.randn(n_samples), "feature_2": np.random.randn(n_samples)},
        index=dates,
    )
    y = pd.Series(np.random.randn(n_samples), index=dates, name="target")
    return X, y, dates


class TestPurgedWalkForwardCV:
    def test_basic_split_shapes(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=5, embargo_days=5, horizon=5, min_train_size=20)
        folds = list(cv.split(X, y))
        assert len(folds) == 5, f"Expected 5 folds, got {len(folds)}"

        for fold, (train_idx, test_idx) in enumerate(folds):
            assert len(train_idx) > 0, f"Fold {fold}: empty train"
            assert len(test_idx) > 0, f"Fold {fold}: empty test"
            assert len(set(train_idx) & set(test_idx)) == 0, (
                f"Fold {fold}: train/test overlap"
            )

    def test_embargo_enforced(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=3, embargo_days=10, horizon=5, min_train_size=20)
        for train_idx, test_idx in cv.split(X, y):
            assert PurgedWalkForwardCV.validate_no_leakage(
                train_idx, test_idx, dates, embargo_days=10
            ), "Leakage detected in embargo window"

    def test_embargo_equals_horizon(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=3, embargo_days=5, horizon=5, min_train_size=20)
        for train_idx, test_idx in cv.split(X, y):
            assert PurgedWalkForwardCV.validate_no_leakage(
                train_idx, test_idx, dates, embargo_days=5
            ), "Leakage with embargo == horizon"

    def test_embargo_less_than_horizon_raises(self):
        with pytest.raises(ValueError, match="Embargo"):
            PurgedWalkForwardCV(n_splits=5, embargo_days=2, horizon=5)

    def test_n_splits_less_than_2_raises(self):
        with pytest.raises(ValueError, match="n_splits"):
            PurgedWalkForwardCV(n_splits=1, embargo_days=5, horizon=5)

    def test_expanding_window(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=5, embargo_days=5, horizon=5, min_train_size=20)
        prev_train_len = 0
        for fold, (train_idx, test_idx) in enumerate(cv.split(X, y)):
            if fold > 0:
                assert len(train_idx) > prev_train_len, (
                    f"Fold {fold}: train should expand"
                )
            prev_train_len = len(train_idx)

    def test_no_test_samples_before_train(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=4, embargo_days=5, horizon=5, min_train_size=20)
        for train_idx, test_idx in cv.split(X, y):
            assert np.all(test_idx > train_idx[-1]), (
                "Test contains samples before/within train set"
            )

    def test_small_dataset_raises(self):
        X, y, dates = _make_test_data(5)
        cv = PurgedWalkForwardCV(n_splits=5, embargo_days=5, horizon=5, min_train_size=20)
        with pytest.raises(ValueError, match="at least"):
            list(cv.split(X, y))

    def test_get_test_indices_map(self):
        n = 200
        cv = PurgedWalkForwardCV(n_splits=4, embargo_days=5, horizon=5, min_train_size=20)
        fold_map = cv.get_test_indices_map(n, 4, min_train_size=20)
        assert len(fold_map) == n
        assert fold_map[0] == -1
        assert fold_map[-1] == 3
        assert set(fold_map) == {-1, 0, 1, 2, 3}

    def test_no_shuffle_property(self):
        X, y, dates = _make_test_data(200)
        cv = PurgedWalkForwardCV(n_splits=4, embargo_days=5, horizon=5, min_train_size=20)
        for train_idx, test_idx in cv.split(X, y):
            train_sorted = np.all(np.diff(train_idx) > 0)
            test_sorted = np.all(np.diff(test_idx) > 0)
            assert train_sorted, "Train indices should be sorted (no shuffle)"
            assert test_sorted, "Test indices should be sorted (no shuffle)"

    def test_embargo_removes_samples(self):
        X, y, dates = _make_test_data(200)
        cv_no_embargo = PurgedWalkForwardCV(
            n_splits=3, embargo_days=0, horizon=0, min_train_size=20
        )
        cv_embargo = PurgedWalkForwardCV(
            n_splits=3, embargo_days=30, horizon=5, min_train_size=20
        )
        no_emb_folds = list(cv_no_embargo.split(X, y))
        emb_folds = list(cv_embargo.split(X, y))
        for (train_no_emb, _), (train_emb, _) in zip(no_emb_folds, emb_folds):
            assert len(train_emb) <= len(train_no_emb), (
                "Embargo should remove training samples"
            )
