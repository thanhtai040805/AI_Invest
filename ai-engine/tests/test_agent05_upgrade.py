"""AGENT-05: Counter Thesis Agent Comprehensive Upgrade & Verification Test Suite (IOS v5.1)

Kiểm thử toàn diện Agent-05 (Devil's Advocate):
1. Phán quyết PROCEED cho cổ phiếu rủi ro thấp (CTS 0 - 30).
2. Phán quyết CONDITIONAL kèm Ràng buộc Thực thi ExecutionConstraints (CTS 31 - 60).
3. Phán quyết BLOCK khi rủi ro tổng hợp cao (CTS > 60).
4. Phán quyết BLOCK tuyệt đối khi vi phạm Hard Law GIL CATASTROPHIC (CTS = 100, Zero Exception).
5. Mô hình phi tuyến quét rủi ro cộng hưởng (ML Interaction Multiplier).
6. Ngoại lệ Bắt đáy Khoa học Capitulation Rebound (Bẫy 3: Regime Multiplier 1.1x & Giải ngân 3 đợt).
7. Kiểm tra Vi phạm Hard Law Điều 3 (Rule of Three Violation -> BLOCK).
8. Lưu trữ State CSDL (`counter_thesis_verdicts`) và Audit Trace (`log_counter_thesis`).
9. Truy vấn O(1) qua IntelligenceRepository.get_counter_thesis_verdict(thesis_id).
"""

import pytest
import asyncio
from datetime import datetime
from app.domain.agents.counter_thesis import CounterThesisAgent
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.infrastructure.database.pg_pool import get_conn


@pytest.fixture
def agent05():
    return CounterThesisAgent()


@pytest.fixture
def counter_engine():
    return CounterThesisEngine()


@pytest.fixture
def intel_repo():
    return IntelligenceRepository()


def test_base_cts_calculation(counter_engine):
    """Test 1: Kiểm tra công thức tính Base CTS (Business 45% + Market 35% + Model 20%)"""
    risk_features = {
        "gil_risk": 20.0,
        "beneish_risk": 10.0,
        "receivable_spike": 15.0,
        "graph_rpt_risk": 20.0,
        "macro_headwind": 20.0,
        "liquidity_stress": 20.0,
        "missing_data": 10.0,
    }
    # Business: 0.15*20 + 0.10*10 + 0.10*15 + 0.10*20 = 3 + 1 + 1.5 + 2 = 7.5
    # Market: 0.15*20 + 0.20*20 = 3 + 4 = 7.0
    # Model: 0.20*10 = 2.0
    # Base CTS = 16.5
    base_cts = counter_engine.calculate_base_cts(risk_features)
    assert base_cts == 16.5


def test_ml_interaction_multiplier(counter_engine):
    """Test 2: Kiểm tra mô hình phi tuyến quét rủi ro cộng hưởng"""
    # 1. Bình thường -> 1.0x
    m1 = counter_engine.calculate_ml_interaction({"receivable_spike": 20.0, "liquidity_stress": 20.0})
    assert m1 == 1.0

    # 2. Phải thu phình to + Thanh khoản cạn -> +0.15 = 1.15x
    m2 = counter_engine.calculate_ml_interaction({"receivable_spike": 70.0, "liquidity_stress": 70.0})
    assert m2 == 1.15

    # 3. Hai cặp cộng hưởng -> 1.0 + 0.15 + 0.15 = 1.30x
    m3 = counter_engine.calculate_ml_interaction({
        "receivable_spike": 70.0,
        "liquidity_stress": 70.0,
        "macro_headwind": 65.0,
    })
    assert m3 == 1.30


def test_gil_catastrophic_zero_exception(counter_engine):
    """Test 3: Bắt buộc phán quyết BLOCK nếu dính cờ GIL CATASTROPHIC (Hard Law Zero Exception)"""
    async def _run():
        thesis = {
            "thesis_id": "THESIS_HOSE_RISKY_2026Q3_001",
            "ticker": "RISKY",
            "confirming_signals": ["Signal 1", "Signal 2", "Signal 3"],
        }
        risk_features = {"gil_status": "CATASTROPHIC", "gil_risk": 100.0}
        market_data = {"current_regime": "BULL_TRENDING"}
        stock_data = {"current_price": 15000.0}

        report = await counter_engine.evaluate_counter_thesis("RISKY", thesis, risk_features, market_data, stock_data)

        assert report.verdict == Verdict.BLOCK
        assert report.final_cts == 100.0
        assert any("GIL CATASTROPHIC" in reason for reason in report.block_reasons)

    asyncio.run(_run())


