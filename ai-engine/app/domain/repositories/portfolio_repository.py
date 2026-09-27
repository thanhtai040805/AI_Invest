"""Portfolio Repository Layer (IOS v5.1)
Quản lý trạng thái thực tế của Người dùng / Tài khoản (bảng users & portfolio_account),
Danh mục vị thế duy nhất chuẩn hóa (bảng positions), 
và Lịch sử khớp lệnh (bảng orders & order_executions) kết nối PostgreSQL / TimescaleDB.
"""

from __future__ import annotations

import logging
import os
import uuid
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


def execution_amounts(shares: int, price: float, action: str) -> Tuple[float, float, float, float]:
    """Keep cash receipts independent of the rounded displayed average fill price."""
    gross = (Decimal(str(price)) * shares).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    fee = max(gross * Decimal("0.001"), Decimal("10000"))
    tax = gross * Decimal("0.001") if action in ("SELL", "SELL_MP") else Decimal("0")
    delta = -gross - fee if action == "BUY" else max(Decimal("0"), gross - fee - tax)
    delta = delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return tuple(float(value) for value in (gross, fee, tax, delta))


def calculate_is_t25_locked(opened_at: Any, now_dt: Optional[datetime] = None) -> bool:
    """Kiểm tra quy chế thanh toán T+2.5 của thị trường chứng khoán Việt Nam.
    - Không tính thứ 7 và Chủ Nhật.
    - T+0, T+1: cổ phiếu chưa về, bị khóa (is_locked = True).
    - T+2: chỉ khả dụng từ phiên chiều sau 11:30 trưa (trước 11:30 vẫn bị khóa).
    - T+3 trở đi: đã về tài khoản hoàn toàn (is_locked = False).
    """
    if not opened_at:
        return False
    try:
        if isinstance(opened_at, str):
            opened_dt = datetime.fromisoformat(opened_at)
        else:
            opened_dt = opened_at

        if now_dt is None:
            if hasattr(opened_dt, "tzinfo") and opened_dt.tzinfo is not None:
                now_dt = datetime.now(opened_dt.tzinfo)
            else:
                now_dt = datetime.now()
        else:
            if hasattr(opened_dt, "tzinfo") and opened_dt.tzinfo is not None and getattr(now_dt, "tzinfo", None) is None:
                now_dt = now_dt.replace(tzinfo=opened_dt.tzinfo)
            elif (not hasattr(opened_dt, "tzinfo") or opened_dt.tzinfo is None) and getattr(now_dt, "tzinfo", None) is not None:
                opened_dt = opened_dt.replace(tzinfo=now_dt.tzinfo)

        opened_date = opened_dt.date()
        current_date = now_dt.date()

        if current_date <= opened_date:
            return True

        b_days = 0
        cur = opened_date + timedelta(days=1)
        while cur <= current_date:
            if cur.weekday() < 5:  # Thứ 2 đến thứ 6
                b_days += 1
            cur += timedelta(days=1)

        if b_days < 2:
            return True
        elif b_days == 2:
            # Ngày T+2: VSDC trả chứng khoán vào 11:30 trưa để giao dịch phiên chiều (13:00)
            return now_dt.time() < time(11, 30)
        else:
            return False
    except Exception as e:
        logger.debug(f"[calculate_is_t25_locked] Lỗi tính toán T+2.5: {e}")
        return False


