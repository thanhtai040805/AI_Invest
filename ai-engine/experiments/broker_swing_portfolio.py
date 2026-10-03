"""Offline broker swing ledger with causal next-open entries and next-close exits.

Close-state decisions can first sell at holding-session three's close. The
maximum planned hold is seven sessions; absent executable quotes may delay it.
Daily bars are execution proxies, not evidence of auction or queue fills.
"""

from collections import Counter
from dataclasses import asdict, dataclass
import math
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class BrokerSwingPolicy:
    """Capital sleeve and execution assumptions, not training-optimal limits."""

    initial_cash: float = 1_000_000_000.0
    min_expected_net_return: float = 0.0
    max_holding_sessions: int = 7
    max_positions: int = 5
    max_single_weight: float = 0.10
    risk_budget_per_position: float = 0.005
    downside_floor: float = 0.01
    cash_buffer: float = 0.10
    liquidity_participation: float = 0.01
    price_scale: float = 1000.0
    friction_bps: float = 20.0
    brokerage_fee_rate: float = 0.001
    sell_tax_rate: float = 0.001
    minimum_fee: float = 10_000.0
    min_notional: float = 10_000_000.0
    sell_cash_settlement_sessions: int = 2
    lot_size: int = 100

    def validate(self):
        for name in ("initial_cash", "price_scale", "downside_floor"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("max_single_weight", "risk_budget_per_position", "cash_buffer", "liquidity_participation"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        for name in ("friction_bps", "brokerage_fee_rate", "sell_tax_rate", "minimum_fee", "min_notional"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if not math.isfinite(self.min_expected_net_return):
            raise ValueError("min_expected_net_return must be finite")
        for name in ("max_holding_sessions", "max_positions", "lot_size", "sell_cash_settlement_sessions"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 3 <= self.max_holding_sessions <= 7:
            raise ValueError("max_holding_sessions must be between 3 and 7")
        if self.sell_cash_settlement_sessions != 2:
            raise ValueError("Broker sale cash must settle at T+2")
        if self.friction_bps >= 20_000 or self.brokerage_fee_rate + self.sell_tax_rate >= 1:
            raise ValueError("Costs must leave positive proportional sale proceeds")


ENTRY_COLUMNS = {
    "decision_date", "ticker", "expected_net_return", "p_profit", "downside_q10",
    "expected_holding_sessions", "market_risk_probability", "market_opportunity_score",
    "expert_id", "adtv20_shares",
}
EXIT_COLUMNS = {
    "entry_decision_date", "ticker", "state_date", "holding_sessions", "continuation_net_return",
}
TRADE_COLUMNS = [
    "ticker", "expert_id", "decision_date", "entry_date", "exit_date", "planned_exit_date",
    "scheduled_exit_date", "deferred_exit_sessions",
    "exit_reason", "shares", "entry_price_vnd", "exit_price_vnd", "buy_notional", "buy_fee",
    "entry_cash_cost", "sell_notional", "sell_fee", "sell_tax", "exit_cash_credit",
    "net_pnl_vnd", "net_return", "planned_holding_sessions", "actual_holding_sessions",
    "expected_holding_sessions", "expected_net_return", "p_profit", "downside_q10",
    "market_risk_probability", "market_opportunity_score", "capital_days_vnd",
]
NAV_COLUMNS = ["date", "cash", "unsettled_receivable", "positions_value", "total_nav",
               "equity_exposure", "cash_weight", "open_positions", "stale_marks"]


def _positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _normalize(frame, date_columns):
    frame = frame.copy()
    if frame["ticker"].isna().any():
        raise ValueError("Ticker must be supplied")
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    if frame["ticker"].eq("").any():
        raise ValueError("Ticker must not be empty")
    for name in date_columns:
        dates = pd.to_datetime(frame[name], errors="raise")
        if isinstance(dates.dtype, pd.DatetimeTZDtype) or dates.isna().any() or not dates.eq(dates.dt.normalize()).all():
            raise ValueError(f"{name} must contain timezone-free session dates")
        frame[name] = dates
    return frame


def _evidence(row):
    return {key: value for key, value in row.items()
            if key in {"model_id", "model_version", "trained_through"}
            or key.endswith(("_model_id", "_model_version", "_trained_through"))}


def simulate_broker_swing(entry_forecasts, exit_forecasts, bars, policy=None, exit_mode="adaptive"):
    """Return closed trades, end-session NAV and economic diagnostics.

    p_profit is descriptive. Positive expected net edge qualifies an entry;
    edge per expected capital session orders it. Market risk continuously reduces
    its risk budget. Supplied ADTV must be known at the original decision close.
    Exit states at holding-session 2..6 schedule the next close; a nonpositive
    continuation advantage exits. Static control ignores learned exit states.
    """
    policy = policy or BrokerSwingPolicy()
    if isinstance(policy, Mapping):
        policy = BrokerSwingPolicy(**policy)
    policy.validate()
    if exit_mode not in {"adaptive", "static"}:
        raise ValueError("exit_mode must be adaptive or static")
    for frame, required, name in ((entry_forecasts, ENTRY_COLUMNS, "entry forecasts"),
                                  (bars, {"date", "ticker", "open", "close"}, "bars")):
        if missing := required.difference(frame.columns):
            raise ValueError(f"Missing {name} columns: {sorted(missing)}")
    volume_column = "volume_continuous" if "volume_continuous" in bars else "volume"
    if volume_column not in bars:
        raise ValueError("Execution bars require observed volume")
    entries = _normalize(entry_forecasts, ["decision_date"])
    market = _normalize(bars, ["date"])
    if entries.duplicated(["decision_date", "ticker"]).any() or market.duplicated(["date", "ticker"]).any():
        raise ValueError("Entry and bar keys must be unique")
    index_dates = market.loc[market["ticker"].eq("VNINDEX"), "date"]
    calendar = sorted(index_dates.unique() if not index_dates.empty else market["date"].unique())
    calendar = [pd.Timestamp(day) for day in calendar]
    if set(market["date"]).difference(calendar):
        raise ValueError("Market calendar is missing observed stock sessions")
    date_index = {day: index for index, day in enumerate(calendar)}
    quotes = market.set_index(["date", "ticker"]).to_dict("index")
    rejected, exit_rejected, deferred = Counter(), Counter(), Counter()
    scheduled, exit_states = {}, {}

    if exit_mode == "adaptive" and not exit_forecasts.empty:
        if missing := EXIT_COLUMNS.difference(exit_forecasts.columns):
            raise ValueError(f"Missing exit forecast columns: {sorted(missing)}")
        states = _normalize(exit_forecasts, ["entry_decision_date", "state_date"])
        if states.duplicated(["entry_decision_date", "ticker", "state_date"]).any():
            raise ValueError("Exit-state keys must be unique")
        for row in states.to_dict("records"):
            try:
                holding = int(row["holding_sessions"])
                continuation = float(row["continuation_net_return"])
                expected_state = date_index[row["entry_decision_date"]] + holding
                action = "HOLD" if continuation > 0 else "EXIT"
                if (float(row["holding_sessions"]) != holding or not 2 <= holding <= 6
                        or not math.isfinite(continuation)
                        or date_index[row["state_date"]] != expected_state
                        or (pd.notna(row.get("action")) and str(row["action"]).upper() != action)):
                    raise ValueError("Invalid state")
            except (TypeError, ValueError, OverflowError, KeyError):
                exit_rejected["INVALID_EXIT_STATE"] += 1
                continue
            exit_states[(row["entry_decision_date"], row["ticker"], row["state_date"])] = {**row, "action": action}

    for row in entries.to_dict("records"):
        try:
            numeric = {name: float(row[name]) for name in (
                "expected_net_return", "p_profit", "downside_q10", "expected_holding_sessions",
                "market_risk_probability", "market_opportunity_score", "adtv20_shares",
            )}
            if (not all(math.isfinite(value) for value in numeric.values())
                    or not 0 <= numeric["p_profit"] <= 1 or not 0 <= numeric["market_risk_probability"] <= 1
                    or not 3 <= numeric["expected_holding_sessions"] <= policy.max_holding_sessions
                    or numeric["adtv20_shares"] <= 0 or not isinstance(row["expert_id"], str)
                    or not row["expert_id"].strip()):
                raise ValueError("Invalid entry")
        except (TypeError, ValueError, OverflowError):
            rejected["INVALID_ENTRY_FORECAST"] += 1
            continue
        if numeric["expected_net_return"] <= max(0.0, policy.min_expected_net_return):
            rejected["NO_POSITIVE_NET_EDGE"] += 1
            continue
        if row["ticker"] == "VNINDEX" or row["decision_date"] not in date_index:
            rejected["INVALID_ENTRY_SESSION_OR_TICKER"] += 1
            continue
        entry_index = date_index[row["decision_date"]] + 1
        if entry_index >= len(calendar):
            rejected["NO_OBSERVED_NEXT_SESSION"] += 1
            continue
        scheduled.setdefault(calendar[entry_index], []).append({**row, **numeric, "entry_index": entry_index})

    cash = float(policy.initial_cash)
    positions, marks, receivables = {}, {}, []
    trades, nav = [], []
    accepted_entries = 0
    expert_entries = Counter()
    capital_days = 0.0
    friction = policy.friction_bps / 20_000
    for day in calendar:
        index = date_index[day]
        sizing_nav = cash + sum(item["amount"] for item in receivables) + sum(
            position["shares"] * marks[ticker][0] for ticker, position in positions.items()
        )
        signals = sorted(scheduled.get(day, []), key=lambda signal: (
            -signal["expected_net_return"] / signal["expected_holding_sessions"],
            -signal["expected_net_return"], -signal["market_opportunity_score"], signal["ticker"],
        ))
        for signal in signals:
            ticker = signal["ticker"]
            if ticker in positions or len(positions) >= policy.max_positions:
                rejected["ALREADY_HELD" if ticker in positions else "POSITION_CAP"] += 1
                continue
            quote = quotes.get((day, ticker), {})
            if not _positive(quote.get("open")) or not _positive(quote.get(volume_column)):
                rejected["NO_EXECUTABLE_NEXT_OPEN"] += 1
                continue
            fill = float(quote["open"]) * policy.price_scale * (1 + friction)
            risk_multiplier = max(0.25, 1 - signal["market_risk_probability"])
            downside = max(policy.downside_floor, -signal["downside_q10"])
            budget = min(sizing_nav * policy.max_single_weight,
                         sizing_nav * policy.risk_budget_per_position * risk_multiplier / downside)
            available = max(0.0, cash - sizing_nav * policy.cash_buffer - (len(positions) + 1) * policy.minimum_fee)
            cash_budget = min(available / (1 + policy.brokerage_fee_rate), max(0.0, available - policy.minimum_fee))
            liquidity_shares = math.floor(signal["adtv20_shares"] * policy.liquidity_participation / policy.lot_size) * policy.lot_size
            shares = min(math.floor(min(budget, cash_budget) / fill / policy.lot_size) * policy.lot_size, liquidity_shares)
            notional = shares * fill
            if shares <= 0 or notional < policy.min_notional:
                rejected["INSUFFICIENT_CAPITAL_OR_LIQUIDITY"] += 1
                continue
            fee = max(policy.minimum_fee, notional * policy.brokerage_fee_rate)
            cost = notional + fee
            if cost > available + 0.01:
                raise ArithmeticError("Entry exceeds settled cash budget")
            cash -= cost
            forced_index = signal["entry_index"] + policy.max_holding_sessions - 1
            positions[ticker] = {**signal, "entry_date": day, "shares": shares,
                "entry_price_vnd": fill, "buy_notional": notional, "buy_fee": fee,
                "entry_cash_cost": cost, "forced_index": forced_index, "exit_index": forced_index,
                "exit_reason": "MAX_PLANNED_HOLD", "planned_holding_sessions": policy.max_holding_sessions,
                "planned_exit_date": calendar[forced_index] if forced_index < len(calendar) else None}
            marks[ticker] = (float(quote["open"]) * policy.price_scale, day)
            accepted_entries += 1
            expert_entries[signal["expert_id"]] += 1

        stale = 0
        for ticker, position in list(positions.items()):
            capital_days += position["entry_cash_cost"]
            quote = quotes.get((day, ticker), {})
            valid = _positive(quote.get("close")) and _positive(quote.get(volume_column))
            if valid:
                marks[ticker] = (float(quote["close"]) * policy.price_scale, day)
            else:
                stale += 1
            if index >= position["exit_index"]:
                if not valid:
                    deferred["NO_EXECUTABLE_CLOSE"] += 1
                    continue
                fill = marks[ticker][0] * (1 - friction)
                notional = position["shares"] * fill
                fee = max(policy.minimum_fee, notional * policy.brokerage_fee_rate)
                tax = notional * policy.sell_tax_rate
                credit = notional - fee - tax
                if credit < 0:
                    cash += credit
                else:
                    receivables.append({"ticker": ticker, "sale_date": day,
                                        "settlement_index": index + 2, "amount": credit})
                actual_holding = index - position["entry_index"] + 1
                trades.append({key: position.get(key) for key in TRADE_COLUMNS} | _evidence(position) | {
                    "exit_date": day, "exit_price_vnd": fill, "sell_notional": notional,
                    "sell_fee": fee, "sell_tax": tax, "exit_cash_credit": credit,
                    "net_pnl_vnd": credit - position["entry_cash_cost"],
                    "net_return": credit / position["entry_cash_cost"] - 1,
                    "actual_holding_sessions": actual_holding,
                    "scheduled_exit_date": calendar[position["exit_index"]],
                    "deferred_exit_sessions": index - position["exit_index"],
                    "capital_days_vnd": position["entry_cash_cost"] * actual_holding,
                })
                del positions[ticker]
                del marks[ticker]
                continue
            if exit_mode == "adaptive":
                state = exit_states.get((position["decision_date"], ticker, day))
                if state is not None and state["action"] == "EXIT":
                    position["exit_index"] = min(position["forced_index"], index + 1)
                    position["exit_reason"] = "LEARNED_EXIT"
                    position.update({f"exit_{key}": value for key, value in _evidence(state).items()})

        matured = [item for item in receivables if item["settlement_index"] <= index]
        cash += sum(item["amount"] for item in matured)
        receivables = [item for item in receivables if item["settlement_index"] > index]
        if cash < -0.01:
            raise ArithmeticError("Cash sleeve cannot borrow to pay fees")
        value = sum(position["shares"] * marks[ticker][0] for ticker, position in positions.items())
        receivable = sum(item["amount"] for item in receivables)
        total = cash + receivable + value
        nav.append({"date": day, "cash": cash, "unsettled_receivable": receivable,
                    "positions_value": value, "total_nav": total,
                    "equity_exposure": value / total if total > 0 else 0.0,
                    "cash_weight": cash / total if total > 0 else 0.0,
                    "open_positions": len(positions), "stale_marks": stale})

    trades = pd.DataFrame(trades) if trades else pd.DataFrame(columns=TRADE_COLUMNS)
    nav = pd.DataFrame(nav, columns=NAV_COLUMNS)
    daily_realized = trades.groupby("exit_date")["net_pnl_vnd"].sum().reindex(nav["date"], fill_value=0.0).cumsum()
    positive = daily_realized.gt(0)
    first_positive = daily_realized.index[positive][0] if positive.any() else None
    profit = float(trades.loc[trades["net_pnl_vnd"] > 0, "net_pnl_vnd"].sum())
    loss = float(-trades.loc[trades["net_pnl_vnd"] < 0, "net_pnl_vnd"].sum())
    final_nav = float(nav["total_nav"].iloc[-1]) if len(nav) else policy.initial_cash
    closed_pnl = float(trades["net_pnl_vnd"].sum())
    expert_stats = []
    for expert, accepted in sorted(expert_entries.items()):
        group = trades.loc[trades["expert_id"].eq(expert)]
        expert_stats.append({"expert_id": expert, "accepted_entries": accepted,
                             "closed_trades": len(group), "open_positions": accepted - len(group),
                             "net_pnl_vnd": float(group["net_pnl_vnd"].sum()),
                             "win_rate": float(group["net_pnl_vnd"].gt(0).mean()) if len(group) else None})
    metrics = {
        "policy": asdict(policy), "exit_mode": exit_mode, "initial_nav": policy.initial_cash,
        "final_nav": final_nav, "total_return": final_nav / policy.initial_cash - 1,
        "closed_trades": len(trades), "closed_net_pnl_vnd": closed_pnl,
        "gross_profit_vnd": profit, "gross_loss_vnd": loss,
        "profit_factor": profit / loss if loss else None,
        "profit_factor_status": "DEFINED" if loss else ("NO_LOSING_TRADES" if profit else "NO_PROFIT_OR_LOSS"),
        "win_rate_closed_net": float(trades["net_pnl_vnd"].gt(0).mean()) if len(trades) else None,
        "expectancy_net_return": float(trades["net_return"].mean()) if len(trades) else None,
        "max_drawdown": float((1 - nav["total_nav"] / nav["total_nav"].cummax().clip(lower=policy.initial_cash)).max()) if len(nav) else 0.0,
        "mean_equity_exposure": float(nav["equity_exposure"].mean()) if len(nav) else 0.0,
        "first_positive_realized_pnl_date": first_positive.date().isoformat() if first_positive is not None else None,
        "sessions_to_first_positive_realized_pnl": int(positive.to_numpy().argmax()) if positive.any() else None,
        "realized_pnl_vnd_at_20_sessions": float(daily_realized.iloc[19]) if len(daily_realized) >= 20 else None,
        "profitable_realized_session_fraction": float(positive.mean()) if len(positive) else 0.0,
        "mean_actual_holding_sessions": float(trades["actual_holding_sessions"].mean()) if len(trades) else None,
        "max_actual_holding_sessions": int(trades["actual_holding_sessions"].max()) if len(trades) else None,
        "trades_exceeding_planned_horizon": int(trades["actual_holding_sessions"].gt(trades["planned_holding_sessions"]).sum()),
        "closed_trades_with_deferred_exit": int(trades["deferred_exit_sessions"].gt(0).sum()),
        "capital_days_vnd": capital_days,
        "closed_capital_days_vnd": float(trades["capital_days_vnd"].sum()),
        "net_profit_per_capital_day": closed_pnl / capital_days if capital_days else None,
        "net_profit_per_capital_day_unit": "VND / (VND * exchange holding session); decimal return per capital-weighted session",
        "per_expert": expert_stats, "final_settled_cash": cash,
        "final_unsettled_receivable": sum(item["amount"] for item in receivables),
        "pending_receivables": [{"ticker": item["ticker"], "sale_date": item["sale_date"].date().isoformat(),
                                 "amount": item["amount"]} for item in receivables],
        "open_positions": [{"ticker": ticker, "entry_date": position["entry_date"].date().isoformat(),
                            "shares": position["shares"], "mark_price_vnd": marks[ticker][0],
                            "mark_date": marks[ticker][1].date().isoformat(),
                            "actual_holding_sessions": len(calendar) - position["entry_index"],
                            "planned_holding_sessions": position["planned_holding_sessions"],
                            "planned_exit_date": position["planned_exit_date"].date().isoformat() if position["planned_exit_date"] is not None else None,
                            "overdue_exit_sessions": max(0, len(calendar) - position["exit_index"])}
                           for ticker, position in positions.items()],
        "coverage": {"entry_predictions": len(entries), "accepted_entries": accepted_entries,
                     "simulated_sessions": len(nav), "entry_rejections": dict(rejected),
                     "exit_state_rejections": dict(exit_rejected), "deferred_exit_sessions": dict(deferred)},
        "warnings": ["Daily next-open/next-close fills are proxies; no auction volume, depth, queue or partial-fill evidence.",
                     "Corporate-action entitlements and price-band execution queues are not modeled.",
                     "Sale proceeds settle at T+2 close and fund entries only at the following open.",
                     "Capital limits are sleeve assumptions; a seven-session planned limit cannot force an untradable exit."],
    }
    return trades, nav, metrics
