"""Daily ETL Daemon (IOS v5.1 - Post-Market Data Ingestion Worker).

Chức năng:
- Chạy nền tự động trong tiến trình AI Engine.
- Canh đúng 18:00 hàng ngày (sau khi các công ty chứng khoán & sở giao dịch chốt nến ngày).
- Nhận diện ngày giao dịch (Thứ 2 - Thứ 6).
- Tự động kích hoạt DailyETLPipeline thực thi nạp OHLCV, tính technical indicators,
  volatility, foreign flow, insider trades và pre-compute factor scores F1-F6.
- Đảm bảo Idempotency: Không bao giờ chạy trùng lặp 2 lần cho cùng một ngày giao dịch.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, Optional

from app.domain.pipeline.daily_etl import DailyETLPipeline, is_trading_day, TZ_VN

logger = logging.getLogger("ai_engine.daemon.daily_etl")


class DailyETLDaemon:
    """Daemon tự động kích hoạt nạp dữ liệu cuối ngày (18:00 Post-Market ETL Cron)."""

    TRIGGER_TIME = dt_time(18, 0)  # 18:00 hàng ngày

    def __init__(
        self,
        pipeline: Optional[DailyETLPipeline] = None,
        check_interval_seconds: int = 30,
    ):
        self.pipeline = pipeline or DailyETLPipeline()
        self.interval = check_interval_seconds
        self._running = False
        self._last_run_date: Optional[str] = None
        self._last_status: str = "IDLE"
        self._last_result: Optional[Dict[str, Any]] = None

    @property
    def status(self) -> Dict[str, Any]:
        """Trạng thái hiện tại của Daily ETL Daemon phục vụ API & Monitoring."""
        return {
            "is_running": self._running,
            "target_trigger_time": "18:00:00",
            "last_run_date": self._last_run_date,
            "last_status": self._last_status,
            "last_result": self._last_result,
        }

    async def trigger_manual(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """Kích hoạt thủ công Daily ETL tức thì (qua REST API hoặc CLI)."""
        run_d = date.fromisoformat(target_date) if target_date else datetime.now(TZ_VN).date()
        today_str = run_d.isoformat()
        logger.info(f"[DailyETLDaemon] Nhận lệnh kích hoạt thủ công cho ngày: {today_str}")
        self._last_status = "RUNNING_MANUAL"
        res = await self.pipeline.run(trade_date=run_d)
        self._last_run_date = today_str
        self._last_status = res.get("status", "COMPLETED")
        self._last_result = res
        return res

    async def _check_and_trigger(self) -> None:
        """Kiểm tra điều kiện giờ và ngày giao dịch để kích hoạt."""
        now_vn = datetime.now(TZ_VN)
        today = now_vn.date()
        today_str = today.isoformat()

        # 1. Kiểm tra ngày làm việc sàn HOSE
        if not is_trading_day(today):
            return

        # 2. Kiểm tra giờ đã chạm 18:00 chưa
        current_time = now_vn.time()
        if current_time < self.TRIGGER_TIME:
            return

        # 3. Kiểm tra tính Idempotent: Đã chạy cho ngày hôm nay chưa
        if self._last_run_date == today_str:
            return

        logger.info(
            f"[DailyETLDaemon] ĐÃ ĐẾN 18:00 ({now_vn.strftime('%H:%M:%S')}) NGÀY GIAO DỊCH {today_str}! "
            "Tự động kích hoạt Daily ETL Data Ingestion Pipeline..."
        )
        self._last_status = "RUNNING_SCHEDULED"
        try:
            res = await self.pipeline.run(trade_date=today)
            self._last_run_date = today_str
            self._last_status = res.get("status", "SUCCESS")
            self._last_result = res
            logger.info(f"[DailyETLDaemon] Pipeline 18:00 ngày {today_str} hoàn tất với trạng thái: {self._last_status}")
        except Exception as e:
            self._last_status = f"FAILED: {e}"
            logger.error(f"[DailyETLDaemon] Lỗi khi chạy scheduled Daily ETL pipeline: {e}", exc_info=True)

    async def start(self) -> None:
        """Bắt đầu vòng lặp chạy nền của Daemon."""
        self._running = True
        logger.info("[DailyETLDaemon] KHỞI ĐỘNG Daily ETL Daemon (Tự động kích hoạt 18:00 Thứ 2 - Thứ 6)...")

        while self._running:
            try:
                await self._check_and_trigger()
            except Exception as e_tick:
                logger.error(f"[DailyETLDaemon] Lỗi trong vòng lặp daemon: {e_tick}", exc_info=True)

            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """Dừng daemon một cách an toàn."""
        self._running = False
        logger.info("[DailyETLDaemon] Đã dừng Daily ETL Daemon.")


# Singleton instance
etl_daemon = DailyETLDaemon()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(etl_daemon.start())
