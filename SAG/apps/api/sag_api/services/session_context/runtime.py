"""Runtime integration bridging PostgreSQL `messages`, Continuum ACL, SiliconFlow routing,
durable multi-view persistence (`session_*` + Qdrant `session_units_{project_id}`), and the
SAG-ECCE Session Context Engine (`off` | `shadow` | `on`).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Collection
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ForbiddenError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.models.agent import Message
from sag_api.db.models.session_context import SessionUnit
from sag_api.enums import MessageRole, MessageStatus
from sag_api.services.decision_router import route_query
from sag_api.services.session_context import persistence as _persistence_mod
from sag_api.services.session_context.models import AblationMode, ContextAuditTrace, SessionContextPack
from sag_api.services.session_context.optimizer import build_ecce_context_pack
from sag_api.services.session_context.persistence import (
    PersistenceSyncStats,
    _is_missing_session_tables_error,
    backfill_pending_qdrant_for_thread,
    compute_embedding_signature,
    hydrate_or_backfill_cold_session_index,
    resolve_persistence_scope,
    sync_session_index_to_storage,
)
from sag_api.services.session_context.substrate import SESSION_INDEX_CACHE

_ORIG_SYNC_STORAGE = sync_session_index_to_storage

log = get_logger("sag.session_context")

HIDDEN_SCOPE_PLACEHOLDER = "[Answer hidden because its evidence is unavailable in the current source scope.]"
KEYSET_BATCH_SIZE = 500


class _ConfiguredHttpEmbedder:
    """Lightweight OpenAI/SiliconFlow-compatible HTTP embedder (0 local models)."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 5.0) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/embeddings"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def batch_generate(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()
        data = sorted(body.get("data") or [], key=lambda item: int(item.get("index", 0)))
        return [[float(v) for v in (row.get("embedding") or [])] for row in data]


def resolve_session_embedder(explicit_embedder: Any | None = None) -> Any | None:
    """Resolve optional HTTP embedder when `session_context_embedding_mode == 'configured_http'`."""
    if explicit_embedder is not None:
        return explicit_embedder
    if settings.session_context_embedding_mode != "configured_http":
        return None
    base_url = settings.effective_embedding_base_url
    api_key = settings.effective_embedding_api_key
    model = (settings.embedding_model or "").strip()
    if not base_url or not api_key or not model:
        return None
    return _ConfiguredHttpEmbedder(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=min(8.0, float(settings.embedding_timeout)),
    )


def log_session_audit_trace(thread_id: str, trace: ContextAuditTrace) -> None:
    """Emit structured telemetry log for `shadow` and `on` observability."""
    log.info(
        "ECCE session context trace thread=%s mode=%s persistence=%s scope=%s temporal=%s "
        "turns=%d ev_tokens=%d recent_tokens=%d budget=%d selected=%d rejected=%d latency_ms=%.2f",
        thread_id,
        trace.mode,
        trace.persistence_status,
        trace.memory_scope,
        trace.temporal_mode,
        trace.total_indexed_turns,
        trace.used_evidence_tokens,
        trace.used_recent_tokens,
        trace.budget_tokens,
        len(trace.selected_groups),
        len(trace.rejected_candidates),
        trace.latency_ms,
    )


def invalidate_session_thread_cache(thread_id: str) -> int:
    """Immediately evict cached session substrate for a thread on message/thread deletion."""
    return SESSION_INDEX_CACHE.invalidate_thread(thread_id)


def _compute_scope_signature(
    *,
    owner_key: str,
    principal: VerifiedPrincipal,
) -> str:
    tenant_id = str(getattr(principal, "tenant_id", "") or "")
    org_id = str(getattr(principal, "organization_id", "") or "")
    projects = ",".join(sorted(getattr(principal, "allowed_project_ids", ()) or ()))
    partitions = ",".join(sorted(getattr(principal, "allowed_partition_ids", ()) or ()))
    raw = f"{tenant_id}|{org_id}|{projects}|{partitions}|{owner_key}|*PROJECT*"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def count_thread_messages(
    session: AsyncSession,
    *,
    thread_id: str,
    exclude_id: str | None = None,
) -> int:
    """Fast index-only count of active user/assistant turns in a thread."""
    stmt = select(func.count(Message.id)).where(
        Message.thread_id == thread_id,
        Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
        Message.status == MessageStatus.OK,
    )
    if exclude_id:
        stmt = stmt.where(Message.id != exclude_id)
    res = await session.execute(stmt)
    return int(res.scalar_one() or 0)


async def load_full_thread_messages_keyset(
    session: AsyncSession,
    *,
    thread_id: str,
    exclude_id: str | None = None,
    exclude_ids: Collection[str] | None = None,
    max_messages: int | None = None,
    after_created_at: Any | None = None,
    after_id: str | None = None,
) -> list[Message]:
    """Load chronological thread history (full or O(Delta) cursor tail) via ix_messages_thread_created_id."""
    hard_cap = max_messages if max_messages is not None else settings.session_context_max_messages
    if hard_cap <= 0:
        return []
    collected: list[Message] = []
    # Subtract 1us on initial cursor when `exclude_ids` is used so SQLite 1s `CURRENT_TIMESTAMP`
    # strings ("YYYY-MM-DD HH:MM:SS") compare `>` against Python `.999999` parameters cleanly.
    last_created_at = (
        after_created_at - timedelta(microseconds=1)
        if (after_created_at is not None and exclude_ids is not None and after_id == "")
        else after_created_at
    )
    last_id = after_id

    while len(collected) < hard_cap:
        batch_limit = min(KEYSET_BATCH_SIZE, hard_cap - len(collected))
        stmt = select(Message).where(
            Message.thread_id == thread_id,
            Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
            Message.status == MessageStatus.OK,
        )
        if exclude_id:
            stmt = stmt.where(Message.id != exclude_id)
        if exclude_ids:
            stmt = stmt.where(Message.id.notin_(list(exclude_ids)))
        if last_created_at is not None and last_id is not None:
            stmt = stmt.where(
                or_(
                    Message.created_at > last_created_at,
                    and_(Message.created_at == last_created_at, Message.id > last_id),
                )
            )
        rows = await session.execute(stmt.order_by(Message.created_at.asc(), Message.id.asc()).limit(batch_limit))
        batch = list(rows.scalars().all())
        if not batch:
            break
        collected.extend(batch)
        last_created_at = batch[-1].created_at
        last_id = batch[-1].id
        if len(batch) < batch_limit:
            break

    return collected


def _extract_visible_records(scoped_messages: list[Message]) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    had_hidden = False
    for msg in scoped_messages:
        msg_id = str(getattr(msg, "id", "") or "")
        content = str(getattr(msg, "content", "") or "")
        if content == HIDDEN_SCOPE_PLACEHOLDER:
            had_hidden = True
            continue
        if not msg_id or not content:
            continue
        role_val = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
        cited_ids: set[str] = {
            str(c["source_id"])
            for c in (getattr(msg, "citations", None) or [])
            if isinstance(c, dict) and c.get("source_id")
        }
        for step in (getattr(msg, "steps", None) or []):
            if not isinstance(step, dict):
                continue
            details = step.get("details")
            if isinstance(details, dict) and details.get("scope") == "knowledge":
                for src in (details.get("sources") or []):
                    if isinstance(src, dict) and src.get("id"):
                        cited_ids.add(str(src["id"]))
        records.append(
            {
                "id": msg_id,
                "role": role_val,
                "content": content,
                "created_at": getattr(msg, "created_at", None),
                "cited_source_ids": sorted(cited_ids),
            }
        )
    return records, had_hidden


async def _cached_sources_still_authorized(
    session: AsyncSession,
    *,
    cached_source_ids: set[str],
    principal: VerifiedPrincipal,
) -> bool:
    if not cached_source_ids:
        return True
    from sag_api.services.source_service import get_authorized_source_ids

    authorized = await get_authorized_source_ids(
        session,
        principal=principal,
        requested_source_ids=sorted(cached_source_ids),
    )
    return cached_source_ids.issubset(authorized)


async def _invoke_sync_storage(*args: Any, **kwargs: Any) -> PersistenceSyncStats:
    fn = (
        sync_session_index_to_storage
        if sync_session_index_to_storage is not _ORIG_SYNC_STORAGE
        else _persistence_mod.sync_session_index_to_storage
    )
    return await fn(*args, **kwargs)


async def _inspect_thread_persisted_state(
    session: AsyncSession,
    *,
    thread_id: str,
) -> tuple[int, int] | None:
    """Return `(persisted_turn_units, unsynced_qdrant_units)` from `session_units`, or None if unavailable."""
    if not settings.session_context_persistence_enabled or not thread_id:
        return None
    try:
        row = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(case((SessionUnit.unit_kind == "TURN", 1), else_=0)),
                        0,
                    ),
                    func.coalesce(
                        func.sum(case((SessionUnit.qdrant_point_id.is_(None), 1), else_=0)),
                        0,
                    ),
                ).where(SessionUnit.thread_id == thread_id)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_session_tables_error(exc):
            return None
        raise


