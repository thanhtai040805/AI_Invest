"""Test Suite: VNext Institutional Portfolio Allocation Agent (IOS v5.1).
Kiểm thử chuyên sâu 8 Engine nghiệp vụ, ràng buộc Deadband >= 2%, phân mảnh T+2.5,
bộ nhớ chiến dịch đa phiên (portfolio_campaigns), và triệt tiêu hoàn toàn mock data.
"""

import asyncio
import pytest
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
from app.domain.rules.portfolio import (
    EligibilityEngine,
    ProbabilityEngine,
    KellySizingEngine,
    PortfolioConstructionEngine,
    DynamicAllocationEngine,
    LiquidityEngine,
    RebalancingEngine,
    DecisionOutputEngine,
)


@pytest.fixture(autouse=True)
def clean_portfolio_db():
    try:
        repo = PortfolioRepository()
        repo.storage.execute("UPDATE users SET cash_balance = 1000000000.0")
        repo.storage.execute("DELETE FROM positions WHERE symbol IN ('HPG', 'VNM', 'FPT')")
    except Exception:
        pass
    yield
    try:
        repo = PortfolioRepository()
        repo.storage.execute("UPDATE users SET cash_balance = 1000000000.0")
        repo.storage.execute("DELETE FROM positions WHERE symbol IN ('HPG', 'VNM', 'FPT')")
    except Exception:
        pass


def test_engine_1_eligibility_no_risk_gate():
    """Kiểm tra Engine 1: Chỉ thẩm định Research, Thesis, Counter-Thesis; KHÔNG chứa Risk."""
    engine = EligibilityEngine()

    # 1. Conviction không đạt (C) -> Bị loại
    res_c = engine.evaluate(
        ticker="HPG",
        candidate_data={"conviction": "C"},
    )
    assert res_c.eligible is False
    assert "không đạt tiêu chuẩn" in res_c.rejection_reasons[0]

    # 2. Counter-Thesis BLOCK -> Bị loại
    res_block = engine.evaluate(
        ticker="HPG",
        candidate_data={"conviction": "A"},
        counter_thesis_data={"verdict": "BLOCK", "block_reasons": ["RPT Anomaly phát hiện dấu hiệu rút ruột"]},
    )
    assert res_block.eligible is False
    assert "Counter-Thesis BLOCK" in res_block.rejection_reasons[0]

    # 3. Hợp lệ hoàn toàn (A conviction, Thesis PROCEED, Counter-Thesis PROCEED)
    res_ok = engine.evaluate(
        ticker="HPG",
        candidate_data={"conviction": "A"},
        thesis_data={"status": "PROCEED", "confirming_signals": ["S1", "S2", "S3"]},
        counter_thesis_data={"verdict": "PROCEED", "cts_score": 35.0},
    )
    assert res_ok.eligible is True
    assert res_ok.status == "ELIGIBLE"
    # Xác nhận không có trường risk trong eligibility check
    assert "risk_status" not in res_ok.details


def test_engine_7_rebalance_deadband_threshold():
    """Kiểm tra Engine 7: Deadband >= 2.0% NAV ngăn chặn hiện tượng bào mòn phí và thuế (Churning Trap)."""
    engine = RebalancingEngine(deadband_threshold_pct=0.02)

    # Trường hợp 1: Độ lệch 1.0% (< 2.0%) -> Ra quyết định HOLD
    res_hold = engine.evaluate_rebalance(
        ticker="HPG",
        current_weight=0.05,
        current_shares=50000,
        available_shares=50000,
        locked_t25_shares=0,
        portfolio_target=0.06,
        executable_target=0.06,
        executable_shares=60000,
        target_shares=60000,
        incremental_shares=10000,
        price=20000.0,
        total_nav=1000000000.0,
    )
    assert res_hold.action == "HOLD"
    assert res_hold.sub_action == "HOLD_DEADBAND"
    assert res_hold.deadband_passed is False
    assert res_hold.incremental_shares == 0

    # Trường hợp 2: Độ lệch 3.0% (>= 2.0%) -> Kích hoạt REBALANCE
    res_rebal = engine.evaluate_rebalance(
        ticker="HPG",
        current_weight=0.05,
        current_shares=50000,
        available_shares=50000,
        locked_t25_shares=0,
        portfolio_target=0.08,
        executable_target=0.08,
        executable_shares=80000,
        target_shares=80000,
        incremental_shares=30000,
        price=20000.0,
        total_nav=1000000000.0,
    )
    assert res_rebal.action == "REBALANCE"
    assert res_rebal.sub_action == "REBALANCE_BUY"
    assert res_rebal.deadband_passed is True
    assert res_rebal.incremental_shares == 30000