def test_rule_of_three_violation_rejection(counter_engine):
    """Test 4: Bắt buộc phán quyết BLOCK nếu Thesis không đủ 3 tín hiệu độc lập"""
    async def _run():
        thesis = {
            "thesis_id": "THESIS_HOSE_WEAK_2026Q3_001",
            "ticker": "WEAK",
            "confirming_signals": ["Only 1 Signal"], # Chỉ có 1 tín hiệu
        }
        risk_features = {"gil_risk": 10.0}
        market_data = {"current_regime": "BULL_TRENDING"}
        stock_data = {"current_price": 20000.0}

        report = await counter_engine.evaluate_counter_thesis("WEAK", thesis, risk_features, market_data, stock_data)

        assert report.verdict == Verdict.BLOCK
        assert report.rule_of_three_passed is False
        assert any("Vi phạm Hard Law Điều 3" in reason for reason in report.block_reasons)

    asyncio.run(_run())


def test_capitulation_entry_rebound(counter_engine):
    """Test 5: Ngoại lệ Bắt đáy Khoa học Capitulation Rebound (Bẫy 3)"""
    # Thỏa mãn 4/5 điều kiện hoảng loạn cực đại
    market_data = {
        "current_regime": "Bear Panic",
        "breadth_recovery_pct": 18.0,
        "foreign_net_flow": 80000000000.0,
        "csad_score": 0.018,
    }
    stock_data = {
        "current_price": 12000.0,
        "pe_ratio": 8.5,
        "pb_ratio": 0.95,
        "volume": 25000000.0,
        "vol_ma20": 10000000.0, # 2.5x MA20
    }

    is_cap, reasons = counter_engine.check_capitulation_criteria(market_data, stock_data)
    assert is_cap is True
    assert len(reasons) >= 3

    # Regime multiplier cho Capitulation Rebound là 1.1x thay vì 1.5x (Bear Panic)
    m_regime = counter_engine.get_regime_multiplier("Bear Panic", is_capitulation=True)
    assert m_regime == 1.10


def test_agent05_db_state_and_audit_persistence(agent05, intel_repo):
    """Test 6: Kiểm tra lưu trữ state vào `counter_thesis_verdicts` và audit trace vào `log_counter_thesis`"""
    async def _run():
        ticker = "VNM"
        thesis_id = "THESIS_HOSE_VNM_2026Q3_001"
        thesis_payload = {
            "thesis_id": thesis_id,
            "ticker": ticker,
            "input_validation": {
                "gil_status": "PASS",
                "independent_signals": {
                    "signal_1_factor": "PASS (CSS=77.4)",
                    "signal_2_surveillance": "PASS (Moat=82.0)",
                    "signal_3_macro_hmm": "PASS (Regime=BULL_TRENDING)",
                }
            },
            "thesis_body": {
                "catalyst": {"primary_type": "Earnings Expansion"},
                "price_target": {"base_case": 85000.0},
            }
        }
        market_data = {
            "current_regime": "BULL_TRENDING",
            "breadth_above_ma50_pct": 65.0,
            "foreign_net_flow": 30000000000.0,
        }
        stock_data = {
            "current_price": 72000.0,
            "pe_ratio": 16.0,
            "pb_ratio": 3.8,
            "volume": 5000000.0,
            "vol_ma20": 4500000.0,
        }

        # Save investment thesis first to satisfy foreign key constraint in DB
        intel_repo.save_investment_thesis({
            "thesis_id": thesis_id,
            "ticker": ticker,
            "catalyst_type": "Earnings Expansion",
            "catalyst_description": "Growth",
            "timeline_months": 3,
            "target_price": 85000.0,
            "entry_price_estimated": 72000.0,
            "confirming_signals": ["s1", "s2", "s3"],
            "invalidation_conditions": ["inv1"],
            "pre_mortem_scenarios": ["pre1"],
            "status": "PENDING_COUNTER_ANALYSIS",
        })

        # Thực thi qua run_event() để ghi cả log audit
        event_res = await agent05.run_event({
            "investment_thesis": thesis_payload,
            "market_data": market_data,
            "stock_data": stock_data,
            "risk_overrides": {"gil_status": "PASS", "gil_risk": 15.0},
        })

        assert event_res["status"] == "SUCCESS"
        data = event_res["result"]["data"]
        assert data["ticker"] == ticker
        assert data["verdict"] in ["PROCEED", "CONDITIONAL"]

        # 1. Verify state persistence in `counter_thesis_verdicts`
        db_verdict = intel_repo.get_counter_thesis_verdict(thesis_id)
        assert db_verdict is not None
        assert db_verdict["thesis_id"] == thesis_id
        assert db_verdict["ticker"] == ticker
        assert db_verdict["verdict"] == data["verdict"]
        assert db_verdict["cts_score"] == data["cts_score"]

        # 2. Verify audit log persistence in `log_counter_thesis`
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thesis_id, ticker, verdict FROM log_counter_thesis WHERE thesis_id = %s",
                    (thesis_id,)
                )
                log_row = cur.fetchone()
                assert log_row is not None
                assert log_row[0] == thesis_id
                assert log_row[1] == ticker
                assert log_row[2] == data["verdict"]

    asyncio.run(_run())


