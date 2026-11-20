import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    KnowledgeUnit,
    SearchUnit,
    Source,
    SourceProjectMapping,
    StageRun,
)
from sag_api.db.models.document import DocumentStatus
from sag_api.jobs.knowledge import KnowledgeWorker
from sag_api.sag.config_builder import (
    PostgresRelationalStore,
    build_engine_config,
    extract_postgres_search_path,
)
from sag_api.sag.incremental_processor import detect_document_language
from sag_api.services.knowledge_service import (
    KnowledgeConfig,
    extract_e0_e1,
    graph_payload,
)


METRIC_PARTITION_UNIVERSE_BLOCKS = [
    (
        "Northwind Labs Knowledge Governance Playbook",
        "The Knowledge Governance Team maintains the AI Quality Register for enterprise "
        "search and retrieval review. Every release candidate is evaluated before "
        "production cutover.",
    ),
    (
        "Northwind Labs Knowledge Governance Playbook > Baseline Metrics",
        "Before the Evidence Gate rollout, weekly review sampled 50 answers per release "
        "and observed an unsupported claim rate of 19%. Reviewer triage required three "
        "separate spreadsheets and could not trace every statement back to a canonical block.",
    ),
    (
        "Northwind Labs Knowledge Governance Playbook > Post-Rollout Metrics",
        "After the Evidence Gate and Evidence Ledger launched in Q2, weekly review "
        "expanded to 120 answers per release while the unsupported claim rate dropped "
        "to 4%. Each accepted claim now links to a document version, canonical block, "
        "and character span.",
    ),
    (
        "Northwind Labs Knowledge Governance Playbook > Security Partition Rules",
        "The Access Control Service assigns every search unit and knowledge unit a "
        "security partition before publication. Cross-partition graph edges are "
        "discarded during universe build, and retrieval queries must filter by "
        "tenant, project, source mapping, and partition.",
    ),
    (
        "Northwind Labs Knowledge Governance Playbook > Operational Thresholds",
        "If the unsupported claim rate exceeds 5 percent or any cross-partition "
        "reference appears in audit logs, the release is blocked until the "
        "Governance Team re-indexes the affected source and verifies the manifest "
        "checksum. Minimum retrieval confidence is 0.75 for automated routing; "
        "ACL mismatches trigger an immediate rollback.",
    ),
]


def _make_block(section_path: str, text: str, ordinal: int = 0) -> CanonicalBlock:
    return CanonicalBlock(
        id=f"block-{ordinal}",
        document_version_id="ver-1",
        ordinal=ordinal,
        block_type="paragraph",
        page_from=1,
        page_to=1,
        section_path=section_path,
        source_anchor=f"anchor-{ordinal}",
        normalized_text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _make_unit(section_path: str, text: str, ordinal: int = 0, dense: list[float] | None = None) -> KnowledgeUnit:
    now = datetime.now(UTC)
    block = _make_block(section_path, text, ordinal)
    _, features = extract_e0_e1(block, KnowledgeConfig(), dense=dense)
    return KnowledgeUnit(
        id=f"unit-{ordinal}",
        document_version_id="ver-1",
        tenant_id="tenant-1",
        project_id="proj-1",
        source_id="src-1",
        security_partition_id="part-1",
        ordinal=ordinal,
        text=text,
        content_hash=block.content_hash,
        input_checksum=f"in-{ordinal}",
        checksum="pending",
        is_current=True,
        provenance_json={
            "document_id": "doc-1",
            "section_path": section_path,
            "evidence_block_ids": [block.id],
        },
        features_json=features,
        valid_from=now - timedelta(hours=1),
        valid_to=now + timedelta(days=1),
    )


def test_task1_extract_e0_e1_multiword_entities_decimals_and_sentence_relations():
    blocks = [_make_block(sec, text, idx) for idx, (sec, text) in enumerate(METRIC_PARTITION_UNIVERSE_BLOCKS)]
    facts_by_block: list[list[dict]] = []
    features_by_block: list[dict] = []
    for block in blocks:
        facts, features = extract_e0_e1(block)
        facts_by_block.append(facts)
        features_by_block.append(features)
        for fact in facts:
            start, end, quote = fact["start"], fact["end"], fact["quote"]
            assert 0 <= start < end <= len(block.normalized_text)
            assert block.normalized_text[start:end] == quote

    entities_by_block = [f["entities"] for f in features_by_block]
    assert "Knowledge Governance Team" in entities_by_block[0]
    assert "AI Quality Register" in entities_by_block[0]
    assert "Evidence Gate" in entities_by_block[1]
    assert "19%" in entities_by_block[1]
    assert "Evidence Gate" in entities_by_block[2]
    assert "Evidence Ledger" in entities_by_block[2]
    assert "Q2" in entities_by_block[2]
    assert "4%" in entities_by_block[2]
    assert "Access Control Service" in entities_by_block[3]
    assert "Governance Team" in entities_by_block[4]
    assert "0.75" in entities_by_block[4]
    assert "ACL" in entities_by_block[4]

    # Per-sentence claim spam and synthetic CO_OCCURS relation bloat are permanently removed
    for facts in facts_by_block:
        assert all(f["kind"] in {"entity", "alias", "passage"} for f in facts)
    for features in features_by_block:
        assert "e2_reason" not in features


def test_task1_and_task4_graph_payload_entity_none_and_dense_signal():
    u_no_ent_1 = _make_unit(
        "Section A",
        "cross-partition edges are discarded during universe build.",
        0,
        dense=[1.0, 0.0, 0.0],
    )
    u_no_ent_2 = _make_unit(
        "Section A",
        "retrieval queries filter cross-partition edges during build.",
        1,
        dense=[0.8, 0.6, 0.0],
    )

    payload = graph_payload([u_no_ent_1, u_no_ent_2])
    assert len(payload["edges"]) == 1
    edge = payload["edges"][0]
    assert edge["signals"]["entity"] is None
    # _cosine maps cos(theta)=0.8 into [0,1] via (0.8 + 1) / 2 = 0.9
    assert edge["signals"]["semantic"] == pytest.approx(0.9, rel=1e-4)


def test_task2_postgres_search_path_extraction_and_config_builder():
    url = "postgresql+asyncpg://postgres:secret@127.0.0.1:5432/sag?search_path=sag_VERIFY_b033290e53"
    assert extract_postgres_search_path(url) == "sag_VERIFY_b033290e53"
    assert extract_postgres_search_path("postgresql+asyncpg://postgres:secret@127.0.0.1:5432/sag") is None

    settings = Settings(
        database_url=url,
        sag_relational_provider="postgres",
        llm_api_key="test-key",
        embedding_api_key="test-key",
    )
    cfg = build_engine_config(settings)
    assert cfg.relational is not None
    assert cfg.relational.provider == "postgres"
    assert getattr(cfg.relational, "_sag_search_path", None) == "sag_VERIFY_b033290e53"
    store = PostgresRelationalStore(provider="postgres", config=cfg.relational)
    assert getattr(store._runtime, "_search_path", None) == "sag_VERIFY_b033290e53"


def test_task3_detect_document_language():
    en_text = (
        "The Knowledge Governance Team maintains the AI Quality Register for enterprise "
        "search and retrieval review."
    )
    vi_text = (
        "Đội ngũ quản trị tri thức duy trì sổ đăng ký chất lượng AI cho quy trình "
        "tìm kiếm và truy xuất doanh nghiệp."
    )
    assert detect_document_language(en_text, configured="zh") == "en"
    assert detect_document_language(vi_text, configured="zh") == "en"
    assert detect_document_language(en_text, configured="en") == "en"
    assert Settings(sag_language="zh").sag_language == "en"


@pytest_asyncio.fixture
async def remediation_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_task4_knowledge_worker_embeds_dense_vectors(remediation_db):
    now = datetime.now(UTC)
    async with remediation_db() as session:
        session.add(Source(id="src-1", name="Source 1", sag_source_config_id="cfg-1"))
        session.add(
            SourceProjectMapping(
                source_id="src-1",
                organization_id="org-1",
                project_id="proj-1",
                state="CONFIRMED",
                mapping_version=1,
                confirmed_at=now,
                confirmed_by="auditor",
                approval_ref="ref-1",
            )
        )
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                project_id="proj-1",
                source_id="src-1",
                filename="playbook.md",
                storage_path="unused",
                status=DocumentStatus.READY,
                is_active=True,
            )
        )
        session.add(
            DocumentVersion(
                id="ver-1",
                document_id="doc-1",
                version_no=1,
                file_hash="hash-1",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(days=1),
                search_status="SEARCH_READY",
                search_ready_at=now,
                knowledge_status="NOT_STARTED",
                metadata_json={"security_partition_id": "part-1"},
            )
        )
        for idx, (sec, text) in enumerate(METRIC_PARTITION_UNIVERSE_BLOCKS[:2]):
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            session.add(
                CanonicalBlock(
                    id=f"blk-{idx}",
                    document_version_id="ver-1",
                    ordinal=idx,
                    block_type="paragraph",
                    page_from=1,
                    page_to=1,
                    section_path=sec,
                    normalized_text=text,
                    content_hash=content_hash,
                )
            )
        await session.commit()

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, float(i + 1), 0.5] for i in range(len(texts))]

    worker = KnowledgeWorker(remediation_db, Settings(), embed=fake_embed)
    await worker.discover()
    assert await worker.run_one() is True

    async with remediation_db() as session:
        units = (
            await session.scalars(select(KnowledgeUnit).order_by(KnowledgeUnit.ordinal))
        ).all()
        assert len(units) == 2
        for unit in units:
            assert "dense" in unit.features_json
            assert len(unit.features_json["dense"]) == 3


