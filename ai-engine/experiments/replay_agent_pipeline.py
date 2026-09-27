"""Event-driven historical paper replay using normalized OHLCV and DNSE history.

Agent decisions use each session's normalized 09:45 input; stops and fills use
DNSE OHLCV events and displayed depth fetched on demand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.backtest.cost_model import round_to_lot
from app.core.base_agent import BaseAgent
from app.core.registry import AgentRegistry
from app.domain.pipeline.daily_pipeline_orchestrator import DailyInvestmentPipeline
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.rules.execution.shadow_fill import shadow_fill
from app.infrastructure.database.pg_pool import get_conn
from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

logger = logging.getLogger(__name__)


def dumps(value: object) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class ReplayAudit:
    """Lightweight in-memory audit event recorder (Pure Python, Zero SQLite)."""

    def __init__(self):
        self.events: list[dict] = []
        self.last_hash = "GENESIS_REPLAY"

    def log_event(self, agent_id: str, event_type: str, details: dict) -> str:
        event_id = str(uuid.uuid4())
        self.last_hash = event_id
        self.events.append({
            "id": event_id,
            "agent": agent_id,
            "event_type": event_type,
            "details": details,
            "current_hash": event_id,
        })
        return event_id

    def verify_full_chain(self) -> tuple[bool, int, str | None]:
        return True, len(self.events), None


def market_dates(end: date, count: int) -> list[date]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day
                       FROM ohlcv_intraday_1m
                       WHERE (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date <= %s
                       ORDER BY day DESC LIMIT %s""", (end, count))
        days = [row[0] for row in cur.fetchall()]
    if not days:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT date FROM market_data_daily_calculation WHERE date <= %s ORDER BY date DESC LIMIT %s", (end, count))
            days = [row[0] for row in cur.fetchall()]
    return sorted(days)


def previous_market_date(current: date) -> date:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT max((time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)
                       FROM ohlcv_intraday_1m
                       WHERE (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date < %s""", (current,))
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        cur.execute("SELECT max(date) FROM market_data_daily_calculation WHERE date < %s", (current,))
        row = cur.fetchone()
        return row[0] if row and row[0] else current - timedelta(days=1)


def bars_for_day(day: date) -> dict[str, dict]:
    results = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, open, data_source
            FROM ohlcv_intraday_1m
            WHERE (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = %s
              AND (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::time = '09:45:00'
        """, (day,))
        for sym, open_p, data_source in cur.fetchall():
            price = float(open_p) * 1000.0 if open_p and 0 < float(open_p) < 1000.0 else float(open_p or 0)
            if price > 0:
                results[str(sym).upper()] = {"price": price, "source": data_source}

    return results


def intraday_bars_for_day(
    day: date,
    symbols: set[str],
    bar_cache: dict[tuple[date, str], list[dict]],
) -> list[dict]:
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    start = datetime.combine(day, day_time(9, 0), vn)
    end = datetime.combine(day, day_time(15, 0), vn)
    tool = get_intraday_tool()
    events = []
    for symbol in symbols:
        key = (day, symbol)
        bars = bar_cache.get(key)
        if bars is None:
            try:
                rows = tool.fetch(symbol, "1", int(start.timestamp()), int(end.timestamp()), strict=True)
            except Exception:
                logger.exception("Replay DNSE OHLCV history failed: %s %s", symbol, day)
                raise
            bars = []
            for row in rows:
                stamp = datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=vn) if stamp.tzinfo is None else stamp.astimezone(vn)
                if start <= stamp <= end:
                    bars.append({
                        "symbol": symbol, "time": stamp, "open": row["open"], "high": row["high"],
                        "low": row["low"], "close": row["close"], "volume": row["volume"],
                    })
            bar_cache[key] = bars
            if not bars:
                logger.warning("Replay DNSE OHLCV history empty: %s %s", symbol, day)
        events.extend(bars)
    return sorted(events, key=lambda bar: (bar["time"], bar["symbol"]))


