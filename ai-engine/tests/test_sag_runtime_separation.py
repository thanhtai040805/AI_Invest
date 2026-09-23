from pathlib import Path

import pandas as pd
import pytest

from app.domain.rules.counter_thesis import CounterThesisEngine
from app.domain.rules.scoring import CSSScoringEngine, ConvictionLevel
from app.infrastructure.vendors.vn.signals import SIGNAL_HOLD, determine_signal


ROOT = Path(__file__).parents[1]


def test_counter_thesis_ignores_retired_provider_features_and_preserves_scale():
    engine = CounterThesisEngine()
    independent = {
        "beneish_risk": 50.0,
        "receivable_spike": 50.0,
        "macro_headwind": 50.0,
        "liquidity_stress": 50.0,
        "missing_data": 50.0,
        "gil_risk": 100.0,
        "graph_rpt_risk": 100.0,
    }
    assert engine.calculate_base_cts(independent) == 50.0
    assert engine.calculate_base_cts({"gil_risk": 100.0, "graph_rpt_risk": 100.0}) == 0.0


def test_css_requires_audit_but_not_sag_evidence_and_normalizes_policy_weights():
    engine = CSSScoringEngine()
    frame = pd.DataFrame([{
        "sector": "General",
        "f1_value": 80.0,
        "f2_quality": 80.0,
        "f3_momentum": 80.0,
        "f4_earnings": 80.0,
        "f5_flow": 80.0,
        "f6_technical": 80.0,
        "audit_opinion": "UNQUALIFIED",
    }])
    scored = engine.calculate_css(frame, custom_weights={key: 2.0 for key in engine.regime_weights["DEFAULT"]})
    assert scored.loc[0, "css"] == pytest.approx(80.0)
    assert scored.loc[0, "conviction"] == ConvictionLevel.A.value


def test_signals_use_current_risk_flags_only():
    assert determine_signal(50.0, False, 0) == SIGNAL_HOLD


def test_decision_runtime_has_no_sag_or_legacy_gil_dependency():
    runtime_files = [
        ROOT / "app/domain/agents/counter_thesis.py",
        ROOT / "app/domain/agents/equity_research.py",
        ROOT / "app/domain/agents/investment_thesis.py",
        ROOT / "app/domain/agents/strategy_cio.py",
        ROOT / "app/domain/agents/system_governance.py",
        ROOT / "app/domain/agents/trade_execution.py",
        ROOT / "app/domain/agents/universe_discovery.py",
        ROOT / "app/domain/rules/counter_thesis.py",
        ROOT / "app/domain/rules/governance/compliance_engine.py",
        ROOT / "app/domain/rules/risk/confidence_scorer.py",
        ROOT / "app/domain/rules/scoring.py",
        ROOT / "app/domain/rules/strategic_memo_generator.py",
        ROOT / "app/domain/rules/thesis_engine.py",
        ROOT / "app/domain/rules/thesis_synthesizer.py",
        ROOT / "app/domain/rules/universe_manager.py",
        ROOT / "app/domain/pipeline/daily_pipeline_orchestrator.py",
        ROOT / "app/domain/repositories/__init__.py",
        ROOT / "app/domain/repositories/intelligence_repository.py",
        ROOT / "app/infrastructure/risk_queries.py",
        ROOT / "app/infrastructure/vendors/vn/signals.py",
    ]
    forbidden = (
        "SAGConnector",
        "ForensicAssessment",
        "forensic_assessment",
        "gil_ocr_score",
        "SAG_CLOSED",
        "business_quality_profiles",
        "source_sag_doc_id",
    )
    for path in runtime_files:
        source = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token.lower() not in source, f"{token} remains in runtime file {path}"

    for path in runtime_files:
        if path.name not in {"universe_discovery.py", "universe_manager.py"}:
            source = path.read_text(encoding="utf-8").lower()
            assert "gil_flag" not in source, f"legacy GIL output remains in {path}"


def test_compose_does_not_make_ai_engine_depend_on_sag():
    compose = (ROOT.parent / "docker-compose.yml").read_text(encoding="utf-8")
    ai_engine = compose.split("  ai-engine:", 1)[1].split("  backend:", 1)[0]
    assert "sag-api" not in ai_engine
    assert "SAG_API_BASE" not in ai_engine
