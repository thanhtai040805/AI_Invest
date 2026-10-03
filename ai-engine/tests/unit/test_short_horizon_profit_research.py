import numpy as np
import pandas as pd
import pytest

from scripts import research_short_horizon_profit as research


def test_monthly_refit_replaces_estimators_and_purges_every_outcome(monkeypatch, tmp_path):
    dates = pd.bdate_range("2020-01-01", "2023-03-31")
    index = pd.MultiIndex.from_product([dates, range(10)], names=["date", "ticker"])
    dataset = index.to_frame(index=False)
    dataset["feature"] = np.arange(len(dataset), dtype=float)
    dataset["net_return"] = np.where(dataset["ticker"] % 2, .02, -.01)
    dataset["universe_eligible"] = True
    dataset["label_status"] = "ok"
    dataset["label_end_date"] = dataset["date"] + pd.offsets.BDay(3)
    fitted = []

    class Model:
        OUTPUT_COLUMNS = ("p_profit", "expected_net_return", "downside_q10", "score")

        def __init__(self, family, n_jobs):
            self.family, self.n_jobs = family, n_jobs

        def fit(self, X, y, calibration_X, calibration_y, trained_through, metadata):
            start = pd.Timestamp(metadata["prediction_start"])
            calibration_start = start - pd.DateOffset(months=3)
            train_rows = dataset.set_index(["date", "ticker"]).loc[X.index]
            calibration_rows = dataset.set_index(["date", "ticker"]).loc[calibration_X.index]
            assert X.index.get_level_values("date").min() >= start - pd.DateOffset(months=24)
            assert train_rows["label_end_date"].max() < calibration_start
            assert calibration_X.index.get_level_values("date").min() >= calibration_start
            assert calibration_rows["label_end_date"].max() < start
            assert y.index.equals(X.index)
            assert calibration_y.index.equals(calibration_X.index)
            self.trained_through, self.metadata = trained_through, metadata
            fitted.append(self)
            return self

        def predict(self, X):
            assert X.index.get_level_values("date").min() > pd.Timestamp(self.trained_through)
            return pd.DataFrame([[.6, .01, -.03, .01]] * len(X), index=X.index, columns=self.OUTPUT_COLUMNS)

        def save(self, path):
            pass

        def recalibrate(self, *args, **kwargs):
            pytest.fail("Monthly refit must replace the base model")

    monkeypatch.setattr(research, "ShortHorizonProfitModel", Model)
    metadata = {"feature_columns": ["feature"], "horizon_sessions": 3}
    initial = research.fit_before(dataset, metadata, "boosted", "2023-01-01", tmp_path / "initial.joblib", 1, 24)
    initial.metadata["selection"] = {"candidate": "boosted_h3_open"}
    predictions, final = research.forecasts_for(
        initial, dataset, metadata, "2023-01-01", "2023-03-31", dates,
        "monthly-refit", 24, tmp_path,
    )
    assert len(fitted) == 3
    assert final is fitted[-1] and final is not initial
    assert [model.metadata["prediction_start"] for model in fitted] == ["2023-01-01", "2023-02-01", "2023-03-01"]
    assert final.metadata["selection"] == initial.metadata["selection"]
    assert predictions["date"].max() == pd.Timestamp("2023-03-28")
    assert predictions["ticker"].nunique() == 10


def test_profit_gate_accepts_asymmetric_payoffs_but_rejects_a_losing_year():
    records = [
        {"closed_trades": 50, "win_rate_closed_net": .4, "gross_profit_vnd": 500,
         "gross_loss_vnd": 250, "expectancy_net_return": .005, "max_drawdown": .05,
         "total_return": .05, "open_positions": [],
         "first_positive_realized_pnl_date": f"{year}-01-10",
         "sessions_to_first_positive_realized_pnl": 5, "realized_pnl_vnd_at_20_sessions": 100}
        for year in (2023, 2024, 2025)
    ]
    result = research.aggregate_development(records)
    assert result["win_rate"] == .4
    assert result["passes_development_gate"]
    records[1]["total_return"] = -.01
    assert not research.aggregate_development(records)["passes_development_gate"]


def test_invalid_training_window_fails_before_data_access():
    with pytest.raises(ValueError, match="twelve historical months"):
        research.fit_before(None, None, "boosted", "2023-01-01", None, 1, 3)