@pytest.mark.asyncio
async def test_task5_paused_and_failed_documents_excluded_from_retrieval_scope(remediation_db):
    from sag_api.services.search_unit_retrieval_service import (
        _load_current_ready_versions_checked,
    )

    now = datetime.now(UTC)
    part_id = "proj-1:src-1:v1"
    async with remediation_db() as session:
        session.add(Source(id="src-1", name="Source 1", sag_source_config_id="cfg-1"))
        session.add(
            SourceProjectMapping(
                source_id="src-1",
                organization_id="org-1",
                project_id="proj-1",
                state="CONFIRMED",
                mapping_version=1,
                confirmed_at=now,
                confirmed_by="auditor",
                approval_ref="ref-1",
            )
        )
        for status, suffix in [
            (DocumentStatus.READY, "ready"),
            (DocumentStatus.PAUSED, "paused"),
            (DocumentStatus.FAILED, "failed"),
        ]:
            doc_id = f"doc-{suffix}"
            ver_id = f"ver-{suffix}"
            blk_id = f"blk-{suffix}"
            run_id = f"run-{suffix}"
            session.add(
                Document(
                    id=doc_id,
                    tenant_id="tenant-1",
                    project_id="proj-1",
                    source_id="src-1",
                    filename=f"{suffix}.md",
                    storage_path="unused",
                    status=status,
                    is_active=True,
                )
            )
            session.add(
                DocumentVersion(
                    id=ver_id,
                    document_id=doc_id,
                    version_no=1,
                    file_hash=f"hash-{suffix}",
                    valid_from=now - timedelta(hours=1),
                    valid_to=now + timedelta(days=1),
                    search_status="SEARCH_READY",
                    search_ready_at=now,
                    metadata_json={"security_partition_id": part_id},
                )
            )
            session.add(
                CanonicalBlock(
                    id=blk_id,
                    document_version_id=ver_id,
                    ordinal=0,
                    block_type="paragraph",
                    page_from=1,
                    page_to=1,
                    section_path="Section",
                    normalized_text=f"Content for {suffix}",
                    content_hash=f"ch-{suffix}",
                )
            )
            session.add(
                SearchUnit(
                    id=f"su-{suffix}",
                    document_version_id=ver_id,
                    block_from_id=blk_id,
                    block_to_id=blk_id,
                    security_partition_id=part_id,
                    content_hash=f"ch-{suffix}",
                    token_count=5,
                    page_from=1,
                    page_to=1,
                    section_path="Section",
                )
            )
            session.add(
                IngestionRun(
                    id=run_id,
                    tenant_id="tenant-1",
                    project_id="proj-1",
                    document_version_id=ver_id,
                    idempotency_key=f"idem-{suffix}",
                    payload_hash=f"ph-{suffix}",
                    status="COMPLETED",
                    started_at=now - timedelta(minutes=5),
                    completed_at=now,
                )
            )
            chk = hashlib.sha256(suffix.encode()).hexdigest()
            session.add(
                StageRun(
                    id=f"stage-{suffix}",
                    run_id=run_id,
                    stage="INDEX_SEARCH",
                    status="SUCCESS",
                    duration_ms=10.0,
                    created_at=now,
                    metrics_json={
                        "manifest_verified": True,
                        "collection_name": "search_units_proj-1",
                        "search_unit_count": 1,
                        "pg_count": 1,
                        "qdrant_count": 1,
                        "qdrant_indexed_count": 1,
                        "manifest_checksum": chk,
                        "qdrant_checksum": chk,
                    },
                )
            )
        await session.commit()

        principal = SimpleNamespace(
            subject="user-1",
            tenant_id="tenant-1",
            organization_id="org-1",
            allowed_project_ids=frozenset({"proj-1"}),
            allowed_partition_ids=frozenset({part_id}),
        )
        sources = [
            SimpleNamespace(
                id="src-1",
                name="Source 1",
                project_id="proj-1",
                tenant_id="tenant-1",
                organization_id="org-1",
                partition_id=part_id,
                mapping_version=1,
            )
        ]
        ready_versions = await _load_current_ready_versions_checked(session, sources, principal)
        assert list(ready_versions.keys()) == ["ver-ready"]


