"""Comprehensive unit & integration tests for Option A+ (Unified Multi-Tenant Knowledge Chamber
+ Layered Cross-Company Bridge) and all Mandatory P0 PIT & HOSE Ingestion Fixes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import fitz
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    SearchUnit,
    Source,
    SourceProjectMapping,
)
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.enums import SourceStatus


def _principal(
    *,
    tenant: str = "aiinvest-default",
    allowed_projects: frozenset[str] = frozenset({"*"}),
) -> VerifiedPrincipal:
    now_ts = int(datetime.now(UTC).timestamp())
    return VerifiedPrincipal(
        subject="user-test",
        organization_id=tenant,
        allowed_project_ids=allowed_projects,
        issuer="continuum-bff",
        key_id="k1",
        token_id="tok-1",
        issued_at=now_ts,
        expires_at=now_ts + 3600,
        tenant_id=tenant,
        allowed_partition_ids=allowed_projects,
    )


def _make_doc(
    doc_id: str,
    *,
    project_id: str,
    tenant_id: str = "aiinvest-default",
    source_id: str | None = None,
    logical_source_id: str | None = None,
    is_active: bool = True,
) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        logical_source_id=logical_source_id,
        filename=f"{project_id.lower()}_{doc_id}.pdf",
        storage_path=f"/tmp/{project_id.lower()}_{doc_id}.pdf",
        is_active=is_active,
    )


# ── 1. Layer 1: Deterministic `mentioned_tickers` & Shared-Chamber Filters ───


def test_extract_mentioned_tickers_zero_hardcode() -> None:
    from sag_api.research.kg_entity_linker import (
        compile_issuer_registry_from_rows,
        extract_mentioned_tickers,
    )

    registry = compile_issuer_registry_from_rows(
        [
            {"symbol": "PVT", "name": "Tổng Công ty Cổ phần Vận tải Dầu khí"},
            {"symbol": "FPT", "name": "Công ty Cổ phần FPT"},
            {"symbol": "MWG", "name": "Công ty Cổ phần Đầu tư Thế Giới Di Động"},
        ]
    )
    text = (
        "Trong năm 2025, Công ty Cổ phần FPT hợp tác chuyển đổi số với "
        "Tổng Công ty Cổ phần Vận tải Dầu khí (HOSE: PVT) và hợp đồng phân phối "
        "cùng MWG. Công ty tiếp tục hỗ trợ khách hàng (cho) việc phát hành cổ phiếu mới "
        "với ROE đạt 22%, doanh thu tính bằng VND và USD theo BCTC hợp nhất."
    )
    mentioned = extract_mentioned_tickers(
        text,
        own_ticker="FPT",
        known_tickers={"FPT", "PVT", "MWG"},
        compiled_issuers=registry,
    )
    assert "PVT" in mentioned
    assert "MWG" in mentioned
    assert "FPT" not in mentioned  # Self-exclusion invariant
    assert "CHO" not in mentioned  # Lowercase Vietnamese word in parens must never match [A-Z]{3}
    for acronym in ("ROE", "VND", "USD", "HOSE", "BCTC"):
        assert acronym not in mentioned


def test_qdrant_payload_and_option_a_plus_filters() -> None:
    from sag_api.sag.search_unit_store import (
        build_cross_ref_search_filter,
        build_universe_search_filter,
    )
    from sag_api.services.search_index_service import PAYLOAD_INDEX_FIELDS, build_qdrant_payload

    assert ("mentioned_tickers", "keyword") in PAYLOAD_INDEX_FIELDS

    unit = SimpleNamespace(
        id="su-1",
        document_version_id="ver-1",
        security_partition_id="FPT",
        section_path="Thuyết minh 12",
        page_from=12,
        page_to=12,
        block_from_id="blk-1",
        block_to_id="blk-1",
        content_hash="h1",
        token_count=32,
        table_id="tbl-1",
    )
    ver = SimpleNamespace(
        id="ver-1",
        document_id="doc-1",
        valid_from=datetime(2025, 4, 1, tzinfo=UTC),
        valid_to=datetime(2026, 4, 1, tzinfo=UTC),
        source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
        metadata_json={"sub_doc_slot": "BCTC_AUDITED", "fiscal_year": 2025, "fiscal_quarter": 0},
    )
    payload = build_qdrant_payload(
        unit,
        version=ver,
        project_id="FPT",
        tenant_id="aiinvest-default",
        source_id="src-1",
        document_id="doc-1",
        content="FPT ký hợp đồng dịch vụ với PVT",
        mentioned_tickers=["PVT", "MWG", "FPT"],
    )
    assert payload["mentioned_tickers"] == ["MWG", "PVT"]  # Self FPT stripped & sorted
    assert payload["fiscal_year"] == 2025
    assert payload["fiscal_quarter"] == 0
    assert payload["table_id"] == "tbl-1"
    assert payload["valid_to_ts"] == int(datetime(2026, 4, 1, tzinfo=UTC).timestamp())

    as_of_ts = int(datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
    xref_filter = build_cross_ref_search_filter(
        tenant_id="aiinvest-default",
        mentioned_tickers=["pvt"],
        exclude_project_ids=["pvt"],
        allowed_project_ids=frozenset({"*"}),
        allowed_document_version_ids=["ver-1", "ver-2"],
        as_of_ts=as_of_ts,
    )
    assert {"key": "tenant_id", "match": {"value": "aiinvest-default"}} in xref_filter["must"]
    assert {"key": "mentioned_tickers", "match": {"any": ["PVT"]}} in xref_filter["must"]
    assert xref_filter["must_not"] == [{"key": "project_id", "match": {"any": ["PVT"]}}]
    assert {"key": "valid_from_ts", "range": {"lte": as_of_ts}} in xref_filter["must"]

    univ_filter = build_universe_search_filter(
        tenant_id="aiinvest-default",
        allowed_project_ids=["FPT", "PVT"],
        allowed_document_version_ids=["ver-1"],
        as_of_ts=as_of_ts,
    )
    assert {"key": "tenant_id", "match": {"value": "aiinvest-default"}} in univ_filter["must"]
    assert {"key": "project_id", "match": {"any": ["FPT", "PVT"]}} in univ_filter["must"]


# ── 2. Layer 1: Leiden `_partition` Zero Hash-Slicing & Zero Singletons ──────


def test_routing_tree_partition_no_hash_slicing_and_zero_singletons() -> None:
    from sag_api.services.routing_tree_service import (
        KnowledgeEdgeInput,
        KnowledgeUnitInput,
        TreeBuildConfig,
        build_routing_snapshot,
    )

    units: list[KnowledgeUnitInput] = []
    edges: list[KnowledgeEdgeInput] = []
    for doc_idx in range(3):
        doc_id = f"doc-{doc_idx}"
        for local_ord in range(84):
            uid = f"ku-{doc_idx}-{local_ord:03d}"
            units.append(
                KnowledgeUnitInput(
                    unit_id=uid,
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    security_partition_id="FPT",
                    sparse=((f"doc{doc_idx}", 1.0), (f"sec{local_ord // 10}", 0.8)),
                    document_id=doc_id,
                    ordinal=local_ord,
                    section_path=f"Section {local_ord // 10}",
                )
            )
            if local_ord > 0:
                prev_uid = f"ku-{doc_idx}-{local_ord - 1:03d}"
                edges.append(
                    KnowledgeEdgeInput(
                        source_unit_id=prev_uid,
                        target_unit_id=uid,
                        weight=1.0,
                    )
                )

    cfg = TreeBuildConfig(
        target_cluster_size=6,
        min_cluster_size=2,
        max_cluster_size=20,
        max_children=8,
        leiden_resolution=0.08,
    )
    snap = build_routing_snapshot(units, edges, config=cfg)

    def _collect_leaves(nodes: tuple[Any, ...]) -> list[Any]:
        out: list[Any] = []
        for n in nodes:
            if not n.children:
                out.append(n)
            else:
                out.extend(_collect_leaves(n.children))
        return out

    leaves = _collect_leaves(snap.roots)
    assert leaves
    assigned_ku_ids = [uid for leaf in leaves for uid in leaf.unit_ids]
    assert sorted(assigned_ku_ids) == sorted(u.unit_id for u in units)
    singletons = [leaf for leaf in leaves if len(leaf.unit_ids) == 1]
    assert len(singletons) == 0  # Zero singletons (was 43-52% under old hash slicing!)
    max_docs_per_leaf = max(
        len({uid.split("-")[1] for uid in leaf.unit_ids}) for leaf in leaves
    )
    assert max_docs_per_leaf <= 2


# ── 3. Layer 1 & 2: Skip Raw Table Block When Verified `table_card` Exists ───


@pytest.mark.asyncio
async def test_knowledge_service_skips_raw_table_and_links_verified_table_cards() -> None:
    from sag_api.services.knowledge_service import (
        build_financial_knowledge_config,
        rebuild_knowledge_version,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now_dt = datetime(2025, 4, 1, tzinfo=UTC)
    async with session_factory() as session:
        session.add(
            Source(
                id="src-fpt",
                name="FPT Source",
                sag_source_config_id="cfg-fpt",
                status=SourceStatus.ACTIVE,
            )
        )
        session.add(
            SourceProjectMapping(
                source_id="src-fpt",
                organization_id="aiinvest-default",
                project_id="FPT",
                state="CONFIRMED",
                confirmed_at=datetime(2025, 4, 1, tzinfo=UTC),
                confirmed_by="tester",
                approval_ref="ref-1",
            )
        )
        session.add(_make_doc("doc-fpt", project_id="FPT", source_id="src-fpt"))
        session.add(
            DocumentVersion(
                id="ver-fpt",
                document_id="doc-fpt",
                version_no=1,
                file_hash="h-fpt",
                valid_from=now_dt,
                source_published_at=now_dt,
                search_status="SEARCH_READY",
                metadata_json={
                    "sub_doc_slot": "BCTC_AUDITED",
                    "fiscal_year": 2025,
                    "security_partition_id": "FPT",
                },
            )
        )
        import hashlib

        prose_txt = (
            "Thuyết minh 12 trình bày chi tiết doanh thu thuần của FPT đạt 70.110 tỷ đồng "
            "trong năm tài chính 2025, tăng trưởng nhờ khối công nghệ thông tin toàn cầu."
        )
        raw_tbl_txt = "| Chỉ tiêu | 2025 |\n|---|---|\n| Doanh thu | 70110 |"
        tcard_txt = "[TABLE_CARD table_id=tbl-fpt-rev] Doanh thu thuần 2025: 70.110 tỷ đồng"
        session.add_all(
            [
                CanonicalBlock(
                    id="blk-prose-1",
                    document_version_id="ver-fpt",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTC_AUDITED > Thuyết minh 12",
                    page_from=12,
                    page_to=12,
                    normalized_text=prose_txt,
                    content_hash=hashlib.sha256(prose_txt.encode()).hexdigest(),
                ),
                CanonicalBlock(
                    id="blk-raw-tbl",
                    document_version_id="ver-fpt",
                    ordinal=2,
                    block_type="table",
                    section_path="BCTC_AUDITED > Thuyết minh 12",
                    page_from=12,
                    page_to=12,
                    normalized_text=raw_tbl_txt,
                    content_hash=hashlib.sha256(raw_tbl_txt.encode()).hexdigest(),
                ),
                CanonicalBlock(
                    id="blk-tcard",
                    document_version_id="ver-fpt",
                    ordinal=3,
                    block_type="table_card",
                    section_path="BCTC_AUDITED > Thuyết minh 12",
                    page_from=12,
                    page_to=12,
                    source_anchor="table_card:tbl-fpt-rev:0",
                    normalized_text=tcard_txt,
                    content_hash=hashlib.sha256(tcard_txt.encode()).hexdigest(),
                    dedup_signature_json={"table_id": "tbl-fpt-rev", "verification_status": "VERIFIED"},
                ),
            ]
        )
        session.add(
            SearchUnit(
                id="su-tcard-1",
                document_version_id="ver-fpt",
                security_partition_id="FPT",
                block_from_id="blk-tcard",
                block_to_id="blk-tcard",
                page_from=12,
                page_to=12,
                section_path="BCTC_AUDITED > Thuyết minh 12",
                token_count=20,
                content_hash="uh-tc-1",
            )
        )
        await session.flush()

        units = await rebuild_knowledge_version(
            session,
            "ver-fpt",
            config=build_financial_knowledge_config(),
        )
        assert units
        all_source_block_ids = [
            bid for u in units for bid in (u.provenance_json or {}).get("evidence_block_ids", [])
        ]
        assert "blk-prose-1" in all_source_block_ids
        assert "blk-raw-tbl" not in all_source_block_ids
        assert "blk-tcard" not in all_source_block_ids
        linked_ids = [
            tid for u in units for tid in (u.provenance_json or {}).get("linked_table_ids", [])
        ]
        assert "tbl-fpt-rev" in linked_ids
    await engine.dispose()


# ── 4. Layer 1: Bypass Broken Leiden `branch_local` on Research Retrieval ────


@pytest.mark.asyncio
async def test_sag_retriever_adapter_bypasses_leiden_branch_local() -> None:
    from sag_api.research.chat_orchestrator import SAGRetrieverAdapter
    from sag_api.sag.dto import SearchOutcome

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            Source(
                id="src-fpt",
                name="FPT Source",
                sag_source_config_id="cfg-fpt",
                status=SourceStatus.ACTIVE,
            )
        )
        session.add(
            SourceProjectMapping(
                source_id="src-fpt",
                organization_id="aiinvest-default",
                project_id="FPT",
                state="CONFIRMED",
                confirmed_at=datetime(2025, 4, 1, tzinfo=UTC),
                confirmed_by="tester",
                approval_ref="ref-1",
            )
        )
        session.add(_make_doc("doc-fpt", project_id="FPT", source_id="src-fpt"))
        await session.flush()

        captured_kwargs: dict[str, Any] = {}

        async def _fake_retrieve_su(*args: Any, **kwargs: Any) -> SearchOutcome:
            captured_kwargs.update(kwargs)
            return SearchOutcome(query="Doanh thu FPT 2025", sections=[], stats={})

        adapter = SAGRetrieverAdapter(session, engine_manager=SimpleNamespace())
        with (
            patch(
                "sag_api.services.source_service.versioned_source_ids",
                new=AsyncMock(return_value={"src-fpt"}),
            ),
            patch(
                "sag_api.services.search_unit_retrieval_service.retrieve_search_unit_sections",
                new=_fake_retrieve_su,
            ),
        ):
            await adapter.retrieve(
                query="Doanh thu FPT 2025",
                tickers=["FPT"],
                as_of_date=date(2026, 6, 1),
                top_k=10,
                principal=_principal(),
            )
        assert captured_kwargs.get("bypass_tree_routing") is True
    await engine.dispose()


# ── 5. Layer 3: Dual-Channel `PRIMARY` + `CROSS-REF`, `NO_DIRECT_FILING`, Hygiene ─


@pytest.mark.asyncio
async def test_layer3_dual_channel_cross_ref_hygiene_and_no_direct_filing() -> None:
    from sag_api.research.chat_orchestrator import (
        ResearchChatOrchestrator,
        RetrievalResult,
        RetrievedResearchSection,
        SAGRetrieverAdapter,
        collect_stream_result,
    )
    from sag_api.research.quote_verifier import compute_quote_hash
    from sag_api.schemas.research_chat import ResearchChatStreamRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pub_dt = datetime(2025, 4, 15, tzinfo=UTC)
    pvt_quote = "Doanh thu thuần vận tải dầu khí của PVT năm 2025 đạt 16.012 tỷ đồng."
    fpt_xref_quote = (
        "Trong năm 2025, FPT triển khai hợp đồng ERP cho đối tác PVT và hợp tác với (HOSE: XYZ) tại khu công nghiệp."
    )

    async with session_factory() as session:
        session.add_all(
            [
                _make_doc("doc-pvt", project_id="PVT"),
                _make_doc("doc-fpt", project_id="FPT"),
            ]
        )
        session.add_all(
            [
                DocumentVersion(
                    id="ver-pvt",
                    document_id="doc-pvt",
                    version_no=1,
                    file_hash="h-pvt",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTC_AUDITED", "fiscal_year": 2025},
                ),
                DocumentVersion(
                    id="ver-fpt",
                    document_id="doc-fpt",
                    version_no=1,
                    file_hash="h-fpt",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTN_CORE", "fiscal_year": 2025},
                ),
            ]
        )
        session.add_all(
            [
                CanonicalBlock(
                    id="blk-pvt-1",
                    document_version_id="ver-pvt",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTC_AUDITED > Kết quả kinh doanh",
                    page_from=5,
                    page_to=5,
                    normalized_text=pvt_quote,
                    content_hash=compute_quote_hash(pvt_quote),
                ),
                CanonicalBlock(
                    id="blk-fpt-1",
                    document_version_id="ver-fpt",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTN_CORE > Đối tác chiến lược",
                    page_from=18,
                    page_to=18,
                    normalized_text=fpt_xref_quote,
                    content_hash=compute_quote_hash(fpt_xref_quote),
                ),
            ]
        )
        session.add_all(
            [
                SagExtractedTable(
                    table_id="tbl-pvt-is",
                    project_id="PVT",
                    document_version_id="ver-pvt",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title="Báo cáo KQKD PVT",
                    section_path="BCTC_AUDITED",
                    page_from=5,
                    page_to=5,
                    currency="VND",
                    unit_raw="tỷ đồng",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "2025", "2024"]],
                    rows_json=[
                        [
                            {"raw_text": "Doanh thu thuần", "numeric_value": None},
                            {"raw_text": "16.012", "numeric_value": 16012.0},
                            {"raw_text": "12.000", "numeric_value": 12000.0},
                        ]
                    ],
                    verification_status="VERIFIED",
                    table_hash="th-pvt",
                    valid_from=pub_dt,
                ),
                SagExtractedTable(
                    table_id="tbl-fpt-is",
                    project_id="FPT",
                    document_version_id="ver-fpt",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title="Báo cáo KQKD FPT",
                    section_path="BCTC_AUDITED",
                    page_from=6,
                    page_to=6,
                    currency="VND",
                    unit_raw="tỷ đồng",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "2025", "2024"]],
                    rows_json=[
                        [
                            {"raw_text": "Doanh thu thuần", "numeric_value": None},
                            {"raw_text": "70.110", "numeric_value": 70110.0},
                            {"raw_text": "62.800", "numeric_value": 62800.0},
                        ]
                    ],
                    verification_status="VERIFIED",
                    table_hash="th-fpt",
                    valid_from=pub_dt,
                ),
            ]
        )
        await session.flush()

        class _HybridTestRetriever(SAGRetrieverAdapter):
            async def retrieve(
                self,
                *,
                query: str,
                tickers: Any,
                as_of_date: date,
                top_k: int,
                principal: VerifiedPrincipal | None = None,
            ) -> RetrievalResult:
                if "PVT" in [t.upper() for t in tickers]:
                    return RetrievalResult(
                        sections=[
                            RetrievedResearchSection(
                                ticker="PVT",
                                doc_id="doc-pvt",
                                document_version_id="ver-pvt",
                                sub_doc_slot="BCTC_AUDITED",
                                page_from=5,
                                page_to=5,
                                published_date=date(2025, 4, 15),
                                content=pvt_quote,
                                heading="Kết quả kinh doanh",
                                table_id="tbl-pvt-is",
                                score=0.92,
                                block_from_id="blk-pvt-1",
                                block_to_id="blk-pvt-1",
                                evidence_origin="PRIMARY",
                            )
                        ]
                    )
                return RetrievalResult()

        captured_prompts: list[str] = []

        class _MockLLM:
            configured = True

            async def stream_complete(self, messages: list[dict[str, str]]):
                captured_prompts.append(messages[-1]["content"])
                yield "PVT đạt doanh thu 16.012 tỷ [C1] và hợp tác ERP với đối tác FPT [C2]."

        orch = ResearchChatOrchestrator(
            session=session,
            retriever=_HybridTestRetriever(session),
            llm=_MockLLM(),
        )
        principal = _principal()

        # Case A: PVT query with cross-ref intent pulls BOTH PRIMARY (PVT) and CROSS-REF (FPT)
        _, meta_a, _ = await collect_stream_result(
            orch.stream_chat(
                ResearchChatStreamRequest(
                    ticker="PVT",
                    query="Doanh thu PVT và quan hệ đối tác triển khai ERP như thế nào?",
                    as_of_date=date(2026, 6, 1),
                    enable_kg=False,
                ),
                principal=principal,
            )
        )
        assert "[PRIMARY]" in captured_prompts[-1]
        assert "[CROSS-REF via FPT" in captured_prompts[-1]
        cits_a = meta_a["citations"]
        assert any(c["ticker"] == "PVT" and c["evidence_origin"] == "PRIMARY" and c["verified"] for c in cits_a)
        assert any(c["ticker"] == "FPT" and c["evidence_origin"] == "CROSS_REF" and c["verified"] for c in cits_a)
        # Primary-only hygiene invariant: ONLY PVT's table hydrated (NEVER FPT's table!)
        assert [t["table_id"] for t in meta_a["hydrated_tables"]] == ["tbl-pvt-is"]

        # Case B: Uncovered ticker XYZ has 0 direct filings BUT FPT mentions (HOSE: XYZ)
        class _MockLLMXyz:
            configured = True

            async def stream_complete(self, messages: list[dict[str, str]]):
                yield "Theo báo cáo của FPT, FPT có hợp tác với XYZ [C1]."

        orch_xyz = ResearchChatOrchestrator(
            session=session,
            retriever=_HybridTestRetriever(session),
            llm=_MockLLMXyz(),
        )
        ans_b, meta_b, _ = await collect_stream_result(
            orch_xyz.stream_chat(
                ResearchChatStreamRequest(
                    query="Thông tin hợp tác của XYZ là gì?",
                    as_of_date=date(2026, 6, 1),
                    enable_kg=False,
                ),
                principal=principal,
            )
        )
        assert "INSUFFICIENT_EVIDENCE:" not in ans_b
        bs_codes_b = {b["code"] for b in meta_b["declared_blindspots"]}
        assert "NO_DIRECT_FILING" in bs_codes_b
        assert "NO_DOC_COVERAGE" not in bs_codes_b
        assert meta_b["hydrated_tables"] == []
        assert any(
            c["ticker"] == "FPT" and c["evidence_origin"] == "CROSS_REF" and c["verified"]
            for c in meta_b["citations"]
        )

        # Case C: Uncovered ticker ZZZ has 0 direct filings AND 0 cross-ref mentions -> fail-closed NO_DOC_COVERAGE
        ans_c, meta_c, _ = await collect_stream_result(
            orch_xyz.stream_chat(
                ResearchChatStreamRequest(
                    query="Phân tích ZZZ năm 2025?",
                    as_of_date=date(2026, 6, 1),
                    enable_kg=False,
                ),
                principal=principal,
            )
        )
        assert "INSUFFICIENT_EVIDENCE" in ans_c
        assert any(b["code"] == "NO_DOC_COVERAGE" for b in meta_c["declared_blindspots"])
    await engine.dispose()


# ── 6. Layer 3: Open Universe Shared-Chamber Qdrant Hybrid & Truncation ──────


@pytest.mark.asyncio
async def test_layer3_open_universe_qdrant_normalization_and_truncation_blindspot() -> None:
    from sag_api.research.chat_orchestrator import ResearchChatOrchestrator, collect_stream_result
    from sag_api.sag.search_unit_store import SearchUnitHit
    from sag_api.schemas.research_chat import ResearchChatStreamRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tickers_8 = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "HPG", "PVT"]
    pub_dt = datetime(2025, 4, 1, tzinfo=UTC)

    async with session_factory() as session:
        for tk in tickers_8:
            session.add(_make_doc(f"doc-{tk}", project_id=tk))
            session.add(
                DocumentVersion(
                    id=f"ver-{tk}",
                    document_id=f"doc-{tk}",
                    version_no=1,
                    file_hash=f"h-{tk}",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                )
            )
        await session.flush()

        fake_hits: list[SearchUnitHit] = [
            SearchUnitHit("pt-pvt-1", 0.99, {"project_id": "PVT", "document_version_id": "ver-PVT"}),
            SearchUnitHit("pt-pvt-2", 0.95, {"project_id": "PVT", "document_version_id": "ver-PVT"}),
        ]
        for idx, tk in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], 1):
            fake_hits.append(
                SearchUnitHit(f"pt-{tk}", 0.70 - idx * 0.02, {"project_id": tk, "document_version_id": f"ver-{tk}"})
            )
        for j in range(10):
            fake_hits.append(
                SearchUnitHit(f"pt-hpg-{j}", 0.40 - j * 0.01, {"project_id": "HPG", "document_version_id": "ver-HPG"})
            )

        fake_em = SimpleNamespace(get_search_unit_qdrant_client=AsyncMock(return_value=object()))
        orch = ResearchChatOrchestrator(session=session, engine_manager=fake_em)
        with patch(
            "sag_api.sag.search_unit_store.SearchUnitQdrantStore.search_batch",
            new=AsyncMock(return_value=[fake_hits]),
        ):
            _, meta, _ = await collect_stream_result(
                orch.stream_chat(
                    ResearchChatStreamRequest(
                        query="Doanh nghiệp nào tăng trưởng đội tàu vận tải mạnh nhất?",
                        as_of_date=date(2026, 6, 1),
                        enable_kg=False,
                    ),
                    principal=_principal(),
                )
            )

        assert len(meta["expanded_tickers"]) == 6
        assert meta["expanded_tickers"][0] == "PVT"
        assert "HPG" not in meta["expanded_tickers"]  # 10 low-rank hits must NOT outscore top hits
        assert any(b["code"] == "TRUNCATED_UNIVERSE_SCOPE" for b in meta["declared_blindspots"])
    await engine.dispose()


# ── 7. P0 PIT Fixes: TableStore, QuoteVerifier, Bi-Temporal KG, Supersede/Revoke ─


@pytest.mark.asyncio
async def test_p0_pit_table_store_and_quote_verifier() -> None:
    from sag_api.research.chat_orchestrator import SQLAlchemyTableStore
    from sag_api.research.quote_verifier import (
        CanonicalBlockRecord,
        compute_quote_hash,
        load_canonical_blocks_for_versions,
        verify_citations,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                _make_doc("doc-act", project_id="FPT", tenant_id="t1", is_active=True),
                _make_doc("doc-inact", project_id="FPT", tenant_id="t1", is_active=False),
            ]
        )
        session.add_all(
            [
                DocumentVersion(
                    id="ver-old",
                    document_id="doc-act",
                    version_no=1,
                    file_hash="h0",
                    valid_from=datetime(2024, 4, 1, tzinfo=UTC),
                    valid_to=datetime(2025, 4, 1, tzinfo=UTC),
                    source_published_at=datetime(2024, 4, 1, tzinfo=UTC),
                    status="SUPERSEDED",
                ),
                DocumentVersion(
                    id="ver-curr",
                    document_id="doc-act",
                    version_no=2,
                    file_hash="h1",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                    valid_to=datetime(2026, 4, 1, tzinfo=UTC),
                    source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
                    status="READY",
                ),
                DocumentVersion(
                    id="ver-future",
                    document_id="doc-act",
                    version_no=3,
                    file_hash="h2",
                    valid_from=datetime(2026, 4, 1, tzinfo=UTC),
                    valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
                    source_published_at=datetime(2026, 4, 1, tzinfo=UTC),
                    status="READY",
                ),
                DocumentVersion(
                    id="ver-revoked",
                    document_id="doc-act",
                    version_no=4,
                    file_hash="h3",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                    valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
                    source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
                    status="REVOKED",
                ),
                DocumentVersion(
                    id="ver-inact",
                    document_id="doc-inact",
                    version_no=1,
                    file_hash="h4",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                    valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
                    source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
                    status="READY",
                ),
            ]
        )
        for tid, vid, vf in (
            ("tbl-old", "ver-old", datetime(2024, 4, 1, tzinfo=UTC)),
            ("tbl-curr", "ver-curr", datetime(2025, 4, 1, tzinfo=UTC)),
            ("tbl-future", "ver-future", datetime(2026, 4, 1, tzinfo=UTC)),
            ("tbl-revoked", "ver-revoked", datetime(2025, 4, 1, tzinfo=UTC)),
            ("tbl-inact", "ver-inact", datetime(2025, 4, 1, tzinfo=UTC)),
        ):
            session.add(
                SagExtractedTable(
                    table_id=tid,
                    project_id="FPT",
                    document_version_id=vid,
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title=f"Table {tid}",
                    section_path="BCTC_AUDITED",
                    page_from=1,
                    page_to=1,
                    currency="VND",
                    unit_raw="tỷ đồng",
                    unit_multiplier=1e9,
                    headers_json=["Chỉ tiêu", "2025"],
                    rows_json=[],
                    verification_status="VERIFIED",
                    table_hash=f"th-{tid}",
                    valid_from=vf,
                )
            )
            session.add(
                CanonicalBlock(
                    id=f"blk-{vid}",
                    document_version_id=vid,
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTC_AUDITED",
                    page_from=1,
                    page_to=1,
                    normalized_text=f"Evidence block for {vid}",
                    content_hash=f"bh-{vid}",
                )
            )
        await session.flush()

        store = SQLAlchemyTableStore(session)
        as_of = date(2025, 12, 31)
        tables = await store.get_tables(table_ids=[], tickers=["FPT"], as_of_date=as_of)
        assert [t["table_id"] for t in tables] == ["tbl-curr"]
        assert await store.get_table_by_id("tbl-curr", as_of_date=as_of) is not None
        assert await store.get_table_by_id("tbl-old", as_of_date=as_of) is None
        assert await store.get_table_by_id("tbl-future", as_of_date=as_of) is None
        assert await store.get_table_by_id("tbl-revoked", as_of_date=as_of) is None
        assert await store.get_table_by_id("tbl-inact", as_of_date=as_of) is None

        loaded = await load_canonical_blocks_for_versions(
            session,
            ["ver-old", "ver-curr", "ver-future", "ver-revoked", "ver-inact"],
            as_of_date=as_of,
        )
        assert list(loaded.keys()) == ["ver-curr"]

        q_old = "Evidence block for ver-old"
        superseded_blk = CanonicalBlockRecord(
            document_version_id="ver-old",
            page_from=1,
            page_to=1,
            normalized_text=q_old,
            published_date=date(2024, 4, 1),
            valid_from=datetime(2024, 4, 1, tzinfo=UTC),
            valid_to=datetime(2025, 4, 1, tzinfo=UTC),
            is_active=True,
        )
        vres = verify_citations(
            [
                {
                    "citation_id": "C1",
                    "document_version_id": "ver-old",
                    "sub_doc_slot": "BCTC_AUDITED",
                    "page": 1,
                    "published_date": "2024-04-01",
                    "quote": q_old,
                    "quote_hash": compute_quote_hash(q_old),
                }
            ],
            {"ver-old": [superseded_blk]},
            as_of,
        )
        assert not vres.verified_citations
        assert any(b["code"] == "OUT_OF_PIT_WINDOW" for b in vres.blindspots)
    await engine.dispose()


@pytest.mark.asyncio
async def test_p0_pit_bitemporal_kg_store_preserves_history() -> None:
    from sag_api.research.kg_store import InMemoryKGStore, SQLAlchemyKGStore

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        stores = [InMemoryKGStore(), SQLAlchemyKGStore(session)]
        for st in stores:
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:PVT",
                        "node_type": "COMPANY",
                        "label": "PVT Legacy Name",
                        "ticker": "PVT",
                        "effective_from": date(2025, 1, 1),
                    },
                    {
                        "node_id": "MACRO:brent",
                        "node_type": "MACRO_FACTOR",
                        "label": "Brent Oil",
                        "effective_from": date(2025, 1, 1),
                    },
                ]
            )
            await st.upsert_edges(
                [
                    {
                        "src_node_id": "MACRO:brent",
                        "dst_node_id": "TICKER:PVT",
                        "layer": "MACRO",
                        "relation_type": "MACRO_SENSITIVE_TO",
                        "beta": 0.25,
                        "verification_tier": "DOC_ONLY",
                        "effective_from": date(2025, 1, 1),
                    }
                ]
            )
            await st.upsert_macro_snapshots(
                [
                    {
                        "target_level": "COMPANY",
                        "target_id": "TICKER:PVT",
                        "macro_factor": "brent",
                        "beta": 0.25,
                        "verification_tier": "DOC_ONLY",
                        "effective_from": date(2025, 1, 1),
                    }
                ]
            )
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:PVT",
                        "node_type": "COMPANY",
                        "label": "PVT Updated Name",
                        "ticker": "PVT",
                        "effective_from": date(2026, 1, 1),
                    }
                ]
            )
            await st.upsert_edges(
                [
                    {
                        "src_node_id": "MACRO:brent",
                        "dst_node_id": "TICKER:PVT",
                        "layer": "MACRO",
                        "relation_type": "MACRO_SENSITIVE_TO",
                        "beta": 0.80,
                        "verification_tier": "CONFIRMED_BOTH",
                        "effective_from": date(2026, 1, 1),
                    }
                ]
            )
            await st.upsert_macro_snapshots(
                [
                    {
                        "target_level": "COMPANY",
                        "target_id": "TICKER:PVT",
                        "macro_factor": "brent",
                        "beta": 0.80,
                        "verification_tier": "CONFIRMED_BOTH",
                        "effective_from": date(2026, 1, 1),
                    }
                ]
            )

            hist = await st.get_subgraph_for_ticker("PVT", date(2025, 6, 1))
            hist_pvt = next(n for n in hist["nodes"] if n["node_id"] == "TICKER:PVT")
            assert hist_pvt["label"] == "PVT Legacy Name"
            assert len(hist["edges"]) == 1
            assert hist["edges"][0]["beta"] == pytest.approx(0.25)
            assert hist["edges"][0]["verification_tier"] == "DOC_ONLY"
            assert len(hist["macro_snapshots"]) == 1
            assert hist["macro_snapshots"][0]["beta"] == pytest.approx(0.25)

            curr = await st.get_subgraph_for_ticker("PVT", date(2026, 6, 1))
            curr_pvt = next(n for n in curr["nodes"] if n["node_id"] == "TICKER:PVT")
            assert curr_pvt["label"] == "PVT Updated Name"
            assert len(curr["edges"]) == 1
            assert curr["edges"][0]["beta"] == pytest.approx(0.80)
            assert curr["edges"][0]["verification_tier"] == "CONFIRMED_BOTH"
            assert len(curr["macro_snapshots"]) == 1
            assert curr["macro_snapshots"][0]["beta"] == pytest.approx(0.80)
    await engine.dispose()


@pytest.mark.asyncio
async def test_p0_pit_supersedes_and_revoke_patch_qdrant_valid_to() -> None:
    from sag_api.services.dedup_and_temporal_service import resolve_temporal_supersedes
    from sag_api.services.document_service import revoke_project_document

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(_make_doc("doc-1", project_id="FPT", logical_source_id="ls-1"))
        v1 = DocumentVersion(
            id="v1",
            document_id="doc-1",
            version_no=1,
            file_hash="h1",
            source_published_at=datetime(2024, 4, 1, tzinfo=UTC),
            valid_from=datetime(2024, 4, 1, tzinfo=UTC),
            valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
            status="READY",
            search_status="SEARCH_READY",
        )
        v3 = DocumentVersion(
            id="v3",
            document_id="doc-1",
            version_no=2,
            file_hash="h3",
            source_published_at=datetime(2026, 4, 1, tzinfo=UTC),
            valid_from=datetime(2026, 4, 1, tzinfo=UTC),
            valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
            status="READY",
            search_status="SEARCH_READY",
        )
        session.add_all([v1, v3])
        await session.flush()
        await resolve_temporal_supersedes(session, current_version=v3, document_id="doc-1")
        # Out-of-order mid-timeline insert v2 (2025-04-01): BOTH v1 AND v2 change validity windows!
        v2 = DocumentVersion(
            id="v2",
            document_id="doc-1",
            version_no=3,
            file_hash="h2",
            source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
            valid_from=datetime(2025, 4, 1, tzinfo=UTC),
            valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
            status="READY",
            search_status="SEARCH_READY",
        )
        session.add(v2)
        await session.flush()
        res = await resolve_temporal_supersedes(session, current_version=v2, document_id="doc-1")
        mod_ids = {m["version_id"] for m in res["modified_versions"]}
        assert "v1" in mod_ids
        assert "v2" in mod_ids
        assert v2.valid_to == datetime(2026, 4, 1, tzinfo=UTC)

        # Revoke document must patch Qdrant valid_to_ts for ALL versions
        patched_calls: list[str] = []

        async def _fake_patch(
            _client: Any,
            *,
            document_version_ids: list[str] | None = None,
            document_version_id: str | None = None,
            **_kw: Any,
        ) -> bool:
            if document_version_ids:
                patched_calls.extend(document_version_ids)
            if document_version_id:
                patched_calls.append(document_version_id)
            return True

        with patch(
            "sag_api.services.search_index_service.set_document_points_valid_to_in_qdrant",
            new=_fake_patch,
        ):
            await revoke_project_document(
                session,
                tenant_id="aiinvest-default",
                project_id="FPT",
                document_id="doc-1",
                qdrant_client=object(),
            )
        assert set(patched_calls) == {"v1", "v2", "v3"}
    await engine.dispose()


def test_p0_pit_query_routing_recency_uses_temporal_from_ts() -> None:
    from sag_api.services.query_routing_service import (
        GroupRoutingSnapshot,
        NodeProfile,
        RoutingPolicy,
        route_snapshot,
        scope_fingerprint,
    )

    scope = {
        "project_id": "FPT",
        "source_ids": ["src-1"],
        "document_version_ids": ["ver-1", "ver-2"],
        "tenant_id": "aiinvest-default",
        "partition_id": "FPT",
    }
    fp = scope_fingerprint(scope)
    open_end = int(datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
    older = NodeProfile(
        node_id="a-old",  # Lexicographically earlier node_id to prove recency tie-breaker wins!
        parent_id=None,
        is_leaf=True,
        accessible_unit_count=3,
        project_id="FPT",
        source_ids=("src-1",),
        document_version_ids=("ver-1", "ver-2"),
        tenant_id="aiinvest-default",
        partition_id="FPT",
        tree_version="tree-1",
        scope_fingerprint=fp,
        signal_scores={"sparse": 0.8},
        temporal_from_ts=int(datetime(2024, 4, 1, tzinfo=UTC).timestamp()),
        temporal_to_ts=open_end,
    )
    newer = NodeProfile(
        node_id="z-new",
        parent_id=None,
        is_leaf=True,
        accessible_unit_count=3,
        project_id="FPT",
        source_ids=("src-1",),
        document_version_ids=("ver-1", "ver-2"),
        tenant_id="aiinvest-default",
        partition_id="FPT",
        tree_version="tree-1",
        scope_fingerprint=fp,
        signal_scores={"sparse": 0.8},
        temporal_from_ts=int(datetime(2026, 4, 1, tzinfo=UTC).timestamp()),
        temporal_to_ts=open_end,
    )
    snap = GroupRoutingSnapshot(
        snapshot_id="snap-1",
        captured_at=datetime(2026, 6, 1, tzinfo=UTC),
        project_id="FPT",
        source_ids=("src-1",),
        document_version_ids=("ver-1", "ver-2"),
        tenant_id="aiinvest-default",
        partition_id="FPT",
        tree_version="tree-1",
        tree_config_version="v1",
        routing_slot="SLOT_A",
        search_epoch=1,
        manifest_status="ACTIVE",
        manifest_checksum="a" * 64,
        manifest_verified=True,
        scope_fingerprint=fp,
        profiles=(older, newer),
    )
    decision = route_snapshot(
        snap,
        policy=RoutingPolicy(beam_width=1, decisive_margin=0.0, broad_margin_threshold=0.0),
        recency_intent=True,
    )
    assert decision.selected_nodes == ("z-new",)


@pytest.mark.asyncio
async def test_p0_pit_acquire_tree_query_snapshot_historical_bypass_and_medoid_guard() -> None:
    from sag_api.db.models.routing_rag import ProjectSearchState, TreeManifest, TreeRoutingProfile
    from sag_api.services.tree_publish_service import _checksum_manifest, _scoped_profile_checksum
    from sag_api.services.tree_query_snapshot_service import acquire_tree_query_snapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    manifest_json = {
        "schema_version": 1,
        "routing_profiles": [
            {
                "partition_id": "FPT",
                "node_id": "n-leaf-1",
                "dense_medoid": [0.6, 0.8],
                "temporal_from": "2025-04-01T00:00:00+00:00",
                "temporal_to": "2026-06-01T00:00:00+00:00",
            },
            {
                "partition_id": "FPT",
                "node_id": "n-leaf-future",
                "dense_medoid": [0.1, 0.9],
                "temporal_from": "2026-06-01T00:00:00+00:00",
                "temporal_to": "9999-12-31T23:59:59+00:00",
            },
        ],
    }
    checksum = _checksum_manifest(manifest_json)
    tree_ver = f"tree-{checksum[:24]}"
    sparse_j = [["doanh", 1.0]]
    ent_j = ["fpt"]
    p_csum = _scoped_profile_checksum(
        tree_ver,
        {
            "project_id": "FPT",
            "source_id": "src-1",
            "document_version_id": "ver-active",
            "partition_id": "FPT",
            "tenant_id": "aiinvest-default",
            "node_id": "n-leaf-1",
            "parent_id": None,
            "is_leaf": True,
            "accessible_unit_count": 3,
            "sparse": sparse_j,
            "entities": ent_j,
        },
    )

    async with session_factory() as session:
        session.add(
            TreeManifest(
                tree_version=tree_ver,
                project_id="FPT",
                config_version="v1",
                node_count=2,
                leaf_count=2,
                max_leaf_size=3,
                giant_ratio=0.5,
                routing_recall_at_k=1.0,
                escape_win_rate=0.0,
                acl_blackhole_rate=0.0,
                status="ACTIVE",
                checksum=checksum,
                manifest_json=manifest_json,
            )
        )
        session.add(
            ProjectSearchState(
                project_id="FPT",
                active_routing_slot="SLOT_A",
                active_tree_version=tree_ver,
                slot_a_tree_version=tree_ver,
                active_search_epoch=1,
            )
        )
        session.add(
            TreeRoutingProfile(
                project_id="FPT",
                tree_version=tree_ver,
                source_id="src-1",
                document_version_id="ver-active",
                partition_id="FPT",
                tenant_id="aiinvest-default",
                node_id="n-leaf-1",
                parent_id=None,
                is_leaf=True,
                accessible_unit_count=3,
                sparse_json=sparse_j,
                entities_json=ent_j,
                profile_checksum=p_csum,
            )
        )
        await session.commit()

    as_of_2025 = int(datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
    # Case 1: Historical SUPERSEDED version (`ver-superseded`) not in active tree -> cleanly bypasses (None)
    snap_bypass = await acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[
            {
                "project_id": "FPT",
                "tenant_id": "aiinvest-default",
                "partition_id": "FPT",
                "source_ids": ["src-1"],
                "document_version_ids": ["ver-superseded"],
            }
        ],
        session_factory=session_factory,
        as_of_ts=as_of_2025,
    )
    assert snap_bypass is None

    # Case 2: Active version present, but manifest includes future 2026-06-01 data > as_of_2025
    # -> dense_medoid scoring MUST be suppressed even when query vector is supplied!
    import asyncio

    from sag_api.services.query_routing_service import scope_fingerprint as _scope_fp

    active_scope = {
        "project_id": "FPT",
        "tenant_id": "aiinvest-default",
        "partition_id": "FPT",
        "source_ids": ["src-1"],
        "document_version_ids": ["ver-active"],
    }
    fp_active = _scope_fp(active_scope)
    q_fut_1 = asyncio.get_running_loop().create_future()
    q_fut_1.set_result([0.6, 0.8])
    snap_guarded = await acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[active_scope],
        session_factory=session_factory,
        query_vector_tasks_by_scope={fp_active: q_fut_1},
        as_of_ts=as_of_2025,
    )
    assert snap_guarded is not None
    grp = snap_guarded.groups[0]
    assert all("dense" not in p.signal_scores for p in grp.profiles)

    # Case 3: After manifest max (2026-12-31), dense_medoid scoring IS enabled!
    as_of_2026 = int(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())
    q_fut_2 = asyncio.get_running_loop().create_future()
    q_fut_2.set_result([0.6, 0.8])
    snap_current = await acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[active_scope],
        session_factory=session_factory,
        query_vector_tasks_by_scope={fp_active: q_fut_2},
        as_of_ts=as_of_2026,
    )
    assert snap_current is not None
    assert any("dense" in p.signal_scores for p in snap_current.groups[0].profiles)
    await engine.dispose()


# ── 8. P0 HOSE Ingestion Fixes ───────────────────────────────────────────────


def test_p0_hose_bilingual_english_readability_and_ctrl_strip() -> None:
    from sag_api.research.input_inspector import (
        compute_vn_readability_score,
        normalize_ctg_font_quirks,
    )

    clean_en_agm = (
        "ANNUAL GENERAL MEETING OF SHAREHOLDERS RESOLUTION 2025. "
        "The Board of Directors approves the audited consolidated financial statements, "
        "revenue targets, net profit after tax, dividend distribution plan, and charter capital "
        "increase for PetroVietnam Transportation Corporation pursuant to the Law on Enterprises."
    )
    en_score, en_diag = compute_vn_readability_score(clean_en_agm)
    assert en_score >= 0.72, f"Clean English AGM misclassified: {en_score} ({en_diag})"

    garbled = "B\x07o c\x10o t\x03i ch\x12nh \x15\x19\x1b 2025 \ufffd\ufffd\ufffd xqzv kptm wrng"
    bad_score, _ = compute_vn_readability_score(garbled)
    assert bad_score < 0.72

    dirty_ctg = "Lợi nhuận\x0e\x0f\x10\x16\x1b\x1f năm 2025 của CTG"
    cleaned = normalize_ctg_font_quirks(dirty_ctg)
    assert not any(ord(c) in range(0x0E, 0x20) for c in cleaned)
    assert "CTG" in cleaned


@pytest.mark.asyncio
async def test_p0_hose_unit_no_usd_bleed_and_underscore_fiscal_parsers() -> None:
    from sag_api.research.batch_ingest import compute_gross_margin_delta_bps
    from sag_api.research.input_selector import (
        FinancialAnomalyContext,
        ResearchCandidateDocument,
        parse_fiscal_quarter_from_meta,
        parse_fiscal_year_from_meta,
        select_research_documents,
    )
    from sag_api.research.table_engine import (
        detect_explicit_unit_declaration,
        extract_tables_from_document,
    )

    # 1. Underscore-delimited filenames
    assert parse_fiscal_year_from_meta("bctc_q3_2026_hn.pdf") == 2026
    assert parse_fiscal_quarter_from_meta("bctc_q3_2026_hn.pdf") == 3
    assert parse_fiscal_year_from_meta("bctc_2025_hn.pdf") == 2025
    assert parse_fiscal_quarter_from_meta("bctc_2025_hn.pdf") == 0
    assert parse_fiscal_year_from_meta("agm_2025.pdf") == 2025

    # 2. Intro prose "triệu USD" must NOT bleed onto unrelated share-count/VND tables
    md = (
        "<!-- PAGE 1 -->\n"
        "Năm 2025, FPT đạt doanh thu phần mềm nước ngoài hơn 1.300 triệu USD.\n\n"
        "<!-- PAGE 10 -->\n"
        "### Cơ cấu cổ đông lớn\n\n"
        "<table>\n"
        "<tr><th>Cổ đông</th><th>Số lượng cổ phiếu</th><th>Tỷ lệ sở hữu (%)</th></tr>\n"
        "<tr><td>Cổ đông A</td><td>100.000.000</td><td>10,0%</td></tr>\n"
        "<tr><td>Cổ đông B</td><td>50.000.000</td><td>5,0%</td></tr>\n"
        "</table>\n"
    )
    assert detect_explicit_unit_declaration("doanh thu đạt 1.300 triệu USD")[2] == "unspecified"
    assert detect_explicit_unit_declaration("Đơn vị tính: triệu đồng")[1] == 1e6
    tables = extract_tables_from_document(
        pdf_bytes=None,
        project_id="FPT",
        document_version_id="ver-fpt",
        sub_doc_slot="BCTN_CORE",
        fiscal_year=2025,
        page_numbers=[1, 10],
        markdown_content=md,
        valid_from=datetime(2025, 4, 1, tzinfo=UTC),
    )
    assert tables
    assert all(t.currency != "USD" for t in tables)

    # 3. Gross margin decimal vs percent scaling (both give 300 bps)
    assert compute_gross_margin_delta_bps(0.28, 0.25) == pytest.approx(300.0)
    assert compute_gross_margin_delta_bps(28.0, 25.0) == pytest.approx(300.0)

    # 4. General Multi-Hop 4-8 recent quarters selection when require_quarter_anomaly=False
    candidates = [
        ResearchCandidateDocument(
            doc_id=f"q-{yr}-{q}",
            ticker="FPT",
            doc_type="financial_statement",
            title=f"BCTC hợp nhất quý {q}/{yr}",
            urls=[f"https://example.invalid/bctc_q{q}_{yr}_hn.pdf"],
            statement_scope="CONSOLIDATED",
            published_date=date(yr, min(12, q * 3 + 1), 25),
        )
        for yr in (2025, 2026)
        for q in (1, 2, 3, 4)
        if (yr, q) <= (2026, 2)
    ]
    sel = await select_research_documents(
        "FPT",
        candidates,
        as_of_date=date(2026, 12, 31),
        anomaly_context=FinancialAnomalyContext(),
        inspect_payloads=False,
        require_quarter_anomaly=False,
        max_latest_quarters=6,
    )
    selected_q = [s for s in sel.selected if s.canonical_doc_type == "LATEST_QUARTER"]
    assert len(selected_q) == 6


@pytest.mark.asyncio
async def test_p0_hose_input_builder_unions_bad_font_and_scan_pages() -> None:
    from sag_api.research.input_builder import build_research_sub_documents

    # Build a 15-page HYBRID (Branch B) bilingual AGM PDF:
    # P1-P10 clean digital English (>300 chars each), P11 garbled BAD_FONT, P12-P15 blank scanned pages
    doc = fitz.open()
    en_body = (
        "ANNUAL GENERAL MEETING OF SHAREHOLDERS RESOLUTION 2025.\n"
        "The Board of Directors hereby approves the audited consolidated financial statements,\n"
        "consolidated revenue target, net profit after tax, dividend distribution plan,\n"
        "and investment charter capital increase for PetroVietnam Transportation Corporation\n"
        "pursuant to the Law on Enterprises and company governance regulations."
    )
    for _ in range(10):
        pg = doc.new_page()
        pg.insert_textbox(fitz.Rect(50, 50, 540, 750), en_body)
    p11 = doc.new_page()
    p11.insert_textbox(fitz.Rect(50, 50, 540, 750), "B\x07o c\x10o \x15\x19 xqzv kptm wrng " * 20)
    for _ in range(4):
        doc.new_page()  # P12-P15: scanned (0 text)
    pdf_bytes = doc.tobytes()
    doc.close()

    captured_ocr_pages: list[int] = []

    class _FakeOcr:
        async def ocr_pdf_pages(
            self, _sliced_bytes: bytes, page_numbers: list[int], *, language: str = "vi"
        ) -> dict[int, str]:
            captured_ocr_pages.extend(page_numbers)
            return {
                p: f"Nghị quyết ĐHĐCĐ trang {p}: Kế hoạch doanh thu 2025 đạt 10.300 tỷ đồng."
                for p in page_numbers
            }

    await build_research_sub_documents(
        ticker="PVT",
        pdf_bytes=pdf_bytes,
        doc_type="agm_resolution",
        published_date=date(2025, 4, 20),
        title="Nghị quyết ĐHĐCĐ thường niên 2025",
        ocr_adapter=_FakeOcr(),
    )
    # Must OCR BOTH bad_font page 11 AND scanned pages 12..15 (never drop scanned pages!)
    assert set(captured_ocr_pages) == {11, 12, 13, 14, 15}
    assert all(p not in captured_ocr_pages for p in range(1, 11))


# ── 9. Adversarial Review Regression Tests (Step 2-3 Audit Hardening) ────────


def test_review_regressions_filters_tickers_same_page_usd_and_fiscal_hashes() -> None:
    from sag_api.research.batch_ingest import compute_gross_margin_delta_bps
    from sag_api.research.input_inspector import compute_vn_readability_score
    from sag_api.research.input_selector import (
        parse_fiscal_quarter_from_meta,
        parse_fiscal_year_from_meta,
    )
    from sag_api.research.kg_entity_linker import extract_mentioned_tickers
    from sag_api.research.table_engine import (
        detect_unit_and_currency,
        extract_tables_from_document,
    )
    from sag_api.sag.search_unit_store import (
        build_cross_ref_search_filter,
        build_universe_search_filter,
    )

    # 1. Non-ticker project_ids preserve case; 3-letter ticker project_ids normalize to uppercase
    xf = build_cross_ref_search_filter(
        tenant_id="t1",
        mentioned_tickers=["fpt"],
        exclude_project_ids=["proj-alpha"],
        allowed_project_ids=["proj-alpha", "proj-beta", "pvt"],
    )
    assert {"key": "project_id", "match": {"any": ["PVT", "proj-beta"]}} in xf["must"]
    assert xf["must_not"] == [{"key": "project_id", "match": {"any": ["proj-alpha"]}}]
    uf = build_universe_search_filter(
        tenant_id="t1",
        allowed_project_ids=["proj-alpha", "fpt"],
    )
    assert {"key": "project_id", "match": {"any": ["FPT", "proj-alpha"]}} in uf["must"]

    # 2. Real Vietnam listed tickers & explicit exchange-qualified tickers are never dropped
    mentioned = extract_mentioned_tickers(
        "Đối tác HNX: VCS, HOSE: TCT, HOSE: HRC và CTCP Chứng khoán EVS (EVS) ký với TNC.",
        own_ticker="FPT",
        known_tickers={"FPT", "VCS", "TCT", "EVS", "TNC"},
    )
    assert set(mentioned) >= {"VCS", "TCT", "HRC", "EVS", "TNC"}

    # 3. Same-page narrative "1.300 triệu USD" must NEVER bleed onto share-count or VND tables
    assert detect_unit_and_currency("Doanh thu đạt 1.300 triệu USD năm 2025")[2] == "unspecified"
    assert detect_unit_and_currency("Đơn vị tính: triệu USD") == ("USD", 1e6, "triệu USD")
    md_same_page = (
        "<!-- PAGE 10 -->\n"
        "Năm 2025, FPT đạt doanh thu phần mềm nước ngoài hơn 1.300 triệu USD.\n\n"
        "### Cơ cấu cổ đông lớn\n\n"
        "<table>\n"
        "<tr><th>Cổ đông</th><th>Số lượng cổ phiếu</th><th>Tỷ lệ sở hữu (%)</th></tr>\n"
        "<tr><td>Cổ đông A</td><td>100.000.000</td><td>10,0%</td></tr>\n"
        "<tr><td>Cổ đông B</td><td>50.000.000</td><td>5,0%</td></tr>\n"
        "</table>\n"
    )
    tbls = extract_tables_from_document(
        pdf_bytes=None,
        project_id="FPT",
        document_version_id="ver-fpt-same-page",
        sub_doc_slot="BCTN_CORE",
        fiscal_year=2025,
        page_numbers=[10],
        markdown_content=md_same_page,
        valid_from=datetime(2025, 4, 1, tzinfo=UTC),
    )
    assert tbls
    assert all(t.currency != "USD" and t.unit_raw == "unspecified" for t in tbls)

    # 4. Random R2/UUID hash segments & upload-path years must not misclassify FY/FQ
    assert parse_fiscal_quarter_from_meta("bctc_2025_q2f9a1.pdf") == 0
    assert parse_fiscal_quarter_from_meta("agm_2025_h1abc.pdf") == 0
    assert parse_fiscal_quarter_from_meta("bctc_h1_2025.pdf") == 2
    assert (
        parse_fiscal_year_from_meta(
            "BCTC hợp nhất",
            "https://cdn.example.invalid/2026/03/bctc_2025_hn.pdf",
        )
        == 2025
    )

    # 5. Low percent gross margins (1.2% vs 0.8%) stay percent-scaled (40 bps, not 4000 bps)
    assert compute_gross_margin_delta_bps(1.2, 0.8) == pytest.approx(40.0)
    assert compute_gross_margin_delta_bps(0.012, 0.008) == pytest.approx(40.0)

    # 6. Short clean English AGM voting page is not misclassified as BAD_FONT
    short_en = (
        "GENERAL MEETING OF SHAREHOLDERS RESOLUTION 2025 "
        "Board of Directors approves annual financial statements."
    )
    en_score, _ = compute_vn_readability_score(short_en)
    assert en_score >= 0.72


@pytest.mark.asyncio
async def test_review_regression_kg_out_of_order_disjoint_and_no_future_peer_leak() -> None:
    from sag_api.research.kg_store import InMemoryKGStore, SQLAlchemyKGStore

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for st in (InMemoryKGStore(), SQLAlchemyKGStore(session)):
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:FPT",
                        "node_type": "COMPANY",
                        "label": "FPT 2024",
                        "ticker": "FPT",
                        "effective_from": date(2024, 1, 1),
                    }
                ]
            )
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:FPT",
                        "node_type": "COMPANY",
                        "label": "FPT 2026",
                        "ticker": "FPT",
                        "effective_from": date(2026, 1, 1),
                    }
                ]
            )
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:FPT",
                        "node_type": "COMPANY",
                        "label": "FPT 2025",
                        "ticker": "FPT",
                        "effective_from": date(2025, 1, 1),
                    }
                ]
            )
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:FPT",
                        "node_type": "COMPANY",
                        "label": "FPT 2024-H2",
                        "ticker": "FPT",
                        "effective_from": date(2024, 7, 1),
                    }
                ]
            )
            expected_windows = [
                (date(2024, 3, 1), "FPT 2024", "2024-01-01", "2024-07-01"),
                (date(2024, 9, 1), "FPT 2024-H2", "2024-07-01", "2025-01-01"),
                (date(2025, 6, 1), "FPT 2025", "2025-01-01", "2026-01-01"),
                (date(2026, 6, 1), "FPT 2026", "2026-01-01", "9999-12-31"),
            ]
            for query_d, exp_lbl, exp_ef, exp_vt in expected_windows:
                sg = await st.get_subgraph_for_ticker("FPT", query_d)
                fpt_node = next(n for n in sg["nodes"] if n["node_id"] == "TICKER:FPT")
                assert fpt_node["label"] == exp_lbl
                assert fpt_node["effective_from"] == exp_ef
                assert fpt_node["valid_to"] == exp_vt

            # Future peer node (valid only from 2026-01-01) must NOT leak at 2025-06-01
            await st.upsert_nodes(
                [
                    {
                        "node_id": "TICKER:CMG",
                        "node_type": "COMPANY",
                        "label": "CMG",
                        "ticker": "CMG",
                        "effective_from": date(2026, 1, 1),
                    }
                ]
            )
            await st.upsert_edges(
                [
                    {
                        "src_node_id": "TICKER:FPT",
                        "dst_node_id": "TICKER:CMG",
                        "layer": "TRUE_PEER",
                        "relation_type": "TRUE_PEER_OF",
                        "weight": 0.9,
                        "effective_from": date(2025, 1, 1),
                    }
                ]
            )
            assert await st.get_true_peers("FPT", date(2025, 6, 1)) == []
            peers_2026 = await st.get_true_peers("FPT", date(2026, 6, 1))
            assert [p["ticker"] for p in peers_2026] == ["CMG"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_review_regression_manifest_cache_lru_sync_no_medoid_leak() -> None:
    import asyncio

    from sag_api.db.models.routing_rag import ProjectSearchState, TreeManifest, TreeRoutingProfile
    from sag_api.services import tree_query_snapshot_service as tqs
    from sag_api.services.query_routing_service import scope_fingerprint as _scope_fp
    from sag_api.services.tree_publish_service import _checksum_manifest, _scoped_profile_checksum

    tqs._MANIFEST_NODE_FEATURES_CACHE.clear()
    tqs._MANIFEST_MAX_VALID_FROM_CACHE.clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    manifest_json = {
        "schema_version": 1,
        "routing_profiles": [
            {
                "partition_id": "FPT",
                "node_id": "n-leaf-1",
                "dense_medoid": [0.6, 0.8],
                "temporal_from": "2025-04-01T00:00:00+00:00",
                "temporal_to": "2026-06-01T00:00:00+00:00",
            },
            {
                "partition_id": "FPT",
                "node_id": "n-leaf-future",
                "dense_medoid": [0.1, 0.9],
                "temporal_from": "2026-06-01T00:00:00+00:00",
                "temporal_to": "9999-12-31T23:59:59+00:00",
            },
        ],
    }
    checksum = _checksum_manifest(manifest_json)
    tree_ver = f"tree-{checksum[:24]}"
    sparse_j = [["doanh", 1.0]]
    ent_j = ["fpt"]
    p_csum = _scoped_profile_checksum(
        tree_ver,
        {
            "project_id": "FPT",
            "source_id": "src-1",
            "document_version_id": "ver-active",
            "partition_id": "FPT",
            "tenant_id": "t1",
            "node_id": "n-leaf-1",
            "parent_id": None,
            "is_leaf": True,
            "accessible_unit_count": 3,
            "sparse": sparse_j,
            "entities": ent_j,
        },
    )
    async with session_factory() as session:
        session.add(
            TreeManifest(
                tree_version=tree_ver,
                project_id="FPT",
                config_version="v1",
                node_count=2,
                leaf_count=2,
                max_leaf_size=3,
                giant_ratio=0.5,
                routing_recall_at_k=1.0,
                escape_win_rate=0.0,
                acl_blackhole_rate=0.0,
                status="ACTIVE",
                checksum=checksum,
                manifest_json=manifest_json,
            )
        )
        session.add(
            ProjectSearchState(
                project_id="FPT",
                active_routing_slot="SLOT_A",
                active_tree_version=tree_ver,
                slot_a_tree_version=tree_ver,
                active_search_epoch=1,
            )
        )
        session.add(
            TreeRoutingProfile(
                project_id="FPT",
                tree_version=tree_ver,
                source_id="src-1",
                document_version_id="ver-active",
                partition_id="FPT",
                tenant_id="t1",
                node_id="n-leaf-1",
                parent_id=None,
                is_leaf=True,
                accessible_unit_count=3,
                sparse_json=sparse_j,
                entities_json=ent_j,
                profile_checksum=p_csum,
            )
        )
        await session.commit()

    active_scope = {
        "project_id": "FPT",
        "tenant_id": "t1",
        "partition_id": "FPT",
        "source_ids": ["src-1"],
        "document_version_ids": ["ver-active"],
    }
    fp_active = _scope_fp(active_scope)
    as_of_2025 = int(datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp())

    f1 = asyncio.get_running_loop().create_future()
    f1.set_result([0.6, 0.8])
    s1 = await tqs.acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[active_scope],
        session_factory=session_factory,
        query_vector_tasks_by_scope={fp_active: f1},
        as_of_ts=as_of_2025,
    )
    assert s1 is not None and all("dense" not in p.signal_scores for p in s1.groups[0].profiles)

    # Churn 67 other cache entries with a midpoint access on FPT: both LRU caches MUST stay synced
    for i in range(32):
        k = (f"P{i}", "v1", f"c{i}")
        tqs._MANIFEST_NODE_FEATURES_CACHE[k] = {}
        tqs._MANIFEST_MAX_VALID_FROM_CACHE[k] = 0
    f2 = asyncio.get_running_loop().create_future()
    f2.set_result([0.6, 0.8])
    await tqs.acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[active_scope],
        session_factory=session_factory,
        query_vector_tasks_by_scope={fp_active: f2},
        as_of_ts=as_of_2025,
    )
    for i in range(32, 67):
        k = (f"P{i}", "v1", f"c{i}")
        tqs._MANIFEST_NODE_FEATURES_CACHE[k] = {}
        tqs._MANIFEST_MAX_VALID_FROM_CACHE[k] = 0
        while len(tqs._MANIFEST_NODE_FEATURES_CACHE) > tqs._MANIFEST_NODE_FEATURES_CACHE_MAX:
            tqs._MANIFEST_NODE_FEATURES_CACHE.popitem(last=False)
        while len(tqs._MANIFEST_MAX_VALID_FROM_CACHE) > tqs._MANIFEST_NODE_FEATURES_CACHE_MAX:
            tqs._MANIFEST_MAX_VALID_FROM_CACHE.popitem(last=False)

    f3 = asyncio.get_running_loop().create_future()
    f3.set_result([0.6, 0.8])
    s3 = await tqs.acquire_tree_query_snapshot(
        query="Doanh thu FPT",
        scopes=[active_scope],
        session_factory=session_factory,
        query_vector_tasks_by_scope={fp_active: f3},
        as_of_ts=as_of_2025,
    )
    assert s3 is not None
    assert all("dense" not in p.signal_scores for p in s3.groups[0].profiles)
    await engine.dispose()


@pytest.mark.asyncio
async def test_review_regression_open_universe_pit_defense_and_uncovered_kg_partner() -> None:
    from sag_api.research.chat_orchestrator import (
        ResearchChatOrchestrator,
        RetrievalResult,
        SAGRetrieverAdapter,
        collect_stream_result,
    )
    from sag_api.research.kg_store import InMemoryKGStore
    from sag_api.research.quote_verifier import compute_quote_hash
    from sag_api.sag.search_unit_store import SearchUnitHit
    from sag_api.schemas.research_chat import ResearchChatStreamRequest

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pub_dt = datetime(2025, 4, 1, tzinfo=UTC)
    fpt_partner_txt = "FPT cung cấp hạ tầng công nghệ và giải pháp vận tải thông minh cho đối tác chiến lược."

    async with session_factory() as session:
        session.add_all(
            [
                _make_doc("doc-fpt", project_id="FPT"),
                _make_doc("doc-pvt", project_id="PVT"),
            ]
        )
        session.add_all(
            [
                DocumentVersion(
                    id="ver-fpt-act",
                    document_id="doc-fpt",
                    version_no=1,
                    file_hash="hf1",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTN_CORE", "fiscal_year": 2025},
                ),
                DocumentVersion(
                    id="ver-pvt-revoked",
                    document_id="doc-pvt",
                    version_no=1,
                    file_hash="hp1",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    status="REVOKED",
                ),
            ]
        )
        session.add(
            CanonicalBlock(
                id="blk-fpt-p1",
                document_version_id="ver-fpt-act",
                ordinal=1,
                block_type="paragraph",
                section_path="BCTN_CORE > Hợp tác",
                page_from=4,
                page_to=4,
                normalized_text=fpt_partner_txt,
                content_hash=compute_quote_hash(fpt_partner_txt),
            )
        )
        await session.flush()

        # 1. Open Universe defense-in-depth ignores Qdrant hits whose document_version_id is revoked/not active
        stale_hits = [
            SearchUnitHit("pt-rev", 0.99, {"project_id": "PVT", "document_version_id": "ver-pvt-revoked"}),
            SearchUnitHit("pt-act", 0.50, {"project_id": "FPT", "document_version_id": "ver-fpt-act"}),
        ]
        fake_em = SimpleNamespace(get_search_unit_qdrant_client=AsyncMock(return_value=object()))
        orch_u = ResearchChatOrchestrator(session=session, engine_manager=fake_em)
        with patch(
            "sag_api.sag.search_unit_store.SearchUnitQdrantStore.search_batch",
            new=AsyncMock(return_value=[stale_hits]),
        ):
            selected, matched_cnt = await orch_u._route_universe_tickers_with_stats(
                query="vận tải thông minh",
                available_tickers={"FPT", "PVT"},
                principal=_principal(),
                as_of_date=date(2026, 6, 1),
                limit=6,
            )
        assert selected[0] == "FPT"
        assert matched_cnt == 1  # Only active FPT counted; revoked PVT point ignored

        # 2. Uncovered ticker XYZ linked via PIT KG edge to FPT discovers FPT as KG partner in CROSS-REF
        kg = InMemoryKGStore()
        await kg.upsert_nodes(
            [
                {
                    "node_id": "TICKER:XYZ",
                    "node_type": "COMPANY",
                    "label": "XYZ Corp",
                    "ticker": "XYZ",
                    "effective_from": date(2025, 1, 1),
                },
                {
                    "node_id": "TICKER:FPT",
                    "node_type": "COMPANY",
                    "label": "FPT Corp",
                    "ticker": "FPT",
                    "effective_from": date(2025, 1, 1),
                },
            ]
        )
        await kg.upsert_edges(
            [
                {
                    "src_node_id": "TICKER:XYZ",
                    "dst_node_id": "TICKER:FPT",
                    "layer": "VALUE_CHAIN",
                    "relation_type": "PARTNER_OF",
                    "weight": 0.9,
                    "effective_from": date(2025, 1, 1),
                }
            ]
        )

        class _NoPrimaryRetriever(SAGRetrieverAdapter):
            async def retrieve(self, **_kw: Any) -> RetrievalResult:
                return RetrievalResult()

        class _MockLLM:
            configured = True

            async def stream_complete(self, _messages: list[dict[str, str]]):
                yield "Theo tài liệu đối tác FPT, hạ tầng công nghệ phục vụ đối tác chiến lược [C1]."

        orch_kg = ResearchChatOrchestrator(
            session=session,
            kg_store=kg,
            retriever=_NoPrimaryRetriever(session),
            llm=_MockLLM(),
        )
        ans, meta, _ = await collect_stream_result(
            orch_kg.stream_chat(
                ResearchChatStreamRequest(
                    query="Hợp tác hạ tầng công nghệ của XYZ như thế nào?",
                    as_of_date=date(2026, 6, 1),
                    enable_kg=True,
                ),
                principal=_principal(),
            )
        )
        assert "INSUFFICIENT_EVIDENCE:" not in ans
        assert any(b["code"] == "NO_DIRECT_FILING" for b in meta["declared_blindspots"])
        assert any(
            c["ticker"] == "FPT" and c["evidence_origin"] == "CROSS_REF" and c["verified"]
            for c in meta["citations"]
        )
    await engine.dispose()
