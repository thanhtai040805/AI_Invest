from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeEvidence, KnowledgeUnit, Source
from sag_api.schemas.universe import UniverseKnowledgeReferenceIn, UniverseTreeOut
from sag_api.services.knowledge_service import unit_checksum
from sag_api.services.universe_knowledge_service import (
    KnowledgeUniverseSnapshot,
    _cursor_after,
    _snapshot_memberships,
    knowledge_universe_detail,
    knowledge_universe_page,
    load_knowledge_universe_snapshot,
    load_source_universe_snapshots,
    resolve_knowledge_universe_references,
)


def authorized_tree():
    return UniverseTreeOut(
        project_id="project", tree_version="tree-test", search_epoch=2,
        node_count=1, unit_count=2, source_count=1,
        nodes=[dict(id="leaf", parent_id=None, depth=0, label="Retrieval", is_leaf=True,
                    unit_count=2, source_count=1)],
    )


def published_membership():
    return {
        "routing_profiles": [
            dict(node_id="leaf", project_id="project", tenant_id="tenant", partition_id="public",
                 unit_ids=["u1", "u2"]),
            dict(node_id="private-leaf", project_id="project", tenant_id="tenant", partition_id="private",
                 unit_ids=["secret"]),
        ],
        "search_unit_assignments": [
            dict(source_id="source", document_version_id="version", partition_id="public",
                 search_unit_id="search-unit", knowledge_unit_ids=["u1", "u2"]),
            dict(source_id="private-source", document_version_id="private-version", partition_id="private",
                 search_unit_id="private-search", knowledge_unit_ids=["secret"]),
        ],
        "edges": [["u1", "u2", 0.7], ["u1", "secret", 0.95]],
    }


def test_published_projection_filters_memberships_and_both_edge_endpoints():
    members, assignments, edges = _snapshot_memberships(
        published_membership(), authorized_tree(), "tenant", {("source", "version", "public")},
    )
    assert members == {"leaf": {"u1", "u2"}}
    assert len(assignments) == 1
    assert edges == [("u1", "u2", 0.7)]


def test_published_projection_rejects_cross_tenant_profiles_and_missing_membership():
    raw = published_membership()
    raw["routing_profiles"][0]["tenant_id"] = "other-tenant"
    with pytest.raises(ServiceUnavailableError):
        _snapshot_memberships(raw, authorized_tree(), "tenant", {("source", "version", "public")})
    with pytest.raises(ServiceUnavailableError):
        _snapshot_memberships({}, authorized_tree(), "tenant", {("source", "version", "public")})


@pytest_asyncio.fixture
async def knowledge_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        now = datetime.now(UTC)
        for private in (False, True):
            prefix = "private-" if private else ""
            source_id, version_id = f"{prefix}source", f"{prefix}version"
            document_id = f"{prefix}document"
            partition = "private" if private else "public"
            session.add(Source(id=source_id, name=f"{prefix}Source", sag_source_config_id=f"{prefix}config"))
            session.add(Document(id=document_id, tenant_id="tenant", project_id="project", source_id=source_id,
                                 filename=f"{prefix}notes.md", storage_path="unused", is_active=True))
            session.add(DocumentVersion(
                id=version_id, document_id=document_id, version_no=1, file_hash="hash",
                valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1),
                search_status="SEARCH_READY", search_ready_at=now, knowledge_status="DATA_READY",
            ))
            for unit_id in (["secret"] if private else ["u1", "u2"]):
                text = f"Original evidence for {unit_id}."
                block_id = f"block-{unit_id}"
                session.add(CanonicalBlock(
                    id=block_id, document_version_id=version_id, ordinal=1 if unit_id == "u2" else 0,
                    block_type="paragraph", page_from=2, page_to=2, section_path="Retrieval",
                    source_anchor=f"anchor-{unit_id}", normalized_text=text, content_hash=f"hash-{unit_id}",
                ))
                provenance = dict(
                    tenant_id="tenant", project_id="project", source_id=source_id, document_id=document_id,
                    document_version_id=version_id, security_partition_id=partition, evidence_block_ids=[block_id],
                )
                unit = KnowledgeUnit(
                    id=unit_id, document_version_id=version_id, tenant_id="tenant", project_id="project",
                    source_id=source_id, security_partition_id=partition, ordinal=1 if unit_id == "u2" else 0,
                    text=text, content_hash=f"hash-{unit_id}", input_checksum="input", checksum="pending",
                    is_current=True, provenance_json=provenance, features_json={},
                    valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1),
                )
                session.add(unit)
                session.add(KnowledgeEvidence(
                    id=f"evidence-{unit_id}", unit_id=unit_id, tier="E0", kind="claim", extractor_version="v1",
                    confidence=0.55, payload_json=dict(quote=text, start=0, end=len(text),
                                                     evidence_block_ids=[block_id], candidate=True),
                ))
        await session.flush()
        for unit in (await session.execute(select(KnowledgeUnit))).scalars():
            unit.checksum = await unit_checksum(session, unit)
        await session.commit()
        members, assignments, edges = _snapshot_memberships(
            published_membership(), authorized_tree(), "tenant", {("source", "version", "public")},
        )
        snapshot = KnowledgeUniverseSnapshot(
            authorized_tree(), "tenant", "scope-key", {("source", "version", "public")},
            members, assignments, edges,
        )
        yield session, snapshot
    await engine.dispose()