class PortfolioRepository:
    """
    Repository quản lý vốn, số dư tiền mặt và danh mục vị thế đồng bộ giữa ai-engine và back-end.
    Tương tác trực tiếp với các bảng CSDL lõi:
    - users: quản lý cash_balance, win_rate
    - positions: bảng vị thế cổ phiếu duy nhất của toàn hệ thống (hỗ trợ T+2.5 qua opened_at)
    - orders: quản lý sổ lệnh khớp
    - portfolio_account: quản lý NAV, peak NAV, drawdown_tier cấp Quỹ Quant
    """

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()
        self.account_id = os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794")
        # Bộ nhớ tạm in-memory fallback phòng khi chạy unit test độc lập
        self._in_memory_account: Dict[str, Any] = {
            "account_id": self.account_id,
            "cash_balance": 1000000000.0,
            "total_nav": 1000000000.0,
            "peak_nav": 1000000000.0,
            "drawdown_tier": "GREEN",
            "win_rate": 0.0,
        }
        self._in_memory_positions: Dict[str, Dict[str, Any]] = {}

        self._in_memory_campaigns: Dict[str, Dict[str, Any]] = {}
        self._in_memory_slippage_records: List[Dict[str, Any]] = []

    def get_account_state(self, user_id: Optional[str] = None, as_of: Optional[datetime | date] = None) -> Dict[str, Any]:
        """Lấy số dư tiền mặt từ bảng users và tính tổng NAV danh mục từ CSDL."""
        target_uid = user_id or self.account_id or self._in_memory_account.get("account_id")
        as_of_error = None
        try:
            # 1. Đọc số dư tiền mặt từ bảng users
            if target_uid:
                query_user = "SELECT id, cash_balance, win_rate FROM users WHERE id = %s"
                rows_user = self.storage.fetch_all(query_user, (target_uid,))
            else:
                rows_user = []

            if rows_user and len(rows_user) > 0:
                uid, cash, win_rate = rows_user[0]
                if as_of is not None and cash is None:
                    as_of_error = f"Missing replay cash balance for account {uid}"
                    raise LookupError(f"Missing replay cash balance for account {uid}")
                cash_val = float(cash) if cash is not None else 1000000000.0
                win_rate_val = float(win_rate) if win_rate is not None else 0.0

                # 2. Tính tổng giá trị danh mục vị thế từ bảng positions
                mark_date = as_of.date() if isinstance(as_of, datetime) else as_of
                query_pos = """
                    SELECT p.symbol, p.quantity,
                           md.close_unadj * 1000 AS current_price
                    FROM positions p
                    LEFT JOIN LATERAL (
                        SELECT close_unadj FROM market_data_daily
                        WHERE ticker = p.symbol AND (%s::date IS NULL OR date <= %s::date)
                        ORDER BY date DESC LIMIT 1
                    ) md ON TRUE
                    WHERE p.user_id = %s AND p.quantity > 0
                """
                rows_pos = self.storage.fetch_all(query_pos, (mark_date, mark_date, uid))
                if any(r[2] is None for r in rows_pos):
                    as_of_error = f"Missing market mark as of {mark_date} for replay account {uid}"
                    raise LookupError(f"Missing market mark as of {mark_date} for replay account {uid}")
                positions_val = sum(float(r[1]) * float(r[2]) for r in rows_pos) if rows_pos else 0.0
                total_nav = cash_val + positions_val

                account_rows = self.storage.fetch_all(
                    "SELECT peak_nav FROM portfolio_account WHERE account_id = %s", (str(uid),)
                )
                previous_peak = float(account_rows[0][0]) if account_rows else total_nav
                peak_nav = max(total_nav, previous_peak)
                drawdown_pct = ((peak_nav - total_nav) / peak_nav * 100.0) if peak_nav > 0 else 0.0
                drawdown_tier = "RED" if drawdown_pct >= 10 else ("ORANGE" if drawdown_pct >= 5 else ("YELLOW" if drawdown_pct >= 2 else "GREEN"))

                self._in_memory_account = {
                    "account_id": str(uid),
                    "cash_balance": cash_val,
                    "total_nav": total_nav,
                    "peak_nav": peak_nav,
                    "drawdown_tier": drawdown_tier,
                    "win_rate": win_rate_val,
                }
                return self._in_memory_account
        except Exception as e:
            logger.warning(f"Không thể đọc account_state từ DB ({e}), dùng in-memory fallback")

        if as_of is not None:
            raise LookupError(as_of_error or "As-of portfolio state unavailable")
        if user_id is not None:
            raise LookupError(f"Portfolio user not found: {user_id}")
        if os.getenv("ENVIRONMENT", "").lower() != "test":
            raise RuntimeError("Portfolio account state is unavailable")
        return self._in_memory_account

    def get_open_positions(
        self,
        user_id: Optional[str] = None,
        as_of: Optional[datetime | date] = None,
        as_of_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy toàn bộ các vị thế cổ phiếu đang nắm giữ kèm phân tách hàng khả dụng T+2.5."""
        target_uid = user_id or self.account_id or self._in_memory_account.get("account_id")
        as_of_error = None
        try:
            if target_uid:
                mark_date = as_of.date() if isinstance(as_of, datetime) else as_of
                query = """
                    SELECT p.symbol, p.quantity, p.avg_price, p.opened_at,
                           md.close_unadj * 1000 AS current_price
                    FROM positions p
                    LEFT JOIN LATERAL (
                        SELECT close_unadj FROM market_data_daily
                        WHERE ticker = p.symbol AND (%s::date IS NULL OR date <= %s::date)
                        ORDER BY date DESC LIMIT 1
                    ) md ON TRUE
                    WHERE p.user_id = %s AND p.quantity > 0
                    ORDER BY quantity DESC
                """
                rows = self.storage.fetch_all(query, (mark_date, mark_date, target_uid))
            else:
                query = """
                    SELECT symbol, quantity, avg_price, opened_at
                    FROM positions
                    WHERE quantity > 0
                    ORDER BY quantity DESC
                """
                rows = self.storage.fetch_all(query)

            if rows is not None:
                if any(len(r) < 5 or r[4] is None for r in rows):
                    as_of_error = f"Missing market mark as of {mark_date} for replay account {target_uid}"
                    raise LookupError(as_of_error)
                results = []
                # Tính tổng NAV để tính % tỷ trọng từng vị thế
                tot_pos_val = sum(int(r[1]) * float(r[4] if len(r) > 4 and r[4] is not None else r[2]) for r in rows)
                try:
                    cash_row = self.storage.fetch_all("SELECT cash_balance FROM users WHERE id = %s", (target_uid,)) if target_uid else None
                    if as_of is not None and (not cash_row or cash_row[0][0] is None):
                        as_of_error = f"Missing replay cash balance for account {target_uid}"
                        raise LookupError(as_of_error)
                    cash_val = float(cash_row[0][0]) if cash_row and cash_row[0][0] is not None else 0.0
                    tot_nav = cash_val + tot_pos_val
                except Exception:
                    if as_of is not None:
                        raise
                    tot_nav = tot_pos_val

                t_plus_2_time = as_of_time or (as_of if isinstance(as_of, datetime) else (
                    datetime.combine(as_of, time(9, 45), ZoneInfo("Asia/Ho_Chi_Minh")) if as_of else datetime.now()
                ))
                for r in rows:
                    total_shares = int(r[1])
                    avg_p = float(r[2])
                    current_p = float(r[4]) if len(r) > 4 and r[4] is not None else avg_p
                    opened_at = r[3] if len(r) > 3 and r[3] else None
                    
                    # Kiểm tra chu kỳ T+2.5 chuẩn ngày làm việc thị trường VN
                    is_locked = calculate_is_t25_locked(opened_at, t_plus_2_time)

                    available_shares = 0 if is_locked else total_shares
                    locked_shares = total_shares if is_locked else 0
                    mkt_val = total_shares * current_p
                    w_pct = round((mkt_val / tot_nav) * 100.0, 1) if tot_nav > 0 else 0.0

                    results.append({
                        "ticker": str(r[0]),
                        "symbol": str(r[0]),
                        "shares": total_shares,
                        "quantity": total_shares,
                        "available_shares": available_shares,
                        "locked_t25_shares": locked_shares,
                        "average_price": avg_p,
                        "opened_at": opened_at,
                        "avg_price": avg_p,
                        "current_price": current_p,
                        "market_value": mkt_val,
                        "weight_pct": w_pct,
                    })
                return results
        except Exception as e:
            logger.warning(f"Không thể đọc positions từ DB ({e}), dùng in-memory fallback")

        if as_of is not None:
            raise LookupError(as_of_error or "As-of positions unavailable")
        if user_id is not None:
            raise LookupError(f"Portfolio positions unavailable for user: {user_id}")
        if os.getenv("ENVIRONMENT", "").lower() != "test":
            raise RuntimeError("Portfolio positions are unavailable")
        # In-memory positions fallback: đảm bảo có available_shares và locked_t25_shares
        in_mem_list = []
        for p in self._in_memory_positions.values():
            total = int(p.get("shares", 0))
            avail = int(p.get("available_shares", total))
            locked = int(p.get("locked_t25_shares", max(0, total - avail)))
            pos_copy = dict(p)
            pos_copy["shares"] = total
            pos_copy["available_shares"] = avail
            pos_copy["locked_t25_shares"] = locked
            in_mem_list.append(pos_copy)
        return in_mem_list

    def get_active_campaign(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Lấy chiến dịch gom/xả đa phiên đang hoạt động của một mã cổ phiếu."""
        ticker_clean = str(ticker).upper().strip()
        try:
            query = """
                SELECT campaign_id, ticker, direction, final_target_weight, current_weight,
                       session_incremental_weight, remaining_weight, target_shares, accumulated_shares,
                       status, created_at, updated_at
                FROM portfolio_campaigns
                WHERE ticker = %s AND status = 'IN_PROGRESS'
                ORDER BY created_at DESC LIMIT 1
            """
            rows = self.storage.fetch_all(query, (ticker_clean,))
            if rows and len(rows) > 0:
                r = rows[0]
                return {
                    "campaign_id": str(r[0]),
                    "ticker": str(r[1]),
                    "direction": str(r[2]),
                    "final_target_weight": float(r[3]),
                    "current_weight": float(r[4]),
                    "session_incremental_weight": float(r[5]),
                    "remaining_weight": float(r[6]),
                    "target_shares": int(r[7]),
                    "accumulated_shares": int(r[8]),
                    "status": str(r[9]),
                    "created_at": str(r[10]),
                    "updated_at": str(r[11]),
                }
        except Exception as e:
            logger.debug(f"Không thể đọc campaign từ DB ({e}), kiểm tra in-memory")

        return self._in_memory_campaigns.get(ticker_clean)

    def upsert_campaign(self, campaign_data: Dict[str, Any]) -> Dict[str, Any]:
        """Tạo mới hoặc cập nhật chiến dịch gom/xả đa phiên."""
        ticker = str(campaign_data["ticker"]).upper().strip()
        cid = campaign_data.get("campaign_id") or str(uuid.uuid4())
        now = datetime.now()
        record = {
            "campaign_id": cid,
            "ticker": ticker,
            "direction": campaign_data.get("direction", "ACCUMULATION"),
            "final_target_weight": float(campaign_data.get("final_target_weight", 0.0)),
            "current_weight": float(campaign_data.get("current_weight", 0.0)),
            "session_incremental_weight": float(campaign_data.get("session_incremental_weight", 0.0)),
            "remaining_weight": float(campaign_data.get("remaining_weight", 0.0)),
            "target_shares": int(campaign_data.get("target_shares", 0)),
            "accumulated_shares": int(campaign_data.get("accumulated_shares", 0)),
            "status": campaign_data.get("status", "IN_PROGRESS"),
            "created_at": campaign_data.get("created_at", now.isoformat()),
            "updated_at": now.isoformat(),
        }
        self._in_memory_campaigns[ticker] = record

        try:
            query = """
                INSERT INTO portfolio_campaigns (
                    campaign_id, ticker, direction, final_target_weight, current_weight,
                    session_incremental_weight, remaining_weight, target_shares, accumulated_shares,
                    status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (campaign_id) DO UPDATE SET
                    current_weight = EXCLUDED.current_weight,
                    session_incremental_weight = EXCLUDED.session_incremental_weight,
                    remaining_weight = EXCLUDED.remaining_weight,
                    accumulated_shares = EXCLUDED.accumulated_shares,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
            """
            self.storage.execute(
                query,
                (
                    cid, ticker, record["direction"], record["final_target_weight"],
                    record["current_weight"], record["session_incremental_weight"],
                    record["remaining_weight"], record["target_shares"],
                    record["accumulated_shares"], record["status"], now, now
                )
            )
        except Exception as e:
            logger.debug(f"Không thể sync campaign vào DB ({e}), đã lưu in-memory")

        return record

    def complete_campaign(self, campaign_id: str, ticker: Optional[str] = None):
        """Đánh dấu hoàn thành một chiến dịch."""
        if ticker:
            ticker_clean = str(ticker).upper().strip()
            if ticker_clean in self._in_memory_campaigns:
                self._in_memory_campaigns[ticker_clean]["status"] = "COMPLETED"
        for t, c in self._in_memory_campaigns.items():
            if c.get("campaign_id") == campaign_id:
                c["status"] = "COMPLETED"

        try:
            query = "UPDATE portfolio_campaigns SET status = 'COMPLETED', updated_at = %s WHERE campaign_id = %s"
            self.storage.execute(query, (datetime.now(), campaign_id))
        except Exception as e:
            logger.debug(f"Lỗi update campaign status ({e})")


    def record_replay_execution(
        self,
        ticker: str,
        action: str,
        shares: int,
        executed_price: float,
        *,
        user_id: str,
        executed_at: datetime,
        mark_as_of: date,
        pending_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically persist a simulated fill to the explicitly allowlisted replay DB."""
        ticker = ticker.upper().strip()
        action = action.upper().strip()
        if not ticker or shares <= 0 or executed_price <= 0 or action not in ("BUY", "SELL"):
            raise ValueError("Invalid replay execution")
        if not user_id or not isinstance(executed_at, datetime) or not isinstance(mark_as_of, date) or isinstance(mark_as_of, datetime):
            raise ValueError("Replay user, execution timestamp, and mark date are required")
        vietnam_tz = ZoneInfo("Asia/Ho_Chi_Minh")
        execution_local = executed_at.replace(tzinfo=vietnam_tz) if executed_at.tzinfo is None else executed_at.astimezone(vietnam_tz)
        execution_db_timestamp = execution_local.replace(tzinfo=None)
        if mark_as_of >= datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date():
            raise ValueError("PostgreSQL replay marks must be strictly historical")
        if mark_as_of >= execution_local.date():
            raise ValueError("Daily replay mark must be strictly before the fill date")

        trade_value, brokerage_fee, tax, cash_delta = execution_amounts(shares, executed_price, action)
        order_id = str(pending_order_id or uuid.uuid4())
        position_id = str(uuid.uuid4())
        account_before = deepcopy(self._in_memory_account)

        try:
            self.storage.begin()
            if pending_order_id:
                pending = self.storage.fetch_all(
                    "SELECT user_id, symbol, side, quantity, status, price FROM orders WHERE id = %s FOR UPDATE",
                    (pending_order_id,),
                )
                if not pending or str(pending[0][4]) != "PENDING_REPLAY":
                    raise ValueError("Replay order is no longer pending")
                uid, symbol, side, quantity, _, limit_price = pending[0]
                if str(uid) != user_id or str(symbol).upper() != ticker or side != action or int(quantity) != shares:
                    raise ValueError("Replay order details mismatch")
                if (action == "BUY" and executed_price > float(limit_price)) or (action == "SELL" and executed_price < float(limit_price)):
                    raise ValueError("Replay fill exceeds limit price")
            users = self.storage.fetch_all(
                "SELECT cash_balance FROM users WHERE id = %s FOR UPDATE", (user_id,)
            )
            if not users or users[0][0] is None:
                raise LookupError(f"Replay account cash balance unavailable: {user_id}")
            cash_before = float(users[0][0])
            if action == "BUY" and cash_before < -cash_delta:
                raise ValueError("Insufficient replay cash")

            existing = self.storage.fetch_all(
                "SELECT id, quantity, avg_price, opened_at FROM positions WHERE user_id = %s AND symbol = %s FOR UPDATE",
                (user_id, ticker),
            )
            if action == "SELL":
                if not existing or int(existing[0][1]) < shares:
                    raise ValueError("Insufficient replay shares")
                if calculate_is_t25_locked(existing[0][3], execution_local):
                    raise ValueError("Replay shares are locked by T+2.5 settlement")

            self.storage.execute("UPDATE users SET cash_balance = cash_balance + %s WHERE id = %s", (cash_delta, user_id))
            if action == "BUY":
                if existing:
                    row_id, old_qty, old_avg, _opened_at = existing[0]
                    new_qty = int(old_qty) + shares
                    avg_price = (float(old_avg) * int(old_qty) + executed_price * shares) / new_qty
                    self.storage.execute(
                        "UPDATE positions SET quantity = %s, avg_price = %s, opened_at = %s WHERE id = %s",
                        (new_qty, avg_price, execution_db_timestamp, row_id),
                    )
                else:
                    self.storage.execute(
                        "INSERT INTO positions (id, user_id, symbol, quantity, avg_price, opened_at) VALUES (%s, %s, %s, %s, %s, %s)",
                        (position_id, user_id, ticker, shares, executed_price, execution_db_timestamp),
                    )
            else:
                row_id, old_qty = existing[0][0], int(existing[0][1])
                remaining = old_qty - shares
                if remaining:
                    self.storage.execute("UPDATE positions SET quantity = %s WHERE id = %s", (remaining, row_id))
                else:
                    self.storage.execute("DELETE FROM positions WHERE id = %s", (row_id,))

            if pending_order_id:
                self.storage.execute("UPDATE orders SET status = 'FILLED_REPLAY', price = %s WHERE id = %s", (executed_price, order_id))
            else:
                self.storage.execute(
                    "INSERT INTO orders (id, user_id, symbol, side, order_type, price, quantity, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (order_id, user_id, ticker, action, "REPLAY_MARKET", executed_price, shares, "FILLED_REPLAY", execution_db_timestamp),
                )
            self.storage.execute(
                "INSERT INTO order_executions (order_id, ticker, action, shares, executed_price, target_price, slippage_bps, execution_mode, executed_at, gross_value, brokerage_fee, transfer_tax, cash_delta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (order_id, ticker, action, shares, executed_price, executed_price, 0.0, "POSTGRES_REPLAY", execution_local, trade_value, brokerage_fee, tax, cash_delta),
            )

            if pending_order_id and action == "BUY":
                self.storage.execute(
                    "INSERT INTO paper_trades (ticker, action, price, date, confidence, thesis, status, quantity, created_at, account_id) VALUES (%s, %s, %s, %s, 0.8, 'ML_REPLAY', 'OPEN', %s, CURRENT_TIMESTAMP, %s)",
                    (ticker, action, executed_price, execution_local, shares, user_id),
                )
            if pending_order_id and action == "SELL":
                trades = self.storage.fetch_all(
                    "SELECT id, price, quantity FROM paper_trades WHERE ticker = %s AND account_id = %s AND status = 'OPEN' ORDER BY date, id FOR UPDATE", (ticker, user_id)
                )
                left = shares
                for trade_id, entry_price, quantity in trades:
                    if left <= 0:
                        break
                    used = min(left, int(quantity))
                    pnl = (executed_price / float(entry_price) - 1) * 100
                    if used == int(quantity):
                        self.storage.execute("UPDATE paper_trades SET status='CLOSED', resolve_price=%s, pnl=%s, resolved_at=%s WHERE id=%s", (executed_price, pnl, execution_local, trade_id))
                    else:
                        self.storage.execute("UPDATE paper_trades SET quantity=quantity-%s WHERE id=%s", (used, trade_id))
                        self.storage.execute("INSERT INTO paper_trades (ticker,action,price,date,confidence,thesis,status,quantity,resolve_price,pnl,resolved_at,created_at,account_id) VALUES (%s,'BUY',%s,%s,0.8,'ML_REPLAY_PARTIAL','CLOSED',%s,%s,%s,%s,CURRENT_TIMESTAMP,%s)", (ticker, entry_price, execution_local, used, executed_price, pnl, execution_local, user_id))
                    left -= used
            account = self.get_account_state(user_id=user_id, as_of=mark_as_of)
            self.storage.execute(
                "INSERT INTO portfolio_account (account_id, cash_balance, total_nav, peak_nav, drawdown_tier, updated_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (account_id) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, total_nav = EXCLUDED.total_nav, peak_nav = EXCLUDED.peak_nav, drawdown_tier = EXCLUDED.drawdown_tier, updated_at = EXCLUDED.updated_at",
                (user_id, account["cash_balance"], account["total_nav"], account["peak_nav"], account["drawdown_tier"], execution_local),
            )
            self.storage.commit()
            return {
                "order_id": order_id,
                "ticker": ticker,
                "action": action,
                "shares": shares,
                "executed_price": executed_price,
                "cash_balance": account["cash_balance"],
                "total_nav": account["total_nav"],
                "status": "FILLED_REPLAY",
                "executed_at": execution_local.isoformat(),
                "mark_as_of": mark_as_of.isoformat(),
                "mark_type": "LAST_DAILY_CLOSE_ON_OR_BEFORE_AS_OF",
            }
        except Exception:
            self.storage.rollback()
            self._in_memory_account = account_before
            raise

    def record_replay_mark(self, *, user_id: str, mark_as_of: date) -> Dict[str, Any]:
        """Persist one replay day's close-marked account state."""
        if not user_id or not isinstance(mark_as_of, date) or isinstance(mark_as_of, datetime):
            raise ValueError("Replay user and close-mark date are required")
        now_vn = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        if mark_as_of > now_vn.date() or (mark_as_of == now_vn.date() and now_vn.time() < time(15, 0)):
            raise ValueError("Replay close marks must not be future-dated or during the current session")

        account_before = deepcopy(self._in_memory_account)
        try:
            self.storage.begin()
            account = self.get_account_state(user_id=user_id, as_of=mark_as_of)
            marked_at = datetime.combine(mark_as_of, time(15, 0), tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
            self.storage.execute(
                "INSERT INTO portfolio_account (account_id, cash_balance, total_nav, peak_nav, drawdown_tier, updated_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (account_id) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, total_nav = EXCLUDED.total_nav, peak_nav = EXCLUDED.peak_nav, drawdown_tier = EXCLUDED.drawdown_tier, updated_at = EXCLUDED.updated_at",
                (user_id, account["cash_balance"], account["total_nav"], account["peak_nav"], account["drawdown_tier"], marked_at),
            )
            self.storage.execute(
                "INSERT INTO portfolio_nav_history (account_id, date, total_nav, cash_balance) VALUES (%s, %s, %s, %s) ON CONFLICT (account_id, date) DO UPDATE SET total_nav = EXCLUDED.total_nav, cash_balance = EXCLUDED.cash_balance",
                (user_id, mark_as_of, account["total_nav"], account["cash_balance"]),
            )
            self.storage.commit()
            return {**account, "mark_as_of": mark_as_of.isoformat(), "mark_type": "LAST_DAILY_CLOSE_ON_OR_BEFORE_AS_OF"}
        except Exception:
            self.storage.rollback()
            self._in_memory_account = account_before
            raise

    def reset_paper_trading_account(
        self,
        user_id: Optional[str] = None,
        initial_capital: float = 1_000_000_000.0,
        clean_agents_and_logs: bool = False,
    ) -> Dict[str, Any]:
        """Khôi phục tài khoản Paper Trading về trạng thái ban đầu sạch sẽ để sẵn sàng lên PROD.
        Xóa toàn bộ:
        - order_executions
        - orders
        - positions
        - Reset cash_balance và total_nav về initial_capital (1 tỷ)
        - Tùy chọn (clean_agents_and_logs=True): Xóa toàn bộ kết quả quyết định và 12 bảng Logs của Agents.
        """
        target_uid = user_id or self.account_id
        try:
            self.storage.begin()
            # 1. Xóa executions của user
            self.storage.execute(
                "DELETE FROM order_executions e USING orders o WHERE e.order_id::text = o.id AND o.user_id = %s",
                (target_uid,),
            )
            # 2. Xóa orders
            self.storage.execute("DELETE FROM orders WHERE user_id = %s", (target_uid,))
            # 3. Xóa positions
            self.storage.execute("DELETE FROM positions WHERE user_id = %s", (target_uid,))
            # 4. Khôi phục số dư users
            self.storage.execute("UPDATE users SET cash_balance = %s WHERE id = %s", (initial_capital, target_uid))
            # 5. Khôi phục portfolio_account
            self.storage.execute(
                "INSERT INTO portfolio_account (account_id, cash_balance, total_nav, peak_nav, drawdown_tier, updated_at) "
                "VALUES (%s, %s, %s, %s, 'GREEN', NOW()) "
                "ON CONFLICT (account_id) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, "
                "total_nav = EXCLUDED.total_nav, peak_nav = EXCLUDED.peak_nav, drawdown_tier = 'GREEN', updated_at = NOW()",
                (target_uid, initial_capital, initial_capital, initial_capital),
            )

            # 6. Dọn dẹp kết quả quyết định và Logs của 12 Agents (khi được yêu cầu)
            if clean_agents_and_logs:
                clean_sql = """
                DO $$ 
                DECLARE 
                    tbl text;
                    tables text[] := ARRAY[
                        'portfolio_decisions', 'investment_theses', 'counter_thesis_verdicts',
                        'cio_resolutions', 'cio_strategic_directives', 'strategic_allocations',
                        'stop_loss_events', 'position_health_ticks', 'risk_snapshots',
                        'slippage_records', 'audit_reports', 'violation_reports',
                        'log_market_surveillance', 'log_universe_discovery', 'log_equity_research',
                        'log_investment_thesis', 'log_counter_thesis', 'log_strategy_cio',
                        'log_portfolio_allocation', 'log_portfolio_risk', 'log_trade_execution',
                        'log_position_monitoring', 'log_reinforcement_learning', 'log_system_governance'
                    ];
                BEGIN 
                    FOREACH tbl IN ARRAY tables LOOP
                        IF to_regclass(tbl) IS NOT NULL THEN
                            EXECUTE 'TRUNCATE TABLE ' || quote_ident(tbl) || ' CASCADE';
                        END IF;
                    END LOOP;
                END $$;
                """
                self.storage.execute(clean_sql)

            self.storage.commit()

            # Reset in-memory
            self._in_memory_account = {
                "account_id": target_uid,
                "cash_balance": initial_capital,
                "total_nav": initial_capital,
                "peak_nav": initial_capital,
                "drawdown_tier": "GREEN",
                "win_rate": 0.0,
            }
            self._in_memory_positions.clear()
            logger.info(f"[PortfolioRepository] Đã reset tài khoản {target_uid} về {initial_capital:,.0f} VND sạch sẽ.")
            return {"user_id": target_uid, "cash_balance": initial_capital, "status": "RESET_SUCCESS"}
        except Exception as e:
            self.storage.rollback()
            logger.error(f"[PortfolioRepository] Lỗi reset paper trading account {target_uid}: {e}")
            raise

    def execute_order_transaction(
        self,
        ticker: str,
        action: str,
        shares: int,
        executed_price: float,
        target_price: float = 0.0,
        slippage_bps: float = 0.0,
        execution_mode: str = "NORMAL",
        user_id: Optional[str] = None,
        status: str = "FILLED",
        pending_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thực hiện giao dịch nguyên tử (Atomic Execution):
        1. Trừ/Cộng tiền mặt trong bảng users (cash_balance)
        2. Thêm/Cộng dồn/Bớt cổ phiếu trong bảng positions
        3. Ghi hóa đơn khớp lệnh vào bảng orders và order_executions
        """
        ticker = ticker.upper().strip()
        action = action.upper().strip()
        if not ticker or shares <= 0 or executed_price <= 0 or action not in ("BUY", "SELL", "SELL_MP"):
            raise ValueError("Invalid order transaction")
        target_uid = user_id or self._in_memory_account.get("account_id") or os.getenv("DEFAULT_PORTFOLIO_USER_ID")
        if not target_uid:
            raise RuntimeError("Portfolio user_id is required")

        trade_value, brokerage_fee, tax, cash_delta = execution_amounts(shares, executed_price, action)
        order_id = str(pending_order_id or uuid.uuid4())
        pos_id = str(uuid.uuid4())
        now = datetime.now()

        account_before = deepcopy(self._in_memory_account)
        positions_before = deepcopy(self._in_memory_positions)

        # 1. Cập nhật In-Memory Cache; rollback nếu DB không commit được.
        # Tính toán phí môi giới (0.10%) và thuế chuyển nhượng (0.10% khi bán)

        if action == "BUY":
            total_deduct = -cash_delta
            self._in_memory_account["cash_balance"] -= total_deduct
            if ticker in self._in_memory_positions:
                pos = self._in_memory_positions[ticker]
                old_shares = pos["shares"]
                old_avg = pos["average_price"]
                new_shares = old_shares + shares
                new_avg = ((old_avg * old_shares) + (executed_price * shares)) / new_shares
                pos["shares"] = new_shares
                pos["average_price"] = new_avg
                pos["current_price"] = executed_price
                pos["market_value"] = new_shares * executed_price
            else:
                self._in_memory_positions[ticker] = {
                    "ticker": ticker,
                    "shares": shares,
                    "average_price": executed_price,
                    "current_price": executed_price,
                    "market_value": trade_value,
                    "weight_pct": (trade_value / self._in_memory_account["total_nav"]) * 100.0,
                }
        elif action in ("SELL", "SELL_MP"):
            net_credit = cash_delta
            self._in_memory_account["cash_balance"] += net_credit
            if ticker in self._in_memory_positions:
                pos = self._in_memory_positions[ticker]
                pos["shares"] = max(0, pos["shares"] - shares)
                pos["market_value"] = pos["shares"] * executed_price
                if pos["shares"] == 0:
                    del self._in_memory_positions[ticker]

        # 2. Cập nhật CSDL PostgreSQL thực tế
        try:
            self.storage.begin()
            if pending_order_id:
                pending = self.storage.fetch_all(
                    "SELECT user_id, symbol, side, quantity, status FROM orders WHERE id = %s FOR UPDATE",
                    (pending_order_id,),
                )
                if not pending or str(pending[0][4]) != "PENDING_SHADOW":
                    raise ValueError("Shadow order is no longer pending")
                if (str(pending[0][0]) != str(target_uid) or str(pending[0][1]).upper() != ticker
                        or str(pending[0][2]).upper() != action or int(pending[0][3]) != shares):
                    raise ValueError("Shadow order details do not match the pending order")
            users = self.storage.fetch_all(
                "SELECT cash_balance FROM users WHERE id = %s FOR UPDATE",
                (target_uid,),
            )
            if not users:
                raise RuntimeError(f"Portfolio user not found: {target_uid}")

            # 2.1 Cập nhật số dư tiền mặt trong bảng users kèm phí & thuế
            if action == "BUY":
                if float(users[0][0]) < total_deduct:
                    raise ValueError("Insufficient buying power")
                self.storage.execute(
                    "UPDATE users SET cash_balance = cash_balance - %s WHERE id = %s",
                    (total_deduct, target_uid),
                )
            elif action in ("SELL", "SELL_MP"):
                sql_user = "UPDATE users SET cash_balance = cash_balance + %s WHERE id = %s"
                self.storage.execute(sql_user, (net_credit, target_uid))

            # 2.2 Cập nhật vị thế trong bảng positions
            if action == "BUY":
                # Kiểm tra vị thế đã tồn tại chưa
                sql_check = "SELECT id, quantity, avg_price FROM positions WHERE user_id = %s AND symbol = %s FOR UPDATE"
                existing = self.storage.fetch_all(sql_check, (target_uid, ticker))
                if existing:
                    pid, old_q, old_avg = existing[0]
                    new_q = int(old_q) + shares
                    new_avg = ((float(old_avg) * int(old_q)) + (executed_price * shares)) / new_q
                    sql_update_pos = "UPDATE positions SET quantity = %s, avg_price = %s, opened_at = %s WHERE id = %s"
                    self.storage.execute(sql_update_pos, (new_q, new_avg, now, pid))
                else:
                    sql_insert_pos = """
                        INSERT INTO positions (id, user_id, symbol, quantity, avg_price, opened_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    self.storage.execute(sql_insert_pos, (pos_id, target_uid, ticker, shares, executed_price, now))
            elif action in ("SELL", "SELL_MP"):
                sql_check = "SELECT id, quantity, opened_at FROM positions WHERE user_id = %s AND symbol = %s FOR UPDATE"
                existing = self.storage.fetch_all(sql_check, (target_uid, ticker))
                if not existing or int(existing[0][1]) < shares:
                    raise ValueError("Insufficient shares to sell")
                if calculate_is_t25_locked(existing[0][2], now):
                    raise ValueError("Shares are locked by T+2.5 settlement")
                if existing:
                    pid, old_q, _opened_at = existing[0]
                    remaining_q = int(old_q) - shares
                    if remaining_q == 0:
                        self.storage.execute("DELETE FROM positions WHERE id = %s", (pid,))
                        # Hủy hoặc hoàn tất mọi chiến dịch gom/xả đang chạy cho ticker này để tránh zombie campaigns
                        try:
                            self.storage.execute(
                                "UPDATE portfolio_campaigns SET status = 'CANCELLED', updated_at = %s WHERE ticker = %s AND status = 'IN_PROGRESS'",
                                (now, ticker),
                            )
                            if ticker in self._in_memory_campaigns:
                                self._in_memory_campaigns[ticker]["status"] = "CANCELLED"
                        except Exception:
                            pass
                    else:
                        self.storage.execute("UPDATE positions SET quantity = %s WHERE id = %s", (remaining_q, pid))

            # 2.3 Ghi lệnh vào bảng orders (Sổ lệnh hệ thống)
            if pending_order_id:
                self.storage.execute(
                    "UPDATE orders SET status = %s, price = %s WHERE id = %s AND status = 'PENDING_SHADOW'",
                    (status, executed_price, pending_order_id),
                )
            else:
                self.storage.execute(
                    "INSERT INTO orders (id, user_id, symbol, side, order_type, price, quantity, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (order_id, target_uid, ticker, action, execution_mode, executed_price, shares, status, now),
                )

            # 2.4 Đồng thời ghi vào order_executions, portfolio_positions & portfolio_account để đồng bộ các Agent
            try:
                sql_order_exec = """
                    INSERT INTO order_executions (
                        order_id, ticker, action, shares, executed_price,
                        target_price, slippage_bps, execution_mode, executed_at,
                        gross_value, brokerage_fee, transfer_tax, cash_delta
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """
                self.storage.execute(
                    sql_order_exec,
                    (order_id, ticker, action, shares, executed_price, target_price, slippage_bps, execution_mode, now, trade_value, brokerage_fee, tax, cash_delta)
                )

                # Đồng bộ bảng portfolio_account
                acc_state = self.get_account_state(user_id=target_uid)
                sql_account = """
                    INSERT INTO portfolio_account (account_id, cash_balance, total_nav, peak_nav, drawdown_tier, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (account_id) DO UPDATE SET
                        cash_balance = EXCLUDED.cash_balance,
                        total_nav = EXCLUDED.total_nav,
                        peak_nav = EXCLUDED.peak_nav,
                        updated_at = EXCLUDED.updated_at
                """
                self.storage.execute(
                    sql_account,
                    (str(target_uid), acc_state["cash_balance"], acc_state["total_nav"], acc_state["peak_nav"], acc_state.get("drawdown_tier", "GREEN"), now)
                )

                # Đồng bộ bảng paper_trades phục vụ học tăng cường (Agent-10)
                try:
                    if action == "BUY":
                        sql_paper = """
                            INSERT INTO paper_trades (ticker, action, price, date, confidence, thesis, status, quantity, created_at, account_id)
                            VALUES (%s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)
                        """
                        self.storage.execute(
                            sql_paper,
                            (ticker, action, executed_price, now, 0.8, "EXECUTION_AGENT_ORDER", shares, now, str(target_uid))
                        )
                    elif action in ("SELL", "SELL_MP"):
                        # FIFO Tranche resolution trong paper_trades
                        sql_get_open = """
                            SELECT id, price, quantity FROM paper_trades
                            WHERE ticker = %s AND account_id = %s AND status = 'OPEN'
                            ORDER BY date ASC, id ASC
                        """
                        open_trades = self.storage.fetch_all(sql_get_open, (ticker, str(target_uid)))
                        rem_sell = shares
                        for trade in open_trades:
                            if rem_sell <= 0:
                                break
                            t_id, t_price, t_qty = trade[0], float(trade[1]), int(trade[2] or 0)
                            if t_qty <= rem_sell:
                                pnl = round(((executed_price - t_price) / t_price * 100), 2) if t_price > 0 else 0.0
                                self.storage.execute("""
                                    UPDATE paper_trades
                                    SET status = 'CLOSED',
                                        resolve_price = %s,
                                        pnl = %s,
                                        resolved_at = %s
                                    WHERE id = %s
                                """, (executed_price, pnl, now, t_id))
                                rem_sell -= t_qty
                            else:
                                pnl = round(((executed_price - t_price) / t_price * 100), 2) if t_price > 0 else 0.0
                                self.storage.execute("""
                                    UPDATE paper_trades
                                    SET quantity = quantity - %s
                                    WHERE id = %s
                                """, (rem_sell, t_id))
                                self.storage.execute("""
                                    INSERT INTO paper_trades (ticker, action, price, date, confidence, thesis, status, quantity, resolve_price, pnl, resolved_at, created_at, account_id)
                                    VALUES (%s, 'BUY', %s, %s, 0.8, 'PARTIAL_FILL_CLOSE', 'CLOSED', %s, %s, %s, %s, %s, %s)
                                """, (ticker, t_price, now, rem_sell, executed_price, pnl, now, now, str(target_uid)))
                                rem_sell = 0
                except Exception:
                    raise
            except Exception:
                raise

            self.storage.commit()
            logger.info(f"Đã cập nhật giao dịch {action} {shares} {ticker} (status: {status}) vào bảng users, positions và orders thành công.")
        except Exception as e:
            self.storage.rollback()
            self._in_memory_account = account_before
            self._in_memory_positions = positions_before
            logger.error(f"Lỗi khi sync giao dịch vào DB; đã rollback toàn bộ ({e}).")
            raise

        return {
            "order_id": order_id,
            "ticker": ticker,
            "action": action,
            "shares": shares,
            "executed_price": executed_price,
            "trade_value_vnd": trade_value,
            "remaining_cash": self._in_memory_account["cash_balance"],
            "status": status,
            "timestamp": now.isoformat(),
        }

    def create_shadow_pending_order(
        self, ticker: str, shares: int, limit_price: float, user_id: Optional[str] = None,
        order_type: str = "SHADOW_LIMIT", side: str = "BUY",
    ) -> str:
        """Persist a day-only Shadow limit order without changing cash or positions."""
        ticker = ticker.upper().strip()
        if (not ticker or shares <= 0 or limit_price <= 0
                or order_type not in ("SHADOW_LIMIT", "SHADOW_ML_LIMIT") or side not in ("BUY", "SELL")):
            raise ValueError("Invalid Shadow pending order")
        target_uid = user_id or self.account_id
        order_id = str(uuid.uuid4())
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)
        self.storage.execute(
            "INSERT INTO orders (id, user_id, symbol, side, order_type, price, quantity, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING_SHADOW', %s)",
            (order_id, target_uid, ticker.upper().strip(), side, order_type, limit_price, shares, now),
        )
        return order_id

    def get_pending_shadow_orders(self) -> List[Dict[str, Any]]:
        rows = self.storage.fetch_all(
            "SELECT id, user_id, symbol, side, price, quantity, order_type FROM orders "
            "WHERE status = 'PENDING_SHADOW' AND order_type IN ('SHADOW_LIMIT', 'SHADOW_ML_LIMIT') "
            "ORDER BY created_at"
        )
        return [
            {"order_id": str(r[0]), "user_id": str(r[1]), "ticker": str(r[2]),
             "side": str(r[3]), "limit_price": float(r[4]), "shares": int(r[5]),
             "order_type": str(r[6])}
            for r in rows
        ]

    def expire_pending_shadow_orders(self) -> int:
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        rows = self.storage.fetch_all(
            "UPDATE orders SET status = 'EXPIRED' WHERE status = 'PENDING_SHADOW' "
            "AND order_type IN ('SHADOW_LIMIT', 'SHADOW_ML_LIMIT') "
            "AND (created_at::date < %s OR %s >= TIME '14:45') RETURNING id",
            (now.date(), now.time().replace(tzinfo=None)),
        )
        return len(rows)

    def cancel_pending_shadow_order(self, order_id: str) -> None:
        self.storage.execute(
            "UPDATE orders SET status = 'CANCELLED' WHERE id = %s AND status = 'PENDING_SHADOW'",
            (order_id,),
        )

    def record_slippage(
        self,
        ticker: str,
        adtv20_bucket: str,
        actual_slippage_bps: float,
        expected_slippage_bps: float,
        mode: str,
        target_date: Optional[Any] = None,
    ) -> bool:
        """Ghi nhận hồ sơ trượt giá vào bảng slippage_records (PostgreSQL & In-memory fallback)."""
        ticker_clean = str(ticker).upper().strip()
        t_date = target_date or datetime.now().date()
        if isinstance(t_date, str):
            from datetime import date
            try:
                t_date = date.fromisoformat(t_date)
            except Exception:
                t_date = datetime.now().date()

        record = {
            "ticker": ticker_clean,
            "date": t_date.isoformat() if hasattr(t_date, "isoformat") else str(t_date),
            "adtv20_bucket": str(adtv20_bucket),
            "actual_slippage_bps": float(actual_slippage_bps),
            "expected_slippage_bps": float(expected_slippage_bps),
            "mode": str(mode),
        }
        self._in_memory_slippage_records.append(record)

        try:
            query = """
                INSERT INTO slippage_records (
                    ticker, date, adtv20_bucket, actual_slippage_bps, expected_slippage_bps, mode
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """
            self.storage.execute(
                query,
                (ticker_clean, t_date, str(adtv20_bucket), actual_slippage_bps, expected_slippage_bps, str(mode))
            )
            logger.info(f"Đã ghi nhận slippage record cho {ticker_clean}: {actual_slippage_bps} bps (bucket: {adtv20_bucket})")
            return True
        except Exception as e:
            logger.debug(f"Không thể sync slippage_records vào DB ({e}), đã lưu in-memory.")
            return False

    def save_decision(self, decision: Dict[str, Any]) -> bool:
        """Lưu quyết định phân bổ vốn vào bảng portfolio_decisions."""
        try:
            from zoneinfo import ZoneInfo
            from datetime import time as day_time

            query = """
                INSERT INTO portfolio_decisions (
                    decision_id, date, ticker, action, target_shares, allocated_weight_pct, rationale, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
            """

            is_replay = bool(decision.get("is_replay", False))
            target_date_raw = decision.get("target_date") or decision.get("date") or decision.get("analysis_date")
            if isinstance(target_date_raw, datetime):
                target_date = target_date_raw.date()
            elif isinstance(target_date_raw, str):
                try:
                    from datetime import date as _date
                    target_date = _date.fromisoformat(target_date_raw.split("T")[0])
                except Exception:
                    if is_replay:
                        raise ValueError("Replay decision requires a valid target_date")
                    target_date = datetime.now().date()
            elif isinstance(target_date_raw, date):
                target_date = target_date_raw
            else:
                if is_replay:
                    raise ValueError("Replay decision requires a target_date")
                target_date = datetime.now().date()

            if is_replay:
                now = datetime.combine(target_date, day_time(9, 45), tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
            else:
                now = datetime.now()

            did_raw = decision.get("decision_id")
            try:
                if did_raw:
                    uuid.UUID(str(did_raw))
                    did = str(did_raw)
                else:
                    did = str(uuid.uuid4())
            except Exception:
                # Nếu không phải UUID chuẩn, sinh UUID mới để tránh lỗi DB
                did = str(uuid.uuid4())
                decision["decision_id"] = did

            ticker = str(decision.get("ticker", "UNKNOWN")).upper().strip()[:16]
            action = str(decision.get("action", "HOLD")).upper().strip()[:16]
            target_shares = int(decision.get("target_shares", 0))
            allocated_weight_pct = float(decision.get("allocated_weight_pct", 0.0))
            rationale = str(decision.get("rationale", ""))
            self.storage.execute(
                query,
                (did, target_date, ticker, action, target_shares, allocated_weight_pct, rationale, now)
            )
            return True
        except Exception as e:
            logger.debug(f"Lỗi khi lưu portfolio_decisions: {e}")
            return False

    def get_latest_decision(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Lấy quyết định phân bổ vốn mới nhất của một cổ phiếu từ bảng portfolio_decisions."""
        try:
            ticker_clean = str(ticker).upper().strip()[:16]
            query = """
                SELECT decision_id, date, ticker, action, target_shares, allocated_weight_pct, rationale, created_at
                FROM portfolio_decisions
                WHERE ticker = %s
                ORDER BY created_at DESC
                LIMIT 1
            """
            rows = self.storage.fetch_all(query, (ticker_clean,))
            if rows and len(rows) > 0:
                r = rows[0]
                if isinstance(r, dict):
                    return {
                        "decision_id": str(r.get("decision_id")),
                        "date": str(r.get("date")),
                        "ticker": str(r.get("ticker")),
                        "action": str(r.get("action")),
                        "target_shares": int(r.get("target_shares", 0)),
                        "allocated_weight_pct": float(r.get("allocated_weight_pct", 0.0)),
                        "rationale": str(r.get("rationale", "")),
                        "created_at": r.get("created_at").isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at")),
                    }
                else:
                    did, d_date, sym, act, shares, weight, rat, cat = r
                    return {
                        "decision_id": str(did),
                        "date": str(d_date),
                        "ticker": str(sym),
                        "action": str(act),
                        "target_shares": int(shares) if shares is not None else 0,
                        "allocated_weight_pct": float(weight) if weight is not None else 0.0,
                        "rationale": str(rat or ""),
                        "created_at": cat.isoformat() if hasattr(cat, "isoformat") else str(cat or ""),
                    }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc portfolio_decisions cho {ticker}: {e}")
        return None

    def get_decisions_by_date(self, target_date: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Lấy danh sách các quyết định phân bổ vốn theo ngày."""
        try:
            if not target_date:
                target_date = datetime.now().date()
            query = """
                SELECT decision_id, date, ticker, action, target_shares, allocated_weight_pct, rationale, created_at
                FROM portfolio_decisions
                WHERE date = %s
                ORDER BY created_at DESC
            """
            rows = self.storage.fetch_all(query, (target_date,))
            results = []
            if rows:
                for r in rows:
                    if isinstance(r, dict):
                        results.append({
                            "decision_id": str(r.get("decision_id")),
                            "date": str(r.get("date")),
                            "ticker": str(r.get("ticker")),
                            "action": str(r.get("action")),
                            "target_shares": int(r.get("target_shares", 0)),
                            "allocated_weight_pct": float(r.get("allocated_weight_pct", 0.0)),
                            "rationale": str(r.get("rationale", "")),
                            "created_at": r.get("created_at").isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at")),
                        })
                    else:
                        did, d_date, sym, act, shares, weight, rat, cat = r
                        results.append({
                            "decision_id": str(did),
                            "date": str(d_date),
                            "ticker": str(sym),
                            "action": str(act),
                            "target_shares": int(shares) if shares is not None else 0,
                            "allocated_weight_pct": float(weight) if weight is not None else 0.0,
                            "rationale": str(rat or ""),
                            "created_at": cat.isoformat() if hasattr(cat, "isoformat") else str(cat or ""),
                        })
            return results
        except Exception as e:
            logger.warning(f"Lỗi khi đọc portfolio_decisions theo ngày {target_date}: {e}")
            return []
