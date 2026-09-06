"""Test Suite: Production Daily Pipeline Scheduled Worker & Daemon (09:15 Morning Cron).

Kiểm tra toàn diện:
1. DailyPipelineDaemon: Cấu hình mặc định (09:15), lọc ngày giao dịch (Thứ 2 - Thứ 6).
2. Kích hoạt thủ công qua daemon: trigger_manual().
3. Idempotency & Status Reporting.
4. REST API Endpoints: /api/admin/daily-pipeline/status và /api/admin/daily-pipeline/trigger.
"""

import asyncio
from datetime import datetime
import pytest
from fastapi.testclient import TestClient

from app.infrastructure.workers.daily_pipeline_daemon import DailyPipelineDaemon, daily_daemon
from app.main import app


def test_daily_daemon_initialization_and_status():
    """Kiểm tra khởi tạo DailyPipelineDaemon và cấu hình giờ trigger 09:15."""
    daemon = DailyPipelineDaemon()
    st = daemon.status

    assert st["is_running"] is False
    assert st["target_trigger_time"] == "09:15:00"
    assert st["last_status"] == "IDLE"


def test_daily_daemon_trading_day_filter():
    """Kiểm tra daemon chỉ nhận diện ngày giao dịch hợp lệ (Thứ 2 - Thứ 6)."""
    daemon = DailyPipelineDaemon()
    saturday = datetime(2026, 8, 29, 9, 15, 0)
    sunday = datetime(2026, 8, 30, 9, 15, 0)
    monday = datetime(2026, 8, 24, 9, 15, 0)

    assert daemon.session_mgr.is_trading_day(saturday) is False
    assert daemon.session_mgr.is_trading_day(sunday) is False
    assert daemon.session_mgr.is_trading_day(monday) is True


def test_daily_daemon_manual_trigger():
    """Kiểm tra trigger_manual của DailyPipelineDaemon điều phối cả Multi-Agent và Standalone ML."""
    async def _test():
        daemon = DailyPipelineDaemon()
        res = await daemon.trigger_manual(
            target_date="2026-08-24",
            force=True,
            candidate_tickers=["FPT", "MWG", "TCB"],
        )

        assert res["status"] == "SUCCESS"
        assert res["date"] == "2026-08-24"
        assert "multi_agent_instructions" in res
        assert "standalone_ml_instructions" in res
        assert res["governance_status"] in ["COMPLIANT", "AUDIT_PASSED"]

        st = daemon.status
        assert st["last_run_date"] == "2026-08-24"
        assert st["last_status"] == "SUCCESS"
        assert st["last_result_summary"] is not None
        assert st["last_result_summary"]["date"] == "2026-08-24"

    asyncio.run(_test())


def test_admin_api_daily_pipeline_endpoints():
    """Kiểm tra REST API endpoints cho Daily Pipeline qua TestClient."""
    client = TestClient(app)

    # 1. GET status
    resp_status = client.get("/api/admin/daily-pipeline/status")
    assert resp_status.status_code == 200
    data_status = resp_status.json()
    assert "target_trigger_time" in data_status
    assert data_status["target_trigger_time"] == "09:15:00"

    # 2. POST trigger
    resp_trigger = client.post("/api/admin/daily-pipeline/trigger?target_date=2026-08-24&force=true")
    assert resp_trigger.status_code == 200
    data_trigger = resp_trigger.json()
    assert "Daily Pipeline completed" in data_trigger["message"]
    assert data_trigger["status"] == "SUCCESS"
    assert "multi_agent_orders" in data_trigger
    assert "standalone_ml_orders" in data_trigger
    assert "governance_status" in data_trigger
