"""Test Suite: Daily ETL Daemon (15:00 Post-Market Data Ingestion Cron)."""

import asyncio
from datetime import datetime, timezone, timedelta
import pytest

from app.infrastructure.workers.daily_etl_daemon import DailyETLDaemon, etl_daemon, TZ_VN
from app.infrastructure.workers import daily_etl_daemon as daemon_module
from app.domain.pipeline import daily_etl as daily_etl_module


def test_daily_etl_daemon_initialization():
    """Kiểm tra khởi tạo DailyETLDaemon và cấu hình giờ trigger 15:00."""
    daemon = DailyETLDaemon()
    st = daemon.status

    assert st["is_running"] is False
    assert st["target_trigger_time"] == "15:00:00"
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


@pytest.mark.anyio
async def test_scheduled_etl_waits_for_candles_and_retries_every_15_minutes(monkeypatch):
    clock = {"now": datetime(2026, 9, 29, 14, 59, tzinfo=TZ_VN)}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(daemon_module, "datetime", FrozenDateTime)
    daemon = DailyETLDaemon()
    calls = []
    statuses = ["WAITING_FOR_EOD_DATA", "SUCCESS"]

    class Pipeline:
        async def run(self, trade_date=None):
            calls.append(trade_date)
            return {"status": statuses.pop(0)}

    daemon.pipeline = Pipeline()

    await daemon._check_and_trigger()
    assert calls == []

    clock["now"] = datetime(2026, 9, 29, 15, 0, tzinfo=TZ_VN)
    await daemon._check_and_trigger()
    assert len(calls) == 1
    assert daemon.status["last_run_date"] is None
    assert daemon.status["last_status"] == "WAITING_FOR_EOD_DATA"

    clock["now"] = datetime(2026, 9, 29, 15, 14, tzinfo=TZ_VN)
    await daemon._check_and_trigger()
    assert len(calls) == 1

    clock["now"] = datetime(2026, 9, 29, 15, 15, tzinfo=TZ_VN)
    await daemon._check_and_trigger()
    assert len(calls) == 2
    assert daemon.status["last_run_date"] == "2026-09-29"
    assert daemon.status["last_status"] == "SUCCESS"

    clock["now"] = datetime(2026, 9, 29, 15, 16, tzinfo=TZ_VN)
    await daemon._check_and_trigger()
    assert len(calls) == 2


@pytest.mark.anyio
async def test_daily_etl_does_not_run_derived_steps_without_today_candles(monkeypatch):
    pipeline = daily_etl_module.DailyETLPipeline()
    ran = []

    async def no_candles():
        ran.append("ohlcv")
        return {"target_rows": 0}

    async def must_not_run():
        ran.append("derived")
        return {}

    monkeypatch.setattr(pipeline, "step_ohlcv_backfill", no_candles)
    monkeypatch.setattr(pipeline, "step_technical_indicators", must_not_run)
    monkeypatch.setattr(daily_etl_module, "set_running", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daily_etl_module, "set_failed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(daily_etl_module, "set_completed", lambda *_args, **_kwargs: None)

    result = await pipeline.run(trade_date=datetime(2026, 9, 29).date())

    assert result["status"] == "WAITING_FOR_EOD_DATA"
    assert ran == ["ohlcv"]