@pytest.mark.asyncio
async def test_search_unit_qdrant_store_batch_query_single_roundtrip() -> None:
    """Verify Qdrant points/query/batch executes multiple channels in 1 HTTP round-trip."""
    import httpx
    from sag_api.sag.search_unit_store import SearchUnitQdrantStore

    requests_seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode("utf-8"))
        requests_seen.append((request.method, request.url.path, body))
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "points": [
                            {
                                "id": "pt-dense-1",
                                "score": 0.91,
                                "payload": {"search_unit_id": "su-1"},
                            }
                        ]
                    },
                    {
                        "points": [
                            {
                                "id": "pt-sparse-1",
                                "score": 4.25,
                                "payload": {"search_unit_id": "su-2"},
                            }
                        ]
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qdrant.example.com",
    ) as client:
        store = SearchUnitQdrantStore(client)
        batch_hits = await store.search_batch(
            "search_units_proj-1",
            searches=[
                {
                    "vector_name": "content_vector",
                    "query": [0.1, 0.2, 0.3],
                    "search_filter": {"must": []},
                    "limit": 5,
                },
                {
                    "vector_name": "bm25_sparse",
                    "query": {"indices": [10, 20], "values": [1.0, 2.0]},
                    "search_filter": {"must": []},
                    "limit": 5,
                },
                {
                    "vector_name": "bm25_sparse",
                    "query": {"indices": [], "values": []},
                    "search_filter": {"must": []},
                    "limit": 5,
                },
            ],
        )

    assert len(requests_seen) == 1
    assert requests_seen[0][1] == "/collections/search_units_proj-1/points/query/batch"
    assert len(requests_seen[0][2]["searches"]) == 2
    assert len(batch_hits) == 3
    assert batch_hits[0][0].point_id == "pt-dense-1"
    assert batch_hits[1][0].point_id == "pt-sparse-1"
    assert batch_hits[2] == []


def test_dual_brain_entity_bridge_enriches_knowledge_unit() -> None:
    """Verify LightRAG-style Dual-Level entity bridge harvests lowercase/domain LLM entities into E1."""
    from sag_api.services.knowledge_service import extract_e0_e1

    block = CanonicalBlock(
        id="blk-bridge-1",
        document_version_id="ver-bridge-1",
        ordinal=0,
        block_type="paragraph",
        page_from=1,
        page_to=1,
        section_path="Intro",
        normalized_text="The personalized pagerank algorithm connects semantic concepts across passages.",
        content_hash="unused",
    )
    facts_without, features_without = extract_e0_e1(block)
    assert "personalized pagerank" not in features_without["entities"]

    facts_with, features_with = extract_e0_e1(
        block,
        extra_entities=("personalized pagerank", "semantic concepts"),
    )
    assert "personalized pagerank" in features_with["entities"]
    assert "semantic concepts" in features_with["entities"]
    bridged = [f for f in facts_with if f.get("extractor_version") == "e1-sag-engine-bridge-v1"]
    assert len(bridged) == 2
    for fact in bridged:
        assert block.normalized_text[fact["start"] : fact["end"]] == fact["quote"]


def test_incremental_processor_disables_event_entity_edge_vectors_by_default() -> None:
    """Verify HippoRAG 2 edge-vector suppression is enabled by default."""
    from sag_api.core.config import Settings
    from sag_api.sag.incremental_processor import IncrementalDocumentProcessor

    default_settings = Settings()
    assert default_settings.document_write_event_entity_vectors is False
    assert default_settings.document_extraction_profile == "concise"

    processor = IncrementalDocumentProcessor(
        SimpleNamespace(),
        "scid-1",
        max_concurrency=6,
    )
    assert processor._write_event_entity_vectors is False
    assert processor._extraction_profile == "concise"


def test_bilingual_vietnamese_english_stopwords_and_diacritics() -> None:
    """Verify Vietnamese + English stopwords and Unicode diacritics in lexical/sparse/query analysis."""
    from pathlib import Path
    import sag_api.sag as sag_pkg
    from sag_api.sag.search_unit_store import _SPARSE_QUERY_STOPWORDS, build_sparse_query_vector
    from sag_api.services.query_analysis import analyze_query
    from sag_api.services.retrieval_service import _LEXICAL_STOPWORDS, _lexical_relevance
    from sag_api.services.search_index_service import COMMON_STOPWORDS

    # fast_graph_processor.py must not exist
    assert not (Path(sag_pkg.__file__).parent / "fast_graph_processor.py").exists()

    # Single source of truth for bilingual stopwords
    assert _SPARSE_QUERY_STOPWORDS is COMMON_STOPWORDS
    assert _LEXICAL_STOPWORDS is COMMON_STOPWORDS
    for vi_stop in ("là", "của", "những", "được", "trong", "với", "tại", "không"):
        assert vi_stop in COMMON_STOPWORDS
    for en_stop in ("the", "what", "where", "which", "about"):
        assert en_stop in COMMON_STOPWORDS

    # analyze_query preserves Vietnamese diacritics and strips Vietnamese stopwords
    plan = analyze_query("Quy trình kiểm duyệt của hệ thống là gì?")
    assert "quy" in plan.scoring_terms
    assert "trình" in plan.scoring_terms
    assert "kiểm" in plan.scoring_terms
    assert "duyệt" in plan.scoring_terms
    assert "hệ" in plan.scoring_terms
    assert "thống" in plan.scoring_terms
    assert "của" not in plan.scoring_terms
    assert "là" not in plan.scoring_terms
    assert "gì" not in plan.scoring_terms

    # _lexical_relevance scores Vietnamese content tokens without diluting on "là", "của", "gì"
    sec = SimpleNamespace(
        heading="Quy trình kiểm duyệt",
        content="Quy trình kiểm duyệt bảo mật trong hệ thống phân quyền.",
    )
    rel_score = _lexical_relevance(
        "Quy trình kiểm duyệt của hệ thống là gì?",
        sec,
        analysis=plan,
    )
    assert rel_score == pytest.approx(0.45)

    # Sparse query vector strips Vietnamese stopwords when informative tokens are present
    with_stops_vec = build_sparse_query_vector("kiểm duyệt của hệ thống là gì")
    clean_vec = build_sparse_query_vector("kiểm duyệt hệ thống")
    assert with_stops_vec == clean_vec
    assert len(clean_vec["indices"]) == 4


