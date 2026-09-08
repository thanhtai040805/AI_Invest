"""全局搜索只公开快速/精确两档，并始终保持信源 fan-out 边界。"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete


@pytest.mark.asyncio
async def test_search_many_caps_candidates_and_concurrency(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.sag.dto import SearchOutcome
    from sag_api.sag.engine_manager import EngineManager

    monkeypatch.setattr(settings, "search_source_candidate_limit", 2)
    monkeypatch.setattr(settings, "search_source_concurrency", 1)
    manager = EngineManager(settings)
    active = 0
    peak = 0
    calls: list[str] = []

    async def fake_search(source_config_id, query, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(source_config_id)
        await asyncio.sleep(0)
        active -= 1
        return SearchOutcome(query=query, sections=[])

    monkeypatch.setattr(manager, "search", fake_search)
    outcome = await manager.search_many(
        [(f"source-{index}", None) for index in range(5)],
        "有界检索",
        strategy="multi",
    )

    assert calls == ["source-0", "source-1"]
    assert peak == 1
    assert outcome.stats == {
        "sources": 2,
        "sources_requested": 5,
        "source_limit_applied": True,
        "candidates": 0,
        "requested_strategy": "multi",
        "effective_strategy": "multi",
        "fallback_used": False,
    }


@pytest.mark.asyncio
async def test_vector_search_many_uses_one_cross_source_embedding(monkeypatch):
    from zleap.sag.core.storage import client as storage_client
    from zleap.sag.core.storage.repositories.source_chunk_repository import (
        SourceChunkRepository,
    )
    from zleap.sag.modules.load.processor import DocumentProcessor

    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager

    manager = EngineManager(settings)
    embedding_queries: list[str] = []
    repository_calls: list[tuple[int, list[str]]] = []

    async def runtime_ready(_sources):
        return None

    async def generate_embedding(_processor, query):
        embedding_queries.append(query)
        return [0.1, 0.2]

    async def search_chunks(
        _repository,
        *,
        query_vector,
        k,
        source_config_ids,
        **_kwargs,
    ):
        assert query_vector == [0.1, 0.2]
        repository_calls.append((k, source_config_ids))
        return [
            {
                "chunk_id": "chunk-2",
                "source_id": "document-2",
                "source_config_id": "source-2",
                "heading": "跨源命中",
                "content": "只生成一次查询向量。",
                "rank": 3,
                "_score": 0.88,
            }
        ]

    monkeypatch.setattr(manager, "_ensure_read_runtime", runtime_ready)
    monkeypatch.setattr(DocumentProcessor, "generate_embedding", generate_embedding)
    monkeypatch.setattr(SourceChunkRepository, "search_similar_by_content", search_chunks)
    monkeypatch.setattr(storage_client, "get_es_client", lambda: object())

    outcome = await manager.search_many(
        [("source-1", None), ("source-2", None)],
        "跨源查询",
        strategy="vector",
        top_k=9,
    )

    assert embedding_queries == ["跨源查询"]
    assert repository_calls == [(9, ["source-1", "source-2"])]
    assert outcome.sections[0].chunk_id == "chunk-2"
    assert outcome.stats["chunk_recall"] == "batch-vector"


@pytest.mark.asyncio
async def test_batch_vector_timeout_does_not_pay_legacy_timeout_again(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager

    manager = EngineManager(settings)

    async def timed_out(*_args, **_kwargs):
        raise TimeoutError

    async def legacy_search(*_args, **_kwargs):  # pragma: no cover - regression guard
        raise AssertionError("timed-out batch recall must not enter legacy fan-out")

    monkeypatch.setattr(manager, "_search_chunk_vectors", timed_out)
    monkeypatch.setattr(manager, "search", legacy_search)

    outcome = await manager.search_many(
        [("source-1", None)],
        "超时仍返回",
        strategy="vector",
        top_k=8,
    )

    assert outcome.sections == []
    assert outcome.stats["chunk_recall"] == "batch-vector-timeout"


@pytest.mark.asyncio
async def test_single_source_timeout_includes_lock_queue(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager

    monkeypatch.setattr(settings, "search_source_timeout", 1.0)
    manager = EngineManager(settings)

    @asynccontextmanager
    async def blocked_use(*_args, **_kwargs):
        await asyncio.Event().wait()
        yield  # pragma: no cover - timeout must happen before acquisition

    monkeypatch.setattr(manager, "use", blocked_use)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await manager._search_raw(
            "source-queued",
            "排队超时",
            source=None,
            strategy="vector",
            top_k=5,
        )

    assert time.monotonic() - started < 1.5
