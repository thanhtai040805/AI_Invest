"""Causal entry events and position states for an offline swing broker policy.

An entry decision follows close t and uses next-session raw open t+1. The
position can first be sold at close t+3, two sessions after entry. Position
decisions follow a close and schedule a sale at the NEXT close; the price that
formed the decision is never also its fill. A seven-session position has a
known time limit at t+7. Only daily closing paths form supervised outcomes;
daily highs/lows do not imply an intraday sequence or a stop fill.

Adjusted OHLC supplies causal price features. Raw OHLC supplies liquidity,
position marks and fill proxies. Full future paths censor labels, never causal
universe membership or event flags. Only the feature columns in metadata may
enter a model. No database, SAG, network or model fitting is used here.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from experiments.short_horizon_profit_dataset import _prepare_bars, _price_valid


RETURN_WINDOWS = (1, 3, 5, 10, 20, 60)
BUY_FEE_RATE = 0.001
SELL_FEE_RATE = 0.001
SELL_TAX_RATE = 0.001
EXPERT_COLUMNS = {name: f"expert_{name}" for name in ("breakout", "pullback", "reversal")}
EVENT_DEFINITIONS = {
    "breakout": {"breakout_20d_min": 0.0, "volume_ratio_20d_min": 1.2,
                 "close_location_min": 0.65, "ma20_distance_min": 0.0,
                 "conditions": "breakout_20d>=0 and volume_ratio_20d>=1.2 and close_location>=0.65 and ma20_distance>0"},
    "pullback": {"return_20d_min": 0.02, "return_3d_max": 0.0,
                 "ma20_distance_min": -0.02, "intraday_return_min": 0.0,
                 "close_location_min": 0.55,
                 "conditions": "return_20d>0.02 and return_3d<0 and ma20_distance>=-0.02 and intraday_return>0 and close_location>=0.55"},
    "reversal": {"return_5d_max": -0.04, "return_1d_min": 0.0,
                 "reversal_3d_min": 0.0, "close_location_min": 0.65,
                 "volume_ratio_20d_min": 1.2,
                 "conditions": "return_5d<=-0.04 and return_1d>0 and reversal_3d>0 and close_location>=0.65 and volume_ratio_20d>=1.2"},
}
STATE_COLUMNS = ["holding_sessions", "unrealized_net_return", "max_close_net_return",
                 "drawdown_from_peak", "entry_relative_strength_20d", "entry_atr_14_pct"]


def _market_features(index_bars: pd.DataFrame) -> pd.DataFrame:
    adjusted = index_bars[[f"{name}_adj" for name in ("open", "high", "low", "close")]].rename(
        columns=lambda name: name.removesuffix("_adj"))
    close = adjusted["close"].where(_price_valid(adjusted))
    result = pd.DataFrame(index=index_bars.index)
    for window in RETURN_WINDOWS:
        result[f"market_return_{window}d"] = close.div(close.shift(window)).sub(1)
    result["market_volatility_20d"] = result["market_return_1d"].rolling(20, min_periods=20).std()
    result["market_ma20_distance"] = close.div(close.rolling(20, min_periods=20).mean()).sub(1)
    return result


def _stock_features(stock, calendar, market, price_scale, history_sessions, max_abs_gap):
    frame = stock.set_index("date").reindex(calendar)
    observed = frame["ticker"].notna()
    adjusted = frame[[f"{name}_adj" for name in ("open", "high", "low", "close")]].rename(
        columns=lambda name: name.removesuffix("_adj"))
    raw_valid = _price_valid(frame)
    adjusted_valid = _price_valid(adjusted)
    gap = adjusted["open"].div(adjusted["close"].shift(1)).sub(1)
    factor = frame["raw_factor"]
    previous_factor = factor.shift(1)
    factor_changed = (factor.notna() & previous_factor.notna()
                      & ~np.isclose(factor, previous_factor, rtol=1e-8, atol=1e-12))
    volume_valid = (frame["volume_total"].notna() & frame["volume_total"].ge(0)
                    & frame["volume_continuous"].notna() & frame["volume_continuous"].ge(0)
                    & frame["volume_continuous"].le(frame["volume_total"]))
    suspect = gap.abs().gt(max_abs_gap) | (factor.notna() & factor.le(0))
    usable = observed & raw_valid & adjusted_valid & volume_valid & ~suspect
    close, high, low = (adjusted[column].where(usable) for column in ("close", "high", "low"))
    previous_close = close.shift(1)
    returns = close.div(previous_close).sub(1)
    total = frame["volume_total"].where(usable)
    continuous = frame["volume_continuous"].where(usable)
    value = frame["close"].where(usable) * continuous * price_scale
    result = pd.DataFrame(index=calendar)
    for window in RETURN_WINDOWS:
        result[f"return_{window}d"] = close.div(close.shift(window)).sub(1)
    for window in (5, 20, 60):
        result[f"volatility_{window}d"] = returns.rolling(window, min_periods=window).std()
    true_range = pd.concat([high.sub(low), high.sub(previous_close).abs(),
                            low.sub(previous_close).abs()], axis=1).max(axis=1)
    result["atr_14_pct"] = true_range.div(previous_close).rolling(14, min_periods=14).mean()
    span = high.sub(low)
    result["range_pct"] = span.div(close)
    result["close_location"] = close.sub(low).div(span.where(span.ne(0)))
    result.loc[span.eq(0) & close.notna(), "close_location"] = 0.5
    result["overnight_gap"] = gap.where(usable)
    result["intraday_return"] = close.div(adjusted["open"].where(usable)).sub(1)
    result["ma20_distance"] = close.div(close.rolling(20, min_periods=20).mean()).sub(1)
    result["breakout_20d"] = close.div(high.shift(1).rolling(20, min_periods=20).max()).sub(1)
    result["distance_from_low_20d"] = close.div(low.shift(1).rolling(20, min_periods=20).min()).sub(1)
    result["reversal_3d"] = result["return_1d"].sub(result["return_3d"].div(3))
    result["beta_60d"] = returns.rolling(60, min_periods=60).cov(market["market_return_1d"]).div(
        market["market_return_1d"].rolling(60, min_periods=60).var().replace(0, np.nan))
    for window in (3, 5, 20, 60):
        result[f"relative_strength_{window}d"] = result[f"return_{window}d"].sub(market[f"market_return_{window}d"])
    setup_columns = list(result)
    # A surge compares today's volume with the preceding 20 sessions.
    result["volume_ratio_20d"] = total.div(total.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan))
    result["turnover_ratio_20d"] = value.div(value.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan))
    result["continuous_volume_share"] = continuous.div(total.replace(0, np.nan))
    result["adtv20_shares"] = continuous.rolling(20, min_periods=20).mean()
    result["adtv20_vnd"] = value.rolling(20, min_periods=20).mean()
    result["log_adtv20_vnd"] = np.log1p(result["adtv20_vnd"])
    foreign = frame["foreign_net_vol"].where(usable)
    result["foreign_flow_ratio"] = foreign.div(total.replace(0, np.nan))
    result["foreign_flow_ratio_5d"] = foreign.rolling(5, min_periods=5).sum().div(
        total.rolling(5, min_periods=5).sum().replace(0, np.nan))
    liquidity_columns = ["volume_ratio_20d", "turnover_ratio_20d", "continuous_volume_share",
                         "log_adtv20_vnd", "foreign_flow_ratio", "foreign_flow_ratio_5d"]
    result = result.join(market).replace([np.inf, -np.inf], np.nan)
    result["ticker"] = stock["ticker"].iloc[0]
    result["date"] = calendar
    result["decision_close_vnd"] = frame["close"] * price_scale
    result["decision_bar_valid"] = usable
    result["decision_price_suspect"] = suspect
    result["history_ready"] = usable.rolling(history_sessions, min_periods=history_sessions).sum().eq(history_sessions)
    result["current_execution_volume_positive"] = continuous.gt(0)
    result["decision_factor_changed"] = factor_changed
    result["decision_adjustment_factor"] = factor
    return result, frame, observed, raw_valid, volume_valid, factor_changed, setup_columns, liquidity_columns


def _add_paths(result, frame, observed, raw_valid, volume_valid, factor_changed,
               max_holding_sessions, cost_bps, price_scale):
    dates = pd.Series(result.index, index=result.index)
    result["entry_date"] = dates.shift(-1)
    result["entry_raw_price_vnd"] = frame["open"].shift(-1) * price_scale
    result["label_end_date"] = dates.shift(-max_holding_sessions)
    side_friction = cost_bps / 20_000
    result["entry_price_vnd"] = result["entry_raw_price_vnd"] * (1 + side_friction)
    forward_observed = pd.concat([observed.shift(-step, fill_value=False)
                                 for step in range(1, max_holding_sessions + 1)], axis=1).all(axis=1)
    forward_valid = pd.concat([raw_valid.shift(-step, fill_value=False)
                              for step in range(1, max_holding_sessions + 1)], axis=1).all(axis=1)
    forward_volume_valid = pd.concat([volume_valid.shift(-step, fill_value=False)
                                     for step in range(1, max_holding_sessions + 1)], axis=1).all(axis=1)
    executable = volume_valid & frame["volume_continuous"].gt(0)
    path_executable = pd.concat([executable.shift(-step, fill_value=False)
                                for step in (1, *range(3, max_holding_sessions + 1))], axis=1).all(axis=1)
    result["label_status"] = "ok"
    result.loc[~path_executable, "label_status"] = "censored_no_executable_bar"
    result.loc[~forward_volume_valid, "label_status"] = "invalid_future_volume"
    result.loc[~forward_valid, "label_status"] = "invalid_future_price"
    result.loc[~forward_observed, "label_status"] = "censored_missing_session"
    result.loc[~observed.shift(-1, fill_value=False), "label_status"] = "censored_missing_entry"
    result.loc[result["label_end_date"].isna(), "label_status"] = "censored_end_of_history"
    result.loc[~result["decision_bar_valid"], "label_status"] = "invalid_decision_bar"
    valid_labels = result["label_status"].eq("ok")
    for step in range(1, max_holding_sessions + 1):
        raw_close = frame["close"].shift(-step) * price_scale
        result[f"_close_raw_h{step}"] = raw_close.where(raw_valid.shift(-step, fill_value=False))
        if step >= 3:
            result[f"exit_date_h{step}"] = dates.shift(-step)
            result[f"exit_raw_price_vnd_h{step}"] = raw_close
            net_return = (raw_close * (1 - side_friction) * (1 - SELL_FEE_RATE - SELL_TAX_RATE)
                          / (result["entry_price_vnd"] * (1 + BUY_FEE_RATE)) - 1)
            result[f"net_return_h{step}"] = net_return.where(valid_labels)
    held_changes = [factor_changed.shift(-step, fill_value=False)
                    for step in range(2, max_holding_sessions + 1)]
    result["has_adjustment_in_label_window"] = pd.concat(held_changes, axis=1).any(axis=1)
    result["label_adjustment_factor_ratio"] = frame["raw_factor"].shift(-max_holding_sessions).div(
        frame["raw_factor"].shift(-1).where(frame["raw_factor"].shift(-1).gt(0)))


def _add_events(output):
    output["expert_breakout"] = (output["breakout_20d"].ge(0) & output["volume_ratio_20d"].ge(1.2)
                                  & output["close_location"].ge(0.65) & output["ma20_distance"].gt(0))
    output["expert_pullback"] = (output["return_20d"].gt(0.02) & output["return_3d"].lt(0)
                                  & output["ma20_distance"].ge(-0.02) & output["intraday_return"].gt(0)
                                  & output["close_location"].ge(0.55))
    output["expert_reversal"] = (output["return_5d"].le(-0.04) & output["return_1d"].gt(0)
                                  & output["reversal_3d"].gt(0) & output["close_location"].ge(0.65)
                                  & output["volume_ratio_20d"].ge(1.2))
    for column in EXPERT_COLUMNS.values():
        output[column] &= output["universe_eligible"]


def _exit_states(output, calendar, feature_columns, max_holding_sessions, cost_bps):
    entries = output.loc[output["universe_eligible"] & output[list(EXPERT_COLUMNS.values())].any(axis=1)]
    current = output[["ticker", "date", *feature_columns, "decision_bar_valid", "decision_close_vnd"]]
    states = []
    date_series = pd.Series(calendar, index=calendar)
    path_columns = [f"net_return_h{step}" for step in range(3, max_holding_sessions + 1)]
    side_friction = cost_bps / 20_000
    for offset in range(2, max_holding_sessions):
        block = entries[["ticker", "date", "entry_date", "entry_raw_price_vnd", "entry_price_vnd",
                         "label_end_date", "label_status", *path_columns]].rename(columns={"date": "entry_decision_date"}).copy()
        block["date"] = block["entry_decision_date"].map(date_series.shift(-offset))
        # Only states that actually exist in the observed calendar can be decisions.
        block = block.loc[block["date"].notna()]
        originals = entries.loc[block.index]
        block["holding_sessions"] = offset
        block["forced_exit"] = offset == max_holding_sessions - 1
        block["scheduled_exit_date"] = originals[f"exit_date_h{offset + 1}"]
        block["sell_next_close_net_return"] = originals[f"net_return_h{offset + 1}"]
        denominator = originals["entry_price_vnd"] * (1 + BUY_FEE_RATE)
        close_mark = originals[f"_close_raw_h{offset}"]
        peak_close = originals[[f"_close_raw_h{step}" for step in range(1, offset + 1)]].max(axis=1)
        block["unrealized_net_return"] = close_mark * (1 - side_friction) * (1 - SELL_FEE_RATE - SELL_TAX_RATE) / denominator - 1
        block["max_close_net_return"] = peak_close * (1 - side_friction) * (1 - SELL_FEE_RATE - SELL_TAX_RATE) / denominator - 1
        block["drawdown_from_peak"] = close_mark.div(peak_close).sub(1)
        block["entry_relative_strength_20d"] = originals["relative_strength_20d"]
        block["entry_atr_14_pct"] = originals["atr_14_pct"]
        for name, column in EXPERT_COLUMNS.items():
            block[f"entry_expert_{name}"] = originals[column].astype(float)
        block = block.merge(current, on=["ticker", "date"], how="left", validate="many_to_one")
        block["state_feature_valid"] = block.pop("decision_bar_valid").eq(True)
        states.append(block)
    columns = ["ticker", "entry_decision_date", "date", "entry_date", "entry_raw_price_vnd", "entry_price_vnd",
               "label_end_date", "label_status", *path_columns, "holding_sessions", "forced_exit",
               "scheduled_exit_date", "sell_next_close_net_return", *STATE_COLUMNS[1:],
               *[f"entry_expert_{name}" for name in EXPERT_COLUMNS], *feature_columns,
               "decision_close_vnd", "state_feature_valid"]
    return (pd.concat(states, ignore_index=True).sort_values(["date", "ticker", "entry_decision_date"]).reset_index(drop=True)
            if states else pd.DataFrame(columns=columns))


def build_dataset(
    bars: pd.DataFrame,
    cost_bps: float = 20.0,
    *,
    max_holding_sessions: int = 7,
    price_scale: float = 1000.0,
    min_adtv20_vnd: float = 10_000_000_000.0,
    universe_top_n: int | None = 100,
    min_history_sessions: int = 60,
    max_abs_overnight_gap: float = 0.12,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return causal entry events, after-close position states and their contract."""
    if (not isinstance(max_holding_sessions, int) or isinstance(max_holding_sessions, bool)
            or not 3 <= max_holding_sessions <= 7):
        raise ValueError("max_holding_sessions must be an integer from 3 through 7")
    if not math.isfinite(cost_bps) or not 0 <= cost_bps < 20_000:
        raise ValueError("cost_bps must be finite, nonnegative and below 20,000")
    if not math.isfinite(price_scale) or price_scale <= 0:
        raise ValueError("price_scale must be positive")
    if not math.isfinite(min_adtv20_vnd) or min_adtv20_vnd < 0:
        raise ValueError("min_adtv20_vnd must be finite and nonnegative")
    if universe_top_n is not None and (not isinstance(universe_top_n, int)
                                      or isinstance(universe_top_n, bool) or universe_top_n < 1):
        raise ValueError("universe_top_n must be a positive integer or None")
    if (not isinstance(min_history_sessions, int) or isinstance(min_history_sessions, bool)
            or min_history_sessions < 20):
        raise ValueError("min_history_sessions must be an integer >= 20")
    if not math.isfinite(max_abs_overnight_gap) or max_abs_overnight_gap <= 0:
        raise ValueError("max_abs_overnight_gap must be positive")
    data = _prepare_bars(bars)
    index_bars = data.loc[data["ticker"].eq("VNINDEX")].set_index("date").sort_index()
    stocks = data.loc[~data["ticker"].eq("VNINDEX")]
    if index_bars.empty or stocks.empty:
        raise ValueError("Both VNINDEX and stock bars are required")
    calendar = pd.DatetimeIndex(index_bars.index, name="session_date")
    orphan_dates = stocks.loc[~stocks["date"].isin(calendar), "date"].unique()
    if len(orphan_dates):
        raise ValueError(f"VNINDEX session calendar is missing {len(orphan_dates)} observed stock dates")
    market = _market_features(index_bars)
    frames = []
    history_sessions = max(61, min_history_sessions)
    for _, stock in stocks.groupby("ticker", sort=True):
        result, frame, observed, raw_valid, volume_valid, changed, setup_columns, liquidity_columns = _stock_features(
            stock, calendar, market, price_scale, history_sessions, max_abs_overnight_gap)
        _add_paths(result, frame, observed, raw_valid, volume_valid, changed,
                   max_holding_sessions, cost_bps, price_scale)
        frames.append(result.loc[observed].reset_index(drop=True))
    output = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
    eligible = (output["decision_bar_valid"] & output["history_ready"]
                & output["current_execution_volume_positive"] & output["adtv20_vnd"].ge(min_adtv20_vnd)
                & output["market_return_60d"].notna())
    liquidity = output["adtv20_vnd"].where(eligible)
    output["liquidity_rank"] = liquidity.groupby(output["date"]).rank(method="first", ascending=False)
    output["liquidity_rank_pct"] = liquidity.groupby(output["date"]).rank(method="average", pct=True)
    top = output["liquidity_rank"].notna() if universe_top_n is None else output["liquidity_rank"].le(universe_top_n)
    output["universe_eligible"] = eligible & top
    breadth = output.loc[output["universe_eligible"], ["date", "return_1d", "ma20_distance"]].copy()
    breadth["breadth_positive_1d"] = breadth["return_1d"].gt(0).astype(float)
    breadth["breadth_above_ma20"] = breadth["ma20_distance"].gt(0).astype(float)
    output = output.merge(breadth.groupby("date")[["breadth_positive_1d", "breadth_above_ma20"]].mean(),
                          left_on="date", right_index=True, how="left", validate="many_to_one")
    if "foreign_net_vol" not in bars:
        liquidity_columns = [name for name in liquidity_columns if not name.startswith("foreign_flow_")]
    liquidity_columns.append("liquidity_rank_pct")
    groups = {"market": [*list(market), "breadth_positive_1d", "breadth_above_ma20"],
              "setup": setup_columns, "liquidity": liquidity_columns}
    feature_columns = [name for group in groups.values() for name in group]
    _add_events(output)
    exits = _exit_states(output, calendar, feature_columns, max_holding_sessions, cost_bps)
    output = output.drop(columns=[name for name in output if name.startswith("_close_raw_h")])
    labeled = output["universe_eligible"] & output["label_status"].eq("ok")
    index_adjusted = index_bars[[f"{name}_adj" for name in ("open", "high", "low", "close")]].rename(
        columns=lambda name: name.removesuffix("_adj"))
    metadata = {
        "schema_version": 1, "architecture": "broker_swing_policy", "feature_groups": groups,
        "entry_feature_columns": feature_columns,
        "exit_feature_columns": [*feature_columns, *STATE_COLUMNS, *[f"entry_expert_{name}" for name in EXPERT_COLUMNS]],
        "expert_columns": EXPERT_COLUMNS.copy(), "event_definitions": EVENT_DEFINITIONS,
        "max_holding_sessions": max_holding_sessions,
        "path_horizons": list(range(3, max_holding_sessions + 1)),
        "execution": {"entry_decision": "after_close_t", "entry_fill_proxy": "raw_open_t_plus_1",
                      "exit_decision": "after_current_close", "exit_fill_proxy": "NEXT_session_raw_close",
                      "minimum_sessions_after_entry": 2, "earliest_exit": "close_t_plus_3",
                      "time_limit_exit": f"close_t_plus_{max_holding_sessions}",
                      "intraday_ordering_assumed": False},
        "costs": {"execution_friction_roundtrip_bps": cost_bps, "buy_fee_rate": BUY_FEE_RATE,
                  "sell_fee_rate": SELL_FEE_RATE, "sell_tax_rate": SELL_TAX_RATE,
                  "minimum_order_fee_in_label": False, "minimum_intended_order_vnd": 10_000_000},
        "price_scale_to_vnd": price_scale, "volume_unit": "shares",
        "session_calendar": "observed VNINDEX sessions; missing stock dates censor full paths",
        "market_context": {"calendar_sessions": len(calendar),
                           "calendar_date_min": calendar.min().date().isoformat(),
                           "calendar_date_max": calendar.max().date().isoformat(),
                           "stock_tickers": int(stocks["ticker"].nunique()),
                           "vnindex_invalid_ohlc_sessions": int((~_price_valid(index_adjusted)).sum()),
                           "missing_features_policy": "No forward fill; imputation must be learned inside each training block"},
        "universe": {"min_adtv20_vnd": min_adtv20_vnd, "top_n_per_decision": universe_top_n,
                     "minimum_clean_history_sessions": history_sessions, "liquidity_window_sessions": 20},
        "adjustment_policy": {"feature_source": "supplied_adjusted_ohlc" if "close_adj" in bars else "raw_ohlc_fallback",
                              "fill_and_mark_source": "raw_ohlc", "future_factor_flags": "diagnostic_only",
                              "corporate_action_cashflows_modeled": False,
                              "max_abs_overnight_gap": max_abs_overnight_gap},
        "date_min": output["date"].min().date().isoformat(), "date_max": output["date"].max().date().isoformat(),
        "rows": len(output), "exit_state_rows": len(exits), "eligible_rows": int(output["universe_eligible"].sum()),
        "eligible_labeled_rows": int(labeled.sum()),
        "eligible_censored_rows": int((output["universe_eligible"] & ~output["label_status"].eq("ok")).sum()),
        "eligible_labeled_adjustment_rows": int((labeled & output["has_adjustment_in_label_window"]).sum()),
        "expert_event_counts": {name: int(output[column].sum()) for name, column in EXPERT_COLUMNS.items()},
        "label_status_counts": {str(key): int(value) for key, value in output["label_status"].value_counts().items()},
        "limitations": ["Daily opening/closing prices are fill proxies, not observed broker fills",
                        "Full future paths censor supervised outcomes without selecting historical events",
                        "Current adjusted data vintage does not establish publication-time revision history",
                        "Raw outcomes omit cash/stock/rights entitlements; future factors are diagnostic only",
                        "Per-share labels omit sizing impact, lot rounding, minimum fees and cash settlement",
                        "Exporter must retain historical and delisted tickers; absent listings cannot be recovered here"],
    }
    return output, exits, metadata
