from copy import deepcopy
from hashlib import sha256

import numpy as np
import pandas as pd
import pytest

from app.domain.services.ml.broker_swing_model import BrokerSwingModel


PREDICTION_START = pd.Timestamp("2024-01-01")
EXPERTS = ("breakout", "pullback", "reversal")


def synthetic_paths():
    """Causal features and completed/censored paths on both sides of each fold."""
    dates = pd.bdate_range("2020-12-21", "2024-01-31")[::10]
    feature_columns = ["market_return_1d", "return_1d", "log_adtv20_vnd"]
    state_columns = ["holding_sessions", "unrealized_net_return", "max_close_net_return",
                     "drawdown_from_peak", "entry_relative_strength_20d", "entry_atr_14_pct",
                     *[f"entry_expert_{name}" for name in EXPERTS]]
    metadata = {"schema_version": 1, "architecture": "broker_swing_policy", "max_holding_sessions": 7,
                "feature_groups": {"market": [feature_columns[0]], "setup": [feature_columns[1]],
                                   "liquidity": [feature_columns[2]]},
                "entry_feature_columns": feature_columns,
                "exit_feature_columns": [*feature_columns, *state_columns],
                "expert_columns": {name: f"expert_{name}" for name in EXPERTS}}
    entries, states = [], []
    for index, date in enumerate(dates):
        for stock in range(3):
            ticker = f"S{stock}"
            signal = np.sin(index * 1.3 + stock)
            returns = {f"net_return_h{age}": signal * .035 + .001 * (age - 3)
                       for age in range(3, 8)}
            features = {"market_return_1d": np.sin(index * .6) * .015,
                        "return_1d": signal * .02, "log_adtv20_vnd": 24.0 + stock * .1}
            entries.append({"date": date, "ticker": ticker, "entry_date": date + pd.offsets.BDay(1),
                            "label_end_date": date + pd.offsets.BDay(7), "label_status": "ok",
                            "universe_eligible": True, "adtv20_shares": 1_000_000.0,
                            **{f"expert_{name}": True for name in EXPERTS}, **features, **returns})
            for age in range(2, 7):
                states.append({"date": date + pd.offsets.BDay(age), "entry_decision_date": date,
                               "ticker": ticker, "holding_sessions": age,
                               "label_end_date": date + pd.offsets.BDay(7), "label_status": "ok",
                               "state_feature_valid": True, "forced_exit": age == 6,
                               "sell_next_close_net_return": returns[f"net_return_h{age + 1}"],
                               "unrealized_net_return": signal * .025 + .001 * age,
                               "max_close_net_return": max(.0, signal * .025 + .001 * age),
                               "drawdown_from_peak": min(.0, signal * .025),
                               "entry_relative_strength_20d": signal * .03, "entry_atr_14_pct": .02,
                               **{f"entry_expert_{name}": 1.0 for name in EXPERTS}, **features, **returns})
    return pd.DataFrame(entries), pd.DataFrame(states), metadata


def fitted_model(entries, states, metadata):
    return BrokerSwingModel(n_jobs=1, n_estimators=3, min_rows=3).fit(
        entries, states, metadata, PREDICTION_START)


@pytest.fixture(scope="module")
def fitted_paths():
    entries, states, metadata = synthetic_paths()
    return entries, states, metadata, fitted_model(entries, states, metadata)


def test_four_temporal_stages_purge_every_path_before_the_next_stage(fitted_paths):
    entries, _, _, model = fitted_paths
    boundaries = [PREDICTION_START - pd.DateOffset(months=months) for months in (36, 12, 6, 3, 0)]
    expected_labels = []
    for summary, name, left, right in zip(model.metadata["temporal_stages"], "ABCD", boundaries[:-1], boundaries[1:]):
        rows = entries.loc[entries["date"].ge(left) & entries["date"].lt(right)
                           & entries["label_end_date"].lt(right)]
        assert summary["stage"] == name
        assert summary["starts"] == left.date().isoformat()
        assert summary["ends_exclusive"] == right.date().isoformat()
        assert summary["rows"] == len(rows)
        assert summary["latest_label_end"] == rows["label_end_date"].max().date().isoformat()
        assert pd.Timestamp(summary["latest_label_end"]) < right
        expected_labels.append(rows["label_end_date"].max())
    assert model.trained_through == max(expected_labels).date().isoformat()
    assert model.metadata["research_only"]
    assert set(model.exit_heads) == {2, 3, 4, 5}
    assert all(value["active"] for value in model.metadata["expert_samples"].values())
    assert model.metadata["upper_layer_target"] == "realized return under frozen stage A stopping policy"


