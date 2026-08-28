"""AGENT-09: Position Monitoring Agent (IOS v5.1)

Chức năng:
- Giám sát thời gian thực toàn bộ các vị thế đang mở trong danh mục theo chu kỳ 5 phút trong giờ giao dịch.
- Thực thi Hệ thống phòng thủ 4 tầng Stop-loss qua StopLossEngine:
    - Lớp 1 (Fast Exit): Cắt sớm khi xuất hiện nến Bearish Rejection kèm Vol > 1.5x MA20.
    - Lớp 2 (Structural Exit): Cắt lỗ khi giá gãy vùng đáy hỗ trợ gần nhất (Swing Low).
    - Lớp 3 (Time Stop): Thoát vị thế khi hết thời hạn nắm giữ mà catalyst không diễn ra.
    - Lớp 4 (Hard Stop Điều 1): Kích hoạt bán khẩn cấp ngay khi lỗ vị thế >= 2% NAV (BYPASS Portfolio Agent).
- Giám sát tính hợp lệ của luận điểm đầu tư (Thesis Health Watchdog) và phát tín hiệu Thesis Invalidation.
- Tích hợp PortfolioRepository tự động lấy danh mục vị thế đang mở từ PostgreSQL.
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
    AGENT-09: Chuyên viên Giám sát Vị thế & Canh gác Rủi ro Thời gian thực.
    Được quyền tối cao phát lệnh cắt lỗ thẳng ra sàn mà không cần xin phép Portfolio Agent.
    """

    def __init__(self, repository: Optional[PortfolioRepository] = None):
        super().__init__(
            agent_name="position_monitoring",
            state_tables=["position_health_ticks", "stop_loss_events"],
            log_table="log_position_monitoring",
            enabled=True,
        )
        self.stop_loss_engine = StopLossEngine()
        self.repository = repository or PortfolioRepository()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Giám sát vị thế:
        - event_data:
            - nav: float (Tổng NAV danh mục)
            - position: Dict (vị thế đơn lẻ) HOẶC positions: List[Dict] (toàn danh mục)
            - market_ticks: Dict[str, Dict] (ticker -> {price, candle, volume_ma20})
            - invalidation_events: List[Dict] (ticker, event_type, reason)
        """
        account_state = self.repository.get_account_state()
        nav = float(event_data.get("nav", account_state.get("total_nav", 1000000000.0)))

        positions_raw = event_data.get("positions", [])
        if not positions_raw and "position" in event_data:
            positions_raw = [event_data["position"]]

        # Nếu không truyền vị thế qua event, tự động đọc từ CSDL PostgreSQL
        if not positions_raw:
            db_positions = self.repository.get_open_positions()
            positions_raw = [
                {
                    "ticker": p["ticker"],
                    "quantity": p["shares"],
                    "entry_price": p["average_price"],
                    "current_price": p["current_price"],
                    "holding_days": 5,
                    "swing_low": p["average_price"] * 0.95,
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
            quantity = int(pos.get("quantity", 0))
            entry_price = float(pos.get("entry_price") or pos.get("average_price", 0.0))
            current_price = float(pos.get("current_price", entry_price))
            swing_low = float(pos.get("swing_low", entry_price * 0.95))
            holding_days = int(pos.get("holding_days", 5))

            tick_info = market_ticks.get(ticker, {})
            current_candle = tick_info.get("current_candle", {
                "open": current_price,
                "high": current_price * 1.01,
                "low": current_price * 0.99,
                "close": current_price,
                "volume": 500000,
            })
            ma20_vol = tick_info.get("ma20_volume", 400000.0)

            # 1. Kiểm tra 4 tầng Stop-loss qua StopLossEngine
            market_data = {
                "current_candle": current_candle,
                "ma20_volume": ma20_vol,
                "swing_low": swing_low,
                "holding_days": holding_days,
            }

            stop_order: Optional[StopLossOrder] = self.stop_loss_engine.check_position(
                ticker=ticker,
                quantity=quantity,
                entry_price=entry_price,
                current_price=current_price,
                nav=nav,
                market_data=market_data,
            )

            pnl_vnd = (current_price - entry_price) * quantity
            pnl_pct = (current_price - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
            pnl_nav_pct = pnl_vnd / nav * 100.0 if nav > 0 else 0.0

            # 2. Xử lý khi kích hoạt Lệnh Cắt lỗ Khẩn cấp
            if stop_order:
                order_id = str(uuid.uuid4())
                stop_loss_payload = {
                    "stop_order_id": order_id,
                    "ticker": ticker,
                    "quantity": quantity,
                    "urgency": stop_order.urgency,
                    "reason": stop_order.reason,
                    "suggested_action": stop_order.suggested_action,
                    "current_loss_nav_pct": round(pnl_nav_pct, 2),
                    "bypass_portfolio_agent": True,
                    "target_execution": "EXECUTION_AGENT_DIRECT_ROUTE",
                }
                stop_loss_orders.append(stop_loss_payload)
                health_status = "CRITICAL_STOP_LOSS"
            else:
                health_status = "HEALTHY" if pnl_pct >= 0 else "WARNING"

            # 3. Giám sát Thesis Invalidation
            for inv in invalidation_events:
                if inv.get("ticker", "").upper() == ticker:
                    invalidation_alerts.append({
                        "ticker": ticker,
                        "event_type": inv.get("event_type", "CATALYST_FAILED"),
                        "reason": inv.get("reason", "BCTC hoặc tin tức vi phạm điều kiện duy trì"),
                        "action_required": "REVIEW_OR_EXIT",
                    })
                    health_status = "THESIS_INVALIDATED"

            monitored_positions.append({
                "ticker": ticker,
                "quantity": quantity,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl_vnd": round(pnl_vnd, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "unrealized_pnl_nav_pct": round(pnl_nav_pct, 2),
                "health_status": health_status,
                "holding_days": holding_days,
            })

        output_data = {
            "monitored_at": datetime.now().isoformat(),
            "nav": nav,
            "monitored_count": len(monitored_positions),
            "positions_health": monitored_positions,
            "stop_loss_orders": stop_loss_orders,
            "stop_loss_triggered": len(stop_loss_orders) > 0,
            "invalidation_alerts": invalidation_alerts,
            "has_emergency_orders": len(stop_loss_orders) > 0,
        }

        trace = {
            "stop_loss_engine": self.stop_loss_engine.__class__.__name__,
            "total_positions_checked": len(positions_raw),
            "emergency_triggers_count": len(stop_loss_orders),
            "thesis_invalidations_count": len(invalidation_alerts),
        }

        return {"data": output_data, "trace": trace}