@pytest.mark.asyncio
async def test_keyset_pagination_is_read_only_and_scope_bound(knowledge_db):
    session, snapshot = knowledge_db
    page = await knowledge_universe_page(session, snapshot, node_id="leaf", limit=1)
    assert [unit.id for unit in page.units] == ["u1"]
    assert page.total == 2 and page.next_cursor
    second = await knowledge_universe_page(session, snapshot, node_id="leaf", limit=1, cursor=page.next_cursor)
    assert [unit.id for unit in second.units] == ["u2"]
    assert second.next_cursor is None
    snapshot.scope_key = "changed-permissions"
    with pytest.raises(ConflictError):
        await knowledge_universe_page(session, snapshot, node_id="leaf", limit=1, cursor=page.next_cursor)
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_source_projection_reads_current_units_without_routing_membership(knowledge_db, monkeypatch):
    from sag_api.db.models import SourceProjectMapping
    from sag_api.services import universe_knowledge_service as service

    session, _ = knowledge_db
    session.add(SourceProjectMapping(source_id="source", organization_id="organization", project_id="project",
                                     state="CONFIRMED", mapping_version=1, confirmed_at=datetime.now(UTC),
                                     confirmed_by="test", approval_ref="test"))
    await session.commit()
    principal = SimpleNamespace(allowed_project_ids=frozenset({"project"}), tenant_id="tenant",
                                organization_id="organization", subject="reader")
    ready = SimpleNamespace(project_id="project", source_id="source", partition_id="public",
                            acl_mapping_version=1, ingestion_run_id="run", index_stage_run_id="index",
                            search_manifest_checksum="checksum")
    monkeypatch.setattr(service, "_load_current_ready_versions", AsyncMock(return_value={"version": ready}))
    # The real scope loader is independently responsible for confirmed mappings and index readiness.
    snapshots = await service.load_source_universe_snapshots(session, principal)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.tree.projection == "source" and snapshot.tree.unit_count == 2
    assert snapshot.assignments == [] and snapshot.edges == [] and snapshot.unit_ids == set()
    page = await knowledge_universe_page(session, snapshot, node_id="source:source", limit=1)
    assert page.total == 2 and page.units[0].id == "u1" and page.edges == [] and page.next_cursor
    detail = await knowledge_universe_detail(session, snapshot, "u2")
    assert detail.block_id == "block-u2"
    with pytest.raises(NotFoundError):
        await knowledge_universe_detail(session, snapshot, "secret")
    matches = await resolve_knowledge_universe_references(session, snapshot, [
        UniverseKnowledgeReferenceIn(source_id="source", block_id="block-u2"),
    ])
    assert matches[0].node_id == "source:source" and matches[0].unit_ids == ["u2"]
    related = await knowledge_universe_page(session, snapshot, node_id="source:source", limit=10, related_to="u1")
    assert related.total == 0 and related.edges == []
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_detail_is_anchored_and_cannot_read_a_private_unit(knowledge_db):
    session, snapshot = knowledge_db
    detail = await knowledge_universe_detail(session, snapshot, "u1")
    assert detail.content == detail.evidence[0].quote
    assert detail.block_id == "block-u1" and detail.evidence[0].candidate
    snapshot.members["leaf"].add("secret")
    with pytest.raises(NotFoundError):
        await knowledge_universe_detail(session, snapshot, "secret")
    evidence = await session.get(KnowledgeEvidence, "evidence-u1")
    evidence.payload_json = {**evidence.payload_json, "quote": "unanchored quote"}
    await session.flush()
    with pytest.raises(ServiceUnavailableError):
        await knowledge_universe_detail(session, snapshot, "u1")


