"""Dify 外部知识库兼容检索的 HTTP 契约。"""

from __future__ import annotations

import pytest


def test_dify_retrieval_defaults_to_vector_strategy():
    from sag_api.core.config import settings

    assert settings.dify_search_strategy == "vector"


@pytest.mark.asyncio
async def test_sag_search_records_when_multi_falls_back_to_vector(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.sag.dto import RetrievedSection, SearchOutcome
    from sag_api.sag.engine_manager import EngineManager

    manager = EngineManager(settings)

    async def search_raw(_source_config_id, _query, *, source, strategy, top_k):
        if strategy == "multi":
            return SearchOutcome(query="fallback", sections=[], stats={})
        return SearchOutcome(
            query="fallback",
            sections=[RetrievedSection(content="vector evidence", score=0.8)],
            stats={"upstream": "vector"},
        )

    monkeypatch.setattr(manager, "_search_raw", search_raw)

    result = await manager.search("source-1", "fallback", strategy="multi", top_k=2)

    assert result.sections[0].content == "vector evidence"
    assert result.stats["requested_strategy"] == "multi"
    assert result.stats["effective_strategy"] == "vector"
    assert result.stats["fallback_used"] is True


@pytest.mark.asyncio
async def test_sag_search_many_preserves_single_source_fallback_metadata(monkeypatch):
    from sag_api.core.config import settings
    from sag_api.sag.dto import RetrievedSection, SearchOutcome
    from sag_api.sag.engine_manager import EngineManager

    manager = EngineManager(settings)

    async def search(_source_config_id, _query, **_kwargs):
        return SearchOutcome(
            query="fallback",
            sections=[RetrievedSection(content="vector evidence", score=0.8)],
            stats={
                "requested_strategy": "multi",
                "effective_strategy": "vector",
                "fallback_used": True,
            },
        )

    monkeypatch.setattr(manager, "search", search)

    result = await manager.search_many([("source-1", None)], "fallback", strategy="multi", top_k=2)

    assert result.stats["requested_strategy"] == "multi"
    assert result.stats["effective_strategy"] == "vector"
    assert result.stats["fallback_used"] is True
