"""Rule 08 L1 + L2 tests for the deterministic Playbook resolver & 0-LLM-token pre-fetch (`playbooks.py`).

Seams exercised unmocked:
- L1: All 8 `task_action` intents map to bounded 4–6 tool playbooks in canonical order; low-confidence
      `< 0.70` or `fallback_used=True` triggers the top-2 union safety valve; memory recall need adds
      `recall_session_memory`.
- L2: `run_research_agent_turn` pre-fetches deterministic `FactSet` + sector KPIs + risk flags (`[CALC-n]`)
      from REAL `AsyncPGCoreMetricsReader` BEFORE LLM Turn 1 (0 LLM tokens), injects `<PREFETCHED_EVIDENCE>`
      into Turn 1's user prompt, exposes only the playbook's focused tools to the model, and populates
      `playbook_id`, `playbook_mode`, `prefetched_tools`, and `exposed_tools` telemetry.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.types import (
    CancellationToken,
    ModelChunk,
    ModelRequest,
    ToolCall,
    Usage,
)
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.research.agent_runner import run_research_agent_turn
from sag_api.research.chat_orchestrator import RetrievalResult, RetrievedResearchSection
from sag_api.research.facts.store import effective_publication_date
from sag_api.research.financial_metrics_store import AsyncPGCoreMetricsReader
from sag_api.research.playbooks import (
    PLAYBOOK_CONFIDENCE_THRESHOLD,
    PLAYBOOKS,
    resolve_playbook,
)
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext

FIXTURE = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
AS_OF = date(2026, 11, 26)


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-pb",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-pb",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


class _FakeConn:
    def __init__(self, sample: dict[str, Any]) -> None:
        self._sample = sample

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        if "FROM financial_statements" in sql:
            as_of, _min_date, tickers, statement_types = params
            out = []
            for r in self._sample["rows"]:
                if tickers and r["symbol"] not in tickers:
                    continue
                if r["statement_type"].upper() not in statement_types:
                    continue
                pe = date.fromisoformat(r["period_end"])
                eff = effective_publication_date(pe, r["published_date"], r["frequency"])
                if pe <= as_of and eff <= as_of:
                    out.append({**r, "effective_published_date": eff})
            return out
        if "FROM stocks" in sql:
            return [
                {"ticker": sym, "company_name": sym, "sector": None, "industry": ind}
                for sym, ind in self._sample["industries"].items()
            ]
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    async def close(self) -> None:
        return None


def test_l1_all_router_intents_map_to_bounded_playbooks_and_safety_valve_unions() -> None:
    expected_actions = {
        "fact_lookup",
        "screening_ranking",
        "peer_comparison",
        "trend_attribution",
        "audit_discrepancy",
        "macro_and_value_chain",
        "deep_moat_thesis",
        "compute_or_scenario",
    }
    assert expected_actions <= set(PLAYBOOKS)

    # High-confidence route -> EXACT_PLAYBOOK with <= 6 focused tools (never all 12!)
    hi = resolve_playbook(
        {
            "task_action": "peer_comparison",
            "evidence_source_hint": "filings_and_tables",
            "confidence": 0.91,
            "axis_confidences": {"task_action": 0.88},
            "fallback_used": False,
            "memory_recall_need": "none",
        },
        chat_mode="MOAT",
        explicit_tickers=["ACB", "MBB"],
    )
    assert hi.playbook_id == "peer_comparison"
    assert hi.selection_mode == "EXACT_PLAYBOOK"
    assert 3 <= len(hi.exposed_tool_names) <= 6
    assert "compare_peers" in hi.prefetch_steps
    assert "search_disclosures" in hi.exposed_tool_names
    assert "search_corporate_filings" not in hi.exposed_tool_names

    # Low-confidence route (< PLAYBOOK_CONFIDENCE_THRESHOLD) -> LOW_CONFIDENCE_UNION of top-2 playbooks
    lo = resolve_playbook(
        {
            "task_action": "fact_lookup",
            "evidence_source_hint": "knowledge_graph_macro",
            "confidence": PLAYBOOK_CONFIDENCE_THRESHOLD - 0.15,
            "axis_confidences": {"task_action": PLAYBOOK_CONFIDENCE_THRESHOLD - 0.15},
            "fallback_used": False,
            "memory_recall_need": "cross_session_graph",
        },
        chat_mode="GENERAL",
        explicit_tickers=["ACB"],
    )
    assert lo.selection_mode == "LOW_CONFIDENCE_UNION"
    assert "query_knowledge_graph" in lo.exposed_tool_names
    assert "recall_session_memory" in lo.exposed_tool_names


@pytest.mark.asyncio
async def test_l2_agent_runner_prefetches_zero_token_facts_and_scopes_tools() -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))

    async def pool_factory(*_args: Any, **_kwargs: Any) -> _FakePool:
        return _FakePool(_FakeConn(sample))

    reader = AsyncPGCoreMetricsReader("postgresql://ro@localhost/aiinvest", pool_factory=pool_factory)

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    class _StubRetriever:
        async def retrieve(self, **_kwargs: Any) -> RetrievalResult:
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="ACB",
                        doc_id="d-acb",
                        document_version_id="v-acb",
                        sub_doc_slot="QUARTER_NOTES",
                        page_from=12,
                        page_to=12,
                        published_date=date(2026, 10, 25),
                        heading="Thuyết minh nợ xấu ACB Q3/2026",
                        content="Tỷ lệ nợ xấu nội bảng của ACB duy trì ở mức kiểm soát thấp.",
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    )
                ]
            )

    seen_turn1_prompt = ""
    seen_turn1_tools: list[str] = []

    class _InspectingModel:
        configured = True

        def __init__(self) -> None:
            self.turn = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            self.turn += 1
            if self.turn == 1:
                nonlocal seen_turn1_prompt, seen_turn1_tools
                seen_turn1_tools = [
                    t["function"]["name"]
                    if isinstance(t, dict) and "function" in t
                    else (t["name"] if isinstance(t, dict) else t.name)
                    for t in request.tools
                ]
                seen_turn1_prompt = "\n".join(
                    str(m.content) for m in request.messages if m.role == "user"
                )
                yield ModelChunk(
                    usage=Usage(input_tokens=600, output_tokens=60, cached_tokens=400),
                    tool_calls=(
                        ToolCall(
                            id="tc-disc",
                            name="search_disclosures",
                            arguments={
                                "tickers": ["ACB"],
                                "sub_doc_slots": ["QUARTER_NOTES"],
                                "fiscal_year": 2026,
                                "fiscal_quarter": 3,
                                "vas_terms": ["nợ xấu"],
                                "question": "Thuyết minh nợ xấu ACB Quý 3/2026",
                            },
                        ),
                    ),
                    finish_reason="tool_calls",
                )
                return
            yield ModelChunk(
                text_delta="ACB duy trì ROE dẫn đầu nhóm ngân hàng [CALC-2] và kiểm soát tốt nợ xấu [C1].",
                usage=Usage(input_tokens=800, output_tokens=90, cached_tokens=500),
                finish_reason="stop",
            )

    evidence = ResearchEvidenceLedger()
    rctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="MOAT",
        session_factory=sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=evidence,
        available_tickers=frozenset({"ACB"}),
        core_reader=reader,
        retriever_override=_StubRetriever(),
        enable_kg=False,
    )

    outcome = await run_research_agent_turn(
        query="Đánh giá chất lượng lợi nhuận và nợ xấu của ACB đến Q3/2026",
        model=_InspectingModel(),
        ctx=rctx,
        route_decision={
            "task_action": "deep_moat_thesis",
            "evidence_source_hint": "filings_and_tables",
            "confidence": 0.92,
            "axis_confidences": {"task_action": 0.90},
            "fallback_used": False,
            "memory_recall_need": "none",
            "max_agent_turns": 5,
        },
        explicit_tickers=["ACB"],
    )

    # 1. Pre-fetch ran BEFORE Turn 1 with 0 LLM tokens and injected [CALC-n] into Turn 1 user prompt!
    assert "<PREFETCHED_DETERMINISTIC_EVIDENCE>" in seen_turn1_prompt
    assert "[CALC-1]" in seen_turn1_prompt and "ROE" in seen_turn1_prompt
    assert len(evidence.calculator_traces) >= 5

    # 2. Only focused playbook tools were exposed on Turn 1 (<= 6 tools, no legacy duplicate)
    assert 3 <= len(seen_turn1_tools) <= 6
    assert "search_disclosures" in seen_turn1_tools
    assert "search_corporate_filings" not in seen_turn1_tools

    # 3. Playbook telemetry captured on outcome
    assert outcome.playbook_id == "deep_moat_thesis"
    assert outcome.playbook_mode == "EXACT_PLAYBOOK"
    assert "get_sector_kpis" in outcome.prefetched_tools
    assert "[CALC-2]" in outcome.answer_text and "[C1]" in outcome.answer_text
    await eng.dispose()