def quote_history(day: date, symbol: str) -> list[dict]:
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    start = datetime.combine(day, day_time(9, 0), vn)
    end = datetime.combine(day, day_time(15, 0), vn)
    try:
        quotes = get_intraday_tool().fetch_quotes(
            symbol, int(start.timestamp()), int(end.timestamp())
        )
    except Exception:
        logger.exception("Replay DNSE quote history failed: %s %s", symbol, day)
        raise
    if not quotes:
        logger.warning("Replay DNSE quote history empty: %s %s", symbol, day)
    return [
        {"time": datetime.fromisoformat(row["time"]), "bid": row["bid"], "offer": row["offer"]}
        for row in quotes
    ]


def quote_events_for_day(
    day: date,
    symbols: set[str],
    quote_cache: dict[tuple[date, str], list[dict]],
) -> list[tuple[datetime, str]]:
    events = []
    for symbol in symbols:
        key = (day, symbol)
        snapshots = quote_cache.get(key)
        if snapshots is None:
            snapshots = quote_history(day, symbol)
            quote_cache[key] = snapshots
        events.extend((snapshot["time"], symbol) for snapshot in snapshots)
    return sorted(events)


def replay_book(snapshot: dict) -> dict:
    local_time = snapshot["time"].astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    clock = local_time.time()
    state = (
        "continuous_morning" if day_time(9, 15) <= clock < day_time(11, 30)
        else "continuous_afternoon" if day_time(13, 0) <= clock < day_time(14, 30)
        else "closed"
    )
    return {
        "symbol": snapshot.get("symbol"),
        "marketState": state,
        "lastUpdate": snapshot["time"].isoformat(),
        "bids": [{"price": x.get("price"), "volume": x.get("qtty", x.get("quantity", x.get("volume", 0)))} for x in snapshot["bid"]],
        "asks": [{"price": x.get("price"), "volume": x.get("qtty", x.get("quantity", x.get("volume", 0)))} for x in snapshot["offer"]],
    }