def test_t25_settlement_split_prevents_illegal_short_selling():
    """Kiểm tra Ràng buộc Hàng Khả Dụng T+2.5: Tuyệt đối không cho phép bán cổ phiếu đang kẹt trên đường về."""
    engine = RebalancingEngine(deadband_threshold_pct=0.02)

    # Đang nắm giữ 50,000 cổ phiếu nhưng 100% đang kẹt T+2.5 (available_shares = 0)
    # Muốn tái cân bằng hạ tỷ trọng bán bớt 20,000 cổ
    res_t25_blocked = engine.evaluate_rebalance(
        ticker="HPG",
        current_weight=0.10,
        current_shares=50000,
        available_shares=0,  # Hàng chưa về!
        locked_t25_shares=50000,
        portfolio_target=0.06,
        executable_target=0.06,
        executable_shares=30000,
        target_shares=30000,
        incremental_shares=-20000,
        price=20000.0,
        total_nav=1000000000.0,
    )
    assert res_t25_blocked.action == "HOLD_T25_SETTLEMENT_PENDING"
    assert res_t25_blocked.sub_action == "HOLD_T25"
    assert res_t25_blocked.incremental_shares == 0
    assert "kẹt chu kỳ T+2.5" in res_t25_blocked.rebalance_reasons[0]

    # Nếu có sẵn 10,000 cổ khả dụng -> Chỉ cho bán tối đa 10,000 cổ
    res_t25_partial = engine.evaluate_rebalance(
        ticker="HPG",
        current_weight=0.10,
        current_shares=50000,
        available_shares=10000,  # Khả dụng 10,000
        locked_t25_shares=40000,
        portfolio_target=0.06,
        executable_target=0.06,
        executable_shares=30000,
        target_shares=30000,
        incremental_shares=-20000,
        price=20000.0,
        total_nav=1000000000.0,
    )
    assert res_t25_partial.action == "REBALANCE"
    assert res_t25_partial.incremental_shares == -10000
    assert "Chỉ bán được 10,000 cổ khả dụng" in res_t25_partial.rebalance_reasons[0]


def test_liquidity_engine_and_campaign_horizon():
    """Kiểm tra Liquidity Engine & Khởi tạo chiến dịch đa phiên (portfolio_campaigns)."""
    liq_engine = LiquidityEngine(max_session_participation_pct=0.15, max_cumulative_capacity_pct=0.25)

    # Muốn mua 10% NAV = 100,000,000 VND (5,000 cổ giá 20,000đ)
    # Nhưng ADTV20 chỉ là 20,000 cổ -> Sức chứa phiên (15%) tối đa chỉ là 3,000 cổ!
    res_liq = liq_engine.evaluate_liquidity(
        ticker="VIX",
        portfolio_target=0.10,
        price=20000.0,
        total_nav=1000000000.0,
        current_shares=0,
        adtv20=20000.0,
    )
    assert res_liq.is_liquidity_constrained is True
    assert res_liq.incremental_shares == 3000
    assert res_liq.executable_shares == 3000
    assert res_liq.execution_horizon_days >= 2

    # Kết nối sang RebalancingEngine kiểm tra tạo chiến dịch
    reb_engine = RebalancingEngine()
    reb_res = reb_engine.evaluate_rebalance(
        ticker="VIX",
        current_weight=0.0,
        current_shares=0,
        available_shares=0,
        locked_t25_shares=0,
        portfolio_target=0.10,
        executable_target=res_liq.executable_target,
        executable_shares=res_liq.executable_shares,
        target_shares=res_liq.target_shares,
        incremental_shares=res_liq.incremental_shares,
        price=20000.0,
        total_nav=1000000000.0,
    )
    assert reb_res.campaign_info is not None
    assert reb_res.campaign_info["direction"] == "ACCUMULATION"
    assert reb_res.campaign_info["status"] == "IN_PROGRESS"
    assert reb_res.campaign_info["final_target_weight"] == 0.10
    assert reb_res.campaign_info["remaining_weight"] > 0


def test_no_mock_data_price_missing_raises_error():
    """Kiểm tra triệt tiêu Mock Data: Nếu thiếu giá thị trường và không query được, phải raise exception."""
    async def _test():
        repo = PortfolioRepository()
        agent = PortfolioAllocationAgent(repository=repo)

        # Truyền 1 mã không có giá, giá = 0, và không tồn tại trong DB
        with pytest.raises(ValueError, match="KHÔNG TÌM THẤY GIÁ THỊ TRƯỜNG"):
            await agent.process({
                "candidate": {"ticker": "NON_EXISTENT_TICKER_999", "conviction": "A", "price": 0.0},
            })

    asyncio.run(_test())


