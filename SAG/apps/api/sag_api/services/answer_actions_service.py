"""Domain services for AI answer feedback, project-scoped sharing, regeneration, and admin queries."""

from __future__ import annotations

import asyncio
import binascii
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_agent import AgentRuntime
from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import new_id
from sag_api.db.models import (
    Agent,
    AnswerFeedback,
    AnswerShare,
    Document,
    DocumentVersion,
    Message,
    SearchUnit,
    Thread,
)
from sag_api.enums import DocumentStatus, MessageRole, MessageStatus
from sag_api.schemas.agent import ThreadOut
from sag_api.schemas.answer_actions import (
    AdminFeedbackPageOut,
    AdminFeedbackRowOut,
    AdminFeedbackTotalsOut,
    FeedbackOut,
    FeedbackUpsertIn,
    OwnFeedbackOut,
    SharedAnswerOut,
    SharedPublicAnswerOut,
    SharedQuestionOut,
    ShareOut,
)
from sag_api.services.agent_domain import (
    MAX_LINEAGE_DEPTH,
    AskPlan,
    _message_source_ids,
    _next_monotonic_utc_now,
    _reject_json_constant,
    _segment_sql_filters,
    _urlsafe_decode,
    _urlsafe_encode,
    build_ask_context,
    compress_history,
    filter_messages_for_scope,
    get_agent,
    get_thread,
    load_segment_messages_chronological,
    resolve_thread_lineage_segments,
    resolve_user_display_name,
    serialize_thread_out,
)
from sag_api.services.agent_domain import (
    deduplicate_prior_regenerations as _deduplicate_prior_regenerations,
)
from sag_api.services.session_context import (
    HIDDEN_SCOPE_PLACEHOLDER,
    IncrementalSessionIndex,
    build_ecce_context_pack,
)
from sag_api.services.source_service import get_authorized_source_ids, versioned_source_ids

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_ADMIN_CURSOR_MAX_LENGTH = 1024
_ACTIVE_THREAD_GENERATIONS: set[str] = set()

_PUBLIC_CITATION_KEYS = (
    "n",
    "number",
    "kind",
    "source_id",
    "source_name",
    "document_id",
    "document_version_id",
    "document_name",
    "version_no",
    "chunk_id",
    "heading",
    "snippet",
    "summary",
    "label",
    "page_from",
    "page_to",
    "anchor",
    "url",
    "title",
    "source",
    "mapped",
    "claim_level",
)


def _has_active_runtime_thread_run(runtime: AgentRuntime | None, thread_id: str) -> bool:
    if runtime is None:
        return False
    active_map = getattr(runtime, "_active", None)
    if not isinstance(active_map, dict):
        return False
    for handle in active_map.values():
        if handle is not None and not handle.done:
            meta = getattr(handle.context, "metadata", None) or {}
            if meta.get("thread_id") == thread_id:
                return True
    return False


def claim_thread_generation(thread_id: str, runtime: AgentRuntime | None) -> None:
    if thread_id in _ACTIVE_THREAD_GENERATIONS or _has_active_runtime_thread_run(runtime, thread_id):
        raise ConflictError("Another generation is already active in this conversation")
    _ACTIVE_THREAD_GENERATIONS.add(thread_id)


def release_thread_generation(thread_id: str) -> None:
    _ACTIVE_THREAD_GENERATIONS.discard(thread_id)


def _resolve_single_project_scope(principal: VerifiedPrincipal) -> tuple[str, str]:
    tenant_id = (principal.tenant_id or principal.organization_id or "").strip()
    if getattr(principal, "conversation_scope", None) == "all":
        if (
            not tenant_id
            or not principal.allowed_project_ids
            or any(not pid.strip() for pid in principal.allowed_project_ids)
        ):
            raise ForbiddenError("Principal Project scope is empty")
        return tenant_id, "all"
    if not tenant_id or len(principal.allowed_project_ids) != 1:
        raise ForbiddenError("Principal does not carry an exact single-Project scope")
    project_id = next(iter(principal.allowed_project_ids))
    if not project_id.strip():
        raise ForbiddenError("Principal Project scope is empty")
    return tenant_id, project_id


def _sanitize_public_citations(raw_citations: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_citations, list):
        return []
    sanitized: list[dict[str, Any]] = []
    for item in raw_citations:
        if not isinstance(item, dict):
            continue
        cleaned = {k: item[k] for k in _PUBLIC_CITATION_KEYS if k in item and item[k] is not None}
        if cleaned:
            sanitized.append(cleaned)
    return sanitized


def _is_document_version_usable(
    ver: DocumentVersion,
    *,
    doc: Document,
    allowed_partitions: frozenset[str],
) -> bool:
    if (
        ver.status == "REVOKED"
        or ver.search_status == "REVOKED"
        or ver.knowledge_status == "REVOKED"
    ):
        return False
    meta = ver.metadata_json if isinstance(ver.metadata_json, dict) else {}
    if meta.get("revoked_at"):
        return False
    sec_partition = meta.get("security_partition_id")
    if doc.project_id is not None:
        if not isinstance(sec_partition, str) or not sec_partition.strip():
            return False
        if allowed_partitions and sec_partition not in allowed_partitions:
            return False
    elif sec_partition is not None and allowed_partitions and sec_partition not in allowed_partitions:
        return False
    return True


