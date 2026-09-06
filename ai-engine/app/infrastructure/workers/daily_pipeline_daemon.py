"""Daily Pipeline Daemon (IOS v5.1 - Realtime Production Morning Orchestrator Worker).

Chức năng:
- Chạy nền tự động trong tiến trình AI Engine khi deploy (FastAPI lifespan).
- Canh đúng 09:15 hàng ngày (khi phiên khớp lệnh liên tục HOSE bắt đầu sau ATO).
- Nhận diện ngày giao dịch (Thứ 2 - Thứ 6 qua MarketSessionManager).
- Tự động kích hoạt DailyInvestmentPipeline thực thi đồng thời:
    1. Multi-Agent Book (12 Agents sovereign pipeline).
    2. Standalone Pure-ML Book (Hybrid Stacking Ranker channel).
- Đảm bảo Idempotency: Mỗi ngày giao dịch chỉ kích hoạt tự động 1 lần duy nhất (trừ khi có lệnh force manual).
- Cung cấp API status và manual trigger cho Admin dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, time as dt_time
from typing import Any, Dict, Optional

from app.domain.pipeline.daily_pipeline_orchestrator import daily_pipeline, DailyInvestmentPipeline
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager

logger = logging.getLogger("ai_engine.daemon.daily_pipeline")


class DailyPipelineDaemon:
    """Daemon tự động kích hoạt chu trình Daily Investment Pipeline đầu phiên (09:15 sáng)."""

    def __init__(
        self,
        runner: Optional[DailyInvestmentPipeline] = None,
        check_interval_seconds: int = 30,
        trigger_time_str: Optional[str] = None,
    ):
        self.runner = runner or daily_pipeline
        self.interval = check_interval_seconds
        self.session_mgr = MarketSessionManager()
        self._running = False
        self._last_run_date: Optional[str] = None
        self._last_status: str = "IDLE"
        self._last_result: Optional[Dict[str, Any]] = None
        self._task: Optional[asyncio.Task] = None

        # Cấu hình giờ trigger từ biến môi trường (mặc định 09:15)
        raw_time = trigger_time_str or os.getenv("DAILY_PIPELINE_TRIGGER_TIME", "09:15")
        try:
            h, m = raw_time.split(":")
            self.trigger_time = dt_time(int(h), int(m))
        except Exception:
            self.trigger_time = dt_time(9, 15)

    @property
    def status(self) -> Dict[str, Any]:
        """Trạng thái hiện tại của Daily Pipeline Daemon phục vụ Admin API & Monitoring."""
        return {
            "is_running": self._running,
            "target_trigger_time": self.trigger_time.strftime("%H:%M:%S"),
            "last_run_date": self._last_run_date,
            "last_status": self._last_status,
            "last_result_summary": {
                "date": self._last_result.get("date") if self._last_result else None,
                "status": self._last_result.get("status") if self._last_result else None,
                "multi_agent_orders": len(self._last_result.get("multi_agent_instructions", [])) if self._last_result else 0,
                "standalone_ml_orders": len(self._last_result.get("standalone_ml_instructions", [])) if self._last_result else 0,
                "governance_status": self._last_result.get("governance_status") if self._last_result else None,
            } if self._last_result else None,
        }

    async def trigger_manual(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
        candidate_tickers: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Kích hoạt thủ công Daily Pipeline tức thì (qua REST API hoặc Admin CLI)."""
        run_d = target_date or date.today().isoformat()
        logger.info(f"[DailyPipelineDaemon] Nhận lệnh kích hoạt thủ công cho ngày: {run_d} (force={force})")
        self._last_status = "RUNNING_MANUAL"
        try:
            res = await self.runner.run(
                target_date=run_d,
                candidate_tickers=candidate_tickers,
            )
            self._last_run_date = run_d
            self._last_result = res
            self._last_status = res.get("status", "COMPLETED")
            return res
        except Exception as e:
            self._last_status = f"FAILED: {e}"
            logger.error(f"[DailyPipelineDaemon] Lỗi khi chạy manual pipeline: {e}", exc_info=True)
            raise

    async def _check_and_trigger(self) -> None:
        """Kiểm tra điều kiện giờ và ngày giao dịch để tự động kích hoạt."""
        now = datetime.now()
        today_str = now.date().isoformat()

        # 1. Kiểm tra ngày làm việc sàn HOSE (Thứ 2 - Thứ 6, không phải ngày lễ)
        if not self.session_mgr.is_trading_day(now):
            return

        # 2. Kiểm tra giờ đã chạm mốc trigger chưa (09:15)
        current_time = now.time()
        if current_time < self.trigger_time:
            return

        # 3. Kiểm tra tính Idempotent: Đã chạy thành công cho ngày hôm nay chưa
        if self._last_run_date == today_str:
            return

        logger.info(
            f"[DailyPipelineDaemon] ĐÃ ĐẾN GIỜ GIAO DỊCH {self.trigger_time.strftime('%H:%M')} "
            f"({now.strftime('%H:%M:%S')}) NGÀY {today_str}! "
            "Tự động kích hoạt Daily Investment Pipeline (12 Agents + Standalone ML)..."
        )
        self._last_status = "RUNNING_SCHEDULED"
        try:
            res = await self.runner.run(target_date=today_str)
            self._last_run_date = today_str
            self._last_result = res
            self._last_status = res.get("status", "SUCCESS")
            logger.info(
                f"[DailyPipelineDaemon] Pipeline ngày {today_str} hoàn tất thành công: "
                f"Status={self._last_status} | "
                f"Multi-Agent={len(res.get('multi_agent_instructions', []))} lệnh | "
                f"Standalone ML={len(res.get('standalone_ml_instructions', []))} lệnh"
            )
        except Exception as e:
            self._last_status = f"FAILED: {e}"
            logger.error(f"[DailyPipelineDaemon] Lỗi khi chạy scheduled Daily pipeline: {e}", exc_info=True)

    async def start(self) -> None:
        """Bắt đầu vòng lặp chạy nền của Daemon."""
        self._running = True
        logger.info(
            f"[DailyPipelineDaemon] KHỞI ĐỘNG Daily Pipeline Daemon "
            f"(Tự động kích hoạt {self.trigger_time.strftime('%H:%M')} Thứ 2 - Thứ 6)..."
        )

        while self._running:
            try:
                await self._check_and_trigger()
            except Exception as e_tick:
                logger.error(f"[DailyPipelineDaemon] Lỗi trong vòng lặp daemon: {e_tick}", exc_info=True)

            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """Dừng daemon một cách an toàn."""
        self._running = False
        logger.info("[DailyPipelineDaemon] Đã dừng Daily Pipeline Daemon.")


# Singleton instance
daily_daemon = DailyPipelineDaemon()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(daily_daemon.start())