@pytest.mark.asyncio
async def test_search_mapping_uses_real_assignments_and_canonical_block_ids(knowledge_db):
    session, snapshot = knowledge_db
    refs = [UniverseKnowledgeReferenceIn(source_id="source", search_unit_id="search-unit")]
    matches = await resolve_knowledge_universe_references(session, snapshot, refs)
    assert matches[0].unit_ids == ["u1", "u2"]
    matches = await resolve_knowledge_universe_references(session, snapshot, [
        UniverseKnowledgeReferenceIn(source_id="source", block_id="block-u2"),
    ])
    assert matches[0].unit_ids == ["u2"]
    assert await resolve_knowledge_universe_references(session, snapshot, [
        UniverseKnowledgeReferenceIn(source_id="private-source", search_unit_id="private-search"),
    ]) == []


@pytest.mark.asyncio
async def test_related_projection_omits_hidden_edge_endpoints(knowledge_db):
    session, snapshot = knowledge_db
    page = await knowledge_universe_page(session, snapshot, node_id="leaf", limit=24, related_to="u1")
    assert [unit.id for unit in page.units] == ["u2"]
    assert [(edge.source, edge.target) for edge in page.edges] == [("u1", "u2")]


def test_invalid_cursor_is_rejected():
    with pytest.raises(ValidationError):
        _cursor_after("tampered.signature", {"v": 2, "scope": "scope"})


@pytest.mark.asyncio
async def test_snapshot_reader_rejects_an_unauthorized_project_before_querying():
    session = AsyncMock()
    principal = SimpleNamespace(allowed_project_ids=frozenset({"other-project"}), tenant_id="tenant")
    with pytest.raises(NotFoundError):
        await load_knowledge_universe_snapshot(session, principal, authorized_tree())
    with pytest.raises(NotFoundError):
        await load_source_universe_snapshots(session, principal, "project")
    session.execute.assert_not_called()
    session.scalars.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("source_mode", [False, True])