async def _verify_evidence_document_provenance(
    session: AsyncSession,
    *,
    message: Message,
    principal: VerifiedPrincipal,
    authorized_source_ids: set[str],
    extra_citations: Any = None,
    _path: frozenset[str] = frozenset(),
    _verified: set[str] | None = None,
) -> bool:
    """Verify every internal cited/matched Document and DocumentVersion is active, unrevoked, and in scope."""
    verified = _verified if _verified is not None else set()
    message_id = message.id
    if message_id in _path or len(_path) >= 32 or len(verified) + len(_path) >= 128:
        return False
    if message_id in verified:
        return True
    path = _path | {message_id}
    tenant_id = (principal.tenant_id or principal.organization_id or "").strip()
    allowed_projects = principal.allowed_project_ids
    allowed_partitions = principal.allowed_partition_ids
    if not tenant_id or not allowed_projects:
        return False

    evidence_items: list[Any] = []
    if message.citations is not None:
        if not isinstance(message.citations, list):
            return False
        evidence_items.extend(message.citations)
    if extra_citations is not None:
        if not isinstance(extra_citations, list):
            return False
        evidence_items.extend(extra_citations)

    session_message_ids: set[str] = set()
    cited_session_source_ids: set[str] = set()
    steps = getattr(message, "steps", None)
    if steps is not None:
        if not isinstance(steps, list):
            return False
        for step in steps:
            if not isinstance(step, dict):
                return False
            details = step.get("details")
            if isinstance(details, dict) and isinstance(details.get("matches"), list):
                evidence_items.extend(details["matches"])

            is_session_step = step.get("kind") == "session_context" or "session_citations" in step
            if not is_session_step:
                continue

            step_refs: set[str] = set()
            has_session_citations = "session_citations" in step
            if has_session_citations:
                session_citations = step["session_citations"]
                if not isinstance(session_citations, list) or not session_citations:
                    return False
                # Available but unmapped session records also reached the model context.
                for citation in session_citations:
                    if not isinstance(citation, dict):
                        return False
                    refs = citation.get("message_ids", [])
                    if not isinstance(refs, list):
                        return False
                    refs = [*refs]
                    if citation.get("primary_message_id") is not None:
                        refs.append(citation["primary_message_id"])
                    if not refs:
                        return False
                    for ref in refs:
                        if not isinstance(ref, str) or not _SAFE_ID_RE.fullmatch(ref):
                            return False
                        step_refs.add(ref)
                    raw_cited_srcs = citation.get("cited_source_ids")
                    if raw_cited_srcs is not None:
                        if not isinstance(raw_cited_srcs, list):
                            return False
                        for sid in raw_cited_srcs:
                            if not isinstance(sid, str) or not sid.strip():
                                return False
                            cited_session_source_ids.add(sid.strip())

            has_audit = "audit" in step
            if has_audit:
                audit = step["audit"]
                if not isinstance(audit, dict):
                    return False
                selected_groups = audit.get("selected_groups")
                if selected_groups is not None:
                    if not isinstance(selected_groups, list):
                        return False
                    for grp in selected_groups:
                        if not isinstance(grp, dict):
                            return False
                        grp_refs = grp.get("included_turn_ids", [])
                        if not isinstance(grp_refs, list):
                            return False
                        grp_refs = [*grp_refs]
                        if grp.get("primary_turn_id") is not None:
                            grp_refs.append(grp["primary_turn_id"])
                        if not grp_refs:
                            return False
                        for ref in grp_refs:
                            if not isinstance(ref, str) or not _SAFE_ID_RE.fullmatch(ref):
                                return False
                            step_refs.add(ref)
                recent_turn_ids = audit.get("recent_turn_ids")
                if recent_turn_ids is not None:
                    if not isinstance(recent_turn_ids, list):
                        return False
                    for ref in recent_turn_ids:
                        if not isinstance(ref, str) or not _SAFE_ID_RE.fullmatch(ref):
                            return False
                        step_refs.add(ref)

                used_session_tokens = any(
                    isinstance(audit.get(k), (int, float))
                    and not isinstance(audit.get(k), bool)
                    and audit[k] > 0
                    for k in (
                        "used_evidence_tokens",
                        "used_recent_tokens",
                        "total_output_history_tokens",
                    )
                )
                audit_mode = str(step.get("mode") or audit.get("mode") or "")
                if audit_mode == "shadow":
                    total_indexed = audit.get("total_indexed_turns", 0)
                    if (
                        isinstance(total_indexed, int)
                        and not isinstance(total_indexed, bool)
                        and total_indexed > 0
                        and message.created_at is not None
                    ):
                        msg_segments = await resolve_thread_lineage_segments(
                            session, message.thread_id
                        )
                        prior_rows: list[str] = []
                        for seg in reversed(msg_segments):
                            rem = 129 - len(prior_rows)
                            if rem <= 0:
                                break
                            seg_filters = _segment_sql_filters(seg)
                            seg_filters.extend(
                                [
                                    Message.id != message_id,
                                    Message.role == MessageRole.ASSISTANT,
                                    Message.status == MessageStatus.OK,
                                ]
                            )
                            if seg.thread_id == message.thread_id:
                                seg_filters.append(
                                    or_(
                                        Message.created_at < message.created_at,
                                        and_(
                                            Message.created_at == message.created_at,
                                            Message.id < message_id,
                                        ),
                                    )
                                )
                            prior_rows.extend(
                                str(pid)
                                for pid in (
                                    await session.scalars(
                                        select(Message.id)
                                        .where(*seg_filters)
                                        .order_by(Message.created_at.desc(), Message.id.desc())
                                        .limit(rem)
                                    )
                                ).all()
                            )
                        if len(prior_rows) > 128:
                            return False
                        step_refs.update(str(pid) for pid in prior_rows)
                elif used_session_tokens and not step_refs:
                    return False
            elif not has_session_citations:
                return False

            session_message_ids.update(step_refs)
            if len(session_message_ids) > 128:
                return False

    if cited_session_source_ids:
        authorized_cited = await get_authorized_source_ids(
            session,
            principal=principal,
            requested_source_ids=sorted(cited_session_source_ids),
        )
        if not cited_session_source_ids.issubset(authorized_cited):
            return False

    lineage_segments = (
        await resolve_thread_lineage_segments(session, message.thread_id)
        if session_message_ids
        else []
    )
    for ref in sorted(session_message_ids):
        prior = await session.get(Message, ref)
        if (
            prior is None
            or not any(seg.includes_message(prior) for seg in lineage_segments)
            or prior.role not in (MessageRole.USER, MessageRole.ASSISTANT)
            or prior.status != MessageStatus.OK
            or (
                prior.role == MessageRole.ASSISTANT
                and (prior.content or "").strip() == HIDDEN_SCOPE_PLACEHOLDER
            )
        ):
            return False
        prior_sources = _message_source_ids(prior) if prior.role == MessageRole.ASSISTANT else set()
        if prior_sources is None:
            return False
        prior_authorized = await get_authorized_source_ids(
            session, principal=principal, requested_source_ids=sorted(prior_sources)
        ) if prior_sources else set()
        if not prior_sources.issubset(prior_authorized):
            return False
        if not await _verify_evidence_document_provenance(
            session,
            message=prior,
            principal=principal,
            authorized_source_ids=prior_authorized,
            _path=path,
            _verified=verified,
        ):
            return False

    if not evidence_items:
        verified.add(message_id)
        return True

    unversioned_source_refs: set[str] = set()
    checked_pairs: set[tuple[str, str, str, int | None]] = set()

    for item in evidence_items:
        if not isinstance(item, dict):
            return False
        src_id = str(item["source_id"]).strip() if item.get("source_id") else ""
        doc_id = str(item["document_id"]).strip() if item.get("document_id") else ""
        ver_id = str(item["document_version_id"]).strip() if item.get("document_version_id") else ""
        chunk_id = str(item["chunk_id"]).strip() if item.get("chunk_id") else ""
        raw_ver_no = item.get("version_no")
        ver_no = (
            int(raw_ver_no)
            if isinstance(raw_ver_no, int) and not isinstance(raw_ver_no, bool)
            else None
        )

        if (
            item.get("kind") == "external"
            and not src_id
            and not doc_id
            and not ver_id
            and not chunk_id
        ):
            continue

        if src_id and src_id not in authorized_source_ids:
            return False

        if not doc_id and not ver_id and chunk_id:
            unit = await session.get(SearchUnit, chunk_id)
            if unit is not None:
                ver_id = str(unit.document_version_id)

        if not doc_id and not ver_id:
            if src_id:
                unversioned_source_refs.add(src_id)
                continue
            return False

        pair_key = (src_id, doc_id, ver_id, ver_no)
        if pair_key in checked_pairs:
            continue
        checked_pairs.add(pair_key)

        ver: DocumentVersion | None = None
        if ver_id:
            ver = await session.get(DocumentVersion, ver_id)
            if ver is None:
                return False
            if doc_id and ver.document_id != doc_id:
                return False
            if ver_no is not None and ver.version_no != ver_no:
                return False
            resolved_doc_id = ver.document_id
        else:
            resolved_doc_id = doc_id

        doc = await session.get(Document, resolved_doc_id)
        if doc is None and not ver_id and doc_id:
            doc = await session.scalar(
                select(Document)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.project_id.in_(allowed_projects),
                    Document.logical_source_id == doc_id,
                )
                .limit(1)
            )
        if doc is None:
            return False

        if not doc.is_active or doc.status in (DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED):
            return False
        if src_id and doc.source_id != src_id:
            return False
        if doc.source_id and doc.source_id not in authorized_source_ids:
            return False

        if doc.project_id is not None:
            if doc.tenant_id != tenant_id or doc.project_id not in allowed_projects:
                return False
        else:
            if not doc.source_id or doc.source_id not in authorized_source_ids:
                return False
            if doc.tenant_id not in (tenant_id, "tenant_continuum_default"):
                return False

        if ver is not None:
            if not _is_document_version_usable(ver, doc=doc, allowed_partitions=allowed_partitions):
                return False
        elif ver_no is not None:
            ver_by_no = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.version_no == ver_no,
                )
            )
            if ver_by_no is None or not _is_document_version_usable(
                ver_by_no, doc=doc, allowed_partitions=allowed_partitions
            ):
                return False
        else:
            versions = list(
                (
                    await session.scalars(
                        select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                    )
                ).all()
            )
            if doc.project_id is not None and not versions:
                return False
            if any(
                not _is_document_version_usable(v, doc=doc, allowed_partitions=allowed_partitions)
                for v in versions
            ):
                return False

    if unversioned_source_refs:
        # Sources carrying versioned documents require explicit document/version provenance.
        versioned = await versioned_source_ids(session, sorted(unversioned_source_refs))
        if unversioned_source_refs & versioned:
            return False
        for legacy_src_id in sorted(unversioned_source_refs):
            docs_for_src = list(
                (
                    await session.scalars(
                        select(Document).where(Document.source_id == legacy_src_id)
                    )
                ).all()
            )
            if docs_for_src and not all(
                d.is_active and d.status not in (DocumentStatus.DELETING, DocumentStatus.DELETE_FAILED)
                for d in docs_for_src
            ):
                return False

    verified.add(message_id)
    return True