def test_stopping_heads_use_successive_purged_bands_of_older_policy(fitted_paths):
    entries, _, _, model = fitted_paths
    start = PREDICTION_START - pd.DateOffset(months=36)
    previous_end = None
    for index, (summary, age) in enumerate(zip(model.metadata["stopping_stages"], (5, 4, 3, 2))):
        left = start + pd.DateOffset(months=6 * index)
        right = start + pd.DateOffset(months=6 * (index + 1))
        rows = entries.loc[entries["date"].ge(left) & entries["date"].lt(right)
                           & entries["label_end_date"].lt(right)]
        assert summary["holding_sessions"] == age
        assert summary["starts"] == left.date().isoformat()
        assert summary["ends_exclusive"] == right.date().isoformat()
        assert summary["paths"] == len(rows)
        assert summary["unique_dates"] == rows["date"].nunique()
        assert summary["latest_label_end"] == rows["label_end_date"].max().date().isoformat()
        assert pd.Timestamp(summary["latest_label_end"]) < right
        if previous_end is not None:
            assert previous_end < left
        previous_end = pd.Timestamp(summary["latest_label_end"])


def test_newer_stopping_band_outcomes_cannot_change_the_older_head(fitted_paths):
    entries, states, metadata, original = fitted_paths
    changed_entries, changed_states = entries.copy(), states.copy()
    first_band_end = PREDICTION_START - pd.DateOffset(months=30)
    newer_entries = changed_entries["date"].ge(first_band_end)
    newer_states = changed_states["entry_decision_date"].ge(first_band_end)
    paths = [f"net_return_h{age}" for age in range(3, 8)]
    changed_entries.loc[newer_entries, paths] += .25
    changed_states.loc[newer_states, [*paths, "sell_next_close_net_return"]] += .25
    altered = fitted_model(changed_entries, changed_states, metadata)
    samples = states.loc[states["holding_sessions"].eq(5) & states["date"].ge(PREDICTION_START)]
    np.testing.assert_array_equal(original.exit_heads[5].predict(samples), altered.exit_heads[5].predict(samples))


def test_forecast_guards_and_finite_outputs_with_artifact_provenance(fitted_paths, tmp_path):
    entries, states, _, original = fitted_paths
    model = deepcopy(original)
    with pytest.raises(ValueError, match="Inference must follow"):
        model.forecast_entries(entries.loc[entries["date"].le(pd.Timestamp(model.trained_through))].tail(3))
    with pytest.raises(ValueError, match="Inference must follow"):
        model.forecast_exits(states.loc[states["date"].le(pd.Timestamp(model.trained_through))].tail(3))
    with pytest.raises(ValueError, match="not fitted"):
        BrokerSwingModel().forecast_entries(entries.tail(3))
    future_entries = entries.loc[entries["date"].ge(PREDICTION_START)]
    future_states = states.loc[states["entry_decision_date"].ge(PREDICTION_START)]
    path = tmp_path / "broker.joblib"
    version = model.save(path)
    assert version == sha256(path.read_bytes()).hexdigest()
    forecasts = model.forecast_entries(future_entries)
    exits = model.forecast_exits(future_states)
    assert len(forecasts) == len(future_entries)
    assert len(exits) == len(future_states)
    numeric = ["expected_net_return", "p_profit", "downside_q10", "expected_holding_sessions",
               "market_risk_probability", "market_opportunity_score"]
    assert np.isfinite(forecasts[numeric].to_numpy()).all()
    assert forecasts["p_profit"].between(0, 1).all()
    assert forecasts["expected_holding_sessions"].between(3, 7).all()
    assert set(forecasts["expert_id"]).issubset(EXPERTS)
    assert forecasts["model_version"].eq(version).all()
    assert exits["model_version"].eq(version).all()
    assert np.isfinite(exits["continuation_net_return"]).all()
    assert forecasts["trained_through"].eq(model.trained_through).all()
    restored = BrokerSwingModel.load(path)
    pd.testing.assert_frame_equal(forecasts, restored.forecast_entries(future_entries))
    pd.testing.assert_frame_equal(exits, restored.forecast_exits(future_states))


def test_labels_outside_all_training_blocks_cannot_change_forecasts(fitted_paths):
    entries, states, metadata, original = fitted_paths
    altered_entries, altered_states = entries.copy(), states.copy()
    boundaries = [PREDICTION_START - pd.DateOffset(months=months) for months in (36, 12, 6, 3, 0)]
    training = pd.Series(False, index=entries.index)
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        training |= entries["date"].ge(left) & entries["date"].lt(right) & entries["label_end_date"].lt(right)
    assert (~training).any()
    excluded_keys = pd.MultiIndex.from_frame(entries.loc[~training, ["date", "ticker"]])
    excluded_states = pd.MultiIndex.from_frame(
        states[["entry_decision_date", "ticker"]].rename(columns={"entry_decision_date": "date"})).isin(excluded_keys)
    path_columns = [f"net_return_h{age}" for age in range(3, 8)]
    altered_entries.loc[~training, path_columns] = 1000.0
    altered_states.loc[excluded_states, [*path_columns, "sell_next_close_net_return"]] = -1000.0
    modified = fitted_model(altered_entries, altered_states, metadata)
    future = entries.loc[entries["date"].ge(PREDICTION_START)]
    pd.testing.assert_frame_equal(original.forecast_entries(future), modified.forecast_entries(future))
    assert original.metadata == modified.metadata


