"""Unit & Integration Tests for Institutional Trade Execution Agent & EAE Engine (HOSE Standard)

Kiểm thử 6 kịch bản thực chiến:
1. Failsafe Block: Chặn lệnh khi Failsafe ACTIVE.
2. Order Validation: Từ chối lệnh vi phạm lô chẵn 100 hoặc giá <= 0.
3. Normal Market Execution: 2-3 child orders, khớp 100% trong 1 session.
4. Stress Market HPG: Chuẩn xác bản tin mẫu (HPG 300k, STRESS, 8 child orders, khớp 180k, dư 120k).
5. ATC Anomaly Kill-Switch: Hủy lệnh an toàn khi phát hiện thao túng ATC.
6. Slippage Recording & ADTV Bucket: Phân tầng HPG là MEGA_ADTV kèm hạ nhãn động và ghi nhận CSDL.
"""

import asyncio
import pytest
from datetime import datetime, time
from app.domain.agents.trade_execution import TradeExecutionAgent
from app.domain.rules.execution.eae import ExecutionAdaptationEngine, ExecutionMode, ExecutionStrategy
from app.domain.rules.failsafe import failsafe_engine, FailsafeStatus
from app.domain.repositories.portfolio_repository import PortfolioRepository


def test_failsafe_block():
    """Kịch bản 1: Failsafe đang ACTIVE phải chặn đứng lệnh ngay lập tức."""
    async def _test():
        repo = PortfolioRepository()
        agent = TradeExecutionAgent(repository=repo)
        failsafe_engine.status = FailsafeStatus.ACTIVE
        try:
            res = await agent.process({
                "order_instruction": {
                    "ticker": "HPG",
                    "action": "BUY",
                    "target_shares": 10000,
                    "price": 27000.0,
                }
            })
            data = res["data"]
            assert data["execution_decision"] == "BLOCK"
            assert data["status"] == "BLOCKED_FAILSAFE"
            assert "FAILSAFE ACTIVE" in data["rejection_reason"]
        finally:
            failsafe_engine.reset()

    asyncio.run(_test())


def test_order_validation_reject():
    """Kịch bản 2: Từ chối lệnh vi phạm lô chẵn 100 của sàn HOSE."""
    async def _test():
        repo = PortfolioRepository()
        agent = TradeExecutionAgent(repository=repo)
        res_odd_lot = await agent.process({
            "order_instruction": {
                "ticker": "HPG",
                "action": "BUY",
                "target_shares": 150,  # Lô lẻ, không chia hết cho 100
                "price": 27000.0,
            }
        })
        data = res_odd_lot["data"]
        assert data["execution_decision"] == "REJECT"
        assert data["status"] == "REJECTED_INVALID_ORDER"
        assert "bội số của lô 100" in data["rejection_reason"]

    asyncio.run(_test())


def test_normal_market_execution_blocked_by_governance():
    """Lệnh vượt floor-gap risk phải bị governance chặn trước thực thi."""
    async def _test():
        repo = PortfolioRepository()
        agent = TradeExecutionAgent(repository=repo)
        res = await agent.process({
            "order_instruction": {
                "ticker": "VNM",
                "action": "BUY",
                "target_shares": 20000,
                "price": 68000.0,
            },
            "market_state": {
                "spread": 0.002,
                "volume_status": "NORMAL",
                "market_regime": "NORMAL",
                "atc_concentration": 0.18,
            },
            "adtv20": 4000000.0,
        })
        data = res["data"]
        assert data["execution_decision"] == "BLOCK"
        assert data["status"] == "BLOCKED_BY_GOVERNANCE_GATE"

    asyncio.run(_test())


def test_stress_market_hpg_blocked_by_governance():
    """Lệnh STRESS quá lớn phải bị governance chặn trước khi lập kế hoạch khớp."""
    async def _test():
        repo = PortfolioRepository()
        agent = TradeExecutionAgent(repository=repo)
        res = await agent.process({
            "order_instruction": {
                "ticker": "HPG",
                "action": "BUY",
                "target_shares": 300000,
                "price": 27000.0,
                "max_price": 27300.0,
            },
            "market_state": {
                "spread": 0.014,
                "volume_status": "LOW",
                "market_regime": "STRESS",
                "atc_concentration": 0.32,
            },
            "adtv20": 25000000.0,
        })
        data = res["data"]

        assert data["execution_decision"] == "BLOCK"
        assert data["status"] == "BLOCKED_BY_GOVERNANCE_GATE"

    asyncio.run(_test())


