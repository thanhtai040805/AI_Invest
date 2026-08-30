"""Tests for Agent-04 (Thesis Agent) and Agent-05 (Counter Thesis Agent)
Testing 3-Tier CTS, Decision Tree, Output JSON Schema and Capitulation Entry Exception (Bẫy 3).
"""

import pytest
import asyncio
from datetime import date
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2
from app.domain.agents.investment_thesis import InvestmentThesisAgent
from app.domain.agents.counter_thesis import CounterThesisAgent


@pytest.fixture
def thesis_engine():
    return ThesisEngine()


@pytest.fixture
def counter_engine():
    return CounterThesisEngine()


def test_thesis_id_generation(thesis_engine):
    t_id = thesis_engine.generate_thesis_id("HPG", target_date=date(2026, 7, 21), seq_num=1)
    assert t_id == "THESIS_HOSE_HPG_2026Q3_001"


def test_catalyst_auto_selection(thesis_engine):
    # F4 High -> Earnings Expansion
    cat1 = thesis_engine.determine_catalyst({"f4_earnings": 85.0, "f5_flow": 50.0}, sector="Thép")
    assert cat1["primary_type"] == "Earnings Expansion"

    # F5 High -> Sector Rotation
    cat2 = thesis_engine.determine_catalyst({"f4_earnings": 50.0, "f5_flow": 80.0, "f3_momentum": 75.0}, sector="Chứng khoán")
    assert cat2["primary_type"] == "Sector Rotation"

    # F1 High -> Undervaluation
    cat3 = thesis_engine.determine_catalyst({"f1_value": 75.0, "f4_earnings": 50.0, "f5_flow": 50.0}, sector="Bán lẻ")
    assert cat3["primary_type"] == "Undervaluation"


def test_structured_thesis_schema_validation(thesis_engine):
    research_report = {
        "ticker": "HPG",
        "sector": "Manufacturing",
        "css": 84.2,
        "conviction": "A",
        "moat_score": 80.0,
        "current_price": 28000.0,
        "f1_value": 70.0,
        "f2_quality": 75.0,
        "f3_momentum": 72.0,
        "f4_earnings": 85.0,
        "f5_flow": 70.0,
        "f6_technical": 68.0,
    }
    market_context = {
        "current_regime": "BULL_TRENDING",
        "gil_status": "PASS",
        "current_price": 28000.0,
    }
    val_inputs = {
        "pe_price": 35000.0,
        "ev_ebitda_price": 37000.0,
        "dcf_price": 40000.0,
    }

    is_eligible, payload, msg = thesis_engine.build_structured_thesis_output(
        ticker="HPG",
        research_report=research_report,
        market_context=market_context,
        valuation_inputs=val_inputs,
        timeline_months=3,
        seq_num=1,
    )

    assert is_eligible is True
    assert payload["ticker"] == "HPG"
    assert "thesis_id" in payload
    assert payload["status"] == "PENDING_COUNTER_ANALYSIS"
    assert payload["input_validation"]["conviction_level"] == "A"
    assert payload["input_validation"]["css_score"] == 84.2
    assert "signal_1_factor" in payload["input_validation"]["independent_signals"]
    assert "signal_2_surveillance" in payload["input_validation"]["independent_signals"]
    assert "signal_3_macro_hmm" in payload["input_validation"]["independent_signals"]
    assert payload["thesis_body"]["catalyst"]["primary_type"] == "Earnings Expansion"
    assert payload["thesis_body"]["timeline"] == "3M"
    assert len(payload["thesis_body"]["price_target"]["target_range"]) == 2
    assert len(payload["thesis_body"]["pre_mortem"]) >= 3
    assert payload["thesis_body"]["exit_conditions"]["hard_stop_loss_price"] > 0
    assert len(payload["thesis_body"]["exit_conditions"]["invalidation_triggers"]) >= 3


def test_hard_filter_gil_catastrophic_rejection(thesis_engine):
    research_report = {"css": 80.0, "conviction": "A"}
    market_context = {"gil_status": "CATASTROPHIC"}
    is_eligible, payload, msg = thesis_engine.build_structured_thesis_output(
        ticker="BAD_STOCK",
        research_report=research_report,
        market_context=market_context,
    )
    assert is_eligible is False
    assert "REJECT" in msg


def test_base_cts_calculation(counter_engine):
    # Test Base CTS with realistic features (no margin tension)
    risk_features = {
        "gil_risk": 20.0,
        "beneish_risk": 10.0,
        "receivable_spike": 15.0,
        "graph_rpt_risk": 20.0,
        "macro_headwind": 20.0,
        "liquidity_stress": 20.0,
        "missing_data": 10.0,
    }
    # Business: 0.15*20 + 0.1*10 + 0.1*15 + 0.1*20 = 3 + 1 + 1.5 + 2 = 7.5
    # Market: 0.15*20 + 0.2*20 = 3 + 4 = 7.0
    # Model: 0.2*10 = 2.0
    # Base CTS = 16.5
    base_cts = counter_engine.calculate_base_cts(risk_features)
    assert base_cts == 16.5


