"""AGENT-04: Investment Thesis Agent Comprehensive Upgrade & Verification Test Suite (IOS v5.1)

Kiểm thử toàn diện Agent-04:
1. Sinh luận điểm đầu tư có cấu trúc (Structured Investment Thesis Payload) chuẩn hóa.
2. Lọc Hard Filter Lớp 0 (GIL CATASTROPHIC -> REJECT).
3. Ngưỡng lọc Conviction & CSS (CSS < 60.0 hoặc Conviction C/D/E -> WAIT/SKIP).
4. Kiểm tra 3 Tín hiệu Độc lập (Hard Law Điều 3).
5. Định giá thích ứng đa mô hình (Adaptive Valuation: loại bỏ DCF với timeline <= 3M, thêm 15% Bull Premium).
6. Tự động nhận diện 4 nhóm Ngòi nổ (Catalyst Selection: Earnings Expansion, Sector Rotation, Undervaluation, Value Unlock).
7. Phân tích Pre-Mortem (3 kịch bản) và Điều kiện Hủy Luận điểm (Thesis Invalidation & Hard Stop -7%).
8. Tự động lưu vào PostgreSQL DB (`investment_theses` state table và `log_investment_thesis` audit log).
9. Truy vấn O(1) qua IntelligenceRepository.get_latest_investment_thesis().
"""

import pytest
import asyncio
from datetime import date
from app.domain.agents.investment_thesis import InvestmentThesisAgent
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.infrastructure.database.pg_pool import get_conn


@pytest.fixture
def agent04():
    return InvestmentThesisAgent()


@pytest.fixture
def thesis_engine():
    return ThesisEngine()


@pytest.fixture
def intel_repo():
    return IntelligenceRepository()


def test_thesis_id_naming_format(thesis_engine):
    """Test 1: Kiểm tra định dạng ID thesis chuẩn hóa: THESIS_HOSE_{TICKER}_{YEAR}Q{Q}_{SEQ:03d}"""
    t_id = thesis_engine.generate_thesis_id("VNM", target_date=date(2026, 9, 5), seq_num=2)
    assert t_id == "THESIS_HOSE_VNM_2026Q3_002"


def test_catalyst_classification(thesis_engine):
    """Test 2: Kiểm tra tự động phân loại 4 nhóm ngòi nổ (Catalyst)"""
    # 1. Earnings Expansion (F4 >= 70)
    c1 = thesis_engine.determine_catalyst({"f4_earnings": 80.0, "f5_flow": 50.0}, sector="Bán lẻ")
    assert c1["primary_type"] == "Earnings Expansion"

    # 2. Sector Rotation (F5 >= 70 hoặc F3/F5 cao)
    c2 = thesis_engine.determine_catalyst({"f4_earnings": 50.0, "f5_flow": 75.0, "f3_momentum": 70.0}, sector="Ngân hàng")
    assert c2["primary_type"] == "Sector Rotation"

    # 3. Undervaluation (F1 >= 70 hoặc F1/F2 cao)
    c3 = thesis_engine.determine_catalyst({"f1_value": 72.0, "f2_quality": 68.0, "f4_earnings": 40.0}, sector="Thép")
    assert c3["primary_type"] == "Undervaluation"

    # 4. Value Unlock (Default)
    c4 = thesis_engine.determine_catalyst({"f1_value": 50.0, "f2_quality": 50.0, "f4_earnings": 50.0}, sector="Bất động sản")
    assert c4["primary_type"] == "Value Unlock"


def test_adaptive_valuation_pricing(thesis_engine):
    """Test 3: Kiểm tra định giá thích ứng (Timeline <= 3M loại bỏ DCF, Timeline > 3M có 30% DCF, Regime Bull +15%)"""
    # Case A: Timeline 3M -> Exclude DCF (50% PE + 50% EV/EBITDA)
    val_3m = thesis_engine.calculate_adaptive_target_price(
        timeline_months=3,
        current_price=50000.0,
        pe_comp_price=60000.0,
        ev_ebitda_comp_price=70000.0,
        dcf_price=100000.0,
        regime_label="SIDEWAY",
        sector="Manufacturing"
    )
    # Expected base case without bull premium = 0.5*60000 + 0.5*70000 = 65000
    assert val_3m["base_case"] == 65000.0
    assert "Loại bỏ DCF" in val_3m["valuation_method"]

    # Case B: Timeline 6M -> Include 30% DCF (35% PE + 35% EV/EBITDA + 30% DCF)
    val_6m = thesis_engine.calculate_adaptive_target_price(
        timeline_months=6,
        current_price=50000.0,
        pe_comp_price=60000.0,
        ev_ebitda_comp_price=70000.0,
        dcf_price=80000.0,
        regime_label="SIDEWAY",
        sector="Manufacturing"
    )
    # Expected base case = 0.35*60000 + 0.35*70000 + 0.30*80000 = 21000 + 24500 + 24000 = 69500
    assert val_6m["base_case"] == 69500.0

    # Case C: Bull Regime -> +15% Premium
    val_bull = thesis_engine.calculate_adaptive_target_price(
        timeline_months=3,
        current_price=50000.0,
        pe_comp_price=60000.0,
        ev_ebitda_comp_price=60000.0,
        dcf_price=60000.0,
        regime_label="BULL_TRENDING",
        sector="Technology"
    )
    # 60000 * 1.15 = 69000
    assert val_bull["base_case"] == 69000.0


