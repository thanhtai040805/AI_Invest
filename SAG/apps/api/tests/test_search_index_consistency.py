"""Missing derived vectors are an index failure, not a knowledge abstention."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from sag_api.core.errors import ServiceUnavailableError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.sag.search_unit_store import (
    SearchIndexUnavailable,
    SearchUnitQdrantStore,
    build_search_filter,
)
from sag_api.services import search_unit_retrieval_service as retrieval

SCOPE_FILTER = build_search_filter(
    project_id="project-a",
    tenant_id="tenant-a",
    partition_id="partition-a",
    document_version_ids=["version-a"],
)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 12])
async def test_exact_point_count_preserves_authorized_scope(count):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"result": {"count": count}})

    async with httpx.AsyncClient(
        base_url="https://qdrant.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        assert await SearchUnitQdrantStore(client).count(
            "search_units_project-a", search_filter=SCOPE_FILTER
        ) == count

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/collections/search_units_project-a/points/count"
    assert json.loads(requests[0].content) == {"filter": SCOPE_FILTER, "exact": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_count", [-1, True, 1.5, "12", None])
async def test_invalid_index_counts_fail_closed(invalid_count):
    async with httpx.AsyncClient(
        base_url="https://qdrant.invalid",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"result": {"count": invalid_count}})
        ),
    ) as client:
        with pytest.raises(SearchIndexUnavailable):
            await SearchUnitQdrantStore(client).count("search_units_project-a", search_filter=SCOPE_FILTER)


@pytest.mark.asyncio
async def test_point_count_failure_does_not_expose_response_or_connection_details():
    secret = "private-provider-detail"
    async with httpx.AsyncClient(
        base_url="https://qdrant.invalid",
        headers={"api-key": secret},
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text=secret)),
    ) as client:
        with pytest.raises(SearchIndexUnavailable) as failure:
            await SearchUnitQdrantStore(client).count("search_units_project-a", search_filter=SCOPE_FILTER)
    assert secret not in str(failure.value)
    assert "qdrant.invalid" not in str(failure.value)


def scoped_retrieval(monkeypatch, client):
    source = SimpleNamespace(id="source-a", name="Project A", sag_source_config_id="config-a")
    principal = VerifiedPrincipal(
        subject="test-user",
        organization_id="tenant-a",
        allowed_project_ids=frozenset({"project-a"}),
        issuer="test-issuer",
        key_id="test-key",
        token_id="test-token",
        issued_at=1,
        expires_at=2,
        tenant_id="tenant-a",
        allowed_partition_ids=frozenset({"partition-a"}),
    )
    version = retrieval._ReadyVersion(
        document_version_id="version-a",
        source_id=source.id,
        project_id="project-a",
        tenant_id="tenant-a",
        partition_id="partition-a",
        source=source,
    )

    @asynccontextmanager
    async def session():
        yield None

    monkeypatch.setattr(retrieval, "SessionLocal", session)
    monkeypatch.setattr(retrieval, "_load_current_ready_versions", AsyncMock(return_value={"version-a": version}))
    monkeypatch.setattr(retrieval, "capture_routing_decisions", AsyncMock(return_value={}))
    monkeypatch.setattr(retrieval, "_query_vector", AsyncMock(return_value=[1.0, 0.0]))
    engine = SimpleNamespace(get_search_unit_qdrant_client=AsyncMock(return_value=client))
    return engine, source, principal


@pytest.mark.asyncio
@pytest.mark.parametrize("available_points", [0, 12])
async def test_empty_query_distinguishes_missing_index_from_genuine_no_match(monkeypatch, available_points):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/points/count"):
            return httpx.Response(200, json={"result": {"count": available_points}})
        if request.url.path.endswith("/points/query/batch"):
            return httpx.Response(200, json={"result": [{"points": []}, {"points": []}]})
        raise AssertionError(f"Unexpected index operation: {request.method} {request.url.path}")

    async with httpx.AsyncClient(
        base_url="https://qdrant.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        engine, source, principal = scoped_retrieval(monkeypatch, client)
        request = retrieval._retrieve_search_unit_sections(
            engine,
            [source],
            "Dự án dùng framework nào?",
            principal=principal,
            top_k=5,
            candidate_limit=8,
            deadline=asyncio.get_running_loop().time() + 2,
        )
        if available_points == 0:
            with pytest.raises(ServiceUnavailableError):
                await request
        else:
            outcome = await request
            assert outcome.sections == []
            assert outcome.stats["ready_versions"] == 1
            assert outcome.stats["routing"]["coverage"]["reason"] == "no_retrieval_candidates"

    count_requests = [request for request in requests if request.url.path.endswith("/points/count")]
    assert len(count_requests) == 1
    assert json.loads(count_requests[0].content) == {"filter": SCOPE_FILTER, "exact": True}


@pytest.mark.asyncio
async def test_index_consistency_check_obeys_the_existing_request_deadline(monkeypatch):
    count_started = asyncio.Event()

    async def respond(request):
        if request.url.path.endswith("/points/count"):
            count_started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"result": [{"points": []}, {"points": []}]})

    async with httpx.AsyncClient(
        base_url="https://qdrant.invalid", transport=httpx.MockTransport(respond)
    ) as client:
        engine, source, principal = scoped_retrieval(monkeypatch, client)
        with pytest.raises(ServiceUnavailableError):
            await asyncio.wait_for(
                retrieval._retrieve_search_unit_sections(
                    engine,
                    [source],
                    "framework",
                    principal=principal,
                    top_k=5,
                    candidate_limit=8,
                    deadline=asyncio.get_running_loop().time() + 0.05,
                ),
                timeout=1,
            )
    assert count_started.is_set()
