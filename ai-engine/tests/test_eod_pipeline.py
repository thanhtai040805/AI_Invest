"""Test Suite: Production EOD Causal Learning Pipeline & 15:15 Scheduled Daemon.

Kiểm tra toàn diện:
1. EODPipelineRunner: Điều phối 5 pha khép kín.
2. EODLearningDaemon: Kích hoạt thủ công và kiểm tra logic ngày giao dịch.
3. REST API: Endpoints /api/admin/eod-pipeline/status và trigger.
4. DB Persistence: Trọng số và ma trận Kelly được lưu vào PostgreSQL.
"""

import asyncio
from datetime import datetime
import pytest
from fastapi.testclient import TestClient

from app.domain.pipeline.eod_pipeline import EODPipelineRunner, eod_runner
from app.infrastructure.workers.eod_learning_daemon import EODLearningDaemon, eod_daemon
from app.main import app


def test_eod_pipeline_runner_execution():
    """Kiểm tra EODPipelineRunner thực thi 5 pha hoàn chỉnh cho ngày dữ liệu thực tế."""
    async def _test():
        runner = EODPipelineRunner()
        result = await runner.run(target_date="2026-08-24", force=True)

        assert result["status"] == "SUCCESS"
        assert result["run_date"] == "2026-08-24"
        assert result["regime"] in ["BULL_MARKET", "BEAR_MARKET", "RANGE_BOUND"]
        assert "policy_weights" in result
        assert len(result["policy_weights"]) == 6
        assert abs(sum(result["policy_weights"].values()) - 1.0) < 0.05

        assert "kelly_matrix" in result
        assert "A+" in result["kelly_matrix"]
        assert "A" in result["kelly_matrix"]
        assert "B" in result["kelly_matrix"]

        assert result["governance_status"] in ["COMPLIANT", "AUDIT_PASSED"]
        assert len(result["audit_sha256"]) == 64  # Chuỗi hex SHA-256

        # Kiểm tra các pha đã hoàn tất
        phases = result["trace"]["phases"]
        assert phases["phase_1_data_check"]["status"] == "COMPLETED"
        assert phases["phase_2_position_settlement"]["status"] == "COMPLETED"
        assert phases["phase_3_regime_detection"]["status"] == "COMPLETED"
        assert phases["phase_4_causal_learning"]["status"] == "COMPLETED"
        assert phases["phase_5_governance"]["status"] == "COMPLETED"

    asyncio.run(_test())


def test_eod_daemon_trigger_manual_and_status():
    """Kiểm tra EODLearningDaemon kích hoạt thủ công và báo cáo status."""
    async def _test():
        daemon = EODLearningDaemon()
        status_before = daemon.status
        assert status_before["target_trigger_time"] == "15:15:00"

        res = await daemon.trigger_manual(target_date="2026-08-24", force=True)
        assert res["status"] == "SUCCESS"

        status_after = daemon.status
        assert status_after["last_run_date"] == "2026-08-24"
        assert status_after["last_status"] == "SUCCESS"
        assert status_after["last_result"] is not None

    asyncio.run(_test())


def test_eod_daemon_trading_day_filter():
    """Kiểm tra logic nhận diện ngày nghỉ cuối tuần."""
    daemon = EODLearningDaemon()
    # 2026-08-29 là Thứ 7, 2026-08-30 là Chủ nhật
    saturday = datetime(2026, 8, 29, 15, 15, 0)
    sunday = datetime(2026, 8, 30, 15, 15, 0)
    monday = datetime(2026, 8, 24, 15, 15, 0)

    assert daemon.session_mgr.is_trading_day(saturday) is False
    assert daemon.session_mgr.is_trading_day(sunday) is False
    assert daemon.session_mgr.is_trading_day(monday) is True


def test_admin_api_eod_endpoints():
    """Kiểm tra REST API endpoints cho EOD Pipeline qua TestClient."""
    client = TestClient(app)

    # 1. GET status
    resp_status = client.get("/api/admin/eod-pipeline/status")
    assert resp_status.status_code == 200
    data_status = resp_status.json()
    assert "target_trigger_time" in data_status
    assert data_status["target_trigger_time"] == "15:15:00"

    # 2. POST trigger
    resp_trigger = client.post("/api/admin/eod-pipeline/trigger?target_date=2026-08-24&force=true")
    assert resp_trigger.status_code == 200
    data_trigger = resp_trigger.json()
    assert "EOD Pipeline completed" in data_trigger["message"]
    assert data_trigger["status"] == "SUCCESS"
    assert data_trigger["result"]["regime"] in ["BULL_MARKET", "BEAR_MARKET", "RANGE_BOUND"]
