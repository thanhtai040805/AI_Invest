"""Causal, offline examples for a fixed-horizon net-profit model.

``build_dataset`` accepts a DataFrame loaded from CSV or parquet. One row is
one observed Vietnamese stock/session; VNINDEX supplies the observed session calendar
and market features. ``date`` is an ISO session date, without a timezone or an
intraday time. OHLC use one common price unit (normally thousands of VND).
Volumes are shares, not lots. Supplied ``open_adj/high_adj/low_adj/close_adj``
are used only for price features; raw OHLC determine executable-price labels
and VND liquidity. Input ``adtv20`` is deliberately not trusted:
liquidity is recomputed from the past 20 observed exchange sessions.

The decision follows close t; entry is open t+1 and exit is close t+H. H is 3
or 5, so the earliest exit is two sessions after entry. This is a research
execution convention, not proof that an opening-price order would fill.
There are no intraday stops or assumptions about the ordering of daily highs
and lows. ``cost_bps`` is roundtrip execution friction, additional to brokerage
and sell tax. Per-order minimum fees are left to the portfolio simulator.

Only ``metadata['feature_columns']`` may be passed to a model. Keep rows with
``universe_eligible`` and ``label_status == 'ok'`` for supervised learning, and
purge training labels whose ``label_end_date`` reaches a later evaluation fold.
Missing bars remain visible as censored rows. Future corporate-action flags
are diagnostics only, never a condition for historical universe membership.
No database, SAG, model fitting, calibration, or network access is used here.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


RETURN_WINDOWS = (1, 3, 5, 10, 20, 60)
BUY_FEE_RATE = 0.001
SELL_FEE_RATE = 0.001
SELL_TAX_RATE = 0.001


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"ticker", "date", "open", "high", "low", "close", "volume_total", "volume_continuous"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"Missing input columns: {sorted(missing)}")
    data = bars.copy()
    if data.empty or data["ticker"].isna().any():
        raise ValueError("Input bars must be nonempty and have non-null tickers")
    data["ticker"] = data["ticker"].astype(str).str.strip().str.upper()
    if data["ticker"].eq("").any():
        raise ValueError("Empty ticker")
    dates = pd.to_datetime(data["date"], format="%Y-%m-%d", errors="raise")
    if isinstance(dates.dtype, pd.DatetimeTZDtype) or dates.isna().any():
        raise ValueError("date must contain timezone-free session dates")
    if not dates.eq(dates.dt.normalize()).all():
        raise ValueError("date must be a session date, not an intraday timestamp")
    data["date"] = dates
    if data.duplicated(["ticker", "date"]).any():
        raise ValueError("Duplicate ticker/date bars; resolve provenance before building examples")
    adjusted_columns = {f"{column}_adj" for column in ("open", "high", "low", "close")}
    supplied_adjusted = adjusted_columns.intersection(data.columns)
    if supplied_adjusted and supplied_adjusted != adjusted_columns:
        raise ValueError("Supply all four adjusted OHLC columns or none")
    if not supplied_adjusted:
        for column in ("open", "high", "low", "close"):
            data[f"{column}_adj"] = data[column]
    if "raw_factor" not in data and "raw_adjustment_factor" in data:
        data["raw_factor"] = data["raw_adjustment_factor"]
    for column in ("open", "high", "low", "close", "open_adj", "high_adj", "low_adj", "close_adj",
                   "volume_total", "volume_continuous", "foreign_net_vol", "raw_factor"):
        if column not in data:
            data[column] = np.nan
        data[column] = pd.to_numeric(data[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return data.sort_values(["ticker", "date"]).reset_index(drop=True)


def _price_valid(frame: pd.DataFrame) -> pd.Series:
    prices = frame[["open", "high", "low", "close"]]
    tolerance = prices.abs().max(axis=1) * 1e-8
    return (prices.notna().all(axis=1) & prices.gt(0).all(axis=1)
            & frame["high"].ge(prices.max(axis=1).sub(tolerance))
            & frame["low"].le(prices.min(axis=1).add(tolerance)))


def _stock_examples(
    stock: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    market: pd.DataFrame,
    horizon: int,
    cost_bps: float,
    price_scale: float,
    history_sessions: int,
    max_abs_overnight_gap: float,
) -> tuple[pd.DataFrame, list[str]]:
    frame = stock.set_index("date").reindex(calendar)
    observed = frame["ticker"].notna()
    valid_price = _price_valid(frame)
    feature_prices = frame[["open_adj", "high_adj", "low_adj", "close_adj"]].rename(columns=lambda name: name.removesuffix("_adj"))
    feature_price_valid = _price_valid(feature_prices)
    feature_gap = feature_prices["open"].div(feature_prices["close"].shift(1)).sub(1)
    raw_gap = frame["open"].div(frame["close"].shift(1)).sub(1)
    factor = frame["raw_factor"]
    previous_factor = factor.shift(1)
    factor_changed = (factor.notna() & previous_factor.notna()
                      & ~np.isclose(factor, previous_factor, rtol=1e-8, atol=1e-12))
    factor_change_pct = factor.div(previous_factor.where(previous_factor.gt(0))).sub(1)
    suspect = feature_gap.abs().gt(max_abs_overnight_gap) | (factor.notna() & factor.le(0))
    volume_valid = (frame["volume_total"].notna() & frame["volume_total"].ge(0)
                    & frame["volume_continuous"].notna() & frame["volume_continuous"].ge(0)
                    & frame["volume_continuous"].le(frame["volume_total"]))
    usable = observed & valid_price & feature_price_valid & volume_valid & ~suspect
    close = feature_prices["close"].where(usable)
    high = feature_prices["high"].where(usable)
    low = feature_prices["low"].where(usable)
    previous_close = close.shift(1)
    returns = close.div(previous_close).sub(1)
    continuous = frame["volume_continuous"].where(usable)
    total = frame["volume_total"].where(usable)
    value = frame["close"].where(usable) * continuous * price_scale
    features = pd.DataFrame(index=calendar)
    for window in RETURN_WINDOWS:
        features[f"return_{window}d"] = close.div(close.shift(window)).sub(1)
    for window in (5, 20, 60):
        features[f"volatility_{window}d"] = returns.rolling(window, min_periods=window).std()
    true_range = pd.concat([high.sub(low), high.sub(previous_close).abs(), low.sub(previous_close).abs()], axis=1).max(axis=1)
    features["atr_14_pct"] = true_range.div(previous_close).rolling(14, min_periods=14).mean()
    features["range_pct"] = high.sub(low).div(close)
    span = high.sub(low)
    features["close_location"] = close.sub(low).div(span.where(span.ne(0)))
    features.loc[span.eq(0) & close.notna(), "close_location"] = 0.5
    features["overnight_gap"] = feature_gap.where(usable)
    features["intraday_return"] = close.div(feature_prices["open"].where(usable)).sub(1)
    features["ma20_distance"] = close.div(close.rolling(20, min_periods=20).mean()).sub(1)
    features["breakout_20d"] = close.div(high.shift(1).rolling(20, min_periods=20).max()).sub(1)
    features["distance_from_low_20d"] = close.div(low.shift(1).rolling(20, min_periods=20).min()).sub(1)
    features["reversal_3d"] = features["return_1d"].sub(features["return_3d"].div(3))
    features["volume_ratio_20d"] = total.div(total.rolling(20, min_periods=20).mean().replace(0, np.nan))
    features["turnover_ratio_20d"] = value.div(value.rolling(20, min_periods=20).mean().replace(0, np.nan))
    features["continuous_volume_share"] = continuous.div(total.replace(0, np.nan))
    foreign = frame["foreign_net_vol"].where(usable)
    features["foreign_flow_ratio"] = foreign.div(total.replace(0, np.nan))
    features["foreign_flow_ratio_5d"] = foreign.rolling(5, min_periods=5).sum().div(total.rolling(5, min_periods=5).sum().replace(0, np.nan))
    market_return = market["market_return_1d"]
    features["beta_60d"] = returns.rolling(60, min_periods=60).cov(market_return).div(market_return.rolling(60, min_periods=60).var().replace(0, np.nan))
    for window in (3, 5, 20, 60):
        features[f"relative_strength_{window}d"] = features[f"return_{window}d"].sub(market[f"market_return_{window}d"])
    features = features.join(market).replace([np.inf, -np.inf], np.nan)
    feature_columns = list(features.columns)

    output = features.copy()
    output["ticker"] = stock["ticker"].iloc[0]
    output["date"] = calendar
    output["decision_close_vnd"] = frame["close"] * price_scale
    output["adtv20_shares"] = continuous.rolling(20, min_periods=20).mean()
    output["adtv20_vnd"] = value.rolling(20, min_periods=20).mean()
    output["decision_price_suspect"] = suspect
    output["decision_bar_valid"] = usable
    output["history_ready"] = usable.rolling(history_sessions, min_periods=history_sessions).sum().eq(history_sessions)
    output["current_execution_volume_positive"] = continuous.gt(0)
    output["decision_adjustment_factor"] = factor
    output["decision_factor_changed"] = factor_changed

    dates = pd.Series(calendar, index=calendar)
    output["entry_date"] = dates.shift(-1)
    output["exit_date"] = dates.shift(-horizon)
    output["label_end_date"] = output["exit_date"]
    output["entry_raw_price_vnd"] = frame["open"].shift(-1) * price_scale
    output["exit_raw_price_vnd"] = frame["close"].shift(-horizon) * price_scale
    side_friction = cost_bps / 20_000
    output["entry_price_vnd"] = output["entry_raw_price_vnd"] * (1 + side_friction)
    output["exit_price_vnd"] = output["exit_raw_price_vnd"] * (1 - side_friction)
    output["entry_gap_pct"] = raw_gap.shift(-1)
    forward_observed = pd.concat([observed.shift(-step, fill_value=False) for step in range(1, horizon + 1)], axis=1).all(axis=1)
    forward_valid = pd.concat([valid_price.shift(-step, fill_value=False) for step in range(1, horizon + 1)], axis=1).all(axis=1)
    # Entry-day ex-date events precede ownership; only later held sessions affect
    # this position's unmodeled entitlement. These diagnostics never select rows.
    output["has_adjustment_in_label_window"] = pd.concat([factor_changed.shift(-step, fill_value=False) for step in range(2, horizon + 1)], axis=1).any(axis=1)
    output["label_adjustment_factor_ratio"] = factor.shift(-horizon).div(factor.shift(-1).where(factor.shift(-1).gt(0)))
    output["label_adjustment_factor_known"] = factor.shift(-1).gt(0) & factor.shift(-horizon).gt(0)
    output["label_max_abs_factor_change"] = pd.concat([factor_change_pct.shift(-step).abs() for step in range(2, horizon + 1)], axis=1).max(axis=1)
    output["has_material_adjustment_in_label_window"] = (output["label_max_abs_factor_change"].gt(0.01)
                                                        | output["label_adjustment_factor_ratio"].sub(1).abs().gt(0.01))
    output["label_max_abs_overnight_gap"] = pd.concat([raw_gap.shift(-step).abs() for step in range(1, horizon + 1)], axis=1).max(axis=1)
    executable = volume_valid & frame["volume_continuous"].gt(0)
    output["label_status"] = "ok"
    output.loc[~(executable.shift(-1, fill_value=False) & executable.shift(-horizon, fill_value=False)), "label_status"] = "censored_no_executable_bar"
    output.loc[~forward_valid, "label_status"] = "invalid_future_price"
    output.loc[~forward_observed, "label_status"] = "censored_missing_session"
    output.loc[~observed.shift(-1, fill_value=False), "label_status"] = "censored_missing_entry"
    output.loc[output["exit_date"].isna(), "label_status"] = "censored_end_of_history"
    output.loc[~usable, "label_status"] = "invalid_decision_bar"
    net_return = (output["exit_price_vnd"] * (1 - SELL_FEE_RATE - SELL_TAX_RATE)
                  / (output["entry_price_vnd"] * (1 + BUY_FEE_RATE)) - 1)
    label_ok = output["label_status"].eq("ok")
    output["net_return"] = net_return.where(label_ok)
    output["profit_label"] = pd.Series(pd.NA, index=calendar, dtype="Int8")
    output.loc[label_ok, "profit_label"] = net_return.loc[label_ok].gt(0).astype("int8")
    return output.loc[observed].reset_index(drop=True), feature_columns


def build_dataset(
    bars: pd.DataFrame,
    horizon_sessions: int = 3,
    cost_bps: float = 20.0,
    *,
    price_scale: float = 1000.0,
    min_adtv20_vnd: float = 10_000_000_000.0,
    universe_top_n: int | None = 100,
    min_history_sessions: int = 60,
    max_abs_overnight_gap: float = 0.12,
) -> tuple[pd.DataFrame, dict]:
    """Return observed stock rows and an explicit feature/label contract.

    Universe membership uses only information available at that decision close.
    Missing future bars and tail rows censor labels without changing historical
    membership. Supplied adjusted OHLC determine features; raw OHLC determine
    targets. ``raw_factor`` is informational and never a model feature. Future
    changes flag potentially affected labels without removing them. Values can remain
    NaN; any imputation/calibration must be fitted inside a training fold.
    """
    if horizon_sessions not in (3, 5):
        raise ValueError("horizon_sessions must be 3 or 5")
    if not math.isfinite(cost_bps) or not 0 <= cost_bps < 20_000:
        raise ValueError("cost_bps must be finite, nonnegative and below 20,000")
    if not math.isfinite(price_scale) or price_scale <= 0:
        raise ValueError("price_scale must be a positive explicit VND multiplier")
    if not math.isfinite(min_adtv20_vnd) or min_adtv20_vnd < 0:
        raise ValueError("min_adtv20_vnd must be finite and nonnegative")
    if universe_top_n is not None and (not isinstance(universe_top_n, int) or universe_top_n < 1):
        raise ValueError("universe_top_n must be a positive integer or None")
    if not isinstance(min_history_sessions, int) or min_history_sessions < 20:
        raise ValueError("min_history_sessions must be an integer >= 20")
    if not math.isfinite(max_abs_overnight_gap) or max_abs_overnight_gap <= 0:
        raise ValueError("max_abs_overnight_gap must be positive")
    adjusted_supplied = {"open_adj", "high_adj", "low_adj", "close_adj"}.issubset(bars.columns)
    data = _prepare_bars(bars)
    index_bars = data.loc[data["ticker"].eq("VNINDEX")].set_index("date").sort_index()
    stocks = data.loc[~data["ticker"].eq("VNINDEX")]
    if index_bars.empty or stocks.empty:
        raise ValueError("Both VNINDEX and stock bars are required")
    calendar = pd.DatetimeIndex(index_bars.index, name="session_date")
    orphan_dates = stocks.loc[~stocks["date"].isin(calendar), "date"].unique()
    if len(orphan_dates):
        raise ValueError(f"VNINDEX session calendar is missing {len(orphan_dates)} observed stock dates")
    index_features = index_bars[["open_adj", "high_adj", "low_adj", "close_adj"]].rename(columns=lambda name: name.removesuffix("_adj"))
    index_close = index_features["close"].where(_price_valid(index_features))
    market = pd.DataFrame(index=calendar)
    for window in RETURN_WINDOWS:
        market[f"market_return_{window}d"] = index_close.div(index_close.shift(window)).sub(1)
    market["market_volatility_20d"] = market["market_return_1d"].rolling(20, min_periods=20).std()
    market["market_ma20_distance"] = index_close.div(index_close.rolling(20, min_periods=20).mean()).sub(1)
    # A return over 60 sessions needs its initial close plus 60 later closes.
    history_sessions = max(61, min_history_sessions)
    frames = []
    for _, stock in stocks.groupby("ticker", sort=True):
        examples, feature_columns = _stock_examples(stock, calendar, market, horizon_sessions,
                                                   cost_bps, price_scale, history_sessions,
                                                   max_abs_overnight_gap)
        frames.append(examples)
    output = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
    base_eligible = (output["decision_bar_valid"] & output["history_ready"]
                     & output["current_execution_volume_positive"]
                     & output["adtv20_vnd"].ge(min_adtv20_vnd)
                     & output["market_return_60d"].notna())
    liquidity = output["adtv20_vnd"].where(base_eligible)
    output["liquidity_rank"] = liquidity.groupby(output["date"]).rank(method="first", ascending=False)
    output["liquidity_rank_pct"] = liquidity.groupby(output["date"]).rank(method="average", pct=True)
    in_top_n = output["liquidity_rank"].notna() if universe_top_n is None else output["liquidity_rank"].le(universe_top_n)
    output["universe_eligible"] = base_eligible & in_top_n
    output["eligibility_reason"] = "eligible"
    output.loc[~in_top_n & base_eligible, "eligibility_reason"] = "outside_daily_liquidity_universe"
    output.loc[~output["adtv20_vnd"].ge(min_adtv20_vnd), "eligibility_reason"] = "insufficient_past_liquidity"
    output.loc[~output["market_return_60d"].notna(), "eligibility_reason"] = "missing_market_history"
    output.loc[~output["current_execution_volume_positive"], "eligibility_reason"] = "no_current_continuous_volume"
    output.loc[~output["history_ready"], "eligibility_reason"] = "insufficient_clean_history"
    output.loc[~output["decision_bar_valid"], "eligibility_reason"] = "invalid_or_suspect_decision_bar"
    breadth_source = output.loc[output["universe_eligible"], ["date", "return_1d", "ma20_distance"]].copy()
    breadth_source["breadth_positive_1d"] = breadth_source["return_1d"].gt(0).astype(float).where(breadth_source["return_1d"].notna())
    breadth_source["breadth_above_ma20"] = breadth_source["ma20_distance"].gt(0).astype(float).where(breadth_source["ma20_distance"].notna())
    breadth = breadth_source.groupby("date")[["breadth_positive_1d", "breadth_above_ma20"]].mean()
    output = output.merge(breadth, left_on="date", right_index=True, how="left", validate="many_to_one")
    feature_columns += ["liquidity_rank_pct", "breadth_positive_1d", "breadth_above_ma20"]
    if "foreign_net_vol" not in bars:
        feature_columns = [column for column in feature_columns if not column.startswith("foreign_flow_")]
    eligible = output["universe_eligible"]
    labeled = eligible & output["label_status"].eq("ok")
    metadata = {
        "schema_version": 1,
        "feature_columns": feature_columns,
        "horizon_sessions": horizon_sessions,
        "decision_timing": "after_close_t",
        "entry_timing": "open_t_plus_1",
        "exit_timing": f"close_t_plus_{horizon_sessions}",
        "minimum_sessions_after_entry": 2,
        "session_calendar": "observed VNINDEX dates; source completeness must be checked by exporter",
        "market_context": {
            "vnindex_missing_price_sessions": int(index_features.isna().any(axis=1).sum()),
            "vnindex_invalid_ohlc_sessions": int((~_price_valid(index_features)).sum()),
            "missing_price_dates": [day.date().isoformat() for day in index_features.index[index_features.isna().any(axis=1)]],
            "missing_features_policy": "remain NaN; no forward fill; model imputation fitted on training data only",
        },
        "price_scale_to_vnd": price_scale,
        "volume_unit": "shares",
        "costs": {"execution_friction_roundtrip_bps": cost_bps, "entry_friction_bps": cost_bps / 2,
                  "exit_friction_bps": cost_bps / 2, "buy_fee_rate": BUY_FEE_RATE,
                  "sell_fee_rate": SELL_FEE_RATE, "sell_tax_rate": SELL_TAX_RATE,
                  "minimum_order_fee_in_label": False, "minimum_intended_order_vnd": 10_000_000},
        "net_return_formula": "exit_price_vnd*(1-sell_fee_rate-sell_tax_rate)/(entry_price_vnd*(1+buy_fee_rate))-1",
        "universe": {"min_adtv20_vnd": min_adtv20_vnd, "top_n_per_decision": universe_top_n,
                     "liquidity_window_sessions": 20, "continuous_volume_only": True,
                     "minimum_clean_history_sessions": history_sessions},
        "adjustment_policy": {"feature_price_source": "supplied_adjusted_ohlc" if adjusted_supplied else "raw_ohlc_fallback",
                              "factor_usage": "diagnostic_only_no_future_factor_filter",
                              "held_adjustment_window": "factor changes t+2 through t+H; entry t+1 events excluded",
                              "material_factor_change_threshold": 0.01,
                              "max_abs_overnight_gap": max_abs_overnight_gap,
                              "corporate_action_cashflows_modeled": False},
        "date_min": output["date"].min().date().isoformat(),
        "date_max": output["date"].max().date().isoformat(),
        "rows": len(output),
        "eligible_rows": int(eligible.sum()),
        "eligible_labeled_rows": int(labeled.sum()),
        "eligible_censored_rows": int((eligible & ~labeled).sum()),
        "eligible_labeled_adjustment_rows": int((labeled & output["has_adjustment_in_label_window"]).sum()),
        "eligible_labeled_material_adjustment_rows": int((labeled & output["has_material_adjustment_in_label_window"]).sum()),
        "label_status_counts": {str(key): int(value) for key, value in output["label_status"].value_counts().items()},
        "eligible_label_status_counts": {str(key): int(value) for key, value in output.loc[eligible, "label_status"].value_counts().items()},
        "limitations": ["Observed daily OHLCV does not establish fillability at the next opening price",
                        "Missing/delisted observations are censored, never relabeled as zero return",
                        "Future adjustment-factor changes are flagged, not filtered; raw targets omit dividend/stock entitlements",
                        "Adjusted OHLC is the supplied data vintage; publication-time vintage and subsequent provider revisions are not established here",
                        "Large adjusted overnight gaps observed by the decision close quarantine that stock's feature history",
                        "No dividend entitlement, minimum fee, impact sizing, settlement cash or portfolio overlap is modeled by per-share labels",
                        "Exporter must include historical/delisted listings; this function cannot recover absent tickers",
                        "Any imputation, scaling, calibration and selection threshold must be learned using training data only"],
    }
    return output, metadata
