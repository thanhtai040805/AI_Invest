"""Offline cash portfolio simulation for causal short-horizon forecasts.

Decisions arrive after the close, entries use the next market session's open,
and planned exits use the close of holding session H (entry is session one).
Daily bars are fill proxies: this does not simulate broker acknowledgements,
intraday depth, queue priority, partial fills, or enforce live settlement.
Prediction label prices and realized outcomes never select or size an entry.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

import pandas as pd


@dataclass(frozen=True)
class PortfolioPolicy:
    """Explicit experiment assumptions; these defaults are not calibrated limits."""

    initial_cash: float = 1_000_000_000.0
    min_profit_probability: float = 0.55
    min_expected_net_return: float = 0.0
    max_positions: int = 5
    max_single_weight: float = 0.10
    risk_budget_per_position: float = 0.005
    cash_buffer: float = 0.10
    liquidity_participation: float = 0.01
    downside_floor: float = 0.01
    holding_sessions: int = 3
    minimum_holding_sessions: int = 3
    min_notional: float = 10_000_000.0
    price_scale: float = 1000.0
    friction_bps: float = 20.0
    prices_include_friction: bool = False
    brokerage_fee_rate: float = 0.001
    sell_tax_rate: float = 0.001
    minimum_fee: float = 10_000.0
    sell_cash_settlement_sessions: int = 2
    lot_size: int = 100
    liquidity_lookback_sessions: int = 20
    minimum_liquidity_observations: int = 5

    def validate(self) -> None:
        for name in ("initial_cash", "price_scale", "downside_floor"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("min_profit_probability", "max_single_weight", "risk_budget_per_position", "cash_buffer", "liquidity_participation"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        for name in ("min_expected_net_return", "min_notional", "friction_bps", "brokerage_fee_rate", "sell_tax_rate", "minimum_fee"):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        for name in ("min_notional", "friction_bps", "brokerage_fee_rate", "sell_tax_rate", "minimum_fee"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        for name in ("max_positions", "lot_size", "holding_sessions", "minimum_holding_sessions", "liquidity_lookback_sessions", "minimum_liquidity_observations"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.minimum_holding_sessions < 3 or self.holding_sessions < self.minimum_holding_sessions:
            raise ValueError("Holding horizons must permit at least three holding sessions")
        if self.minimum_liquidity_observations > self.liquidity_lookback_sessions:
            raise ValueError("Liquidity observations exceed the lookback")
        if not isinstance(self.sell_cash_settlement_sessions, int) or isinstance(self.sell_cash_settlement_sessions, bool) or self.sell_cash_settlement_sessions < 0:
            raise ValueError("sell_cash_settlement_sessions must be a nonnegative integer")
        if self.prices_include_friction and self.friction_bps:
            raise ValueError("Set friction_bps=0 when input bar prices already include friction")
        if self.friction_bps >= 20_000 or self.brokerage_fee_rate + self.sell_tax_rate >= 1:
            raise ValueError("Transaction costs would make exit proceeds nonpositive")


TRADE_COLUMNS = [
    "ticker", "decision_date", "entry_date", "exit_date", "planned_exit_date",
    "holding_sessions", "shares", "entry_price_vnd", "exit_price_vnd",
    "buy_notional", "buy_fee", "entry_cash_cost", "sell_notional", "sell_fee",
    "sell_tax", "exit_cash_credit", "net_pnl_vnd", "net_return",
    "p_profit", "expected_net_return", "downside_q10",
]
NAV_COLUMNS = [
    "date", "cash", "unsettled_receivable", "positions_value", "total_nav", "equity_exposure",
    "cash_weight", "locked_positions_value", "open_positions", "stale_marks",
]


def _finite_positive(value: Any) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.tz_localize(None).dt.normalize()


def _fee(notional: float, policy: PortfolioPolicy) -> float:
    return max(policy.minimum_fee, notional * policy.brokerage_fee_rate)


def simulate_portfolio(
    predictions: pd.DataFrame,
    bars: pd.DataFrame,
    policy: PortfolioPolicy | Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Simulate predictions without accessing a database or modifying inputs.

    Required forecasts: decision_date, ticker, p_profit, expected_net_return,
    downside_q10. Optional horizon_sessions/holding_sessions/horizon overrides
    the policy horizon. Optional adtv20_shares must be known at decision time;
    otherwise past volume bars through that decision close supply ADTV.
    Required bars: date, ticker, open, close. Execution-volume checks prefer
    volume_continuous, then volume; without either, fillability is unverified.
    Open and close are raw price units multiplied by policy.price_scale.
    entry_date/exit_date and *_price_vnd label columns are diagnostic only.
    """
    policy = policy or PortfolioPolicy()
    if isinstance(policy, Mapping):
        policy = PortfolioPolicy(**policy)
    policy.validate()
    required_forecasts = {"decision_date", "ticker", "p_profit", "expected_net_return", "downside_q10"}
    required_bars = {"date", "ticker", "open", "close"}
    if missing := required_forecasts.difference(predictions.columns):
        raise ValueError(f"Missing prediction columns: {sorted(missing)}")
    if missing := required_bars.difference(bars.columns):
        raise ValueError(f"Missing bar columns: {sorted(missing)}")

    forecasts, market = predictions.copy(), bars.copy()
    forecasts["decision_date"] = _dates(forecasts["decision_date"])
    market["date"] = _dates(market["date"])
    if forecasts["decision_date"].isna().any() or market["date"].isna().any():
        raise ValueError("Decision and market dates must be valid")
    for frame in (forecasts, market):
        if frame["ticker"].isna().any():
            raise ValueError("Ticker must be supplied")
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        if frame["ticker"].eq("").any():
            raise ValueError("Ticker must not be empty")
    if market.duplicated(["date", "ticker"]).any():
        raise ValueError("Market bars must be unique per date and ticker")
    if forecasts.duplicated(["decision_date", "ticker"]).any():
        raise ValueError("Pass one forecast per decision date and ticker per experiment")
    market = market.sort_values(["ticker", "date"])
    for column in ("open", "close", "volume", "volume_continuous", "raw_factor"):
        if column in market:
            market[column] = pd.to_numeric(market[column], errors="coerce")
    volume_column = "volume_continuous" if "volume_continuous" in market else ("volume" if "volume" in market else None)
    if volume_column is not None:
        market["past_adtv_shares"] = market.groupby("ticker")[volume_column].transform(
            lambda series: series.where(series >= 0).rolling(
                policy.liquidity_lookback_sessions,
                min_periods=policy.minimum_liquidity_observations,
            ).mean()
        )
    rows = market.set_index(["date", "ticker"]).to_dict("index")
    index_dates = market.loc[market["ticker"].eq("VNINDEX"), "date"]
    calendar = sorted(index_dates.unique() if not index_dates.empty else market["date"].unique())
    calendar = [pd.Timestamp(day) for day in calendar]
    date_index = {day: index for index, day in enumerate(calendar)}
    warnings = [
        "Daily-bar open/close fills are proxies; no broker/depth/latency/queue or partial-fill simulation.",
        "Policy thresholds and downside floor are experiment assumptions, not calibrated production limits.",
        "Sale proceeds settle at the close of T+settlement_sessions and become usable at the following open; no margin or cash advance is modeled.",
        "Raw-price returns do not include dividend, split, or rights entitlements; held raw_factor changes are diagnostic only.",
    ]
    if index_dates.empty:
        warnings.append("VNINDEX calendar unavailable: using the union of supplied bar dates; verify calendar completeness independently.")
    if volume_column is None:
        warnings.append("Execution volume is unavailable: positive bar prices do not verify fillability.")
    rejected: Counter[str] = Counter()
    scheduled: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    pending_end = 0
    threshold_eligible = 0
    label_schedule_mismatches = 0
    deferred_exits: Counter[str] = Counter()
    stale_mark_reasons: Counter[str] = Counter()

    for row in forecasts.to_dict("records"):
        decision_date = row["decision_date"]
        if decision_date not in date_index:
            rejected["DECISION_SESSION_UNAVAILABLE"] += 1
            continue
        try:
            probability = float(row["p_profit"])
            expected_return = float(row["expected_net_return"])
            downside = float(row["downside_q10"])
            horizon_raw = next((row[key] for key in ("horizon_sessions", "holding_sessions", "horizon") if key in row and pd.notna(row[key])), policy.holding_sessions)
            horizon = int(horizon_raw)
            if float(horizon_raw) != horizon or horizon < policy.minimum_holding_sessions:
                raise ValueError("Invalid horizon")
        except (TypeError, ValueError, OverflowError):
            rejected["INVALID_FORECAST"] += 1
            continue
        if not all(math.isfinite(value) for value in (probability, expected_return, downside)) or not 0 <= probability <= 1:
            rejected["INVALID_FORECAST"] += 1
            continue
        if probability < policy.min_profit_probability or expected_return <= policy.min_expected_net_return:
            rejected["BELOW_PROFIT_GATE"] += 1
            continue
        threshold_eligible += 1
        entry_index = date_index[decision_date] + 1
        if entry_index >= len(calendar):
            pending_end += 1
            continue
        entry_date = calendar[entry_index]
        exit_index = entry_index + horizon - 1
        exit_date = calendar[exit_index] if exit_index < len(calendar) else None
        for label_key, simulated_date in (("entry_date", entry_date), ("exit_date", exit_date)):
            if simulated_date is not None and label_key in row and pd.notna(row[label_key]):
                try:
                    label_date = pd.Timestamp(row[label_key]).tz_localize(None).normalize()
                except (TypeError, ValueError):
                    label_date = None
                if label_date != simulated_date:
                    label_schedule_mismatches += 1
        adtv = row.get("adtv20_shares")
        if not _finite_positive(adtv):
            past = market[(market["ticker"] == row["ticker"]) & (market["date"] <= decision_date)]
            adtv = past["past_adtv_shares"].iloc[-1] if not past.empty and "past_adtv_shares" in past else None
        scheduled.setdefault(entry_date, []).append({
            "ticker": row["ticker"], "decision_date": decision_date,
            "entry_index": entry_index, "exit_index": exit_index,
            "planned_exit_date": exit_date, "holding_sessions": horizon,
            "p_profit": probability, "expected_net_return": expected_return,
            "downside_q10": downside, "adtv20_shares": adtv,
        })

    cash = float(policy.initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    marks: dict[str, tuple[float, pd.Timestamp]] = {}
    receivables: list[dict[str, Any]] = []
    adjustment_events: list[dict[str, Any]] = []
    trades, nav_rows = [], []
    accepted_entries = 0
    entry_friction = policy.friction_bps / 20_000.0
    start = forecasts["decision_date"].min() if not forecasts.empty else None
    for day in calendar:
        if start is None or day < start:
            continue
        index = date_index[day]
        # Only prior closes mark existing holdings for sizing at this open.
        sizing_nav = cash + sum(receipt["amount"] for receipt in receivables) + sum(position["shares"] * marks[ticker][0] for ticker, position in positions.items())
        candidates = sorted(scheduled.get(day, []), key=lambda row: (-row["expected_net_return"], -row["p_profit"], row["ticker"]))
        for signal in candidates:
            ticker = signal["ticker"]
            if ticker in positions:
                rejected["ALREADY_HELD"] += 1
                continue
            if len(positions) >= policy.max_positions:
                rejected["POSITION_CAP"] += 1
                continue
            quote = rows.get((day, ticker), {})
            if not _finite_positive(quote.get("open")):
                rejected["NO_VALID_NEXT_SESSION_OPEN"] += 1
                continue  # Cancel; never fill from a later or forward-filled quote.
            if volume_column is not None and not _finite_positive(quote.get(volume_column)):
                rejected["NO_VALID_NEXT_SESSION_VOLUME"] += 1
                continue
            if not _finite_positive(signal["adtv20_shares"]):
                rejected["NO_CAUSAL_LIQUIDITY_ESTIMATE"] += 1
                continue
            raw_open = float(quote["open"]) * policy.price_scale
            fill_price = raw_open * (1.0 + entry_friction)
            downside_risk = max(policy.downside_floor, -signal["downside_q10"])
            max_notional = min(
                sizing_nav * policy.max_single_weight,
                sizing_nav * policy.risk_budget_per_position / downside_risk,
            )
            # Keep an exit minimum-fee reserve; unsettled proceeds cannot fund buys.
            available_cash = max(0.0, cash - sizing_nav * policy.cash_buffer - (len(positions) + 1) * policy.minimum_fee)
            cash_notional = min(available_cash / (1.0 + policy.brokerage_fee_rate), max(0.0, available_cash - policy.minimum_fee))
            liquidity_shares = math.floor(float(signal["adtv20_shares"]) * policy.liquidity_participation / policy.lot_size) * policy.lot_size
            shares = min(math.floor(min(max_notional, cash_notional) / fill_price / policy.lot_size) * policy.lot_size, liquidity_shares)
            notional = shares * fill_price
            if shares <= 0 or notional < policy.min_notional:
                rejected["INSUFFICIENT_RISK_CASH_OR_LIQUIDITY_BUDGET"] += 1
                continue
            fee = _fee(notional, policy)
            cost = notional + fee
            if cost > available_cash + 0.01:
                raise ArithmeticError("Entry exceeds available cash after reservation")
            cash -= cost
            if cash < -0.01:
                raise ArithmeticError("Cash portfolio cannot borrow")
            positions[ticker] = {**signal, "entry_date": day, "shares": shares,
                "entry_price_vnd": fill_price, "buy_notional": notional,
                "buy_fee": fee, "entry_cash_cost": cost,
                "last_raw_factor": quote.get("raw_factor")}
            marks[ticker] = (raw_open, day)
            accepted_entries += 1

        stale_marks = 0
        for ticker, position in list(positions.items()):
            quote = rows.get((day, ticker), {})
            raw_factor, old_factor = quote.get("raw_factor"), position.get("last_raw_factor")
            if _finite_positive(raw_factor):
                if _finite_positive(old_factor) and abs(float(raw_factor) / float(old_factor) - 1.0) > 0.01:
                    adjustment_events.append({"ticker": ticker, "date": day.date().isoformat(),
                        "previous_raw_factor": float(old_factor), "raw_factor": float(raw_factor),
                        "relative_change": float(raw_factor) / float(old_factor) - 1.0})
                position["last_raw_factor"] = raw_factor
            valid_volume = volume_column is None or _finite_positive(quote.get(volume_column))
            if _finite_positive(quote.get("close")) and valid_volume:
                raw_close = float(quote["close"]) * policy.price_scale
                marks[ticker] = (raw_close, day)
            else:
                stale_marks += 1
                reason = "NO_VALID_EXECUTION_VOLUME" if not valid_volume else "NO_VALID_CLOSE"
                stale_mark_reasons[reason] += 1
                if index >= position["exit_index"]:
                    deferred_exits[reason] += 1
                continue  # Missing or untradable quotes cannot create exit fills.
            if index < position["exit_index"]:
                continue
            fill_price = raw_close * (1.0 - entry_friction)
            notional = position["shares"] * fill_price
            fee = _fee(notional, policy)
            tax = notional * policy.sell_tax_rate
            credit = notional - fee - tax
            pnl = credit - position["entry_cash_cost"]
            if credit < 0:
                # An exceptional minimum fee can exceed proceeds; charge it from
                # the settled exit-fee reserve instead of creating a cash loan.
                cash += credit
            else:
                settlement_index = index + policy.sell_cash_settlement_sessions
                receivables.append({"ticker": ticker, "sale_date": day,
                    "settlement_index": settlement_index,
                    "settlement_date": calendar[settlement_index] if settlement_index < len(calendar) else None,
                    "amount": credit})
            trades.append({key: position.get(key) for key in TRADE_COLUMNS} | {
                "exit_date": day, "exit_price_vnd": fill_price,
                "sell_notional": notional, "sell_fee": fee, "sell_tax": tax,
                "exit_cash_credit": credit, "net_pnl_vnd": pnl,
                "net_return": pnl / position["entry_cash_cost"],
            })
            del positions[ticker]
            del marks[ticker]
        matured = [receipt for receipt in receivables if receipt["settlement_index"] <= index]
        cash += sum(receipt["amount"] for receipt in matured)
        receivables = [receipt for receipt in receivables if receipt["settlement_index"] > index]
        if cash < -0.01:
            raise ArithmeticError("Settled cash portfolio cannot borrow to pay sale fees")
        positions_value = sum(position["shares"] * marks[ticker][0] for ticker, position in positions.items())
        unsettled_receivable = sum(receipt["amount"] for receipt in receivables)
        total_nav = cash + unsettled_receivable + positions_value
        locked_value = sum(position["shares"] * marks[ticker][0] for ticker, position in positions.items() if index - position["entry_index"] < policy.minimum_holding_sessions - 1)
        nav_rows.append({
            "date": day, "cash": cash, "unsettled_receivable": unsettled_receivable, "positions_value": positions_value,
            "total_nav": total_nav,
            "equity_exposure": positions_value / total_nav if total_nav > 0 else 0.0,
            "cash_weight": cash / total_nav if total_nav > 0 else 0.0,
            "locked_positions_value": locked_value, "open_positions": len(positions),
            "stale_marks": stale_marks,
        })

    trades_df = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    nav_df = pd.DataFrame(nav_rows, columns=NAV_COLUMNS)
    final_nav = float(nav_df["total_nav"].iloc[-1]) if not nav_df.empty else policy.initial_cash
    closed_returns = trades_df["net_return"]
    wins = trades_df.loc[trades_df["net_pnl_vnd"] > 0]
    losses = trades_df.loc[trades_df["net_pnl_vnd"] < 0]
    gross_profit = float(wins["net_pnl_vnd"].sum())
    gross_loss = float(-losses["net_pnl_vnd"].sum())
    cagr = None
    if not nav_df.empty:
        elapsed_days = (nav_df["date"].iloc[-1] - nav_df["date"].iloc[0]).days
        if elapsed_days >= 365 and final_nav > 0:
            cagr = (final_nav / policy.initial_cash) ** (365.25 / elapsed_days) - 1
        else:
            warnings.append("CAGR omitted: observed period is less than one year or final NAV is nonpositive.")
    if label_schedule_mismatches:
        warnings.append("Label entry/exit calendars differ from the market calendar; labels did not alter eligibility or fills.")
    if not nav_df.empty and nav_df["stale_marks"].sum():
        warnings.append("Some NAV marks carry the last known price; missing quotes never generate fills.")
    if adjustment_events:
        warnings.append("Raw-factor changes occurred during holdings; portfolio PnL is not corrected for corporate entitlements.")
    metrics = {
        "policy": asdict(policy), "initial_nav": policy.initial_cash,
        "final_nav": final_nav, "total_return": final_nav / policy.initial_cash - 1,
        "closed_trades": len(trades_df), "win_rate_closed_net": len(wins) / len(trades_df) if len(trades_df) else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "profit_factor_status": "DEFINED" if gross_loss else ("NO_LOSING_TRADES" if len(wins) else "NO_PROFIT_OR_LOSS"),
        "average_win_net_return": float(wins["net_return"].mean()) if len(wins) else None,
        "average_loss_net_return": float(losses["net_return"].mean()) if len(losses) else None,
        "average_win_net_pnl_vnd": float(wins["net_pnl_vnd"].mean()) if len(wins) else None,
        "average_loss_net_pnl_vnd": float(losses["net_pnl_vnd"].mean()) if len(losses) else None,
        "expectancy_net_return": float(closed_returns.mean()) if len(trades_df) else None,
        "expectancy_net_pnl_vnd": float(trades_df["net_pnl_vnd"].mean()) if len(trades_df) else None,
        "closed_net_pnl_vnd": float(trades_df["net_pnl_vnd"].sum()),
        "max_drawdown": float((1.0 - nav_df["total_nav"] / nav_df["total_nav"].cummax().clip(lower=policy.initial_cash)).max()) if not nav_df.empty else 0.0,
        "cagr": cagr,
        "max_equity_exposure": float(nav_df["equity_exposure"].max()) if not nav_df.empty else 0.0,
        "mean_equity_exposure": float(nav_df["equity_exposure"].mean()) if not nav_df.empty else 0.0,
        "min_cash_weight": float(nav_df["cash_weight"].min()) if not nav_df.empty else 1.0,
        "final_settled_cash": cash,
        "final_unsettled_receivable": sum(receipt["amount"] for receipt in receivables),
        "pending_receivables": [{"ticker": receipt["ticker"], "sale_date": receipt["sale_date"].date().isoformat(),
            "settlement_date": receipt["settlement_date"].date().isoformat() if receipt["settlement_date"] is not None else None,
            "amount": receipt["amount"]} for receipt in receivables],
        "adjustment_events_in_held_positions": adjustment_events,
        "calendar_source": "VNINDEX" if not index_dates.empty else "BAR_DATE_UNION",
        "execution_volume_source": volume_column,
        "open_positions": [{"ticker": ticker, "entry_date": position["entry_date"].date().isoformat(),
            "planned_exit_date": position["planned_exit_date"].date().isoformat() if position["planned_exit_date"] is not None else None,
            "shares": position["shares"], "entry_cash_cost": position["entry_cash_cost"],
            "mark_price_vnd": marks[ticker][0], "mark_date": marks[ticker][1].date().isoformat(),
            "market_value": position["shares"] * marks[ticker][0]} for ticker, position in positions.items()],
        "coverage": {"predictions": len(forecasts), "threshold_eligible": threshold_eligible,
            "accepted_entries": accepted_entries, "closed_trades": len(trades_df),
            "open_positions": len(positions), "pending_unobserved_next_session": pending_end,
            "unsettled_sale_receipts": len(receivables),
            "simulated_sessions": len(nav_df), "label_schedule_mismatches": label_schedule_mismatches,
            "deferred_exit_sessions_by_reason": dict(deferred_exits),
            "stale_mark_sessions_by_reason": dict(stale_mark_reasons),
            "rejections": dict(rejected)},
        "warnings": warnings,
    }
    return trades_df, nav_df, metrics
