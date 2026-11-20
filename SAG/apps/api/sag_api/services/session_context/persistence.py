"""Durable multi-view persistence, cold-start hydration, lazy backfill, and Qdrant sync (Chặng 3).

Bridges in-memory `IncrementalSessionIndex` with PostgreSQL (`session_units`, `session_unit_messages`,
`session_decision_ledger`, `session_graph_edges`) and Qdrant (`session_units_{project_id}`), while
enforcing:
- Fail-closed Continuum tenant/project/source ACL boundaries;
- Data minimization (zero raw conversation text in Qdrant payloads; character spans in `sentences_json`);
- Zero HTTP re-embedding on cold-start worker restarts via Qdrant vector hydration;
- Trigger-compliant O(Delta) incremental append and atomic reconciliation on delete/revocation/drift.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ForbiddenError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.core.sanitizer import sanitize_error_message
from sag_api.db.models.agent import Message
from sag_api.db.models.session_context import (
    SESSION_OPEN_ENDED_VALID_TO,
    SessionDecisionLedger,
    SessionGraphEdge,
    SessionUnit,
    SessionUnitMessage,
    generate_session_decision_id,
    generate_session_graph_edge_id,
    generate_session_unit_id,
    generate_session_unit_point_id,
)
from sag_api.db.models.source_project_mapping import SourceProjectMapping
from sag_api.enums import MessageRole, MessageStatus
from sag_api.services.search_index_service import (
    compute_sparse_bm25_vector,
    get_cached_text_embedding,
    put_cached_text_embedding,
)
from sag_api.services.session_context.extractor import (
    classify_epistemic_state,
    extract_content_terms,
    normalize_session_text,
    split_sentences_with_spans,
)
from sag_api.services.session_context.models import (
    DecisionLedgerEntry,
    EntityMention,
    EpisodeSegment,
    IndexedSentence,
    IndexedTurn,
)
from sag_api.services.session_context.substrate import (
    FALLBACK_VECTOR_DIM,
    SESSION_INDEX_CACHE,
    SOFT_ALIAS_WEIGHT,
    IncrementalSessionIndex,
    compute_embedding_signature,
    declared_embedder_dim,
    deterministic_ngram_vector,
    embed_texts_with_provenance,
    l2_normalize,
    sparse_dict_from_bm25,
)

log = get_logger("sag.session_context.persistence")

SESSION_QDRANT_PAYLOAD_INDEX_FIELDS: tuple[tuple[str, str], ...] = (
    ("tenant_id", "keyword"),
    ("project_id", "keyword"),
    ("thread_id", "keyword"),
    ("session_unit_id", "keyword"),
    ("unit_kind", "keyword"),
    ("episode_id", "keyword"),
    ("epistemic_status", "keyword"),
    ("topic_slot_key", "keyword"),
    ("security_partition_ids", "keyword"),
    ("cited_source_ids", "keyword"),
    ("entity_keys", "keyword"),
    ("embedding_model_signature", "keyword"),
    ("ordinal", "integer"),
    ("valid_from_ts", "integer"),
    ("valid_to_ts", "integer"),
)

# Process-local cache of verified Qdrant collections: (transport_tag, base_url, collection_name) -> dim
_VERIFIED_SESSION_COLLECTIONS: dict[tuple[str, str, str], int] = {}

# Optional test hook for deterministic ASGI/mock Qdrant client injection without live sockets
_CUSTOM_QDRANT_CLIENT_FACTORY: Callable[[], httpx.AsyncClient | None] | None = None


@dataclass(frozen=True, slots=True)
class SessionPersistenceScope:
    """Verified multi-tenant & project persistence scope for a conversation thread."""

    tenant_id: str
    project_id: str
    thread_id: str
    owner_key: str
    security_partition_ids: tuple[str, ...]


@dataclass(slots=True)
class PersistenceSyncStats:
    """Telemetry counters returned from cold-start hydration or O(Delta) sync."""

    status: str = "memory_only"
    units_written: int = 0
    qdrant_points_upserted: int = 0
    hydrated_turns: int = 0


def set_custom_session_qdrant_client_factory(
    factory: Callable[[], httpx.AsyncClient | None] | None,
) -> None:
    """Register or clear an injectable Qdrant AsyncClient factory (used by unit/integration tests)."""
    global _CUSTOM_QDRANT_CLIENT_FACTORY
    _CUSTOM_QDRANT_CLIENT_FACTORY = factory
    _VERIFIED_SESSION_COLLECTIONS.clear()


def clear_verified_session_collections_cache() -> None:
    """Clear process-cached Qdrant collection dimension metadata."""
    _VERIFIED_SESSION_COLLECTIONS.clear()


def session_qdrant_collection_name(project_id: str) -> str:
    """Return canonical per-project Qdrant collection name for session units."""
    safe_proj = re.sub(r"[^A-Za-z0-9_\-]", "_", str(project_id).strip())
    return f"session_units_{safe_proj}"


def _ts_to_utc(ts: float | None) -> datetime:
    if ts is None or ts <= 0:
        return datetime(1970, 1, 1, tzinfo=UTC)
    return datetime.fromtimestamp(float(ts), tz=UTC)


def _is_open_ended(dt: datetime | None) -> bool:
    if dt is None:
        return True
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.year >= 9999


def _session_dialect_name(session: AsyncSession) -> str:
    try:
        bind = session.get_bind()
        return str(bind.dialect.name or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _is_missing_session_tables_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "session_units" in msg and (
        "no such table" in msg or "does not exist" in msg or "undefinedtable" in msg
    )


def _open_qdrant_client_context(
    session: AsyncSession,
    explicit_client: httpx.AsyncClient | None = None,
) -> tuple[httpx.AsyncClient | None, bool]:
    """Resolve `(client, should_close)` without polluting live localhost Qdrant during SQLite tests."""
    if explicit_client is not None:
        return explicit_client, False
    if not settings.session_context_qdrant_sync_enabled:
        return None, False
    if _CUSTOM_QDRANT_CLIENT_FACTORY is not None:
        client = _CUSTOM_QDRANT_CLIENT_FACTORY()
        return client, False
    if settings.sag_vector_provider != "qdrant":
        return None, False
    if _session_dialect_name(session) == "sqlite":
        return None, False
    qdrant_url = (settings.sag_qdrant_url or "").strip()
    if not qdrant_url:
        return None, False
    headers: dict[str, str] = {}
    if settings.sag_qdrant_api_key:
        headers["api-key"] = settings.sag_qdrant_api_key
    return httpx.AsyncClient(base_url=qdrant_url, headers=headers, timeout=4.0), True


async def _lookup_confirmed_source_projects(
    session: AsyncSession,
    *,
    organization_id: str,
    source_ids: Collection[str],
    allowed_projects: Collection[str] | None = None,
) -> dict[str, str]:
    """Resolve `{source_id: project_id}` for `CONFIRMED` `SourceProjectMapping` rows."""
    clean_sids = sorted({str(s).strip() for s in source_ids if str(s).strip()})
    if not clean_sids or not organization_id:
        return {}
    stmt = select(SourceProjectMapping.source_id, SourceProjectMapping.project_id).where(
        SourceProjectMapping.state == "CONFIRMED",
        SourceProjectMapping.organization_id == organization_id,
        SourceProjectMapping.source_id.in_(clean_sids),
    )
    if allowed_projects is not None:
        clean_projects = sorted({str(p).strip() for p in allowed_projects if str(p).strip()})
        if not clean_projects:
            return {}
        stmt = stmt.where(SourceProjectMapping.project_id.in_(clean_projects))
    rows = (await session.execute(stmt)).all()
    return {str(sid): str(pid) for sid, pid in rows}


async def _collect_thread_message_source_ids(
    session: AsyncSession,
    *,
    thread_id: str,
) -> set[str]:
    """Collect `source_id` values referenced by active `ASSISTANT` messages across `thread_id` lineage."""
    from sag_api.services.agent_domain import (
        _message_source_ids,
        _segment_sql_filters,
        resolve_thread_lineage_segments,
    )

    segments = await resolve_thread_lineage_segments(session, thread_id)
    assistant_rows: list[Message] = []
    if segments:
        for seg in segments:
            seg_filters = _segment_sql_filters(seg)
            seg_filters.extend(
                [
                    Message.role == MessageRole.ASSISTANT,
                    Message.status == MessageStatus.OK,
                ]
            )
            seg_rows = (await session.execute(select(Message).where(*seg_filters))).scalars().all()
            assistant_rows.extend(seg_rows)
    else:
        assistant_rows = list(
            (
                await session.execute(
                    select(Message).where(
                        Message.thread_id == thread_id,
                        Message.role == MessageRole.ASSISTANT,
                        Message.status == MessageStatus.OK,
                    )
                )
            )
            .scalars()
            .all()
        )
    collected: set[str] = set()
    for msg in assistant_rows:
        sids = _message_source_ids(msg)
        if sids:
            collected.update(str(s).strip() for s in sids if str(s).strip())
    return collected


async def resolve_persistence_scope(
    session: AsyncSession,
    *,
    thread_id: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    requested_source_ids: Collection[str] | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
    caller_had_pending_state: bool = False,
) -> SessionPersistenceScope | None:
    """Resolve and verify unique `(tenant_id, project_id)` fail-closed via `SourceProjectMapping`.

    Multi-project principals (`len(allowed_project_ids) > 1`) NEVER pick `allowed_projects[0]`
    arbitrarily: persistence is activated ONLY when `SourceProjectMapping` (`state='CONFIRMED'`)
    over thread citations / `requested_source_ids` resolves to a single unambiguous `project_id`.
    """
    if not settings.session_context_persistence_enabled or not thread_id:
        return None
    tenant_id = str(
        getattr(principal, "tenant_id", "") or getattr(principal, "organization_id", "") or ""
    ).strip()
    org_id = str(getattr(principal, "organization_id", "") or tenant_id).strip()
    allowed_projects = sorted(
        str(p).strip()
        for p in (getattr(principal, "allowed_project_ids", ()) or ())
        if str(p).strip()
    )
    allowed_partitions = tuple(
        sorted(
            str(p).strip()
            for p in (getattr(principal, "allowed_partition_ids", ()) or ())
            if str(p).strip()
        )
    )
    if not tenant_id or not org_id or not allowed_projects:
        return None

    rows = (
        await session.execute(
            select(SessionUnit.tenant_id, SessionUnit.project_id)
            .where(SessionUnit.thread_id == thread_id)
            .distinct()
        )
    ).all()
    existing_project_id: str | None = None
    if rows:
        distinct_scopes = {(str(r[0]), str(r[1])) for r in rows}
        for ex_tenant, ex_proj in distinct_scopes:
            if ex_tenant != tenant_id or ex_proj not in allowed_projects:
                log.warning(
                    "Blocked cross-scope session_units access thread=%s principal_tenant=%s",
                    thread_id,
                    tenant_id,
                )
                raise ForbiddenError("Session context scope mismatch for thread")
        if len(distinct_scopes) > 1:
            raise ForbiddenError("Ambiguous persisted project scopes for thread")
        existing_project_id = next(iter(distinct_scopes))[1]

    inherited_project_id: str | None = None
    if existing_project_id is None:
        from sag_api.services.agent_domain import resolve_thread_lineage_segments

        lineage_segments = await resolve_thread_lineage_segments(session, thread_id)
        if len(lineage_segments) > 1:
            anc_tids = [s.thread_id for s in lineage_segments[:-1]]
            anc_rows = (
                await session.execute(
                    select(SessionUnit.tenant_id, SessionUnit.project_id)
                    .where(SessionUnit.thread_id.in_(anc_tids))
                    .distinct()
                )
            ).all()
            if anc_rows:
                anc_scopes = {(str(r[0]), str(r[1])) for r in anc_rows}
                if any(
                    ex_tenant != tenant_id or ex_proj not in allowed_projects
                    for ex_tenant, ex_proj in anc_scopes
                ):
                    return None
                if len(anc_scopes) > 1:
                    return None
                inherited_project_id = next(iter(anc_scopes))[1]

    raw_thread_sids = await _collect_thread_message_source_ids(session, thread_id=thread_id)
    org_source_projects = await _lookup_confirmed_source_projects(
        session,
        organization_id=org_id,
        source_ids=raw_thread_sids,
    )
    thread_confirmed_projects = set(org_source_projects.values())

    clean_req_sids = sorted(
        {str(s).strip() for s in (requested_source_ids or ()) if str(s).strip()}
    )
    req_source_projects = (
        await _lookup_confirmed_source_projects(
            session,
            organization_id=org_id,
            source_ids=clean_req_sids,
            allowed_projects=allowed_projects,
        )
        if clean_req_sids
        else {}
    )
    req_projects = set(req_source_projects.values())

    # Never bind or persist a thread that cites active CONFIRMED sources outside `allowed_projects`
    if not thread_confirmed_projects.issubset(set(allowed_projects)):
        return None

    # Thread spans >= 2 projects -> purge any partial single-project rows and stay memory-only
    if len(thread_confirmed_projects) > 1:
        if existing_project_id is not None:
            await purge_thread_session_rows(session, thread_id=thread_id)
            if not caller_had_pending_state:
                await session.commit()
            await purge_thread_qdrant_points_for_projects(
                session,
                thread_id=thread_id,
                project_ids=[existing_project_id],
                qdrant_client=qdrant_client,
            )
        return None

    if existing_project_id is not None:
        if thread_confirmed_projects and thread_confirmed_projects != {existing_project_id}:
            await purge_thread_session_rows(session, thread_id=thread_id)
            if not caller_had_pending_state:
                await session.commit()
            await purge_thread_qdrant_points_for_projects(
                session,
                thread_id=thread_id,
                project_ids=[existing_project_id],
                qdrant_client=qdrant_client,
            )
            return None
        if len(allowed_projects) > 1 and clean_req_sids:
            if len(req_source_projects) != len(clean_req_sids) or not req_projects.issubset(
                {existing_project_id}
            ):
                return None
        project_id = existing_project_id
    elif len(allowed_projects) == 1:
        project_id = allowed_projects[0]
    else:
        # Multi-project principal with no existing persisted rows: require a unique confirmed project
        if len(thread_confirmed_projects) == 1:
            inferred_proj = next(iter(thread_confirmed_projects))
            if inherited_project_id is not None and inherited_project_id != inferred_proj:
                return None
            if clean_req_sids and (
                len(req_source_projects) != len(clean_req_sids)
                or not req_projects.issubset({inferred_proj})
            ):
                return None
            project_id = inferred_proj
        elif not thread_confirmed_projects and inherited_project_id is not None:
            if clean_req_sids and (
                len(req_source_projects) != len(clean_req_sids)
                or not req_projects.issubset({inherited_project_id})
            ):
                return None
            project_id = inherited_project_id
        else:
            if (
                not clean_req_sids
                or len(req_source_projects) != len(clean_req_sids)
                or len(req_projects) != 1
            ):
                return None
            project_id = next(iter(req_projects))

    if not allowed_partitions:
        partitions = (project_id,)
    elif len(allowed_projects) > 1 and set(allowed_partitions).issubset(set(allowed_projects)):
        partitions = (project_id,)
    else:
        partitions = allowed_partitions

    return SessionPersistenceScope(
        tenant_id=tenant_id,
        project_id=project_id,
        thread_id=thread_id,
        owner_key=owner_key,
        security_partition_ids=partitions,
    )


async def ensure_session_qdrant_collection(
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    vector_dim: int,
    force_verify: bool = False,
) -> str:
    """Ensure `session_units_{project_id}` exists with matching vector_dim & filter indexes.

    Because `session_units_{project_id}` is 100% derived from raw PostgreSQL `messages`,
    any embedding dimension change safely recreates the derived collection.
    """
    collection_name = session_qdrant_collection_name(project_id)
    transport = getattr(qdrant_client, "_transport", None)
    transport_tag = (
        "http" if isinstance(transport, httpx.AsyncHTTPTransport) else f"mock:{id(transport)}"
    )
    cache_key = (transport_tag, str(qdrant_client.base_url), collection_name)
    if not force_verify and _VERIFIED_SESSION_COLLECTIONS.get(cache_key) == vector_dim:
        return collection_name

    needs_create = True
    info_res = await qdrant_client.get(f"/collections/{collection_name}")
    if info_res.is_success:
        cfg = info_res.json().get("result", {}).get("config", {})
        existing_vectors = cfg.get("params", {}).get("vectors", {})
        existing_dim = (
            existing_vectors.get("content_vector", {}).get("size")
            if isinstance(existing_vectors, dict)
            else None
        )
        if existing_dim == vector_dim:
            needs_create = False
        elif existing_dim and existing_dim != vector_dim:
            log.info(
                "Recreating derived Qdrant collection %s on dimension change (%s -> %s)",
                collection_name,
                existing_dim,
                vector_dim,
            )
            del_col = await qdrant_client.delete(f"/collections/{collection_name}")
            if del_col.status_code >= 400 and del_col.status_code != 404:
                del_col.raise_for_status()
            needs_create = True

    if needs_create:
        col_res = await qdrant_client.put(
            f"/collections/{collection_name}",
            json={
                "vectors": {
                    "content_vector": {
                        "size": int(vector_dim),
                        "distance": "Cosine",
                    }
                },
                "sparse_vectors": {
                    "bm25_sparse": {
                        "modifier": "idf",
                    }
                },
            },
        )
        if col_res.status_code >= 400 and col_res.status_code != 409:
            if "already exists" not in col_res.text.lower():
                raise RuntimeError(
                    f"Failed to create Qdrant collection {collection_name}: "
                    f"HTTP {col_res.status_code} - {col_res.text}"
                )

    async def _create_idx(field_name: str, field_schema: str) -> None:
        idx_res = await qdrant_client.put(
            f"/collections/{collection_name}/index?wait=true",
            json={"field_name": field_name, "field_schema": field_schema},
        )
        if idx_res.status_code >= 400 and idx_res.status_code != 409:
            if "already exists" not in idx_res.text.lower():
                raise RuntimeError(
                    f"Failed to create Qdrant index {field_name} on {collection_name}: {idx_res.text}"
                )

    await asyncio.gather(
        *(_create_idx(fname, fschema) for fname, fschema in SESSION_QDRANT_PAYLOAD_INDEX_FIELDS)
    )
    _VERIFIED_SESSION_COLLECTIONS[cache_key] = int(vector_dim)
    return collection_name


def build_session_unit_qdrant_payload(unit: SessionUnit) -> dict[str, Any]:
    """Build Qdrant metadata payload with ZERO raw conversation text (Data Minimization)."""
    entity_keys = [
        str(item["key"])
        for item in (unit.entity_mentions_json or [])
        if isinstance(item, dict) and item.get("key")
    ]
    valid_from_aware = (
        unit.valid_from if unit.valid_from.tzinfo is not None else unit.valid_from.replace(tzinfo=UTC)
    )
    valid_to_aware = (
        unit.valid_to if unit.valid_to.tzinfo is not None else unit.valid_to.replace(tzinfo=UTC)
    )
    return {
        "session_unit_id": unit.id,
        "tenant_id": unit.tenant_id,
        "project_id": unit.project_id,
        "thread_id": unit.thread_id,
        "unit_kind": unit.unit_kind,
        "ordinal": int(unit.ordinal),
        "episode_id": unit.episode_id,
        "episode_ordinal": int(unit.episode_ordinal),
        "window_id": unit.window_id,
        "message_from_id": unit.message_from_id,
        "message_to_id": unit.message_to_id,
        "role": unit.role,
        "epistemic_status": unit.epistemic_status,
        "topic_slot_key": unit.topic_slot_key,
        "security_partition_ids": list(unit.security_partition_ids_json or []),
        "cited_source_ids": list(unit.cited_source_ids_json or []),
        "entity_keys": entity_keys,
        "token_count": int(unit.token_count),
        "content_hash": unit.content_hash,
        "embedding_model_signature": unit.embedding_model_signature,
        "valid_from_ts": int(valid_from_aware.timestamp()),
        "valid_to_ts": int(valid_to_aware.timestamp()),
    }


async def upsert_session_units_to_qdrant(
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    items: Sequence[tuple[SessionUnit, Sequence[float], dict[str, list]]],
) -> int:
    """Upsert dense + BM25 sparse vectors for `SessionUnit` rows into `session_units_{project_id}`."""
    if not items:
        return 0
    first_vec = items[0][1]
    vector_dim = len(first_vec) if first_vec else FALLBACK_VECTOR_DIM
    collection_name = await ensure_session_qdrant_collection(
        qdrant_client,
        project_id=project_id,
        vector_dim=vector_dim,
    )
    points: list[dict[str, Any]] = []
    for unit, dense_vec, sparse_vec in items:
        point_id = unit.qdrant_point_id or generate_session_unit_point_id(collection_name, unit.id)
        points.append(
            {
                "id": point_id,
                "payload": build_session_unit_qdrant_payload(unit),
                "vector": {
                    "content_vector": [float(v) for v in dense_vec],
                    "bm25_sparse": sparse_vec,
                },
            }
        )
    res = await qdrant_client.put(
        f"/collections/{collection_name}/points?wait=true",
        json={"points": points},
    )
    if res.status_code in {400, 404}:
        await ensure_session_qdrant_collection(
            qdrant_client,
            project_id=project_id,
            vector_dim=vector_dim,
            force_verify=True,
        )
        res = await qdrant_client.put(
            f"/collections/{collection_name}/points?wait=true",
            json={"points": points},
        )
    res.raise_for_status()
    return len(points)


async def fetch_thread_turn_vectors_from_qdrant(
    qdrant_client: httpx.AsyncClient,
    *,
    tenant_id: str,
    project_id: str,
    thread_id: str,
    expected_embedding_sig: str | None = None,
    expected_dim: int | None = None,
) -> dict[str, tuple[float, ...]]:
    """Scroll dense `TURN` vectors for a thread from Qdrant (`0` HTTP embedder calls on cold start)."""
    collection_name = session_qdrant_collection_name(project_id)
    vectors_by_unit_id: dict[str, tuple[float, ...]] = {}
    observed_dim: int | None = expected_dim
    next_offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 256,
            "with_payload": [
                "session_unit_id",
                "unit_kind",
                "tenant_id",
                "thread_id",
                "embedding_model_signature",
            ],
            "with_vector": True,
            "filter": {
                "must": [
                    {"key": "tenant_id", "match": {"value": tenant_id}},
                    {"key": "project_id", "match": {"value": project_id}},
                    {"key": "thread_id", "match": {"value": thread_id}},
                    {"key": "unit_kind", "match": {"value": "TURN"}},
                ]
            },
        }
        if next_offset is not None:
            body["offset"] = next_offset
        res = await qdrant_client.post(f"/collections/{collection_name}/points/scroll", json=body)
        if res.status_code == 404:
            break
        if not res.is_success:
            res.raise_for_status()
        result = res.json().get("result", {})
        points = result.get("points") or []
        for pt in points:
            payload = pt.get("payload") or {}
            uid = str(payload.get("session_unit_id") or "")
            pt_sig = str(payload.get("embedding_model_signature") or "")
            if expected_embedding_sig and pt_sig != expected_embedding_sig:
                continue
            vec_field = pt.get("vector") or {}
            raw_vec = (
                vec_field.get("content_vector")
                if isinstance(vec_field, dict)
                else (vec_field if isinstance(vec_field, list) else None)
            )
            if not uid or not isinstance(raw_vec, list) or not raw_vec:
                continue
            if observed_dim is not None and len(raw_vec) != observed_dim:
                continue
            observed_dim = len(raw_vec)
            vectors_by_unit_id[uid] = l2_normalize(raw_vec)
        next_offset = result.get("next_page_offset")
        if next_offset is None or not points:
            break
    return vectors_by_unit_id


async def _scroll_thread_qdrant_points(
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Scroll point IDs and metadata payloads for `thread_id` in `session_units_{project_id}`."""
    collection_name = session_qdrant_collection_name(project_id)
    collected: list[dict[str, Any]] = []
    next_offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 500,
            "with_payload": ["session_unit_id", "embedding_model_signature"],
            "with_vector": False,
            "filter": {
                "must": [
                    {"key": "thread_id", "match": {"value": str(thread_id)}},
                ]
            },
        }
        if next_offset is not None:
            body["offset"] = next_offset
        res = await qdrant_client.post(f"/collections/{collection_name}/points/scroll", json=body)
        if res.status_code == 404:
            return []
        if not res.is_success:
            res.raise_for_status()
        result = res.json().get("result", {})
        points = result.get("points") or []
        collected.extend(points)
        next_offset = result.get("next_page_offset")
        if next_offset is None or not points:
            break
    return collected