def test_qwen3_enable_thinking_false_enforced_in_litellm_policy() -> None:
    """Verify Qwen3 on SiliconFlow always receives enable_thinking=False even if reasoning_effort exists."""
    from sag_api.core.litellm_policy import apply_litellm_completion_policy

    cfg = Settings(
        llm_model="Qwen/Qwen3-8B",
        llm_base_url="https://api.siliconflow.cn/v1",
        llm_api_key="test-key",
    )
    normalized = apply_litellm_completion_policy(
        cfg,
        {
            "model": "openai/Qwen/Qwen3-8B",
            "api_base": "https://api.siliconflow.cn/v1",
            "reasoning_effort": "low",
        },
    )
    assert normalized.get("extra_body", {}).get("enable_thinking") is False


@pytest.mark.asyncio
async def test_canonical_pipeline_reuses_search_units_and_shared_embedding_cache(remediation_db) -> None:
    """Verify IncrementalDocumentProcessor reuses canonical SearchUnits and shared embedding cache."""
    from sag_api.sag.engine_manager import EngineManager
    from sag_api.services.search_index_service import put_cached_text_embedding

    now = datetime.now(UTC)
    async with remediation_db() as session:
        session.add(Source(id="src-c1", name="Source C1", sag_source_config_id="scid-c1"))
        session.add(
            Document(
                id="doc-c1",
                tenant_id="tenant-1",
                project_id="proj-1",
                source_id="src-c1",
                filename="canonical.md",
                storage_path="unused",
                status=DocumentStatus.READY,
                is_active=True,
            )
        )
        session.add(
            DocumentVersion(
                id="ver-c1",
                document_id="doc-c1",
                version_no=1,
                file_hash="hash-c1",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(days=1),
                search_status="SEARCH_READY",
                search_ready_at=now,
            )
        )
        for idx, (sec, text) in enumerate(METRIC_PARTITION_UNIVERSE_BLOCKS[:2]):
            ch = hashlib.sha256(text.encode()).hexdigest()
            session.add(
                CanonicalBlock(
                    id=f"cblk-{idx}",
                    document_version_id="ver-c1",
                    ordinal=idx,
                    block_type="paragraph",
                    page_from=1,
                    page_to=1,
                    section_path=sec,
                    normalized_text=text,
                    content_hash=ch,
                )
            )
            session.add(
                SearchUnit(
                    id=f"csu-{idx}",
                    document_version_id="ver-c1",
                    block_from_id=f"cblk-{idx}",
                    block_to_id=f"cblk-{idx}",
                    security_partition_id="part-1",
                    content_hash=ch,
                    token_count=20,
                    page_from=1,
                    page_to=1,
                    section_path=sec,
                )
            )
        await session.commit()

    # Pre-warm shared cache for one text and verify wrapper avoids network call for cached text
    cached_text = f"{METRIC_PARTITION_UNIVERSE_BLOCKS[0][0]}\n{METRIC_PARTITION_UNIVERSE_BLOCKS[0][1]}"
    put_cached_text_embedding(cached_text, [0.25, 0.5, 0.75])

    api_calls: list[list[str]] = []

    class FakeEmbeddingClient:
        async def generate(self, text: str, **kwargs):
            api_calls.append([text])
            return [0.1, 0.2, 0.3]

        async def batch_generate(self, texts: list[str], **kwargs):
            api_calls.append(list(texts))
            return [[0.1, 0.2, 0.3] for _ in texts]

    fake_engine = SimpleNamespace(
        resources=SimpleNamespace(embedding=FakeEmbeddingClient()),
        _relational_store=SimpleNamespace(),
        _vector_store=SimpleNamespace(),
    )
    mgr = EngineManager(Settings())
    mgr._configure_embedding_request_dimensions(fake_engine)

    unprefixed_text = METRIC_PARTITION_UNIVERSE_BLOCKS[1][1]
    vecs = await fake_engine.resources.embedding.batch_generate([cached_text, unprefixed_text])
    assert vecs[0] == [0.25, 0.5, 0.75]
    assert vecs[1] == [0.1, 0.2, 0.3]
    # Only the uncached second string should have been sent to FakeEmbeddingClient
    assert api_calls == [[unprefixed_text]]