async def _ensure_authorized_assistant_message(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
    require_non_empty: bool = False,
    allow_inherited: bool = True,
) -> tuple[Thread, Message, set[str]]:
    agent = await get_agent(session, agent_id, owner_key=owner_key)
    thread = await get_thread(session, agent.id, thread_id, owner_key=owner_key)
    message = await session.get(Message, message_id)
    if message is None:
        raise NotFoundError("Message not found")
    if message.thread_id != thread.id:
        segments = await resolve_thread_lineage_segments(session, thread)
        if not any(seg.includes_message(message) for seg in segments):
            raise NotFoundError("Message not found")
        if not allow_inherited:
            raise BadRequestError("Inherited messages from a parent chat cannot be modified in place")
    if message.role != MessageRole.ASSISTANT:
        raise BadRequestError("Target message must be an assistant answer")
    if message.status != MessageStatus.OK:
        raise BadRequestError("Target assistant message is not in a completed state")
    if require_non_empty and not (message.content or "").strip():
        raise BadRequestError("Assistant answer has no shareable content")
    if (message.content or "").strip() == HIDDEN_SCOPE_PLACEHOLDER:
        raise NotFoundError("Message not found")

    source_ids = _message_source_ids(message)
    if source_ids is None:
        raise NotFoundError("Message not found")
    authorized: set[str] = set()
    if source_ids:
        authorized = await get_authorized_source_ids(
            session,
            principal=principal,
            requested_source_ids=sorted(source_ids),
        )
        if not source_ids.issubset(authorized):
            raise NotFoundError("Message not found")
    if not await _verify_evidence_document_provenance(
        session,
        message=message,
        principal=principal,
        authorized_source_ids=authorized,
    ):
        raise NotFoundError("Message not found")
    return thread, message, source_ids