def test_gil_data_error_fallback_veto(counter_engine):
    """Test 7: Bắt buộc phán quyết BLOCK nếu xảy ra lỗi dữ liệu GIL (DATA_ERROR) theo Failure Modes IOS v5.1"""
    async def _run():
        thesis = {
            "thesis_id": "THESIS_HOSE_GILERR_001",
            "ticker": "GILERR",
            "confirming_signals": ["Signal 1", "Signal 2", "Signal 3"],
        }
        risk_features = {"gil_status": "DATA_ERROR", "gil_risk": 80.0}
        market_data = {"current_regime": "BULL_TRENDING"}
        stock_data = {"current_price": 25000.0}

        report = await counter_engine.evaluate_counter_thesis("GILERR", thesis, risk_features, market_data, stock_data)

        assert report.verdict == Verdict.BLOCK
        assert report.final_cts == 100.0
        assert any("Lỗi dữ liệu đồ thị sở hữu chéo (GIL)" in reason for reason in report.block_reasons)

    asyncio.run(_run())


def test_rule_of_three_with_dict_signals(counter_engine):
    """Test 8: Kiểm tra parse dict confirming_signals: nếu có 1 signal là FAIL thì Rule of Three vi phạm -> BLOCK"""
    async def _run():
        thesis = {
            "thesis_id": "THESIS_HOSE_DICTFAIL_001",
            "ticker": "DICTFAIL",
            "confirming_signals": {
                "signal_1_factor": "FAIL (CSS=35.0)",
                "signal_2_surveillance": "PASS (Moat=80.0)",
                "signal_3_macro_hmm": "PASS (Regime=BULL_TRENDING)",
            },
        }
        risk_features = {"gil_status": "PASS", "gil_risk": 10.0}
        market_data = {"current_regime": "BULL_TRENDING"}
        stock_data = {"current_price": 30000.0}

        report = await counter_engine.evaluate_counter_thesis("DICTFAIL", thesis, risk_features, market_data, stock_data)

        assert report.verdict == Verdict.BLOCK
        assert report.rule_of_three_passed is False
        assert any("Vi phạm Hard Law Điều 3" in reason for reason in report.block_reasons)

    asyncio.run(_run())


def test_agent05_auto_hydration_from_db(agent05, intel_repo):
    """Test 9: Kiểm tra Auto-hydration từ DB khi chỉ truyền ticker và cập nhật status thesis"""
    async def _run():
        ticker = "HYDRATE"
        thesis_id = f"THESIS_HOSE_HYDRATE_{datetime.now().strftime('%H%M%S')}"

        # 1. Lưu thesis trước vào DB
        intel_repo.save_investment_thesis({
            "thesis_id": thesis_id,
            "ticker": ticker,
            "catalyst_type": "Earnings Expansion",
            "catalyst_description": "Growth",
            "timeline_months": 3,
            "target_price": 50000.0,
            "entry_price_estimated": 40000.0,
            "confirming_signals": {
                "s1": "PASS (CSS=75.0)",
                "s2": "PASS (Moat=80.0)",
                "s3": "PASS (Regime=BULL)",
            },
            "invalidation_conditions": ["inv"],
            "pre_mortem_scenarios": ["pre"],
            "status": "PENDING_COUNTER_ANALYSIS",
        })

        # 2. Gọi Agent 05 CHỈ với ticker (không truyền investment_thesis)
        res = await agent05.run_event({"ticker": ticker})
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        assert data["thesis_id"] == thesis_id
        assert data["ticker"] == ticker

        # 3. Kiểm tra DB: counter_thesis_verdicts phải có bản ghi
        db_verdict = intel_repo.get_latest_counter_thesis_verdict(ticker)
        assert db_verdict is not None
        assert db_verdict["thesis_id"] == thesis_id

        # 4. Kiểm tra investment_theses.status phải được cập nhật
        updated_thesis = intel_repo.get_latest_investment_thesis(ticker)
        assert updated_thesis is not None
        assert updated_thesis["status"] in ["APPROVED_ACTIVE", "CONDITIONAL_APPROVED", "REJECTED"]

    asyncio.run(_run())