def test_first_principles_stage1_to_stage5_architectural_upgrades() -> None:
    """Verify Stage 1-5 First-Principles upgrades across parsing, query analysis, E0/E1, and rerank."""
    from sag_api.parsing.canonical import extract_canonical_blocks
    from sag_api.sag import RetrievedSection
    from sag_api.sag.search_unit_store import build_sparse_query_vector
    from sag_api.services.query_analysis import analyze_query, extract_query_features
    from sag_api.services.retrieval_service import rerank_sections, select_diverse_sections
    from sag_api.services.search_index_service import compute_sparse_bm25_vector

    # 1. Stage 1: Document title contextual injection & table header preservation
    blocks = extract_canonical_blocks(
        "## Consolidated Balance Sheets\n\n| Item | 2018 | 2017 |\n|---|---|---|\n| Total PP&E, net | $8,738 | $8,866 |",
        version_id="ver-1",
        document_title="3M 2018 10K Note6 PPE",
    )
    assert blocks[0].section_path == "3M 2018 10K Note6 PPE > Consolidated Balance Sheets"

    # Sparse BM25 indexes normalized thousand-separated numbers, ampersand initialisms, and alphanumeric tokens
    sparse = compute_sparse_bm25_vector("In FY2018, R&D recorded $8,738M of capital assets.")
    assert set(build_sparse_query_vector("8738m")["indices"]) <= set(sparse["indices"])
    assert set(build_sparse_query_vector("2018")["indices"]) <= set(sparse["indices"])
    assert set(build_sparse_query_vector("R&D")["indices"]) <= set(sparse["indices"])

    # 2. Stage 2: No 8-term truncation on long queries, preserve 2-char alnum ("3m", "q2"), detect relational multi-hop
    long_query = (
        "In the quarterly infrastructure review, how much did 3M spend on capital expenditures in FY2018 during Q2?"
    )
    analysis = analyze_query(long_query)
    assert "3m" in analysis.scoring_terms
    assert "capital" in analysis.scoring_terms
    assert "expenditures" in analysis.scoring_terms
    assert "2018" in analysis.scoring_terms
    assert "q2" in analysis.scoring_terms

    mh_features = extract_query_features(
        "What caused the database failover, and how does it affect the replication lag?"
    )
    assert mh_features.multi_hop is True

    # 3. Stage 3: Unicode diacritic entity extraction (Vietnamese + European proper nouns)
    unicode_block = _make_block(
        "Bio",
        "Xawery Żuławski is the son of Małgorzata Braunek and visited Đà Nẵng and Tây Ninh.",
        0,
    )
    _, unicode_features = extract_e0_e1(unicode_block)
    assert "Xawery Żuławski" in unicode_features["entities"]
    assert "Małgorzata Braunek" in unicode_features["entities"]
    assert "Đà Nẵng" in unicode_features["entities"]
    assert "Tây Ninh" in unicode_features["entities"]

    # 4. Stage 5: Contextual rerank & document-level diversity in select_diverse_sections
    sec_a1 = RetrievedSection(
        chunk_id="c1",
        document_id="doc-a",
        document_name="3M_2018_10K.md",
        section_path="3M 2018 10K > Note 6",
        heading="Balance Sheet",
        content="Property, plant and equipment net was $8,738 million.",
        score=0.85,
        rank=0,
        source_id="src-1",
    )
    sec_a2 = RetrievedSection(
        chunk_id="c2",
        document_id="doc-a",
        document_name="3M_2018_10K.md",
        section_path="3M 2018 10K > Note 7",
        heading="Balance Sheet Summary",
        content="Property, plant and equipment net was $8,738 million at year end.",
        score=0.82,
        rank=1,
        source_id="src-1",
    )
    sec_b1 = RetrievedSection(
        chunk_id="c3",
        document_id="doc-b",
        document_name="3M_2018_CashFlow.md",
        section_path="3M 2018 CashFlow > CapEx",
        heading="Cash Flows",
        content="Purchases of capital assets totaled $1,577 million in fiscal 2018.",
        score=0.81,
        rank=2,
        source_id="src-1",
    )
    reranked = rerank_sections("3M 2018 Note 6 property plant equipment", [sec_b1, sec_a1], limit=2)
    assert reranked.sections[0].chunk_id == "c1"

    diverse, _ = select_diverse_sections(
        "property plant equipment",
        [sec_a1, sec_a2, sec_b1],
        limit=2,
        diversity_weight=0.35,
    )
    assert [s.chunk_id for s in diverse] == ["c1", "c3"]


def test_graph_payload_same_doc_degree_quota_preserves_cross_doc_bridge() -> None:
    """Verify intra-document edges cannot monopolize all degree slots and starve cross-doc bridges."""
    cfg = KnowledgeConfig(max_degree=4)
    units: list[KnowledgeUnit] = []
    # Create 5 units in doc-A (ordinals 0..4) and 1 unit in doc-B sharing entity "Project Chronos"
    for idx in range(5):
        u = _make_unit(
            "Section A",
            f"Project Chronos milestone {idx} completed in Q1.",
            ordinal=idx,
            dense=[1.0, 0.0, 0.0],
        )
        u.id = f"docA-u{idx}"
        u.document_version_id = "ver-A"
        u.provenance_json["document_id"] = "doc-A"
        units.append(u)

    u_bridge = _make_unit(
        "Section B",
        "Project Chronos deployment schedule depends on infrastructure signoff.",
        ordinal=0,
        dense=[0.7, 0.7, 0.0],
    )
    u_bridge.id = "docB-u0"
    u_bridge.document_version_id = "ver-B"
    u_bridge.provenance_json["document_id"] = "doc-B"
    units.append(u_bridge)

    payload = graph_payload(units, cfg)
    edges_touching_u0 = [
        e for e in payload["edges"] if e["source"] == "docA-u0" or e["target"] == "docA-u0"
    ]
    cross_doc_edges = [
        e for e in edges_touching_u0 if "docB-u0" in (e["source"], e["target"])
    ]
    assert len(cross_doc_edges) == 1