async def resolve_parent_question(
    session: AsyncSession,
    *,
    thread_id: str,
    answer_message: Message,
) -> Message:
    """Resolve the exact original USER question for an assistant message without guessing."""
    cursor_msg = answer_message
    visited: set[str] = set()
    for _ in range(32):
        if cursor_msg.id in visited:
            raise BadRequestError("Detected cyclic message regeneration relation")
        visited.add(cursor_msg.id)
        if cursor_msg.reply_to_message_id:
            question = await session.get(Message, cursor_msg.reply_to_message_id)
            if question is None or question.thread_id != thread_id or question.role != MessageRole.USER:
                raise BadRequestError("Original user question is unavailable")
            return question
        if not cursor_msg.regenerated_from_message_id:
            break
        prior = await session.get(Message, cursor_msg.regenerated_from_message_id)
        if prior is None or prior.thread_id != thread_id or prior.role != MessageRole.ASSISTANT:
            raise BadRequestError("Original parent answer lineage is broken")
        cursor_msg = prior
    else:
        raise BadRequestError("Message regeneration lineage exceeds the supported depth")

    # A nearby USER is insufficient: an earlier slow legacy run may still own this answer.
    rows = await session.execute(
        select(Message)
        .where(
            Message.thread_id == thread_id,
            or_(
                Message.created_at < cursor_msg.created_at,
                and_(Message.created_at == cursor_msg.created_at, Message.id < cursor_msg.id),
            ),
        )
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    prefix = list(rows.scalars().all())
    preceding = list(reversed(prefix[-2:]))
    if not preceding or preceding[0].role != MessageRole.USER:
        raise BadRequestError("Cannot resolve an unambiguous parent question for this legacy answer")
    if len(preceding) > 1 and preceding[1].role == MessageRole.USER:
        raise BadRequestError("Ambiguous consecutive user questions precede this legacy answer")
    if preceding[0].created_at == cursor_msg.created_at or (
        len(preceding) > 1 and preceding[1].created_at == preceding[0].created_at
    ):
        raise BadRequestError("Ambiguous parent question timestamps for this legacy answer")

    same_ts_sibling = await session.scalar(
        select(Message.id)
        .where(
            Message.thread_id == thread_id,
            Message.created_at == cursor_msg.created_at,
            Message.id != cursor_msg.id,
        )
        .limit(1)
    )
    if same_ts_sibling is not None:
        raise BadRequestError("Ambiguous concurrent message timestamps for this legacy answer")

    for idx in range(1, len(prefix)):
        prev_msg = prefix[idx - 1]
        curr_msg = prefix[idx]
        if (
            prev_msg.created_at == curr_msg.created_at
            and not curr_msg.reply_to_message_id
            and not prev_msg.reply_to_message_id
        ):
            raise BadRequestError("Ambiguous concurrent message timestamps in legacy history")

    candidate_question = preceding[0]
    unanswered: set[str] = set()
    for prior in prefix:
        if prior.role == MessageRole.USER:
            unanswered.add(prior.id)
        elif prior.role == MessageRole.ASSISTANT:
            if prior.reply_to_message_id:
                unanswered.discard(prior.reply_to_message_id)
            elif not prior.regenerated_from_message_id and len(unanswered) == 1:
                unanswered.clear()
    if unanswered != {candidate_question.id}:
        raise BadRequestError("Earlier unanswered user questions make this legacy answer ambiguous")
    claimed_by_other = await session.scalar(
        select(Message.id)
        .where(
            Message.thread_id == thread_id,
            Message.reply_to_message_id == candidate_question.id,
            Message.id != cursor_msg.id,
            Message.regenerated_from_message_id.is_(None),
        )
        .limit(1)
    )
    if claimed_by_other is not None:
        raise BadRequestError("Candidate parent question is already bound to another answer")
    return candidate_question


# ── Feedback ──────────────────────────────────────────────────────────
async def get_own_feedback(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
) -> OwnFeedbackOut:
    _resolve_single_project_scope(principal)
    _, message, _ = await _ensure_authorized_assistant_message(
        session,
        agent_id=agent_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_key=owner_key,
        principal=principal,
    )
    target_message_id = str(message.id)
    row = await session.scalar(
        select(AnswerFeedback).where(
            AnswerFeedback.message_id == target_message_id,
            AnswerFeedback.voter_subject == principal.subject,
        )
    )
    return OwnFeedbackOut(feedback=FeedbackOut.model_validate(row) if row is not None else None)


async def upsert_own_feedback(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
    payload: FeedbackUpsertIn,
) -> FeedbackOut:
    tenant_id, project_id = _resolve_single_project_scope(principal)
    _, message, _ = await _ensure_authorized_assistant_message(
        session,
        agent_id=agent_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_key=owner_key,
        principal=principal,
    )
    target_message_id = str(message.id)
    voter_subject = str(principal.subject)
    reason_category = payload.reason_category if payload.rating == "DISLIKE" else None
    reason_text = (
        payload.reason_text.strip()
        if payload.rating == "DISLIKE" and payload.reason_text is not None
        else None
    )
    now = datetime.now(UTC)

    dialect = session.get_bind().dialect.name
    insert_fn = postgresql_insert if dialect == "postgresql" else sqlite_insert
    statement = (
        insert_fn(AnswerFeedback)
        .values(
            id=new_id(),
            message_id=target_message_id,
            voter_subject=voter_subject,
            tenant_id=tenant_id,
            project_id=project_id,
            rating=payload.rating,
            reason_category=reason_category,
            reason_text=reason_text,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[AnswerFeedback.message_id, AnswerFeedback.voter_subject],
            set_={
                "rating": payload.rating,
                "reason_category": reason_category,
                "reason_text": reason_text,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "updated_at": now,
            },
        )
        .returning(AnswerFeedback)
        .execution_options(populate_existing=True)
    )
    try:
        row = (await session.scalars(statement)).one()
        dto = FeedbackOut.model_validate(row)
        await session.commit()
        return dto
    except IntegrityError as error:
        await session.rollback()
        raise NotFoundError("Message not found") from error


async def delete_own_feedback(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
) -> None:
    _resolve_single_project_scope(principal)
    _, message, _ = await _ensure_authorized_assistant_message(
        session,
        agent_id=agent_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_key=owner_key,
        principal=principal,
    )
    target_message_id = str(message.id)
    await session.execute(
        delete(AnswerFeedback).where(
            AnswerFeedback.message_id == target_message_id,
            AnswerFeedback.voter_subject == str(principal.subject),
        )
    )
    await session.commit()


# ── Shares ────────────────────────────────────────────────────────────
async def create_or_get_share(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
) -> tuple[ShareOut, bool]:
    tenant_id, project_id = _resolve_single_project_scope(principal)
    thread, message, source_ids = await _ensure_authorized_assistant_message(
        session,
        agent_id=agent_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_key=owner_key,
        principal=principal,
        require_non_empty=True,
    )
    question = await resolve_parent_question(session, thread_id=message.thread_id, answer_message=message)

    creator_subject = str(principal.subject)
    target_message_id = str(message.id)
    question_id = str(question.id)
    question_content = question.content or ""
    answer_content = message.content or ""
    sanitized_citations = _sanitize_public_citations(message.citations)
    sorted_sources = sorted(source_ids)

    for _ in range(3):
        existing = await session.scalar(
            select(AnswerShare).where(
                AnswerShare.creator_subject == creator_subject,
                AnswerShare.message_id == target_message_id,
                AnswerShare.revoked_at.is_(None),
            )
        )
        if existing is not None:
            if existing.tenant_id != tenant_id or existing.project_id != project_id:
                raise NotFoundError("Message not found")
            return ShareOut.model_validate(existing), False

        share = AnswerShare(
            creator_subject=creator_subject,
            message_id=target_message_id,
            question_id=question_id,
            tenant_id=tenant_id,
            project_id=project_id,
            question_content=question_content,
            answer_content=answer_content,
            citations=sanitized_citations,
            source_ids=sorted_sources,
        )
        session.add(share)
        try:
            await session.commit()
            await session.refresh(share)
            return ShareOut.model_validate(share), True
        except IntegrityError:
            await session.rollback()
            if await session.get(Message, target_message_id) is None:
                raise NotFoundError("Message not found") from None

    winner = await session.scalar(
        select(AnswerShare).where(
            AnswerShare.creator_subject == creator_subject,
            AnswerShare.message_id == target_message_id,
            AnswerShare.revoked_at.is_(None),
        )
    )
    if winner is None:
        raise ConflictError("Share state changed concurrently; please retry")
    if winner.tenant_id != tenant_id or winner.project_id != project_id:
        raise NotFoundError("Message not found")
    return ShareOut.model_validate(winner), False


async def read_project_share(
    session: AsyncSession,
    *,
    project_id: str,
    share_id: str,
    principal: VerifiedPrincipal,
) -> SharedAnswerOut:
    if not _SAFE_ID_RE.fullmatch(project_id) or not _SAFE_ID_RE.fullmatch(share_id):
        raise NotFoundError("Shared answer not found")
    tenant_id = (principal.tenant_id or principal.organization_id or "").strip()
    is_all_projects_scope = (
        project_id == "all"
        and getattr(principal, "conversation_scope", None) == "all"
        and bool(principal.allowed_project_ids)
    )
    if not tenant_id or (not is_all_projects_scope and project_id not in principal.allowed_project_ids):
        raise NotFoundError("Shared answer not found")

    share = await session.get(AnswerShare, share_id)
    if (
        share is None
        or share.revoked_at is not None
        or share.project_id != project_id
        or share.tenant_id != tenant_id
    ):
        raise NotFoundError("Shared answer not found")

    answer_msg = await session.get(Message, share.message_id)
    question_msg = await session.get(Message, share.question_id)
    if (
        answer_msg is None
        or question_msg is None
        or answer_msg.role != MessageRole.ASSISTANT
        or answer_msg.status != MessageStatus.OK
        or question_msg.role != MessageRole.USER
        or answer_msg.thread_id != question_msg.thread_id
        or (answer_msg.content or "").strip() == HIDDEN_SCOPE_PLACEHOLDER
    ):
        raise NotFoundError("Shared answer not found")

    live_source_ids = _message_source_ids(answer_msg)
    if live_source_ids is None:
        raise NotFoundError("Shared answer not found")
    stored_source_ids = {
        str(sid) for sid in (share.source_ids or []) if isinstance(sid, str) and sid.strip()
    }
    required_sources = live_source_ids | stored_source_ids
    authorized: set[str] = set()
    if required_sources:
        authorized = await get_authorized_source_ids(
            session,
            principal=principal,
            requested_source_ids=sorted(required_sources),
        )
        if not required_sources.issubset(authorized):
            raise NotFoundError("Shared answer not found")

    if not await _verify_evidence_document_provenance(
        session,
        message=answer_msg,
        principal=principal,
        authorized_source_ids=authorized,
        extra_citations=share.citations,
    ):
        raise NotFoundError("Shared answer not found")

    return SharedAnswerOut(
        share=ShareOut.model_validate(share),
        question=SharedQuestionOut(content=share.question_content),
        answer=SharedPublicAnswerOut(
            id=share.message_id,
            content=share.answer_content,
            citations=_sanitize_public_citations(share.citations),
        ),
    )


async def revoke_project_share(
    session: AsyncSession,
    *,
    project_id: str,
    share_id: str,
    principal: VerifiedPrincipal,
) -> None:
    if not _SAFE_ID_RE.fullmatch(project_id) or not _SAFE_ID_RE.fullmatch(share_id):
        raise NotFoundError("Shared answer not found")
    tenant_id = (principal.tenant_id or principal.organization_id or "").strip()
    is_all_projects_scope = (
        project_id == "all"
        and getattr(principal, "conversation_scope", None) == "all"
        and bool(principal.allowed_project_ids)
    )
    if not tenant_id or (not is_all_projects_scope and project_id not in principal.allowed_project_ids):
        raise NotFoundError("Shared answer not found")

    share = await session.get(AnswerShare, share_id)
    if (
        share is None
        or share.project_id != project_id
        or share.tenant_id != tenant_id
        or share.creator_subject != principal.subject
    ):
        raise NotFoundError("Shared answer not found")

    if share.revoked_at is None:
        now = datetime.now(UTC)
        await session.execute(
            update(AnswerShare)
            .where(AnswerShare.id == share_id, AnswerShare.revoked_at.is_(None))
            .values(revoked_at=now, updated_at=now)
        )
        await session.commit()


# ── Branch in New Chat ────────────────────────────────────────────────
async def branch_thread_from_message(
    session: AsyncSession,
    *,
    agent_id: str,
    thread_id: str,
    message_id: str,
    owner_key: str,
    principal: VerifiedPrincipal,
    title: str | None = None,
) -> ThreadOut:
    """Create an O(1) child `Thread` anchored at `message_id` (`0` message or vector copies)."""
    _resolve_single_project_scope(principal)
    source_thread, target_msg, _ = await _ensure_authorized_assistant_message(
        session,
        agent_id=agent_id,
        thread_id=thread_id,
        message_id=message_id,
        owner_key=owner_key,
        principal=principal,
        require_non_empty=False,
        allow_inherited=True,
    )
    await resolve_parent_question(
        session,
        thread_id=target_msg.thread_id,
        answer_message=target_msg,
    )

    origin_thread_id = str(target_msg.thread_id)
    origin_segments = await resolve_thread_lineage_segments(session, origin_thread_id)
    if len(origin_segments) >= MAX_LINEAGE_DEPTH:
        raise BadRequestError("Maximum conversation branch depth reached")

    resolved_title = (
        title.strip()[:200]
        if title is not None and title.strip()
        else (source_thread.title or "New chat")[:200]
    )
    ts = _next_monotonic_utc_now()
    branched_thread = Thread(
        agent_id=source_thread.agent_id,
        owner_key=owner_key,
        title=resolved_title,
        parent_thread_id=origin_thread_id,
        branch_point_message_id=str(target_msg.id),
        created_at=ts,
        updated_at=ts,
    )
    session.add(branched_thread)
    await session.commit()
    await session.refresh(branched_thread)
    return await serialize_thread_out(session, branched_thread)


# ── Regenerate ("Try again") ──────────────────────────────────────────
async def prepare_regenerate(
    session: AsyncSession,
    *,
    agent: Agent,
    thread: Thread,
    target_message_id: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    llm: Any = None,
    user_name: str | None = None,
) -> AskPlan:
    from sag_api.api.v1.attachments import attachment_path
    from sag_api.services.decision_router import route_query
    from sag_api.services.session_context.runtime import _extract_visible_records

    thread = await get_thread(session, agent.id, thread.id, owner_key=owner_key)
    segments = await resolve_thread_lineage_segments(session, thread)
    target = await session.get(Message, target_message_id)
    if target is None or not any(seg.includes_message(target) for seg in segments):
        raise NotFoundError("Message not found")
    if target.thread_id != thread.id:
        raise BadRequestError(
            "Inherited messages from a parent chat cannot be regenerated in place"
        )
    if target.role != MessageRole.ASSISTANT:
        raise BadRequestError("Only assistant answers can be regenerated")

    question = await resolve_parent_question(session, thread_id=thread.id, answer_message=target)
    query_text = question.content or ""
    raw_attachments = question.attachments if isinstance(question.attachments, list) else []
    if not query_text.strip() and not raw_attachments:
        raise BadRequestError("Original question has no content to regenerate")

    resolved_attachments: list[dict[str, Any]] = []
    for item in raw_attachments:
        aid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(aid, str) or not aid:
            continue
        path = attachment_path(aid, owner_key=owner_key)
        if path is not None:
            resolved_attachments.append(
                {
                    "id": aid,
                    "media_type": item.get("media_type") or "image/png",
                    "path": path,
                }
            )

    ancestor_deduped: list[Message] = []
    for anc_seg in segments[:-1]:
        anc_msgs = await load_segment_messages_chronological(
            session,
            anc_seg,
            roles=(MessageRole.USER, MessageRole.ASSISTANT),
            only_ok=True,
            max_messages=settings.history_load_limit,
        )
        ancestor_deduped.extend(_deduplicate_prior_regenerations(anc_msgs))

    rows = await session.execute(
        select(Message)
        .where(
            Message.thread_id == thread.id,
            Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
            Message.status == MessageStatus.OK,
            or_(
                Message.created_at < question.created_at,
                and_(Message.created_at == question.created_at, Message.id < question.id),
            ),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(settings.history_load_limit)
    )
    own_deduped = _deduplicate_prior_regenerations(list(reversed(rows.scalars().all())))
    prior_chronological = [*ancestor_deduped, *own_deduped][-settings.history_load_limit :]
    scoped_prior = await filter_messages_for_scope(
        session,
        prior_chronological,
        principal=principal,
        requested_source_ids=None,
    )

    budget_tokens = int(settings.llm_context_window * 0.4)
    audit_trace: dict[str, Any] | None = None
    route_decision: dict[str, Any] | None = None
    session_citations: list[dict[str, Any]] = []

    if settings.session_context_mode == "on" and scoped_prior:
        prior_records, _ = _extract_visible_records(scoped_prior)
        ephemeral_index = IncrementalSessionIndex(thread.id)
        await ephemeral_index.append_messages(prior_records)
        allowed_turns = set(ephemeral_index.turns_by_id.keys())
        decision = await asyncio.to_thread(
            route_query,
            query_text,
            {
                "has_thread_history": bool(allowed_turns),
                "turn_count": len(allowed_turns),
                "active_decisions_count": len(ephemeral_index.active_decisions),
            },
        )
        pack = await build_ecce_context_pack(
            ephemeral_index,
            query=query_text,
            budget_tokens=budget_tokens,
            route_decision=decision,
            allowed_turn_ids=allowed_turns,
            keep_recent_default=settings.history_keep_recent,
            legacy_load_limit=settings.history_load_limit,
        )
        history = pack.history_messages
        audit_trace = pack.audit_trace.to_dict()
        route_decision = dict(pack.audit_trace.route_decision)
        session_citations = list(pack.session_citations)
    else:
        raw_history = [{"role": m.role.value, "content": m.content} for m in scoped_prior]
        history = await compress_history(raw_history, llm=llm, budget_tokens=budget_tokens)

    effective_user_name = await resolve_user_display_name(
        session,
        user_name=user_name,
        principal=principal,
    )
    plan = build_ask_context(
        agent=agent,
        query=query_text,
        history=history,
        attachments=resolved_attachments or None,
        source_ids=None,
        user_name=effective_user_name,
    )
    plan.user_message_id = question.id
    plan.regenerated_from_message_id = target.id
    plan.session_audit_trace = audit_trace
    plan.route_decision = route_decision
    plan.session_citations = session_citations
    return plan


# ── Admin Feedback Telemetry ──────────────────────────────────────────
def _admin_filter_scope_hash(
    *,
    organization_id: str | None,
    project_id: str | None,
    rating: str | None,
    from_iso: str | None,
    to_iso: str | None,
) -> str:
    canonical = json.dumps(
        {
            "org": organization_id or "",
            "proj": project_id or "",
            "rating": rating or "",
            "from": from_iso or "",
            "to": to_iso or "",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _encode_admin_feedback_cursor(scope_hash: str, row: AnswerFeedback) -> str:
    payload = {
        "v": 1,
        "kind": "admin_feedback",
        "scope": scope_hash,
        "created_at": _as_utc(row.created_at).isoformat(timespec="microseconds"),
        "id": row.id,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    sig = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_urlsafe_encode(raw)}.{_urlsafe_encode(sig)}"


def _decode_admin_feedback_cursor(scope_hash: str, value: str) -> tuple[datetime, str]:
    def _invalid() -> BadRequestError:
        return BadRequestError("Invalid feedback cursor", code=ErrorCode.INVALID_CURSOR)

    if not value or len(value) > _ADMIN_CURSOR_MAX_LENGTH or value.count(".") != 1:
        raise _invalid()
    try:
        enc_payload, enc_sig = value.split(".", 1)
        raw = _urlsafe_decode(enc_payload)
        sig = _urlsafe_decode(enc_sig)
        if len(raw) > 512 or len(sig) != hashlib.sha256().digest_size:
            raise ValueError("invalid cursor length")
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("invalid cursor signature")
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "kind", "scope", "created_at", "id"}
            or payload["v"] != 1
            or isinstance(payload["v"], bool)
            or payload["kind"] != "admin_feedback"
            or payload["scope"] != scope_hash
            or not isinstance(payload["created_at"], str)
            or not isinstance(payload["id"], str)
            or not _SAFE_ID_RE.fullmatch(payload["id"])
        ):
            raise ValueError("invalid cursor claims")
        dt = datetime.fromisoformat(payload["created_at"])
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("cursor timestamp missing timezone")
        return dt.astimezone(UTC), payload["id"]
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise _invalid() from exc


def _parse_iso_utc(raw: str | None, field_name: str) -> tuple[datetime | None, str | None]:
    if raw is None:
        return None, None
    value = raw.strip()
    if not value:
        raise BadRequestError(f"Query parameter '{field_name}' cannot be blank")
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BadRequestError(f"Query parameter '{field_name}' must be an ISO-8601 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise BadRequestError(f"Query parameter '{field_name}' must include a UTC offset")
    utc_dt = dt.astimezone(UTC)
    return utc_dt, utc_dt.isoformat(timespec="microseconds")


async def list_admin_feedback(
    session: AsyncSession,
    *,
    organization_id: str | None = None,
    project_id: str | None = None,
    rating: str | None = None,
    from_raw: str | None = None,
    to_raw: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> AdminFeedbackPageOut:
    if organization_id is not None and not _SAFE_ID_RE.fullmatch(organization_id):
        raise BadRequestError("Invalid organizationId filter")
    if project_id is not None and not _SAFE_ID_RE.fullmatch(project_id):
        raise BadRequestError("Invalid projectId filter")
    if rating is not None and rating not in ("LIKE", "DISLIKE"):
        raise BadRequestError("Invalid rating filter")
    if limit < 1 or limit > 100:
        raise BadRequestError("Limit must be between 1 and 100")

    from_dt, from_iso = _parse_iso_utc(from_raw, "from")
    to_dt, to_iso = _parse_iso_utc(to_raw, "to")
    if from_dt is not None and to_dt is not None and from_dt >= to_dt:
        raise BadRequestError("'from' must be strictly earlier than 'to'")

    scope_hash = _admin_filter_scope_hash(
        organization_id=organization_id,
        project_id=project_id,
        rating=rating,
        from_iso=from_iso,
        to_iso=to_iso,
    )

    filters = []
    if organization_id is not None:
        filters.append(AnswerFeedback.tenant_id == organization_id)
    if project_id is not None:
        filters.append(AnswerFeedback.project_id == project_id)
    if rating is not None:
        filters.append(AnswerFeedback.rating == rating)
    if from_dt is not None:
        filters.append(AnswerFeedback.created_at >= from_dt)
    if to_dt is not None:
        filters.append(AnswerFeedback.created_at < to_dt)

    totals_stmt = select(
        func.coalesce(func.sum(case((AnswerFeedback.rating == "LIKE", 1), else_=0)), 0),
        func.coalesce(func.sum(case((AnswerFeedback.rating == "DISLIKE", 1), else_=0)), 0),
    )
    if filters:
        totals_stmt = totals_stmt.where(*filters)
    likes_count, dislikes_count = (await session.execute(totals_stmt)).one()

    items_stmt = select(AnswerFeedback)
    if filters:
        items_stmt = items_stmt.where(*filters)
    if cursor is not None:
        cur_dt, cur_id = _decode_admin_feedback_cursor(scope_hash, cursor)
        items_stmt = items_stmt.where(
            or_(
                AnswerFeedback.created_at < cur_dt,
                and_(AnswerFeedback.created_at == cur_dt, AnswerFeedback.id < cur_id),
            )
        )
    rows = list(
        (
            await session.execute(
                items_stmt.order_by(AnswerFeedback.created_at.desc(), AnswerFeedback.id.desc()).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = (
        _encode_admin_feedback_cursor(scope_hash, page_rows[-1])
        if has_more and page_rows
        else None
    )

    return AdminFeedbackPageOut(
        totals=AdminFeedbackTotalsOut(
            likes=int(likes_count or 0),
            dislikes=int(dislikes_count or 0),
        ),
        items=[
            AdminFeedbackRowOut(
                id=row.id,
                organization_id=row.tenant_id,
                project_id=row.project_id,
                message_id=row.message_id,
                rating=row.rating,  # type: ignore[arg-type]
                reason_category=row.reason_category,  # type: ignore[arg-type]
                reason_text=row.reason_text,
                created_at=_as_utc(row.created_at),
                updated_at=_as_utc(row.updated_at),
            )
            for row in page_rows
        ],
        next_cursor=next_cursor,
    )
