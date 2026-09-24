"""Historical Paper Trading & Replay Runner (Zero SQLite, Pure PostgreSQL).

Runs historical Agent decisions directly on PostgreSQL with the standard
portfolio account (MULTI_AGENT_ACCOUNT_ID), simulating 09:45 bar execution
and maintaining zero temporary SQLite files.
Includes --reset to restore the account and wipe agent states/logs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.backtest.cost_model import round_to_lot
from app.core.base_agent import BaseAgent
from app.core.registry import AgentRegistry
from app.domain.pipeline.daily_pipeline_orchestrator import DailyInvestmentPipeline
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.database.pg_pool import get_conn

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
            cur.execute("SELECT DISTINCT date FROM market_data_daily WHERE date <= %s ORDER BY date DESC LIMIT %s", (end, count))
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
        cur.execute("SELECT max(date) FROM market_data_daily WHERE date < %s", (current,))
        row = cur.fetchone()
        return row[0] if row and row[0] else current - timedelta(days=1)


def bars_for_day(day: date) -> dict[str, dict]:
    results = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT symbol, open
            FROM ohlcv_intraday_1m
            WHERE (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::date = %s
              AND (time AT TIME ZONE 'Asia/Ho_Chi_Minh')::time = '09:45:00'
        """, (day,))
        for sym, open_p in cur.fetchall():
            price = float(open_p) * 1000.0 if open_p and 0 < float(open_p) < 1000.0 else float(open_p or 0)
            if price > 0:
                results[str(sym).upper()] = {"price": price, "source": "DNSE_1M_OPEN"}

        cur.execute("SELECT ticker, close_adj FROM market_data_daily WHERE date = %s", (day,))
        for ticker, close_adj in cur.fetchall():
            sym = str(ticker).upper()
            if sym not in results and close_adj and float(close_adj) > 0:
                results[sym] = {"price": float(close_adj), "source": "EOD_CLOSE"}
    return results


def fill_pending(
    day: date,
    bars: dict,
    pending_orders: list[dict],
    repository: PortfolioRepository,
    account_id: str,
    mark_as_of: date,
    fills_log: list[dict] | None = None,
) -> list[dict]:
    remaining = []
    executed_at = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=45, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    for order in pending_orders:
        symbol, side = order["symbol"], order["side"]
        bar = bars.get(symbol)
        if not bar or bar["price"] <= 0:
            remaining.append(order)
            continue
        quantity = round_to_lot(int(order["quantity"]))
        if quantity <= 0:
            continue
        try:
            result = repository.record_replay_execution(
                symbol, side, quantity, float(bar["price"]), user_id=account_id,
                executed_at=executed_at, mark_as_of=mark_as_of,
            )
        except ValueError as exc:
            reason = str(exc)
            if "T+2.5" in reason or "locked" in reason:
                remaining.append(order)
                continue
            if "Insufficient" in reason:
                account = repository.get_account_state(user_id=account_id, as_of=mark_as_of)
                while quantity > 0:
                    gross = float(bar["price"]) * quantity
                    fee = max(gross * 0.001, 10_000.0)
                    if gross + fee <= float(account.get("cash_balance", 0)):
                        break
                    quantity -= 100
                if quantity <= 0:
                    continue
                result = repository.record_replay_execution(
                    symbol, side, quantity, float(bar["price"]), user_id=account_id,
                    executed_at=executed_at, mark_as_of=mark_as_of,
                )
            else:
                remaining.append(order)
                continue
        except Exception as exc:
            logger.warning(f"Lỗi fill pending order {symbol} {side}: {exc}")
            remaining.append(order)
            continue

        gross = float(result["executed_price"]) * int(result["shares"])
        cost = max(gross * 0.001, 10_000.0) + (gross * 0.001 if side == "SELL" else 0.0)
        if fills_log is not None:
            fills_log.append({
                "day": day.isoformat(),
                "symbol": symbol,
                "side": side,
                "quantity": result["shares"],
                "price": result["executed_price"],
                "cost": cost,
                "source": bar["source"],
            })
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


def enqueue_order(pending: list[dict], symbol: str, side: str, quantity: int) -> None:
    if quantity > 0 and not any(o["symbol"] == symbol and o["side"] == side for o in pending):
        pending.append({"symbol": symbol, "side": side, "quantity": quantity})


