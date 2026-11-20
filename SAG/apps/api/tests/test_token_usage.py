"""Unit and integration tests for SAG token metering, per-file rollup, and API reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.core.token_usage import (
    build_project_token_usage_report,
    get_runtime_token_usage,
    record_embedding_usage,
    record_llm_usage,
    reset_runtime_token_usage,
    sync_document_token_usage,
    token_usage_scope,
    wrap_embedding_client_with_cache_and_metering,
)
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeJob, SearchUnit, Source
from sag_api.enums import DocumentStatus
from sag_api.generation.llm import LLMClient
from sag_api.services import search_index_service


@pytest.fixture
async def token_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_embedding_wrapper_captures_provider_usage_and_cache_hits() -> None:
    reset_runtime_token_usage()
    search_index_service._SHARED_TEXT_EMBEDDING_CACHE.clear()

    class FakeRawEmbeddingClient:
        model = "Qwen/Qwen3-Embedding-0.6B"

        def __init__(self) -> None:
            self.calls = 0

        async def _create_embeddings(self, input_data, *, label: str):
            self.calls += 1
            items = [input_data] if isinstance(input_data, str) else list(input_data)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.25, 0.75]) for _ in items],
                usage=SimpleNamespace(prompt_tokens=11 * len(items), total_tokens=11 * len(items)),
            )

        async def generate(self, text: str) -> list[float]:
            resp = await self._create_embeddings(text, label="single")
            return list(resp.data[0].embedding)

        async def batch_generate(self, texts: list[str]) -> list[list[float]]:
            resp = await self._create_embeddings(texts, label="batch")
            return [list(item.embedding) for item in resp.data]

    emb = FakeRawEmbeddingClient()
    wrap_embedding_client_with_cache_and_metering(emb, default_model="Qwen/Qwen3-Embedding-0.6B")

    with token_usage_scope(component="search_index_embedding", project_id="proj-tok") as ledger:
        vecs1 = await emb.batch_generate(["alpha chunk", "beta chunk"])
        vecs2 = await emb.batch_generate(["alpha chunk", "gamma chunk"])

    assert len(vecs1) == 2 and len(vecs2) == 2
    assert emb.calls == 2
    summary = ledger.to_summary()
    assert summary["totals"]["embedding_input_tokens"] == 33
    assert summary["totals"]["total_tokens"] == 33
    assert summary["totals"]["embedding_calls"] == 2
    assert summary["totals"]["cache_hits"] == 1
    assert summary["totals"]["estimated_calls"] == 0
    assert summary["by_component"]["search_index_embedding"]["embedding_input_tokens"] == 33
    assert summary["by_model"]["Qwen/Qwen3-Embedding-0.6B"]["total_tokens"] == 33
    assert summary["cost_usd"] == pytest.approx(33 * 0.01 / 1_000_000)


@pytest.mark.asyncio
async def test_llm_stream_complete_captures_final_usage_chunk() -> None:
    reset_runtime_token_usage()
    cfg = Settings(
        llm_provider="openai",
        llm_model="Qwen/Qwen3-8B",
        llm_base_url="https://api.siliconflow.com/v1",
        llm_api_key="test-key",
    )
    client = LLMClient(cfg)
    captured_kwargs: dict = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)

        async def _gen():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello "), finish_reason=None)],
                usage=None,
            )
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="world [1]."), finish_reason="stop")],
                usage=None,
            )
            # Final usage-only chunk with empty choices, matching OpenAI / SiliconFlow stream_options
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=420,
                    completion_tokens=18,
                    total_tokens=438,
                    prompt_tokens_details=SimpleNamespace(cached_tokens=20),
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
                ),
            )

        return _gen()

    with patch("sag_api.generation.llm._litellm_completion", side_effect=fake_acompletion):
        with token_usage_scope(component="answer_generation", project_id="proj-stream") as ledger:
            out = "".join([delta async for delta in client.stream_complete([{"role": "user", "content": "hi"}])])

    assert out == "Hello world [1]."
    assert captured_kwargs.get("stream_options") == {"include_usage": True}
    summary = ledger.to_summary()
    assert summary["by_token_type"]["llm_input_tokens"] == 420
    assert summary["by_token_type"]["llm_cached_input_tokens"] == 20
    assert summary["by_token_type"]["llm_uncached_input_tokens"] == 400
    assert summary["by_token_type"]["llm_output_tokens"] == 18
    assert summary["by_token_type"]["total_tokens"] == 438
    assert summary["by_component"]["answer_generation"]["llm_calls"] == 1
    assert summary["cost_usd"] == pytest.approx(438 * 0.06 / 1_000_000)


@pytest.mark.asyncio
async def test_per_file_rollup_and_project_report_avoid_double_counting(token_db) -> None:
    reset_runtime_token_usage()
    now = datetime.now(UTC)

    async with token_db() as session:
        session.add(Source(id="src-1", name="Bench Source", sag_source_config_id="scid-1"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="org-1",
                project_id="proj-1",
                source_id="src-1",
                filename="adr-001.md",
                logical_source_id="corpus/adr-001.md",
                storage_path="unused",
                size_bytes=2048,
                status=DocumentStatus.READY,
                is_active=True,
            )
        )
        with token_usage_scope(component="search_index_embedding", project_id="proj-1") as idx_ledger:
            record_embedding_usage(
                model="Qwen/Qwen3-Embedding-0.6B",
                usage={"embedding_input_tokens": 500, "total_tokens": 500, "usage_source": "provider"},
            )
        with token_usage_scope(component="knowledge_foundation_embedding", project_id="proj-1") as foundation_ledger:
            record_embedding_usage(
                model="Qwen/Qwen3-Embedding-0.6B",
                usage={"embedding_input_tokens": 250, "total_tokens": 250, "usage_source": "provider"},
            )
        session.add(
            DocumentVersion(
                id="ver-1",
                document_id="doc-1",
                version_no=1,
                file_hash="abc123",
                search_status="SEARCH_READY",
                knowledge_status="DATA_READY",
                valid_from=now - timedelta(hours=1),
                valid_to=now + timedelta(days=30),
                metadata_json={"search_index_token_usage": idx_ledger.to_summary()},
            )
        )
        session.add(
            CanonicalBlock(
                id="blk-1",
                document_version_id="ver-1",
                ordinal=0,
                block_type="paragraph",
                page_from=1,
                page_to=1,
                section_path="Overview",
                normalized_text="Sample search unit",
                content_hash="hash-blk-1",
            )
        )
        session.add(
            SearchUnit(
                id="su-1",
                document_version_id="ver-1",
                block_from_id="blk-1",
                block_to_id="blk-1",
                security_partition_id="proj-1",
                content_hash="hash-su-1",
                token_count=10,
                page_from=1,
                page_to=1,
                section_path="Overview",
            )
        )
        session.add(
            KnowledgeJob(
                id="kj-foundation-1",
                tenant_id="org-1",
                document_version_id="ver-1",
                kind="FOUNDATION",
                input_checksum="in-chk-1",
                status="SUCCEEDED",
                attempts=1,
                usage_json=foundation_ledger.to_summary(),
            )
        )
        await session.flush()
        doc_summary = await sync_document_token_usage(session, "ver-1")
        await session.commit()

        assert doc_summary["totals"]["total_tokens"] == 750
        doc_row = await session.get(Document, "doc-1")
        assert doc_row is not None
        assert doc_row.token_usage == 750
        assert doc_row.token_usage_breakdown["by_token_type"]["embedding_input_tokens"] == 750
        assert doc_row.token_usage_breakdown["by_token_type"]["llm_input_tokens"] == 0
        assert doc_row.token_usage_breakdown["by_token_type"]["llm_output_tokens"] == 0

        # Simulate one query + answer generation in runtime registry for proj-1
        with token_usage_scope(component="search_request", project_id="proj-1"):
            with token_usage_scope(component="query_embedding", project_id="proj-1"):
                record_embedding_usage(
                    model="Qwen/Qwen3-Embedding-0.6B",
                    usage={"embedding_input_tokens": 40, "total_tokens": 40, "usage_source": "provider"},
                )
            with token_usage_scope(component="answer_generation", project_id="proj-1"):
                record_llm_usage(
                    model="Qwen/Qwen3-8B",
                    usage={"llm_input_tokens": 1100, "llm_output_tokens": 120, "total_tokens": 1220, "usage_source": "provider"},
                )

        report = await build_project_token_usage_report(session, tenant_id="org-1", project_id="proj-1")

    assert report["file_count"] == 1
    assert report["files"][0]["logical_source_id"] == "corpus/adr-001.md"
    assert report["files"][0]["totals"]["total_tokens"] == 750
    assert report["ingestion_and_knowledge"]["totals"]["total_tokens"] == 750
    assert report["query_and_generation"]["totals"]["total_tokens"] == 1260
    assert report["grand_total"]["totals"]["total_tokens"] == 2010
    assert report["grand_total"]["totals"]["embedding_input_tokens"] == 790
    assert report["grand_total"]["totals"]["llm_input_tokens"] == 1100
    assert report["grand_total"]["totals"]["llm_output_tokens"] == 120
    assert get_runtime_token_usage("proj-1")["totals"]["total_tokens"] == 2010
