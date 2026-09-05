"""AGENT-09: Position Monitoring Agent (IOS v5.1 - Senior Broker Edition)

Chức năng:
- Giám sát thời gian thực toàn bộ các vị thế đang mở trong danh mục theo chu kỳ 5 phút trong giờ giao dịch.
- Tuân thủ nghiêm ngặt chu kỳ thanh toán bù trừ T+2.5 trên sàn HOSE (phân tách available_shares vs locked_t25_shares).
- Thực thi Hệ thống phòng thủ đa tầng qua StopLossEngine:
    - Tầng 0: Kiểm tra hàng khả dụng T+2.5 & xử lý nghẽn thanh khoản kẹt sàn (Floor Lock / Múa bên trăng).
    - Tầng 1 (Hard Stop Tối cao Điều 1): Kích hoạt bán khẩn cấp ngay khi lỗ vị thế >= 2% NAV (BYPASS Portfolio Agent).
    - Tầng 2 (Trailing Stop): Khóa lợi nhuận khi cổ phiếu từng lãi >= 10% nhưng đánh mất >= 35% lợi nhuận từ đỉnh.
    - Tầng 3 (Structural Exit): Cắt lỗ khi giá gãy vùng đáy hỗ trợ gần nhất (Swing Low).
    - Tầng 4 (Fast Exit VSA): Cắt sớm 50% lô chẵn khi xuất hiện nến Bearish Rejection kèm Vol > 1.5x MA20.
    - Tầng 5 (Time Stop): Cơ cấu 50% lô chẵn khi hết 50% thời hạn nắm giữ mà lãi < 2% (chôn vốn).
- Giám sát Thesis Invalidation với SLA 14:00:
    - Trước 14:00: Báo động khẩn cấp cho Portfolio Agent xem xét.
    - Đúng 14:00: Nếu Portfolio Agent không phản hồi, Agent 9 tự động kích hoạt AUTO_EXIT giải phóng vị thế trước ATC.
- Tự động bán khi Failsafe ACTIVE:
    - Failsafe chỉ phong tỏa chiều MUA, chiều BÁN thoát hiểm của Agent 9 luôn được tự động kích hoạt thẳng ra sàn.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.stop_loss import StopLossEngine, StopLossOrder
from app.domain.repositories.portfolio_repository import PortfolioRepository

logger = logging.getLogger(__name__)


class PositionMonitoringAgent(BaseAgent):
    """
    AGENT-09: Chuyên viên Giám sát Vị thế & Canh gác Rủi ro Thời gian thực (Senior Broker Edition).
    Được quyền tối cao phát lệnh cắt lỗ thẳng ra sàn mà không cần xin phép Portfolio Agent.
    """

    def __init__(
        self,
        repository: Optional[PortfolioRepository] = None,
        market_data_repo: Optional[Any] = None,
        auto_dispatch: bool = True,
    ):
        super().__init__(
            agent_name="position_monitoring",
            state_tables=["position_health_ticks", "stop_loss_events"],
            log_table="log_position_monitoring",
            enabled=True,
        )
        self.stop_loss_engine = StopLossEngine()
        self.repository = repository or PortfolioRepository()
        self._market_data_repo = market_data_repo
        self.auto_dispatch = auto_dispatch
        self._peak_price_cache: Dict[str, float] = {}

    @property
    def market_data_repo(self):
        if self._market_data_repo is None:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                self._market_data_repo = MarketDataRepository()
            except Exception as e:
                logger.debug(f"Không thể khởi tạo MarketDataRepository: {e}")
        return self._market_data_repo

    def _persist_health_ticks(self, monitored_positions: List[Dict[str, Any]]) -> None:
        """Ghi nhận trạng thái sức khỏe các vị thế vào bảng nghiệp vụ position_health_ticks."""
        if not monitored_positions:
            return
        try:
            sql = """
                INSERT INTO position_health_ticks (ticker, current_pnl_pct, distance_to_stop_loss_pct, thesis_health_status, last_updated)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (ticker) DO UPDATE SET
                    current_pnl_pct = EXCLUDED.current_pnl_pct,
                    distance_to_stop_loss_pct = EXCLUDED.distance_to_stop_loss_pct,
                    thesis_health_status = EXCLUDED.thesis_health_status,
                    last_updated = EXCLUDED.last_updated
            """
            for pos in monitored_positions:
                ticker = pos["ticker"]
                pnl_pct = float(pos.get("unrealized_pnl_pct", 0.0))
                pnl_nav = float(pos.get("unrealized_pnl_nav_pct", 0.0))
                distance_to_stop = round(pnl_nav - (-2.0), 2)
                status_short = str(pos.get("health_status", "NORMAL"))[:16]
                self.repository.storage.execute(sql, (ticker, pnl_pct, distance_to_stop, status_short))
        except Exception as e:
            logger.debug(f"Không thể ghi position_health_ticks vào DB ({e})")

    def _persist_stop_loss_events(self, stop_loss_orders: List[Dict[str, Any]]) -> None:
        """Ghi nhận sự kiện kích hoạt Stop-Loss khẩn cấp vào bảng nghiệp vụ stop_loss_events và cập nhật paper_trades."""
        if not stop_loss_orders:
            return
        try:
            sql = """
                INSERT INTO stop_loss_events (event_id, ticker, triggered_price, loss_pct_nav, bypass_order_id, triggered_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """
            for order in stop_loss_orders:
                event_id = order.get("stop_order_id") or str(uuid.uuid4())
                ticker = order["ticker"]
                loss_nav = float(order.get("current_pnl_nav_pct", 0.0))
                bypass_id = event_id if order.get("bypass_portfolio_agent") else None
                triggered_price = float(order.get("triggered_price", 0.0))
                self.repository.storage.execute(sql, (event_id, ticker, triggered_price, loss_nav, bypass_id))

            # Đồng bộ sang bảng paper_trades: Đóng vị thế và ghi nhận P&L thực tế phục vụ Agent-10 học tăng cường
            sql_paper = """
                UPDATE paper_trades
                SET status = 'CLOSED',
                    resolve_price = %s,
                    pnl = %s,
                    resolved_at = CURRENT_TIMESTAMP
                WHERE ticker = %s AND status = 'OPEN'
            """
            for order in stop_loss_orders:
                ticker = order["ticker"]
                triggered_price = float(order.get("triggered_price", 0.0))
                pnl_pct = float(order.get("current_pnl_pct", 0.0))
                self.repository.storage.execute(sql_paper, (triggered_price, pnl_pct, ticker))
        except Exception as e:
            logger.debug(f"Không thể ghi stop_loss_events hoặc paper_trades vào DB ({e})")

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Giám sát vị thế:
        - event_data:
            - nav: float (Tổng NAV danh mục)
            - position: Dict (vị thế đơn lẻ) HOẶC positions: List[Dict] (toàn danh mục)
            - market_ticks: Dict[str, Dict] (ticker -> {price, candle, volume_ma20, floor_price, bid_volume, is_floor_locked})
            - invalidation_events: List[Dict] (ticker, event_type, reason, portfolio_approved)
            - failsafe_active / failsafe: bool (Trạng thái Failsafe hệ thống)
            - current_time: str (ISO datetime để kiểm tra mốc 14:00)
            - auto_dispatch: bool (Tự động chuyển tiếp lệnh khẩn cấp sang Agent-08)
        """
        account_state = self.repository.get_account_state()
        nav = float(event_data.get("nav", account_state.get("total_nav", 1000000000.0)))
        failsafe_active = bool(event_data.get("failsafe_active", False) or event_data.get("failsafe", False))
        auto_dispatch = bool(event_data.get("auto_dispatch", self.auto_dispatch))

        # Kiểm tra mốc thời gian đánh giá Invalidation (SLA trước 14:00)
        eval_time_str = event_data.get("current_time")
        if eval_time_str:
            try:
                eval_dt = datetime.fromisoformat(str(eval_time_str))
            except Exception:
                eval_dt = datetime.now()
        else:
            eval_dt = datetime.now()
        is_past_14h = eval_dt.hour >= 14

        positions_raw = event_data.get("positions", [])
        if not positions_raw and "position" in event_data:
            positions_raw = [event_data["position"]]

        # Nếu không truyền vị thế qua event, tự động đọc từ CSDL PostgreSQL (kèm phân tách T+2.5)
        if not positions_raw:
            db_positions = self.repository.get_open_positions()
            positions_raw = [
                {
                    "ticker": p["ticker"],
                    "quantity": p.get("shares", 0),
                    "available_shares": p.get("available_shares", p.get("shares", 0)),
                    "locked_t25_shares": p.get("locked_t25_shares", 0),
                    "entry_price": p.get("average_price", 0.0),
                    "current_price": None,  # Để Agent 9 tự động fetch giá realtime
                    "holding_days": p.get("holding_days", 0),
                    "swing_low": p.get("swing_low") or (p.get("average_price", 0.0) * 0.95),
                    "peak_price": p.get("peak_price", 0.0),
                }
                for p in db_positions
            ]

        market_ticks = event_data.get("market_ticks", {})
        invalidation_events = event_data.get("invalidation_events", [])

        monitored_positions = []
        stop_loss_orders = []
        invalidation_alerts = []

        for pos in positions_raw:
            ticker = pos.get("ticker")
            if not ticker:
                logger.warning(f"[PositionMonitoringAgent] Bỏ qua vị thế không có ticker: {pos}")
                continue
            ticker = str(ticker).upper().strip()

            total_quantity = int(pos.get("quantity") or pos.get("shares", 0))
            available_shares = int(pos.get("available_shares", total_quantity))
            locked_t25_shares = int(pos.get("locked_t25_shares", max(0, total_quantity - available_shares)))

            entry_price = float(pos.get("entry_price") or pos.get("average_price", 0.0))
            raw_current_price = pos.get("current_price")
            current_price = float(raw_current_price) if raw_current_price is not None and float(raw_current_price) > 0 else entry_price

            swing_low = pos.get("swing_low") or pos.get("swing_low_price")
            holding_days = int(pos.get("holding_days") or pos.get("days_held", 0))
            expected_timeline = int(pos.get("expected_timeline_days", 90))
            last_order_unfilled = bool(pos.get("last_order_unfilled", False))

            # 1. TỰ ĐỘNG KẾT NỐI DATA REALTIME:
            # Nếu có tick trong market_ticks -> cập nhật
            # Nếu không có market_ticks VÀ pos không truyền current_price cụ thể -> tự query qua Repository
            tick_info = market_ticks.get(ticker)
            current_candle = None
            ma20_vol = 0.0
            floor_price = entry_price * 0.93  # Biên độ -7% HOSE mặc định
            is_floor_locked = False

            if tick_info:
                if "price" in tick_info or "close" in tick_info:
                    current_price = float(tick_info.get("price") or tick_info.get("close", current_price))
                current_candle = tick_info.get("current_candle")
                ma20_vol = float(tick_info.get("ma20_volume", 0.0))
                floor_price = float(tick_info.get("floor_price", floor_price))
                bid_vol = float(tick_info.get("bid_volume", 1000.0))
                is_floor_locked = bool(
                    tick_info.get("is_floor_locked", False)
                    or (current_price <= floor_price and bid_vol == 0)
                )
            elif raw_current_price is None and self.market_data_repo:
                # Tự động truy xuất giá realtime từ Redis / DNSE WebSocket khi chưa có giá cụ thể
                try:
                    live_p = self.market_data_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True)
                    if live_p and live_p > 0:
                        current_price = float(live_p)
                except Exception as e_price:
                    logger.debug(f"Không thể tra cứu giá realtime cho {ticker}: {e_price}")

                # Kiểm tra thông tin biên độ sàn/trần và dư mua từ Redis
                try:
                    from app.infrastructure.external_api.dnse.redis_pub import get_redis
                    import json
                    r = get_redis()
                    sec_def_raw = r.get(f"stock:{ticker}:sec_def")
                    if sec_def_raw:
                        sec_def = json.loads(sec_def_raw)
                        floor_price = float(sec_def.get("floor", floor_price))
                    quote_raw = r.get(f"stock:{ticker}:quote")
                    if quote_raw:
                        quote = json.loads(quote_raw)
                        bid_vol = float(quote.get("bid_volume", 1000.0))
                        is_floor_locked = (current_price <= floor_price and bid_vol == 0)
                except Exception:
                    pass

            # 2. CẬP NHẬT VÀ LƯU GIỮ GIÁ ĐỈNH (PEAK_PRICE) PHỤC VỤ TRAILING STOP
            cached_peak = self._peak_price_cache.get(ticker, 0.0)
            initial_pos_peak = float(pos.get("peak_price") or pos.get("highest_price", 0.0))
            peak_price = max(
                initial_pos_peak,
                cached_peak,
                current_price,
                entry_price,
            )
            self._peak_price_cache[ticker] = peak_price

            # 3. Kiểm tra Hệ thống phòng thủ đa tầng qua StopLossEngine
            market_data = {
                "available_shares": available_shares,
                "peak_price": peak_price,
                "swing_low": swing_low,
                "holding_days": holding_days,
                "expected_timeline_days": expected_timeline,
                "is_floor_locked": is_floor_locked,
                "last_order_unfilled": last_order_unfilled,
                "current_candle": current_candle,
                "ma20_volume": ma20_vol,
            }

            stop_order: Optional[StopLossOrder] = self.stop_loss_engine.check_position(
                ticker=ticker,
                quantity=total_quantity,
                entry_price=entry_price,
                current_price=current_price,
                nav=nav,
                market_data=market_data,
                available_shares=available_shares,
            )

            pnl_vnd = (current_price - entry_price) * total_quantity
            pnl_pct = (current_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
            pnl_nav_pct = pnl_vnd / nav * 100.0 if nav > 0 else 0.0

            # 4. Phân loại trạng thái sức khỏe vị thế & Lệnh phòng thủ
            if stop_order:
                if stop_order.suggested_action == "WAIT_T25_SETTLEMENT":
                    health_status = "CRITICAL_T25_LOCKED"
                elif stop_order.suggested_action == "FLOOR_LOCK_RESET":
                    health_status = "FLOOR_LOCK_UNFILLED"
                elif stop_order.rule_level == "HARD_STOP":
                    health_status = "CRITICAL_STOP_LOSS"
                elif stop_order.rule_level == "TRAILING_STOP":
                    health_status = "TRAILING_STOP_TRIGGERED"
                else:
                    health_status = "STOP_LOSS_WARNING"

                if stop_order.quantity > 0:
                    order_id = str(uuid.uuid4())
                    stop_loss_payload = {
                        "stop_order_id": order_id,
                        "ticker": ticker,
                        "action": "SELL",
                        "side": "SELL",
                        "quantity": stop_order.quantity,
                        "shares": stop_order.quantity,
                        "approved_shares": stop_order.quantity,
                        "price": current_price,
                        "target_price": current_price,
                        "decision_price": current_price,
                        "available_shares": available_shares,
                        "locked_t25_shares": locked_t25_shares,
                        "urgency": stop_order.urgency,
                        "rule_level": stop_order.rule_level,
                        "reason": stop_order.reason,
                        "suggested_action": stop_order.suggested_action,
                        "triggered_price": current_price,
                        "current_pnl_pct": round(pnl_pct, 2),
                        "current_pnl_nav_pct": round(pnl_nav_pct, 2),
                        "bypass_portfolio_agent": True,
                        "target_execution": "EXECUTION_AGENT_DIRECT_ROUTE",
                        "failsafe_override": failsafe_active,
                    }
                    stop_loss_orders.append(stop_loss_payload)
            else:
                health_status = "HEALTHY" if pnl_pct >= 0 else "WARNING"

            # 5. Giám sát Luận điểm đầu tư (Thesis Invalidation Watchdog) với SLA 14:00
            for inv in invalidation_events:
                if inv.get("ticker", "").upper() == ticker:
                    portfolio_resolved = bool(
                        inv.get("portfolio_approved", False) or inv.get("portfolio_resolved", False)
                    )

                    if is_past_14h and not portfolio_resolved:
                        # Đã qua 14:00 mà Portfolio Agent chưa phản hồi -> Buộc kích hoạt lệnh thoát trước ATC
                        if available_shares > 0:
                            sell_qty = self.stop_loss_engine.round_hose_lot(available_shares)
                            order_id = str(uuid.uuid4())
                            inval_order = {
                                "stop_order_id": order_id,
                                "ticker": ticker,
                                "action": "SELL",
                                "side": "SELL",
                                "quantity": sell_qty,
                                "shares": sell_qty,
                                "approved_shares": sell_qty,
                                "price": current_price,
                                "target_price": current_price,
                                "decision_price": current_price,
                                "available_shares": available_shares,
                                "locked_t25_shares": locked_t25_shares,
                                "urgency": "EMERGENCY",
                                "rule_level": "THESIS_INVALIDATION_AUTO_EXIT",
                                "reason": (
                                    f"SLA 14:00 Vi phạm: Luận điểm đầu tư {ticker} bị Invalidation và quá 14:00 "
                                    "không có phản hồi từ Portfolio Agent. Buộc bán khẩn cấp trước phiên ATC."
                                ),
                                "suggested_action": "AUTO_EXIT_INVALIDATED_THESIS",
                                "triggered_price": current_price,
                                "current_pnl_pct": round(pnl_pct, 2),
                                "current_pnl_nav_pct": round(pnl_nav_pct, 2),
                                "bypass_portfolio_agent": True,
                                "target_execution": "EXECUTION_AGENT_DIRECT_ROUTE",
                                "failsafe_override": failsafe_active,
                            }
                            stop_loss_orders.append(inval_order)
                            health_status = "CRITICAL_THESIS_AUTO_EXIT"
                        else:
                            health_status = "THESIS_INVALIDATED_T25_LOCKED"


                        invalidation_alerts.append({
                            "ticker": ticker,
                            "event_type": inv.get("event_type", "CATALYST_FAILED"),
                            "reason": inv.get("reason", "Luận điểm đầu tư bị phá vỡ"),
                            "action_taken": "AUTO_EXIT_TRIGGERED_PAST_14H",
                            "deadline": "14:00:00",
                            "is_past_14h": True,
                        })
                    else:
                        # Trước 14:00: Cảnh báo khẩn cấp lên Portfolio Agent
                        if not health_status.startswith("CRITICAL"):
                            health_status = "THESIS_INVALIDATED_ESCALATED"
                        invalidation_alerts.append({
                            "ticker": ticker,
                            "event_type": inv.get("event_type", "CATALYST_FAILED"),
                            "reason": inv.get("reason", "Luận điểm đầu tư bị phá vỡ"),
                            "action_required": "ESCALATE_PORTFOLIO_CONFIRMATION",
                            "deadline": "14:00:00",
                            "is_past_14h": False,
                        })

            monitored_positions.append({
                "ticker": ticker,
                "quantity": total_quantity,
                "available_shares": available_shares,
                "locked_t25_shares": locked_t25_shares,
                "entry_price": entry_price,
                "current_price": current_price,
                "peak_price": peak_price,
                "unrealized_pnl_vnd": round(pnl_vnd, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "unrealized_pnl_nav_pct": round(pnl_nav_pct, 2),
                "health_status": health_status,
                "holding_days": holding_days,
            })

        # 6. TỰ ĐỘNG DISPATCH LỆNH CẮT LỖ KHẨN CẤP SANG AGENT-08 (TRADE EXECUTION AGENT)
        dispatch_results = []
        if auto_dispatch and stop_loss_orders:
            for order in stop_loss_orders:
                if order.get("urgency") in ("EMERGENCY", "HIGH") and order.get("quantity", 0) > 0:
                    try:
                        from app.core.registry import AgentRegistry
                        exec_res = await AgentRegistry.dispatch("trade_execution", {
                            "order_instruction": order,
                            "failsafe_active": failsafe_active,
                            "failsafe_override": True,
                        })
                        order["dispatch_status"] = "DISPATCHED_TO_AGENT_08"
                        order["execution_response"] = exec_res.get("result", {}).get("data", {})
                        dispatch_results.append(order["execution_response"])
                        logger.info(f"[PositionMonitoringAgent] Đã tự động dispatch lệnh khẩn cấp cho {order['ticker']} sang Agent-08.")
                    except Exception as e_dispatch:
                        logger.error(f"[PositionMonitoringAgent] Lỗi khi dispatch lệnh sang Agent-08: {e_dispatch}")
                        order["dispatch_status"] = f"DISPATCH_ERROR: {e_dispatch}"

        # 7. GHI NHẬN PERSISTENCE VÀO CSDL (TABLES NGHIỆP VỤ)
        self._persist_health_ticks(monitored_positions)
        if stop_loss_orders:
            self._persist_stop_loss_events(stop_loss_orders)

        # Cung cấp các trường top-level để BaseAgent ghi đúng vào log_position_monitoring
        first_pos = monitored_positions[0] if monitored_positions else {}
        avg_pnl = (
            round(sum(p.get("unrealized_pnl_pct", 0.0) for p in monitored_positions) / len(monitored_positions), 2)
            if monitored_positions
            else 0.0
        )
        single_or_multi_ticker = (
            first_pos.get("ticker", "PORTFOLIO")
            if len(monitored_positions) <= 1
            else [p["ticker"] for p in monitored_positions]
        )

        output_data = {
            "monitored_at": eval_dt.isoformat(),
            "nav": nav,
            "ticker": single_or_multi_ticker,
            "pnl_pct": avg_pnl,
            "stop_loss_triggered": len(stop_loss_orders) > 0,
            "thesis_invalidated": any(
                p.get("health_status", "").startswith("THESIS_INVALIDATED")
                or p.get("health_status", "").startswith("CRITICAL_THESIS")
                for p in monitored_positions
            ),
            "failsafe_active": failsafe_active,
            "failsafe_mode": "DEFENSIVE_SELL_ENABLED_BUY_BLOCKED" if failsafe_active else "NORMAL",
            "monitored_count": len(monitored_positions),
            "positions_health": monitored_positions,
            "stop_loss_orders": stop_loss_orders,
            "dispatch_results": dispatch_results,
            "invalidation_alerts": invalidation_alerts,
            "has_emergency_orders": any(
                o.get("urgency") in ["EMERGENCY", "HIGH"] for o in stop_loss_orders
            ),
        }

        trace = {
            "stop_loss_engine": self.stop_loss_engine.__class__.__name__,
            "total_positions_checked": len(positions_raw),
            "emergency_triggers_count": len(stop_loss_orders),
            "thesis_invalidations_count": len(invalidation_alerts),
            "auto_dispatched_count": len(dispatch_results),
            "failsafe_active": failsafe_active,
            "is_past_14h": is_past_14h,
        }

        return {"data": output_data, "trace": trace}


