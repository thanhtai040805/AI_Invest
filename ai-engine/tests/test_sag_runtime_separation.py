from pathlib import Path

import pandas as pd
import pytest

from app.domain.rules.counter_thesis import CounterThesisEngine
from app.domain.rules.scoring import ConvictionLevel, CSSScoringEngine

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


def test_legacy_gil_modules_and_routes_are_removed():
    removed_ai_engine_paths = (
        ROOT / "app/adapters/sag_connector.py",
        ROOT / "app/adapters/sag_evidence_agent.py",
        ROOT / "app/domain/forensic_assessment.py",
        ROOT / "app/domain/pipeline/bctc_to_sag_pipeline.py",
        ROOT / "app/domain/services/document_selector.py",
        ROOT / "app/domain/repositories/bctc_pipeline_repository.py",
        ROOT / "app/domain/rules/bctc_ingestion_guard.py",
    )
    for path in removed_ai_engine_paths:
        assert not path.exists(), f"Legacy GIL module still exists: {path}"

    sag_root = ROOT.parent / "SAG" / "apps" / "api" / "sag_api"
    removed_sag_paths = (
        sag_root / "api/v1/gil.py",
        sag_root / "api/v1/analysis.py",
        sag_root / "api/v2/financial.py",
        sag_root / "services/gil_service.py",
        sag_root / "services/financial_v2_service.py",
        sag_root / "services/extraction_v2_service.py",
        sag_root / "extraction/compiler/gil_graph_compiler.py",
        sag_root / "extraction/frames/gil_frames.py",
    )
    for path in removed_sag_paths:
        assert not path.exists(), f"Legacy GIL SAG file still exists: {path}"
    assert not (ROOT.parent / "SAG" / "apps" / "api" / "packaging" / "sag-api.spec").exists()

    required_clean_sag_paths = (
        ROOT.parent / "SAG" / "compose.yaml",
        sag_root / "services/canonical_service.py",
        sag_root / "services/knowledge_service.py",
        sag_root / "services/routing_tree_service.py",
        sag_root / "services/search_unit_retrieval_service.py",
        sag_root / "core/token_usage.py",
    )
    for path in required_clean_sag_paths:
        assert path.exists(), f"Clean Knowledge Routing SAG component missing: {path}"


def test_compose_research_profile_uses_clean_sag_postgres_and_qdrant():
    compose = (ROOT.parent / "docker-compose.yml").read_text(encoding="utf-8")
    settings_src = (ROOT / "app" / "config" / "settings.py").read_text(encoding="utf-8")
    sag_config_src = (ROOT.parent / "SAG" / "apps" / "api" / "sag_api" / "core" / "config.py").read_text(encoding="utf-8")
    assert "sag-postgres:" in compose
    assert "sag-qdrant:" in compose
    assert "sag-api:" in compose
    assert "SAG_SAG_VECTOR_PROVIDER=qdrant" in compose
    assert "SAG_DOCUMENT_EVENT_EXTRACTION_ENABLED" not in compose
    assert "document_event_extraction_enabled: bool = False" in sag_config_src
    assert "SAG_AUTH_MODE=${SAG_AUTH_MODE:-continuum}" in compose
    assert "SAG_BFF_SERVICE_TOKEN=" in compose
    assert "SAG_ENVIRONMENT=${SAG_ENVIRONMENT:-dev}" in compose
    assert "QDRANT__SERVICE__API_KEY: ${SAG_QDRANT_API_KEY:-${SAG_SAG_QDRANT_API_KEY:-}}" in compose
    assert "SAG_MARKET_DATABASE_URL" not in compose
    assert "SAG_SERVICE_TOKEN" not in compose
    assert "SAG_SERVICE_TOKEN" not in settings_src
    assert "sag_service_token" not in settings_src

