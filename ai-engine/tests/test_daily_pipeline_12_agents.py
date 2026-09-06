"""Comprehensive Test Suite: 12-Agent Daily Autonomous Investment Pipeline (IOS v5.1).

Kiểm tra toàn diện:
1. Module Exports & Pipeline Aggregation: Cả 3 pipelines đều có thể import từ `app.domain.pipeline`.
2. Backward Compatibility: Import từ `app.application.use_cases.daily_pipeline_orchestrator` và `app.infrastructure.data_pipelines.daily_etl` vẫn hoạt động 100%.
3. Full 12-Agent Pipeline Execution: Chạy chu trình khép kín 12 Pha trên ngày giao dịch thực tế.
4. Bear Defense Mode: Tự động khóa 100% tiền mặt khi thị trường sập gãy.
5. Governance Audit Trail: Sổ cái kiểm toán SHA-256 được Agent-11 thẩm định và cấp chứng nhận COMPLIANT.
"""

import asyncio
from datetime import date
import pytest

from app.domain.pipeline import (
    DailyInvestmentPipeline,
    ExecutionMode,
    pipeline,
    EODPipelineRunner,
    DailyETLPipeline,
)
from app.application.use_cases.daily_pipeline_orchestrator import (
    pipeline as compat_pipeline,
    DailyInvestmentPipeline as CompatDailyInvestmentPipeline,
)
from app.infrastructure.data_pipelines.daily_etl import (
    DailyETLPipeline as CompatDailyETLPipeline,
)


def test_pipeline_exports_and_backward_compatibility():
    """Kiểm tra xuất khẩu tập trung tại app.domain.pipeline và tương thích ngược tại các vị trí cũ."""
    # Kiểm tra export tại app.domain.pipeline
    assert DailyInvestmentPipeline is not None
    assert pipeline is not None
    assert EODPipelineRunner is not None
    assert DailyETLPipeline is not None

    # Kiểm tra backward compatibility wrappers
    assert compat_pipeline is not None
    assert CompatDailyInvestmentPipeline is DailyInvestmentPipeline
    assert CompatDailyETLPipeline is DailyETLPipeline


def test_12_agent_pipeline_bull_execution():
    """Kiểm tra DailyInvestmentPipeline điều phối toàn vẹn chuỗi 12 Agents cho ngày thị trường Bull/Normal."""
    async def _test():
        runner = DailyInvestmentPipeline(
            multi_agent_mode=ExecutionMode.SHADOW_RUNNER.value,
            standalone_ml_mode=ExecutionMode.LIVE.value,
        )

        res = await runner.run(
            target_date="2026-08-28",
            current_nav=1_000_000_000.0,
            standalone_nav=500_000_000.0,
            candidate_tickers=["FPT", "VNM", "HPG"],
            max_candidates=2,
        )

        assert res["status"] == "SUCCESS"
        assert res["date"] == "2026-08-28"
        assert res["regime"] in ["BULL_MARKET", "RANGE_BOUND", "BULL_EXPANSION"]
        assert res["cash_ratio"] <= 0.20  # Thị trường tăng trưởng giữ tỷ lệ tiền mặt thấp
        assert res["governance_status"] in ["COMPLIANT", "AUDIT_PASSED"]
        assert len(res["audit_sha256"]) == 64

        # Kiểm tra dấu vết 12 pha
        phases = res["trace"]["phases"]
        assert "phase_1_market_surveillance" in phases
        assert phases["phase_1_market_surveillance"]["status"] == "COMPLETED"
        assert "phase_2_reinforcement_learning" in phases
        assert phases["phase_2_reinforcement_learning"]["status"] == "COMPLETED"
        assert "phase_3_universe_discovery" in phases
        assert phases["phase_3_universe_discovery"]["status"] == "COMPLETED"
        assert "phase_12_system_governance" in phases
        assert phases["phase_12_system_governance"]["status"] == "COMPLETED"

        # Kiểm tra danh sách chỉ thị lệnh phát sinh
        orders_ma = res.get("multi_agent_instructions", [])
        for order in orders_ma:
            assert order["shares"] > 0
            assert order["price"] > 0
            assert order["target_weight_pct"] <= 0.15  # Tuân thủ nghiêm ngặt Điều 4 Hard Law

        orders_sa = res.get("standalone_ml_instructions", [])
        for order in orders_sa:
            assert order["shares"] > 0
            assert order["price"] > 0
            assert order["target_weight_pct"] <= 0.20

    asyncio.run(_test())


def test_12_agent_pipeline_bear_defense_mode():
    """Kiểm tra tính năng bảo vệ vốn tối cao: Tự động ngắt và giữ 100% tiền mặt khi thị trường sụp đổ."""
    async def _test():
        runner = DailyInvestmentPipeline()

        # Phiên 2022-05-13 là đỉnh điểm đợt sập Bear Market lịch sử
        res = await runner.run(
            target_date="2022-05-13",
            current_nav=1_000_000_000.0,
            candidate_tickers=["FPT", "VNM", "HPG"],
        )

        # Khóa toàn bộ mua mới và duy trì 100% tiền mặt
        assert res["cash_ratio"] == 1.0
        assert res["status"] in ["BEAR_DEFENSE_100PCT_CASH", "SUCCESS"]
        assert len(res["multi_agent_instructions"]) == 0
        assert len(res["standalone_ml_instructions"]) == 0

    asyncio.run(_test())
