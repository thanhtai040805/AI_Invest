"""Financial handoff and observation-only audits, without Agent startup or DB writes."""

import ast
import asyncio
import hashlib
import json
import logging
import math
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.core.base_agent import BaseAgent, _resolve_target_date
from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.repositories.replay_factor_snapshot import current_factor_snapshot
from app.domain.rules.counter_thesis import CounterThesisEngine
from app.domain.rules.scoring import CSSScoringEngine
from app.domain.rules.thesis_synthesizer import ThesisSynthesizer


ROOT = Path(__file__).resolve().parents[2]


def agent_class(filename, name):
    # Importing the Agent package starts all Agents and can write to LOCAL.
    path = ROOT / "app/domain/agents" / filename
    source = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    nodes += [node for node in source.body if isinstance(node, ast.ClassDef) and node.name == name]
    namespace = dict(globals(), logger=logging.getLogger(__name__))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)
    return namespace[name]


def ratio_row(**changes):
    fields = dict(pe=6.22, pb=0.42, roe=0.01398, roa=0.0035, debt_equity=1.0,
                  current_ratio=None, gross_margin=0.25939, net_margin=None, fcf_yield=None,
                  ev_ebitda=None, yoy_revenue_growth=None, yoy_earnings_growth=5.80333)
    fields.update(changes)
    return (date(2026, 6, 30), *fields.values(), date(2026, 10, 23), "quarterly")


@pytest.mark.parametrize("rows", [[], [ratio_row(roe=None)]])
def test_ratio_evidence_preserves_missing_values_without_changing_legacy_defaults(rows):
    storage = MagicMock()
    storage.fetch_all.return_value = rows
    repo = FinancialRepository(storage=storage)
    evidence = repo.get_latest_ratios("NVL", as_of=date(2026, 9, 16), allow_defaults=False)
    if rows:
        assert evidence["roe"] is None
        assert repo.get_latest_ratios("NVL")["roe"] == 0.0
    else:
        assert evidence is None
        assert repo.get_latest_ratios("NVL")["pe"] == 15.0


@pytest.mark.parametrize("changes", [{}, {"roe": 0.0, "gross_margin": -0.05, "yoy_earnings_growth": None}, {"roe": float("nan")}])
def test_research_to_thesis_handoff_uses_core_ratios_and_percent_units(monkeypatch, changes):
    storage = MagicMock()
    storage.fetch_all.return_value = [ratio_row(**changes)]
    monkeypatch.setattr(FinancialRepository, "__init__", lambda self: setattr(self, "storage", storage))
    cls = agent_class("equity_research.py", "EquityResearchAgent")
    agent = cls.__new__(cls)
    agent.scoring_engine = CSSScoringEngine()
    agent.factor_service = MagicMock()
    agent.intel_repo = MagicMock()
    agent.intel_repo.get_factor_score.return_value = {
        **{f"f{i}_{name}": 75.0 for i, name in enumerate(("value", "quality", "momentum", "earnings", "flow", "technical"), 1)},
        "factor_details": {"gross_margin": 91.8, "roe": 88.0},
    }
    event = dict(ticker="NVL", sector="Real Estate", current_price=12_150,
                 audit_opinion="UNQUALIFIED", target_date="2026-09-17", market_data_date="2026-09-16",
                 policy_weights={"f1_value": 1.0})
    report = asyncio.run(agent.process(event))["data"]
    metrics = report["financial_metrics"]
    assert metrics["pe"] == 6.22
    assert metrics["pb"] == 0.42
    assert metrics["gpm"] == pytest.approx(changes.get("gross_margin", 0.25939) * 100)
    assert metrics["earnings_growth"] == (None if "yoy_earnings_growth" in changes else pytest.approx(580.333))
    assert metrics["roe"] == (None if math.isnan(changes.get("roe", 0.01398)) else pytest.approx(changes.get("roe", 0.01398) * 100))
    assert storage.fetch_all.call_args.args[1] == ("NVL", "quarterly", date(2026, 9, 16))
    assert metrics["source"]["as_of"] == "2026-09-16"
    assert metrics["source"]["ratio_date"] == "2026-06-30"
    assert metrics["source"]["published_date"] == "2026-10-23"
    assert agent.intel_repo.log_equity_research.call_args.kwargs["factor_raw_metrics"]["financial_metrics"] == metrics

    thesis_cls = agent_class("investment_thesis.py", "InvestmentThesisAgent")
    thesis = thesis_cls.__new__(thesis_cls)
    thesis.thesis_engine = MagicMock()
    thesis.thesis_engine.build_structured_thesis_output.return_value = (True, {
        "thesis_id": "nvl-new", "ticker": "NVL", "input_validation": {"independent_signals": {}},
        "thesis_body": {
            "catalyst": {"primary_type": "Earnings", "description": "Earnings"},
            "price_target": {"base_case": 23_519, "target_range": [23_519, 24_695]},
            "exit_conditions": {"invalidation_triggers": ["Review earnings"]}, "pre_mortem": ["Earnings risk"],
        },
    }, "OK")
    thesis.catalyst_validator = MagicMock()
    thesis.catalyst_validator.check_peai_accumulation.return_value = "CLEAN"
    thesis.thesis_synthesizer = MagicMock()
    thesis.thesis_synthesizer.synthesize_thesis_narrative = AsyncMock(return_value=None)
    thesis.publish_event = AsyncMock()
    monkeypatch.setattr("app.domain.repositories.market_data_repository.MarketDataRepository.get_ohlcv", lambda *a, **k: [])
    monkeypatch.setattr("app.domain.repositories.intelligence_repository.IntelligenceRepository.save_investment_thesis", lambda *a, **k: True)
    asyncio.run(thesis.process(dict(event, research_report=report, valuation_inputs={"pe_price": 23_519},
                                   volume_data_3w=[1, 1, 1], price_data_3w=[12_150] * 3)))
    handed = thesis.thesis_synthesizer.synthesize_thesis_narrative.call_args.kwargs["financial_metrics"]
    assert handed == {**metrics, "roic": None, "f4_score": 75.0}
    prompt = ThesisSynthesizer().build_prompt(
        ticker="NVL", sector="Real Estate", business_quality_data={}, catalyst_type="Earnings",
        price_target_info={"base_case": 23_519}, current_price=12_150, timeline_months=3,
        financial_metrics=handed,
    )[1]["content"]
    assert "6.2x" in prompt and "0.42x" in prompt
    assert "91.8%" not in prompt
    if changes.get("roe") == 0.0:
        assert "ROE kỳ báo cáo: 0.0%" in prompt and "-5.0%" in prompt
        assert "không tự nhân 4" in prompt and "lũy kế" in prompt
    assert "nan%" not in prompt


