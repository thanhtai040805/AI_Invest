"""Test Suite: Agent-10 (Reinforcement Learning & Causal Adaptation) Institutional Upgrade."""

import asyncio
import pytest
from datetime import date
from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp toàn bộ 12 Agents
from app.domain.rules.learning.causal_learning_engines import (
    FactorPerformanceEngine,
    MoatHallucinationCalibrator,
    DecayDiagnosisEngine,
    ProbabilityCalibrationEngine,
    OOSValidationGatekeeper,
)
from app.adapters.postgres_adapter import PostgresAdapter


def test_moat_hallucination_calibrator_penalizes_phantom_moat():
    """Kiểm tra MoatHallucinationCalibrator phạt nặng khi LLM chấm điểm hào kinh tế ảo."""
    calibrator = MoatHallucinationCalibrator()

    # Trường hợp 1: Doanh nghiệp có Moat thật (FPT: ROIC cao, biên gộp giữ vững)
    res_real = calibrator.evaluate_moat(
        ticker="FPT",
        llm_moat_score=85.0,
        financial_ratios={
            "roic": 22.5,
            "wacc": 11.5,
            "roic_spread_persistence_quarters": 8,
            "gross_margin_delta_4q": 1.2,
            "rev_growth_relative_to_sector": 4.5,
        }
    )
    assert res_real.hallucination_risk in ["LOW", "MODERATE"]
    assert res_real.penalty_factor < 0.30
    assert res_real.calibrated_moat_score > 75.0
    assert res_real.calibrated_multiplier > 1.05

    # Trường hợp 2: Hào ảo (LLM chấm 85 nhưng ROIC âm, biên gộp sụp đổ do hết ưu đãi thuế)
    res_phantom = calibrator.evaluate_moat(
        ticker="PHANTOM_CORP",
        llm_moat_score=85.0,
        financial_ratios={
            "roic": 8.0,
            "wacc": 12.0,  # Lợi tức thấp hơn chi phí vốn
            "roic_spread_persistence_quarters": 0,
            "gross_margin_delta_4q": -5.5,  # Biên gộp sụt mạnh
            "rev_growth_relative_to_sector": -6.0,  # Mất thị phần
        }
    )
    assert res_phantom.hallucination_risk == "HIGH_HALLUCINATION"
    assert res_phantom.hallucination_divergence > 30.0
    assert res_phantom.penalty_factor >= 0.50
    # Điểm sau hiệu chuẩn phải bị kéo giật lùi xuống mạnh
    assert res_phantom.calibrated_moat_score < 65.0
    # Hệ số nhân phải bị phạt sát về 1.0
    assert res_phantom.calibrated_multiplier < 1.05


def test_bayesian_kelly_matrix_no_fake_sample_size():
    """Kiểm tra ProbabilityCalibrationEngine không bịa sample_size = 50 khi không có lệnh."""
    engine = ProbabilityCalibrationEngine()

    # Khi không có giao dịch nào
    matrix_empty = engine.calibrate(realized_trades=[], regime="BULL_MARKET")
    assert matrix_empty["A+"]["sample_size"] == 0
    assert matrix_empty["A"]["sample_size"] == 0
    assert matrix_empty["B"]["sample_size"] == 0
    assert matrix_empty["A"]["data_quality_flag"] == "INSUFFICIENT_SAMPLE_PRIOR_FALLBACK"

    # Khi có 5 lệnh thực tế
    real_trades = [
        {"conviction": "A+", "pnl": 15000000.0},
        {"conviction": "A+", "pnl": 20000000.0},
        {"conviction": "A+", "pnl": -5000000.0},
        {"conviction": "A", "pnl": 8000000.0},
        {"conviction": "A", "pnl": -4000000.0},
    ]
    matrix_trades = engine.calibrate(realized_trades=real_trades, regime="BULL_MARKET", shrinkage_weight_n0=10.0)
    assert matrix_trades["A+"]["sample_size"] == 3
    assert matrix_trades["A"]["sample_size"] == 2
    assert matrix_trades["B"]["sample_size"] == 0  # Tier B chưa có lệnh thì phải bằng 0