async def delete_thread_points_from_qdrant(
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    thread_id: str,
) -> None:
    """Delete all Qdrant points belonging to `thread_id` in `session_units_{project_id}`."""
    collection_name = session_qdrant_collection_name(project_id)
    del_res = await qdrant_client.post(
        f"/collections/{collection_name}/points/delete?wait=true",
        json={
            "filter": {
                "must": [
                    {"key": "thread_id", "match": {"value": str(thread_id)}},
                ]
            }
        },
    )
    if del_res.status_code >= 400 and del_res.status_code != 404:
        del_res.raise_for_status()


async def _mark_units_qdrant_synced(
    session: AsyncSession,
    *,
    collection_name: str,
    synced_units: Sequence[SessionUnit],
    commit_if_clean: bool = True,
    caller_had_pending_state: bool = False,
) -> None:
    """Persist deterministic `qdrant_point_id` onto `SessionUnit` rows ONLY after Qdrant upsert succeeds."""
    if not synced_units:
        return
    dedup_units = {u.id: u for u in synced_units}
    async with session.begin_nested():
        for uid, u_obj in dedup_units.items():
            point_id = generate_session_unit_point_id(collection_name, uid)
            await session.execute(
                update(SessionUnit)
                .where(SessionUnit.id == uid)
                .values(qdrant_point_id=point_id)
            )
            u_obj.qdrant_point_id = point_id
        await session.flush()
    if commit_if_clean and not caller_had_pending_state:
        await session.commit()


