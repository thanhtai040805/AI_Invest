"""Agent 领域逻辑：CRUD、绑定（信源/MCP）、上下文解析、多源 fan-out 对话。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.branding import DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_NAME
from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.models import Agent, AgentBinding, Message, Source, Thread, User
from sag_api.enums import BindingTargetType, MessageRole, MessageStatus
from sag_api.generation import build_agent_messages, build_prompt_preview
from sag_api.generation.prompt import estimate_tokens
from sag_api.schemas.agent import ThreadOut
from sag_api.services.session_context import (
    collect_thread_persistence_projects,
    invalidate_session_thread_cache,
    log_session_audit_trace,
    prepare_session_context_for_ask,
    purge_thread_qdrant_points_for_projects,
    purge_thread_session_rows,
)
from sag_api.services.source_service import get_authorized_source_ids, search_source_candidates

_log = get_logger("sag.agent_domain")

_DEFAULT_TITLES = {"New chat", "Cuộc trò chuyện mới"}
_KNOWLEDGE_TOOL_NAMES = frozenset({"search_context", "get_entity"})
THREAD_PAGE_DEFAULT = 6
THREAD_PAGE_MAX = 100
MESSAGE_PAGE_DEFAULT = 40
MESSAGE_PAGE_MAX = 100
MESSAGE_CURSOR_MAX_LENGTH = 2048
MAX_LINEAGE_DEPTH = 32
_MESSAGE_CURSOR_ID = re.compile(r"[0-9a-f]{32}\Z")
_LAST_MONOTONIC_UTC_US: int = 0


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _next_monotonic_utc_now() -> datetime:
    """Return a strictly increasing microsecond UTC timestamp across rapid writes."""
    global _LAST_MONOTONIC_UTC_US
    now_us = time.time_ns() // 1_000
    if now_us <= _LAST_MONOTONIC_UTC_US:
        now_us = _LAST_MONOTONIC_UTC_US + 1
    _LAST_MONOTONIC_UTC_US = now_us
    sec, us = divmod(now_us, 1_000_000)
    return datetime.fromtimestamp(sec, tz=UTC).replace(microsecond=us)


@dataclass(frozen=True, slots=True)
class LineageSegment:
    thread_id: str
    cutoff_created_at: datetime | None = None
    cutoff_message_id: str | None = None
    cutoff_reply_to_message_id: str | None = None
    is_inherited: bool = False

    def includes_message(self, message: Message) -> bool:
        if message.thread_id != self.thread_id:
            return False
        if self.cutoff_created_at is None or self.cutoff_message_id is None:
            return True
        if message.created_at is None:
            return False
        msg_dt = _ensure_utc(message.created_at)
        cut_dt = _ensure_utc(self.cutoff_created_at)
        if (msg_dt, str(message.id)) > (cut_dt, self.cutoff_message_id):
            return False
        if (
            self.cutoff_reply_to_message_id
            and message.role == MessageRole.ASSISTANT
            and message.reply_to_message_id == self.cutoff_reply_to_message_id
            and str(message.id) != self.cutoff_message_id
        ):
            return False
        return True


def _segment_sql_filters(segment: LineageSegment) -> list[Any]:
    filters: list[Any] = [Message.thread_id == segment.thread_id]
    if segment.cutoff_created_at is not None and segment.cutoff_message_id is not None:
        filters.append(
            or_(
                Message.created_at < segment.cutoff_created_at,
                and_(
                    Message.created_at == segment.cutoff_created_at,
                    Message.id <= segment.cutoff_message_id,
                ),
            )
        )
        if segment.cutoff_reply_to_message_id:
            filters.append(
                or_(
                    Message.role != MessageRole.ASSISTANT,
                    Message.reply_to_message_id.is_(None),
                    Message.reply_to_message_id != segment.cutoff_reply_to_message_id,
                    Message.id == segment.cutoff_message_id,
                )
            )
    return filters


async def resolve_thread_lineage_segments(
    session: AsyncSession,
    thread_or_id: Thread | str,
) -> list[LineageSegment]:
    """Resolve root-to-leaf `LineageSegment` chain (`[ancestor_0, ..., leaf]`) with cycle/scope guards."""
    if isinstance(thread_or_id, Thread):
        leaf = thread_or_id
    else:
        leaf = await session.get(Thread, str(thread_or_id))
        if leaf is None:
            return [LineageSegment(thread_id=str(thread_or_id), is_inherited=False)]

    if leaf.parent_thread_id is None or leaf.branch_point_message_id is None:
        return [LineageSegment(thread_id=leaf.id, is_inherited=False)]

    segments_rev: list[LineageSegment] = [LineageSegment(thread_id=leaf.id, is_inherited=False)]
    visited: set[str] = {leaf.id}
    cur_thread = leaf

    for _ in range(MAX_LINEAGE_DEPTH):
        parent_id = cur_thread.parent_thread_id
        bp_msg_id = cur_thread.branch_point_message_id
        if parent_id is None or bp_msg_id is None:
            break
        if parent_id in visited:
            raise ValidationError("Detected cyclic conversation branch lineage")
        visited.add(parent_id)

        parent = await session.get(Thread, parent_id)
        if (
            parent is None
            or parent.agent_id != leaf.agent_id
            or parent.owner_key != leaf.owner_key
        ):
            raise ValidationError("Broken conversation branch lineage scope")

        bp_msg = await session.get(Message, bp_msg_id)
        if bp_msg is None or bp_msg.thread_id != parent.id:
            raise ValidationError("Broken conversation branch point message")

        segments_rev.append(
            LineageSegment(
                thread_id=parent.id,
                cutoff_created_at=bp_msg.created_at,
                cutoff_message_id=bp_msg.id,
                cutoff_reply_to_message_id=bp_msg.reply_to_message_id,
                is_inherited=True,
            )
        )
        cur_thread = parent
    else:
        if cur_thread.parent_thread_id is not None:
            raise ValidationError("Conversation branch lineage exceeds maximum supported depth")

    return list(reversed(segments_rev))


async def load_segment_messages_chronological(
    session: AsyncSession,
    segment: LineageSegment,
    *,
    roles: tuple[MessageRole, ...] | None = None,
    only_ok: bool = False,
    max_messages: int | None = None,
) -> list[Message]:
    """Load chronological messages inside a single `LineageSegment` via keyset batches."""
    cap = max_messages if max_messages is not None else settings.session_context_max_messages
    if cap <= 0:
        return []
    collected: list[Message] = []
    last_dt: datetime | None = None
    last_id: str | None = None

    while len(collected) < cap:
        batch_limit = min(500, cap - len(collected))
        filters = _segment_sql_filters(segment)
        if roles is not None:
            filters.append(Message.role.in_(roles))
        if only_ok:
            filters.append(Message.status == MessageStatus.OK)
        if last_dt is not None and last_id is not None:
            filters.append(
                or_(
                    Message.created_at > last_dt,
                    and_(Message.created_at == last_dt, Message.id > last_id),
                )
            )
        rows = await session.execute(
            select(Message)
            .where(*filters)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(batch_limit)
        )
        batch = list(rows.scalars().all())
        if not batch:
            break
        collected.extend(batch)
        last_dt = batch[-1].created_at
        last_id = batch[-1].id
        if len(batch) < batch_limit:
            break
    return collected


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: list[Message]
    next_cursor: str | None
    has_more: bool


def _message_cursor_scope(thread_id: str) -> str:
    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    if not value:
        raise ValueError("empty base64 value")
    padded = f"{value}{'=' * (-len(value) % 4)}".encode("ascii")
    decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    if _urlsafe_encode(decoded) != value:
        raise ValueError("non-canonical base64 value")
    return decoded


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


def _encode_message_cursor(thread_id: str, message: Message) -> str:
    payload = {
        "v": 1,
        "kind": "messages",
        "scope": _message_cursor_scope(thread_id),
        "created_at": message.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "id": message.id,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_urlsafe_encode(raw)}.{_urlsafe_encode(signature)}"


def _decode_message_cursor(thread_id: str, value: str) -> tuple[datetime, str]:
    def invalid() -> ValidationError:
        return ValidationError("Invalid message cursor", code=ErrorCode.INVALID_CURSOR)

    if not value or len(value) > MESSAGE_CURSOR_MAX_LENGTH or value.count(".") != 1:
        raise invalid()
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        raw = _urlsafe_decode(encoded_payload)
        signature = _urlsafe_decode(encoded_signature)
        if len(raw) > 512 or len(signature) != hashlib.sha256().digest_size:
            raise ValueError("invalid cursor size")
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid cursor signature")
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        expected_keys = {"v", "kind", "scope", "created_at", "id"}
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("invalid cursor payload")
        if (
            not isinstance(payload["v"], int)
            or isinstance(payload["v"], bool)
            or payload["v"] != 1
            or payload["kind"] != "messages"
            or payload["scope"] != _message_cursor_scope(thread_id)
            or not isinstance(payload["created_at"], str)
            or not isinstance(payload["id"], str)
            or _MESSAGE_CURSOR_ID.fullmatch(payload["id"]) is None
        ):
            raise ValueError("invalid cursor scope or values")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("cursor timestamp must include a timezone")
        return created_at.astimezone(UTC), payload["id"]
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise invalid() from error


def _encode_message_cursor_v2(thread_id: str, seg_idx: int, message: Message) -> str:
    payload = {
        "v": 2,
        "kind": "messages",
        "scope": _message_cursor_scope(thread_id),
        "seg_idx": int(seg_idx),
        "created_at": _ensure_utc(message.created_at).isoformat(timespec="microseconds"),
        "id": message.id,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_urlsafe_encode(raw)}.{_urlsafe_encode(signature)}"


def _decode_message_cursor_v2(
    thread_id: str,
    value: str,
    *,
    max_seg_idx: int,
) -> tuple[int, datetime, str]:
    def invalid() -> ValidationError:
        return ValidationError("Invalid message cursor", code=ErrorCode.INVALID_CURSOR)

    if not value or len(value) > MESSAGE_CURSOR_MAX_LENGTH or value.count(".") != 1:
        raise invalid()
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        raw = _urlsafe_decode(encoded_payload)
        signature = _urlsafe_decode(encoded_signature)
        if len(raw) > 512 or len(signature) != hashlib.sha256().digest_size:
            raise ValueError("invalid cursor size")
        expected = hmac.new(settings.secret_key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid cursor signature")
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        if not isinstance(payload, dict) or payload.get("kind") != "messages":
            raise ValueError("invalid cursor payload")
        ver = payload.get("v")
        if ver == 1 and not isinstance(ver, bool):
            dt, msg_id = _decode_message_cursor(thread_id, value)
            return max_seg_idx, dt, msg_id
        expected_keys = {"v", "kind", "scope", "seg_idx", "created_at", "id"}
        if (
            ver != 2
            or isinstance(ver, bool)
            or set(payload) != expected_keys
            or payload["scope"] != _message_cursor_scope(thread_id)
            or not isinstance(payload["seg_idx"], int)
            or isinstance(payload["seg_idx"], bool)
            or not (0 <= payload["seg_idx"] <= max_seg_idx)
            or not isinstance(payload["created_at"], str)
            or not isinstance(payload["id"], str)
            or _MESSAGE_CURSOR_ID.fullmatch(payload["id"]) is None
        ):
            raise ValueError("invalid v2 cursor scope or values")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("cursor timestamp must include a timezone")
        return payload["seg_idx"], created_at.astimezone(UTC), payload["id"]
    except ValidationError:
        raise
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise invalid() from error


# ── CRUD ────────────────────────────────────────────────────────────
async def list_agents(session: AsyncSession, *, owner_key: str) -> list[Agent]:
    rows = await session.execute(
        select(Agent).where(or_(Agent.owner_key == owner_key, Agent.is_default.is_(True)))
        .order_by(Agent.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_agent(session: AsyncSession, agent_id: str, *, owner_key: str, write: bool = False) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or (agent.owner_key != owner_key and (write or not agent.is_default)):
        raise NotFoundError("Agent not found")
    if not write:
        agent = await _upgrade_legacy_default_agent_if_needed(session, agent)
    return agent


async def create_agent(
    session: AsyncSession, *, owner_key: str, name: str, avatar: str = "", persona: dict | None = None,
) -> Agent:
    name = name.strip()
    if not name:
        raise ValidationError("Agent name cannot be empty")
    agent = Agent(owner_key=owner_key, name=name, avatar=avatar or name[:1], persona=persona or {})
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(
    session: AsyncSession,
    agent_id: str,
    *,
    owner_key: str,
    name: str | None = None,
    avatar: str | None = None,
    persona: dict | None = None,
) -> Agent:
    agent = await get_agent(session, agent_id, owner_key=owner_key, write=True)
    if name is not None:
        agent.name = name
    if avatar is not None:
        agent.avatar = avatar
    if persona is not None:
        agent.persona = persona
    await session.commit()
    await session.refresh(agent)
    return agent


_DEFAULT_GREETING = "I am ready. Upload documents to the knowledge base, or ask me any question."
_DEFAULT_PERSONA = {"greeting": _DEFAULT_GREETING, "system_prompt": ""}


_LEGACY_DEFAULT_AGENT_NAMES = frozenset({"sag", "zleap"})
_LEGACY_DEFAULT_AGENT_AVATARS = frozenset({"s", "S", DEFAULT_AGENT_AVATAR})


def _is_legacy_default_agent(agent: Agent) -> bool:
    """Recognize untouched legacy default assistant rows ('sag' or 'Zleap') without overwriting user edits."""
    normalized_name = (agent.name or "").strip().casefold()
    return (
        bool(agent.is_default)
        and normalized_name in _LEGACY_DEFAULT_AGENT_NAMES
        and (agent.avatar or "") in _LEGACY_DEFAULT_AGENT_AVATARS
        and (agent.persona or {}) == _DEFAULT_PERSONA
    )


async def _upgrade_legacy_default_agent_if_needed(session: AsyncSession, agent: Agent) -> Agent:
    if _is_legacy_default_agent(agent) and not session.new and not session.dirty and not session.deleted:
        agent.name = DEFAULT_AGENT_NAME
        agent.avatar = DEFAULT_AGENT_AVATAR
        await session.commit()
        await session.refresh(agent)
    return agent


async def get_default_agent(session: AsyncSession) -> Agent:
    """默认 agent（开箱即用的主对话入口）：get-or-create，幂等。"""
    agent = await session.scalar(select(Agent).where(Agent.is_default.is_(True)))
    if agent is not None:
        if _is_legacy_default_agent(agent):
            agent.name = DEFAULT_AGENT_NAME
            agent.avatar = DEFAULT_AGENT_AVATAR
            await session.commit()
            await session.refresh(agent)
        return agent
    agent = Agent(
        name=DEFAULT_AGENT_NAME,
        avatar=DEFAULT_AGENT_AVATAR,
        is_default=True,
        persona=dict(_DEFAULT_PERSONA),
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def delete_agent(session: AsyncSession, agent_id: str, *, owner_key: str) -> None:
    agent = await get_agent(session, agent_id, owner_key=owner_key, write=True)
    agent_threads = list(
        (await session.scalars(select(Thread).where(Thread.agent_id == agent.id))).all()
    )
    qdrant_cleanup_targets: list[tuple[str, list[str]]] = []
    if agent_threads:
        by_id = {t.id: t for t in agent_threads}
        remaining = set(by_id.keys())
        while remaining:
            referenced_parents = {
                by_id[tid].parent_thread_id
                for tid in remaining
                if by_id[tid].parent_thread_id in remaining
            }
            leaves = sorted(remaining - referenced_parents) or sorted(remaining)
            for leaf_id in leaves:
                persisted_projects = await collect_thread_persistence_projects(
                    session, thread_id=leaf_id
                )
                qdrant_cleanup_targets.append((leaf_id, persisted_projects))
                await purge_thread_session_rows(session, thread_id=leaf_id)
                await session.delete(by_id[leaf_id])
                invalidate_session_thread_cache(leaf_id)
                remaining.discard(leaf_id)
            await session.flush()
    await session.delete(agent)
    await session.commit()
    for gc_tid, gc_projects in qdrant_cleanup_targets:
        await purge_thread_qdrant_points_for_projects(
            session, thread_id=gc_tid, project_ids=gc_projects
        )


# ── 绑定（信源 / MCP server）────────────────────────────────────────
async def list_bindings(session: AsyncSession, agent: Agent) -> list[AgentBinding]:
    rows = await session.execute(select(AgentBinding).where(AgentBinding.agent_id == agent.id))
    return list(rows.scalars().all())


async def add_binding(
    session: AsyncSession,
    agent: Agent,
    *,
    target_type: BindingTargetType,
    target_id: str,
    config: dict | None = None,
) -> AgentBinding:
    config = config or {}
    if target_type == BindingTargetType.SOURCE:
        if await session.get(Source, target_id) is None:
            raise NotFoundError("Source not found")
    elif target_type == BindingTargetType.MCP_SERVER:
        if not (config.get("url") or config.get("command")):
            raise ValidationError("MCP server requires url or command")
        target_id = target_id or (config.get("name") or config.get("url") or "mcp")
    exists = await session.scalar(
        select(AgentBinding).where(
            AgentBinding.agent_id == agent.id,
            AgentBinding.target_type == target_type,
            AgentBinding.target_id == target_id,
        )
    )
    if exists is not None:
        raise ConflictError("Target already bound")
    binding = AgentBinding(agent_id=agent.id, target_type=target_type, target_id=target_id, config=config)
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return binding


async def remove_binding(session: AsyncSession, agent: Agent, binding_id: str) -> None:
    binding = await session.get(AgentBinding, binding_id)
    if binding is None or binding.agent_id != agent.id:
        raise NotFoundError("Binding not found")
    await session.delete(binding)
    await session.commit()


async def resolve_sources(
    session: AsyncSession,
    agent: Agent,
    source_ids: list[str] | None = None,
    *,
    principal: VerifiedPrincipal | None = None,
    include_project_sources: bool = False,
) -> list[Source]:
    """解析本轮可见信源。

    显式 `source_ids` 来自输入框的 @ 范围，优先于持久绑定；所有入口共用同一
    候选上限，避免默认 Agent 或大量绑定造成无界 fan-out。
    """
    if principal is None:
        return []
    if source_ids is not None:
        return await search_source_candidates(
            session,
            principal=principal,
            requested_source_ids=source_ids,
            include_project_sources=include_project_sources,
        )
    if agent.is_default:
        return await search_source_candidates(
            session,
            principal=principal,
            include_project_sources=include_project_sources,
        )
    bindings = await list_bindings(session, agent)
    src_ids = [b.target_id for b in bindings if b.target_type == BindingTargetType.SOURCE]
    if not src_ids:
        return []
    return await search_source_candidates(
        session,
        principal=principal,
        requested_source_ids=src_ids,
        include_project_sources=include_project_sources,
    )


async def resolve_mcp_specs(session: AsyncSession, agent: Agent) -> list[tuple[str, dict]]:
    """展开外部 MCP server 绑定 → `[(label, config), …]`，供 agent 作 MCP 客户端挂载。"""
    bindings = await list_bindings(session, agent)
    specs: list[tuple[str, dict]] = []
    for b in bindings:
        if b.target_type != BindingTargetType.MCP_SERVER:
            continue
        cfg = b.config or {}
        specs.append((cfg.get("name") or b.target_id or "mcp", cfg))
    return specs


# ── 会话 ────────────────────────────────────────────────────────────
async def serialize_threads_out(session: AsyncSession, threads: list[Thread]) -> list[ThreadOut]:
    if not threads:
        return []
    parent_ids = sorted({t.parent_thread_id for t in threads if t.parent_thread_id})
    parents_by_id: dict[str, Thread] = {}
    if parent_ids:
        parent_rows = list(
            (await session.scalars(select(Thread).where(Thread.id.in_(parent_ids)))).all()
        )
        parents_by_id = {p.id: p for p in parent_rows}

    result: list[ThreadOut] = []
    for t in threads:
        is_branched = t.parent_thread_id is not None
        visible_parent_id: str | None = None
        visible_parent_title: str | None = None
        if is_branched and t.parent_thread_id:
            parent = parents_by_id.get(t.parent_thread_id)
            if (
                parent is not None
                and parent.deleted_by_owner_at is None
                and parent.agent_id == t.agent_id
                and parent.owner_key == t.owner_key
            ):
                visible_parent_id = parent.id
                visible_parent_title = parent.title
        result.append(
            ThreadOut(
                id=t.id,
                agent_id=t.agent_id,
                title=t.title,
                archived=bool(t.archived),
                is_branched=is_branched,
                parent_thread_id=visible_parent_id,
                parent_thread_title=visible_parent_title,
                branch_point_message_id=t.branch_point_message_id if is_branched else None,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
        )
    return result


async def serialize_thread_out(session: AsyncSession, thread: Thread) -> ThreadOut:
    return (await serialize_threads_out(session, [thread]))[0]


async def list_threads(
    session: AsyncSession,
    agent_id: str,
    *,
    owner_key: str,
    archived: bool = False,
    limit: int = THREAD_PAGE_DEFAULT,
    offset: int = 0,
) -> list[Thread]:
    statement = (
        select(Thread)
        .where(
            Thread.agent_id == agent_id,
            Thread.owner_key == owner_key,
            Thread.archived.is_(archived),
            Thread.deleted_by_owner_at.is_(None),
        )
        .order_by(Thread.updated_at.desc(), Thread.id.desc())
    )
    if offset:
        statement = statement.offset(offset)
    statement = statement.limit(max(1, min(int(limit), THREAD_PAGE_MAX)))
    rows = await session.execute(statement)
    return list(rows.scalars().all())


async def update_thread(
    session: AsyncSession,
    agent_id: str,
    thread_id: str,
    *,
    owner_key: str,
    title: str | None = None,
    archived: bool | None = None,
) -> Thread:
    thread = await get_thread(session, agent_id, thread_id, owner_key=owner_key)
    if title is not None and title.strip():
        thread.title = title.strip()[:200]
    if archived is not None:
        thread.archived = archived
    thread.updated_at = _next_monotonic_utc_now()
    await session.commit()
    await session.refresh(thread)
    return thread


async def create_thread(session: AsyncSession, agent: Agent, title: str = "New chat", *, owner_key: str) -> Thread:
    await get_agent(session, agent.id, owner_key=owner_key)
    ts = _next_monotonic_utc_now()
    thread = Thread(
        agent_id=agent.id,
        owner_key=owner_key,
        title=title or "New chat",
        created_at=ts,
        updated_at=ts,
    )
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


async def get_thread(session: AsyncSession, agent_id: str, thread_id: str, *, owner_key: str) -> Thread:
    thread = await session.get(Thread, thread_id)
    if (
        thread is None
        or thread.agent_id != agent_id
        or thread.owner_key != owner_key
        or thread.deleted_by_owner_at is not None
    ):
        raise NotFoundError("Thread not found")
    return thread


async def delete_thread(session: AsyncSession, agent_id: str, thread_id: str, *, owner_key: str) -> None:
    thread = await get_thread(session, agent_id, thread_id, owner_key=owner_key)
    has_child = (
        await session.scalar(
            select(Thread.id).where(Thread.parent_thread_id == thread.id).limit(1)
        )
        is not None
    )
    if has_child:
        thread.deleted_by_owner_at = datetime.now(UTC)
        invalidate_session_thread_cache(thread.id)
        await session.commit()
        return

    qdrant_cleanup_targets: list[tuple[str, list[str]]] = []
    cur: Thread | None = thread
    visited_gc: set[str] = set()
    while cur is not None and cur.id not in visited_gc:
        visited_gc.add(cur.id)
        parent_id = cur.parent_thread_id
        persisted_projects = await collect_thread_persistence_projects(session, thread_id=cur.id)
        qdrant_cleanup_targets.append((cur.id, persisted_projects))
        await purge_thread_session_rows(session, thread_id=cur.id)
        await session.delete(cur)
        await session.flush()
        invalidate_session_thread_cache(cur.id)

        if not parent_id:
            break
        parent = await session.get(Thread, parent_id)
        if parent is None or parent.deleted_by_owner_at is None:
            break
        still_referenced = (
            await session.scalar(
                select(Thread.id).where(Thread.parent_thread_id == parent.id).limit(1)
            )
            is not None
        )
        if still_referenced:
            break
        cur = parent

    await session.commit()
    for gc_tid, gc_projects in qdrant_cleanup_targets:
        await purge_thread_qdrant_points_for_projects(
            session, thread_id=gc_tid, project_ids=gc_projects
        )


async def list_messages_page(
    session: AsyncSession,
    thread_id: str,
    *,
    limit: int = MESSAGE_PAGE_DEFAULT,
    cursor: str | None = None,
) -> MessagePage:
    """返回最近一页消息，页内保持正向时间顺序（支持跨分支段 HMAC v2 游标分页）。"""
    if limit < 1 or limit > MESSAGE_PAGE_MAX:
        raise ValidationError(
            f"Message page limit must be between 1 and {MESSAGE_PAGE_MAX}",
            code=ErrorCode.INVALID_PAGE_LIMIT,
        )

    segments = await resolve_thread_lineage_segments(session, thread_id)
    max_seg_idx = len(segments) - 1
    start_seg_idx = max_seg_idx
    cur_created_at: datetime | None = None
    cur_message_id: str | None = None
    if cursor:
        start_seg_idx, cur_created_at, cur_message_id = _decode_message_cursor_v2(
            thread_id,
            cursor,
            max_seg_idx=max_seg_idx,
        )

    candidates: list[tuple[int, Message]] = []
    for seg_idx in range(start_seg_idx, -1, -1):
        needed = (limit + 1) - len(candidates)
        if needed <= 0:
            break
        seg = segments[seg_idx]
        filters = _segment_sql_filters(seg)
        if seg_idx == start_seg_idx and cur_created_at is not None and cur_message_id is not None:
            filters.append(
                or_(
                    Message.created_at < cur_created_at,
                    and_(Message.created_at == cur_created_at, Message.id < cur_message_id),
                )
            )
        rows = await session.execute(
            select(Message)
            .where(*filters)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(needed)
        )
        for msg in rows.scalars().all():
            candidates.append((seg_idx, msg))

    has_more = len(candidates) > limit
    page_desc = candidates[:limit]
    next_cursor: str | None = None
    if has_more and page_desc:
        last_seg_idx, last_msg = page_desc[-1]
        if len(segments) == 1:
            next_cursor = _encode_message_cursor(thread_id, last_msg)
        else:
            next_cursor = _encode_message_cursor_v2(thread_id, last_seg_idx, last_msg)
    return MessagePage(
        items=[msg for _, msg in reversed(page_desc)],
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _message_source_ids(message: Message) -> set[str] | None:
    citations = message.citations if message.citations is not None else []
    if not isinstance(citations, list):
        return None
    source_ids: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            return None
        if citation.get("source_id"):
            source_ids.add(str(citation["source_id"]))
        elif citation.get("kind") != "external" or citation.get("chunk_id") or not citation.get("url"):
            return None

    steps = getattr(message, "steps", None)
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            return None
        tool_name = step.get("name")
        details = step.get("details")
        has_knowledge_scope = isinstance(details, dict) and details.get("scope") == "knowledge"
        if not (isinstance(tool_name, str) and tool_name in _KNOWLEDGE_TOOL_NAMES) and not has_knowledge_scope:
            continue
        if not has_knowledge_scope:
            return None
        sources = details.get("sources")
        if not isinstance(sources, list) or not sources:
            return None
        step_source_ids = {
            str(source["id"])
            for source in sources
            if isinstance(source, dict) and source.get("id")
        }
        if len(step_source_ids) != len(sources):
            return None
        source_ids.update(step_source_ids)
    return source_ids


async def filter_messages_for_scope(
    session: AsyncSession,
    messages: list[Message],
    *,
    principal: VerifiedPrincipal,
    requested_source_ids: list[str] | None = None,
) -> list[Message]:
    provenance = [
        (message, _message_source_ids(message) if message.role == MessageRole.ASSISTANT else None)
        for message in messages
    ]
    source_ids = set().union(*(ids for _, ids in provenance if ids))
    authorized = await get_authorized_source_ids(
        session,
        principal=principal,
        requested_source_ids=sorted(source_ids),
    ) if source_ids else set()
    if requested_source_ids is not None:
        authorized.intersection_update(requested_source_ids)
    visible: list[Message] = []
    for message, cited_source_ids in provenance:
        if message.role != MessageRole.ASSISTANT:
            visible.append(message)
            continue
        can_read = cited_source_ids is not None and cited_source_ids.issubset(authorized)
        # Preserve conversation roles without changing the persisted ORM row.
        # Frozen prompts can embed earlier revoked answers, even for a currently
        # authorized answer; omit those snapshots from every assistant history read.
        visible.append(Message(
            id=getattr(message, "id", None),
            thread_id=getattr(message, "thread_id", None),
            reply_to_message_id=getattr(message, "reply_to_message_id", None),
            regenerated_from_message_id=getattr(message, "regenerated_from_message_id", None),
            created_at=getattr(message, "created_at", None),
            role=MessageRole.ASSISTANT,
            content=(
                message.content if can_read else
                "[Answer hidden because its evidence is unavailable in the current source scope.]"
            ),
            citations=message.citations if can_read else [],
            attachments=(getattr(message, "attachments", None) or []) if can_read else [],
            steps=(getattr(message, "steps", None) or []) if can_read else [],
            prompt_preview="",
            status=(getattr(message, "status", None) or MessageStatus.OK) if can_read else MessageStatus.OK,
            error=getattr(message, "error", None) if can_read else None,
        ))
    return visible


def deduplicate_prior_regenerations(messages: list[Message]) -> list[Message]:
    """Keep only the latest chronological answer per parent question in prior turns."""
    latest_by_question: dict[str, str] = {}
    for msg in messages:
        if msg.role == MessageRole.ASSISTANT and msg.reply_to_message_id:
            latest_by_question[msg.reply_to_message_id] = msg.id
    return [
        msg
        for msg in messages
        if not (
            msg.role == MessageRole.ASSISTANT
            and msg.reply_to_message_id
            and latest_by_question.get(msg.reply_to_message_id) != msg.id
        )
    ]


async def _history(
    session: AsyncSession,
    thread_id: str,
    exclude_id: str,
    *,
    principal: VerifiedPrincipal,
    requested_source_ids: list[str] | None,
) -> list[dict[str, str]]:
    segments = await resolve_thread_lineage_segments(session, thread_id)
    collected_segments_chron: list[list[Message]] = []
    remaining = settings.history_load_limit
    for seg in reversed(segments):
        if remaining <= 0:
            break
        filters = _segment_sql_filters(seg)
        filters.extend(
            [
                Message.id != exclude_id,
                Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
                Message.status == MessageStatus.OK,
            ]
        )
        rows = await session.execute(
            select(Message)
            .where(*filters)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(remaining * 2 + 4)
        )
        seg_chron = deduplicate_prior_regenerations(list(reversed(rows.scalars().all())))
        if seg_chron:
            sliced = seg_chron[-remaining:]
            collected_segments_chron.append(sliced)
            remaining -= len(sliced)
    chronological = [m for seg_msgs in reversed(collected_segments_chron) for m in seg_msgs]
    messages = await filter_messages_for_scope(
        session,
        chronological,
        principal=principal,
        requested_source_ids=requested_source_ids,
    )
    return [{"role": m.role.value, "content": m.content} for m in messages]


def _history_tokens(history: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) for m in history)


async def compress_history(history: list[dict[str, str]], *, llm=None, budget_tokens: int) -> list[dict[str, str]]:
    """上下文阈值压缩：超预算时把较早消息压成一段摘要，仅保留最近 N 条原文。

    有 LLM → 摘要旧段（保留事实/结论/称呼/待办）；无 LLM/失败 → 按预算从尾部裁剪。
    """
    if _history_tokens(history) <= budget_tokens:
        return history

    keep = max(2, settings.history_keep_recent)
    recent = history[-keep:]
    older = history[:-keep]
    if not older:
        return recent

    if llm is not None and getattr(llm, "configured", False):
        transcript = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in older
        )[:12000]
        try:
            summary = await llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize the following conversation into key bullet points (<=400 words) "
                            "in the same language used in the conversation: preserve facts, conclusions, "
                            "numbers, names, and open items; do not add commentary."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ]
            )
            return [
                {"role": "user", "content": f"(Summary of earlier conversation for reference)\n{summary.strip()}"},
                *recent,
            ]
        except Exception:  # noqa: BLE001
            pass

    # 兜底：从最近往前装，装满预算为止
    trimmed: list[dict[str, str]] = []
    used = 0
    for m in reversed(history):
        t = estimate_tokens(m["content"])
        if used + t > budget_tokens and trimmed:
            break
        trimmed.append(m)
        used += t
    return list(reversed(trimmed))


# ── 问答计划 ─────────────────────────────────────────────────────────
@dataclass
class AskPlan:
    """一次问答的提示词计划（agent-first：检索由循环内工具按需完成，不预置资料区）。"""

    query: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    prompt_preview: str = ""
    source_ids: list[str] | None = None  # @范围：限定循环内检索工具可见的信源
    user_message_id: str | None = None
    regenerated_from_message_id: str | None = None
    session_audit_trace: dict[str, Any] | None = None
    route_decision: dict[str, Any] | None = None
    session_citations: list[dict[str, Any]] = field(default_factory=list)


async def _resolve_session_history(
    session: AsyncSession,
    *,
    legacy_history: list[dict[str, str]],
    thread_id: str,
    exclude_id: str,
    query: str,
    principal: VerifiedPrincipal,
    owner_key: str,
    source_ids: list[str] | None,
    llm: Any,
    budget_tokens: int,
) -> tuple[list[dict[str, str]], dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]]]:
    mode = settings.session_context_mode
    if mode == "off":
        compressed = await compress_history(legacy_history, llm=llm, budget_tokens=budget_tokens)
        return compressed, None, None, []

    if mode == "shadow":
        compressed = await compress_history(legacy_history, llm=llm, budget_tokens=budget_tokens)
        try:
            pack = await prepare_session_context_for_ask(
                session,
                thread_id=thread_id,
                exclude_message_id=exclude_id,
                query=query,
                principal=principal,
                owner_key=owner_key,
                requested_source_ids=source_ids,
                budget_tokens=budget_tokens,
            )
            pack.audit_trace.mode = "shadow"
            log_session_audit_trace(thread_id, pack.audit_trace)
            return compressed, pack.audit_trace.to_dict(), dict(pack.audit_trace.route_decision), []
        except Exception as exc:  # noqa: BLE001 - shadow telemetry must never fail user ask
            _log.warning("ECCE shadow session context failed (%s)", type(exc).__name__)
            return compressed, None, None, []

    try:
        pack = await prepare_session_context_for_ask(
            session,
            thread_id=thread_id,
            exclude_message_id=exclude_id,
            query=query,
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=source_ids,
            budget_tokens=budget_tokens,
        )
        log_session_audit_trace(thread_id, pack.audit_trace)
        return (
            pack.history_messages,
            pack.audit_trace.to_dict(),
            dict(pack.audit_trace.route_decision),
            list(pack.session_citations),
        )
    except ForbiddenError:
        raise
    except Exception as exc:  # noqa: BLE001 - graceful fallback to bounded legacy window on unexpected error
        _log.warning("ECCE session context fallback to legacy history (%s)", type(exc).__name__)
        fallback_hist = legacy_history or await _history(
            session,
            thread_id,
            exclude_id=exclude_id,
            principal=principal,
            requested_source_ids=source_ids,
        )
        compressed = await compress_history(fallback_hist, llm=llm, budget_tokens=budget_tokens)
        return compressed, None, None, []


async def resolve_user_display_name(
    session: AsyncSession,
    *,
    user_name: str | None = None,
    principal: VerifiedPrincipal | None = None,
) -> str | None:
    if user_name is not None and user_name.strip():
        return user_name
    subject = getattr(principal, "subject", "") if principal is not None else ""
    if subject:
        user = await session.get(User, subject)
        if user is not None and user.is_active and user.name:
            return user.name
    return user_name


def build_ask_context(
    *,
    agent: Agent,
    query: str,
    history: list[dict[str, str]] | None = None,
    attachments: list[dict] | None = None,
    source_ids: list[str] | None = None,
    user_name: str | None = None,
) -> AskPlan:
    """组装带系统提示的消息（不落库，对话与 OpenAI 端点复用）。是否检索由模型经工具决定。"""
    query = unicodedata.normalize("NFC", query or "")
    effective_agent_name = DEFAULT_AGENT_NAME if _is_legacy_default_agent(agent) else agent.name
    messages = build_agent_messages(
        effective_agent_name,
        agent.persona or {},
        query,
        history=history,
        language=settings.sag_language,
        timezone=settings.timezone,
        attachments=attachments,
        user_name=user_name,
    )
    return AskPlan(
        query=query,
        messages=messages,
        prompt_preview=build_prompt_preview(messages),
        source_ids=source_ids,
    )


async def prepare_ask(
    session: AsyncSession,
    *,
    agent: Agent,
    thread: Thread,
    query: str,
    attachments: list[str] | None = None,
    source_ids: list[str] | None = None,
    principal: VerifiedPrincipal,
    owner_key: str,
    llm=None,
    user_name: str | None = None,
) -> AskPlan:
    """落库用户消息（含图片附件 meta）、解析历史（超上下文阈值时主动压缩），组装计划。"""
    from sag_api.api.v1.attachments import attachment_path

    query = unicodedata.normalize("NFC", query or "")
    await get_thread(session, agent.id, thread.id, owner_key=owner_key)
    resolved: list[dict] = []
    for aid in attachments or []:
        path = attachment_path(aid, owner_key=owner_key)
        if path is None:
            raise ValidationError(f"Attachment not found or expired: {aid}")
        ext = aid.rsplit(".", 1)[-1].lower()
        media_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext, "image/png")
        resolved.append({"id": aid, "media_type": media_type, "path": path})

    ts = _next_monotonic_utc_now()
    user_msg = Message(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=query,
        citations=[],
        attachments=[{k: a[k] for k in ("id", "media_type")} for a in resolved],
        created_at=ts,
        updated_at=ts,
    )
    session.add(user_msg)
    if thread.title in _DEFAULT_TITLES:
        thread.title = query[:40] or "Image chat"
    thread.updated_at = ts
    await session.commit()
    await session.refresh(user_msg)

    history = [] if settings.session_context_mode == "on" else await _history(
        session,
        thread.id,
        exclude_id=user_msg.id,
        principal=principal,
        requested_source_ids=source_ids,
    )
    # 历史预算 = 上下文窗口的 40%（其余留给工具轮/回答）
    history, audit_trace, route_decision, session_citations = await _resolve_session_history(
        session,
        legacy_history=history,
        thread_id=thread.id,
        exclude_id=user_msg.id,
        query=query,
        principal=principal,
        owner_key=owner_key,
        source_ids=source_ids,
        llm=llm,
        budget_tokens=int(settings.llm_context_window * 0.4),
    )
    effective_user_name = await resolve_user_display_name(
        session,
        user_name=user_name,
        principal=principal,
    )
    plan = build_ask_context(
        agent=agent,
        query=query,
        history=history,
        attachments=resolved or None,
        source_ids=source_ids,
        user_name=effective_user_name,
    )
    plan.user_message_id = user_msg.id
    plan.session_audit_trace = audit_trace
    plan.route_decision = route_decision
    plan.session_citations = session_citations
    return plan


async def delete_message(
    session: AsyncSession, agent_id: str, thread_id: str, message_id: str, *, owner_key: str,
) -> None:
    thread = await get_thread(session, agent_id, thread_id, owner_key=owner_key)
    message = await session.get(Message, message_id)
    if message is None or message.thread_id != thread.id:
        raise NotFoundError("Message not found")

    child_bp_ids = list(
        (
            await session.scalars(
                select(Thread.branch_point_message_id).where(
                    Thread.parent_thread_id == thread.id,
                    Thread.branch_point_message_id.is_not(None),
                )
            )
        ).all()
    )
    if child_bp_ids:
        bp_messages = list(
            (await session.scalars(select(Message).where(Message.id.in_(child_bp_ids)))).all()
        )
        for bp_msg in bp_messages:
            seg = LineageSegment(
                thread_id=thread.id,
                cutoff_created_at=bp_msg.created_at,
                cutoff_message_id=bp_msg.id,
                cutoff_reply_to_message_id=bp_msg.reply_to_message_id,
                is_inherited=True,
            )
            if seg.includes_message(message):
                raise ConflictError(
                    "Cannot delete a message that belongs to a branched conversation's inherited history"
                )

    persisted_projects = await collect_thread_persistence_projects(session, thread_id=thread.id)
    await purge_thread_session_rows(session, thread_id=thread.id)
    await session.delete(message)
    invalidate_session_thread_cache(thread.id)
    await session.commit()
    await purge_thread_qdrant_points_for_projects(
        session, thread_id=thread.id, project_ids=persisted_projects
    )


async def persist_answer(
    session_factory: async_sessionmaker,
    thread_id: str,
    answer: str,
    citations: list[dict],
    steps: list[dict] | None = None,
    prompt_preview: str = "",
    *,
    reply_to_message_id: str | None = None,
    regenerated_from_message_id: str | None = None,
    status: MessageStatus = MessageStatus.OK,
    error: dict | None = None,
) -> str:
    """写入一条 assistant 消息。失败/取消时 answer 传部分已生成文本（可为 ""），
    并附带结构化 error {code, message, retryable, details}。"""
    async with session_factory() as session:
        ts = _next_monotonic_utc_now()
        message = Message(
            reply_to_message_id=reply_to_message_id,
            regenerated_from_message_id=regenerated_from_message_id,
            thread_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            citations=citations,
            steps=steps or [],
            prompt_preview=prompt_preview,
            status=status,
            error=error,
            created_at=ts,
            updated_at=ts,
        )
        session.add(message)
        await session.execute(update(Thread).where(Thread.id == thread_id).values(updated_at=ts))
        await session.commit()
        await session.refresh(message)
        return message.id