def test_spearman_rank_ic_calculation():
    """Kiểm tra Spearman Rank IC phản ánh đúng tương quan thứ hạng."""
    engine = FactorPerformanceEngine(min_sample_threshold=5)

    # 10 mã có thứ hạng thuận hoàn hảo
    f_scores = {f"SYM_{i}": float(i * 10) for i in range(10)}
    f_returns = {f"SYM_{i}": float(i * 0.02) for i in range(10)}

    res = engine.calculate_rank_ic(f_scores, f_returns, factor_name="F1_Value")
    assert res.rank_ic > 0.95
    assert res.is_statistically_significant is True
    assert res.sample_size == 10


def test_agent10_dispatch_and_db_persistence():
    """Chạy toàn diện Agent-10 qua AgentRegistry và kiểm tra lưu trữ 100% CSDL."""
    async def _test():
        predictions = {
            f"TICKER_{i}": {
                "f1_value": 40 + i * 5,
                "f2_quality": 35 + i * 6,
                "f3_momentum": 30 + i * 7,
                "css": 45 + i * 5,
            }
            for i in range(10)
        }
        forward_rets = {f"TICKER_{i}": 0.015 * i - 0.05 for i in range(10)}

        moat_inputs = {
            "FPT": {
                "moat_score": 85.0,
                "financial_ratios": {"roic": 22.0, "wacc": 11.5, "gross_margin_delta_4q": 1.0}
            },
            "PHANTOM_CORP": {
                "moat_score": 90.0,
                "financial_ratios": {"roic": 6.0, "wacc": 12.0, "gross_margin_delta_4q": -6.0}
            }
        }

        realized_trades = [
            {"conviction": "A+", "pnl": 12000000.0},
            {"conviction": "A", "pnl": -3500000.0},
        ]

        res = await AgentRegistry.dispatch("reinforcement_learning", {
            "target_date": date.today(),
            "regime": "BULL_MARKET",
            "realized_trades": realized_trades,
            "factor_predictions": predictions,
            "forward_returns": forward_rets,
            "moat_assessments": moat_inputs,
        })

        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]

        # 1. Kiểm tra cấu trúc output chuẩn
        assert "policy_weights" in data
        assert "kelly_matrix" in data
        assert "ic_by_factor" in data
        assert "moat_calibrations" in data
        assert "governance_proposal" in data

        # 2. Kiểm tra Moat Calibration
        assert data["moat_calibrations"]["PHANTOM_CORP"]["hallucination_risk"] == "HIGH_HALLUCINATION"
        assert data["moat_calibrations"]["PHANTOM_CORP"]["penalty_factor"] > 0.40

        # 3. Kiểm tra không bịa sample size ảo
        assert data["kelly_matrix"]["A+"]["sample_size"] == 1
        assert data["kelly_matrix"]["B"]["sample_size"] == 0

        # 4. Kiểm tra CSDL thực tế
        storage = PostgresAdapter()
        rows_weights = storage.fetch_all("SELECT regime, f1_value_weight FROM rl_factor_weights WHERE regime = 'BULL_MARKET'")
        assert len(rows_weights) > 0

        rows_kelly = storage.fetch_all("SELECT conviction_tier, sample_count FROM kelly_win_rate_matrix WHERE regime = 'BULL_MARKET'")
        assert len(rows_kelly) >= 3

        # 5. Kiểm tra Cắm dây sang Agent-03: EquityResearch tiêu thụ moat_calibrations
        res_research = await AgentRegistry.dispatch("equity_research", {
            "ticker": "PHANTOM_CORP",
            "sector": "Real Estate",
            "current_regime": "BULL_MARKET",
            "policy_weights": data["policy_weights"],
            "moat_calibrations": data["moat_calibrations"],
        })
        assert res_research["status"] == "SUCCESS"
        research_data = res_research["result"]["data"]
        # Phải phát hiện cờ hiệu chuẩn Moat và điểm Moat bị kéo tụt
        assert "MOAT_CALIBRATED_AGENT10_HIGH_HALLUCINATION" in research_data["data_quality_flag"]
        assert research_data["moat_score"] < 65.0

    asyncio.run(_test())