def test_ml_interaction_multiplier(counter_engine):
    # Không có rủi ro cộng hưởng -> 1.0
    mult_normal = counter_engine.calculate_ml_interaction({
        "receivable_spike": 20.0,
        "liquidity_stress": 20.0,
    })
    assert mult_normal == 1.0

    # Phải thu cao + Thanh khoản cạn -> +0.15 = 1.15
    mult_pair1 = counter_engine.calculate_ml_interaction({
        "receivable_spike": 70.0,
        "liquidity_stress": 70.0,
    })
    assert mult_pair1 == 1.15

    # Cả 2 cặp cộng hưởng -> 1.0 + 0.15 + 0.15 = 1.30
    mult_pair2 = counter_engine.calculate_ml_interaction({
        "receivable_spike": 70.0,
        "liquidity_stress": 70.0,
        "macro_headwind": 65.0,
    })
    assert mult_pair2 == 1.30


def test_regime_multiplier_and_capitulation_exception(counter_engine):
    # 1. Bear Panic bình thường -> 1.5x
    m_panic = counter_engine.get_regime_multiplier("Bear Panic", is_capitulation=False)
    assert m_panic == 1.50

    # 2. Bear Panic nhưng thỏa mãn Capitulation Exception (Bẫy 3) -> 1.1x
    m_cap = counter_engine.get_regime_multiplier("Bear Panic", is_capitulation=True)
    assert m_cap == 1.10

    # 3. Bull Low Vol -> 0.9x
    m_bull = counter_engine.get_regime_multiplier("Bull Low Vol")
    assert m_bull == 0.90


def test_counter_thesis_verdicts(counter_engine):
    async def _run():
        thesis = {
            "thesis_id": "THESIS_HOSE_HPG_2026Q3_001",
            "ticker": "HPG",
            "confirming_signals": ["Signal 1", "Signal 2", "Signal 3"],
        }
        market_data = {"current_regime": "Bull Low Vol", "derivative_basis": 0.5, "csad_score": 0.005}
        stock_data = {"current_price": 28000.0, "volume": 15000000.0, "vol_ma20": 12000000.0}

        # Case 1: Rủi ro thấp -> PROCEED
        low_risk = {
            "gil_risk": 10.0, "beneish_risk": 10.0, "receivable_spike": 10.0,
            "graph_rpt_risk": 10.0, "macro_headwind": 10.0, "liquidity_stress": 10.0, "missing_data": 10.0
        }
        rep1 = await counter_engine.evaluate_counter_thesis("HPG", thesis, low_risk, market_data, stock_data)
        assert rep1.verdict == Verdict.PROCEED
        assert rep1.final_cts <= 30.0

        # Case 2: Rủi ro trung bình -> CONDITIONAL (với constraints)
        med_risk = {
            "gil_risk": 40.0, "beneish_risk": 50.0, "receivable_spike": 40.0,
            "graph_rpt_risk": 30.0, "macro_headwind": 40.0, "liquidity_stress": 50.0, "missing_data": 20.0
        }
        rep2 = await counter_engine.evaluate_counter_thesis("HPG", thesis, med_risk, market_data, stock_data)
        assert rep2.verdict == Verdict.CONDITIONAL
        assert rep2.execution_constraints is not None
        assert "max_position_size_multiplier" in rep2.execution_constraints

        # Case 3: GIL CATASTROPHIC -> BLOCK (Hard Law Zero Exception)
        cat_risk = {
            "gil_status": "CATASTROPHIC", "gil_risk": 100.0
        }
        rep3 = await counter_engine.evaluate_counter_thesis("HPG", thesis, cat_risk, market_data, stock_data)
        assert rep3.verdict == Verdict.BLOCK
        assert rep3.final_cts == 100.0

    asyncio.run(_run())


def test_agents_end_to_end():
    async def _run():
        agent04 = InvestmentThesisAgent()
        agent05 = CounterThesisAgent()

        # 1. Agent 04 Input
        research_report = {
            "ticker": "HPG",
            "css": 82.0,
            "conviction": "A",
            "moat_score": 78.0,
            "current_price": 28500.0,
            "f1_value": 68.0,
            "f2_quality": 75.0,
            "f3_momentum": 70.0,
            "f4_earnings": 88.0,
            "f5_flow": 72.0,
            "f6_technical": 65.0,
        }
        market_context = {
            "current_regime": "Bull Low Vol",
            "gil_status": "PASS",
            "current_price": 28500.0,
            "derivative_basis": 1.0,
            "foreign_net_flow": 50000000000.0,
            "csad_score": 0.008,
        }

        res04 = await agent04.process({
            "research_report": research_report,
            "market_context": market_context,
            "timeline_months": 3,
        })

        assert "data" in res04
        thesis_data = res04["data"]
        assert "thesis_id" in thesis_data
        assert thesis_data["status"] == "PENDING_COUNTER_ANALYSIS"

        # 2. Agent 05 Input
        res05 = await agent05.process({
            "investment_thesis": thesis_data,
            "market_data": market_context,
            "stock_data": {"current_price": 28500.0, "volume": 20000000.0, "vol_ma20": 15000000.0},
        })

        assert "data" in res05
        verdict_data = res05["data"]
        assert verdict_data["ticker"] == "HPG"
        assert "cts_score" in verdict_data
        assert verdict_data["verdict"] in ["PROCEED", "CONDITIONAL"]

    asyncio.run(_run())