async def run(args: argparse.Namespace) -> None:
    account_id = args.account_id or os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794")
    repo = PortfolioRepository()

    if args.reset:
        logger.info(f"Đang reset tài khoản Paper Trading {account_id} về {args.capital:,.0f} VND...")
        res = repo.reset_paper_trading_account(
            user_id=account_id,
            initial_capital=args.capital,
            clean_agents_and_logs=True,
        )
        print(f"[RESET THÀNH CÔNG] Tài khoản {res['user_id']} đã được khôi phục về {res['cash_balance']:,.0f} VND sạch sẽ.")
        if not args.days:
            return

    end_date = date.fromisoformat(args.end)
    if end_date >= datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date():
        end_date = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() - timedelta(days=1)

    days = market_dates(end_date, args.days)
    if not days:
        print(f"Không có dữ liệu ngày giao dịch trước ngày {end_date}.")
        return

    audit = ReplayAudit()

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
    print(f"Tài khoản PROD: {account_id} | Vốn: {args.capital:,.0f} VND (Pure PostgreSQL)")
    print(f"================================================================================\n")

    for day in days:
        current_day = day.isoformat()
        mark_as_of = previous_market_date(day)
        bars = bars_for_day(day)

        # 1. Khớp lệnh chờ từ các phiên trước ở bar 09:45 hôm nay
        if pending_orders:
            pending_orders = fill_pending(day, bars, pending_orders, repo, account_id, mark_as_of, fills_history)

        # 2. Đọc trạng thái danh mục thực tế từ PostgreSQL
        portfolio = get_portfolio_state(day, repo, account_id, mark_as_of, returns_series)

        # 3. Kích hoạt giám sát vị thế & cắt lỗ tự động
        if portfolio["positions"]:
            monitored = await AgentRegistry.dispatch("position_monitoring", {
                "positions": portfolio["positions"],
                "nav": portfolio["total_nav"],
                "current_time": f"{day}T09:45:00+07:00",
                "target_date": current_day,
                "market_data_date": mark_as_of.isoformat(),
                "replay_account_id": account_id,
                "auto_dispatch": False,
            })
            for order in monitored.get("result", {}).get("data", {}).get("stop_loss_orders", []):
                if order.get("quantity", 0) > 0:
                    enqueue_order(pending_orders, order["ticker"], "SELL", int(order["quantity"]))

        # 4. Chạy chu trình quyết định đầu tư 12 Agents cho ngày hôm nay
        result = await runner.run(
            target_date=day,
            current_nav=portfolio["total_nav"],
            candidate_tickers=args.symbols,
            max_candidates=args.max_candidates,
            replay_portfolio=portfolio,
            replay_account_id=account_id,
        )

        # 5. Lấy các lệnh mua đã được duyệt qua Risk / Allocation để xếp hàng khớp phiên sau
        for order in result.get("multi_agent_instructions", []):
            approved_shares = int(order.get("approved_shares", 0))
            if approved_shares > 0:
                enqueue_order(pending_orders, order["ticker"], "BUY", approved_shares)

        ret = (portfolio["total_nav"] / previous_nav - 1) if previous_nav > 0 else 0.0
        returns_series.append(ret)
        previous_nav = portfolio["total_nav"]

        print(
            f"[{current_day}] NAV: {portfolio['total_nav']:>14,.0f} VND | "
            f"Tiền mặt: {portfolio['cash_vnd']:>14,.0f} VND | "
            f"Vị thế: {len(portfolio['positions']):>2} mã | "
            f"Lệnh chờ: {len(pending_orders):>2}",
            flush=True,
        )

    print(f"\n================================================================================")
    print(f"HOÀN TẤT HISTORICAL PAPER TRADING: {len(days)} phiên.")
    print(f"Tổng số lệnh khớp trên PostgreSQL: {len(fills_history)}")
    print(f"Để reset tài khoản về ban đầu, hãy chạy lệnh với cờ: --reset")
    print(f"================================================================================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chạy Historical Paper Trading trực tiếp trên PostgreSQL (Zero SQLite)")
    parser.add_argument("--end", default=(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", nargs="+", default=["FPT", "VCB", "SSI", "MBB", "MWG"])
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--capital", type=float, default=1_000_000_000)
    parser.add_argument("--account-id", default=os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794"))
    parser.add_argument("--reset", action="store_true", help="Reset tài khoản và dọn dẹp CSDL về ban đầu")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