SIGNALS = {"signal_1_factor": "PASS F4", "signal_2_surveillance": "PASS PEAI", "signal_3_macro_hmm": "PASS regime"}


@pytest.mark.parametrize("evidence,expected,reason", [
    ([{"path": "ticker", "quote": "NVL"}, {"path": "thesis_id", "quote": "NVL"}], True, "ACCEPTED_TWO_DISTINCT_FIELDS"),
    ([{"path": "ticker", "quote": "NVL"}] * 2, False, "DOWNGRADED_INSUFFICIENT_CITATIONS"),
    ([{"path": "missing", "quote": "NVL"}, {"path": "ticker", "quote": "bad"}], False, "DOWNGRADED_INSUFFICIENT_CITATIONS"),
])
def test_fatal_logging_explains_veto_without_tightening_existing_validator(evidence, expected, reason):
    response = dict(is_truly_independent=True, blindspot_penalty=16.0, fatal_flaw=True,
                    fatal_flaw_evidence=evidence, holes=[], rationale="Model rationale")
    llm = MagicMock(complete_json=AsyncMock(return_value=response))
    engine = CounterThesisEngine(llm_client=llm)
    report = asyncio.run(engine.evaluate_counter_thesis(
        "NVL", {"ticker": "NVL", "thesis_id": "NVL-17", "confirming_signals": SIGNALS},
        {"missing_data": 20.0}, {"current_regime": "RANGE_BOUND"}, {"current_price": 12_150},
    ))
    audit = report.llm_audit
    prompt = llm.complete_json.call_args.args[0]
    assert audit["parsed_response"] == response
    assert audit["prompt"] == prompt
    assert audit["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert audit["requested_fatal_flaw"] is True
    assert audit["accepted_fatal_flaw"] is expected
    assert audit["fatal_validation_result"] == reason
    assert audit["semantic_sufficiency_checked"] is False
    assert report.final_cts < 31
    assert audit["gate_trace"]["score_only_verdict"] == "PROCEED"
    assert report.verdict.value == ("BLOCK" if expected else "PROCEED")
    assert audit["gate_trace"]["fatal_veto_applied"] is expected


def test_counter_audit_is_per_call_and_records_errors():
    class LLM:
        async def complete_json(self, prompt):
            await asyncio.sleep(0)
            if "NVL-17" in prompt:
                raise TimeoutError("test timeout")
            return dict(is_truly_independent=True, blindspot_penalty=0, fatal_flaw=False)

    engine = CounterThesisEngine(llm_client=LLM())

    async def run():
        return await asyncio.gather(*[
            engine.evaluate_counter_thesis("NVL", {"thesis_id": f"NVL-{day}", "confirming_signals": SIGNALS},
                                           {}, {}, {"current_price": 12_150}) for day in (17, 18)
        ])

    failed, passed = asyncio.run(run())
    assert failed.llm_audit["status"] == "ERROR"
    assert failed.llm_audit["error_type"] == "TimeoutError"
    assert passed.llm_audit["status"] == "REVIEWED"
    assert passed.llm_audit["fatal_validation_result"] == "MODEL_DID_NOT_ASSERT_FATAL"
    assert failed.llm_audit["prompt_sha256"] != passed.llm_audit["prompt_sha256"]


def test_counter_audit_reaches_database_log_without_changing_event_payload(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr("app.infrastructure.database.pg_pool.get_conn", connection)
    for method in ("save_counter_thesis_verdict", "update_thesis_status"):
        monkeypatch.setattr(f"app.domain.repositories.intelligence_repository.IntelligenceRepository.{method}", lambda *a, **k: True)
    cls = agent_class("counter_thesis.py", "CounterThesisAgent")
    cls.process.__globals__["beneish_engine"] = MagicMock()
    cls.process.__globals__["beneish_engine"].calculate_m_score.return_value = {"m_score": -3.0}
    agent = cls.__new__(cls)
    BaseAgent.__init__(agent, "counter_thesis", [], "log_counter_thesis")
    agent.publish_event = AsyncMock()
    response = dict(is_truly_independent=True, blindspot_penalty=16, fatal_flaw=False, rationale="Same rationale")
    agent.counter_thesis_engine = CounterThesisEngine(llm_client=MagicMock(complete_json=AsyncMock(return_value=response)))
    output = asyncio.run(agent.run_event(dict(
        investment_thesis={"ticker": "NVL", "thesis_id": "NVL-18", "confirming_signals": SIGNALS},
        target_date="2026-09-18", is_replay=True, market_data={"current_regime": "RANGE_BOUND"},
        stock_data={"current_price": 12_500, "pe_ratio": 6.22, "pb_ratio": 0.42, "volume": 1000, "vol_ma20": 1000},
    )))
    assert output["status"] == "SUCCESS"
    cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    sql, params = cursor.execute.call_args.args
    assert "log_counter_thesis" in sql
    assert json.loads(params[3])["llm_audit"]["parsed_response"] == response
    assert "llm_audit" not in output["result"]["data"]
    assert "llm_audit" not in agent.publish_event.call_args.kwargs["payload"]


@pytest.mark.parametrize("lookup", ["found", "empty", "error"])
def test_cio_suspension_observation_is_logged_without_changing_current_decision(monkeypatch, lookup):
    connection = MagicMock()
    cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    prior = ("old-resolution", "old-thesis", "DISCRETIONARY_BLOCK",
             {"conditions": ["SUSPEND_PURCHASE_UNTIL_AUDITED"]}, datetime(2026, 9, 17, 9, 45, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
    cursor.fetchone.return_value = prior if lookup == "found" else None
    if lookup == "error":
        cursor.execute.side_effect = RuntimeError("test lookup failure")
    monkeypatch.setattr("app.infrastructure.database.pg_pool.get_conn", connection)
    cls = agent_class("strategy_cio.py", "StrategyCIOAgent")
    cio = cls.__new__(cls)
    cio._persist_audit_record = MagicMock(return_value="hash")
    cio._publish_cio_event = AsyncMock()
    event = dict(conflict={"ticker": "NVL", "thesis_id": "new-thesis", "cts_score": 35.3,
                           "counter_view": "CONDITIONAL"}, target_date="2026-09-18", is_replay=True)
    result = asyncio.run(cio.process(event))
    decision, trace = result["data"], result["trace"]
    assert decision["final_resolution"] == "PROCEED_WITH_PENALTY"
    assert decision["weight_cap"] == 0.08 and decision["penalty_factor"] == 0.5
    observation = trace["suspension_observation"]
    assert observation["lookup_status"] == {"found": "FOUND", "empty": "NOT_FOUND", "error": "ERROR"}[lookup]
    assert observation["prior_condition_enforced"] is False
    assert "suspension_observation" not in decision
    assert "suspension_observation" not in cio._persist_audit_record.call_args.kwargs["payload"]
    if lookup == "found":
        assert observation["prior_suspension"]["same_thesis"] is False
    sql, params = cursor.execute.call_args.args
    assert "created_at < %s" in sql and "is_replay" in sql
    assert "resolution_id <> %s" in sql and params[3] == decision["resolution_id"]
    assert params[1] == datetime(2026, 9, 18, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    assert params[2] == "true"

    # The audit row contains the observation; the published/hashed decision stays unchanged.
    cursor.execute.side_effect = None
    cio.log_table = "log_strategy_cio"
    asyncio.run(cio._log_audit_trace(event, trace, decision, "SUCCESS"))
    _, params = cursor.execute.call_args.args
    assert json.loads(params[2])["suspension_observation"] == observation