def _blend_episode_centroid(turn_vecs: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Reproduce `EpisodeSegment` running centroid over constituent `TURN` vectors."""
    centroid = list(turn_vecs[0])
    for idx_v, tv in enumerate(turn_vecs[1:], start=1):
        blended = [(centroid[i] * idx_v + float(tv[i])) / (idx_v + 1) for i in range(len(tv))]
        centroid = list(l2_normalize(blended))
    return l2_normalize(centroid)


async def _verify_persisted_thread_acl_and_messages(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    thread_id: str,
    thread_units: Sequence[SessionUnit],
) -> tuple[bool, dict[str, str]]:
    """Verify every persisted `SessionUnit` in `thread_units` still has active `Message` rows
    and `CONFIRMED` `SourceProjectMapping` in `(tenant_id, project_id)`.

    Returns `(is_authorized_and_intact, normalized_turn_text_by_message_id)`.
    """
    from sag_api.services.agent_domain import _message_source_ids

    if not thread_units or not tenant_id or not project_id:
        return False, {}
    if any(
        u.tenant_id != tenant_id or u.project_id != project_id or u.thread_id != thread_id
        for u in thread_units
    ):
        return False, {}

    turn_units = sorted(
        (u for u in thread_units if u.unit_kind == "TURN"),
        key=lambda u: int(u.ordinal),
    )
    if not turn_units:
        return False, {}

    msg_ids = [u.message_from_id for u in turn_units]
    msg_rows = list(
        (
            await session.execute(
                select(Message).where(
                    Message.thread_id == thread_id,
                    Message.id.in_(msg_ids),
                    Message.status == MessageStatus.OK,
                )
            )
        )
        .scalars()
        .all()
    )
    row_by_id = {str(m.id): m for m in msg_rows}
    if any(mid not in row_by_id for mid in msg_ids):
        return False, {}

    required_sids: set[str] = set()
    norm_text_by_id: dict[str, str] = {}
    for u in turn_units:
        m = row_by_id[u.message_from_id]
        norm_txt = normalize_session_text(str(m.content or ""))
        if not norm_txt:
            return False, {}
        expected_hash = hashlib.sha256(norm_txt.encode("utf-8")).hexdigest()
        if u.content_hash != expected_hash:
            return False, {}
        norm_text_by_id[u.message_from_id] = norm_txt

        u_sids = {str(s).strip() for s in (u.cited_source_ids_json or []) if str(s).strip()}
        required_sids.update(u_sids)
        if m.role == MessageRole.ASSISTANT:
            m_sids = _message_source_ids(m)
            if m_sids is None:
                return False, {}
            required_sids.update(str(s).strip() for s in m_sids if str(s).strip())

    for u in thread_units:
        if u.unit_kind != "TURN":
            required_sids.update(
                str(s).strip() for s in (u.cited_source_ids_json or []) if str(s).strip()
            )

    if required_sids:
        confirmed_map = await _lookup_confirmed_source_projects(
            session,
            organization_id=tenant_id,
            source_ids=required_sids,
            allowed_projects=(project_id,),
        )
        if not required_sids.issubset(confirmed_map.keys()):
            return False, {}

    return True, norm_text_by_id


async def _repair_thread_qdrant_from_db_units(
    session: AsyncSession,
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    thread_id: str,
    thread_units: Sequence[SessionUnit],
    missing_unit_ids: set[str],
    embedder: Any | None = None,
    caller_had_pending_state: bool = False,
    preverified_turn_texts: dict[str, str] | None = None,
) -> int:
    """Reconstruct and upsert missing Qdrant `SessionUnit` points for `thread_id` from PostgreSQL truth."""
    if not thread_units or not missing_unit_ids:
        return 0
    collection_name = session_qdrant_collection_name(project_id)
    turn_units = sorted(
        (u for u in thread_units if u.unit_kind == "TURN"),
        key=lambda u: int(u.ordinal),
    )
    if not turn_units:
        return 0

    # Enforce active SourceProjectMapping ACL (`state='CONFIRMED'`) BEFORE warm or cold Qdrant repair
    if preverified_turn_texts is None:
        acl_ok, msg_by_id = await _verify_persisted_thread_acl_and_messages(
            session,
            tenant_id=turn_units[0].tenant_id,
            project_id=project_id,
            thread_id=thread_id,
            thread_units=thread_units,
        )
        if not acl_ok:
            log.warning(
                "Purging revoked or stale session units instead of repairing Qdrant project=%s thread=%s",
                project_id,
                thread_id,
            )
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            await purge_thread_session_rows(session, thread_id=thread_id)
            if not caller_had_pending_state:
                await session.commit()
            await delete_thread_points_from_qdrant(
                qdrant_client,
                project_id=project_id,
                thread_id=thread_id,
            )
            return 0
    else:
        msg_by_id = preverified_turn_texts

    thread_sig = turn_units[0].embedding_model_signature
    if any(u.embedding_model_signature != thread_sig for u in thread_units):
        log.warning("Skipping Qdrant repair for mixed-signature thread=%s", thread_id)
        return 0

    # Fast path: reuse warm `IncrementalSessionIndex` if present with matching signature & turn set
    warm_entries = SESSION_INDEX_CACHE.find_thread_entries(thread_id)
    for warm_idx in warm_entries:
        own_warm_turns = warm_idx.turns[warm_idx.inherited_turn_count :]
        if (
            warm_idx.embedding_mode == thread_sig
            and len(own_warm_turns) == len(turn_units)
            and all(
                own_warm_turns[i].turn_id == turn_units[i].message_from_id
                for i in range(len(turn_units))
            )
        ):
            scope = SessionPersistenceScope(
                tenant_id=turn_units[0].tenant_id,
                project_id=project_id,
                thread_id=thread_id,
                owner_key=str(turn_units[0].owner_key or ""),
                security_partition_ids=tuple(turn_units[0].security_partition_ids_json or ()),
            )
            items = _build_qdrant_items_from_index(
                warm_idx,
                scope=scope,
                embedding_sig=thread_sig,
                only_unit_ids=missing_unit_ids,
            )
            if not items:
                return 0
            _validate_vectors_for_persistence(
                index=warm_idx,
                qdrant_upsert_batch=items,
                embedding_sig=thread_sig,
            )
            upserted = await upsert_session_units_to_qdrant(
                qdrant_client,
                project_id=project_id,
                items=items,
            )
            await _mark_units_qdrant_synced(
                session,
                collection_name=collection_name,
                synced_units=[u for u, _, _ in items],
                commit_if_clean=True,
                caller_had_pending_state=caller_had_pending_state,
            )
            for entry in warm_entries:
                entry.qdrant_sync_pending = False
                entry.qdrant_rebuild_pending = False
            return upserted

    # Cold DB reconstruction path using ACL-verified `msg_by_id`
    msg_ids = [u.message_from_id for u in turn_units]
    if any(mid not in msg_by_id or not msg_by_id[mid] for mid in msg_ids):
        return 0

    turn_texts = [msg_by_id[u.message_from_id] for u in turn_units]
    if thread_sig == "deterministic-ngram-v1":
        computed_vecs = [deterministic_ngram_vector(txt) for txt in turn_texts]
        actual_sig = "deterministic-ngram-v1"
    else:
        from sag_api.services.session_context.runtime import resolve_session_embedder

        cand_embedder = resolve_session_embedder(embedder)
        if cand_embedder is None or compute_embedding_signature(cand_embedder) != thread_sig:
            log.warning(
                "Cannot repair Qdrant points for thread=%s: active embedder does not match '%s'",
                thread_id,
                thread_sig,
            )
            return 0
        computed_vecs, actual_sig = await embed_texts_with_provenance(
            turn_texts,
            embedder=cand_embedder,
        )
        if actual_sig != thread_sig:
            log.warning(
                "Skipping Qdrant repair for thread=%s: embedder degraded (%s != %s)",
                thread_id,
                actual_sig,
                thread_sig,
            )
            return 0

    if not computed_vecs or any(not v for v in computed_vecs):
        return 0
    ref_dim = len(computed_vecs[0])
    if any(len(v) != ref_dim for v in computed_vecs):
        return 0

    turn_vec_by_msg_id = {
        u.message_from_id: vec for u, vec in zip(turn_units, computed_vecs, strict=True)
    }
    doc_freqs: dict[str, int] = {}
    total_words = 0
    for u in turn_units:
        for word in (u.term_freqs_json or {}):
            doc_freqs[str(word)] = doc_freqs.get(str(word), 0) + 1
        total_words += max(1, int(u.doc_word_len or 1))
    avg_len = max(8.0, total_words / max(1, len(turn_units)))
    total_docs = max(50, len(turn_units))

    repair_items: list[tuple[SessionUnit, Sequence[float], dict[str, list]]] = []
    for u in thread_units:
        if u.id not in missing_unit_ids:
            continue
        if u.unit_kind == "TURN":
            dense_vec = turn_vec_by_msg_id.get(u.message_from_id)
            if dense_vec is None:
                continue
            sparse_vec = compute_sparse_bm25_vector(
                msg_by_id[u.message_from_id],
                avg_doc_len=avg_len,
                corpus_doc_freqs=doc_freqs,
                total_corpus_docs=total_docs,
            )
            repair_items.append((u, dense_vec, sparse_vec))
        else:
            src_mids = [str(m) for m in (u.source_message_ids_json or []) if str(m) in turn_vec_by_msg_id]
            if not src_mids:
                continue
            span_vecs = [turn_vec_by_msg_id[m] for m in src_mids]
            if u.unit_kind == "WINDOW":
                avg_v = [sum(v[i] for v in span_vecs) / len(span_vecs) for i in range(ref_dim)]
                dense_vec = l2_normalize(avg_v)
            else:
                dense_vec = _blend_episode_centroid(span_vecs)
            joined_txt = "\n".join(msg_by_id[m] for m in src_mids)
            sparse_vec = compute_sparse_bm25_vector(
                joined_txt,
                avg_doc_len=avg_len,
                corpus_doc_freqs=doc_freqs,
                total_corpus_docs=total_docs,
            )
            repair_items.append((u, dense_vec, sparse_vec))

    if not repair_items:
        return 0

    upserted = await upsert_session_units_to_qdrant(
        qdrant_client,
        project_id=project_id,
        items=repair_items,
    )
    await _mark_units_qdrant_synced(
        session,
        collection_name=collection_name,
        synced_units=[u for u, _, _ in repair_items],
        commit_if_clean=True,
        caller_had_pending_state=caller_had_pending_state,
    )
    for entry in warm_entries:
        entry.qdrant_sync_pending = False
        entry.qdrant_rebuild_pending = False
    return upserted


async def reconcile_orphan_session_units(
    session: AsyncSession,
    qdrant_client: httpx.AsyncClient,
    *,
    project_id: str,
    embedder: Any | None = None,
) -> dict[str, int]:
    """Bidirectionally reconcile `session_units_{project_id}` against PostgreSQL `session_units`.

    1. Deletes orphan or signature-drifted Qdrant points missing from PostgreSQL.
    2. Backfills missing Qdrant points for PostgreSQL `SessionUnit` rows (`qdrant_point_id IS NULL`
       or absent from Qdrant after transient `503` / collection recovery).
    """
    caller_had_pending_state = bool(session.new or session.dirty or session.deleted)
    collection_name = session_qdrant_collection_name(project_id)
    all_points: list[dict[str, Any]] = []
    next_offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 500,
            "with_payload": ["session_unit_id", "thread_id", "embedding_model_signature"],
            "with_vector": False,
        }
        if next_offset is not None:
            body["offset"] = next_offset
        res = await qdrant_client.post(f"/collections/{collection_name}/points/scroll", json=body)
        if res.status_code == 404:
            break
        if not res.is_success:
            res.raise_for_status()
        data = res.json().get("result", {})
        points = data.get("points") or []
        all_points.extend(points)
        next_offset = data.get("next_page_offset")
        if next_offset is None or not points:
            break

    db_units = list(
        (
            await session.execute(
                select(SessionUnit)
                .where(SessionUnit.project_id == project_id)
                .order_by(
                    SessionUnit.thread_id.asc(),
                    SessionUnit.unit_kind.asc(),
                    SessionUnit.ordinal.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    units_by_thread: dict[str, list[SessionUnit]] = {}
    for u in db_units:
        units_by_thread.setdefault(u.thread_id, []).append(u)

    # Verify active `SourceProjectMapping` (`CONFIRMED`) & backing `Message` integrity per thread;
    # purge any thread whose cited source was revoked before computing orphans or repairing Qdrant.
    verified_texts_by_thread: dict[str, dict[str, str]] = {}
    purged_any_thread = False
    for t_id, t_units in sorted(list(units_by_thread.items())):
        acl_ok, norm_texts = await _verify_persisted_thread_acl_and_messages(
            session,
            tenant_id=t_units[0].tenant_id,
            project_id=project_id,
            thread_id=t_id,
            thread_units=t_units,
        )
        if not acl_ok:
            log.warning(
                "Purging unauthorized or stale persisted session units during reconciliation project=%s thread=%s",
                project_id,
                t_id,
            )
            SESSION_INDEX_CACHE.invalidate_thread(t_id)
            await purge_thread_session_rows(session, thread_id=t_id)
            units_by_thread.pop(t_id, None)
            purged_any_thread = True
        else:
            verified_texts_by_thread[t_id] = norm_texts

    if purged_any_thread and not caller_had_pending_state:
        await session.commit()

    valid_db_units = [u for t_units in units_by_thread.values() for u in t_units]
    db_units_by_id = {u.id: u for u in valid_db_units}

    orphan_point_ids: list[str] = []
    present_valid_unit_ids: set[str] = set()
    for pt in all_points:
        pid = pt.get("id")
        if not pid:
            continue
        payload = pt.get("payload") or {}
        uid = str(payload.get("session_unit_id") or "")
        pt_sig = str(payload.get("embedding_model_signature") or "")
        db_u = db_units_by_id.get(uid) if uid else None
        if db_u is None or (pt_sig and pt_sig != db_u.embedding_model_signature):
            orphan_point_ids.append(str(pid))
        else:
            present_valid_unit_ids.add(uid)

    if orphan_point_ids:
        del_res = await qdrant_client.post(
            f"/collections/{collection_name}/points/delete?wait=true",
            json={"points": orphan_point_ids},
        )
        if del_res.status_code >= 400 and del_res.status_code != 404:
            raise RuntimeError(f"Failed to delete orphan session Qdrant points: {del_res.text}")

    missing_by_thread: dict[str, set[str]] = {}
    for u in valid_db_units:
        if u.qdrant_point_id is None or u.id not in present_valid_unit_ids:
            missing_by_thread.setdefault(u.thread_id, set()).add(u.id)

    missing_repaired = 0
    for t_id, missing_ids in sorted(missing_by_thread.items()):
        missing_repaired += await _repair_thread_qdrant_from_db_units(
            session,
            qdrant_client,
            project_id=project_id,
            thread_id=t_id,
            thread_units=units_by_thread[t_id],
            missing_unit_ids=missing_ids,
            embedder=embedder,
            caller_had_pending_state=caller_had_pending_state,
            preverified_turn_texts=verified_texts_by_thread.get(t_id),
        )

    return {
        "scanned": len(all_points),
        "orphans_deleted": len(orphan_point_ids),
        "missing_repaired": missing_repaired,
    }


async def collect_thread_persistence_projects(
    session: AsyncSession,
    *,
    thread_id: str,
) -> list[str]:
    """Return distinct `project_id` values currently persisted for `thread_id`."""
    if not settings.session_context_persistence_enabled or not thread_id:
        return []
    try:
        rows = (
            await session.execute(
                select(SessionUnit.project_id)
                .where(SessionUnit.thread_id == thread_id)
                .distinct()
            )
        ).scalars().all()
        return sorted({str(p) for p in rows if p})
    except Exception as exc:  # noqa: BLE001
        if _is_missing_session_tables_error(exc):
            return []
        log.warning("Could not inspect persisted project scopes for thread=%s: %s", thread_id, exc)
        return []


def _expunge_derived_from_identity_map(
    session: AsyncSession,
    *,
    thread_id: str,
    only_unit_id: str | None = None,
) -> None:
    sync_sess = session.sync_session
    for obj in list(sync_sess.identity_map.values()):
        if not isinstance(
            obj,
            (SessionUnit, SessionUnitMessage, SessionDecisionLedger, SessionGraphEdge),
        ):
            continue
        if getattr(obj, "thread_id", None) != thread_id:
            continue
        if only_unit_id is not None:
            if isinstance(obj, SessionUnit) and obj.id != only_unit_id:
                continue
            if isinstance(obj, SessionUnitMessage) and obj.session_unit_id != only_unit_id:
                continue
            if isinstance(obj, SessionDecisionLedger):
                continue
            if (
                isinstance(obj, SessionGraphEdge)
                and obj.source_unit_id != only_unit_id
                and obj.target_unit_id != only_unit_id
            ):
                continue
        sync_sess.expunge(obj)


async def purge_thread_session_rows(
    session: AsyncSession,
    *,
    thread_id: str,
) -> None:
    """Delete derived PostgreSQL/SQLite session rows in child-to-parent order."""
    if not thread_id:
        return
    try:
        async with session.begin_nested():
            await session.execute(
                delete(SessionGraphEdge).where(SessionGraphEdge.thread_id == thread_id)
            )
            await session.execute(
                delete(SessionDecisionLedger).where(SessionDecisionLedger.thread_id == thread_id)
            )
            await session.execute(
                delete(SessionUnitMessage).where(SessionUnitMessage.thread_id == thread_id)
            )
            await session.execute(
                delete(SessionUnit).where(SessionUnit.thread_id == thread_id)
            )
            await session.flush()
        _expunge_derived_from_identity_map(session, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001
        if not _is_missing_session_tables_error(exc):
            raise


async def purge_thread_qdrant_points_for_projects(
    session: AsyncSession,
    *,
    thread_id: str,
    project_ids: Sequence[str],
    qdrant_client: httpx.AsyncClient | None = None,
) -> None:
    """Best-effort Qdrant cleanup for `thread_id` across `project_ids` after DB delete/purge."""
    if not project_ids or not thread_id:
        return
    client, should_close = _open_qdrant_client_context(session, qdrant_client)
    if client is None:
        return
    try:
        for proj_id in project_ids:
            try:
                await delete_thread_points_from_qdrant(
                    client,
                    project_id=proj_id,
                    thread_id=thread_id,
                )
            except Exception as q_exc:  # noqa: BLE001
                log.warning(
                    "Best-effort Qdrant thread cleanup skipped project=%s thread=%s err=%s",
                    proj_id,
                    thread_id,
                    sanitize_error_message(q_exc),
                )
    finally:
        if should_close:
            await client.aclose()


def _serialize_sentence_spans(turn: IndexedTurn) -> list[dict[str, Any]]:
    """Persist sentence character offsets `[start, end)` + entity keys (zero raw sentence strings)."""
    raw_spans = split_sentences_with_spans(turn.content)
    serialized: list[dict[str, Any]] = []
    search_cursor = 0
    for idx, sent in enumerate(turn.sentences):
        if idx < len(raw_spans):
            s_start, s_end, _ = raw_spans[idx]
        else:
            pos = turn.content.find(sent.text, search_cursor)
            if pos >= 0:
                s_start, s_end = pos, pos + len(sent.text)
                search_cursor = s_end
            else:
                s_start, s_end = 0, len(turn.content)
        serialized.append(
            {
                "sentence_id": sent.sentence_id,
                "ordinal": int(sent.ordinal),
                "start": int(max(0, s_start)),
                "end": int(min(len(turn.content), max(s_start, s_end))),
                "entity_keys": list(sent.entity_keys),
            }
        )
    return serialized


def _turn_valid_window(
    turn: IndexedTurn,
    turns_by_id: dict[str, IndexedTurn],
) -> tuple[datetime, datetime]:
    valid_from = _ts_to_utc(turn.created_at_ts)
    if turn.epistemic_status == "NEGATED":
        return valid_from, valid_from
    if turn.superseded_by_turn_id:
        sup_turn = turns_by_id.get(turn.superseded_by_turn_id)
        sup_dt = _ts_to_utc(sup_turn.created_at_ts) if sup_turn is not None else valid_from
        return valid_from, max(valid_from, sup_dt)
    return valid_from, SESSION_OPEN_ENDED_VALID_TO


def _build_turn_session_unit(
    turn: IndexedTurn,
    *,
    scope: SessionPersistenceScope,
    episode_ordinal: int,
    embedding_sig: str,
    collection_name: str,
    turns_by_id: dict[str, IndexedTurn],
    qdrant_point_id: str | None = None,
) -> SessionUnit:
    _ = collection_name
    unit_id = generate_session_unit_id(scope.thread_id, "TURN", turn.ordinal, turn.turn_id)
    valid_from, valid_to = _turn_valid_window(turn, turns_by_id)
    return SessionUnit(
        id=unit_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        thread_id=scope.thread_id,
        owner_key=scope.owner_key or None,
        unit_kind="TURN",
        ordinal=turn.ordinal,
        episode_id=turn.episode_id,
        episode_ordinal=episode_ordinal,
        window_id=turn.window_id,
        message_from_id=turn.turn_id,
        message_to_id=turn.turn_id,
        source_message_ids_json=[turn.turn_id],
        role=turn.role,
        epistemic_status=turn.epistemic_status,
        topic_slot_key=turn.topic_slot_key,
        is_anaphoric_or_short=bool(turn.is_anaphoric_or_short),
        security_partition_ids_json=list(scope.security_partition_ids),
        cited_source_ids_json=list(turn.cited_source_ids),
        entity_mentions_json=[
            {
                "key": m.key,
                "surface": m.surface,
                "soft_aliases": list(m.soft_aliases),
                "confidence": round(float(m.confidence), 4),
            }
            for m in turn.entities
        ],
        sentences_json=_serialize_sentence_spans(turn),
        term_freqs_json=dict(turn.term_freqs),
        doc_word_len=max(0, int(turn.doc_word_len)),
        token_count=max(0, int(turn.token_count)),
        content_hash=turn.content_hash,
        embedding_model_signature=embedding_sig,
        qdrant_point_id=qdrant_point_id,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _build_span_session_unit(
    *,
    unit_kind: str,
    ordinal: int,
    episode_id: str,
    episode_ordinal: int,
    window_id: str | None,
    turn_ids: Sequence[str],
    scope: SessionPersistenceScope,
    embedding_sig: str,
    collection_name: str,
    turns_by_id: dict[str, IndexedTurn],
    qdrant_point_id: str | None = None,
) -> SessionUnit:
    _ = collection_name
    dedup_turn_ids = list(dict.fromkeys(str(tid) for tid in turn_ids if tid in turns_by_id))
    span_turns = [turns_by_id[tid] for tid in dedup_turn_ids]
    first_tid = dedup_turn_ids[0]
    last_tid = dedup_turn_ids[-1]
    unit_id = generate_session_unit_id(scope.thread_id, unit_kind, ordinal, first_tid)

    combined_hash = hashlib.sha256(
        "|".join(t.content_hash for t in span_turns).encode("utf-8")
    ).hexdigest()
    cited_union = sorted({src for t in span_turns for src in t.cited_source_ids})
    merged_entities: dict[str, dict[str, Any]] = {}
    for t in span_turns:
        for m in t.entities:
            merged_entities.setdefault(
                m.key,
                {
                    "key": m.key,
                    "surface": m.surface,
                    "soft_aliases": list(m.soft_aliases),
                    "confidence": round(float(m.confidence), 4),
                },
            )
    valid_from = _ts_to_utc(span_turns[0].created_at_ts)
    return SessionUnit(
        id=unit_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        thread_id=scope.thread_id,
        owner_key=scope.owner_key or None,
        unit_kind=unit_kind,
        ordinal=ordinal,
        episode_id=episode_id,
        episode_ordinal=episode_ordinal,
        window_id=window_id,
        message_from_id=first_tid,
        message_to_id=last_tid,
        source_message_ids_json=dedup_turn_ids,
        role=None,
        epistemic_status="FACT",
        topic_slot_key=None,
        is_anaphoric_or_short=False,
        security_partition_ids_json=list(scope.security_partition_ids),
        cited_source_ids_json=cited_union,
        entity_mentions_json=list(merged_entities.values()),
        sentences_json=[],
        term_freqs_json={},
        doc_word_len=sum(max(1, t.doc_word_len) for t in span_turns),
        token_count=sum(max(1, t.token_count) for t in span_turns),
        content_hash=combined_hash,
        embedding_model_signature=embedding_sig,
        qdrant_point_id=qdrant_point_id,
        valid_from=valid_from,
        valid_to=SESSION_OPEN_ENDED_VALID_TO,
    )


def _build_turn_graph_edges(
    turn: IndexedTurn,
    *,
    index: IncrementalSessionIndex,
    scope: SessionPersistenceScope,
    turn_unit_ids: dict[str, str],
) -> list[SessionGraphEdge]:
    u_id = turn_unit_ids[turn.turn_id]
    dedup_edges: dict[tuple[str, str, str, str, str, str], SessionGraphEdge] = {}

    def _add_edge(
        *,
        source_unit_id: str,
        target_unit_id: str | None,
        source_node_type: str,
        source_node_key: str,
        target_node_type: str,
        target_node_key: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        s_key = source_node_key[:256].strip()
        t_key = target_node_key[:256].strip()
        if not s_key or not t_key:
            return
        prov_key = (source_unit_id, source_node_type, s_key, target_node_type, t_key, edge_type)
        if prov_key in dedup_edges:
            return
        edge_id = generate_session_graph_edge_id(
            source_unit_id,
            s_key,
            t_key,
            edge_type,
            source_node_type=source_node_type,
            target_node_type=target_node_type,
        )
        dedup_edges[prov_key] = SessionGraphEdge(
            id=edge_id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            thread_id=scope.thread_id,
            source_unit_id=source_unit_id,
            target_unit_id=target_unit_id,
            source_node_type=source_node_type,
            source_node_key=s_key,
            target_node_type=target_node_type,
            target_node_key=t_key,
            edge_type=edge_type,
            weight=max(0.0, float(weight)),
            metadata_json=metadata or {},
        )

    for mention in turn.entities:
        occ = index.entity_to_turn_counts.get(mention.key, {}).get(turn.turn_id, 1)
        _add_edge(
            source_unit_id=u_id,
            target_unit_id=None,
            source_node_type="UNIT",
            source_node_key=u_id,
            target_node_type="ENTITY",
            target_node_key=mention.key,
            edge_type="ENTITY_MENTION",
            weight=float(max(1, occ)),
            metadata={"surface": mention.surface},
        )
        for alias_key in mention.soft_aliases:
            if alias_key and alias_key != mention.key:
                _add_edge(
                    source_unit_id=u_id,
                    target_unit_id=None,
                    source_node_type="ENTITY",
                    source_node_key=mention.key,
                    target_node_type="ENTITY",
                    target_node_key=alias_key,
                    edge_type="ALIAS_CANDIDATE",
                    weight=SOFT_ALIAS_WEIGHT,
                )

    for sent in turn.sentences:
        _add_edge(
            source_unit_id=u_id,
            target_unit_id=u_id,
            source_node_type="SENTENCE",
            source_node_key=sent.sentence_id,
            target_node_type="UNIT",
            target_node_key=u_id,
            edge_type="SENTENCE_IN_UNIT",
            weight=1.0,
        )
        for e_key in dict.fromkeys(sent.entity_keys):
            _add_edge(
                source_unit_id=u_id,
                target_unit_id=None,
                source_node_type="SENTENCE",
                source_node_key=sent.sentence_id,
                target_node_type="ENTITY",
                target_node_key=e_key,
                edge_type="ENTITY_MENTION",
                weight=1.0,
            )

    if turn.ordinal > 0 and turn.ordinal < len(index.turns):
        prev_turn = index.turns[turn.ordinal - 1]
        if prev_turn.episode_id == turn.episode_id and prev_turn.turn_id in turn_unit_ids:
            prev_u_id = turn_unit_ids[prev_turn.turn_id]
            _add_edge(
                source_unit_id=prev_u_id,
                target_unit_id=u_id,
                source_node_type="UNIT",
                source_node_key=prev_u_id,
                target_node_type="UNIT",
                target_node_key=u_id,
                edge_type="ADJACENT_IN_EPISODE",
                weight=0.35,
            )

    for sup_tid in turn.supersedes_turn_ids:
        old_u_id = turn_unit_ids.get(sup_tid)
        if old_u_id:
            _add_edge(
                source_unit_id=u_id,
                target_unit_id=old_u_id,
                source_node_type="UNIT",
                source_node_key=u_id,
                target_node_type="UNIT",
                target_node_key=old_u_id,
                edge_type="SUPERSEDES",
                weight=1.0,
            )

    for src_id in turn.cited_source_ids:
        _add_edge(
            source_unit_id=u_id,
            target_unit_id=None,
            source_node_type="UNIT",
            source_node_key=u_id,
            target_node_type="SOURCE",
            target_node_key=str(src_id),
            edge_type="CITES_DOCUMENT_SOURCE",
            weight=1.0,
        )

    return list(dedup_edges.values())


def _sparse_qdrant_dict_for_turn(turn: IndexedTurn) -> dict[str, list]:
    sorted_indices = sorted(turn.sparse_bm25.keys())
    return {
        "indices": sorted_indices,
        "values": [float(turn.sparse_bm25[idx]) for idx in sorted_indices],
    }


def _sparse_qdrant_dict_for_span(
    turn_ids: Sequence[str],
    *,
    index: IncrementalSessionIndex,
) -> dict[str, list]:
    joined_text = "\n".join(
        index.turns_by_id[tid].content for tid in turn_ids if tid in index.turns_by_id
    )
    avg_len = max(8.0, index.total_token_words / max(1, len(index.turns)))
    return compute_sparse_bm25_vector(
        joined_text,
        avg_doc_len=avg_len,
        corpus_doc_freqs=index.doc_freqs,
        total_corpus_docs=max(50, len(index.turns)),
    )


def _validate_vectors_for_persistence(
    *,
    index: IncrementalSessionIndex,
    qdrant_upsert_batch: Sequence[tuple[SessionUnit, Sequence[float], dict[str, list]]],
    embedding_sig: str,
    embedder: Any | None = None,
) -> int | None:
    """Verify vector provenance signature and uniform dimensions before writing to DB or Qdrant."""
    sig = embedding_sig.strip()
    if not sig:
        raise ValueError("embedding_model_signature must not be empty")
    if embedder is not None:
        embedder_sig = compute_embedding_signature(embedder)
        if embedder_sig != sig:
            raise ValueError(
                f"Embedder signature '{embedder_sig}' does not match target '{sig}'"
            )
    if index.turns and index.embedding_mode != sig:
        raise ValueError(
            f"Refusing to persist session vectors from '{index.embedding_mode}' under '{sig}'"
        )

    expected_dim = (
        declared_embedder_dim(embedder)
        if embedder is not None
        else (FALLBACK_VECTOR_DIM if sig == "deterministic-ngram-v1" else None)
    )
    observed_dim: int | None = expected_dim

    for turn in index.turns:
        if not turn.dense_vector:
            raise ValueError(f"Empty dense vector on turn {turn.turn_id}")
        if observed_dim is None:
            observed_dim = len(turn.dense_vector)
        elif len(turn.dense_vector) != observed_dim:
            raise ValueError(
                f"Inconsistent turn vector dimension {len(turn.dense_vector)} != {observed_dim}"
            )

    for win in index.windows:
        if win.turn_ids and (not win.dense_vector or (observed_dim and len(win.dense_vector) != observed_dim)):
            raise ValueError(f"Inconsistent window vector dimension on {win.window_id}")

    for ep in index.episodes:
        if ep.turn_ids and (not ep.centroid or (observed_dim and len(ep.centroid) != observed_dim)):
            raise ValueError(f"Inconsistent episode centroid dimension on {ep.episode_id}")

    for unit, dense_vec, _ in qdrant_upsert_batch:
        if unit.embedding_model_signature != sig:
            raise ValueError(
                f"Unit {unit.id} signature '{unit.embedding_model_signature}' != '{sig}'"
            )
        if not dense_vec:
            raise ValueError(f"Empty Qdrant vector for SessionUnit {unit.id}")
        if observed_dim is None:
            observed_dim = len(dense_vec)
        elif len(dense_vec) != observed_dim:
            raise ValueError(
                f"Inconsistent Qdrant vector dimension {len(dense_vec)} != {observed_dim} on {unit.id}"
            )

    return observed_dim


def _build_qdrant_items_from_index(
    index: IncrementalSessionIndex,
    *,
    scope: SessionPersistenceScope,
    embedding_sig: str,
    only_unit_ids: set[str] | None = None,
) -> list[tuple[SessionUnit, Sequence[float], dict[str, list]]]:
    """Build Qdrant `(SessionUnit, dense_vec, sparse_vec)` items from `IncrementalSessionIndex`."""
    collection_name = session_qdrant_collection_name(scope.project_id)
    ep_ord_by_id = {ep.episode_id: ep.ordinal for ep in index.episodes}
    items: list[tuple[SessionUnit, Sequence[float], dict[str, list]]] = []

    for turn in index.turns[index.inherited_turn_count :]:
        u_id = generate_session_unit_id(scope.thread_id, "TURN", turn.ordinal, turn.turn_id)
        if only_unit_ids is not None and u_id not in only_unit_ids:
            continue
        u_row = _build_turn_session_unit(
            turn,
            scope=scope,
            episode_ordinal=ep_ord_by_id.get(turn.episode_id, 0),
            embedding_sig=embedding_sig,
            collection_name=collection_name,
            turns_by_id=index.turns_by_id,
        )
        items.append((u_row, turn.dense_vector, _sparse_qdrant_dict_for_turn(turn)))

    for win in index.windows[index.inherited_window_count :]:
        if not win.turn_ids:
            continue
        first_tid = win.turn_ids[0]
        w_id = generate_session_unit_id(scope.thread_id, "WINDOW", win.ordinal, first_tid)
        if only_unit_ids is not None and w_id not in only_unit_ids:
            continue
        w_row = _build_span_session_unit(
            unit_kind="WINDOW",
            ordinal=win.ordinal,
            episode_id=win.episode_id,
            episode_ordinal=ep_ord_by_id.get(win.episode_id, 0),
            window_id=win.window_id,
            turn_ids=win.turn_ids,
            scope=scope,
            embedding_sig=embedding_sig,
            collection_name=collection_name,
            turns_by_id=index.turns_by_id,
        )
        items.append((w_row, win.dense_vector, _sparse_qdrant_dict_for_span(win.turn_ids, index=index)))

    for ep in index.episodes[index.inherited_episode_count :]:
        if not ep.turn_ids:
            continue
        ep_uid = generate_session_unit_id(scope.thread_id, "EPISODE", ep.ordinal, ep.turn_ids[0])
        if only_unit_ids is not None and ep_uid not in only_unit_ids:
            continue
        ep_row = _build_span_session_unit(
            unit_kind="EPISODE",
            ordinal=ep.ordinal,
            episode_id=ep.episode_id,
            episode_ordinal=ep.ordinal,
            window_id=None,
            turn_ids=ep.turn_ids,
            scope=scope,
            embedding_sig=embedding_sig,
            collection_name=collection_name,
            turns_by_id=index.turns_by_id,
        )
        items.append((ep_row, ep.centroid, _sparse_qdrant_dict_for_span(ep.turn_ids, index=index)))

    return items


async def backfill_pending_qdrant_for_thread(
    session: AsyncSession,
    *,
    index: IncrementalSessionIndex,
    scope: SessionPersistenceScope,
    embedding_sig: str,
    embedder: Any | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
    commit_if_clean: bool = True,
    caller_had_pending_state: bool = False,
) -> int:
    """Retry/backfill any missing or pending Qdrant `SessionUnit` points for `scope.thread_id`."""
    if len(index.turns) <= index.inherited_turn_count:
        index.qdrant_sync_pending = False
        index.qdrant_rebuild_pending = False
        return 0

    client, should_close = _open_qdrant_client_context(session, qdrant_client)
    if client is None:
        return 0

    collection_name = session_qdrant_collection_name(scope.project_id)
    try:
        cited_sids = {
            s
            for t in index.turns[index.inherited_turn_count :]
            for s in t.cited_source_ids
            if s
        }
        if cited_sids:
            confirmed_map = await _lookup_confirmed_source_projects(
                session,
                organization_id=scope.tenant_id,
                source_ids=cited_sids,
                allowed_projects=(scope.project_id,),
            )
            if not cited_sids.issubset(confirmed_map.keys()):
                SESSION_INDEX_CACHE.invalidate_thread(scope.thread_id)
                await purge_thread_session_rows(session, thread_id=scope.thread_id)
                if commit_if_clean and not caller_had_pending_state:
                    await session.commit()
                await delete_thread_points_from_qdrant(
                    client,
                    project_id=scope.project_id,
                    thread_id=scope.thread_id,
                )
                return 0

        all_items = _build_qdrant_items_from_index(
            index,
            scope=scope,
            embedding_sig=embedding_sig,
        )
        _validate_vectors_for_persistence(
            index=index,
            qdrant_upsert_batch=all_items,
            embedding_sig=embedding_sig,
            embedder=embedder,
        )
        all_expected_ids = {u.id for u, _, _ in all_items}

        db_unsynced_ids = set(
            (
                await session.execute(
                    select(SessionUnit.id).where(
                        SessionUnit.thread_id == scope.thread_id,
                        SessionUnit.qdrant_point_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        if index.qdrant_rebuild_pending:
            await delete_thread_points_from_qdrant(
                client,
                project_id=scope.project_id,
                thread_id=scope.thread_id,
            )
            index.qdrant_rebuild_pending = False
            items_to_upsert = all_items
        else:
            existing_pts = await _scroll_thread_qdrant_points(
                client,
                project_id=scope.project_id,
                thread_id=scope.thread_id,
            )
            stale_pids: list[str] = []
            present_uids: set[str] = set()
            for pt in existing_pts:
                pid = pt.get("id")
                payload = pt.get("payload") or {}
                uid = str(payload.get("session_unit_id") or "")
                pt_sig = str(payload.get("embedding_model_signature") or "")
                if not uid or uid not in all_expected_ids or (pt_sig and pt_sig != embedding_sig):
                    if pid:
                        stale_pids.append(str(pid))
                else:
                    present_uids.add(uid)

            if stale_pids:
                del_res = await client.post(
                    f"/collections/{collection_name}/points/delete?wait=true",
                    json={"points": stale_pids},
                )
                if del_res.status_code >= 400 and del_res.status_code != 404:
                    del_res.raise_for_status()

            missing_ids = db_unsynced_ids | (all_expected_ids - present_uids)
            items_to_upsert = [item for item in all_items if item[0].id in missing_ids]

        upserted = 0
        if items_to_upsert:
            upserted = await upsert_session_units_to_qdrant(
                client,
                project_id=scope.project_id,
                items=items_to_upsert,
            )
            await _mark_units_qdrant_synced(
                session,
                collection_name=collection_name,
                synced_units=[u for u, _, _ in items_to_upsert],
                commit_if_clean=commit_if_clean,
                caller_had_pending_state=caller_had_pending_state,
            )
        elif db_unsynced_ids:
            # Points were already in Qdrant, only DB markers were missing
            synced_objs = [item[0] for item in all_items if item[0].id in db_unsynced_ids]
            await _mark_units_qdrant_synced(
                session,
                collection_name=collection_name,
                synced_units=synced_objs,
                commit_if_clean=commit_if_clean,
                caller_had_pending_state=caller_had_pending_state,
            )

        index.qdrant_sync_pending = False
        index.qdrant_rebuild_pending = False
        return upserted
    except Exception as q_exc:  # noqa: BLE001
        index.qdrant_sync_pending = True
        log.warning(
            "Deferred Qdrant backfill failed project=%s thread=%s err=%s",
            scope.project_id,
            scope.thread_id,
            sanitize_error_message(q_exc),
        )
        return 0
    finally:
        if should_close:
            await client.aclose()


async def sync_session_index_to_storage(
    session: AsyncSession,
    *,
    index: IncrementalSessionIndex,
    scope: SessionPersistenceScope,
    embedding_sig: str,
    mode: str,
    start_turn_ordinal: int = 0,
    start_window_ordinal: int = 0,
    start_episode_ordinal: int = 0,
    start_decision_idx: int = 0,
    mutated_prior_turn_ids: set[str] | None = None,
    grown_episode_ordinal: int | None = None,
    embedder: Any | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
    commit_if_clean: bool = True,
    caller_had_pending_state: bool = False,
) -> PersistenceSyncStats:
    """Persist `IncrementalSessionIndex` (`mode='rebuild'` or `mode='delta'`) to PostgreSQL & Qdrant."""
    _validate_vectors_for_persistence(
        index=index,
        qdrant_upsert_batch=(),
        embedding_sig=embedding_sig,
        embedder=embedder,
    )

    stats = PersistenceSyncStats(status=mode)
    collection_name = session_qdrant_collection_name(scope.project_id)
    inh_turn_k = index.inherited_turn_count
    inh_win_k = index.inherited_window_count
    inh_ep_k = index.inherited_episode_count
    inh_dec_k = index.inherited_decision_count
    inherited_turn_ids = {t.turn_id for t in index.turns[:inh_turn_k]}
    ep_ord_by_id = {ep.episode_id: ep.ordinal for ep in index.episodes}
    turn_unit_ids = {
        t.turn_id: generate_session_unit_id(scope.thread_id, "TURN", t.ordinal, t.turn_id)
        for t in index.turns[inh_turn_k:]
    }
    decision_ids_by_turn: dict[str, str] = {}
    for dec in index.decision_ledger[inh_dec_k:]:
        u_id = turn_unit_ids.get(dec.turn_id)
        if u_id:
            decision_ids_by_turn[dec.turn_id] = generate_session_decision_id(u_id, dec.topic_slot_key)

    qdrant_upsert_batch: list[tuple[SessionUnit, Sequence[float], dict[str, list]]] = []
    prior_unsynced_ids: set[str] = set()

    try:
        async with session.begin_nested():
            if mode == "rebuild":
                await purge_thread_session_rows(session, thread_id=scope.thread_id)
                start_turn_ordinal = inh_turn_k
                start_window_ordinal = inh_win_k
                start_episode_ordinal = inh_ep_k
                start_decision_idx = inh_dec_k
                mutated_prior_turn_ids = set()
                grown_episode_ordinal = None
            else:
                start_turn_ordinal = max(start_turn_ordinal, inh_turn_k)
                start_window_ordinal = max(start_window_ordinal, inh_win_k)
                start_episode_ordinal = max(start_episode_ordinal, inh_ep_k)
                start_decision_idx = max(start_decision_idx, inh_dec_k)
                prior_unsynced_ids = set(
                    (
                        await session.execute(
                            select(SessionUnit.id).where(
                                SessionUnit.thread_id == scope.thread_id,
                                SessionUnit.qdrant_point_id.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )

            # 1. Update newly superseded prior TURN units (`epistemic_status`, `valid_to`, dirty `qdrant_point_id`)
            for prior_tid in sorted(mutated_prior_turn_ids or ()):
                prior_turn = index.turns_by_id.get(prior_tid)
                if (
                    prior_turn is None
                    or prior_turn.ordinal < inh_turn_k
                    or prior_turn.ordinal >= start_turn_ordinal
                ):
                    continue
                prior_uid = turn_unit_ids[prior_tid]
                _, new_valid_to = _turn_valid_window(prior_turn, index.turns_by_id)
                await session.execute(
                    update(SessionUnit)
                    .where(SessionUnit.id == prior_uid)
                    .values(
                        epistemic_status=prior_turn.epistemic_status,
                        valid_to=new_valid_to,
                        qdrant_point_id=None,
                    )
                )
                rebuilt_unit = _build_turn_session_unit(
                    prior_turn,
                    scope=scope,
                    episode_ordinal=ep_ord_by_id.get(prior_turn.episode_id, 0),
                    embedding_sig=embedding_sig,
                    collection_name=collection_name,
                    turns_by_id=index.turns_by_id,
                )
                qdrant_upsert_batch.append(
                    (rebuilt_unit, prior_turn.dense_vector, _sparse_qdrant_dict_for_turn(prior_turn))
                )

            # 2. Insert new TURN units
            new_turn_units: list[SessionUnit] = []
            for turn in index.turns[start_turn_ordinal:]:
                u_row = _build_turn_session_unit(
                    turn,
                    scope=scope,
                    episode_ordinal=ep_ord_by_id.get(turn.episode_id, 0),
                    embedding_sig=embedding_sig,
                    collection_name=collection_name,
                    turns_by_id=index.turns_by_id,
                )
                new_turn_units.append(u_row)
                qdrant_upsert_batch.append(
                    (u_row, turn.dense_vector, _sparse_qdrant_dict_for_turn(turn))
                )
            if new_turn_units:
                session.add_all(new_turn_units)

            # 3. Insert new WINDOW units
            new_window_units: list[SessionUnit] = []
            for win in index.windows[start_window_ordinal:]:
                if not win.turn_ids:
                    continue
                w_row = _build_span_session_unit(
                    unit_kind="WINDOW",
                    ordinal=win.ordinal,
                    episode_id=win.episode_id,
                    episode_ordinal=ep_ord_by_id.get(win.episode_id, 0),
                    window_id=win.window_id,
                    turn_ids=win.turn_ids,
                    scope=scope,
                    embedding_sig=embedding_sig,
                    collection_name=collection_name,
                    turns_by_id=index.turns_by_id,
                )
                new_window_units.append(w_row)
                qdrant_upsert_batch.append(
                    (
                        w_row,
                        win.dense_vector,
                        _sparse_qdrant_dict_for_span(win.turn_ids, index=index),
                    )
                )
            if new_window_units:
                session.add_all(new_window_units)

            # 4. Replace grown active EPISODE unit via DELETE + INSERT (respects immutable update trigger)
            if (
                grown_episode_ordinal is not None
                and inh_ep_k <= grown_episode_ordinal < start_episode_ordinal
            ):
                grown_ep = index.episodes[grown_episode_ordinal]
                grown_ep_uid = generate_session_unit_id(
                    scope.thread_id, "EPISODE", grown_ep.ordinal, grown_ep.turn_ids[0]
                )
                prior_unsynced_ids.discard(grown_ep_uid)
                await session.execute(
                    delete(SessionGraphEdge).where(
                        or_(
                            SessionGraphEdge.source_unit_id == grown_ep_uid,
                            SessionGraphEdge.target_unit_id == grown_ep_uid,
                        )
                    )
                )
                await session.execute(
                    delete(SessionUnitMessage).where(SessionUnitMessage.session_unit_id == grown_ep_uid)
                )
                await session.execute(delete(SessionUnit).where(SessionUnit.id == grown_ep_uid))
                await session.flush()
                _expunge_derived_from_identity_map(
                    session,
                    thread_id=scope.thread_id,
                    only_unit_id=grown_ep_uid,
                )

                ep_row = _build_span_session_unit(
                    unit_kind="EPISODE",
                    ordinal=grown_ep.ordinal,
                    episode_id=grown_ep.episode_id,
                    episode_ordinal=grown_ep.ordinal,
                    window_id=None,
                    turn_ids=grown_ep.turn_ids,
                    scope=scope,
                    embedding_sig=embedding_sig,
                    collection_name=collection_name,
                    turns_by_id=index.turns_by_id,
                )
                session.add(ep_row)
                qdrant_upsert_batch.append(
                    (
                        ep_row,
                        grown_ep.centroid,
                        _sparse_qdrant_dict_for_span(grown_ep.turn_ids, index=index),
                    )
                )
                stats.units_written += 1

            # 5. Insert brand-new EPISODE units
            new_episode_units: list[SessionUnit] = []
            for ep in index.episodes[start_episode_ordinal:]:
                if not ep.turn_ids:
                    continue
                ep_row = _build_span_session_unit(
                    unit_kind="EPISODE",
                    ordinal=ep.ordinal,
                    episode_id=ep.episode_id,
                    episode_ordinal=ep.ordinal,
                    window_id=None,
                    turn_ids=ep.turn_ids,
                    scope=scope,
                    embedding_sig=embedding_sig,
                    collection_name=collection_name,
                    turns_by_id=index.turns_by_id,
                )
                new_episode_units.append(ep_row)
                qdrant_upsert_batch.append(
                    (
                        ep_row,
                        ep.centroid,
                        _sparse_qdrant_dict_for_span(ep.turn_ids, index=index),
                    )
                )
            if new_episode_units:
                session.add_all(new_episode_units)

            await session.flush()
            stats.units_written += (
                len(new_turn_units) + len(new_window_units) + len(new_episode_units)
            )

            # 6. Insert new SessionDecisionLedger entries in chronological flush order (lineage-safe)
            for dec_idx in range(start_decision_idx, len(index.decision_ledger)):
                entry = index.decision_ledger[dec_idx]
                u_id = turn_unit_ids.get(entry.turn_id)
                turn_obj = index.turns_by_id.get(entry.turn_id)
                if not u_id or turn_obj is None:
                    continue
                dec_id = decision_ids_by_turn[entry.turn_id]
                same_thread_superseded_dec_ids = [
                    decision_ids_by_turn[tid]
                    for tid in entry.supersedes_turn_ids
                    if tid in decision_ids_by_turn
                ]
                inherited_superseded_markers = [
                    f"inherited_turn:{tid}"
                    for tid in entry.supersedes_turn_ids
                    if tid not in decision_ids_by_turn and tid in inherited_turn_ids
                ]
                superseded_dec_ids = [
                    *same_thread_superseded_dec_ids,
                    *inherited_superseded_markers,
                ]
                primary_supersedes_id = (
                    same_thread_superseded_dec_ids[-1] if same_thread_superseded_dec_ids else None
                )
                valid_from = _ts_to_utc(entry.valid_from_ts)
                ledger_kind = "OPEN_ISSUE" if entry.epistemic_status == "OPEN_ISSUE" else "DECISION"
                companion_uids = [
                    turn_unit_ids[cid] for cid in entry.companion_turn_ids if cid in turn_unit_ids
                ]
                dec_row = SessionDecisionLedger(
                    id=dec_id,
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                    thread_id=scope.thread_id,
                    session_unit_id=u_id,
                    ledger_kind=ledger_kind,
                    topic_slot_key=entry.topic_slot_key,
                    turn_ordinal=entry.turn_ordinal,
                    epistemic_status=(
                        "NEGATED" if turn_obj.epistemic_status == "NEGATED" else turn_obj.epistemic_status
                    )
                    if entry.superseded_by_turn_id is None
                    else entry.epistemic_status,
                    summary_text=entry.summary_text,
                    entity_keys_json=list(entry.entity_keys),
                    superseded_entity_keys_json=list(turn_obj.superseded_entity_keys),
                    companion_unit_ids_json=companion_uids,
                    supersedes_id=primary_supersedes_id,
                    superseded_by_id=None,
                    supersedes_ids_json=superseded_dec_ids,
                    security_partition_ids_json=list(scope.security_partition_ids),
                    cited_source_ids_json=list(turn_obj.cited_source_ids),
                    valid_from=valid_from,
                    valid_to=(
                        valid_from
                        if turn_obj.epistemic_status == "NEGATED" and entry.superseded_by_turn_id is None
                        else SESSION_OPEN_ENDED_VALID_TO
                    ),
                )
                session.add(dec_row)
                await session.flush()

                # Close any prior OWN decision entries superseded or confirmed by `entry.turn_id`
                for prior_idx in range(inh_dec_k, dec_idx):
                    prior_entry = index.decision_ledger[prior_idx]
                    if prior_entry.superseded_by_turn_id != entry.turn_id:
                        continue
                    prior_dec_id = decision_ids_by_turn.get(prior_entry.turn_id)
                    if not prior_dec_id:
                        continue
                    prior_from = _ts_to_utc(prior_entry.valid_from_ts)
                    closed_to = max(prior_from, valid_from)
                    await session.execute(
                        update(SessionDecisionLedger)
                        .where(SessionDecisionLedger.id == prior_dec_id)
                        .values(
                            epistemic_status=prior_entry.epistemic_status,
                            superseded_by_id=dec_id,
                            valid_to=closed_to,
                        )
                    )

            # 7. Insert SessionGraphEdge rows for newly indexed turns
            new_edges: list[SessionGraphEdge] = []
            for turn in index.turns[start_turn_ordinal:]:
                new_edges.extend(
                    _build_turn_graph_edges(
                        turn,
                        index=index,
                        scope=scope,
                        turn_unit_ids=turn_unit_ids,
                    )
                )
            if new_edges:
                session.add_all(new_edges)
                await session.flush()

        if commit_if_clean and not caller_had_pending_state:
            await session.commit()
    except BaseException:
        if commit_if_clean and not caller_had_pending_state and session.in_transaction():
            await session.rollback()
        _expunge_derived_from_identity_map(session, thread_id=scope.thread_id)
        raise

    # Include any earlier `qdrant_point_id IS NULL` units so delta turns also heal past Qdrant outages
    if index.qdrant_rebuild_pending:
        qdrant_upsert_batch = _build_qdrant_items_from_index(
            index,
            scope=scope,
            embedding_sig=embedding_sig,
        )
    elif prior_unsynced_ids:
        queued_ids = {u.id for u, _, _ in qdrant_upsert_batch}
        carryover_ids = prior_unsynced_ids - queued_ids
        if carryover_ids:
            qdrant_upsert_batch = [
                *_build_qdrant_items_from_index(
                    index,
                    scope=scope,
                    embedding_sig=embedding_sig,
                    only_unit_ids=carryover_ids,
                ),
                *qdrant_upsert_batch,
            ]

    # 8. Sync derived vectors to Qdrant `session_units_{project_id}` (durable retry via qdrant_point_id IS NULL)
    client, should_close = _open_qdrant_client_context(session, qdrant_client)
    if client is not None:
        if mode == "rebuild":
            index.qdrant_rebuild_pending = True
        if qdrant_upsert_batch or index.qdrant_rebuild_pending:
            index.qdrant_sync_pending = True
        try:
            if index.qdrant_rebuild_pending:
                await delete_thread_points_from_qdrant(
                    client,
                    project_id=scope.project_id,
                    thread_id=scope.thread_id,
                )
                index.qdrant_rebuild_pending = False
            if qdrant_upsert_batch:
                _validate_vectors_for_persistence(
                    index=index,
                    qdrant_upsert_batch=qdrant_upsert_batch,
                    embedding_sig=embedding_sig,
                    embedder=embedder,
                )
                stats.qdrant_points_upserted = await upsert_session_units_to_qdrant(
                    client,
                    project_id=scope.project_id,
                    items=qdrant_upsert_batch,
                )
                await _mark_units_qdrant_synced(
                    session,
                    collection_name=collection_name,
                    synced_units=[u for u, _, _ in qdrant_upsert_batch],
                    commit_if_clean=commit_if_clean,
                    caller_had_pending_state=caller_had_pending_state,
                )
            index.qdrant_sync_pending = False
        except Exception as q_exc:  # noqa: BLE001
            index.qdrant_sync_pending = True
            log.warning(
                "Qdrant session unit sync deferred for retry project=%s thread=%s err=%s",
                scope.project_id,
                scope.thread_id,
                sanitize_error_message(q_exc),
            )
        finally:
            if should_close:
                await client.aclose()

    return stats


def _reconstruct_prefix_into_index(
    index: IncrementalSessionIndex,
    *,
    persisted_turn_units: Sequence[SessionUnit],
    persisted_decisions: Sequence[SessionDecisionLedger],
    project_records: Sequence[dict[str, Any]],
    hydrated_vectors: dict[str, tuple[float, ...]],
    embedder: Any | None,
    as_of_cutoff: bool = False,
) -> None:
    """Hydrate prefix `0 .. K - 1` into `IncrementalSessionIndex` without re-running NER or HTTP embedding."""
    target_mode = compute_embedding_signature(embedder)
    index.embedding_mode = target_mode
    base_turn_ordinal = len(index.turns)
    base_dec_idx = len(index.decision_ledger)

    unit_id_to_turn_id = {u.id: u.message_from_id for u in persisted_turn_units}
    dec_id_to_turn_id = {
        d.id: unit_id_to_turn_id[d.session_unit_id]
        for d in persisted_decisions
        if d.session_unit_id in unit_id_to_turn_id
    }

    for i, u_row in enumerate(persisted_turn_units):
        rec = project_records[i]
        t_id = str(rec["id"])
        content = normalize_session_text(str(rec["content"]))
        role = str(rec.get("role") or u_row.role or "user")
        created_at = rec.get("created_at")
        if isinstance(created_at, datetime):
            ts = created_at.timestamp()
            iso = created_at.astimezone(UTC).isoformat() if created_at.tzinfo else created_at.isoformat()
        else:
            vf = u_row.valid_from if u_row.valid_from.tzinfo else u_row.valid_from.replace(tzinfo=UTC)
            ts = vf.timestamp()
            iso = vf.isoformat()

        t_vec = hydrated_vectors.get(u_row.id)
        if t_vec is None:
            t_vec = deterministic_ngram_vector(content)

        mentions = tuple(
            EntityMention(
                key=str(m.get("key") or ""),
                surface=str(m.get("surface") or ""),
                soft_aliases=tuple(str(a) for a in (m.get("soft_aliases") or ()) if a),
                confidence=float(m.get("confidence", 0.9)),
            )
            for m in (u_row.entity_mentions_json or [])
            if isinstance(m, dict) and m.get("key")
        )

        sentences_list: list[IndexedSentence] = []
        raw_sent_meta = list(u_row.sentences_json or [])
        for s_idx, s_meta in enumerate(raw_sent_meta):
            if not isinstance(s_meta, dict):
                continue
            s_start = max(0, int(s_meta.get("start", 0)))
            s_end = min(len(content), max(s_start, int(s_meta.get("end", len(content)))))
            s_text = content[s_start:s_end].strip() or content
            cached_s_vec = (
                get_cached_text_embedding(s_text, model_signature=target_mode)
                if embedder is not None
                else None
            )
            if cached_s_vec is not None and len(cached_s_vec) == len(t_vec):
                s_vec = l2_normalize(cached_s_vec)
            elif embedder is None:
                s_vec = deterministic_ngram_vector(s_text)
            else:
                s_vec = t_vec
            sentences_list.append(
                IndexedSentence(
                    sentence_id=str(s_meta.get("sentence_id") or f"{t_id}:s{s_idx}"),
                    turn_id=t_id,
                    ordinal=int(s_meta.get("ordinal", s_idx)),
                    text=s_text,
                    entity_keys=tuple(str(k) for k in (s_meta.get("entity_keys") or ()) if k),
                    dense_vector=s_vec,
                )
            )
        if not sentences_list:
            sentences_list.append(
                IndexedSentence(
                    sentence_id=f"{t_id}:s0",
                    turn_id=t_id,
                    ordinal=0,
                    text=content,
                    entity_keys=tuple(m.key for m in mentions),
                    dense_vector=t_vec,
                )
            )

        term_freqs = {str(k): int(v) for k, v in (u_row.term_freqs_json or {}).items()}
        word_len = max(1, int(u_row.doc_word_len or 1))
        for word, tf in term_freqs.items():
            index.doc_freqs[word] = index.doc_freqs.get(word, 0) + 1
            index.term_postings.setdefault(word, []).append((t_id, tf))
        total_so_far = base_turn_ordinal + i + 1
        index.total_token_words += word_len
        avg_len = index.total_token_words / max(1, total_so_far)

        sparse_vec = sparse_dict_from_bm25(
            content,
            avg_doc_len=avg_len,
            corpus_doc_freqs=index.doc_freqs,
            total_corpus_docs=max(50, total_so_far),
        )
        content_terms = frozenset(extract_content_terms(content))

        ep_id = u_row.episode_id
        ep_obj = index.episodes_by_id.get(ep_id)
        if ep_obj is None:
            ep_obj = EpisodeSegment(
                episode_id=ep_id,
                thread_id=index.thread_id,
                ordinal=len(index.episodes),
                turn_ids=[t_id],
                centroid=list(t_vec),
                content_terms=set(content_terms),
                start_ts=ts,
                end_ts=ts,
            )
            index.episodes.append(ep_obj)
            index.episodes_by_id[ep_id] = ep_obj
        else:
            n_prev = len(ep_obj.turn_ids)
            ep_obj.turn_ids.append(t_id)
            if len(ep_obj.centroid) == len(t_vec):
                blended = [
                    (ep_obj.centroid[j] * n_prev + t_vec[j]) / (n_prev + 1)
                    for j in range(len(t_vec))
                ]
                ep_obj.centroid = list(l2_normalize(blended))
            ep_obj.content_terms.update(content_terms)
            ep_obj.end_ts = max(ep_obj.end_ts, ts)

        win_node = index._append_sliding_window(ep_obj, t_id)
        cited_sources = tuple(sorted(str(s) for s in (rec.get("cited_source_ids") or ()) if s))
        if cited_sources:
            index.cached_cited_source_ids.update(cited_sources)

        restored_turn_status = u_row.epistemic_status
        if as_of_cutoff and restored_turn_status == "SUPERSEDED":
            intrinsic_st, _, _ = classify_epistemic_state(content, role=role)
            restored_turn_status = intrinsic_st if intrinsic_st != "FACT" else "CONFIRMED"

        turn_obj = IndexedTurn(
            turn_id=t_id,
            thread_id=index.thread_id,
            ordinal=base_turn_ordinal + i,
            role=role,
            content=content,
            created_at_ts=ts,
            created_at_iso=iso,
            token_count=max(1, int(u_row.token_count or 1)),
            content_hash=u_row.content_hash,
            episode_id=ep_id,
            window_id=win_node.window_id,
            dense_vector=t_vec,
            sparse_bm25=sparse_vec,
            content_terms=content_terms,
            entities=mentions,
            sentences=tuple(sentences_list),
            term_freqs=term_freqs,
            doc_word_len=word_len,
            epistemic_status=restored_turn_status,  # type: ignore[arg-type]
            topic_slot_key=u_row.topic_slot_key,
            is_anaphoric_or_short=bool(u_row.is_anaphoric_or_short),
            cited_source_ids=cited_sources,
        )
        index.turns.append(turn_obj)
        index.turns_by_id[t_id] = turn_obj
        index._refresh_window_representation(win_node)
        index._register_turn_entities(turn_obj)

    # Restore decision ledger & turn supersession links (with Bi-Temporal As-Of cutoff restoration)
    for d_idx, d_row in enumerate(persisted_decisions):
        t_id = unit_id_to_turn_id.get(d_row.session_unit_id)
        turn_obj = index.turns_by_id.get(t_id or "")
        if not t_id or turn_obj is None:
            continue
        resolved_sup_tids: list[str] = []
        for sid in (d_row.supersedes_ids_json or []):
            sid_str = str(sid)
            if sid_str in dec_id_to_turn_id:
                resolved_sup_tids.append(dec_id_to_turn_id[sid_str])
            elif sid_str.startswith("inherited_turn:"):
                inh_tid = sid_str.removeprefix("inherited_turn:")
                if inh_tid in index.turns_by_id:
                    resolved_sup_tids.append(inh_tid)
        sup_turn_ids = tuple(dict.fromkeys(resolved_sup_tids))
        if not sup_turn_ids and d_row.supersedes_id and d_row.supersedes_id in dec_id_to_turn_id:
            sup_turn_ids = (dec_id_to_turn_id[d_row.supersedes_id],)
        sup_by_turn_id = (
            dec_id_to_turn_id.get(d_row.superseded_by_id) if d_row.superseded_by_id else None
        )
        comp_turn_ids = tuple(
            unit_id_to_turn_id[cuid]
            for cuid in (d_row.companion_unit_ids_json or [])
            if cuid in unit_id_to_turn_id
        )
        vf_aware = (
            d_row.valid_from if d_row.valid_from.tzinfo else d_row.valid_from.replace(tzinfo=UTC)
        )
        dec_status = d_row.epistemic_status
        vt_ts: float | None = None
        if as_of_cutoff and dec_status == "SUPERSEDED" and sup_by_turn_id is None:
            # Decision was superseded on ancestor AFTER our branch cutoff -> restore As-Of active state!
            intrinsic_st, _, _ = classify_epistemic_state(turn_obj.content, role=turn_obj.role)
            if d_row.ledger_kind == "OPEN_ISSUE":
                dec_status = "OPEN_ISSUE"
            elif intrinsic_st in {"CONFIRMED", "PROPOSED", "CONSIDERING", "OPEN_ISSUE", "NEGATED"}:
                dec_status = intrinsic_st
            else:
                dec_status = "CONFIRMED"
            turn_obj.epistemic_status = dec_status  # type: ignore[assignment]
            turn_obj.superseded_by_turn_id = None
            vt_ts = vf_aware.timestamp() if dec_status == "NEGATED" else None
        else:
            if not _is_open_ended(d_row.valid_to):
                vt_aware = (
                    d_row.valid_to if d_row.valid_to.tzinfo else d_row.valid_to.replace(tzinfo=UTC)
                )
                vt_ts = vt_aware.timestamp()

        turn_obj.superseded_entity_keys = tuple(
            str(k) for k in (d_row.superseded_entity_keys_json or []) if k
        )
        if sup_turn_ids:
            turn_obj.supersedes_turn_ids = sup_turn_ids
            # Apply cross-branch supersession onto inherited in-memory turns/decisions (never touches parent DB)
            for sup_tid in sup_turn_ids:
                prior_inh_turn = index.turns_by_id.get(sup_tid)
                if prior_inh_turn is not None and prior_inh_turn.ordinal < base_turn_ordinal:
                    prior_inh_turn.epistemic_status = "SUPERSEDED"
                    prior_inh_turn.superseded_by_turn_id = t_id
                    for prior_dec_idx, prior_dec in enumerate(index.decision_ledger):
                        if prior_dec.turn_id == sup_tid and prior_dec.superseded_by_turn_id is None:
                            index.decision_ledger[prior_dec_idx] = DecisionLedgerEntry(
                                entry_id=prior_dec.entry_id,
                                topic_slot_key=prior_dec.topic_slot_key,
                                turn_id=prior_dec.turn_id,
                                turn_ordinal=prior_dec.turn_ordinal,
                                epistemic_status="SUPERSEDED",
                                summary_text=prior_dec.summary_text,
                                entity_keys=prior_dec.entity_keys,
                                companion_turn_ids=prior_dec.companion_turn_ids,
                                valid_from_ts=prior_dec.valid_from_ts,
                                valid_to_ts=max(prior_dec.valid_from_ts, vf_aware.timestamp()),
                                superseded_by_turn_id=t_id,
                                supersedes_turn_ids=prior_dec.supersedes_turn_ids,
                            )
        if sup_by_turn_id and dec_status == "SUPERSEDED":
            turn_obj.epistemic_status = "SUPERSEDED"
            turn_obj.superseded_by_turn_id = sup_by_turn_id

        index.decision_ledger.append(
            DecisionLedgerEntry(
                entry_id=f"{index.thread_id}:dec{base_dec_idx + d_idx}",
                topic_slot_key=d_row.topic_slot_key,
                turn_id=t_id,
                turn_ordinal=turn_obj.ordinal,
                epistemic_status=dec_status,  # type: ignore[arg-type]
                summary_text=d_row.summary_text,
                entity_keys=tuple(str(k) for k in (d_row.entity_keys_json or []) if k),
                companion_turn_ids=comp_turn_ids,
                valid_from_ts=vf_aware.timestamp(),
                valid_to_ts=vt_ts,
                superseded_by_turn_id=sup_by_turn_id,
                supersedes_turn_ids=sup_turn_ids,
            )
        )


async def _hydrate_ancestor_segments_as_of(
    session: AsyncSession,
    *,
    index: IncrementalSessionIndex,
    ancestor_segments_records: Sequence[tuple[str, Sequence[dict[str, Any]]]],
    lineage_signature: str,
    scope: SessionPersistenceScope | None,
    embedder: Any | None,
    qdrant_client: httpx.AsyncClient | None,
) -> int:
    """Hydrate read-only ancestor segments As-Of their branch cutoffs (`0` HTTP re-embeds when persisted)."""
    if not ancestor_segments_records:
        return 0

    embedding_sig = compute_embedding_signature(embedder)
    expected_dim = declared_embedder_dim(embedder)
    hydrated_anc_turns = 0

    for anc_thread_id, seg_records in ancestor_segments_records:
        if not seg_records:
            continue
        if scope is None:
            await index.append_messages(seg_records, embedder=embedder)
            continue

        anc_units = list(
            (
                await session.execute(
                    select(SessionUnit)
                    .where(
                        SessionUnit.tenant_id == scope.tenant_id,
                        SessionUnit.project_id == scope.project_id,
                        SessionUnit.thread_id == anc_thread_id,
                        SessionUnit.unit_kind == "TURN",
                    )
                    .order_by(SessionUnit.ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
        anc_unit_by_msg_id = {
            str(u.message_from_id): u
            for u in anc_units
            if u.embedding_model_signature == embedding_sig
        }
        usable_units: list[SessionUnit] = []
        for rec in seg_records:
            cand_u = anc_unit_by_msg_id.get(str(rec["id"]))
            if cand_u is None:
                break
            expected_hash = hashlib.sha256(
                normalize_session_text(str(rec["content"])).encode("utf-8")
            ).hexdigest()
            if cand_u.content_hash != expected_hash:
                break
            usable_units.append(cand_u)

        usable_k = len(usable_units)
        if usable_k > 0:
            anc_vecs: dict[str, tuple[float, ...]] = {}
            if embedder is None:
                for idx_i, u_row in enumerate(usable_units):
                    c_norm = normalize_session_text(str(seg_records[idx_i]["content"]))
                    anc_vecs[u_row.id] = deterministic_ngram_vector(c_norm)
            else:
                missing_anc: list[tuple[SessionUnit, str]] = []
                for idx_i, u_row in enumerate(usable_units):
                    c_norm = normalize_session_text(str(seg_records[idx_i]["content"]))
                    cached_vec = get_cached_text_embedding(c_norm, model_signature=embedding_sig)
                    if (
                        cached_vec is not None
                        and len(cached_vec) > 0
                        and (expected_dim is None or len(cached_vec) == expected_dim)
                    ):
                        anc_vecs[u_row.id] = l2_normalize(cached_vec)
                    else:
                        missing_anc.append((u_row, c_norm))

                if missing_anc:
                    client, should_close = _open_qdrant_client_context(session, qdrant_client)
                    if client is not None:
                        try:
                            q_vecs = await fetch_thread_turn_vectors_from_qdrant(
                                client,
                                tenant_id=scope.tenant_id,
                                project_id=scope.project_id,
                                thread_id=anc_thread_id,
                                expected_embedding_sig=embedding_sig,
                                expected_dim=expected_dim,
                            )
                            for u_row, c_norm in missing_anc:
                                q_vec = q_vecs.get(u_row.id)
                                if q_vec is not None:
                                    anc_vecs[u_row.id] = q_vec
                                    put_cached_text_embedding(
                                        c_norm,
                                        q_vec,
                                        model_signature=embedding_sig,
                                    )
                        except Exception as q_exc:  # noqa: BLE001
                            log.warning(
                                "Ancestor Qdrant vector hydration fallback anc=%s err=%s",
                                anc_thread_id,
                                sanitize_error_message(q_exc),
                            )
                        finally:
                            if should_close:
                                await client.aclose()

                    still_missing = [
                        (u_row, c_norm)
                        for u_row, c_norm in missing_anc
                        if u_row.id not in anc_vecs
                    ]
                    if still_missing:
                        f_vecs, actual_sig = await embed_texts_with_provenance(
                            [txt for _, txt in still_missing],
                            embedder=embedder,
                        )
                        if actual_sig == embedding_sig:
                            for (u_row, _), f_vec in zip(still_missing, f_vecs, strict=True):
                                anc_vecs[u_row.id] = f_vec

            if len(anc_vecs) == usable_k:
                usable_uids = [u.id for u in usable_units]
                anc_decisions = list(
                    (
                        await session.execute(
                            select(SessionDecisionLedger)
                            .where(
                                SessionDecisionLedger.tenant_id == scope.tenant_id,
                                SessionDecisionLedger.project_id == scope.project_id,
                                SessionDecisionLedger.thread_id == anc_thread_id,
                                SessionDecisionLedger.session_unit_id.in_(usable_uids),
                            )
                            .order_by(
                                SessionDecisionLedger.turn_ordinal.asc(),
                                SessionDecisionLedger.created_at.asc(),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                _reconstruct_prefix_into_index(
                    index,
                    persisted_turn_units=usable_units,
                    persisted_decisions=anc_decisions,
                    project_records=seg_records[:usable_k],
                    hydrated_vectors=anc_vecs,
                    embedder=embedder,
                    as_of_cutoff=True,
                )
                hydrated_anc_turns += usable_k
            else:
                usable_k = 0

        if usable_k < len(seg_records):
            await index.append_messages(seg_records[usable_k:], embedder=embedder)

    index.seal_inherited_boundary()
    index.inherited_lineage_signature = lineage_signature
    return hydrated_anc_turns


async def hydrate_or_backfill_cold_session_index(
    session: AsyncSession,
    *,
    index: IncrementalSessionIndex,
    project_records: Sequence[dict[str, Any]],
    principal: VerifiedPrincipal,
    owner_key: str,
    requested_source_ids: Collection[str] | None = None,
    embedder: Any | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
    caller_had_pending_state: bool = False,
    ancestor_segments_records: Sequence[tuple[str, Sequence[dict[str, Any]]]] = (),
    lineage_signature: str = "",
) -> PersistenceSyncStats:
    """Cold-start entrypoint: hydrate verified prefix (`0` HTTP re-embeds), lazy-backfill, or reconcile."""
    try:
        scope = await resolve_persistence_scope(
            session,
            thread_id=index.thread_id,
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=requested_source_ids,
            qdrant_client=qdrant_client,
            caller_had_pending_state=caller_had_pending_state,
        )
    except ForbiddenError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_missing_session_tables_error(exc):
            if ancestor_segments_records:
                await _hydrate_ancestor_segments_as_of(
                    session,
                    index=index,
                    ancestor_segments_records=ancestor_segments_records,
                    lineage_signature=lineage_signature,
                    scope=None,
                    embedder=embedder,
                    qdrant_client=qdrant_client,
                )
            await index.append_messages(project_records, embedder=embedder)
            return PersistenceSyncStats(status="memory_only")
        raise

    if scope is None:
        index.persistence_active = False
        if ancestor_segments_records:
            await _hydrate_ancestor_segments_as_of(
                session,
                index=index,
                ancestor_segments_records=ancestor_segments_records,
                lineage_signature=lineage_signature,
                scope=None,
                embedder=embedder,
                qdrant_client=qdrant_client,
            )
        await index.append_messages(project_records, embedder=embedder)
        return PersistenceSyncStats(status="memory_only")

    index.persistence_active = True

    try:
        persisted_turn_units = list(
            (
                await session.execute(
                    select(SessionUnit)
                    .where(
                        SessionUnit.tenant_id == scope.tenant_id,
                        SessionUnit.project_id == scope.project_id,
                        SessionUnit.thread_id == scope.thread_id,
                        SessionUnit.unit_kind == "TURN",
                    )
                    .order_by(SessionUnit.ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        if _is_missing_session_tables_error(exc):
            index.persistence_active = False
            await index.append_messages(project_records, embedder=embedder)
            return PersistenceSyncStats(status="memory_only")
        raise

    hydrated_anc_count = 0
    if ancestor_segments_records:
        hydrated_anc_count = await _hydrate_ancestor_segments_as_of(
            session,
            index=index,
            ancestor_segments_records=ancestor_segments_records,
            lineage_signature=lineage_signature,
            scope=scope,
            embedder=embedder,
            qdrant_client=qdrant_client,
        )

    base_turn_ordinal = index.inherited_turn_count
    k_persisted = len(persisted_turn_units)
    n_visible = len(project_records)
    embedding_sig = compute_embedding_signature(embedder)
    expected_dim = declared_embedder_dim(embedder)

    if index.turns and index.embedding_mode != embedding_sig:
        await index.append_messages(project_records, embedder=None)
        return PersistenceSyncStats(status="degraded_unpersisted")

    # Empty own visible history: purge any lingering own derived state if k_persisted > 0
    if n_visible == 0:
        if k_persisted > 0:
            await purge_thread_session_rows(session, thread_id=scope.thread_id)
            if not caller_had_pending_state:
                await session.commit()
            await purge_thread_qdrant_points_for_projects(
                session,
                thread_id=scope.thread_id,
                project_ids=[scope.project_id],
                qdrant_client=qdrant_client,
            )
            return PersistenceSyncStats(
                status="reconciled_empty",
                hydrated_turns=hydrated_anc_count,
            )
        return PersistenceSyncStats(
            status="clean_empty" if hydrated_anc_count == 0 else "hydrated_clean",
            hydrated_turns=hydrated_anc_count,
        )

    # Verify whether `persisted_turn_units` is an exact, unbroken prefix of `project_records`
    prefix_ok = 0 < k_persisted <= n_visible
    if prefix_ok:
        for idx_i, u_row in enumerate(persisted_turn_units):
            rec = project_records[idx_i]
            expected_hash = hashlib.sha256(
                normalize_session_text(str(rec["content"])).encode("utf-8")
            ).hexdigest()
            if (
                int(u_row.ordinal) != base_turn_ordinal + idx_i
                or u_row.message_from_id != str(rec["id"])
                or u_row.content_hash != expected_hash
                or u_row.embedding_model_signature != embedding_sig
            ):
                prefix_ok = False
                break

    # Lazy Backfill (`k == 0`) or Reconciled Rebuild (delete / ACL revocation / model drift)
    if not prefix_ok:
        await index.append_messages(project_records, embedder=embedder)
        if index.turns and index.embedding_mode != embedding_sig:
            log.warning(
                "Skipping cold persistence write for thread=%s: embedder degraded (%s != %s)",
                scope.thread_id,
                index.embedding_mode,
                embedding_sig,
            )
            return PersistenceSyncStats(status="degraded_unpersisted")
        status_label = "lazy_backfill" if k_persisted == 0 else "reconciled_rebuild"
        sync_stats = await sync_session_index_to_storage(
            session,
            index=index,
            scope=scope,
            embedding_sig=embedding_sig,
            mode="rebuild",
            embedder=embedder,
            qdrant_client=qdrant_client,
            caller_had_pending_state=caller_had_pending_state,
        )
        sync_stats.status = status_label
        return sync_stats

    # Prefix `0 .. K - 1` is valid -> Hydrate from PostgreSQL + Qdrant with 0 HTTP re-embeddings!
    hydrated_vectors: dict[str, tuple[float, ...]] = {}
    qdrant_missing_during_hydration = False
    if embedder is None:
        for idx_i, u_row in enumerate(persisted_turn_units):
            content_norm = normalize_session_text(str(project_records[idx_i]["content"]))
            hydrated_vectors[u_row.id] = deterministic_ngram_vector(content_norm)
    else:
        missing_units: list[tuple[SessionUnit, str]] = []
        for idx_i, u_row in enumerate(persisted_turn_units):
            content_norm = normalize_session_text(str(project_records[idx_i]["content"]))
            cached_vec = get_cached_text_embedding(content_norm, model_signature=embedding_sig)
            if (
                cached_vec is not None
                and len(cached_vec) > 0
                and (expected_dim is None or len(cached_vec) == expected_dim)
            ):
                hydrated_vectors[u_row.id] = l2_normalize(cached_vec)
            else:
                missing_units.append((u_row, content_norm))

        if missing_units:
            client, should_close = _open_qdrant_client_context(session, qdrant_client)
            if client is not None:
                try:
                    q_vecs = await fetch_thread_turn_vectors_from_qdrant(
                        client,
                        tenant_id=scope.tenant_id,
                        project_id=scope.project_id,
                        thread_id=scope.thread_id,
                        expected_embedding_sig=embedding_sig,
                        expected_dim=expected_dim,
                    )
                    for u_row, content_norm in missing_units:
                        q_vec = q_vecs.get(u_row.id)
                        if q_vec is not None:
                            hydrated_vectors[u_row.id] = q_vec
                            put_cached_text_embedding(
                                content_norm,
                                q_vec,
                                model_signature=embedding_sig,
                            )
                        else:
                            qdrant_missing_during_hydration = True
                except Exception as q_exc:  # noqa: BLE001
                    index.qdrant_sync_pending = True
                    log.warning(
                        "Cold-start Qdrant vector hydration fallback thread=%s err=%s",
                        scope.thread_id,
                        sanitize_error_message(q_exc),
                    )
                finally:
                    if should_close:
                        await client.aclose()

            still_missing = [
                (u_row, content_norm)
                for u_row, content_norm in missing_units
                if u_row.id not in hydrated_vectors
            ]
            if still_missing:
                fallback_vecs, actual_sig = await embed_texts_with_provenance(
                    [txt for _, txt in still_missing],
                    embedder=embedder,
                )
                if actual_sig != embedding_sig:
                    await index.append_messages(project_records, embedder=None)
                    return PersistenceSyncStats(status="degraded_unpersisted")
                for (u_row, _), f_vec in zip(still_missing, fallback_vecs, strict=True):
                    hydrated_vectors[u_row.id] = f_vec

    persisted_decisions = list(
        (
            await session.execute(
                select(SessionDecisionLedger)
                .where(
                    SessionDecisionLedger.tenant_id == scope.tenant_id,
                    SessionDecisionLedger.project_id == scope.project_id,
                    SessionDecisionLedger.thread_id == scope.thread_id,
                )
                .order_by(SessionDecisionLedger.turn_ordinal.asc(), SessionDecisionLedger.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    _reconstruct_prefix_into_index(
        index,
        persisted_turn_units=persisted_turn_units,
        persisted_decisions=persisted_decisions,
        project_records=project_records[:k_persisted],
        hydrated_vectors=hydrated_vectors,
        embedder=embedder,
    )

    if k_persisted == n_visible:
        has_db_unsynced = (
            await session.execute(
                select(SessionUnit.id)
                .where(
                    SessionUnit.thread_id == scope.thread_id,
                    SessionUnit.qdrant_point_id.is_(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        repaired_pts = 0
        if has_db_unsynced or qdrant_missing_during_hydration:
            repaired_pts = await backfill_pending_qdrant_for_thread(
                session,
                index=index,
                scope=scope,
                embedding_sig=embedding_sig,
                embedder=embedder,
                qdrant_client=qdrant_client,
                caller_had_pending_state=caller_had_pending_state,
            )
        return PersistenceSyncStats(
            status="hydrated_clean",
            hydrated_turns=hydrated_anc_count + k_persisted,
            qdrant_points_upserted=repaired_pts,
        )

    # Append ONLY the unseen tail `K .. N - 1` (`O(Delta)`!)
    prev_win_count = len(index.windows)
    prev_ep_count = len(index.episodes)
    prev_ep_last_len = len(index.episodes[-1].turn_ids) if index.episodes else 0
    prev_dec_count = len(index.decision_ledger)
    prev_turn_state = {
        t.turn_id: (t.epistemic_status, t.superseded_by_turn_id) for t in index.turns
    }

    await index.append_messages(project_records[k_persisted:], embedder=embedder)
    if index.turns and index.embedding_mode != embedding_sig:
        log.warning(
            "Skipping delta persistence write for thread=%s: embedder degraded (%s != %s)",
            scope.thread_id,
            index.embedding_mode,
            embedding_sig,
        )
        return PersistenceSyncStats(status="degraded_unpersisted")

    mutated_prior_turns = {
        tid
        for tid, old_st in prev_turn_state.items()
        if (index.turns_by_id[tid].epistemic_status, index.turns_by_id[tid].superseded_by_turn_id)
        != old_st
    }
    grown_ep_ord = (
        prev_ep_count - 1
        if prev_ep_count > index.inherited_episode_count
        and len(index.episodes[prev_ep_count - 1].turn_ids) > prev_ep_last_len
        else None
    )

    delta_stats = await sync_session_index_to_storage(
        session,
        index=index,
        scope=scope,
        embedding_sig=embedding_sig,
        mode="delta",
        start_turn_ordinal=base_turn_ordinal + k_persisted,
        start_window_ordinal=prev_win_count,
        start_episode_ordinal=prev_ep_count,
        start_decision_idx=prev_dec_count,
        mutated_prior_turn_ids=mutated_prior_turns,
        grown_episode_ordinal=grown_ep_ord,
        embedder=embedder,
        qdrant_client=qdrant_client,
        caller_had_pending_state=caller_had_pending_state,
    )
    delta_stats.status = "hydrated_delta"
    delta_stats.hydrated_turns = hydrated_anc_count + k_persisted
    return delta_stats


async def backfill_thread_session_context(
    session: AsyncSession,
    *,
    thread_id: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    requested_source_ids: Collection[str] | None = None,
    embedder: Any | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
) -> PersistenceSyncStats:
    """Explicitly materialize or reconcile multi-view session context persistence for a thread."""
    from sag_api.services.agent_domain import filter_messages_for_scope
    from sag_api.services.session_context.runtime import (
        _extract_visible_records,
        load_ancestor_segments_visible_records,
        load_full_thread_messages_keyset,
        resolve_session_embedder,
    )

    caller_pending = bool(session.new or session.dirty or session.deleted)
    active_embedder = resolve_session_embedder(embedder)
    ancestor_records, lineage_sig, _ = await load_ancestor_segments_visible_records(
        session,
        thread_id=thread_id,
        principal=principal,
    )
    raw_messages = await load_full_thread_messages_keyset(session, thread_id=thread_id)
    scoped_messages = await filter_messages_for_scope(
        session,
        raw_messages,
        principal=principal,
        requested_source_ids=None,
    )
    visible_records, _ = _extract_visible_records(scoped_messages)
    temp_index = IncrementalSessionIndex(thread_id)
    return await hydrate_or_backfill_cold_session_index(
        session,
        index=temp_index,
        project_records=visible_records,
        principal=principal,
        owner_key=owner_key,
        requested_source_ids=requested_source_ids,
        embedder=active_embedder,
        qdrant_client=qdrant_client,
        caller_had_pending_state=caller_pending,
        ancestor_segments_records=ancestor_records,
        lineage_signature=lineage_sig,
    )