def executable_shares(book: dict, side: str, limit_price: float) -> int:
    levels = book.get("asks" if side == "BUY" else "bids", [])
    eligible = []
    for level in levels:
        try:
            price = float(level["price"])
            volume = int(level["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        price = round(price * 1000) if 0 < price < 500 else price
        if price > 0 and volume > 0 and (price <= limit_price if side == "BUY" else price >= limit_price):
            eligible.append((price, volume))
    eligible.sort(reverse=side == "SELL")
    return sum(volume for _, volume in eligible)


def fill_pending(
    day: date,
    bars: dict,
    pending_orders: list[dict],
    repository: PortfolioRepository,
    account_id: str,
    mark_as_of: date,
    fills_log: list[dict] | None = None,
    until: datetime | None = None,
    symbols: set[str] | None = None,
    quote_cache: dict[tuple[date, str], list[dict]] | None = None,
) -> list[dict]:
    remaining = []
    for order in pending_orders:
        symbol, side = order["symbol"], order["side"]
        if symbols is not None and symbol not in symbols:
            remaining.append(order)
            continue
        bar = bars.get(symbol)
        if not bar or bar["price"] <= 0:
            if order.get("entry_type") != "PILOT":
                remaining.append(order)
            continue
        quantity = round_to_lot(int(order["quantity"]))
        limit_price = float(order.get("limit_price") or bar["price"])
        if order.get("entry_type") == "PILOT":
            if bar["source"] != "DNSE":
                continue
            account = repository.get_account_state(user_id=account_id, as_of=mark_as_of)
            quantity = min(quantity, round_to_lot(int(float(account["total_nav"]) * 0.05 / limit_price)))
        if quantity <= 0:
            continue
        requested_at = order.get("not_before")
        if requested_at is not None:
            requested_at = requested_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
        if requested_at and requested_at.date() == day:
            earliest = requested_at
            require_later_snapshot = True
        else:
            earliest = datetime.combine(day, day_time(9, 15), ZoneInfo("Asia/Ho_Chi_Minh"))
            require_later_snapshot = False
        left = quantity
        history_key = (day, symbol)
        if quote_cache is not None:
            snapshots = quote_cache.get(history_key)
            if snapshots is None:
                snapshots = quote_history(day, symbol)
                quote_cache[history_key] = snapshots
        else:
            snapshots = quote_history(day, symbol)
        first = bisect_right(snapshots, earliest, key=lambda snapshot: snapshot["time"]) if require_later_snapshot else bisect_left(
            snapshots, earliest, key=lambda snapshot: snapshot["time"]
        )
        last_seen = None
        for snapshot_index in range(first, len(snapshots)):
            snapshot = snapshots[snapshot_index]
            if until is not None and snapshot["time"] >= until:
                break
            last_seen = snapshot["time"]
            book = replay_book({**snapshot, "symbol": symbol})
            if book["marketState"] == "closed":
                continue
            fill_qty = round_to_lot(min(left, executable_shares(book, side, limit_price)))
            if fill_qty <= 0:
                continue
            try:
                fill_price = shadow_fill(book, side, fill_qty, limit_price, now=snapshot["time"])
                result = repository.record_replay_execution(
                    symbol, side, fill_qty, fill_price, user_id=account_id,
                    executed_at=snapshot["time"], mark_as_of=mark_as_of,
                )
            except ValueError as exc:
                reason = str(exc)
                if "T+2.5" in reason or "locked" in reason:
                    break
                if "Insufficient" in reason and side == "BUY":
                    account = repository.get_account_state(user_id=account_id, as_of=mark_as_of)
                    while fill_qty > 0:
                        gross = fill_price * fill_qty
                        if gross + max(gross * 0.001, 10_000.0) <= float(account.get("cash_balance", 0)):
                            break
                        fill_qty -= 100
                    if not fill_qty:
                        break
                    result = repository.record_replay_execution(
                        symbol, side, fill_qty, fill_price, user_id=account_id,
                        executed_at=snapshot["time"], mark_as_of=mark_as_of,
                    )
                else:
                    logger.warning(f"Replay fill rejected {symbol} {side}: {exc}")
                    break
            except Exception as exc:
                logger.warning(f"Replay fill failed {symbol} {side}: {exc}")
                break

            filled = int(result["shares"])
            left -= filled
            gross = float(result["executed_price"]) * filled
            if fills_log is not None:
                fills_log.append({
                    "day": day.isoformat(), "time": snapshot["time"].isoformat(),
                    "symbol": symbol, "side": side, "quantity": filled,
                    "price": result["executed_price"],
                    "cost": max(gross * 0.001, 10_000.0) + (gross * 0.001 if side == "SELL" else 0.0),
                    "source": "DNSE_HISTORICAL_ORDER_BOOK",
                    "reason": order.get("reason"),
                })
            if left <= 0:
                break
        if left > 0:
            order["quantity"] = left
            if until is not None and last_seen is not None:
                current = order.get("not_before")
                current_local = current.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")) if current else None
                if current_local is None or current_local.date() != day or last_seen > current:
                    order["not_before"] = last_seen.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            elif order.get("not_before") and order["not_before"].date() != day:
                order.pop("not_before", None)
            remaining.append(order)
    return remaining


def get_portfolio_state(
    day: date,
    repository: PortfolioRepository,
    account_id: str,
    mark_as_of: date,
    returns_series: list[float],
) -> dict:
    replay_at = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=45, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    account = repository.get_account_state(user_id=account_id, as_of=mark_as_of)
    positions = repository.get_open_positions(
        user_id=account_id, as_of=mark_as_of, as_of_time=replay_at
    )
    sector_exposure = {}
    locked_value = 0.0
    for position in positions:
        val = int(position.get("shares", 0)) * float(position.get("current_price", 0))
        sec = position.get("sector", "Unknown")
        sector_exposure[sec] = sector_exposure.get(sec, 0.0) + val
        locked_value += int(position.get("locked_t25_shares", 0)) * float(position.get("current_price", 0))

    return {
        "total_nav": float(account.get("total_nav", 1_000_000_000.0)),
        "peak_nav": float(account.get("peak_nav", 1_000_000_000.0)),
        "cash_vnd": float(account.get("cash_balance", 1_000_000_000.0)),
        "positions": positions,
        "sector_exposure": sector_exposure,
        "locked_t25_value": locked_value,
        "returns_series": returns_series,
    }


def enqueue_order(
    pending: list[dict], symbol: str, side: str, quantity: int, entry_type: str = "STANDARD",
    limit_price: float | None = None, not_before: datetime | None = None, reason: str | None = None,
) -> None:
    if quantity > 0 and not any(o["symbol"] == symbol and o["side"] == side for o in pending):
        order = {"symbol": symbol, "side": side, "quantity": quantity, "entry_type": entry_type}
        if limit_price and limit_price > 0:
            order["limit_price"] = limit_price
        if not_before:
            order["not_before"] = not_before
        if reason:
            order["reason"] = reason
        pending.append(order)


def price_to_vnd(value: float) -> float:
    return value * 1000.0 if 0 < value < 1000.0 else value


async def monitor_intraday_bar(
    day: date,
    bar: dict,
    repository: PortfolioRepository,
    account_id: str,
    mark_as_of: date,
    pending_orders: list[dict],
    fills_log: list[dict],
    peak_prices: dict[str, float],
    active_symbols: set[str],
    quote_cache: dict[tuple[date, str], list[dict]],
) -> None:
    symbol = str(bar["symbol"]).upper()
    if symbol not in active_symbols:
        return
    if any(order["symbol"] == symbol and order["side"] == "SELL" for order in pending_orders):
        return
    price = price_to_vnd(bar["low"])
    if price <= 0:
        return
    event_time = bar["time"]
    positions = repository.get_open_positions(
        user_id=account_id, as_of=mark_as_of, as_of_time=event_time
    )
    position = next((pos for pos in positions if pos["ticker"].upper() == symbol), None)
    if not position:
        return

    peak_prices[symbol] = max(peak_prices.get(symbol, float(position["average_price"])), price_to_vnd(bar["high"]))
    result = await AgentRegistry.dispatch("position_monitoring", {
        "positions": [{
            **position,
            "entry_price": float(position["average_price"]),
            "current_price": price,
            "peak_price": peak_prices[symbol],
        }],
        "nav": repository.get_account_state(user_id=account_id, as_of=mark_as_of).get("total_nav", 0),
        "current_time": event_time.isoformat(),
        "target_date": day.isoformat(),
        "market_data_date": mark_as_of.isoformat(),
        "replay_account_id": account_id,
        "is_replay": True,
        "auto_dispatch": False,
    })
    data = result.get("result", {}).get("data", {})
    stop_orders = data.get("stop_loss_orders", [])
    for stop_order in stop_orders:
        quantity = int(stop_order.get("quantity", 0))
        if quantity <= 0:
            continue
        order = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": quantity,
            "entry_type": "STOP",
            "limit_price": price * 0.985,
            "not_before": event_time,
            "reason": stop_order.get("reason"),
        }
        unfilled = fill_pending(day, {symbol: {"price": price, "source": "DNSE"}}, [order],
                               repository, account_id, mark_as_of, fills_log,
                               until=event_time, symbols={symbol}, quote_cache=quote_cache)
        pending_orders.extend(unfilled)
        if not unfilled:
            remaining_positions = repository.get_open_positions(
                user_id=account_id, as_of=mark_as_of, as_of_time=event_time
            )
            if not any(pos["ticker"].upper() == symbol for pos in remaining_positions):
                active_symbols.discard(symbol)


async def run(args: argparse.Namespace) -> None:
    account_id = args.account_id or os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794")
    repo = PortfolioRepository()

    end_date = date.fromisoformat(args.end)
    now_vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    if end_date > now_vn.date() or (end_date == now_vn.date() and now_vn.time() < day_time(15, 0)):
        raise ValueError("Replay can include today's session only after the 15:00 market close.")

    days = market_dates(end_date, args.days)
    if not days:
        print(f"Không có dữ liệu ngày giao dịch trước ngày {end_date}.")
        return
    if len(days) > 52:
        raise ValueError(f"DNSE historical bid/ask is available for at most 52 sessions; requested {len(days)}.")

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT count(*), count(DISTINCT (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)
            FROM ohlcv_intraday_1m
            WHERE (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date BETWEEN %s AND %s
              AND (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::time = '09:45:00'
        """, (days[0], days[-1]))
        scan_rows, scan_days = cur.fetchone()
    if not scan_rows or scan_days != len(days):
        raise RuntimeError(
            f"Normalized 09:45 OHLCV scan inputs are incomplete: {scan_rows} rows/{scan_days} days; "
            f"expected {len(days)} trading days"
        )

    if args.reset:
        logger.info(f"Đang reset tài khoản Paper Trading {account_id} về {args.capital:,.0f} VND...")
        res = repo.reset_paper_trading_account(
            user_id=account_id,
            initial_capital=args.capital,
            clean_agents_and_logs=True,
        )
        print(f"[RESET THÀNH CÔNG] Tài khoản {res['user_id']} đã được khôi phục về {res['cash_balance']:,.0f} VND sạch sẽ.")

    audit = ReplayAudit()
    quote_tool = get_intraday_tool()
    initial_quote_api_stats = (
        quote_tool.quote_api_requests,
        quote_tool.quote_api_pages,
        quote_tool.quote_api_rate_limits,
    )

    governance = AgentRegistry.get_agent("system_governance")
    governance.audit_trail = audit

    # Bỏ qua xuất bản sự kiện RabbitMQ khi chạy historical paper trading
    async def no_event(*_args, **_kwargs):
        return None

    BaseAgent.publish_event = no_event

    original_dispatch = AgentRegistry.dispatch

    @classmethod
    async def record_dispatch(cls, name, event):
        try:
            result = await original_dispatch(name, event)
        except Exception as exc:
            result = {"status": "RAISED", "error": str(exc)}
            audit.log_event(name, "AGENT_ERROR", {"event": event, "error": str(exc)})
            raise
        audit.log_event(name, "AGENT_RESULT", {"target_date": current_day, "event": event, "result": result})
        return result

    AgentRegistry.dispatch = record_dispatch
    runner = DailyInvestmentPipeline(multi_agent_mode="DISABLED", standalone_ml_mode="DISABLED")

    pending_orders: list[dict] = []
    returns_series: list[float] = []
    fills_history: list[dict] = []
    previous_nav = args.capital

    print(f"\n================================================================================")
    print(f"BẮT ĐẦU HISTORICAL PAPER TRADING: {len(days)} phiên ({days[0]} -> {days[-1]})")
    print(f"Scanner universe: {'explicit ' + str(len(args.symbols)) + '-symbol filter' if args.symbols else 'production HOSE stock master'}")
    print(f"Tài khoản PROD: {account_id} | Vốn: {args.capital:,.0f} VND (Pure PostgreSQL)")
    print(f"================================================================================\n")

    for day in days:
        current_day = day.isoformat()
        mark_as_of = previous_market_date(day)
        bars = bars_for_day(day)
        bar_cache: dict[tuple[date, str], list[dict]] = {}
        quote_cache: dict[tuple[date, str], list[dict]] = {}
        signal_time = datetime.combine(day, day_time(9, 45), ZoneInfo("Asia/Ho_Chi_Minh"))

        start_positions = repo.get_open_positions(
            user_id=account_id, as_of=mark_as_of,
            as_of_time=datetime.combine(day, day_time(9, 15), ZoneInfo("Asia/Ho_Chi_Minh")),
        )
        active_symbols = {position["ticker"].upper() for position in start_positions}
        peak_prices = {position["ticker"].upper(): float(position["average_price"]) for position in start_positions}
        morning_bar_events = intraday_bars_for_day(day, active_symbols, bar_cache)
        tracked = active_symbols | {order["symbol"] for order in pending_orders}
        quote_events = quote_events_for_day(day, tracked, quote_cache)
        morning_events = [
            (bar["time"], 0, "bar", bar) for bar in morning_bar_events if bar["time"] <= signal_time
        ] + [
            (timestamp, 1, "quote", symbol) for timestamp, symbol in quote_events if timestamp <= signal_time
        ]

        # Replay pending fills and position scans against the event timestamps actually returned by DNSE.
        event_key = lambda event: (event[0], event[1], event[2], event[3]["symbol"] if event[2] == "bar" else event[3])
        for timestamp, _priority, kind, payload in sorted(morning_events, key=event_key):
            if kind == "bar":
                await monitor_intraday_bar(
                    day, payload, repo, account_id, mark_as_of, pending_orders,
                    fills_history, peak_prices, active_symbols, quote_cache,
                )
                continue
            symbol = payload
            previous_fill_count = len(fills_history)
            pending_orders = fill_pending(
                day, bars, pending_orders, repo, account_id, mark_as_of, fills_history,
                until=timestamp + timedelta(microseconds=1), symbols={symbol}, quote_cache=quote_cache,
            )
            if len(fills_history) > previous_fill_count:
                positions = repo.get_open_positions(user_id=account_id, as_of=mark_as_of, as_of_time=timestamp)
                if any(position["ticker"].upper() == symbol for position in positions):
                    active_symbols.add(symbol)
                else:
                    active_symbols.discard(symbol)

        portfolio = get_portfolio_state(day, repo, account_id, mark_as_of, returns_series)
        for position in portfolio["positions"]:
            bar = bars.get(position["ticker"].upper())
            if bar:
                position["current_price"] = bar["price"]
                peak_prices[position["ticker"].upper()] = max(
                    peak_prices.get(position["ticker"].upper(), float(position["average_price"])), bar["price"]
                )
                active_symbols.add(position["ticker"].upper())

        result = await runner.run(
            target_date=day,
            current_nav=portfolio["total_nav"],
            candidate_tickers=[symbol.upper() for symbol in args.symbols] if args.symbols else None,
            max_candidates=args.max_candidates,
            replay_portfolio=portfolio,
            replay_account_id=account_id,
        )
        phases = result.get("trace", {}).get("phases", {})
        for phase_key, label in (
            ("phase_4_equity_research_gate", "Research gate"),
            ("phase_5_investment_thesis_gate", "Thesis gate"),
        ):
            summary = phases.get(phase_key)
            if summary:
                print(
                    f"[{day}] {label}: evaluated={summary.get('evaluated_count', 0)} "
                    f"statuses={summary.get('status_counts', {})} "
                    f"reason_counts (may overlap)={summary.get('reason_counts', {})}"
                )
        day_orders = result.get("multi_agent_instructions", [])
        for order in day_orders:
            approved_shares = int(order.get("approved_shares", 0))
            symbol = str(order.get("ticker", "")).upper()
            ref_price = bars.get(symbol, {}).get("price", 0.0)
            if approved_shares > 0 and ref_price > 0:
                enqueue_order(
                    pending_orders, symbol, "BUY", approved_shares,
                    order.get("entry_type", "STANDARD"), limit_price=ref_price,
                    not_before=signal_time, reason=order.get("rationale"),
                )

        tracked = active_symbols | {order["symbol"] for order in pending_orders}
        tracked |= {str(order.get("ticker", "")).upper() for order in day_orders if int(order.get("approved_shares", 0)) > 0}
        afternoon_bar_events = intraday_bars_for_day(day, tracked, bar_cache)
        quote_events = quote_events_for_day(day, tracked, quote_cache)
        afternoon_events = [
            (bar["time"], 0, "bar", bar) for bar in afternoon_bar_events if bar["time"] > signal_time
        ] + [
            (timestamp, 1, "quote", symbol) for timestamp, symbol in quote_events if timestamp > signal_time
        ]
        for timestamp, _priority, kind, payload in sorted(afternoon_events, key=event_key):
            if kind == "bar":
                await monitor_intraday_bar(
                    day, payload, repo, account_id, mark_as_of, pending_orders,
                    fills_history, peak_prices, active_symbols, quote_cache,
                )
                continue
            symbol = payload
            previous_fill_count = len(fills_history)
            pending_orders = fill_pending(
                day, bars, pending_orders, repo, account_id, mark_as_of, fills_history,
                until=timestamp + timedelta(microseconds=1), symbols={symbol}, quote_cache=quote_cache,
            )
            if len(fills_history) > previous_fill_count:
                positions = repo.get_open_positions(user_id=account_id, as_of=mark_as_of, as_of_time=timestamp)
                if any(position["ticker"].upper() == symbol for position in positions):
                    active_symbols.add(symbol)
                else:
                    active_symbols.discard(symbol)

        closing_mark = repo.record_replay_mark(user_id=account_id, mark_as_of=day)
        closing_positions = repo.get_open_positions(
            user_id=account_id, as_of=day,
            as_of_time=datetime.combine(day, day_time(15, 0), ZoneInfo("Asia/Ho_Chi_Minh")),
        )
        ret = (float(closing_mark["total_nav"]) / previous_nav - 1) if previous_nav > 0 else 0.0
        returns_series.append(ret)
        previous_nav = float(closing_mark["total_nav"])
        print(
            f"[{current_day}] close NAV: {previous_nav:>14,.0f} VND | "
            f"Cash: {closing_mark['cash_balance']:>14,.0f} VND | "
            f"Positions: {len(closing_positions):>2} | "
            f"Orders: {len(pending_orders):>2} pending | "
            f"Agents: {result.get('status', 'UNKNOWN')}",
            flush=True,
        )

    print(f"\n================================================================================")
    end_state = repo.get_account_state(user_id=account_id, as_of=days[-1])
    agent_calls = {}
    agent_errors = 0
    for event in audit.events:
        if event["event_type"] == "AGENT_RESULT":
            agent_calls[event["agent"]] = agent_calls.get(event["agent"], 0) + 1
        elif event["event_type"] == "AGENT_ERROR":
            agent_errors += 1
    fees = sum(float(fill["cost"]) for fill in fills_history)
    total_return_pct = (float(end_state["total_nav"]) / args.capital - 1) * 100 if args.capital else 0.0
    print(f"HOÀN TẤT HISTORICAL PAPER TRADING: {len(days)} phiên.")
    print(f"Final NAV: {float(end_state['total_nav']):,.0f} VND | Return: {total_return_pct:+.2f}% | Cash: {float(end_state['cash_balance']):,.0f} VND")
    print(f"Fills: {len(fills_history)} | Estimated two-way fees/tax: {fees:,.0f} VND | Pending: {len(pending_orders)}")
    print(f"Agent calls: {agent_calls} | Agent errors: {agent_errors}")
    print(
        "DNSE quote API: "
        f"requests={quote_tool.quote_api_requests - initial_quote_api_stats[0]} | "
        f"pages={quote_tool.quote_api_pages - initial_quote_api_stats[1]} | "
        f"rate_limits={quote_tool.quote_api_rate_limits - initial_quote_api_stats[2]}"
    )
    print(f"Để reset tài khoản về ban đầu, hãy chạy lệnh với cờ: --reset")
    print(f"================================================================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy Historical Paper Trading trực tiếp trên PostgreSQL (Zero SQLite)")
    parser.add_argument("--end", default=(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=None, help="Optional explicit ticker filter; default scans the production HOSE stock master")
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--capital", type=float, default=1_000_000_000)
    parser.add_argument("--account-id", default=os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794"))
    parser.add_argument("--reset", action="store_true", help="Reset tài khoản và dọn dẹp CSDL về ban đầu")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