def test_cognitive_upgrades_clause_decomposition_artifact_bridge_and_entropy_admission() -> None:
    """Verify the 4 First-Principles cognitive upgrades for Recall & Precision."""
    from sag_api.sag import RetrievedSection
    from sag_api.services.query_analysis import (
        decompose_query_clauses,
        extract_query_features,
        extract_salient_query_terms,
    )
    from sag_api.services.retrieval_service import select_diverse_sections
    from sag_api.services.search_unit_retrieval_service import _extract_artifact_codes

    # 1. Clause decomposition, salient proper nouns, and dispute multi-hop detection
    clauses = decompose_query_clauses(
        "Why was Northpeak's invoice disputed, and how was the billing calculation corrected?"
    )
    assert len(clauses) == 2
    salient = extract_salient_query_terms(
        "What is the root cause of the MedThink latency spike on GPU nodes?"
    )
    assert "medthink" in salient
    assert "gpu" in salient

    dispute_features = extract_query_features(
        "How does INC-9821 relate to the driver upgrade, and what caused the latency spike?"
    )
    assert dispute_features.multi_hop is True

    # 2. Prefix-normalized artifact/issue codes across headings and inline refs
    assert _extract_artifact_codes("# Sup 28488 Billing Question") == {"sup 28488"}
    assert _extract_artifact_codes("Fixed in pr-24108 for SUP-28488") == {"pr 24108", "sup 28488"}

    # 3. Entropy-aware document admission on Single-Hop vs Multi-Hop
    sh_sections = [
        RetrievedSection(
            chunk_id=f"d1-c{i}",
            document_id="doc-primary",
            document_name="runbook.md",
            heading=f"Section {i}",
            content=f"Detailed runbook procedure step {i} for failover recovery.",
            score=round(0.90 - 0.02 * i, 4),
            rank=i,
            source_id="src-1",
        )
        for i in range(5)
    ] + [
        RetrievedSection(
            chunk_id=f"noise-{j}",
            document_id=f"doc-noise-{j}",
            document_name=f"noise_{j}.md",
            heading="General Notes",
            content=f"Unrelated note {j} mentioning recovery.",
            score=round(0.62 - 0.03 * j, 4),
            rank=5 + j,
            source_id="src-1",
        )
        for j in range(3)
    ]
    sh_selected, _ = select_diverse_sections(
        "What is the runbook procedure for failover recovery?",
        sh_sections,
        limit=5,
    )
    assert len(sh_selected) == 5
    assert {s.document_id for s in sh_selected} == {"doc-primary"}

    # Multi-Hop: 4 distinct qualifying docs each get 1 slot before doc-1 takes its 2nd slot
    mh_sections = [
        RetrievedSection(
            chunk_id="mh-d1-c1",
            document_id="mh-doc-1",
            document_name="d1.md",
            heading="H1",
            content="Alpha cause of outage.",
            score=0.90,
            rank=0,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="mh-d1-c2",
            document_id="mh-doc-1",
            document_name="d1.md",
            heading="H2",
            content="Secondary paragraph of doc 1.",
            score=0.88,
            rank=1,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="mh-d2-c1",
            document_id="mh-doc-2",
            document_name="d2.md",
            heading="H3",
            content="Beta link in chain.",
            score=0.84,
            rank=2,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="mh-d3-c1",
            document_id="mh-doc-3",
            document_name="d3.md",
            heading="H4",
            content="Gamma ticket details.",
            score=0.80,
            rank=3,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="mh-d4-c1",
            document_id="mh-doc-4",
            document_name="d4.md",
            heading="H5",
            content="Delta PR fix.",
            score=0.76,
            rank=4,
            source_id="src-1",
        ),
    ]
    mh_selected, _ = select_diverse_sections(
        "Why was the outage triggered, and how did the PR fix the ticket?",
        mh_sections,
        limit=4,
    )
    assert {s.document_id for s in mh_selected} == {"mh-doc-1", "mh-doc-2", "mh-doc-3", "mh-doc-4"}


def test_system_bottleneck_bug_fixes_e0_routing_and_multihop_diversity() -> None:
    """Verify fixes for slash-entity pollution, Vietnamese E0/E1, Tree Routing margins, and 5-doc Multi-Hop diversity."""
    from sag_api.sag import RetrievedSection
    from sag_api.services.query_routing_service import (
        GroupRoutingSnapshot,
        NodeProfile,
        route_snapshot,
        scope_fingerprint,
    )
    from sag_api.services.retrieval_service import select_diverse_sections
    from sag_api.services.search_unit_retrieval_service import _extract_uri_slugs
    from sag_api.services.tree_query_snapshot_service import _query_signal_scores

    # 1. E0/E1: Slash words ("Support/CS", "timeouts/5xx") must NOT produce fake "/CS" or "/5xx" entities,
    # while real paths ("/v1/chat/completions"), scheme URIs, Vietnamese dates/units/labels are extracted.
    en_slash_block = _make_block(
        "Escalation",
        "Support/CS escalates timeouts/5xx on /v1/chat/completions via confluence://support/playbook/dedicated-throughput-slo-escalation.",
        0,
    )
    _, en_slash_features = extract_e0_e1(en_slash_block)
    assert "/CS" not in en_slash_features["entities"]
    assert "/5xx" not in en_slash_features["entities"]
    assert "/v1/chat/completions" in en_slash_features["entities"]
    assert "confluence://support/playbook/dedicated-throughput-slo-escalation" in en_slash_features["entities"]
    assert _extract_uri_slugs(en_slash_block.normalized_text) == {"dedicated throughput slo escalation"}

    vn_block = _make_block(
        "Quy chế",
        "Ngày 19-10-2026, theo 15/2026/NĐ-CP và Điều 5, Dự án A kiểm tra 120 câu trong 30 ngày vì phải từ chối nếu lỗi.",
        1,
    )
    _, vn_features = extract_e0_e1(vn_block)
    assert "19-10-2026" in vn_features["entities"]
    assert "15/2026/NĐ-CP" in vn_features["entities"]
    assert "Điều 5" in vn_features["entities"]
    assert "Dự án A" in vn_features["entities"]
    assert "e2_reason" not in vn_features

    # Zero-entity block emits single passage anchor fact and preserves local_confidence/importance without E2 fields
    low_conf_block = _make_block(
        "Quy định",
        "nếu tỷ lệ câu trả lời thiếu bằng chứng vượt ngưỡng thì hệ thống bắt buộc phải từ chối trả lời ngay.",
        2,
    )
    low_conf_facts, low_conf_features = extract_e0_e1(low_conf_block)
    assert len(low_conf_facts) == 1
    assert low_conf_facts[0]["kind"] == "passage"
    assert low_conf_features["local_confidence"] == pytest.approx(0.55)
    assert low_conf_features["importance"] >= 0.50
    assert "e2_reason" not in low_conf_features

    # 2. Tree Routing: _query_signal_scores strips stopwords and produces decisive margin (>= 0.18)
    branch_a_profile = {
        "sparse": [["bằng", 5.0], ["chứng", 5.0], ["cổng", 4.0], ["điểm", 4.0], ["từ", 3.0], ["chối", 3.0]],
        "entities": ["0.75", "Cổng Bằng Chứng"],
    }
    branch_b_profile = {
        "sparse": [["phiên", 4.0], ["bản", 4.0], ["lưu", 3.0], ["mã", 3.0], ["băm", 3.0]],
        "entities": ["Sổ Cái Bằng Chứng", "SHA-256"],
    }
    q_vn = "Khi điểm bằng chứng của Cổng Bằng Chứng dưới bao nhiêu thì phải từ chối?"
    sig_a = _query_signal_scores(q_vn, branch_a_profile)
    sig_b = _query_signal_scores(q_vn, branch_b_profile)
    fp = scope_fingerprint(
        {
            "project_id": "proj-1",
            "source_ids": ["src-1"],
            "document_version_ids": ["ver-1"],
            "tenant_id": "tenant-1",
            "partition_id": "part-1",
        }
    )
    common_node = dict(
        project_id="proj-1",
        source_ids=("src-1",),
        document_version_ids=("ver-1",),
        tenant_id="tenant-1",
        partition_id="part-1",
        tree_version="tree-1",
        scope_fingerprint=fp,
    )
    snap = GroupRoutingSnapshot(
        snapshot_id="snap-1",
        captured_at=datetime.now(UTC),
        project_id="proj-1",
        source_ids=("src-1",),
        document_version_ids=("ver-1",),
        tenant_id="tenant-1",
        partition_id="part-1",
        scope_fingerprint=fp,
        tree_version="tree-1",
        tree_config_version="v1",
        routing_slot="SLOT_A",
        search_epoch=1,
        manifest_status="ACTIVE",
        manifest_checksum="a" * 64,
        manifest_verified=True,
        profiles=(
            NodeProfile(
                node_id="root",
                parent_id=None,
                is_leaf=False,
                accessible_unit_count=5,
                signal_scores={"sparse": 0.6, "entity": 1.0},
                **common_node,
            ),
            NodeProfile(
                node_id="branch-a",
                parent_id="root",
                is_leaf=True,
                accessible_unit_count=3,
                signal_scores=sig_a,
                **common_node,
            ),
            NodeProfile(
                node_id="branch-b",
                parent_id="root",
                is_leaf=True,
                accessible_unit_count=2,
                signal_scores=sig_b,
                **common_node,
            ),
        ),
    )
    decision = route_snapshot(snap)
    assert decision.routed is True
    assert decision.broad_route is False
    assert decision.reason_code == "ROUTE_DESCENDED"
    assert decision.selected_nodes == ("branch-a",)

    # 3. Multi-Hop Diversity: 5 distinct qualifying docs each get 1 slot when limit=5
    mh5_sections = [
        RetrievedSection(
            chunk_id="d1-c1",
            document_id="doc-1",
            document_name="d1.md",
            heading="H1",
            content="Primary ticket outbreak.",
            score=0.80,
            rank=0,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="d1-c2",
            document_id="doc-1",
            document_name="d1.md",
            heading="H1-b",
            content="Second chunk of primary ticket.",
            score=0.79,
            rank=1,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="d2-c1",
            document_id="doc-2",
            document_name="d2.md",
            heading="H2",
            content="Second doc in chain.",
            score=0.75,
            rank=2,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="d3-c1",
            document_id="doc-3",
            document_name="d3.md",
            heading="H3",
            content="Third doc in chain.",
            score=0.70,
            rank=3,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="d4-c1",
            document_id="doc-4",
            document_name="d4.md",
            heading="H4",
            content="Fourth doc in chain.",
            score=0.65,
            rank=4,
            source_id="src-1",
        ),
        RetrievedSection(
            chunk_id="d5-c1",
            document_id="doc-5",
            document_name="d5.md",
            heading="H5",
            content="Fifth doc in chain.",
            score=0.60,
            rank=5,
            source_id="src-1",
        ),
    ]
    mh5_selected, _ = select_diverse_sections(
        "Why was the outage triggered, and how did the PR fix the ticket?",
        mh5_sections,
        limit=5,
    )
    assert [s.document_id for s in mh5_selected] == ["doc-1", "doc-2", "doc-3", "doc-4", "doc-5"]