async def test_knowledge_http_routes_read_data_without_publishing(knowledge_db, monkeypatch, source_mode):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from sag_api.api.v1 import universe
    from sag_api.schemas.universe import UniverseManifestOut
    from sag_api.services.universe_service import _universe_policy

    session, snapshot = knowledge_db
    if source_mode:
        snapshot.tree.projection = "source"
        snapshot.tree.tree_version = "source-test"
        snapshot.members = {"source:source": set()}
        snapshot.assignments = []
        snapshot.edges = []
    app = FastAPI()
    app.include_router(universe.router, prefix="/api/v1")

    async def db_session():
        yield session

    app.dependency_overrides[universe.get_session] = db_session
    app.dependency_overrides[universe.get_current_user] = lambda: SimpleNamespace(id="user")
    app.dependency_overrides[universe.require_project_read_principal] = lambda: SimpleNamespace()
    monkeypatch.setattr(universe, "manifest", AsyncMock(return_value=UniverseManifestOut(
        version="manifest", status="stale" if source_mode else "ready",
        trees=[] if source_mode else [snapshot.tree], policy=_universe_policy(),
    )))
    monkeypatch.setattr(universe, "load_knowledge_universe_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(universe, "load_source_universe_snapshots", AsyncMock(return_value=[snapshot]))
    publisher = AsyncMock(side_effect=AssertionError("Universe reads must not publish a routing tree"))
    monkeypatch.setattr(universe, "enqueue_project_tree_rebuild", publisher)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        overview = await client.get("/api/v1/universe/knowledge/overview")
        assert overview.status_code == 200
        assert overview.json()["trees"][0]["projection"] == ("source" if source_mode else "routing")
        if source_mode:
            assert overview.json()["status"] == "stale" and overview.json()["counts"]["trees"] == 0
        page = await client.get("/api/v1/universe/knowledge/cluster", params={
            "project_id": "project", "tree_version": snapshot.tree.tree_version,
            "node_id": "source:source" if source_mode else "leaf",
        })
        assert page.status_code == 200
        assert [unit["id"] for unit in page.json()["units"]] == ["u1", "u2"]
        detail = await client.get("/api/v1/universe/knowledge/units/u1", params={
            "project_id": "project", "tree_version": snapshot.tree.tree_version,
        })
        assert detail.status_code == 200 and detail.json()["block_id"] == "block-u1"
        resolved = await client.post("/api/v1/universe/knowledge/resolve", json={
            "references": [{"source_id": "source", "block_id": "block-u1"}],
        })
        assert resolved.status_code == 200
        assert resolved.json()["matches"][0]["unit_ids"] == ["u1"]
    publisher.assert_not_called()
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_neural_cortex_cross_document_synapses_and_e2_tier(knowledge_db):
    session, snapshot = knowledge_db
    now = datetime.now(UTC)
    session.add(Document(
        id="document-2", tenant_id="tenant", project_id="project", source_id="source",
        filename="architecture-v2.md", storage_path="unused-2", is_active=True,
    ))
    session.add(DocumentVersion(
        id="version-2", document_id="document-2", version_no=2, file_hash="hash-2",
        valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1),
        search_status="SEARCH_READY", search_ready_at=now, knowledge_status="DATA_READY",
    ))
    text_u3 = "Project Alpha requires causal constraint verification."
    session.add(CanonicalBlock(
        id="block-u3", document_version_id="version-2", ordinal=0,
        block_type="paragraph", page_from=5, page_to=5, section_path="Architecture",
        source_anchor="anchor-u3", normalized_text=text_u3, content_hash="hash-u3",
    ))
    u1 = await session.get(KnowledgeUnit, "u1")
    u1.features_json = {
        "entities": ["Project Alpha", "Retrieval"],
        "importance": 0.75,
        "local_confidence": 0.88,
        "e2_reason": None,
        "citations": [],
    }
    u1.checksum = await unit_checksum(session, u1)

    u3 = KnowledgeUnit(
        id="u3", document_version_id="version-2", tenant_id="tenant", project_id="project",
        source_id="source", security_partition_id="public", ordinal=0,
        text=text_u3, content_hash="hash-u3", input_checksum="input-u3", checksum="pending",
        is_current=True,
        provenance_json=dict(
            tenant_id="tenant", project_id="project", source_id="source", document_id="document-2",
            document_version_id="version-2", security_partition_id="public", evidence_block_ids=["block-u3"],
        ),
        features_json={
            "entities": ["project_alpha", "Verification"],
            "importance": 0.92,
            "local_confidence": 0.94,
            "e2_reason": "causal_constraint",
            "citations": [],
        },
        valid_from=now - timedelta(days=1), valid_to=now + timedelta(days=1),
    )
    session.add(u3)
    session.add(KnowledgeEvidence(
        id="evidence-u3", unit_id="u3", tier="E2", kind="relation", extractor_version="e2-llm-v1",
        confidence=0.94,
        payload_json=dict(
            quote="Project Alpha", start=0, end=len("Project Alpha"),
            evidence_block_ids=["block-u3"], candidate=False,
            subject="Project Alpha", predicate="REQUIRES", object="Verification",
        ),
    ))
    await session.flush()
    u3.checksum = await unit_checksum(session, u3)
    await session.commit()

    snapshot.scopes.add(("source", "version-2", "public"))
    snapshot.members["leaf"].add("u3")
    snapshot.edges.append(("u1", "u3", 0.84))

    page = await knowledge_universe_page(session, snapshot, node_id="leaf", limit=10)
    units_by_id = {u.id: u for u in page.units}
    assert units_by_id["u3"].neural_tier == "E2"
    assert units_by_id["u3"].e2_reason == "causal_constraint"
    assert units_by_id["u3"].cross_doc_synapse_count == 1
    assert units_by_id["u1"].cross_doc_synapse_count == 1

    cross_edges = [e for e in page.edges if e.cross_document]
    assert len(cross_edges) == 1
    assert cross_edges[0].source_document_name == "notes.md"
    assert cross_edges[0].target_document_name == "architecture-v2.md"
    assert cross_edges[0].shared_entities == ["Project Alpha"]
    assert cross_edges[0].signals["entity"] is not None and cross_edges[0].signals["entity"] > 0

    detail_u3 = await knowledge_universe_detail(session, snapshot, "u3")
    assert detail_u3.neural_tier == "E2"
    assert detail_u3.evidence[0].subject == "Project Alpha"
    assert detail_u3.evidence[0].predicate == "REQUIRES"
    assert detail_u3.evidence[0].object == "Verification"
    assert len(detail_u3.synapses) == 1
    assert detail_u3.synapses[0].cross_document is True
    assert detail_u3.synapses[0].target_unit_id == "u1"
    assert detail_u3.synapses[0].target_document_name == "notes.md"
    assert detail_u3.synapses[0].shared_entities == ["project_alpha"]
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.asyncio
async def test_revoked_document_is_rejected_immediately_without_waiting_for_cache_ttl(knowledge_db, monkeypatch):
    import hashlib

    from sag_api.api.v1 import universe
    from sag_api.db.models import (
        IngestionRun,
        ProjectSearchState,
        SearchUnit,
        SourceProjectMapping,
        StageRun,
        TreeManifest,
        TreeRoutingProfile,
    )
    from sag_api.services import universe_knowledge_service as service
    from sag_api.services.document_service import revoke_project_document
    from sag_api.services.tree_publish_service import _checksum_manifest, _scoped_profile_checksum
    from sag_api.services.tree_query_snapshot_service import _source_profile_payload

    session, _ = knowledge_db
    now = datetime.now(UTC)
    version = await session.get(DocumentVersion, "version")
    version.metadata_json = {"security_partition_id": "public"}
    index_checksum = hashlib.sha256(b"search-unit").hexdigest()
    session.add_all([
        SearchUnit(
            id="search-unit",
            document_version_id=version.id,
            block_from_id="block-u1",
            block_to_id="block-u2",
            security_partition_id="public",
            content_hash=index_checksum,
            token_count=10,
            page_from=2,
            page_to=2,
            section_path="Retrieval",
        ),
        IngestionRun(
            id="run",
            tenant_id="tenant",
            project_id="project",
            document_version_id=version.id,
            idempotency_key="run",
            payload_hash=index_checksum,
            status="COMPLETED",
            started_at=now - timedelta(minutes=1),
            completed_at=now,
        ),
        StageRun(
            id="index",
            run_id="run",
            stage="INDEX_SEARCH",
            status="SUCCESS",
            duration_ms=10.0,
            created_at=now,
            metrics_json={
                "manifest_verified": True,
                "collection_name": "search_units_project",
                "search_unit_count": 1,
                "pg_count": 1,
                "qdrant_count": 1,
                "qdrant_indexed_count": 1,
                "manifest_checksum": index_checksum,
                "qdrant_checksum": index_checksum,
            },
        ),
    ])
    session.add(
        SourceProjectMapping(
            source_id="source",
            organization_id="organization",
            project_id="project",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="test",
            approval_ref="test",
        )
    )
    raw_manifest = {
        "tenant_id": "tenant",
        "project_id": "project",
        "routing_profiles": [
            {
                "node_id": "leaf",
                "project_id": "project",
                "tenant_id": "tenant",
                "partition_id": "public",
                "unit_ids": ["u1", "u2"],
            }
        ],
        "source_routing_profiles": [
            {
                "project_id": "project",
                "source_id": "source",
                "document_version_id": "version",
                "partition_id": "public",
                "tenant_id": "tenant",
                "node_id": "leaf",
            }
        ],
        "search_unit_assignments": [
            {
                "source_id": "source",
                "document_version_id": "version",
                "partition_id": "public",
                "search_unit_id": "search-unit",
                "knowledge_unit_ids": ["u1", "u2"],
            }
        ],
        "edges": [["u1", "u2", 0.7]],
    }
    digest = _checksum_manifest(raw_manifest)
    tree_ver = f"tree-{digest[:24]}"
    session.add(
        TreeManifest(
            tree_version=tree_ver,
            project_id="project",
            config_version="v1",
            node_count=1,
            leaf_count=1,
            max_leaf_size=2,
            giant_ratio=0.0,
            routing_recall_at_k=1.0,
            escape_win_rate=0.0,
            acl_blackhole_rate=0.0,
            status="ACTIVE",
            checksum=digest,
            manifest_json=raw_manifest,
        )
    )
    session.add(
        ProjectSearchState(
            project_id="project",
            active_tree_version=tree_ver,
            active_routing_slot="SLOT_A",
            slot_a_tree_version=tree_ver,
            active_search_epoch=2,
        )
    )
    routing_profile = TreeRoutingProfile(
        **raw_manifest["source_routing_profiles"][0],
        tree_version=tree_ver,
        is_leaf=True,
        accessible_unit_count=2,
        sparse_json=[["retrieval", 1.0]],
        entities_json=[],
    )
    routing_profile.profile_checksum = _scoped_profile_checksum(tree_ver, _source_profile_payload(routing_profile))
    session.add(routing_profile)
    await session.commit()

    principal = SimpleNamespace(
        allowed_project_ids=frozenset({"project"}),
        allowed_partition_ids=frozenset({"public"}),
        tenant_id="tenant",
        organization_id="organization",
        subject="reader",
    )
    user = SimpleNamespace(id="reader")
    monkeypatch.setattr(universe, "_MANIFEST_FAST_CACHE", {})
    monkeypatch.setattr(universe, "_MANIFEST_TREE_OUTPUT_CACHE", {})
    monkeypatch.setattr(service, "_SNAPSHOT_MEMBERSHIP_CACHE", {})
    monkeypatch.setattr(service, "_VERIFIED_TREE_MANIFEST_CACHE", {})
    fast_key = ("tenant", "organization", frozenset({"project"}), frozenset({"public"}))

    # 1. Before revocation: cluster query succeeds and populates _SNAPSHOT_MEMBERSHIP_CACHE + page_cache.
    snap_before = await universe._knowledge_snapshot(session, user, principal, "project", tree_ver)
    page_before = await knowledge_universe_page(session, snap_before, node_id="leaf", limit=24)
    assert [u.id for u in page_before.units] == ["u1", "u2"]
    assert snap_before._runtime_cache is not None and len(snap_before._runtime_cache["page_cache"]) == 1
    assert fast_key in universe._MANIFEST_FAST_CACHE

    # 2. Even if a document is deactivated in DB while page_cache holds a cached page on snap_before,
    # knowledge_universe_page must re-verify unit liveness and never serve revoked preview snippets.
    doc = await session.get(Document, "document")
    doc.is_active = False
    await session.flush()
    page_after_db_deactivate = await knowledge_universe_page(session, snap_before, node_id="leaf", limit=24)
    assert page_after_db_deactivate.units == []
    assert page_after_db_deactivate.total == 0

    # Restore active state, warm caches again, then revoke via revoke_project_document API service.
    doc.is_active = True
    await session.commit()
    snap_warm = await universe._knowledge_snapshot(session, user, principal, "project", tree_ver)
    page_warm = await knowledge_universe_page(session, snap_warm, node_id="leaf", limit=24)
    assert [u.id for u in page_warm.units] == ["u1", "u2"]

    await session.rollback()
    session_factory = async_sessionmaker(session.bind, expire_on_commit=False)
    # The writer's process-local invalidation cannot reach a different reader worker.
    with monkeypatch.context() as writer_process:
        writer_process.setattr(universe, "clear_universe_caches", lambda _project_id: None, raising=False)
        async with session_factory() as writer:
            revoke_res = await revoke_project_document(
                writer,
                tenant_id="tenant",
                project_id="project",
                document_id="document",
                reason="SECURITY_REVOKE",
            )
        assert revoke_res.status == "REVOKED"

    # 3. A new reader still has the warm manifest but must observe the committed revocation.
    assert fast_key in universe._MANIFEST_FAST_CACHE
    async with session_factory() as reader:
        with pytest.raises(NotFoundError):
            await universe._knowledge_snapshot(reader, user, principal, "project", tree_ver)
