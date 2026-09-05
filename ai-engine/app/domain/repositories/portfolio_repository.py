"""Portfolio Repository Layer (IOS v5.1)
Quản lý trạng thái thực tế của Người dùng / Tài khoản (bảng users & portfolio_account),
Danh mục vị thế duy nhất chuẩn hóa (bảng positions), 
và Lịch sử khớp lệnh (bảng orders & order_executions) kết nối PostgreSQL / TimescaleDB.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


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
        # Bộ nhớ tạm in-memory fallback phòng khi chạy unit test độc lập
        self._in_memory_account: Dict[str, Any] = {
            "account_id": "940b0c70-2010-42f3-b947-797e6419b794",
            "cash_balance": 1000000000.0,
            "total_nav": 1000000000.0,
            "peak_nav": 1000000000.0,
            "drawdown_tier": "GREEN",
            "win_rate": 0.0,
        }
        self._in_memory_positions: Dict[str, Dict[str, Any]] = {}

        self._in_memory_campaigns: Dict[str, Dict[str, Any]] = {}
        self._in_memory_slippage_records: List[Dict[str, Any]] = []

    def get_account_state(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Lấy số dư tiền mặt từ bảng users và tính tổng NAV danh mục từ CSDL."""
        try:
            # 1. Đọc số dư tiền mặt từ bảng users
            if user_id:
                query_user = "SELECT id, cash_balance, win_rate FROM users WHERE id = %s"
                rows_user = self.storage.fetch_all(query_user, (user_id,))
            else:
                query_user = "SELECT id, cash_balance, win_rate FROM users ORDER BY created_at ASC LIMIT 1"
                rows_user = self.storage.fetch_all(query_user)

            if rows_user and len(rows_user) > 0:
                uid, cash, win_rate = rows_user[0]
                cash_val = float(cash) if cash is not None else 1000000000.0
                win_rate_val = float(win_rate) if win_rate is not None else 0.0

                # 2. Tính tổng giá trị danh mục vị thế từ bảng positions
                query_pos = "SELECT symbol, quantity, avg_price FROM positions WHERE user_id = %s AND quantity > 0"
                rows_pos = self.storage.fetch_all(query_pos, (uid,))
                positions_val = sum(float(r[1]) * float(r[2]) for r in rows_pos) if rows_pos else 0.0
                total_nav = cash_val + positions_val

                self._in_memory_account = {
                    "account_id": str(uid),
                    "cash_balance": cash_val,
                    "total_nav": total_nav,
                    "peak_nav": max(total_nav, self._in_memory_account.get("peak_nav", total_nav)),
                    "drawdown_tier": "GREEN",
                    "win_rate": win_rate_val,
                }
                return self._in_memory_account
        except Exception as e:
            logger.warning(f"Không thể đọc users/positions từ DB ({e}), sử dụng in-memory state")

        return self._in_memory_account

    def get_open_positions(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lấy toàn bộ các vị thế cổ phiếu đang nắm giữ kèm phân tách hàng khả dụng T+2.5."""
        try:
            if user_id:
                query = """
                    SELECT symbol, quantity, avg_price, opened_at
                    FROM positions
                    WHERE user_id = %s AND quantity > 0
                    ORDER BY quantity DESC
                """
                rows = self.storage.fetch_all(query, (user_id,))
            else:
                query = """
                    SELECT symbol, quantity, avg_price, opened_at
                    FROM positions
                    WHERE quantity > 0
                    ORDER BY quantity DESC
                """
                rows = self.storage.fetch_all(query)

            if rows:
                results = []
                now = datetime.now()
                for r in rows:
                    total_shares = int(r[1])
                    opened_at = r[3] if len(r) > 3 and r[3] else None
                    # Kiểm tra chu kỳ T+2.5 (2 ngày làm việc)
                    is_locked = False
                    if opened_at:
                        try:
                            if isinstance(opened_at, str):
                                opened_dt = datetime.fromisoformat(opened_at)
                            else:
                                opened_dt = opened_at
                            # Nếu mở trong vòng 2 ngày (48h), coi như chưa về hết
                            if (now - opened_dt).total_seconds() < 2 * 86400:
                                is_locked = True
                        except Exception:
                            pass

                    available_shares = 0 if is_locked else total_shares
                    locked_shares = total_shares if is_locked else 0

                    results.append({
                        "ticker": str(r[0]),
                        "shares": total_shares,
                        "available_shares": available_shares,
                        "locked_t25_shares": locked_shares,
                        "average_price": float(r[2]),
                        "current_price": float(r[2]),
                        "market_value": total_shares * float(r[2]),
                        "weight_pct": 0.0,
                    })
                return results
        except Exception as e:
            logger.warning(f"Không thể đọc positions từ DB ({e}), dùng in-memory fallback")

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
    ) -> Dict[str, Any]:
        """
        Thực hiện giao dịch nguyên tử (Atomic Execution):
        1. Trừ/Cộng tiền mặt trong bảng users (cash_balance)
        2. Thêm/Cộng dồn/Bớt cổ phiếu trong bảng positions
        3. Ghi hóa đơn khớp lệnh vào bảng orders và order_executions
        """
        ticker = ticker.upper().strip()
        action = action.upper().strip()
        trade_value = float(executed_price * shares)
        order_id = str(uuid.uuid4())
        pos_id = str(uuid.uuid4())
        now = datetime.now()

        # 1. Cập nhật In-Memory Cache
        if action == "BUY":
            self._in_memory_account["cash_balance"] -= trade_value
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
            self._in_memory_account["cash_balance"] += trade_value
            if ticker in self._in_memory_positions:
                pos = self._in_memory_positions[ticker]
                pos["shares"] = max(0, pos["shares"] - shares)
                pos["market_value"] = pos["shares"] * executed_price
                if pos["shares"] == 0:
                    del self._in_memory_positions[ticker]

        # 2. Cập nhật CSDL PostgreSQL thực tế
        target_uid = user_id or self._in_memory_account.get("account_id") or "940b0c70-2010-42f3-b947-797e6419b794"
        try:
            # 2.1 Cập nhật số dư tiền mặt trong bảng users
            if action == "BUY":
                sql_user = "UPDATE users SET cash_balance = cash_balance - %s WHERE id = %s"
                self.storage.execute(sql_user, (trade_value, target_uid))
            elif action in ("SELL", "SELL_MP"):
                sql_user = "UPDATE users SET cash_balance = cash_balance + %s WHERE id = %s"
                self.storage.execute(sql_user, (trade_value, target_uid))

            # 2.2 Cập nhật vị thế trong bảng positions
            if action == "BUY":
                # Kiểm tra vị thế đã tồn tại chưa
                sql_check = "SELECT id, quantity, avg_price FROM positions WHERE user_id = %s AND symbol = %s"
                existing = self.storage.fetch_all(sql_check, (target_uid, ticker))
                if existing:
                    pid, old_q, old_avg = existing[0]
                    new_q = int(old_q) + shares
                    new_avg = ((float(old_avg) * int(old_q)) + (executed_price * shares)) / new_q
                    sql_update_pos = "UPDATE positions SET quantity = %s, avg_price = %s WHERE id = %s"
                    self.storage.execute(sql_update_pos, (new_q, new_avg, pid))
                else:
                    sql_insert_pos = """
                        INSERT INTO positions (id, user_id, symbol, quantity, avg_price, opened_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    self.storage.execute(sql_insert_pos, (pos_id, target_uid, ticker, shares, executed_price, now))
            elif action in ("SELL", "SELL_MP"):
                sql_check = "SELECT id, quantity FROM positions WHERE user_id = %s AND symbol = %s"
                existing = self.storage.fetch_all(sql_check, (target_uid, ticker))
                if existing:
                    pid, old_q = existing[0]
                    remaining_q = max(0, int(old_q) - shares)
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
            sql_order = """
                INSERT INTO orders (id, user_id, symbol, side, order_type, price, quantity, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            self.storage.execute(sql_order, (order_id, target_uid, ticker, action, execution_mode, executed_price, shares, status, now))

            # 2.4 Đồng thời ghi vào order_executions, portfolio_positions & portfolio_account để đồng bộ các Agent
            try:
                sql_order_exec = """
                    INSERT INTO order_executions (
                        order_id, ticker, action, shares, executed_price,
                        target_price, slippage_bps, execution_mode, executed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """
                self.storage.execute(
                    sql_order_exec,
                    (order_id, ticker, action, shares, executed_price, target_price, slippage_bps, execution_mode, now)
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
                    ("MAIN_FUND", acc_state["cash_balance"], acc_state["total_nav"], acc_state["peak_nav"], "GREEN", now)
                )

                # Đồng bộ bảng paper_trades phục vụ học tăng cường (Agent-10)
                try:
                    if action == "BUY":
                        sql_paper = """
                            INSERT INTO paper_trades (ticker, action, price, date, confidence, thesis, status, quantity, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)
                        """
                        self.storage.execute(
                            sql_paper,
                            (ticker, action, executed_price, now, 0.8, "EXECUTION_AGENT_ORDER", shares, now)
                        )
                    elif action in ("SELL", "SELL_MP"):
                        sql_close = """
                            UPDATE paper_trades
                            SET status = 'CLOSED',
                                resolve_price = %s,
                                pnl = ROUND(((%s - price) / price * 100)::numeric, 2),
                                resolved_at = %s
                            WHERE ticker = %s AND status = 'OPEN'
                        """
                        self.storage.execute(sql_close, (executed_price, executed_price, now, ticker))
                except Exception as e_pt:
                    logger.debug(f"Không thể sync paper_trades: {e_pt}")
            except Exception as e_sync:
                logger.debug(f"Không thể sync portfolio_account hoặc paper_trades: {e_sync}")

            logger.info(f"Đã cập nhật giao dịch {action} {shares} {ticker} (status: {status}) vào bảng users, positions và orders thành công.")
        except Exception as e:
            logger.warning(f"Lỗi khi sync giao dịch vào DB ({e}), đã ghi in-memory.")

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
            query = """
                INSERT INTO portfolio_decisions (
                    decision_id, date, ticker, action, target_shares, allocated_weight_pct, rationale, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO NOTHING
            """
            now = datetime.now()
            target_date = now.date()
            did = decision.get("decision_id") or str(uuid.uuid4())
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

