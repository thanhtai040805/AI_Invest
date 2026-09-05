"""EOD Learning Daemon (IOS v5.1 - Realtime Production Scheduled Worker).

Chức năng:
- Chạy nền tự động trong tiến trình AI Engine.
- Canh đúng 15:15 hàng ngày (sau khi phiên ATC sàn HOSE kết thúc và nến EOD hoàn tất).
- Nhận diện ngày giao dịch (Thứ 2 - Thứ 6 qua MarketSessionManager).
- Tự động kích hoạt EODPipelineRunner thực thi 5 pha học nhân quả và hiệu chuẩn ma trận Kelly.
- Đảm bảo Idempotency: Không bao giờ chạy trùng lặp 2 lần cho cùng một ngày giao dịch (trừ khi có lệnh force).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dt_time
from typing import Any, Dict, Optional

from app.domain.pipeline.eod_pipeline import eod_runner, EODPipelineRunner
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager

logger = logging.getLogger("ai_engine.daemon.eod_learning")


class EODLearningDaemon:
    """Daemon tự động kích hoạt Causal Learning cuối phiên (15:15 EOD Cron)."""

    TRIGGER_TIME = dt_time(15, 15)  # 15:15 hàng ngày

    def __init__(
        self,
        runner: Optional[EODPipelineRunner] = None,
        check_interval_seconds: int = 30,
    ):
        self.runner = runner or eod_runner
        self.interval = check_interval_seconds
        self.session_mgr = MarketSessionManager()
        self._running = False
        self._last_run_date: Optional[str] = None
        self._last_status: str = "IDLE"
        self._task: Optional[asyncio.Task] = None

    @property
    def status(self) -> Dict[str, Any]:
        """Trạng thái hiện tại của EOD Daemon phục vụ API & Monitoring."""
        return {
            "is_running": self._running,
            "target_trigger_time": "15:15:00",
            "last_run_date": self._last_run_date or self.runner.last_run_date,
            "last_status": self._last_status,
            "last_result": self.runner.last_result,
        }

    async def trigger_manual(self, target_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        """Kích hoạt thủ công EOD pipeline tức thì (qua REST API hoặc CLI)."""
        run_d = target_date or date.today().isoformat()
        logger.info(f"[EODDaemon] Nhận lệnh kích hoạt thủ công cho ngày: {run_d} (force={force})")
        self._last_status = "RUNNING_MANUAL"
        res = await self.runner.run(target_date=run_d, force=force)
        self._last_run_date = run_d
        self._last_status = res.get("status", "COMPLETED")
        return res

    async def _check_and_trigger(self) -> None:
        """Kiểm tra điều kiện giờ và ngày giao dịch để kích hoạt."""
        now = datetime.now()
        today_str = now.date().isoformat()

        # 1. Kiểm tra ngày làm việc sàn HOSE
        if not self.session_mgr.is_trading_day(now):
            return

        # 2. Kiểm tra giờ đã chạm 15:15 chưa
        current_time = now.time()
        if current_time < self.TRIGGER_TIME:
            return

        # 3. Kiểm tra tính Idempotent: Đã chạy thành công cho ngày hôm nay chưa
        if self._last_run_date == today_str:
            return

        logger.info(
            f"[EODDaemon] ĐÃ ĐẾN 15:15 ({now.strftime('%H:%M:%S')}) NGÀY GIAO DỊCH {today_str}! "
            "Tự động kích hoạt EOD Causal Learning Pipeline..."
        )
        self._last_status = "RUNNING_SCHEDULED"
        try:
            res = await self.runner.run(target_date=today_str, force=False)
            self._last_run_date = today_str
            self._last_status = res.get("status", "SUCCESS")
            logger.info(f"[EODDaemon] Pipeline 15:15 ngày {today_str} hoàn tất với trạng thái: {self._last_status}")
        except Exception as e:
            self._last_status = f"FAILED: {e}"
            logger.error(f"[EODDaemon] Lỗi khi chạy scheduled EOD pipeline: {e}", exc_info=True)

    async def start(self) -> None:
        """Bắt đầu vòng lặp chạy nền của Daemon."""
        self._running = True
        logger.info("[EODDaemon] KHỞI ĐỘNG EOD Learning Daemon (Tự động kích hoạt 15:15 Thứ 2 - Thứ 6)...")

        while self._running:
            try:
                await self._check_and_trigger()
            except Exception as e_tick:
                logger.error(f"[EODDaemon] Lỗi trong vòng lặp daemon: {e_tick}", exc_info=True)

            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """Dừng daemon một cách an toàn."""
        self._running = False
        logger.info("[EODDaemon] Đã dừng EOD Learning Daemon.")


# Singleton instance
eod_daemon = EODLearningDaemon()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(eod_daemon.start())
