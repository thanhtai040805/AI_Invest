from unittest.mock import AsyncMock

import pytest

from sag_api.core.config import settings
from sag_api.sag import RetrievedSection, SearchOutcome
from sag_api.schemas.search import GlobalSearchRequest
from sag_api.services import search_unit_retrieval_service as retrieval
from sag_api.services.retrieval_service import select_diverse_sections


def sections(count):
    return [
        RetrievedSection(
            chunk_id=f"chunk-{i}",
            document_id=f"doc-{i}",
            heading=f"Policy {i}",
            content=f"Policy evidence item {i} with unique facts identifier{i}",
            score=0.95,
            rank=i,
            source_id="source",
            source_config_id="config",
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,requested,expected,expected_auto,expected_first_wave",
    [
        ("policies", None, 5, False, 32),
        ("policies", 5, 5, False, 32),
        ("policies", 3, 3, False, 32),
        ("policies", 70, 50, False, 200),
        ("liệt kê 20 file liên quan đến chính sách", None, 20, True, 80),
    ],
)
async def test_retrieval_resolves_cardinality_from_plan_and_api_override(
    monkeypatch, query, requested, expected, expected_auto, expected_first_wave
):
    monkeypatch.setattr(settings, "search_unit_candidate_limit", 256)
    monkeypatch.setattr(settings, "search_top_k", 8)
    calls = []

    async def fetch(*args, candidate_limit, top_k, **kwargs):
        calls.append(candidate_limit)
        pool = sections(min(candidate_limit, 80))
        return SearchOutcome(
            query=query,
            sections=pool[:top_k],
            stats={
                "candidate_pool_saturated": candidate_limit <= 80,
                "coverage": {"anchor_coverage": "complete"},
                "stage_latencies_ms": {"candidate_retrieval": 1.0},
            },
        )

    monkeypatch.setattr(retrieval, "_retrieve_search_unit_sections", fetch)
    result = await retrieval.retrieve_search_unit_sections(None, [], query, principal=None, top_k=requested)
    assert len(result.sections) == expected
    assert result.stats["automatic_top_k"] is expected_auto
    assert result.stats["requested_top_k"] == expected
    assert calls == [expected_first_wave]


@pytest.mark.asyncio
async def test_budget_is_reported_without_silently_inventing_complete_coverage(monkeypatch):
    monkeypatch.setattr(settings, "search_unit_candidate_limit", 16)
    fetch = AsyncMock(
        return_value=SearchOutcome(
            query="q",
            sections=sections(2),
            stats={
                "candidate_pool_saturated": True,
                "coverage": {"anchor_coverage": "missing_required_anchor"},
            },
        )
    )
    monkeypatch.setattr(retrieval, "_retrieve_search_unit_sections", fetch)
    result = await retrieval.retrieve_search_unit_sections(None, [], "q", principal=None)
    assert result.stats["budget_exhausted"] is True
    assert result.stats["retrieval_stop_reason"] == "candidate_budget"
    assert result.stats["coverage"]["anchor_coverage"] == "missing_required_anchor"


@pytest.mark.asyncio
async def test_cross_scope_hydration_truncation_is_not_reported_as_exhausted_corpus(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "_retrieve_search_unit_sections",
        AsyncMock(
            return_value=SearchOutcome(
                query="q",
                sections=sections(2),
                stats={"candidate_pool_saturated": False, "candidate_units_truncated": 5},
            )
        ),
    )
    result = await retrieval.retrieve_search_unit_sections(None, [], "q", principal=None)
    assert result.stats["budget_exhausted"] is True
    assert result.stats["retrieval_stop_reason"] == "candidate_budget"


@pytest.mark.asyncio
async def test_load_more_is_not_offered_after_five_results(monkeypatch):
    monkeypatch.setattr(settings, "search_unit_candidate_limit", 32)
    monkeypatch.setattr(
        retrieval,
        "_retrieve_search_unit_sections",
        AsyncMock(
            return_value=SearchOutcome(
                query="q",
                sections=sections(5),
                stats={"candidate_pool_saturated": False, "relevant": 8},
            )
        ),
    )
    result = await retrieval.retrieve_search_unit_sections(None, [], "q", principal=None, top_k=5)
    assert result.stats["has_more"] is False


@pytest.mark.asyncio
async def test_legacy_lexical_recall_does_not_silently_take_only_two_matches():
    from types import SimpleNamespace

    from sag_api.services.query_analysis import analyze_query
    from sag_api.services.retrieval_service import _lexical_sections

    engine = SimpleNamespace(
        grep_chunks=AsyncMock(
            return_value=[
                {"chunk_id": f"c-{i}", "heading": "policy", "snippet": f"policy evidence {i}"} for i in range(8)
            ]
        )
    )
    rows, _ = await _lexical_sections(
        engine,
        [SimpleNamespace(sag_source_config_id="source")],
        analysis=analyze_query("policy"),
        candidate_limit=128,
    )
    assert len(rows) >= 8
    assert all(call.kwargs["limit"] == 128 for call in engine.grep_chunks.await_args_list)


def test_mmr_auto_can_cover_more_than_five_documents_and_filter_weaker_tail():
    candidates = sections(9)
    candidates[-1] = candidates[-1].model_copy(update={"score": 0.05})
    selected, _ = select_diverse_sections("Compare all policy relationships", candidates, limit=None)
    assert len({s.document_id for s in selected}) == 8


def test_schema_accepts_automatic_and_large_explicit_k():
    assert GlobalSearchRequest(query="q").top_k is None
    assert GlobalSearchRequest(query="q", top_k=128).top_k == 128
    with pytest.raises(ValueError):
        GlobalSearchRequest(query="q", top_k=0)


@pytest.mark.asyncio
async def test_sibling_expansion_uses_one_budget_across_documents(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    versions = {f"v-{i}": SimpleNamespace(partition_id="partition") for i in range(3)}
    seeds = [
        section.model_copy(update={"document_version_id": version})
        for section, version in zip(sections(3), versions, strict=True)
    ]
    index = SimpleNamespace(
        hydrated_su_by_version={version: [(section, 0, 1) for section in sections(8)] for version in versions}
    )
    context = MagicMock()
    context.__aenter__ = AsyncMock()
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(retrieval, "SessionLocal", lambda: context)
    monkeypatch.setattr(retrieval, "_get_or_build_scope_knowledge_index", AsyncMock(return_value=index))
    monkeypatch.setattr(retrieval, "_ensure_versions_hydrated", AsyncMock())
    expanded = await retrieval._hydrate_sibling_sections_for_top_versions(
        sections=seeds,
        current_versions=versions,
        sources=[],
        limit=4,
    )
    assert len(expanded) == 4


@pytest.mark.asyncio
async def test_precision_pruned_diversity_selection_stops_after_one_wave(monkeypatch):
    monkeypatch.setattr(settings, "search_unit_candidate_limit", 256)
    monkeypatch.setattr(settings, "search_top_k", 8)
    calls = []

    async def fetch(*args, candidate_limit, top_k, **kwargs):
        calls.append(candidate_limit)
        return SearchOutcome(
            query="multipart upload limits",
            sections=sections(3),
            stats={
                "candidate_pool_saturated": True,
                "coverage": {
                    "candidate_count": 37,
                    "deduplicated_count": 0,
                    "anchor_coverage": "not_required",
                },
                "stage_latencies_ms": {"candidate_retrieval": 1.0},
            },
        )

    monkeypatch.setattr(retrieval, "_retrieve_search_unit_sections", fetch)
    result = await retrieval.retrieve_search_unit_sections(
        None, [], "multipart upload limits", principal=None, top_k=5
    )
    assert len(result.sections) == 3
    assert result.stats["adaptive_waves"] == 1
    assert result.stats["retrieval_stop_reason"] == "requested_top_k_reached"
    assert result.stats["budget_exhausted"] is False
    assert calls == [32]

