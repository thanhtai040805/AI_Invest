"""Rule 08 L1 + L2 tests for push-down Qdrant disclosure search (`search_disclosures` & R1/R2/R9 fixes).

Seams exercised unmocked:
- `search_disclosures_tool` / `search_corporate_filings_tool` -> `SAGRetrieverAdapter.retrieve_disclosures`
  -> `SearchUnitQdrantStore.search_batch` over `httpx.MockTransport` (asserting exact outbound JSON filter
  clauses and dual-register BM25 VAS sparse indices) + real SQLAlchemy `Document`/`DocumentVersion`/`SearchUnit`.
- `hydrate_financial_tables_tool` -> `SQLAlchemyTableStore.get_tables` with SQL push-down `fiscal_year` and
  `fiscal_quarter` filters and zero silent unfiltered fallback.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.tools import ToolContext
from sag_agent.types import CancellationToken
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import CanonicalBlock, DocumentVersion, SearchUnit
from sag_api.db.models.source import Source
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.enums import SourceStatus
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext
from sag_api.research.tools.research_tools import (
    hydrate_financial_tables_tool,
    normalize_disclosure_slots,
    search_corporate_filings_tool,
    search_disclosures_tool,
)
from sag_api.sag.search_unit_store import (
    build_sparse_query_vector,
    build_universe_search_filter,
)

AS_OF = date(2026, 11, 26)


def _principal(allowed: frozenset[str] = frozenset({"*"})) -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-disc",
        organization_id="aiinvest-default",
        allowed_project_ids=allowed,
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-disc",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


class _FakeEmbedder:
    async def generate(self, text: str) -> list[float]:
        return [0.25, 0.5, 0.75, float(len(text) % 7 + 1) / 10.0]


class _FakeEngineManager:
    def __init__(self, qdrant_client: httpx.AsyncClient) -> None:
        self._qdrant = qdrant_client

    async def get_search_unit_qdrant_client(self) -> httpx.AsyncClient:
        return self._qdrant

    async def get_sag_embedding(self, _cfg_id: str | None, _src: Any) -> _FakeEmbedder:
        return _FakeEmbedder()


def _filter_matches_point(flt: dict[str, Any], payload: dict[str, Any]) -> bool:
    for clause in flt.get("must") or ():
        if "key" in clause and "match" in clause:
            k = clause["key"]
            m = clause["match"]
            val = payload.get(k)
            if "value" in m and val != m["value"]:
                return False
            if "any" in m:
                if isinstance(val, list):
                    if not any(v in m["any"] for v in val):
                        return False
                elif val not in m["any"]:
                    return False
        elif "key" in clause and "range" in clause:
            val = payload.get(clause["key"])
            rng = clause["range"]
            if val is None:
                return False
            if "lte" in rng and not (val <= rng["lte"]):
                return False
            if "gt" in rng and not (val > rng["gt"]):
                return False
    return True


@pytest.fixture
async def disclosure_env(tmp_path: Path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'disc.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    long_note_body = (
        "Thuyết minh V.06 — Phân loại nợ theo nhóm của MBB tại Quý 3/2026: "
        "Nợ nhóm 3 (nợ dưới tiêu chuẩn) là 4.120 tỷ đồng; nợ nhóm 4 (nợ nghi ngờ) là 3.850 tỷ đồng; "
        "nợ nhóm 5 (nợ có khả năng mất vốn) là 5.210 tỷ đồng. "
        + ("Chi tiết biến động dự phòng rủi ro tín dụng theo Thông tư 31 và Thông tư 49. " * 18)
        + "MỐC_CUỐI_ĐOẠN_1650_KÝ_TỰ: tỷ lệ CASA đạt 36,8%."
    )
    assert 1500 < len(long_note_body) < 1950

    pub_2025 = datetime(2026, 3, 25, tzinfo=UTC)
    pub_q3_26 = datetime(2026, 10, 28, tzinfo=UTC)
    far_future = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

    async with sf() as s:
        s.add(
            Source(
                id="src-mbb",
                name="HOSE-MBB",
                sag_source_config_id="cfg-mbb",
                status=SourceStatus.ACTIVE,
            )
        )
        s.add_all(
            [
                Document(
                    id="doc-mbb-bctn25",
                    source_id="src-mbb",
                    project_id="MBB",
                    tenant_id="aiinvest-default",
                    filename="MBB_BCTN_2025.pdf",
                    storage_path="/mbb25.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-mbb-q326",
                    source_id="src-mbb",
                    project_id="MBB",
                    tenant_id="aiinvest-default",
                    filename="MBB_Q3_2026.pdf",
                    storage_path="/mbbq326.pdf",
                    is_active=True,
                ),
            ]
        )
        s.add_all(
            [
                DocumentVersion(
                    id="ver-mbb-bctn25",
                    document_id="doc-mbb-bctn25",
                    version_no=1,
                    file_hash="h-mbb-25",
                    source_published_at=pub_2025,
                    valid_from=pub_2025,
                    valid_to=far_future,
                    search_status="SEARCH_READY",
                    metadata_json={"sub_doc_slot": "BCTN_CORE", "fiscal_year": 2025, "fiscal_quarter": 0},
                ),
                DocumentVersion(
                    id="ver-mbb-q326",
                    document_id="doc-mbb-q326",
                    version_no=1,
                    file_hash="h-mbb-q326",
                    source_published_at=pub_q3_26,
                    valid_from=pub_q3_26,
                    valid_to=far_future,
                    search_status="SEARCH_READY",
                    metadata_json={"sub_doc_slot": "QUARTER_NOTES", "fiscal_year": 2026, "fiscal_quarter": 3},
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                CanonicalBlock(
                    id="blk-mbb-25",
                    document_version_id="ver-mbb-bctn25",
                    ordinal=1,
                    block_type="paragraph",
                    page_from=42,
                    page_to=42,
                    section_path="MBB BCTN 2025 > Quản trị rủi ro",
                    normalized_text="Báo cáo thường niên năm 2025 của MBB: nợ xấu kiểm soát dưới 1,7%.",
                    content_hash="ch-25",
                ),
                CanonicalBlock(
                    id="blk-mbb-q326",
                    document_version_id="ver-mbb-q326",
                    ordinal=1,
                    block_type="paragraph",
                    page_from=19,
                    page_to=20,
                    section_path="MBB Q3/2026 > Thuyết minh V.06 Phân loại nợ",
                    source_anchor="table_card:tbl-mbb-q326",
                    normalized_text=long_note_body,
                    content_hash="ch-q326",
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                SearchUnit(
                    id="su-mbb-25",
                    document_version_id="ver-mbb-bctn25",
                    block_from_id="blk-mbb-25",
                    block_to_id="blk-mbb-25",
                    security_partition_id="MBB",
                    content_hash="ch-25",
                    token_count=120,
                    page_from=42,
                    page_to=42,
                    section_path="MBB BCTN 2025 > Quản trị rủi ro",
                ),
                SearchUnit(
                    id="su-mbb-q326",
                    document_version_id="ver-mbb-q326",
                    block_from_id="blk-mbb-q326",
                    block_to_id="blk-mbb-q326",
                    security_partition_id="MBB",
                    content_hash="ch-q326",
                    token_count=380,
                    page_from=19,
                    page_to=20,
                    section_path="MBB Q3/2026 > Thuyết minh V.06 Phân loại nợ",
                ),
                *[
                    SagExtractedTable(
                        table_id=f"tbl-mbb-q226-{idx}",
                        project_id="MBB",
                        document_version_id="ver-mbb-bctn25",
                        sub_doc_slot="QUARTER_NOTES",
                        fiscal_year=2026,
                        fiscal_quarter=2,
                        table_title=f"Bảng kết quả kinh doanh Quý 2/2026 #{idx}",
                        section_path="KQKD Q2",
                        page_from=idx,
                        page_to=idx,
                        currency="VND",
                        unit_raw="triệu đồng",
                        unit_multiplier=1e6,
                        headers_json=["Chỉ tiêu", "Q2/2026"],
                        rows_json=[[{"raw_text": "Thu nhập lãi thuần", "numeric_value": None},
                                    {"raw_text": "10.100.000", "numeric_value": 10_100_000.0}]],
                        verification_status="VERIFIED",
                        table_hash=f"th-q226-{idx}",
                        valid_from=pub_2025,
                    )
                    for idx in range(1, 6)
                ],
                SagExtractedTable(
                    table_id="tbl-mbb-q326",
                    project_id="MBB",
                    document_version_id="ver-mbb-q326",
                    sub_doc_slot="QUARTER_NOTES",
                    fiscal_year=2026,
                    fiscal_quarter=3,
                    table_title="Bảng kết quả kinh doanh Quý 3/2026",
                    section_path="KQKD Q3",
                    page_from=6,
                    page_to=6,
                    currency="VND",
                    unit_raw="triệu đồng",
                    unit_multiplier=1e6,
                    headers_json=["Chỉ tiêu", "Q3/2026"],
                    rows_json=[[{"raw_text": "Thu nhập lãi thuần", "numeric_value": None},
                                {"raw_text": "11.450.000", "numeric_value": 11_450_000.0}]],
                    verification_status="VERIFIED",
                    table_hash="th-q326",
                    valid_from=pub_q3_26,
                ),
            ]
        )
        await s.commit()

    qdrant_points = [
        {
            "id": "pt-mbb-25",
            "score": 0.99,  # Higher raw score than Q3/2026 so unfiltered top-K would pick FY2025!
            "payload": {
                "search_unit_id": "su-mbb-25",
                "project_id": "MBB",
                "tenant_id": "aiinvest-default",
                "security_partition_id": "MBB",
                "document_id": "doc-mbb-bctn25",
                "document_version_id": "ver-mbb-bctn25",
                "sub_doc_slot": "BCTN_CORE",
                "doc_type": "BCTN",
                "fiscal_year": 2025,
                "fiscal_quarter": 0,
                "page_from": 42,
                "page_to": 42,
                "section_path": "MBB BCTN 2025 > Quản trị rủi ro",
                "content": " Báo cáo thường niên năm 2025 của MBB: nợ xấu kiểm soát dưới 1,7%.",
                "valid_from_ts": int(pub_2025.timestamp()),
                "valid_to_ts": int(far_future.timestamp()),
            },
        },
        {
            "id": "pt-mbb-q326",
            "score": 0.82,
            "payload": {
                "search_unit_id": "su-mbb-q326",
                "project_id": "MBB",
                "tenant_id": "aiinvest-default",
                "security_partition_id": "MBB",
                "document_id": "doc-mbb-q326",
                "document_version_id": "ver-mbb-q326",
                "sub_doc_slot": "QUARTER_NOTES",
                "doc_type": "LATEST_QUARTER",
                "fiscal_year": 2026,
                "fiscal_quarter": 3,
                "page_from": 19,
                "page_to": 20,
                "section_path": "MBB Q3/2026 > Thuyết minh V.06 Phân loại nợ",
                "source_anchor": "table_card:tbl-mbb-q326",
                "content": long_note_body,
                "valid_from_ts": int(pub_q3_26.timestamp()),
                "valid_to_ts": int(far_future.timestamp()),
            },
        },
    ]
    outbound_batches: list[list[dict[str, Any]]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/points/query/batch")
        body = json.loads(request.content.decode("utf-8"))
        searches = body["searches"]
        outbound_batches.append(searches)
        res = []
        for sp in searches:
            matched = [p for p in qdrant_points if _filter_matches_point(sp["filter"], p["payload"])]
            res.append({"points": matched[: sp["limit"]]})
        return httpx.Response(200, json={"result": res})

    qdrant_client = httpx.AsyncClient(base_url="http://qdrant.local", transport=httpx.MockTransport(_handler))
    yield sf, _FakeEngineManager(qdrant_client), outbound_batches
    await qdrant_client.aclose()
    await eng.dispose()


def _make_tctx(
    sf: Any, em: Any, *, available: frozenset[str] = frozenset({"MBB"})
) -> tuple[ToolContext, ResearchEvidenceLedger]:
    evidence = ResearchEvidenceLedger()
    rctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="MOAT",
        session_factory=sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=evidence,
        available_tickers=available,
        engine_manager=em,
    )

    async def _noop(_p: Any) -> None:
        return None

    return (
        ToolContext(
            run_id="run-disc",
            tool_call_id="tc-disc",
            data=rctx,
            cancellation=CancellationToken(),
            _on_progress=_noop,
        ),
        evidence,
    )


def test_l1_universe_filter_pushes_down_slots_years_and_quarters() -> None:
    flt = build_universe_search_filter(
        tenant_id="aiinvest-default",
        allowed_project_ids=["MBB"],
        allowed_document_version_ids=["ver-1"],
        as_of_ts=1795000000,
        sub_doc_slots=["QUARTER_NOTES"],
        fiscal_years=[2026],
        fiscal_quarters=[3],
    )
    by_key = {c["key"]: c["match"] for c in flt["must"] if "key" in c and "match" in c}
    assert by_key["sub_doc_slot"] == {"any": ["QUARTER_NOTES"]}
    assert by_key["fiscal_year"] == {"any": [2026]}
    assert by_key["fiscal_quarter"] == {"any": [3]}

    assert normalize_disclosure_slots(["BCTN", "QUARTER_NOTES"]) == ["BCTN_CORE", "BCTN_PR", "QUARTER_NOTES"]
    with pytest.raises(ValueError):
        normalize_disclosure_slots(["INVALID_SLOT_XYZ"])


async def test_l2_search_disclosures_pushes_filters_and_preserves_vas_terms_and_2000_chars(disclosure_env) -> None:
    sf, em, outbound_batches = disclosure_env
    tctx, evidence = _make_tctx(sf, em)

    res = await search_disclosures_tool.execute(
        {
            "tickers": ["MBB"],
            "sub_doc_slots": ["QUARTER_NOTES"],
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "vas_terms": ["nợ nhóm 3", "nợ có khả năng mất vốn", "V.06"],
            "question": "Chất lượng tài sản nợ xấu nhóm 3-5 và CASA của MBB Quý 3/2026",
            "top_k": 5,
        },
        tctx,
    )
    assert len(outbound_batches) == 1
    searches = outbound_batches[0]
    using_names = [s["using"] for s in searches]
    assert "content_vector" in using_names and "bm25_sparse" in using_names

    # R1 regression guard: VAS terms MUST be encoded into bm25_sparse indices
    expected_vas_indices = set(build_sparse_query_vector("nợ nhóm 3 nợ có khả năng mất vốn V.06")["indices"])
    sparse_specs = [s for s in searches if s["using"] == "bm25_sparse"]
    actual_sparse_indices = {i for s in sparse_specs for i in s["query"]["indices"]}
    assert expected_vas_indices <= actual_sparse_indices

    # Wire-level push-down filter guard: outbound Qdrant HTTP payload must carry must-clauses
    wire_must = {
        c["key"]: c["match"]
        for c in searches[0]["filter"]["must"]
        if "key" in c and "match" in c
    }
    assert wire_must["sub_doc_slot"] == {"any": ["QUARTER_NOTES"]}
    assert wire_must["fiscal_year"] == {"any": [2026]}
    assert wire_must["fiscal_quarter"] == {"any": [3]}

    # R2 push-down guard: FY2025 had higher score (0.99 > 0.82), yet Qdrant must-filter excluded it
    assert res.details["returned_count"] == 1
    assert "tbl-mbb-q326" in res.details["table_hints"]
    assert len(evidence.sections) == 1
    assert evidence.sections[0].fiscal_year == 2026
    assert evidence.sections[0].fiscal_quarter == 3
    assert "Báo cáo thường niên năm 2025" not in res.content

    # R9 chunk truncation guard: 1650-char tail marker must survive (old 1100-char cap lost it!)
    assert "MỐC_CUỐI_ĐOẠN_1650_KÝ_TỰ" in res.content


async def test_l2_search_corporate_filings_never_drops_vas_after_3_dense_queries(disclosure_env) -> None:
    sf, em, outbound_batches = disclosure_env
    tctx, _evidence = _make_tctx(sf, em)
    outbound_batches.clear()

    await search_corporate_filings_tool.execute(
        {
            "tickers": ["MBB"],
            "dense_semantic_queries": ["Câu hỏi 1 MBB", "Câu hỏi 2 MBB", "Câu hỏi 3 MBB"],
            "bm25_vas_queries": ["dự phòng rủi ro tín dụng cụ thể"],
            "sub_doc_slots": ["QUARTER_NOTES"],
            "fiscal_years": [2026],
        },
        tctx,
    )
    vas_idx = set(build_sparse_query_vector("dự phòng rủi ro tín dụng cụ thể")["indices"])
    sparse_indices = {i for s in outbound_batches[0] if s["using"] == "bm25_sparse" for i in s["query"]["indices"]}
    assert vas_idx <= sparse_indices, "bm25_vas_queries must never be dropped by dense_semantic_queries[:2]"


async def test_l2_missing_quarter_returns_not_found_without_silent_fallback(disclosure_env) -> None:
    sf, em, _batches = disclosure_env
    tctx, evidence = _make_tctx(sf, em)

    # MBB has FY2025 BCTN_CORE and Q3/2026 QUARTER_NOTES, NOT Q1/2026!
    res = await search_disclosures_tool.execute(
        {
            "tickers": ["MBB"],
            "sub_doc_slots": ["QUARTER_NOTES"],
            "fiscal_year": 2026,
            "fiscal_quarter": 1,
            "vas_terms": ["nợ nhóm 3"],
            "question": "Nợ xấu MBB Quý 1/2026",
        },
        tctx,
    )
    assert res.details["status"] == "NOT_FOUND"
    assert res.details["returned_count"] == 0
    assert evidence.sections == [], "must never silently register wrong-quarter/wrong-year chunks"
    assert "NOT_FOUND" in res.content
    assert "Q3/2026" in res.content and "FY2025" in res.content


async def test_l2_hydrate_financial_tables_pushes_down_fiscal_quarter_without_silent_fallback(
    disclosure_env,
) -> None:
    sf, em, _batches = disclosure_env
    tctx, evidence = _make_tctx(sf, em)

    ok = await hydrate_financial_tables_tool.execute(
        {"tickers": ["MBB"], "statement_kind": "IS", "fiscal_year": 2026, "fiscal_quarter": 3},
        tctx,
    )
    assert ok.details["table_ids"] == ["tbl-mbb-q326"]
    assert "11450000.0" in ok.content and "10100000.0" not in ok.content

    tctx2, evidence2 = _make_tctx(sf, em)
    miss = await hydrate_financial_tables_tool.execute(
        {"tickers": ["MBB"], "statement_kind": "IS", "fiscal_year": 2026, "fiscal_quarter": 4},
        tctx2,
    )
    assert miss.details.get("returned_count", 0) == 0
    assert evidence2.hydrated_tables == {}


@pytest.mark.asyncio
async def test_retrieve_disclosures_stitches_section_siblings_and_bridges_hipporag_unit_edges(
    tmp_path: Path,
) -> None:
    import hashlib

    from sag_api.core.config import Settings
    from sag_api.db.models import SourceProjectMapping
    from sag_api.jobs.knowledge import KnowledgeWorker
    from sag_api.research.quote_verifier import verify_citations

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stitch_hippo.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    pub_dt = datetime(2026, 10, 25, tzinfo=UTC)
    vf_ts = int(pub_dt.timestamp())
    vt_ts = int(datetime(2099, 1, 1, tzinfo=UTC).timestamp())
    p8_a = (
        "Giải trình biến động KQKD Q3/2026: Doanh thu hợp nhất giảm do Công ty Cổ phần Viễn thông FPT (FTEL) "
        "không còn là công ty con theo Công văn số 1906/TTg-ĐMDN; nếu so sánh tương đương doanh thu tăng 12,1%."
    )
    p8_b = (
        "Khối Công nghệ tiếp tục giữ vai trò chủ lực với doanh thu tăng trưởng 14,4% và Lãi từ công ty liên doanh, "
        "liên kết tăng 46% so với cùng kỳ."
    )
    p14 = (
        "Thuyết minh 1 — Cấu trúc Tập đoàn: Thực hiện Công văn số 1906/TTg-ĐMDN, quyền biểu quyết tại "
        "Công ty Cổ phần Viễn thông FPT (FTEL) chuyển sang hạch toán công ty liên kết từ tháng 7/2025."
    )

    async with sf() as s:
        s.add(Source(id="src-fpt-sh", name="HOSE", sag_source_config_id="cfg-fpt-sh", status=SourceStatus.ACTIVE))
        s.add(
            SourceProjectMapping(
                source_id="src-fpt-sh",
                organization_id="aiinvest-default",
                project_id="FPT",
                state="CONFIRMED",
                confirmed_at=pub_dt,
                confirmed_by="tester",
                approval_ref="ref-sh",
            )
        )
        s.add(
            Document(
                id="doc-fpt-q3",
                source_id="src-fpt-sh",
                project_id="FPT",
                tenant_id="aiinvest-default",
                filename="FPT_Q3_2026.pdf",
                storage_path="/FPT_Q3_2026.pdf",
                is_active=True,
            )
        )
        s.add(
            DocumentVersion(
                id="ver-fpt-q3",
                document_id="doc-fpt-q3",
                version_no=1,
                file_hash="h-fpt-q3",
                source_published_at=pub_dt,
                valid_from=pub_dt,
                search_status="SEARCH_READY",
                search_ready_at=pub_dt,
                knowledge_status="NOT_STARTED",
                metadata_json={
                    "sub_doc_slot": "QUARTER_NOTES",
                    "fiscal_year": 2026,
                    "fiscal_quarter": 3,
                    "security_partition_id": "FPT",
                },
            )
        )
        s.add_all(
            [
                CanonicalBlock(
                    id="blk-p8-a",
                    document_version_id="ver-fpt-q3",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="QUARTER_NOTES > GIẢI TRÌNH KQKD",
                    page_from=8,
                    page_to=8,
                    source_anchor="p8a",
                    normalized_text=p8_a,
                    content_hash=hashlib.sha256(p8_a.encode()).hexdigest(),
                ),
                CanonicalBlock(
                    id="blk-p8-b",
                    document_version_id="ver-fpt-q3",
                    ordinal=2,
                    block_type="paragraph",
                    section_path="QUARTER_NOTES > GIẢI TRÌNH KQKD",
                    page_from=9,
                    page_to=9,
                    source_anchor="p8b",
                    normalized_text=p8_b,
                    content_hash=hashlib.sha256(p8_b.encode()).hexdigest(),
                ),
                CanonicalBlock(
                    id="blk-p14",
                    document_version_id="ver-fpt-q3",
                    ordinal=3,
                    block_type="paragraph",
                    section_path="QUARTER_NOTES > Thuyết minh 1",
                    page_from=14,
                    page_to=14,
                    source_anchor="p14",
                    normalized_text=p14,
                    content_hash=hashlib.sha256(p14.encode()).hexdigest(),
                ),
                SearchUnit(
                    id="su-p8-a",
                    document_version_id="ver-fpt-q3",
                    block_from_id="blk-p8-a",
                    block_to_id="blk-p8-a",
                    security_partition_id="FPT",
                    content_hash=hashlib.sha256(p8_a.encode()).hexdigest(),
                    token_count=60,
                    page_from=8,
                    page_to=8,
                    section_path="QUARTER_NOTES > GIẢI TRÌNH KQKD",
                ),
                SearchUnit(
                    id="su-p8-b",
                    document_version_id="ver-fpt-q3",
                    block_from_id="blk-p8-b",
                    block_to_id="blk-p8-b",
                    security_partition_id="FPT",
                    content_hash=hashlib.sha256(p8_b.encode()).hexdigest(),
                    token_count=45,
                    page_from=9,
                    page_to=9,
                    section_path="QUARTER_NOTES > GIẢI TRÌNH KQKD",
                ),
                SearchUnit(
                    id="su-p14",
                    document_version_id="ver-fpt-q3",
                    block_from_id="blk-p14",
                    block_to_id="blk-p14",
                    security_partition_id="FPT",
                    content_hash=hashlib.sha256(p14.encode()).hexdigest(),
                    token_count=55,
                    page_from=14,
                    page_to=14,
                    section_path="QUARTER_NOTES > Thuyết minh 1",
                ),
            ]
        )
        await s.commit()

    worker = KnowledgeWorker(sf, Settings(_env_file=None))  # type: ignore[call-arg]
    await worker.discover()
    assert await worker.run_one() is True

    # Qdrant returns ONLY su-p8-a (Page 8); sibling stitching MUST attach Page 9 (p8_b),
    # and HippoRAG-2 MUST bridge over KnowledgeUnitEdge / shared entity (FTEL, 1906/TTg-ĐMDN) to Page 14 (p14)!
    points = [
        {
            "id": "pt-p8-a",
            "score": 0.91,
            "payload": {
                "tenant_id": "aiinvest-default",
                "project_id": "FPT",
                "document_version_id": "ver-fpt-q3",
                "search_unit_id": "su-p8-a",
                "sub_doc_slot": "QUARTER_NOTES",
                "fiscal_year": 2026,
                "fiscal_quarter": 3,
                "valid_from_ts": vf_ts,
                "valid_to_ts": vt_ts,
                "page_from": 8,
                "page_to": 8,
                "block_from_id": "blk-p8-a",
                "block_to_id": "blk-p8-a",
                "section_path": "QUARTER_NOTES > GIẢI TRÌNH KQKD",
                "content": p8_a,
            },
        }
    ]

    def _qdrant_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        searches = body.get("searches") or []
        results = []
        for s_item in searches:
            flt = s_item.get("filter") or {}
            hits = [pt for pt in points if _filter_matches_point(flt, pt["payload"])]
            results.append({"points": hits})
        return httpx.Response(200, json={"result": results})

    q_client = httpx.AsyncClient(transport=httpx.MockTransport(_qdrant_handler), base_url="http://qdrant.local")
    em = _FakeEngineManager(q_client)
    tctx, evidence = _make_tctx(sf, em, available=frozenset({"FPT"}))

    out = await search_disclosures_tool.execute(
        {
            "tickers": ["FPT"],
            "dense_queries": ["giải trình biến động doanh thu lợi nhuận Q3/2026"],
            "vas_terms": ["giải trình doanh thu FTEL"],
            "sub_doc_slots": ["QUARTER_NOTES"],
            "fiscal_years": [2026],
            "fiscal_quarters": [3],
            "top_k": 5,
        },
        tctx,
    )
    await q_client.aclose()

    assert out.details["returned_count"] >= 2
    assert evidence.hipporag_hops >= 1
    combined_content = "\n".join(sec.content for sec in evidence.sections)
    assert "12,1%" in combined_content
    assert "14,4%" in combined_content  # Stitched sibling su-p8-b!
    assert "Thuyết minh 1" in combined_content  # HippoRAG-2 bridged su-p14!

    # Stage 7 quote_hash verification MUST pass 100% on both stitched and bridged sections!
    from sag_api.research.quote_verifier import compute_quote_hash

    cand_cits = [
        {
            "citation_id": f"C{idx}",
            "ticker": sec.ticker,
            "doc_id": sec.doc_id,
            "document_version_id": sec.document_version_id,
            "sub_doc_slot": sec.sub_doc_slot,
            "page": sec.page_from,
            "page_to": sec.page_to,
            "published_date": sec.published_date.isoformat(),
            "quote": sec.content,
            "quote_hash": compute_quote_hash(sec.content),
            "evidence_origin": sec.evidence_origin,
        }
        for idx, sec in enumerate(evidence.sections, 1)
    ]
    v_res = verify_citations(
        cand_cits,
        evidence.canonical_blocks,
        AS_OF,
        answer_text="FPT ngừng hợp nhất FTEL [C1] theo Thuyết minh 1 [C2].",
        valid_citation_ids={f"C{i}" for i in range(1, len(evidence.sections) + 1)},
        sections_by_cid={f"C{i}": s for i, s in enumerate(evidence.sections, 1)},
    )
    assert v_res.verified_rate == 1.0
    assert len(v_res.rejected_citations) == 0
    await eng.dispose()