def test_vietnamese_entity_lexer_and_multihop_detection() -> None:
    """Verify Vietnamese entity extraction, newline boundary safety, and multi-hop query feature detection."""
    from sag_api.services.query_analysis import extract_query_features

    # 1. Newline boundary safety + Vietnamese full date + compound proper nouns + single-word mid-sentence proper nouns
    vi_block = _make_block(
        "Bảo tàng Hàng hải Penobscot",
        (
            "Bảo tàng Hàng hải Penobscot\n\n"
            "Bảo tàng Hàng hải Penobscot là một bảo tàng ở Searsport, Maine, nằm ở đầu Vịnh Penobscot. "
            "Sự kiện diễn ra vào ngày 18 tháng 12 năm 2015 tại Tòa án Quân sự Nürnberg."
        ),
        0,
    )
    _, vi_features = extract_e0_e1(vi_block)
    ents = set(vi_features["entities"])
    # Must never glue across \n\n
    assert all("\n" not in e for e in ents)
    # Must extract compound and single-word proper nouns + universal numeric year without wordlists
    assert "Bảo tàng Hàng hải Penobscot" in ents
    assert "Penobscot" in ents
    assert "Searsport" in ents
    assert "Maine" in ents
    assert "Vịnh Penobscot" in ents
    assert "Tòa án Quân sự Nürnberg" in ents
    assert "Nürnberg" in ents
    assert "2015" in ents
    assert "18 tháng" not in ents
    assert "12 năm" not in ents

    # Multilingual universal anchors (Cyrillic + Japanese CJK brackets & Katakana + CJK bigram terms)
    from sag_api.services.knowledge_service import terms

    multilang_block = _make_block(
        "国際法務 > データ保護",
        "2026-04-15 に Москва で Лев Толстой が《個人情報保護法》とアルゴリズム `SHA-256` を検証した。",
        1,
    )
    _, ml_features = extract_e0_e1(multilang_block)
    ml_ents = set(ml_features["entities"])
    assert "2026-04-15" in ml_ents
    assert "Москва" in ml_ents
    assert "Лев Толстой" in ml_ents
    assert "個人情報保護法" in ml_ents
    assert "アルゴリズム" in ml_ents
    assert "SHA-256" in ml_ents
    ml_terms = terms(multilang_block.normalized_text)
    assert "個人" in ml_terms and "情報" in ml_terms and "保護" in ml_terms

    # 2. Vietnamese multi-hop query feature detection (domain-agnostic relational/comparative structure)
    mh_q1 = "Mối liên hệ giữa dịch vụ xác thực và cổng thanh toán dẫn đến lỗi gì?"
    mh_q2 = "Sự khác nhau giữa quy trình phê duyệt cấp 1 và quy trình khẩn cấp là gì, và khi nào cần áp dụng?"
    assert extract_query_features(mh_q1).multi_hop is True
    assert extract_query_features(mh_q2).multi_hop is True