def test_hard_filter_catastrophic_rejection(agent04):
    """Test 4: Bắt buộc REJECT nếu vi phạm Hard Filter Lớp 0 (GIL CATASTROPHIC)"""
    async def _run():
        res = await agent04.process({
            "ticker": "EVIL_TICKER",
            "research_report": {
                "ticker": "EVIL_TICKER",
                "css": 85.0,
                "conviction": "A",
                "gil_status": "CATASTROPHIC",
            },
            "market_context": {"current_regime": "BULL_TRENDING"}
        })
        assert res["data"]["status"] == "REJECTED"
        assert "GIL == CATASTROPHIC" in res["data"]["reason"]

    asyncio.run(_run())


def test_low_css_wait_or_skip(agent04):
    """Test 5: WAIT / SKIP nếu CSS < 60.0 hoặc Conviction C/D/E"""
    async def _run():
        res = await agent04.process({
            "ticker": "WEAK_STOCK",
            "research_report": {
                "ticker": "WEAK_STOCK",
                "css": 52.0,
                "conviction": "C",
                "gil_status": "PASS",
            },
            "market_context": {"current_regime": "BULL_TRENDING"}
        })
        assert res["data"]["status"] == "WAIT_OR_SKIP"
        assert "chưa đạt ngưỡng B" in res["data"]["reason"]

    asyncio.run(_run())


def test_agent04_full_structured_payload(agent04):
    """Test 6: Kiểm tra sinh structured thesis payload đầy đủ cho cổ phiếu Conviction A (HPG)"""
    async def _run():
        research_report = {
            "ticker": "HPG",
            "sector": "Thép",
            "css": 82.5,
            "conviction": "A",
            "moat_score": 85.0,
            "current_price": 30000.0,
            "f1_value": 75.0,
            "f2_quality": 80.0,
            "f3_momentum": 70.0,
            "f4_earnings": 88.0,
            "f5_flow": 72.0,
            "f6_technical": 68.0,
        }
        market_context = {
            "current_regime": "BULL_TRENDING",
            "gil_status": "PASS",
            "current_price": 30000.0,
        }
        val_inputs = {
            "pe_price": 36000.0,
            "ev_ebitda_price": 38000.0,
            "dcf_price": 42000.0,
        }

        res = await agent04.process({
            "research_report": research_report,
            "market_context": market_context,
            "valuation_inputs": val_inputs,
            "timeline_months": 3,
        })

        assert "data" in res
        payload = res["data"]
        assert payload["ticker"] == "HPG"
        assert payload["status"] == "PENDING_COUNTER_ANALYSIS"
        assert "THESIS_HOSE_HPG" in payload["thesis_id"]

        # Hard Law Rule 3: 3 confirming signals
        signals = payload["input_validation"]["independent_signals"]
        assert len(signals) == 3
        assert "signal_1_factor" in signals
        assert "signal_2_surveillance" in signals
        assert "signal_3_macro_hmm" in signals

        # Body details
        body = payload["thesis_body"]
        assert body["catalyst"]["primary_type"] == "Earnings Expansion"
        assert body["timeline"] == "3M"
        assert body["price_target"]["base_case"] > 30000.0
        assert len(body["pre_mortem"]) >= 3
        assert body["exit_conditions"]["hard_stop_loss_price"] == 27900.0 # 30000 * 0.93
        assert len(body["exit_conditions"]["invalidation_triggers"]) >= 3

    asyncio.run(_run())


