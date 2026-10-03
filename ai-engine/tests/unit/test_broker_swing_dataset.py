import numpy as np
import pandas as pd
import pytest

from experiments.broker_swing_dataset import build_dataset


def market_bars(sessions=100):
    dates = pd.bdate_range("2024-01-02", periods=sessions)
    frames = []
    for ticker, base, step in (("VNINDEX", 1000.0, 1.0), ("AAA", 100.0, 0.2)):
        close = base + np.arange(sessions) * step
        frame = pd.DataFrame({"ticker": ticker, "date": dates, "open": close - step / 2,
                              "high": close * 1.005, "low": close * 0.995, "close": close,
                              "volume_total": 1_000_000.0, "volume_continuous": 900_000.0,
                              "raw_factor": 1.0})
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    day = dates[80]
    stock = bars["ticker"].eq("AAA")
    signal = stock & bars["date"].eq(day)
    last_close = bars.loc[stock & bars["date"].eq(dates[79]), "close"].iloc[0]
    new_close = last_close * 1.025
    bars.loc[signal, ["open", "high", "low", "close"]] = [last_close, new_close * 1.001,
                                                            last_close * 0.998, new_close]
    bars.loc[signal, ["volume_total", "volume_continuous"]] = [3_000_000, 2_700_000]
    for column in ("open", "high", "low", "close"):
        bars[f"{column}_adj"] = bars[column]
    return bars, dates


def test_future_outcomes_do_not_select_events_or_change_features():
    bars, dates = market_bars()
    original, states, metadata = build_dataset(bars)
    changed_bars = bars.copy()
    future = changed_bars["date"].gt(dates[80])
    price_columns = [*list(("open", "high", "low", "close")),
                     *[f"{name}_adj" for name in ("open", "high", "low", "close")]]
    changed_bars.loc[future, price_columns] *= 1.3
    changed_bars.loc[changed_bars["date"].ge(dates[83]), price_columns] *= 1.1
    changed_bars.loc[future, "raw_factor"] = 0.8
    changed_bars.loc[changed_bars["date"].ge(dates[82]), "raw_factor"] = 0.7
    changed, _, _ = build_dataset(changed_bars)
    causal = ["ticker", "date", *metadata["entry_feature_columns"], "universe_eligible",
              "expert_breakout", "expert_pullback", "expert_reversal"]
    historical = original["date"].le(dates[80])
    pd.testing.assert_frame_equal(original.loc[historical, causal], changed.loc[historical, causal])
    row = original.loc[original["date"].eq(dates[80])].iloc[0]
    assert row["expert_breakout"]
    assert len(states) == 5
    modified = changed.loc[changed["date"].eq(dates[80])].iloc[0]
    assert row["net_return_h3"] != modified["net_return_h3"]
    assert modified["has_adjustment_in_label_window"]


def test_exit_state_decides_before_next_close_and_settlement_minimum():
    bars, dates = market_bars()
    entries, states, metadata = build_dataset(bars)
    assert metadata["execution"]["exit_fill_proxy"] == "NEXT_session_raw_close"
    trade_states = states.loc[states["entry_decision_date"].eq(dates[80])].sort_values("date")
    assert trade_states["holding_sessions"].tolist() == [2, 3, 4, 5, 6]
    assert trade_states["date"].tolist() == list(dates[82:87])
    assert trade_states["scheduled_exit_date"].tolist() == list(dates[83:88])
    assert trade_states.iloc[0]["entry_date"] == dates[81]
    assert trade_states.iloc[0]["scheduled_exit_date"] == dates[83]
    assert trade_states["forced_exit"].tolist() == [False, False, False, False, True]
    assert trade_states["state_feature_valid"].all()
    entry = entries.loc[entries["date"].eq(dates[80])].iloc[0]
    assert trade_states["label_end_date"].eq(dates[87]).all()
    assert trade_states.iloc[0]["sell_next_close_net_return"] == entry["net_return_h3"]
    assert trade_states.iloc[-1]["sell_next_close_net_return"] == entry["net_return_h7"]


def test_exit_features_only_use_observations_through_state_close():
    bars, dates = market_bars()
    _, original, metadata = build_dataset(bars)
    changed = bars.copy()
    future = changed["date"].gt(dates[82])
    price_columns = [*list(("open", "high", "low", "close")),
                     *[f"{name}_adj" for name in ("open", "high", "low", "close")]]
    changed.loc[future, price_columns] *= 1.05
    _, altered, _ = build_dataset(changed)
    observed = original["entry_decision_date"].eq(dates[80]) & original["date"].eq(dates[82])
    columns = ["ticker", "date", "entry_decision_date", *metadata["exit_feature_columns"], "state_feature_valid"]
    pd.testing.assert_frame_equal(original.loc[observed, columns], altered.loc[observed, columns])
    assert (original.loc[observed, "sell_next_close_net_return"].iloc[0]
            != altered.loc[observed, "sell_next_close_net_return"].iloc[0])
    assert not set(metadata["exit_feature_columns"]).intersection(
        {"sell_next_close_net_return", "net_return_h3", "net_return_h7", "label_end_date", "forced_exit"})