@pytest.mark.asyncio
async def test_four_pillar_retrieval_and_semantic_routing_upgrade() -> None:
    """Verify semantic+temporal tree routing, word-boundary reranking, stemming alignment, and subquery interleaving."""
    from datetime import UTC, datetime

    from sag_api.sag import RetrievedSection
    from sag_api.sag.search_unit_store import SearchUnitHit, build_sparse_query_vector
    from sag_api.services.query_analysis import (
        analyze_query,
        decompose_query_clauses,
        extract_query_features,
    )
    from sag_api.services.query_routing_service import (
        GroupRoutingSnapshot,
        route_snapshot,
        scope_fingerprint,
    )
    from sag_api.services.retrieval_service import _lexical_relevance
    from sag_api.services.search_index_service import compute_sparse_bm25_vector
    from sag_api.services.search_unit_retrieval_service import (
        _interleave_hit_lists,
        _is_cjk_or_continuous_entity,
    )
    from sag_api.services.tree_query_snapshot_service import (
        _build_query_node_profiles,
        _extract_manifest_node_features,
        _normalize_unit_vector,
    )

    # Pillar 1: Semantic dense_medoid + temporal tree routing on zero-lexical-overlap paraphrase query
    manifest_json = {
        "routing_profiles": [
            {
                "partition_id": "public",
                "node_id": "root",
                "dense_medoid": [0.70, 0.70, 0.10, 0.10],
                "temporal_from": "2024-01-01T00:00:00Z",
                "temporal_to": "2026-12-31T00:00:00Z",
            },
            {
                "partition_id": "public",
                "node_id": "leaf-auth",
                "dense_medoid": [0.95, 0.25, 0.10, 0.05],
                "temporal_from": "2026-01-01T00:00:00Z",
                "temporal_to": "2026-12-31T00:00:00Z",
            },
            {
                "partition_id": "public",
                "node_id": "leaf-billing",
                "dense_medoid": [0.35, 0.90, 0.15, 0.10],
                "temporal_from": "2024-01-01T00:00:00Z",
                "temporal_to": "2024-12-31T00:00:00Z",
            },
        ]
    }
    m_features = _extract_manifest_node_features(manifest_json)
    fp = scope_fingerprint(
        {
            "project_id": "proj-sem",
            "source_ids": ["src-1"],
            "document_version_ids": ["ver-1"],
            "tenant_id": "tenant-1",
            "partition_id": "public",
        }
    )
    cached_nodes = [
        ("root", None, False, 6, [["credential", 3.0], ["invoice", 3.0]], []),
        ("leaf-auth", "root", True, 3, [["credential", 5.0], ["mfa", 4.0]], []),
        ("leaf-billing", "root", True, 3, [["invoice", 5.0], ["ledger", 4.0]], []),
    ]
    # Paraphrase query has ZERO lexical overlap with ["credential", "mfa", "invoice", "ledger"]
    q_paraphrase = "How do users verify identity at sign-in during 2026?"
    q_vec = _normalize_unit_vector([0.96, 0.22, 0.08, 0.05])
    node_profiles = _build_query_node_profiles(
        cached_nodes,
        project_id="proj-sem",
        tenant_id="tenant-1",
        tree_version="tree-1",
        source_ids=("src-1",),
        partition_id="public",
        document_version_ids=("ver-1",),
        fingerprint=fp,
        query=q_paraphrase,
        manifest_features=m_features,
        query_unit_vector=q_vec,
    )
    snap = GroupRoutingSnapshot(
        snapshot_id="snap-sem",
        captured_at=datetime.now(UTC),
        project_id="proj-sem",
        source_ids=("src-1",),
        document_version_ids=("ver-1",),
        tenant_id="tenant-1",
        partition_id="public",
        scope_fingerprint=fp,
        tree_version="tree-1",
        tree_config_version="v1",
        routing_slot="SLOT_A",
        search_epoch=1,
        manifest_status="ACTIVE",
        manifest_checksum="b" * 64,
        manifest_verified=True,
        profiles=node_profiles,
    )
    sem_decision = route_snapshot(snap)
    assert sem_decision.routed is True
    assert sem_decision.broad_route is False
    assert sem_decision.reason_code == "ROUTE_DESCENDED"
    assert sem_decision.selected_nodes == ("leaf-auth",)

    # Pillar 2: Word-boundary lexical reranking + safe secondary stemming + multilingual query analysis
    sec_false_sub = RetrievedSection(
        chunk_id="c-smart",
        document_id="d-smart",
        heading="Smart Configuration",
        content="Smart configuration manages chart partitions.",
        score=0.5,
        rank=0,
        source_id="src-1",
    )
    assert _lexical_relevance("art", sec_false_sub, analysis=analyze_query("art")) == 0.0

    # Safe secondary stemming alignment between index and query (no false truncation of string/during/status/exceed)
    for word in ("string", "during", "status", "exceed"):
        # Unstemmable words emit exactly 1 bucket (no corrupted stem bucket)
        assert len(build_sparse_query_vector(word)["indices"]) == 1
        assert build_sparse_query_vector(word)["indices"] == compute_sparse_bm25_vector(word)["indices"]
    # True inflections share stem bucket between index and query
    assert set(build_sparse_query_vector("configurations")["indices"]) & set(
        compute_sparse_bm25_vector("configuration")["indices"]
    )

    # Spatial Vietnamese prepositions ("từ ... đến ...") must NOT trigger false temporal cue, whereas structural dates do
    assert bool(extract_query_features("Khoảng cách từ Hà Nội đến Đà Nẵng là bao nhiêu?").temporal_cues) is False
    assert bool(extract_query_features("Chính sách năm 2026 quy định gì?").temporal_cues) is True
    assert bool(extract_query_features("2026年4月の監査基準は何ですか？").temporal_cues) is True

    # Multi-question interrogative decomposition & continuous-script CJK/Japanese/Thai entity detection
    clauses = decompose_query_clauses("What is the audit log retention period? What port does the metrics exporter use?")
    assert len(clauses) == 2
    assert _is_cjk_or_continuous_entity("証拠台帳") is True
    assert _is_cjk_or_continuous_entity("個人情報") is True
    assert _is_cjk_or_continuous_entity("policy") is False

    # Pillar 3: Subquery sparse hit rank-interleaving preserves top hits from every clause without duplicate votes
    main_hits = [
        SearchUnitHit(point_id="p-clause1-a", score=18.0, payload={}),
        SearchUnitHit(point_id="p-clause1-b", score=15.0, payload={}),
    ]
    sub2_hits = [
        SearchUnitHit(point_id="p-clause2-a", score=6.0, payload={}),
        SearchUnitHit(point_id="p-clause1-a", score=2.0, payload={}),
    ]
    interleaved = _interleave_hit_lists([main_hits, sub2_hits], limit=4)
    assert [h.point_id for h in interleaved] == ["p-clause1-a", "p-clause2-a", "p-clause1-b"]
    assert interleaved[1].score == pytest.approx(18.0)