def test_agent04_db_state_and_audit_persistence(agent04, intel_repo):
    """Test 7: Kiểm tra lưu trữ state vào `investment_theses` và audit trace vào `log_investment_thesis`"""
    async def _run():
        ticker = "SSI"
        research_report = {
            "ticker": ticker,
            "sector": "Chứng khoán",
            "css": 78.0,
            "conviction": "A",
            "moat_score": 75.0,
            "current_price": 32000.0,
            "f1_value": 70.0,
            "f2_quality": 75.0,
            "f3_momentum": 82.0,
            "f4_earnings": 65.0,
            "f5_flow": 85.0,
            "f6_technical": 78.0,
        }
        market_context = {
            "current_regime": "BULL_TRENDING",
            "gil_status": "PASS",
            "current_price": 32000.0,
        }

        # Chạy qua run_event() để thực thi cả audit logging
        event_res = await agent04.run_event({
            "research_report": research_report,
            "market_context": market_context,
            "timeline_months": 3,
        })

        assert event_res["status"] == "SUCCESS"
        payload = event_res["result"]["data"]
        thesis_id = payload["thesis_id"]

        # 1. Verify state persistence in `investment_theses`
        db_thesis = intel_repo.get_latest_investment_thesis(ticker)
        assert db_thesis is not None
        assert db_thesis["thesis_id"] == thesis_id
        assert db_thesis["ticker"] == ticker
        assert db_thesis["catalyst_type"] == "Sector Rotation"
        assert db_thesis["target_price"] > 32000.0
        assert len(db_thesis["confirming_signals"]) == 3
        assert len(db_thesis["pre_mortem_scenarios"]) >= 3
        assert db_thesis["target_price_range"] is not None
        assert len(db_thesis["target_price_range"]) == 2
        assert db_thesis["status"] == "PENDING_COUNTER_ANALYSIS"

        # 2. Verify audit log persistence in `log_investment_thesis`
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thesis_id, ticker, pre_mortem_scenarios FROM log_investment_thesis WHERE thesis_id = %s",
                    (thesis_id,)
                )
                log_row = cur.fetchone()
                assert log_row is not None
                assert log_row[0] == thesis_id
                assert log_row[1] == ticker
                # Assert pre_mortem_scenarios was extracted & stored correctly
                assert len(log_row[2]) >= 3

    asyncio.run(_run())


def test_rule_of_three_insufficient_signals(agent04):
    """Test 8: Hard Law Điều 3: Từ chối sinh thesis nếu không đủ 3 tín hiệu độc lập xác nhận"""
    async def _run():
        # Tạo bối cảnh thị trường BEAR_TRENDING và Moat/Quality bình thường (< 90)
        research_report = {
            "ticker": "VRE",
            "sector": "Bất động sản",
            "css": 72.0,
            "conviction": "B",
            "moat_score": 50.0,
            "current_price": 25000.0,
            "f1_value": 50.0,
            "f2_quality": 55.0,
            "f3_momentum": 50.0,
            "f4_earnings": 50.0,
            "f5_flow": 50.0,
            "f6_technical": 50.0,
        }
        market_context = {
            "current_regime": "BEAR_TRENDING",
            "gil_status": "PASS",
            "current_price": 25000.0,
        }

        res = await agent04.process({
            "research_report": research_report,
            "market_context": market_context,
        })
        assert res["data"]["status"] == "WAIT_OR_SKIP"
        assert "Không đủ 3 tín hiệu độc lập" in res["data"]["reason"]

    asyncio.run(_run())


def test_thesis_re_evaluation_on_conflict_update(intel_repo):
    """Test 9: Kiểm tra cập nhật toàn diện khi Re-evaluate thesis (ON CONFLICT DO UPDATE)"""
    thesis_id = "THESIS_HOSE_TEST_REVAL_001"
    ticker = "TEST_TICKER"

    payload_1 = {
        "thesis_id": thesis_id,
        "ticker": ticker,
        "catalyst_type": "Earnings Expansion",
        "catalyst_description": "Mo rong cong suat cu",
        "timeline_months": 3,
        "target_price": 40000.0,
        "entry_price_estimated": 35000.0,
        "confirming_signals": {"signal_1": "PASS"},
        "invalidation_conditions": ["Condition 1"],
        "pre_mortem_scenarios": ["Scenario 1"],
        "target_price_range": [40000.0, 45000.0],
        "status": "PENDING_COUNTER_ANALYSIS",
    }
    assert intel_repo.save_investment_thesis(payload_1) is True

    # Re-evaluation với mục tiêu giá và kịch bản mới
    payload_2 = {
        "thesis_id": thesis_id,
        "ticker": ticker,
        "catalyst_type": "Sector Rotation",
        "catalyst_description": "Dong tien moi do bo",
        "timeline_months": 6,
        "target_price": 50000.0,
        "entry_price_estimated": 38000.0,
        "confirming_signals": {"signal_1": "PASS", "signal_2": "PASS", "signal_3": "PASS"},
        "invalidation_conditions": ["Condition 1", "Condition 2"],
        "pre_mortem_scenarios": ["Scenario 1", "Scenario 2", "Scenario 3"],
        "target_price_range": [50000.0, 55000.0],
        "status": "APPROVED_ACTIVE",
    }
    assert intel_repo.save_investment_thesis(payload_2) is True

    updated_thesis = intel_repo.get_latest_investment_thesis(ticker)
    assert updated_thesis is not None
    assert updated_thesis["target_price"] == 50000.0
    assert updated_thesis["catalyst_type"] == "Sector Rotation"
    assert updated_thesis["catalyst_description"] == "Dong tien moi do bo"
    assert updated_thesis["timeline_months"] == 6
    assert updated_thesis["target_price_range"] == [50000.0, 55000.0]
    assert updated_thesis["status"] == "APPROVED_ACTIVE"
    assert len(updated_thesis["pre_mortem_scenarios"]) == 3

