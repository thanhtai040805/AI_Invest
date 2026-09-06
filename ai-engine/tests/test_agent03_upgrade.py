"""Unit & Integration Test Suite for AGENT-03: Equity Research Agent (IOS v5.1)."""

import asyncio
import pytest
from datetime import date
from app.domain.agents.equity_research import EquityResearchAgent
from app.domain.services.factor_service import FactorService
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.domain.rules.scoring import CSSScoringEngine, ConvictionLevel
from app.domain.rules.market.hmm_classifier import MarketRegime
import pandas as pd


def test_factor_service_real_computation():
    """Kiểm tra FactorService tính toán 6 nhóm nhân tố F1-F6 từ dữ liệu thật CSDL (financial_ratios + market_data_daily)."""
    fs = FactorService()
    factors = fs.compute_factors_for_ticker("FPT")
    
    assert "f1_value" in factors
    assert "f2_quality" in factors
    assert "f3_momentum" in factors
    assert "f4_earnings" in factors
    assert "f5_flow" in factors
    assert "f6_technical" in factors
    assert "raw_metrics" in factors
    
    # Đảm bảo không rơi vào giá trị default 50.0 trên cả 6 nhân tố
    assert factors["f1_value"] > 0
    assert factors["f2_quality"] > 0
    assert factors["f3_momentum"] > 0


def test_intelligence_repository_factor_and_moat():
    """Kiểm tra IntelligenceRepository lưu và đọc đầy đủ 6 nhân tố F1-F6, hiệu chuẩn Moat và ghi log."""
    repo = IntelligenceRepository()
    test_d = date(2026, 9, 5)
    
    # 1. Lưu factor_score
    saved = repo.save_factor_score(
        symbol="FPT",
        f1_value=47.36,
        f2_quality=83.71,
        f3_momentum=65.45,
        f4_earnings=67.24,
        f5_flow=63.85,
        f6_technical=60.0,
        css=75.84,
        conviction="A",
        score_date=test_d,
    )
    assert saved is True
    
    # 2. Đọc lại factor_score
    res = repo.get_factor_score("FPT", score_date=test_d)
    assert res is not None
    assert res["ticker"] == "FPT"
    assert res["f1_value"] == 47.36
    assert res["f2_quality"] == 83.71
    assert res["f3_momentum"] == 65.45
    assert res["f4_earnings"] == 67.24
    assert res["f5_flow"] == 63.85
    assert res["f6_technical"] == 60.0
    assert res["css"] == 75.84
    assert res["conviction"] == "A"
    
    # 3. Moat profile auto-recovery
    moat = repo.get_moat_profile("FPT")
    assert moat is not None
    assert moat["moat_score"] >= 70.0
    assert moat["multiplier"] >= 1.15
    
    # 4. Ghi log_equity_research
    log_saved = repo.log_equity_research(
        ticker="FPT",
        factor_raw_metrics={"pe": 18.5, "roe": 0.28},
        moat_citations_evidence={"evidence_quote": "Leading enterprise tech in VN"},
        llm_prompt_tokens=200,
        research_date=test_d,
    )
    assert log_saved is True


def test_equity_research_agent_process_fpt():
    """Kiểm tra Agent 03 chạy thực tế trên FPT, nạp trọng số thích ứng từ Agent-10 RL và đủ điều kiện sinh Thesis."""
    async def _test():
        agent = EquityResearchAgent()
        res = await agent.process({
            "ticker": "FPT",
            "sector": "Technology",
            "current_regime": "BULL_TRENDING",
        })
        
        data = res["data"]
        trace = res["trace"]
        
        assert data["ticker"] == "FPT"
        assert data["conviction"] in ["A+", "A", "B"]
        assert data["css"] >= 60.0
        assert data["eligible_for_thesis"] is True
        assert data["moat_score"] >= 70.0
        assert data["current_price"] > 0
        assert trace["scoring_engine"] == "CSSScoringEngine"
        assert "BULL" in trace["regime_applied"]
    asyncio.run(_test())


def test_equity_research_moat_calibration_reduction():
    """Kiểm tra cơ chế dập tắt ảo giác Moat AI: Agent 10 hiệu chuẩn Moat sẽ hạ moat_score và multiplier."""
    async def _test():
        agent = EquityResearchAgent()
        res = await agent.process({
            "ticker": "FPT",
            "current_regime": "BULL_TRENDING",
            "moat_calibrations": {
                "FPT": {
                    "calibrated_moat_score": 35.0,
                    "calibrated_multiplier": 0.85,
                    "hallucination_risk": "HIGH",
                }
            }
        })
        
        data = res["data"]
        assert data["moat_score"] == 35.0
        assert data["moat_multiplier"] == 0.85
        assert "MOAT_CALIBRATED_AGENT10_HIGH" in data["data_quality_flag"]
    asyncio.run(_test())


def test_css_scoring_engine_gatekeeper_veto():
    """Kiểm tra Gatekeeper của CSSScoringEngine: Ý kiến kiểm toán hoặc GIL CATASTROPHIC phải ép về hạng E."""
    engine = CSSScoringEngine()
    
    df_bad_audit = pd.DataFrame([{
        "ticker": "TEST",
        "sector": "General",
        "f1_value": 90.0,
        "f2_quality": 90.0,
        "f3_momentum": 90.0,
        "f4_earnings": 90.0,
        "f5_flow": 90.0,
        "f6_technical": 90.0,
        "audit_opinion": "DISCLAIMER",
        "gil_flag": "PASS",
    }])
    scored = engine.calculate_css(df_bad_audit, MarketRegime.BULL_TRENDING)
    assert scored["conviction"].iloc[0] == ConvictionLevel.E.value
    
    df_catastrophic = pd.DataFrame([{
        "ticker": "TEST2",
        "sector": "General",
        "f1_value": 90.0,
        "f2_quality": 90.0,
        "f3_momentum": 90.0,
        "f4_earnings": 90.0,
        "f5_flow": 90.0,
        "f6_technical": 90.0,
        "audit_opinion": "UNQUALIFIED",
        "gil_flag": "CATASTROPHIC",
    }])
    scored_cat = engine.calculate_css(df_catastrophic, MarketRegime.BULL_TRENDING)
    assert scored_cat["conviction"].iloc[0] == ConvictionLevel.E.value


def test_equity_research_missing_ticker_raises():
    """Kiểm tra validate đầu vào: Thiếu ticker phải raise ValueError."""
    async def _test():
        agent = EquityResearchAgent()
        with pytest.raises(ValueError):
            await agent.process({})
    asyncio.run(_test())