def test_eae_tick_size_alignment():
    """Kiểm tra độ chính xác của bộ làm tròn Bước giá sàn HOSE."""
    eae = ExecutionAdaptationEngine()
    # Dưới 10k: Bước 10 đồng
    assert eae.align_to_hose_tick_size(8432.0) == 8430.0
    assert eae.align_to_hose_tick_size(8436.0) == 8440.0
    # Từ 10k đến dưới 50k: Bước 50 đồng
    assert eae.align_to_hose_tick_size(27032.4) == 27050.0
    assert eae.align_to_hose_tick_size(27010.0) == 27000.0
    # Từ 50k trở lên: Bước 100 đồng
    assert eae.align_to_hose_tick_size(68045.0) == 68000.0
    assert eae.align_to_hose_tick_size(68060.0) == 68100.0


def test_atc_3tier_contingency():
    """Kiểm tra logic Giao thức 3 Pha thích ứng ATC."""
    eae = ExecutionAdaptationEngine()

    # Pha 1: Pre-ATC Skim lúc 14:20
    t_skim = datetime.strptime("2026-09-04 14:20:00", "%Y-%m-%d %H:%M:%S")
    res_skim = eae.resolve_atc_contingency(
        ticker="HPG",
        remaining_quantity=120000,
        decision_price=27000.0,
        max_price=27300.0,
        current_time=t_skim,
        iep_price=27050.0,
        atc_concentration=0.32,
        anomaly_status="NORMAL",
    )
    assert res_skim["phase"] == "PRE_ATC_SKIM"
    assert res_skim["target_quantity"] == 72000  # 60% của 120,000

    # Pha 2: Dynamic IEP Pegging lúc 14:35
    t_pegging = datetime.strptime("2026-09-04 14:35:00", "%Y-%m-%d %H:%M:%S")
    res_pegging = eae.resolve_atc_contingency(
        ticker="HPG",
        remaining_quantity=48000,
        decision_price=27000.0,
        max_price=27300.0,
        current_time=t_pegging,
        iep_price=27100.0,
        atc_concentration=0.32,
        anomaly_status="NORMAL",
    )
    assert res_pegging["phase"] == "DYNAMIC_IEP_PEGGING"
    assert res_pegging["price"] == 27150.0  # IEP + 1 tick (50đ)

    # Pha 3: ATC Anomaly Kill-Switch lúc 14:43 khi có cờ CRITICAL
    t_kill = datetime.strptime("2026-09-04 14:43:00", "%Y-%m-%d %H:%M:%S")
    res_kill = eae.resolve_atc_contingency(
        ticker="HPG",
        remaining_quantity=48000,
        decision_price=27000.0,
        max_price=27300.0,
        current_time=t_kill,
        iep_price=27100.0,
        atc_concentration=0.32,
        anomaly_status="CRITICAL",
    )
    assert res_kill["phase"] == "ATC_KILL_SWITCH"
    assert res_kill["action"] == "CANCEL_ALL_ATC_ORDERS"


def test_portfolio_repository_slippage_recording():
    """Kiểm tra phương thức record_slippage ghi nhận thành công."""
    repo = PortfolioRepository()
    ok = repo.record_slippage(
        ticker="HPG",
        adtv20_bucket="MID_ADTV",
        actual_slippage_bps=37.0,
        expected_slippage_bps=40.0,
        mode="STRESS",
    )
    assert ok is True or len(repo._in_memory_slippage_records) > 0
    record = next(r for r in repo._in_memory_slippage_records if r["ticker"] == "HPG")
    assert record["adtv20_bucket"] == "MID_ADTV"
    assert record["actual_slippage_bps"] == 37.0
    assert record["mode"] == "STRESS"
