"""Test Suite: Daily ETL Daemon (18:00 Post-Market Data Ingestion Cron)."""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest

from app.infrastructure.workers.daily_etl_daemon import DailyETLDaemon, etl_daemon, TZ_VN


def test_daily_etl_daemon_initialization():
    """Kiểm tra khởi tạo DailyETLDaemon và cấu hình giờ trigger 18:00."""
    daemon = DailyETLDaemon()
    st = daemon.status

    assert st["is_running"] is False
    assert st["target_trigger_time"] == "18:00:00"
    assert st["last_status"] == "IDLE"
    assert st["last_run_date"] is None


def test_daily_etl_daemon_trading_day_check():
    """Kiểm tra daemon lọc ngày làm việc chuẩn HOSE."""
    from app.domain.pipeline.daily_etl import is_trading_day
    from datetime import date

    # Saturday
    assert is_trading_day(date(2026, 8, 29)) is False
    # Sunday
    assert is_trading_day(date(2026, 8, 30)) is False
    # Monday
    assert is_trading_day(date(2026, 8, 24)) is True


@pytest.mark.anyio
async def test_daily_etl_daemon_manual_trigger(monkeypatch):
    """Kiểm tra trigger_manual hoạt động đúng quy trình."""
    daemon = DailyETLDaemon()

    # Mock pipeline.run to return instant mock result
    async def mock_run(trade_date=None, include_news=False, include_financials=False):
        return {
            "status": "SUCCESS",
            "trade_date": str(trade_date),
            "steps": {"ohlcv": "OK", "technicals": "OK"},
        }

    monkeypatch.setattr(daemon.pipeline, "run", mock_run)

    res = await daemon.trigger_manual(target_date="2026-08-24")
    assert res["status"] == "SUCCESS"
    assert res["trade_date"] == "2026-08-24"

    st = daemon.status
    assert st["last_run_date"] == "2026-08-24"
    assert st["last_status"] == "SUCCESS"