class ConstantAdvantage:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return np.full(len(frame), self.value)


def test_stopping_chooses_first_policy_exit_instead_of_hindsight_maximum():
    entries, states, _ = synthetic_paths()
    entry = entries.iloc[[0]].copy()
    identity = states["entry_decision_date"].eq(entry.iloc[0]["date"]) & states["ticker"].eq(entry.iloc[0]["ticker"])
    path = states.loc[identity].copy()
    entry.loc[:, [f"net_return_h{age}" for age in range(3, 8)]] = [.01, -.03, .25, .4, .5]
    model = BrokerSwingModel(min_rows=2)
    model.exit_heads = {2: ConstantAdvantage(.01), 3: ConstantAdvantage(-.01),
                        4: ConstantAdvantage(-.01), 5: ConstantAdvantage(-.01)}
    result = model.policy_outcomes(entry, path)
    assert result.iloc[0]["policy_holding_sessions"] == 4
    assert result.iloc[0]["policy_net_return"] == -.03
    assert result.iloc[0]["policy_net_return"] != entry.filter(regex="^net_return_").max(axis=1).iloc[0]
    # Later prices and state labels cannot change the chosen stopping action.
    entry.loc[:, ["net_return_h5", "net_return_h6", "net_return_h7"]] = [10, 20, 30]
    path["sell_next_close_net_return"] = 999
    pd.testing.assert_frame_equal(result, model.policy_outcomes(entry, path))


def test_missing_state_defaults_to_hold_until_valid_decision_or_time_limit():
    entries, states, _ = synthetic_paths()
    entry = entries.iloc[[0]].copy()
    identity = states["entry_decision_date"].eq(entry.iloc[0]["date"]) & states["ticker"].eq(entry.iloc[0]["ticker"])
    path = states.loc[identity].copy()
    model = BrokerSwingModel(min_rows=2)
    model.exit_heads = {age: ConstantAdvantage(-.01) for age in range(2, 6)}
    path.loc[path["holding_sessions"].eq(2), "state_feature_valid"] = False
    outcome = model.policy_outcomes(entry, path).iloc[0]
    assert outcome["policy_holding_sessions"] == 4
    path["state_feature_valid"] = False
    outcome = model.policy_outcomes(entry, path).iloc[0]
    assert outcome["policy_holding_sessions"] == 7
    assert outcome["policy_net_return"] == entry.iloc[0]["net_return_h7"]
    outcome_missing = model.policy_outcomes(entry, path.iloc[:0]).iloc[0]
    assert outcome_missing["policy_holding_sessions"] == 7
    model.is_fitted, model.trained_through = True, "2019-12-31"
    assert model.forecast_exits(path).empty


@pytest.mark.parametrize("future_column", ["net_return_h7", "entry_price_vnd", "exit_raw_price_vnd_h3"])
def test_future_outcome_columns_are_rejected_even_when_declared_as_features(future_column):
    entries, states, metadata = synthetic_paths()
    invalid = deepcopy(metadata)
    if future_column not in entries:
        entries[future_column] = 123.0
    invalid["entry_feature_columns"].append(future_column)
    invalid["feature_groups"]["setup"].append(future_column)
    with pytest.raises(ValueError, match="features|feature|contract|outcome"):
        fitted_model(entries, states, invalid)


def test_extra_feature_group_cannot_smuggle_a_future_fill_price():
    entries, states, metadata = synthetic_paths()
    invalid = deepcopy(metadata)
    entries["entry_price_vnd"] = 123.0
    invalid["feature_groups"]["future"] = ["entry_price_vnd"]
    invalid["entry_feature_columns"].append("entry_price_vnd")
    with pytest.raises(ValueError, match="features|feature|contract|outcome|group"):
        fitted_model(entries, states, invalid)


def test_inactive_expert_does_not_mutate_source_event_flags(fitted_paths):
    entries, _, _, original = fitted_paths
    model = deepcopy(original)
    del model.expert_heads["pullback"]
    source = entries.loc[entries["date"].ge(PREDICTION_START)].copy()
    before = source.copy(deep=True)
    lower = model._lower_predictions(source)
    assert lower["pullback_active"].eq(0).all()
    pd.testing.assert_frame_equal(source, before)


def test_raw_specialist_control_uses_the_selected_expert_without_arbiter(fitted_paths):
    entries, _, _, model = fitted_paths
    future = entries.loc[entries["date"].ge(PREDICTION_START)]
    lower = model._lower_predictions(future)
    forecast = model.forecast_entries(future, use_arbiter=False)
    assert len(forecast) == len(future)
    for position, (_, row) in enumerate(lower.iterrows()):
        name = row["selected_expert"]
        assert forecast.iloc[position]["expert_id"] == name
        assert forecast.iloc[position]["expected_net_return"] == row[f"{name}_mean"]
        assert forecast.iloc[position]["p_profit"] == row[f"{name}_probability"]
        assert forecast.iloc[position]["downside_q10"] == row[f"{name}_q10"]