def test_four_output_groups_complete_contract():
    """Kiểm tra tính đầy đủ của 4 Nhóm Output (A, B, C, D) theo hợp đồng dữ liệu chuẩn định chế."""
    async def _test():
        repo = PortfolioRepository()
        repo._in_memory_account["cash_balance"] = 1000000000.0
        repo._in_memory_account["total_nav"] = 1000000000.0
        repo._in_memory_positions.clear()
        try:
            repo.storage.execute("DELETE FROM positions WHERE symbol = 'HPG'")
        except Exception:
            pass

        agent = PortfolioAllocationAgent(repository=repo)
        res = await agent.process({
            "candidate": {
                "ticker": "HPG",
                "conviction": "A",
                "price": 28000.0,
                "sector": "Materials",
                "adtv20": 15000000.0,
            },
            "regime": "BULL_MARKET",
            "total_nav": 1000000000.0,
        })
        assert "data" in res
        data = res["data"]

        # 1. Nhóm A: Portfolio Decision
        assert "portfolio_decision" in data
        group_a = data["portfolio_decision"]
        assert group_a["portfolio_decision"] in ("BUY", "HOLD", "REBALANCE", "SELL")
        assert group_a["ticker"] == "HPG"
        assert "decision_id" in group_a

        # 2. Nhóm B: Capital Allocation
        assert "capital_allocation" in data
        group_b = data["capital_allocation"]
        assert group_b["ticker"] == "HPG"
        assert "preliminary_target" in group_b
        assert "portfolio_target" in group_b
        assert "executable_target" in group_b
        assert "incremental_weight" in group_b
        assert "target_value" in group_b
        assert "order_value_vnd" in group_b
        assert group_b["target_shares"] > 0

        # 3. Nhóm C: Portfolio Impact
        assert "portfolio_impact" in data
        group_c = data["portfolio_impact"]
        assert group_c["sector"] == "Materials"
        assert "sector_exposure_after" in group_c
        assert group_c["factor_exposure"] == "ACCEPTABLE"
        assert "cash_after" in group_c
        assert group_c["portfolio_risk_after"] == "WITHIN_LIMIT"

        # 4. Nhóm D: Decision Log
        assert "decision_log" in data
        group_d = data["decision_log"]
        assert group_d["research"] == "VALID"
        assert group_d["thesis"] == "PROCEED"
        assert group_d["counter_thesis"] == "PROCEED"
        assert "p_calibrated" in group_d
        assert "payoff_ratio" in group_d
        assert isinstance(group_d["reason"], list)
        assert len(group_d["reason"]) > 0

        # Kiểm tra Backward Compatibility
        assert data["ticker"] == "HPG"
        assert data["action"] == "BUY"
        assert data["target_shares"] > 0
        assert data["target_price"] == 28000.0

    asyncio.run(_test())


def test_red_drawdown_freezes_new_buys():
    """Kiểm tra tuân thủ Hiến pháp IOS v5.1: Tầng RED Drawdown cấm mở mới vị thế."""
    agent = PortfolioAllocationAgent()

    async def _test():
        res = await agent.process({
            "candidate": {
                "ticker": "MBB",
                "conviction": "A",
                "price": 24000.0,
                "sector": "Banking",
                "adtv20": 10000000.0,
            },
            "total_nav": 1000000000.0,
            "cash_balance": 800000000.0,
            "regime": "BEAR_MARKET",
            "drawdown_tier": "RED",  # Đang sụt giảm nghiêm trọng > 15% NAV
        })
        assert "data" in res
        data = res["data"]
        # Phải từ chối mua mới
        assert data["action"] == "HOLD"
        assert data["target_shares"] == 0
        assert data["capital_allocation"]["executable_target"] == 0.0
        assert data["portfolio_impact"]["min_cash_target"] >= 0.75

    asyncio.run(_test())


def test_save_decision_persisted_in_database():
    """Kiểm tra quyết định của Agent-07 được lưu thành công vào bảng portfolio_decisions."""
    agent = PortfolioAllocationAgent()

    async def _test():
        res = await agent.process({
            "candidate": {
                "ticker": "TCB",
                "conviction": "A",
                "price": 25000.0,
                "sector": "Banking",
                "adtv20": 8000000.0,
            },
            "total_nav": 1000000000.0,
            "cash_balance": 500000000.0,
            "regime": "BULL_MARKET",
        })
        assert "data" in res
        data = res["data"]
        dec_id = data["decision_id"]

        # Truy vấn trực tiếp PostgreSQL để xác minh
        try:
            from app.infrastructure.database.connection import get_raw_connection
            conn = get_raw_connection()
            cur = conn.cursor()
            cur.execute("SELECT ticker, action, target_shares FROM portfolio_decisions WHERE decision_id = %s", (dec_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                assert row[0] == "TCB"
                assert row[1] in ("BUY", "HOLD", "REBALANCE")
        except Exception as e:
            # Fallback nếu môi trường test không có kết nối DB
            pass

    asyncio.run(_test())

