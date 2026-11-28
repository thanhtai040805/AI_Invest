"""TDD Red -> Green Suite for V3.1 Buy-Side Workflow, 360-Degree Relationship Graph & Stage-7 Parity."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.tools import ToolContext
from sag_agent.types import CancellationToken, ModelChunk, ModelProvider, ModelRequest, Usage
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models.stock_research import KgEdge, KgNode
from sag_api.research.brief import BriefEntities, ClaimSpec, ResearchBrief, validate_research_brief
from sag_api.research.brief_executor import LocatedTargets, _parse_doc_spec, execute_brief_claims
from sag_api.research.chat_orchestrator import RetrievalResult, RetrievedResearchSection
from sag_api.research.kg_store import InMemoryKGStore
from sag_api.research.market_pulse import (
    _SIGNAL_LABEL_VI,
    _load_pit_subsidiary_summary,
    clear_market_pulse_cache,
)
from sag_api.research.quote_verifier import verify_numeric_claims_against_evidence
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext
from sag_api.research.tools.research_tools import query_knowledge_graph_tool
from sag_api.schemas.research_chat import GraphHopItem


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-v31-test",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-v31",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


class _DelayedDocModel(ModelProvider):
    """Deterministic async ModelProvider that sleeps `delay_s` on each Mini-Reader call."""

    def __init__(self, delay_s: float = 0.16) -> None:
        self.delay_s = delay_s
        self.call_count = 0

    async def stream_turn(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelChunk]:
        self.call_count += 1
        await asyncio.sleep(self.delay_s)
        yield ModelChunk(
            text_delta=(
                '{"status": "CONFIRMED", "findings": ["Ban lãnh đạo giải trình cơ chế mở rộng thị trường '
                'kèm điều kiện kiểm soát vốn lưu động [C1]."], "anomaly_followup_query": null}'
            ),
            usage=Usage(requests=1, input_tokens=80, output_tokens=30),
        )


class _StubRetriever:
    async def retrieve(self, **kwargs: Any) -> RetrievalResult:
        tk = (kwargs.get("tickers") or ["ACB"])[0]
        sec = RetrievedResearchSection(
            ticker=tk,
            doc_id=f"doc-{tk}",
            document_version_id=f"ver-{tk}",
            sub_doc_slot="AGM_TARGETS",
            page_from=12,
            page_to=12,
            published_date=date(2026, 4, 15),
            content="Ban lãnh đạo giải trình cơ chế mở rộng thị trường kèm điều kiện kiểm soát vốn lưu động.",
            heading="AGM > Thảo luận",
            block_from_id=f"b-{tk}-1",
            block_to_id=f"b-{tk}-1",
        )
        return RetrievalResult(sections=[sec], hipporag_hops=1)


@pytest.mark.asyncio
async def test_t1_relationship_summary_deduplicates_subsidiaries_preserves_acb_insurance_and_maps_f1_f8(
    tmp_path,
) -> None:
    """Verify:
    1) All 8 V3 economic facets `F1..F8` have clean Vietnamese labels in `_SIGNAL_LABEL_VI` (no raw `F1_...` leak).
    2) Duplicate punctuation/abbreviation rows (`ACBS ("ACBS")` vs `ACBS`, `ACBL`, `ACBA`, `ACBC`) collapse so
       `Công ty TNHH Bảo hiểm phi nhân thọ ACB` is preserved inside top 6 instead of pushed out at row #9!
    3) `TRUE_PEER_OF` and `MACRO_SENSITIVE_TO` (`AUDITED_NOTE` without `beta=0.000`) surface cleanly.
    """
    clear_market_pulse_cache()
    for fid in (
        "F1_GROWTH_BACKLOG_MARKET",
        "F2_MARGIN_PRICING_COST",
        "F3_FUNDING_CASA_LIQUIDITY",
        "F4_FEE_ECOSYSTEM_IB",
        "F5_ASSET_CREDIT_RISK",
        "F6_CONSOLIDATION_MNA_DIVIDEND",
        "F7_CAPEX_TECH_NEW_BET",
        "F8_GOVERNANCE_HUMAN_CAPITAL",
    ):
        assert fid in _SIGNAL_LABEL_VI
        assert not _SIGNAL_LABEL_VI[fid].startswith("F")

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'v31_pulse.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    eff = date(2026, 1, 1)
    vto = date(9999, 12, 31)
    async with sf() as s:
        s.add_all(
            [
                KgNode(
                    node_id="TICKER:ACB",
                    node_type="COMPANY",
                    label="NH TMCP Á Châu",
                    ticker="ACB",
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="TICKER:MBB",
                    node_type="COMPANY",
                    label="NH TMCP Quân Đội",
                    ticker="MBB",
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="MACRO:usd_vnd_exchange",
                    node_type="MACRO_FACTOR",
                    label="usd_vnd_exchange",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbs_1",
                    node_type="SUBSIDIARY",
                    label='Công ty TNHH Chứng khoán ACB ("ACBS")',
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbs_2",
                    node_type="SUBSIDIARY",
                    label="Công ty TNHH Chứng khoán ACB",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbl_1",
                    node_type="SUBSIDIARY",
                    label='Công ty TNHH Một thành viên Cho thuê Tài chính Ngân hàng Á Châu ("ACBL")',
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbl_2",
                    node_type="SUBSIDIARY",
                    label="Công ty TNHH MTV Cho thuê tài chính Ngân hàng Á Châu",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acba_1",
                    node_type="SUBSIDIARY",
                    label='Công ty TNHH Quản lý Nợ và Khai thác Tài sản Ngân hàng Á Châu ("ACBA")',
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acba_2",
                    node_type="SUBSIDIARY",
                    label="Công ty TNHH Quản lý nợ và khai thác tài sản Ngân hàng Á Châu",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbc_1",
                    node_type="SUBSIDIARY",
                    label='Công ty TNHH Một thành viên Quản lý Quỹ ACB ("ACBC") (*)',
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbc_2",
                    node_type="SUBSIDIARY",
                    label="Công ty TNHH MTV Quản lý quỹ ACB",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
                KgNode(
                    node_id="CORP:ACB:acbi",
                    node_type="SUBSIDIARY",
                    label="Công ty TNHH Bảo hiểm phi nhân thọ ACB",
                    ticker=None,
                    metadata_json={},
                    effective_from=eff,
                    valid_to=vto,
                ),
            ]
        )
        for idx, dst in enumerate(
            [
                "CORP:ACB:acbs_1",
                "CORP:ACB:acbs_2",
                "CORP:ACB:acbl_1",
                "CORP:ACB:acbl_2",
                "CORP:ACB:acba_1",
                "CORP:ACB:acba_2",
                "CORP:ACB:acbc_1",
                "CORP:ACB:acbc_2",
                "CORP:ACB:acbi",
            ],
            1,
        ):
            s.add(
                KgEdge(
                    edge_id=f"e-sub-{idx}",
                    src_node_id="TICKER:ACB",
                    dst_node_id=dst,
                    relation_type="OWNS_SUBSIDIARY",
                    layer="VALUE_CHAIN",
                    direction="DIRECTED",
                    weight=1.0,
                    verification_tier="DOC_ONLY",
                    evidence_json={"ownership_pct": 100.0},
                    effective_from=eff,
                    valid_to=vto,
                )
            )
        s.add(
            KgEdge(
                edge_id="e-peer-1",
                src_node_id="TICKER:ACB",
                dst_node_id="TICKER:MBB",
                relation_type="TRUE_PEER_OF",
                layer="TRUE_PEER",
                direction="UNDIRECTED",
                weight=0.68,
                verification_tier="CLUSTER",
                evidence_json={},
                effective_from=eff,
                valid_to=vto,
            )
        )
        s.add(
            KgEdge(
                edge_id="e-macro-1",
                src_node_id="MACRO:usd_vnd_exchange",
                dst_node_id="TICKER:ACB",
                relation_type="MACRO_SENSITIVE_TO",
                layer="MACRO",
                direction="DIRECTED",
                weight=0.0,
                beta=0.0,
                verification_tier="DOC_ONLY",
                evidence_json={"channel": "AUDITED_NOTE", "target_metric": "PBT_IMPACT"},
                effective_from=eff,
                valid_to=vto,
            )
        )
        await s.commit()

        rel_lines = await _load_pit_subsidiary_summary(s, tickers=["ACB"], as_of_date=date(2026, 11, 28))
        joined = "\n".join(rel_lines)
        assert "Bảo hiểm phi nhân thọ ACB" in joined
        # Must NOT repeat Chứng khoán ACB twice!
        assert joined.lower().count("chứng khoán acb") == 1
        assert "MBB" in joined or "Quân Đội" in joined
        assert "0.000" not in joined

    await eng.dispose()


def test_t2_parse_doc_spec_strips_leaked_facet_codes_cleanly() -> None:
    slots, years, quarters, q_clean = _parse_doc_spec(
        "doc:BCTN_CORE|F1_GROWTH_BACKLOG_MARKET tăng vốn chủ sở hữu nhờ lợi nhuận",
        "FY2025",
    )
    assert slots == ["BCTN_CORE"]
    assert years == [2025]
    assert quarters == []
    assert "F1_GROWTH_BACKLOG_MARKET" not in q_clean
    assert "tăng vốn chủ sở hữu" in q_clean

    # Pure facet-only query falls back to meaningful Vietnamese phrase instead of empty string!
    _, _, _, q_fallback = _parse_doc_spec("doc:BCTN_CORE|F5_ASSET_CREDIT_RISK", "Q3/2026")
    assert "F5_ASSET_CREDIT_RISK" not in q_fallback
    assert len(q_fallback) >= 6


@pytest.mark.asyncio
async def test_t3_query_knowledge_graph_tool_balances_3_layers_humanizes_labels_and_hides_zero_beta() -> None:
    kg = InMemoryKGStore()
    eff = "2026-01-01"
    await kg.upsert_nodes(
        [
            {
                "node_id": "TICKER:FPT",
                "node_type": "COMPANY",
                "label": "CTCP FPT",
                "ticker": "FPT",
                "effective_from": eff,
            },
            {
                "node_id": "TICKER:FRT",
                "node_type": "COMPANY",
                "label": "CTCP Bán lẻ Kỹ thuật số FPT",
                "ticker": "FRT",
                "effective_from": eff,
            },
            {
                "node_id": "CORP:FPT:ftel",
                "node_type": "ASSOCIATE",
                "label": "CTCP Viễn thông FPT (FTEL)",
                "ticker": None,
                "effective_from": eff,
            },
        ]
        + [
            {
                "node_id": f"TICKER:P{i}",
                "node_type": "COMPANY",
                "label": f"Peer {i}",
                "ticker": f"P{i}",
                "effective_from": eff,
            }
            for i in range(1, 12)
        ]
        + [
            {
                "node_id": "MACRO:usd_vnd_exchange",
                "node_type": "MACRO_FACTOR",
                "label": "Tỷ giá USD/VND",
                "ticker": None,
                "effective_from": eff,
            }
        ]
    )
    # Insert 10 TRUE_PEER edges with high weights + 1 AUDITED_NOTE macro edge (beta=0.0) + 2 VALUE_CHAIN associate edges
    edges: list[dict[str, Any]] = [
        {
            "src_node_id": "TICKER:FPT",
            "dst_node_id": f"TICKER:P{i}",
            "relation_type": "TRUE_PEER_OF",
            "layer": "TRUE_PEER",
            "weight": 0.95 - i * 0.01,
            "verification_tier": "CLUSTER",
            "effective_from": eff,
        }
        for i in range(1, 11)
    ]
    edges.append(
        {
            "src_node_id": "MACRO:usd_vnd_exchange",
            "dst_node_id": "TICKER:FPT",
            "relation_type": "MACRO_SENSITIVE_TO",
            "layer": "MACRO",
            "weight": 0.0,
            "beta": 0.0,
            "verification_tier": "DOC_ONLY",
            "evidence_json": {"channel": "AUDITED_NOTE", "target_metric": "PBT_IMPACT"},
            "effective_from": eff,
        }
    )
    edges.append(
        {
            "src_node_id": "TICKER:FPT",
            "dst_node_id": "CORP:FPT:ftel",
            "relation_type": "ASSOCIATE_OF",
            "layer": "VALUE_CHAIN",
            "weight": 0.4566,
            "verification_tier": "DOC_ONLY",
            "evidence_json": {"ownership_pct": 45.66},
            "effective_from": eff,
        }
    )
    edges.append(
        {
            "src_node_id": "TICKER:FPT",
            "dst_node_id": "TICKER:FRT",
            "relation_type": "ASSOCIATE_OF",
            "layer": "VALUE_CHAIN",
            "weight": 0.465,
            "verification_tier": "DOC_ONLY",
            "evidence_json": {"ownership_pct": 46.5},
            "effective_from": eff,
        }
    )
    await kg.upsert_edges(edges)

    @asynccontextmanager_null
    async def _null_sf():
        yield None

    ctx = ResearchToolContext(
        principal=_principal(),
        as_of_date=date(2026, 11, 28),
        chat_mode="GENERAL",
        enable_kg=True,
        available_tickers=frozenset({"FPT", "FRT"}),
        session_ledger=FinancialZeroMemLedger(request_as_of_date=date(2026, 11, 28)),
        evidence=ResearchEvidenceLedger(),
        session_factory=_null_sf,
        kg_store_override=kg,
    )

    async def _noop(_p: Any) -> None:
        return None

    tctx = ToolContext(
        run_id="r-v31",
        tool_call_id="kg-1",
        data=ctx,
        cancellation=CancellationToken(),
        _on_progress=_noop,
    )
    res = await query_knowledge_graph_tool.execute({"tickers": ["FPT"], "limit": 8}, tctx)
    hops = res.details["kg_hops"]
    layers_returned = {h["layer"] for h in hops}
    # All 3 layers MUST be represented despite 10 high-weight TRUE_PEER edges!
    assert {"VALUE_CHAIN", "TRUE_PEER", "MACRO"}.issubset(layers_returned)
    # Human-readable label MUST replace raw `CORP:FPT:ftel`, while `TICKER:FPT` stays preserved!
    vc_hops = [h for h in hops if h["layer"] == "VALUE_CHAIN"]
    assert any("Viễn thông FPT" in h["dst_label"] and h.get("ownership_pct") == 45.66 for h in vc_hops)
    assert any("TICKER:FPT" in h["src_label"] for h in vc_hops)
    # AUDITED_NOTE macro edge MUST NOT expose `beta == 0.0`!
    macro_hops = [h for h in hops if h["layer"] == "MACRO"]
    assert macro_hops[0].get("beta") is None
    assert macro_hops[0].get("note_channel") == "AUDITED_NOTE"
    assert "beta=0.000" not in res.content


from contextlib import asynccontextmanager as asynccontextmanager_null  # noqa: E402


@pytest.mark.asyncio
async def test_t4_single_wave_parallel_for_descriptive_premise_auto_kg_and_directional_refutation() -> None:
    """Verify:
    Case A: Descriptive non-directional `P1` + `K1, K2` (`depends_on=["P1"]`, `0` `"kg"` in `evidence`)
      -> All 3 (`P1`, `K1`, `K2`) execute in ONE parallel Wave 1 (`< 0.31s` vs `>= 0.34s` serial) AND
         auto-sweep populates `ctx.evidence.graph_hops`!
    Case B: Directional false premise (`"NIM ACB sập mạnh trong Q3/2026"`)
      -> `P1` STILL evaluates to `REFUTED` and `K1` (`depends_on=["P1"]`) STILL skips doc search!
    """
    kg = InMemoryKGStore()
    await kg.upsert_nodes(
        [
            {
                "node_id": "TICKER:ACB",
                "node_type": "COMPANY",
                "label": "NH TMCP Á Châu",
                "ticker": "ACB",
                "effective_from": "2026-01-01",
            },
            {
                "node_id": "CORP:ACB:acbi",
                "node_type": "SUBSIDIARY",
                "label": "Công ty TNHH Bảo hiểm phi nhân thọ ACB",
                "ticker": None,
                "effective_from": "2026-01-01",
            },
        ]
    )
    await kg.upsert_edges(
        [
            {
                "src_node_id": "TICKER:ACB",
                "dst_node_id": "CORP:ACB:acbi",
                "relation_type": "OWNS_SUBSIDIARY",
                "layer": "VALUE_CHAIN",
                "weight": 1.0,
                "verification_tier": "DOC_ONLY",
                "evidence_json": {"ownership_pct": 100.0},
                "effective_from": "2026-01-01",
            }
        ]
    )

    @asynccontextmanager_null
    async def _null_sf():
        yield None

    # --- Case A: Descriptive P1 + K1, K2 depending on P1 ---
    brief_open = ResearchBrief(
        user_concern="Câu chuyện tăng trưởng và hệ sinh thái ACB",
        answer_shape="verdict_with_reasons",
        entities=BriefEntities(tickers=["ACB"]),
        premises_to_check=[
            ClaimSpec(
                id="P1",
                statement="ACB đang mở rộng hệ sinh thái dịch vụ tài chính phi tín dụng.",
                evidence=["doc:AGM_TARGETS|hệ sinh thái dịch vụ"],
                period="Q3/2026",
            )
        ],
        claims=[
            ClaimSpec(
                id="K1",
                statement="Động lực khách hàng FDI và doanh nghiệp lớn",
                evidence=["doc:AGM_TARGETS|khách hàng FDI"],
                period="Q3/2026",
                depends_on=["P1"],
            ),
            ClaimSpec(
                id="K2",
                statement="Cấu trúc công ty con bảo hiểm phi nhân thọ và chứng khoán",
                evidence=["doc:AGM_TARGETS|bảo hiểm phi nhân thọ"],
                period="Q3/2026",
                depends_on=["P1"],
            ),
        ],
    )
    ctx_a = ResearchToolContext(
        principal=_principal(),
        as_of_date=date(2026, 11, 28),
        chat_mode="GENERAL",
        enable_kg=True,
        available_tickers=frozenset({"ACB"}),
        session_ledger=FinancialZeroMemLedger(request_as_of_date=date(2026, 11, 28)),
        evidence=ResearchEvidenceLedger(),
        session_factory=_null_sf,
        retriever_override=_StubRetriever(),
        kg_store_override=kg,
    )
    model_a = _DelayedDocModel(delay_s=0.17)
    t0 = time.perf_counter()
    res_a = await execute_brief_claims(
        brief=brief_open,
        located=LocatedTargets(analysis_tickers=["ACB"]),
        ctx=ctx_a,
        model=model_a,
    )
    elapsed_a = time.perf_counter() - t0
    assert model_a.call_count == 3
    # If serial (Wave 1 P1=0.17s -> Wave 2 K1||K2=0.17s), elapsed >= 0.34s. Single-wave parallel < 0.30s!
    assert elapsed_a < 0.31, f"Expected single-wave parallel execution (<0.31s), took {elapsed_a:.3f}s"
    assert len(res_a.claim_cards) == 2
    assert len(ctx_a.evidence.graph_hops) >= 1
    assert any("Bảo hiểm phi nhân thọ ACB" in ln for c in res_a.claim_cards for ln in c.summary_lines)

    # --- Case B: Directional false premise ("NIM ACB sập mạnh") MUST STILL refute & block K1 doc search ---
    class _BankNimCoreReader:
        async def fetch_issuer_profiles(self, tickers: Any = None) -> list[dict[str, Any]]:
            del tickers
            return [{"ticker": "ACB", "company_name": "NH Á Châu", "sector": "Tài chính", "industry": "Ngân hàng"}]

        async def fetch_pit_financial_statements(self, *a: Any, **kw: Any) -> list[dict[str, Any]]:
            del a, kw
            return [
                {
                    "symbol": "ACB",
                    "period_end": date(2026, 6, 30),
                    "statement_type": "RATIOS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 7, 25),
                    "data": {"rt_bank_nim": 0.0360},
                },
                {
                    "symbol": "ACB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "RATIOS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"rt_bank_nim": 0.0358},
                },
            ]

    val_b = validate_research_brief(
        {
            "user_concern": "Kiểm tra NIM ACB sập",
            "answer_shape": "premise_audit_and_explanation",
            "entities": {"tickers": ["ACB"]},
            "premises_to_check": [
                {
                    "id": "P1",
                    "statement": "NIM của ACB sập mạnh trong Q3/2026",
                    "evidence": ["fact:vr_nim"],
                    "period": "Q3/2026",
                }
            ],
            "claims": [
                {
                    "id": "K1",
                    "statement": "Nguyên nhân NIM sập",
                    "evidence": ["doc:QUARTER_NOTES|biên lãi thuần"],
                    "period": "Q3/2026",
                    "depends_on": ["P1"],
                }
            ],
        },
        as_of_date=date(2026, 11, 28),
        allowed_project_ids=frozenset({"*"}),
    )
    assert val_b.ok and val_b.brief is not None
    ctx_b = ResearchToolContext(
        principal=_principal(),
        as_of_date=date(2026, 11, 28),
        chat_mode="GENERAL",
        enable_kg=False,
        available_tickers=frozenset({"ACB"}),
        session_ledger=FinancialZeroMemLedger(request_as_of_date=date(2026, 11, 28)),
        evidence=ResearchEvidenceLedger(),
        session_factory=_null_sf,
        core_reader=_BankNimCoreReader(),
        retriever_override=_StubRetriever(),
    )
    model_b = _DelayedDocModel(delay_s=0.05)
    res_b = await execute_brief_claims(
        brief=val_b.brief,
        located=LocatedTargets(analysis_tickers=["ACB"]),
        ctx=ctx_b,
        model=model_b,
    )
    assert res_b.premise_cards[0].status == "REFUTED"
    assert model_b.call_count == 0


def test_t5_stage7_verifies_kg_ownership_pct_strictly_by_owner_and_percent_unit() -> None:
    hops = [
        GraphHopItem(
            layer="VALUE_CHAIN",
            src_label="TICKER:FPT (CTCP FPT)",
            relation_type="ASSOCIATE_OF",
            dst_label="CTCP Viễn thông FPT (FTEL) (45.66%)",
            verification_tier="DOC_ONLY",
            effective_from="2026-01-01",
            ownership_pct=45.66,
        ),
        GraphHopItem(
            layer="VALUE_CHAIN",
            src_label="TICKER:ACB (NH TMCP Á Châu)",
            relation_type="OWNS_SUBSIDIARY",
            dst_label="Công ty TNHH Bảo hiểm phi nhân thọ ACB (100.0%)",
            verification_tier="DOC_ONLY",
            effective_from="2026-01-01",
            ownership_pct=100.0,
        ),
    ]
    # 1) Valid ownership_pct on owner FPT passes cleanly!
    ok_res = verify_numeric_claims_against_evidence(
        "FPT hiện nắm 45,66% vốn tại công ty liên kết FTEL [KG:VALUE_CHAIN|DOC_ONLY].",
        sections_by_cid={},
        hydrated_tables=[],
        calculator_traces=[],
        graph_hops=hops,
    )
    assert ok_res.unsupported_claims == []

    # 2) Cross-ticker laundering onto ACB MUST fail!
    bad_tk = verify_numeric_claims_against_evidence(
        "ACB hiện nắm 45,66% vốn tại công ty liên kết [KG:VALUE_CHAIN|DOC_ONLY].",
        sections_by_cid={},
        hydrated_tables=[],
        calculator_traces=[],
        graph_hops=hops,
    )
    assert len(bad_tk.unsupported_claims) == 1

    # 3) Unit-class laundering (PERCENT -> MONETARY `45,66 tỷ`) MUST fail!
    bad_unit = verify_numeric_claims_against_evidence(
        "FPT thu về 45,66 tỷ đồng từ công ty liên kết [KG:VALUE_CHAIN|DOC_ONLY].",
        sections_by_cid={},
        hydrated_tables=[],
        calculator_traces=[],
        graph_hops=hops,
    )
    assert len(bad_unit.unsupported_claims) == 1


def test_t6_stage7_allows_related_party_disclosure_and_fpt_long_chau_brand_without_cross_laundering() -> None:
    """Verify exact fixes for B1_FPT (`Doanh thu từ FRT ... [C1]` in FPT's BCTC) and B3_FRT (`FPT Long Châu`)."""
    sections = {
        "C1": {
            "ticker": "FPT",
            "doc_id": "d-fpt",
            "page_from": 38,
            "heading": "Giao dịch bên liên quan",
            "content": "Bán hàng hóa và dịch vụ cho Công ty CP Bán lẻ Kỹ thuật số FPT (FRT): 535.438.239.866 đồng.",
        },
        "C2": {
            "ticker": "FRT",
            "doc_id": "d-frt",
            "page_from": 5,
            "heading": "Kết quả kinh doanh 2025",
            "content": "Lợi nhuận trước thuế hợp nhất đạt 1.219 tỷ đồng, tăng 131,35% dẫn dắt bởi FPT Long Châu.",
        },
    }
    # 1) Related-party disclosure in FPT's BCTC mentioning FRT (`535.438.239.866 đồng [C1]`) MUST pass!
    r1 = verify_numeric_claims_against_evidence(
        "Doanh thu từ FRT năm 2025 đạt 535.438.239.866 đồng [C1].",
        sections_by_cid=sections,
    )
    assert r1.unsupported_claims == []

    # 2) `FPT Long Châu` brand phrase on FRT sentence (`1.219 tỷ đồng [C2]`) MUST bind to FRT (NOT FPT)!
    r2 = verify_numeric_claims_against_evidence(
        "Lợi nhuận trước thuế năm 2025 đạt 1.219 tỷ đồng, tăng 131,35% nhờ chuỗi FPT Long Châu [C2].",
        sections_by_cid=sections,
    )
    assert r2.unsupported_claims == []

    # 3) Unrelated cross-ticker laundering (`ACB đạt 1.219 tỷ đồng [C2]` when C2 never mentions ACB) MUST fail!
    r3 = verify_numeric_claims_against_evidence(
        "ACB đạt lợi nhuận 1.219 tỷ đồng [C2].",
        sections_by_cid={
            **sections,
            "C3": {
                "ticker": "ACB",
                "doc_id": "d-acb",
                "page_from": 1,
                "heading": "H",
                "content": "LNTT đạt 20.000 tỷ.",
            },
        },
    )
    assert len(r3.unsupported_claims) == 1
