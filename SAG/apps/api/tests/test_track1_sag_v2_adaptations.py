"""Comprehensive Track 1 tests for SAG v2 Stock Research adaptations (P1..P5, A1..A7, 0013, DeepSeek-V4)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.core.errors import ApiError
from sag_api.core.litellm_policy import apply_litellm_completion_policy
from sag_api.core.token_usage import DEFAULT_MODEL_PRICING_PER_1M_USD, compute_bucket_cost_usd
from sag_api.db.base import Base
from sag_api.db.models import (
    Document,
    DocumentVersion,
    KgEdge,
    KgNode,
    KnowledgeUnit,
    MacroSensitivitySnapshot,
    SagExtractedTable,
    SearchUnit,
    Source,
)
from sag_api.parsing.canonical import extract_canonical_blocks
from sag_api.parsing.mineru_official import OfficialMinerUClient
from sag_api.parsing.service import _allows_markitdown_fallback, prepare_document
from sag_api.sag import RetrievedSection
from sag_api.sag.search_unit_store import build_search_filter, build_sparse_query_vector
from sag_api.schemas.routing_rag import DocumentObjectIngestRequest
from sag_api.schemas.search import GlobalSearchRequest, SearchRequest
from sag_api.services.document_service import (
    _build_version_metadata,
    _resolve_pit_version_window,
)
from sag_api.services.knowledge_quality_service import (
    EvidenceSignal,
    QualityTopicInput,
    UnitSignal,
    extract_batch_quality_metrics,
    resolve_unit_half_life_days,
    score_topic,
)
from sag_api.services.knowledge_service import (
    VN_FINANCIAL_ALIASES,
    KnowledgeConfig,
    build_financial_knowledge_config,
    graph_payload,
)
from sag_api.services.query_analysis import analyze_query
from sag_api.services.query_routing_service import (
    GroupRoutingSnapshot,
    NodeProfile,
    RoutingPolicy,
    route_snapshot,
    scope_fingerprint,
)
from sag_api.services.retrieval_service import (
    select_diverse_sections,
    stream_synthesize_search_answer,
    synthesize_search_answer,
)
from sag_api.services.routing_tree_service import (
    KnowledgeEdgeInput,
    KnowledgeUnitInput,
    TreeBuildConfig,
    build_routing_snapshot,
    filter_nodes_pit,
    research_tree_build_config,
)
from sag_api.services.search_index_service import (
    PAYLOAD_INDEX_FIELDS,
    build_qdrant_payload,
    compute_sparse_bm25_vector,
    normalize_numeric_separators,
)
from sag_api.services.search_unit_retrieval_service import (
    _CompactKU,
    _expand_multi_hop_sections,
    _extract_artifact_codes,
    _extract_vn_financial_bridge_terms,
    _is_artifact_code_norm,
    _query_group,
    _ReadyVersion,
    _ScopeKnowledgeIndex,
    _SearchGroup,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 8, 0, tzinfo=UTC)


def test_migration_0013_sql_contract_and_idempotency() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "migrations" / "0013_stock_research_tables_and_kg.sql"
    sql = sql_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS sag_extracted_tables" in sql
    assert re.search(
        r"document_version_id\s+VARCHAR\(64\)\s+NOT NULL REFERENCES document_versions\(id\) ON DELETE CASCADE",
        sql,
    )
    assert "CREATE TABLE IF NOT EXISTS kg_nodes" in sql
    assert "CREATE TABLE IF NOT EXISTS kg_edges" in sql
    assert "CREATE TABLE IF NOT EXISTS macro_sensitivity_snapshots" in sql
    assert "idx_sag_extracted_tables_pit" in sql
    assert "idx_kg_edges_src_pit" in sql


@pytest.mark.asyncio
async def test_stock_research_orm_models_sqlite_roundtrip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        src = Source(
            id=str(uuid.uuid4()),
            name="HPG BCTC 2025",
            sag_source_config_id=f"cfg-{uuid.uuid4()}",
        )
        doc = Document(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            project_id="HPG",
            source_id=src.id,
            filename="hpg_2025.pdf",
            storage_path="/tmp/hpg_2025.pdf",
        )
        ver = DocumentVersion(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            version_no=1,
            file_hash="abc123",
            valid_from=_utc(2026, 3, 25),
        )
        session.add_all([src, doc, ver])
        await session.flush()

        tbl = SagExtractedTable(
            project_id="HPG",
            document_version_id=ver.id,
            sub_doc_slot="S03",
            fiscal_year=2025,
            fiscal_quarter=0,
            table_title="B02-DN/HN Kết quả hoạt động kinh doanh",
            headers_json=["Chỉ tiêu", "2025", "2024"],
            rows_json=[["Doanh thu thuần", 140000, 120000]],
            verification_status="VERIFIED",
            table_hash="hash-b02-2025",
            valid_from=_utc(2026, 3, 25),
        )
        node_hpg = KgNode(
            node_id="TICKER:HPG",
            node_type="TICKER",
            label="Hòa Phát",
            ticker="HPG",
            metadata_json={"exchange": "HOSE"},
            effective_from=date(2026, 3, 25),
        )
        node_steel = KgNode(
            node_id="COMMODITY:HRC_STEEL",
            node_type="COMMODITY",
            label="Thép cuộn cán nóng HRC",
            effective_from=date(2026, 1, 1),
        )
        edge = KgEdge(
            src_node_id="COMMODITY:HRC_STEEL",
            dst_node_id="TICKER:HPG",
            relation_type="COSTS_SENSITIVE_TO",
            layer="QUANT",
            direction="POSITIVE",
            weight=0.82,
            beta=0.45,
            p_value=0.01,
            fdr_q_value=0.02,
            tercile_stable=True,
            verification_tier="CONFIRMED_BOTH",
            evidence_json={"table_id": tbl.table_id},
            effective_from=date(2026, 3, 25),
        )
        snap = MacroSensitivitySnapshot(
            target_id="HPG",
            target_level="TICKER",
            macro_factor="HRC_STEEL",
            as_of_date=date(2026, 3, 25),
            best_lag_q=1,
            beta=0.45,
            hac_t_stat=3.1,
            p_value=0.01,
            fdr_q_value=0.02,
            tercile_stable=True,
            doc_stance="SUPPORTIVE",
            verification_tier="CONFIRMED_BOTH",
            effective_from=date(2026, 3, 25),
            sample_n=24,
        )
        session.add_all([tbl, node_hpg, node_steel, edge, snap])
        await session.commit()

        loaded_tbl = await session.scalar(
            select(SagExtractedTable).where(SagExtractedTable.document_version_id == ver.id)
        )
        loaded_snap = await session.scalar(
            select(MacroSensitivitySnapshot).where(MacroSensitivitySnapshot.target_id == "HPG")
        )
        assert loaded_tbl is not None and loaded_tbl.sub_doc_slot == "S03"
        assert loaded_snap is not None and loaded_snap.best_lag_q == 1

    await engine.dispose()


def test_config_and_deepseek_v4_flash_policy_and_pricing() -> None:
    cfg = Settings(
        sag_language="vi",
        core_database_url="postgresql+asyncpg://user:pass@localhost:5432/aiinvest",
        research_search_top_k=10,
    )
    assert cfg.sag_language == "vi"
    assert cfg.core_database_url == "postgresql+asyncpg://user:pass@localhost:5432/aiinvest"
    assert cfg.research_search_top_k == 10

    blank_cfg = Settings(core_database_url="   ")
    assert blank_cfg.core_database_url is None

    req = apply_litellm_completion_policy(
        cfg,
        {"model": "deepseek-ai/DeepSeek-V4-Flash-0731", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert req["extra_body"]["thinking"] == {"type": "disabled"}

    assert "deepseek-ai/deepseek-v4-flash-0731" in DEFAULT_MODEL_PRICING_PER_1M_USD
    assert "qwen/qwen3-embedding-0.6b" in DEFAULT_MODEL_PRICING_PER_1M_USD
    cost = compute_bucket_cost_usd(
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        {"llm_input_tokens": 1_000_000, "llm_output_tokens": 1_000_000},
    )
    assert cost > 0


@pytest.mark.asyncio
async def test_p1_fail_closed_pdf_mineru_vi_and_canonical_page_markers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _allows_markitdown_fallback("notes.docx") is True
    assert _allows_markitdown_fallback("bctc.pdf") is False
    assert _allows_markitdown_fallback("scan.png") is False

    class FailingMinerU:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def parse(self, _path: str, **_kwargs) -> str:
            raise ApiError("MinerU OCR timeout", code="mineru_failed")

    monkeypatch.setattr("sag_api.parsing.service.MinerUClient", FailingMinerU)

    pdf_path = tmp_path / "bctc_2025.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    parse_settings = Settings(
        document_parser="mineru",
        mineru_base_url="https://mineru.net/api/v4",
        mineru_official_api_token="tok-test",
        sag_language="vi",
    )
    states: list[dict] = []

    async def capture_state(st: dict) -> None:
        states.append(st)

    with pytest.raises(ApiError, match="MinerU OCR timeout"):
        await prepare_document(str(pdf_path), parse_settings, on_state=capture_state)
    assert states and states[-1]["status"] == "failed"

    client = OfficialMinerUClient(parse_settings)
    # Vietnamese (`vi`) maps to MinerU's canonical `latin` script pack
    assert client.signature.endswith("-latin")
    captured_payloads: list[dict] = []

    class FakeResp:
        is_success = True
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url: str, headers=None, json=None):
            captured_payloads.append(dict(json or {}))
            return FakeResp({"code": 0, "data": {"task_id": "task-123"}})

    monkeypatch.setattr("sag_api.parsing.mineru_official.httpx.AsyncClient", FakeAsyncClient)
    task_id = await client._create_url_task("https://example.com/bctc.pdf")
    assert task_id == "task-123"
    assert captured_payloads[0]["language"] == "latin"
    assert captured_payloads[0]["enable_table"] is True

    md = "## B02-DN\n<!-- PAGE 4 --> Doanh thu thuần: 16.012.813\n\n<!-- PAGE 5 --> Lợi nhuận sau thuế: 2.100.000"
    blocks = extract_canonical_blocks(md, version_id="ver-1")
    assert any(b.page_from == 4 and "Doanh thu" in b.normalized_text for b in blocks)
    assert any(b.page_from == 5 and "Lợi nhuận" in b.normalized_text for b in blocks)


def test_p2_vn_dot_thousands_bm25_preserves_decimals_and_codes() -> None:
    normalized = normalize_numeric_separators("Doanh thu đạt 16.012.813 triệu, biên 15.29%, mã 12.34.567.")
    assert "16012813" in normalized
    assert "15.29" in normalized
    assert "12.34.567" in normalized

    doc_vec = compute_sparse_bm25_vector("Doanh thu thuần 16.012.813 triệu VND")
    query_vec = build_sparse_query_vector("16.012.813")
    raw_query_vec = build_sparse_query_vector("16012813")
    assert query_vec == raw_query_vec
    assert set(query_vec["indices"]) & set(doc_vec["indices"])


@pytest.mark.asyncio
async def test_p3_pit_valid_from_and_out_of_order_historical_backfill() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    t_2025 = _utc(2026, 3, 20)
    t_2024 = _utc(2025, 3, 20)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        src = Source(
            id=str(uuid.uuid4()),
            name="HPG BCTC",
            sag_source_config_id=f"cfg-{uuid.uuid4()}",
        )
        doc = Document(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            project_id="HPG",
            source_id=src.id,
            filename="hpg_bctc.pdf",
            storage_path="/tmp/hpg_bctc.pdf",
        )
        ver_2025 = DocumentVersion(
            id="v-2025",
            document_id=doc.id,
            version_no=1,
            file_hash="hash-2025",
            valid_from=t_2025,
            valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
        )
        session.add_all([src, doc, ver_2025])
        await session.flush()

        # Backfilling an older 2024 filing AFTER 2025 was already ingested:
        valid_to, supersedes_id = await _resolve_pit_version_window(session, doc.id, t_2024)
        assert valid_to == t_2025
        assert supersedes_id is None
        assert ver_2025.valid_to.year == 9999

    await engine.dispose()

    meta = _build_version_metadata(
        {"existing": 1},
        fiscal_year=2024,
        fiscal_quarter=0,
        doc_type="audited_annual",
        sub_doc_slot="S03",
        sector_group="STEEL",
        table_id="B02-DN/HN",
    )
    assert meta["fiscal_year"] == 2024
    assert meta["sub_doc_slot"] == "S03"


def test_p4_p5_extended_metadata_qdrant_filter_and_top_k_schemas() -> None:
    index_field_names = {name for name, _ in PAYLOAD_INDEX_FIELDS}
    for field_name in ("fiscal_year", "fiscal_quarter", "doc_type", "sub_doc_slot", "sector_group", "table_id"):
        assert field_name in index_field_names

    ver = DocumentVersion(
        id="ver1",
        document_id="doc1",
        version_no=1,
        file_hash="h1",
        valid_from=datetime.fromtimestamp(1774000000, tz=UTC),
        valid_to=datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
    )
    unit = SearchUnit(
        id="su1",
        document_version_id="ver1",
        security_partition_id="part1",
        block_from_id="b1",
        block_to_id="b1",
        section_path="B02",
        token_count=5,
        page_from=4,
        page_to=4,
        content_hash="ch1",
    )
    payload = build_qdrant_payload(
        unit,
        version=ver,
        project_id="HPG",
        tenant_id="t1",
        source_id="src-1",
        document_id="doc1",
        version_no=1,
        source_anchor="p4",
        fiscal_year=2025,
        fiscal_quarter=0,
        doc_type="audited_annual",
        sub_doc_slot="S03",
        sector_group="STEEL",
        table_id="B02-DN",
    )
    assert payload["fiscal_year"] == 2025
    assert payload["table_id"] == "B02-DN"

    q_filter = build_search_filter(
        tenant_id="t1",
        project_id="HPG",
        partition_id="part1",
        document_version_ids=["ver1"],
        as_of_ts=1774500000,
        sub_doc_slots=["S03"],
        doc_types=["audited_annual"],
    )
    assert {"key": "valid_from_ts", "range": {"lte": 1774500000}} in q_filter["must"]
    assert any(c.get("key") == "sub_doc_slot" for c in q_filter["must"])

    ingest_req = DocumentObjectIngestRequest(
        source_url="https://example.com/hpg_2025.pdf",
        original_filename="hpg_2025.pdf",
        expected_sha256="a" * 64,
        expected_size_bytes=1024,
        fiscal_year=2025,
        sub_doc_slot="S03",
    )
    assert ingest_req.fiscal_year == 2025
    assert ingest_req.sub_doc_slot == "S03"
    s_req = SearchRequest(query="doanh thu HPG", as_of_ts=1774500000)
    g_req = GlobalSearchRequest(query="so sánh HPG HSG", as_of_ts=1774500000)
    assert s_req.as_of_ts == 1774500000
    assert g_req.as_of_ts == 1774500000


@pytest.mark.asyncio
async def test_a1_a3_hipporag_pit_and_vn_accounting_artifact_bridge() -> None:
    src = SimpleNamespace(id="src-1", name="HPG Source", sag_source_config_id="cfg-1")
    past_ts = int(_utc(2025, 4, 1).timestamp())
    future_ts = int(_utc(2026, 6, 1).timestamp())
    as_of_ts = int(_utc(2026, 5, 1).timestamp())

    group = _SearchGroup(
        project_id="HPG",
        tenant_id="t1",
        partition_id="p1",
        source=src,
        versions=("ver-past",),
    )
    captured_filters: list[dict] = []

    class FakeQdrantStore:
        async def search_batch(self, _collection: str, searches: list[dict]):
            for item in searches:
                captured_filters.append(item["search_filter"])
            return [[] for _ in searches]

    await _query_group(
        group,
        query="doanh thu",
        limit=5,
        store=FakeQdrantStore(),  # type: ignore[arg-type]
        query_vector=[1.0, 0.0],
        as_of_ts=as_of_ts,
    )
    assert captured_filters
    assert {"key": "valid_from_ts", "range": {"lte": as_of_ts}} in captured_filters[0]["must"]

    # Multi-hop expansion must reject future versions & future KUs even if edge weight is high
    rv_past = _ReadyVersion("ver-past", "src-1", "HPG", "t1", "p1", src, valid_from_ts=past_ts)
    rv_future = _ReadyVersion("ver-future", "src-1", "HPG", "t1", "p1", src, valid_from_ts=future_ts)

    seed_sec = RetrievedSection(
        chunk_id="su-past",
        search_unit_id="su-past",
        document_id="doc-past",
        document_name="BCTC 2024",
        document_version_id="ver-past",
        section_path="TM-12",
        heading="Hàng tồn kho",
        content="Xem Thuyết minh 12 chi tiết hàng tồn kho.",
        score=0.90,
        rank=0,
        source_id="src-1",
    )
    future_sec = RetrievedSection(
        chunk_id="su-future",
        search_unit_id="su-future",
        document_id="doc-future",
        document_name="BCTC Q2 2026",
        document_version_id="ver-future",
        section_path="TM-12",
        heading="Hàng tồn kho Q2 2026",
        content="Thông tin tương lai chưa công bố.",
        score=0.95,
        rank=0,
        source_id="src-1",
    )
    ku_past = _CompactKU(
        id="ku-past",
        document_version_id="ver-past",
        ordinal=0,
        sec_path_norm="tm 12",
        entities_raw=("Hàng tồn kho", "TM-12"),
        entities_norm=frozenset({"hàng tồn kho", "tm-12"}),
        valid_from_ts=past_ts,
    )
    ku_future = _CompactKU(
        id="ku-future",
        document_version_id="ver-future",
        ordinal=0,
        sec_path_norm="tm 12",
        entities_raw=("Hàng tồn kho",),
        entities_norm=frozenset({"hàng tồn kho", "tm-12"}),
        valid_from_ts=future_ts,
    )
    k_index = _ScopeKnowledgeIndex(
        ku_by_version={"ver-past": [ku_past], "ver-future": [ku_future]},
        ku_by_id={"ku-past": ku_past, "ku-future": ku_future},
        edges_by_ku={"ku-past": [("ku-future", 0.99)], "ku-future": [("ku-past", 0.99)]},
        ent_exact_versions={"tm-12": {"ver-past", "ver-future"}},
        hydrated_su_by_version={
            "ver-past": [(seed_sec, 0, 0)],
            "ver-future": [(future_sec, 0, 0)],
        },
        lineage_by_version_ordinal={},
    )

    expanded = await _expand_multi_hop_sections(
        seed_sections=[seed_sec],
        unit_ordinals={"su-past": (0, 0)},
        current_versions={"ver-past": rv_past, "ver-future": rv_future},
        existing_unit_ids={"su-past"},
        sources=[src],
        limit=5,
        query="Thuyết minh 12 hàng tồn kho",
        preloaded_k_index=k_index,
        as_of_ts=as_of_ts,
    )
    assert all(sec.chunk_id != "su-future" for sec in expanded)

    # A3: VN accounting artifact normalization
    assert _is_artifact_code_norm("tm-12")
    assert _is_artifact_code_norm("b09-dn/hn")
    assert _is_artifact_code_norm("tt334")
    artifact_codes = _extract_artifact_codes(
        "Xem Thuyết minh 12 và Note 5 cùng biểu mẫu B09-DN/HN, TT334"
    )
    assert "tm-12" in artifact_codes
    assert "tm-5" in artifact_codes
    assert "b09-dn/hn" in artifact_codes
    assert "tt334" in artifact_codes
    bridge_terms = _extract_vn_financial_bridge_terms("biên lợi nhuận gộp")
    assert "gpm" in bridge_terms


def test_a2_leiden_research_profile_and_pit_node_mask() -> None:
    default_cfg = TreeBuildConfig()
    research_cfg = research_tree_build_config()
    assert (
        default_cfg.target_cluster_size,
        default_cfg.max_cluster_size,
        default_cfg.leiden_resolution,
    ) == (6, 20, 0.08)
    assert (
        research_cfg.target_cluster_size,
        research_cfg.max_cluster_size,
        research_cfg.leiden_resolution,
    ) == (6, 20, 0.08)

    t_past = _utc(2025, 4, 1)
    t_future = _utc(2026, 7, 1)
    units = [
        KnowledgeUnitInput(
            unit_id=f"ku-{i}",
            tenant_id="t1",
            project_id="HPG",
            security_partition_id="p1",
            dense=(1.0, 0.1 * i),
            sparse=(("doanh_thu", 1.0),),
            entities=("HPG",),
            valid_from=t_past if i < 4 else t_future,
        )
        for i in range(6)
    ]
    edges = [KnowledgeEdgeInput(f"ku-{i}", f"ku-{i+1}", 0.85) for i in range(5)]
    snap_pit = build_routing_snapshot(
        units,
        edges,
        config=TreeBuildConfig(target_cluster_size=2, max_cluster_size=4, max_children=4),
        as_of_ts=int(_utc(2026, 5, 1).timestamp()),
    )
    assert all(uid in {"ku-0", "ku-1", "ku-2", "ku-3"} for root in snap_pit.roots for uid in root.unit_ids)

    snap_full = build_routing_snapshot(
        units,
        edges,
        config=TreeBuildConfig(target_cluster_size=2, max_cluster_size=4, max_children=4),
    )
    kept_roots = filter_nodes_pit(snap_full.roots, int(_utc(2026, 5, 1).timestamp()))
    assert all(
        node.profile.temporal_from is None or node.profile.temporal_from <= _utc(2026, 5, 1)
        for node in kept_roots
    )

    # query_routing_service.route_snapshot enforces PIT node mask + recency_intent
    fp = scope_fingerprint(
        {
            "project_id": "HPG",
            "source_ids": ("src-1",),
            "document_version_ids": ("ver-1",),
            "tenant_id": "t1",
            "partition_id": "p1",
        }
    )

    def _mk_node(node_id: str, ts: int) -> NodeProfile:
        return NodeProfile(
            node_id=node_id,
            is_leaf=True,
            accessible_unit_count=3,
            project_id="HPG",
            source_ids=("src-1",),
            document_version_ids=("ver-1",),
            tenant_id="t1",
            partition_id="p1",
            tree_version="tv-1",
            scope_fingerprint=fp,
            signal_scores={"dense": 0.9, "sparse": 0.8},
            temporal_from_ts=ts,
            temporal_to_ts=ts,
        )

    n_past = _mk_node("n-past", int(_utc(2025, 4, 1).timestamp()))
    n_recent = _mk_node("n-recent", int(_utc(2026, 3, 1).timestamp()))
    n_future = _mk_node("n-future", int(_utc(2026, 8, 1).timestamp()))
    group_snap = GroupRoutingSnapshot(
        snapshot_id="snap-1",
        captured_at=_utc(2026, 5, 1),
        project_id="HPG",
        source_ids=("src-1",),
        document_version_ids=("ver-1",),
        tenant_id="t1",
        partition_id="p1",
        scope_fingerprint=fp,
        tree_version="tv-1",
        tree_config_version="routing-tree-v1",
        routing_slot="SLOT_A",
        search_epoch=1,
        manifest_status="ACTIVE",
        manifest_checksum="a" * 64,
        manifest_verified=True,
        profiles=(n_past, n_recent, n_future),
    )
    decision = route_snapshot(
        group_snap,
        policy=RoutingPolicy(beam_width=1, decisive_margin=0.0, broad_margin_threshold=0.0),
        as_of_ts=int(_utc(2026, 5, 1).timestamp()),
        recency_intent=True,
    )
    assert "n-future" not in decision.selected_nodes
    assert decision.selected_nodes[0] == "n-recent"


def test_a4_dense_graph_temporal_plan_actual_and_vn_note_citations() -> None:
    assert len(VN_FINANCIAL_ALIASES) >= 100
    f_cfg = build_financial_knowledge_config()
    assert any(canon == "CASA" for _, canon in f_cfg.aliases)

    u_plan = KnowledgeUnit(
        id="u-plan",
        tenant_id="t1",
        project_id="HPG",
        document_version_id="v-s03-2024",
        security_partition_id="p1",
        source_id="src1",
        ordinal=0,
        text="Kế hoạch doanh thu năm 2025 tại Thuyết minh 12.",
        features_json={"entities": ["HPG", "Doanh thu"], "citations": []},
        provenance_json={"document_id": "doc-plan", "section_path": "S03", "sub_doc_slot": "S03", "fiscal_year": 2024},
        valid_from=_utc(2025, 3, 25),
        valid_to=_utc(2025, 12, 31),
        checksum="c1",
    )
    u_actual = KnowledgeUnit(
        id="u-actual",
        tenant_id="t1",
        project_id="HPG",
        document_version_id="v-s03-2025",
        security_partition_id="p1",
        source_id="src1",
        ordinal=0,
        text="Thực hiện doanh thu năm 2025 theo Thuyết minh 12.",
        features_json={"entities": ["HPG", "Doanh thu"], "citations": []},
        provenance_json={
            "document_id": "doc-actual",
            "section_path": "S03",
            "sub_doc_slot": "S03",
            "fiscal_year": 2025,
        },
        valid_from=_utc(2026, 3, 25),
        valid_to=_utc(9999, 12, 31),
        checksum="c2",
    )
    payload = graph_payload([u_plan, u_actual], KnowledgeConfig(edge_min_score=0.0))
    assert len(payload["edges"]) == 1
    signals = payload["edges"][0]["signals"]
    assert signals["temporal"] == 1.0
    assert signals["citation"] == 0.85

    # Non-overlapping non-plan pair still meets >= 0.50 temporal floor
    u_other = KnowledgeUnit(
        id="u-other",
        tenant_id="t1",
        project_id="HPG",
        document_version_id="v-s13-2026",
        security_partition_id="p1",
        source_id="src1",
        ordinal=0,
        text="Báo cáo triển vọng ngành thép dài hạn.",
        features_json={"entities": ["HPG"], "citations": []},
        provenance_json={"document_id": "doc-other", "section_path": "S01", "sub_doc_slot": "S01", "fiscal_year": 2026},
        valid_from=_utc(2026, 6, 1),
        valid_to=_utc(9999, 12, 31),
        checksum="c3",
    )
    payload_floor = graph_payload([u_plan, u_other], KnowledgeConfig(edge_min_score=0.0))
    pair_edge = next(e for e in payload_floor["edges"] if {e["source"], e["target"]} == {"u-other", "u-plan"})
    assert pair_edge["signals"]["temporal"] >= 0.50


def test_a5_table_safe_jaccard_mmr_preserves_distinct_fiscal_tables() -> None:
    sec_2024 = RetrievedSection(
        chunk_id="tbl-2024",
        document_id="doc-2024",
        document_name="HPG_BCTC_FY2024.md",
        section_path="B02-DN/HN > Kết quả kinh doanh FY2024",
        heading="Báo cáo kết quả hoạt động kinh doanh FY2024",
        content="| Chỉ tiêu | Giá trị |\n| Doanh thu thuần | 140.500.000 |\n| Lợi nhuận sau thuế | 12.020.000 |",
        score=0.92,
        rank=0,
        source_id="src-1",
    )
    sec_2025 = RetrievedSection(
        chunk_id="tbl-2025",
        document_id="doc-2025",
        document_name="HPG_BCTC_FY2025.md",
        section_path="B02-DN/HN > Kết quả kinh doanh FY2025",
        heading="Báo cáo kết quả hoạt động kinh doanh FY2025",
        content="| Chỉ tiêu | Giá trị |\n| Doanh thu thuần | 162.800.000 |\n| Lợi nhuận sau thuế | 15.400.000 |",
        score=0.90,
        rank=1,
        source_id="src-1",
    )
    selected, _ = select_diverse_sections(
        "doanh thu thuần lợi nhuận sau thuế",
        [sec_2024, sec_2025],
        limit=2,
        near_duplicate_similarity=0.90,
    )
    assert {s.chunk_id for s in selected} == {"tbl-2024", "tbl-2025"}


def test_a6_query_analysis_vietnamese_temporal_and_identifiers() -> None:
    analysis = analyze_query(
        "Xem doanh thu mới nhất gần đây 9T/2026 và Quý 3/2026 trong Thuyết minh 12 biểu mẫu B09-DN/HN"
    )
    assert analysis.features.recency_intent is True
    assert "9T/2026" in analysis.features.temporal_cues
    assert any("3/2026" in expr for expr in analysis.features.temporal_cues)
    assert any("thuyết minh 12" in ident.casefold() for ident in analysis.features.identifier_terms)
    assert any("b09-dn/hn" in ident.casefold() for ident in analysis.features.identifier_terms)
    assert analysis.features.recency_intent


@pytest.mark.asyncio
async def test_a7_half_life_batch_metrics_and_3layer_abstention_blindspots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = _utc(2026, 11, 20)
    published = as_of - timedelta(days=90)
    ev = EvidenceSignal(evidence_id="ev-1", quote="Doanh thu 100 tỷ", locator="p3", confidence=0.95)
    u_annual = UnitSignal(unit_id="u1", source_id="s1", published_at=published, sub_doc_slot="S03", evidence=(ev,))
    u_agm = UnitSignal(unit_id="u2", source_id="s1", published_at=published, sub_doc_slot="S12", evidence=(ev,))
    u_qtr = UnitSignal(unit_id="u3", source_id="s1", published_at=published, sub_doc_slot="S13", evidence=(ev,))

    assert resolve_unit_half_life_days(u_annual, 30.0) == 365.0
    assert resolve_unit_half_life_days(u_agm, 30.0) == 180.0
    assert resolve_unit_half_life_days(u_qtr, 30.0) == 90.0

    # At age=90d and half_life=90d, freshness of quarterly unit == 0.5
    topic = QualityTopicInput(
        tenant_id="t1",
        project_id="HPG",
        security_partition_id="p1",
        tree_version="tv-1",
        node_id="n-qtr",
        as_of=as_of,
        demand_window_days=30,
        units=(u_qtr,),
    )
    res = score_topic(topic)
    assert res.eligible is False
    assert res.metrics.freshness == pytest.approx(0.5, rel=1e-6)
    batch = extract_batch_quality_metrics([topic])
    assert batch[0]["eligible"] is False
    assert batch[0]["metrics"]["freshness"] == pytest.approx(0.5, rel=1e-6)

    # Layer 1 abstention
    ans_empty = await synthesize_search_answer("Doanh thu HPG?", [], llm=None)
    assert ans_empty.status == "no_answer"
    assert ans_empty.declared_blindspots and ans_empty.declared_blindspots[0]["scope"] == "retrieval"

    # Layer 2 & 3 abstention + partial blindspot extraction
    src = SimpleNamespace(id="src-1", name="HPG BCTC 2025", sag_source_config_id="cfg-1")

    async def _passthrough_traceable(sections, _sources):
        return sections

    monkeypatch.setattr("sag_api.services.retrieval_service.resolve_traceable_evidence", _passthrough_traceable)
    sec = RetrievedSection(
        chunk_id="c1",
        search_unit_id="c1",
        document_id="doc1",
        document_version_id="ver-1",
        document_name="HPG_2025.md",
        section_path="B02",
        heading="KQKD HPG 2025",
        content=(
            "Doanh thu thuần hợp nhất năm 2025 của Công ty Cổ phần Tập đoàn Hòa Phát (HPG) "
            "đạt 162.800 tỷ VND, tăng trưởng 18% so với năm 2024 theo báo cáo tài chính hợp nhất đã kiểm toán. "
            "Lợi nhuận sau thuế hợp nhất đạt 15.400 tỷ VND."
        ),
        score=0.91,
        rank=0,
        source_id="src-1",
        source_config_id="cfg-1",
        page_from=4,
        page_to=4,
        anchor="p4",
    )

    class PartialBlindspotLLM:
        configured = True

        async def complete(self, messages) -> str:
            return "Doanh thu thuần năm 2025 đạt 162.800 tỷ VND [1]. Tuy nhiên chưa có dữ liệu về kế hoạch cổ tức 2026."

        async def stream_complete(self, messages):
            yield "Không đủ bằng chứng trong tài liệu để trả lời câu hỏi."

    ans_partial = await synthesize_search_answer(
        "Doanh thu thuần năm 2025 của HPG?",
        [sec],
        llm=PartialBlindspotLLM(),
        sources=[src],  # type: ignore[list-item]
    )
    assert ans_partial.status == "answered"
    assert any(item["code"] == "PARTIAL_MISSING_DATA" for item in ans_partial.declared_blindspots)

    updates = [
        upd
        async for upd in stream_synthesize_search_answer(
            "Doanh thu thuần năm 2025 của HPG?",
            [sec],
            llm=PartialBlindspotLLM(),
            sources=[src],  # type: ignore[list-item]
        )
    ]
    assert updates[-1].status == "no_answer"
    assert updates[-1].declared_blindspots
    assert updates[-1].declared_blindspots[0]["code"] == "LLM_ABSTENTION_INSUFFICIENT_EVIDENCE"