async def _resolve_scope_safe(
    session: AsyncSession,
    *,
    thread_id: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    requested_source_ids: Collection[str] | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
    caller_had_pending_state: bool = False,
) -> Any | None:
    try:
        return await resolve_persistence_scope(
            session,
            thread_id=thread_id,
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
            return None
        raise


async def load_ancestor_segments_visible_records(
    session: AsyncSession,
    *,
    thread_id: str,
    principal: VerifiedPrincipal,
) -> tuple[list[tuple[str, list[dict[str, Any]]]], str, bool]:
    """Load live ACL-filtered visible records for inherited ancestor segments (`O(1)` no-op on root threads)."""
    from sag_api.services.agent_domain import (
        deduplicate_prior_regenerations,
        filter_messages_for_scope,
        load_segment_messages_chronological,
        resolve_thread_lineage_segments,
    )
    from sag_api.services.session_context.extractor import normalize_session_text

    try:
        segments = await resolve_thread_lineage_segments(session, thread_id)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_session_tables_error(exc):
            return [], "", False
        raise

    if len(segments) <= 1:
        return [], "", False

    ancestor_segments = segments[:-1]
    remaining_cap = settings.session_context_max_messages
    collected_per_seg: list[tuple[str, list[dict[str, Any]]]] = []
    hasher = hashlib.sha256()
    any_hidden = False

    for seg in ancestor_segments:
        if remaining_cap <= 0:
            break
        seg_raw = await load_segment_messages_chronological(
            session,
            seg,
            roles=(MessageRole.USER, MessageRole.ASSISTANT),
            only_ok=True,
            max_messages=remaining_cap,
        )
        seg_deduped = deduplicate_prior_regenerations(seg_raw)
        remaining_cap -= len(seg_deduped)
        if not seg_deduped:
            continue
        seg_scoped = await filter_messages_for_scope(
            session,
            seg_deduped,
            principal=principal,
            requested_source_ids=None,
        )
        seg_records, seg_hidden = _extract_visible_records(seg_scoped)
        if seg_hidden:
            any_hidden = True
        hasher.update(f"seg:{seg.thread_id}:".encode())
        for rec in seg_records:
            c_hash = hashlib.sha256(
                normalize_session_text(str(rec["content"])).encode("utf-8")
            ).hexdigest()
            hasher.update(f"{rec['id']}:{c_hash}|".encode())
        collected_per_seg.append((seg.thread_id, seg_records))

    lineage_sig = hasher.hexdigest()[:32]
    return collected_per_seg, lineage_sig, any_hidden


async def prepare_session_context_for_ask(
    session: AsyncSession,
    *,
    thread_id: str,
    exclude_message_id: str,
    query: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    requested_source_ids: list[str] | None,
    budget_tokens: int,
    ablation: AblationMode = "V5_ecce_full",
    embedder: Any | None = None,
    qdrant_client: httpx.AsyncClient | None = None,
) -> SessionContextPack:
    """Build ACL-enforced, SiliconFlow-routed, durably-synced ECCE context pack."""
    from sag_api.services.agent_domain import filter_messages_for_scope

    caller_had_pending_state = bool(session.new or session.dirty or session.deleted)
    active_embedder = resolve_session_embedder(embedder)
    target_emb_sig = compute_embedding_signature(active_embedder)
    projects_sig = ",".join(sorted(getattr(principal, "allowed_project_ids", ()) or ())) or "default"
    scope_key = f"{projects_sig}:{owner_key}"
    scope_sig = _compute_scope_signature(
        owner_key=owner_key,
        principal=principal,
    )
    ancestor_records, lineage_sig, _ = await load_ancestor_segments_visible_records(
        session,
        thread_id=thread_id,
        principal=principal,
    )

    index = SESSION_INDEX_CACHE.get_or_create(
        scope_key=scope_key,
        thread_id=thread_id,
        scope_signature=scope_sig,
    )

    can_use_delta = (
        bool(index.turns)
        and index.inherited_lineage_signature == lineage_sig
        and index.last_db_created_at is not None
        and index.last_db_id is not None
        and not index.had_hidden_acl_turns
        and index.persisted_embedding_signature == target_emb_sig
        and (index.persistence_active or not requested_source_ids)
    )
    db_count = 0
    persisted_state: tuple[int, int] | None = None
    if can_use_delta:
        db_count = await count_thread_messages(
            session,
            thread_id=thread_id,
            exclude_id=exclude_message_id,
        )
        sources_ok = await _cached_sources_still_authorized(
            session,
            cached_source_ids=index.cached_cited_source_ids,
            principal=principal,
        )
        persisted_state = await _inspect_thread_persisted_state(
            session,
            thread_id=thread_id,
        )
        persisted_turns_ok = (
            not index.persistence_active
            or persisted_state is None
            or persisted_state[0] == (len(index.turns) - index.inherited_turn_count)
        )
        if db_count < index.seen_db_count or not sources_ok or not persisted_turns_ok:
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            index = SESSION_INDEX_CACHE.get_or_create(
                scope_key=scope_key,
                thread_id=thread_id,
                scope_signature=scope_sig,
            )
            can_use_delta = False

    persistence_stats = PersistenceSyncStats(status="memory_only")
    delta_raw: list[Message] = []
    evict_after_pack = False

    if can_use_delta:
        # Exclude already-seen IDs sharing the boundary second (`boundary_ts_ids`) so 1s-granularity
        # timestamps + random UUID4s return ONLY genuine unseen delta rows in 1 cursor query.
        remaining_cap = max(0, settings.session_context_max_messages - index.seen_db_count)
        fetched_tail = await load_full_thread_messages_keyset(
            session,
            thread_id=thread_id,
            exclude_id=exclude_message_id,
            exclude_ids=index.boundary_ts_ids,
            max_messages=remaining_cap,
            after_created_at=index.last_db_created_at,
            after_id="",
        )
        delta_raw = [m for m in fetched_tail if str(m.id) not in index.seen_raw_ids]
        expected_total = min(settings.session_context_max_messages, db_count)
        if len(index.seen_raw_ids) + len(delta_raw) != expected_total:
            # Prefix deletion (e.g. raw SQL DELETE + new ask) or out-of-order backdated insert -> reconcile!
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            index = SESSION_INDEX_CACHE.get_or_create(
                scope_key=scope_key,
                thread_id=thread_id,
                scope_signature=scope_sig,
            )
            can_use_delta = False

    if can_use_delta:
        try:
            if delta_raw:
                delta_scoped = await filter_messages_for_scope(
                    session,
                    delta_raw,
                    principal=principal,
                    requested_source_ids=None,
                )
                delta_records, delta_hidden = _extract_visible_records(delta_scoped)

                prev_turn_count = len(index.turns)
                prev_win_count = len(index.windows)
                prev_ep_count = len(index.episodes)
                prev_ep_last_len = len(index.episodes[-1].turn_ids) if index.episodes else 0
                prev_dec_count = len(index.decision_ledger)
                prev_turn_state = {
                    t.turn_id: (t.epistemic_status, t.superseded_by_turn_id) for t in index.turns
                }

                # Stage next cursor values locally; commit onto `index` ONLY AFTER DB write succeeds!
                next_last_dt = delta_raw[-1].created_at
                if (
                    index.last_db_created_at is not None
                    and next_last_dt is not None
                    and abs((next_last_dt - index.last_db_created_at).total_seconds()) <= 1.0
                ):
                    next_boundary_ts_ids = set(index.boundary_ts_ids) | {
                        str(m.id) for m in delta_raw
                    }
                else:
                    next_boundary_ts_ids = {
                        str(m.id)
                        for m in delta_raw
                        if next_last_dt is not None
                        and m.created_at is not None
                        and abs((next_last_dt - m.created_at).total_seconds()) <= 1.0
                    }
                next_last_id = str(delta_raw[-1].id)
                next_seen_raw_ids = set(index.seen_raw_ids) | {str(m.id) for m in delta_raw}

                added = await index.append_messages(delta_records, embedder=active_embedder)
                if added > 0 and index.embedding_mode != target_emb_sig:
                    # HTTP embedder degraded to deterministic fallback -> never poison DB/Qdrant
                    persistence_stats = PersistenceSyncStats(status="degraded_unpersisted")
                    evict_after_pack = True
                elif added > 0:
                    scope = await _resolve_scope_safe(
                        session,
                        thread_id=thread_id,
                        principal=principal,
                        owner_key=owner_key,
                        requested_source_ids=requested_source_ids,
                        qdrant_client=qdrant_client,
                        caller_had_pending_state=caller_had_pending_state,
                    )
                    if scope is not None:
                        if not index.persistence_active:
                            persistence_stats = await _invoke_sync_storage(
                                session,
                                index=index,
                                scope=scope,
                                embedding_sig=target_emb_sig,
                                mode="rebuild",
                                embedder=active_embedder,
                                qdrant_client=qdrant_client,
                                caller_had_pending_state=caller_had_pending_state,
                            )
                            persistence_stats.status = "lazy_backfill"
                        else:
                            mutated_prior_turns = {
                                tid
                                for tid, old_st in prev_turn_state.items()
                                if (
                                    index.turns_by_id[tid].epistemic_status,
                                    index.turns_by_id[tid].superseded_by_turn_id,
                                )
                                != old_st
                            }
                            grown_ep_ord = (
                                prev_ep_count - 1
                                if prev_ep_count > index.inherited_episode_count
                                and len(index.episodes[prev_ep_count - 1].turn_ids) > prev_ep_last_len
                                else None
                            )
                            persistence_stats = await _invoke_sync_storage(
                                session,
                                index=index,
                                scope=scope,
                                embedding_sig=target_emb_sig,
                                mode="delta",
                                start_turn_ordinal=prev_turn_count,
                                start_window_ordinal=prev_win_count,
                                start_episode_ordinal=prev_ep_count,
                                start_decision_idx=prev_dec_count,
                                mutated_prior_turn_ids=mutated_prior_turns,
                                grown_episode_ordinal=grown_ep_ord,
                                embedder=active_embedder,
                                qdrant_client=qdrant_client,
                                caller_had_pending_state=caller_had_pending_state,
                            )
                            persistence_stats.status = "delta_synced"
                        index.persistence_active = True
                    else:
                        index.persistence_active = False
                        persistence_stats = PersistenceSyncStats(status="memory_only")

                    index.persisted_embedding_signature = target_emb_sig
                    if delta_hidden:
                        index.had_hidden_acl_turns = True
                    index.boundary_ts_ids = next_boundary_ts_ids
                    index.last_db_created_at = next_last_dt
                    index.last_db_id = next_last_id
                    index.seen_raw_ids = next_seen_raw_ids
                    index.seen_db_count = len(next_seen_raw_ids)
                else:
                    if delta_hidden:
                        index.had_hidden_acl_turns = True
                    index.boundary_ts_ids = next_boundary_ts_ids
                    index.last_db_created_at = next_last_dt
                    index.last_db_id = next_last_id
                    index.seen_raw_ids = next_seen_raw_ids
                    index.seen_db_count = len(next_seen_raw_ids)
                    persistence_stats = PersistenceSyncStats(status="warm_clean")

            if not delta_raw or persistence_stats.status == "warm_clean":
                needs_qdrant_retry = index.qdrant_sync_pending or (
                    index.persistence_active
                    and persisted_state is not None
                    and persisted_state[1] > 0
                )
                if needs_qdrant_retry:
                    scope = await _resolve_scope_safe(
                        session,
                        thread_id=thread_id,
                        principal=principal,
                        owner_key=owner_key,
                        requested_source_ids=requested_source_ids,
                        qdrant_client=qdrant_client,
                        caller_had_pending_state=caller_had_pending_state,
                    )
                    if scope is not None:
                        repaired_pts = await backfill_pending_qdrant_for_thread(
                            session,
                            index=index,
                            scope=scope,
                            embedding_sig=target_emb_sig,
                            embedder=active_embedder,
                            qdrant_client=qdrant_client,
                            caller_had_pending_state=caller_had_pending_state,
                        )
                        if repaired_pts > 0:
                            persistence_stats = PersistenceSyncStats(
                                status="qdrant_backfilled",
                                qdrant_points_upserted=repaired_pts,
                            )
                        else:
                            persistence_stats = PersistenceSyncStats(status="warm_clean")
                    else:
                        persistence_stats = PersistenceSyncStats(status="warm_clean")
                else:
                    persistence_stats = PersistenceSyncStats(status="warm_clean")
        except BaseException:
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            raise
    else:
        if index.turns:
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            index = SESSION_INDEX_CACHE.get_or_create(
                scope_key=scope_key,
                thread_id=thread_id,
                scope_signature=scope_sig,
            )

        try:
            raw_messages = await load_full_thread_messages_keyset(
                session,
                thread_id=thread_id,
                exclude_id=exclude_message_id,
            )
            project_scoped = await filter_messages_for_scope(
                session,
                raw_messages,
                principal=principal,
                requested_source_ids=None,
            )
            project_records, had_hidden = _extract_visible_records(project_scoped)

            persistence_stats = await hydrate_or_backfill_cold_session_index(
                session,
                index=index,
                project_records=project_records,
                principal=principal,
                owner_key=owner_key,
                requested_source_ids=requested_source_ids,
                embedder=active_embedder,
                qdrant_client=qdrant_client,
                caller_had_pending_state=caller_had_pending_state,
                ancestor_segments_records=ancestor_records,
                lineage_signature=lineage_sig,
            )
            if persistence_stats.status == "degraded_unpersisted":
                evict_after_pack = True
            else:
                index.persisted_embedding_signature = target_emb_sig
                index.had_hidden_acl_turns = had_hidden
                index.seen_raw_ids = {str(m.id) for m in raw_messages}
                index.seen_db_count = len(index.seen_raw_ids)
                if raw_messages:
                    last_dt = raw_messages[-1].created_at
                    index.last_db_created_at = last_dt
                    index.last_db_id = str(raw_messages[-1].id)
                    index.boundary_ts_ids = {
                        str(m.id)
                        for m in raw_messages
                        if last_dt is not None
                        and m.created_at is not None
                        and abs((last_dt - m.created_at).total_seconds()) <= 1.0
                    }
        except BaseException:
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)
            raise

    if requested_source_ids is None:
        allowed_turn_ids = set(index.turns_by_id.keys())
    else:
        req_src_set = set(requested_source_ids)
        allowed_turn_ids = {
            t.turn_id
            for t in index.turns
            if t.role != "assistant" or set(t.cited_source_ids).issubset(req_src_set)
        }

    # SiliconFlow System One routing (run off event loop; deterministic fallback if disabled/unconfigured)
    route_ctx = {
        "has_thread_history": bool(allowed_turn_ids),
        "turn_count": len(allowed_turn_ids),
        "active_decisions_count": len(
            [d for d in index.active_decisions if d.turn_id in allowed_turn_ids]
        ),
    }
    decision = await asyncio.to_thread(route_query, query, route_ctx)

    try:
        pack = await build_ecce_context_pack(
            index,
            query=query,
            budget_tokens=budget_tokens,
            ablation=ablation,
            route_decision=decision,
            allowed_turn_ids=allowed_turn_ids,
            embedder=active_embedder,
            keep_recent_default=settings.history_keep_recent,
            legacy_load_limit=settings.history_load_limit,
        )
    finally:
        if evict_after_pack:
            SESSION_INDEX_CACHE.invalidate_thread(thread_id)

    pack.audit_trace.persistence_status = persistence_stats.status
    pack.audit_trace.persisted_units_written = persistence_stats.units_written
    pack.audit_trace.qdrant_points_upserted = persistence_stats.qdrant_points_upserted
    return pack
