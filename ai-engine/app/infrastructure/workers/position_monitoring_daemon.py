"""Position Monitoring Daemon (IOS v5.1 - Realtime Production Worker).

Chức năng:
- Chạy ngầm liên tục theo chu kỳ 5 phút (300 giây) trong phiên giao dịch sàn HOSE (09:15 - 11:30 & 13:00 - 14:45).
- Tự động ngắt nghỉ ngoài giờ giao dịch qua MarketSessionManager để tiết kiệm tài nguyên.
- Kích hoạt PositionMonitoringAgent (Agent-09) tự động quét toàn bộ danh mục vị thế mở.
- Tự động bắt tín hiệu và đẩy lệnh cắt lỗ / thoát Invalidation khẩn cấp sang Agent-08.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp toàn bộ 12 agents vào registry
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager, MarketState

logger = logging.getLogger("ai_engine.daemon.position_monitoring")


class PositionMonitoringDaemon:
    def __init__(self, check_interval_seconds: int = 300):
        self.interval = check_interval_seconds
        self.session_mgr = MarketSessionManager()
        self._running = False

    async def run_single_tick(self) -> dict:
        """Chạy một nhịp giám sát vị thế và xử lý khẩn cấp."""
        now = datetime.now()
        logger.info(f"[PositionDaemon] Bắt đầu nhịp giám sát vị thế lúc {now.strftime('%H:%M:%S')}...")

        try:
            res = await AgentRegistry.dispatch("position_monitoring", {
                "current_time": now.isoformat(),
                "auto_dispatch": True,  # Cho phép tự động bắn lệnh cắt lỗ sang Agent-08
            })

            if res.get("status") == "SUCCESS":
                data = res.get("result", {}).get("data", {})
                monitored_count = data.get("monitored_count", 0)
                emergency_count = len(data.get("stop_loss_orders", []))
                invalidation_count = len(data.get("invalidation_alerts", []))
                dispatched_count = len(data.get("dispatch_results", []))

                logger.info(
                    f"[PositionDaemon] Hoàn tất nhịp: Giám sát {monitored_count} vị thế | "
                    f"StopLoss Triggers: {emergency_count} | Invalidations: {invalidation_count} | "
                    f"Đã Dispatch sang Agent-08: {dispatched_count} lệnh."
                )
                return data
            else:
                logger.error(f"[PositionDaemon] Agent-09 trả về lỗi: {res.get('error')}")
                return {"error": res.get("error")}
        except Exception as e:
            logger.error(f"[PositionDaemon] Lỗi nghiêm trọng khi chạy Agent-09: {e}", exc_info=True)
            return {"error": str(e)}

    async def start(self):
        """Vòng lặp chạy nền định kỳ trên PROD."""
        self._running = True
        logger.info("[PositionDaemon] Khởi động Daemon Giám sát Vị thế & Phòng thủ Stop-Loss...")

        while self._running:
            market_state = self.session_mgr.get_market_state()
            is_open = self.session_mgr.is_market_open()

            if not is_open:
                state_str = market_state.value
                _, wait_secs = self.session_mgr.next_state_change()
                logger.info(
                    f"[PositionDaemon] Thị trường đang ở trạng thái '{state_str}'. "
                    f"Nghỉ ngơi, kiểm tra lại sau {min(wait_secs, 60)} giây..."
                )
                await asyncio.sleep(min(wait_secs, 60))
                continue

            # Khi thị trường mở cửa: Thực thi nhịp giám sát 5 phút
            await self.run_single_tick()
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False
        logger.info("[PositionDaemon] Đã dừng Daemon.")


daemon = PositionMonitoringDaemon()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(daemon.start())