def test_raw_close_paths_include_all_costs_without_adjusted_outcomes():
    bars, dates = market_bars()
    entries, _, metadata = build_dataset(bars, cost_bps=20)
    signal = entries.loc[entries["date"].eq(dates[80])].iloc[0]
    stock = bars["ticker"].eq("AAA")
    entry_open = bars.loc[stock & bars["date"].eq(dates[81]), "open"].iloc[0] * 1000
    for horizon in (3, 4, 5, 6, 7):
        exit_close = bars.loc[stock & bars["date"].eq(dates[80 + horizon]), "close"].iloc[0] * 1000
        expected = exit_close * .999 * .998 / (entry_open * 1.001 * 1.001) - 1
        assert signal[f"net_return_h{horizon}"] == pytest.approx(expected)
        assert signal[f"exit_date_h{horizon}"] == dates[80 + horizon]
    adjusted_different = bars.copy()
    adjusted_different.loc[stock, [f"{name}_adj" for name in ("open", "high", "low", "close")]] *= 0.5
    alternative, _, _ = build_dataset(adjusted_different)
    alt = alternative.loc[alternative["date"].eq(dates[80])].iloc[0]
    assert alt["net_return_h7"] == signal["net_return_h7"]
    assert metadata["adjustment_policy"]["fill_and_mark_source"] == "raw_ohlc"


@pytest.mark.parametrize("damage", ["missing", "zero_volume", "invalid_price"])
def test_full_path_censoring_does_not_change_historical_event_membership(damage):
    bars, dates = market_bars()
    original, _, _ = build_dataset(bars)
    row = bars["ticker"].eq("AAA") & bars["date"].eq(dates[85])
    damaged = bars.copy()
    if damage == "missing":
        damaged = damaged.loc[~row]
    elif damage == "zero_volume":
        damaged.loc[row, ["volume_total", "volume_continuous"]] = 0
    else:
        damaged.loc[row, "high"] = 0
    entries, states, _ = build_dataset(damaged)
    before = original.loc[original["date"].eq(dates[80])].iloc[0]
    after = entries.loc[entries["date"].eq(dates[80])].iloc[0]
    assert before["universe_eligible"] and after["universe_eligible"]
    assert before["expert_breakout"] and after["expert_breakout"]
    assert after["label_status"] != "ok"
    assert after[[f"net_return_h{step}" for step in range(3, 8)]].isna().all()
    selected_states = states.loc[states["entry_decision_date"].eq(dates[80])]
    assert len(selected_states) == 5
    assert selected_states["label_status"].ne("ok").all()


def test_tail_is_censored_and_observed_stock_dates_require_market_calendar():
    bars, dates = market_bars()
    entries, _, metadata = build_dataset(bars)
    tail = entries.loc[entries["date"].gt(dates[-8])]
    assert tail["label_status"].eq("censored_end_of_history").all()
    assert tail["label_end_date"].isna().all()
    assert metadata["path_horizons"] == [3, 4, 5, 6, 7]
    missing_index = bars.loc[~(bars["ticker"].eq("VNINDEX") & bars["date"].eq(dates[85]))]
    with pytest.raises(ValueError, match="VNINDEX session calendar is missing"):
        build_dataset(missing_index)


@pytest.mark.parametrize("expert,closes", [
    ("pullback", [115.2, 117.0, 116.5, 115.8, 116.3]),
    ("reversal", [115.2, 112.0, 110.0, 108.0, 109.5]),
])
def test_other_entry_experts_recognize_their_causal_setup(expert, closes):
    bars, dates = market_bars()
    for offset, close in enumerate(closes, start=76):
        selected = bars["ticker"].eq("AAA") & bars["date"].eq(dates[offset])
        open_price = close * 0.995
        bars.loc[selected, ["open", "high", "low", "close"]] = [open_price, close * 1.001,
                                                                  close * 0.993, close]
        for column in ("open", "high", "low", "close"):
            bars.loc[selected, f"{column}_adj"] = bars.loc[selected, column]
    entries, states, metadata = build_dataset(bars)
    row = entries.loc[entries["date"].eq(dates[80])].iloc[0]
    assert row[f"expert_{expert}"]
    assert metadata["expert_columns"][expert] == f"expert_{expert}"
    assert "conditions" in metadata["event_definitions"][expert]
    relevant_states = states.loc[states["entry_decision_date"].eq(dates[80])]
    assert len(relevant_states) == 5
    assert relevant_states[f"entry_expert_{expert}"].eq(1).all()


def test_missing_current_position_bar_cannot_form_an_exit_decision():
    bars, dates = market_bars()
    missing = bars["ticker"].eq("AAA") & bars["date"].eq(dates[82])
    _, states, _ = build_dataset(bars.loc[~missing])
    state = states.loc[states["entry_decision_date"].eq(dates[80]) & states["date"].eq(dates[82])].iloc[0]
    assert not state["state_feature_valid"]
    assert pd.isna(state["decision_close_vnd"])
    assert state["label_status"] == "censored_missing_session"
