"""Logic miền Agent: CRUD, ràng buộc (nguồn/MCP), phân giải ngữ cảnh, hội thoại fan-out đa nguồn."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.branding import DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_NAME
from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.db.models import Agent, AgentBinding, Message, Source, Thread
from sag_api.enums import BindingTargetType, MessageRole
from sag_api.generation import build_agent_messages, build_prompt_preview
from sag_api.generation.prompt import estimate_tokens
from sag_api.services.source_service import search_source_candidates

_DEFAULT_TITLES = {"Cuộc hội thoại mới", "Trò chuyện mới", "New chat"}
THREAD_PAGE_DEFAULT = 6
THREAD_PAGE_MAX = 100
MESSAGE_PAGE_DEFAULT = 40
MESSAGE_PAGE_MAX = 100
MESSAGE_CURSOR_MAX_LENGTH = 2048
_MESSAGE_CURSOR_ID = re.compile(r"[0-9a-f]{32}\Z")


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
        return ValidationError("Cursor tin nhắn không hợp lệ", code=ErrorCode.INVALID_CURSOR)

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


# ── CRUD ────────────────────────────────────────────────────────────
async def list_agents(session: AsyncSession) -> list[Agent]:
    rows = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
    return list(rows.scalars().all())


async def get_agent(session: AsyncSession, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError("Agent không tồn tại")
    return agent


async def create_agent(session: AsyncSession, *, name: str, avatar: str = "", persona: dict | None = None) -> Agent:
    name = name.strip()
    if not name:
        raise ValidationError("Tên Agent không được để trống")
    agent = Agent(name=name, avatar=avatar or name[:1], persona=persona or {})
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(
    session: AsyncSession,
    agent_id: str,
    *,
    name: str | None = None,
    avatar: str | None = None,
    persona: dict | None = None,
) -> Agent:
    agent = await get_agent(session, agent_id)
    if name is not None:
        agent.name = name
    if avatar is not None:
        agent.avatar = avatar
    if persona is not None:
        agent.persona = persona
    await session.commit()
    await session.refresh(agent)
    return agent


_DEFAULT_GREETING = "Xin chào! Tôi là Trợ lý Phân tích Đầu tư AIInvest. Hãy nạp tài liệu BCTC/BCTN/Tin tức hoặc hỏi tôi bất kỳ câu hỏi phân tích nào."
_DEFAULT_PERSONA = {"greeting": _DEFAULT_GREETING, "system_prompt": ""}


def _is_legacy_default_agent(agent: Agent) -> bool:
    """Chỉ nhận diện trợ lý mặc định hoàn toàn chưa tùy chỉnh của phiên bản cũ, tránh ghi đè thay đổi của người dùng."""
    return agent.name == "sag" and agent.avatar in {"s", "S"} and (agent.persona or {}) == _DEFAULT_PERSONA


async def get_default_agent(session: AsyncSession) -> Agent:
    """Agent mặc định (cổng hội thoại chính dùng ngay): get-or-create, idempotent."""
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


async def delete_agent(session: AsyncSession, agent_id: str) -> None:
    agent = await get_agent(session, agent_id)
    await session.delete(agent)
    await session.commit()


# ── Ràng buộc (nguồn / MCP server) ────────────────────────────────────────
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
            raise NotFoundError("Nguồn không tồn tại")
    elif target_type == BindingTargetType.MCP_SERVER:
        if not (config.get("url") or config.get("command")):
            raise ValidationError("MCP server cần cung cấp url hoặc command")
        target_id = target_id or (config.get("name") or config.get("url") or "mcp")
    exists = await session.scalar(
        select(AgentBinding).where(
            AgentBinding.agent_id == agent.id,
            AgentBinding.target_type == target_type,
            AgentBinding.target_id == target_id,
        )
    )
    if exists is not None:
        raise ConflictError("Mục tiêu này đã được ràng buộc")
    binding = AgentBinding(agent_id=agent.id, target_type=target_type, target_id=target_id, config=config)
    session.add(binding)
    await session.commit()
    await session.refresh(binding)
    return binding


async def remove_binding(session: AsyncSession, agent: Agent, binding_id: str) -> None:
    binding = await session.get(AgentBinding, binding_id)
    if binding is None or binding.agent_id != agent.id:
        raise NotFoundError("Ràng buộc không tồn tại")
    await session.delete(binding)
    await session.commit()


async def resolve_sources(
    session: AsyncSession,
    agent: Agent,
    source_ids: list[str] | None = None,
) -> list[Source]:
    """Phân giải các nguồn hiển thị ở lượt này.

    `source_ids` tường minh đến từ phạm vi @ của ô nhập liệu, ưu tiên hơn ràng buộc bền vững; mọi cổng vào dùng chung cùng một
    giới hạn ứng viên, tránh fan-out không giới hạn do Agent mặc định hoặc quá nhiều ràng buộc.
    """
    if source_ids:
        return await search_source_candidates(session, source_ids)
    if agent.is_default:
        return await search_source_candidates(session)
    bindings = await list_bindings(session, agent)
    src_ids = [b.target_id for b in bindings if b.target_type == BindingTargetType.SOURCE]
    if not src_ids:
        return []
    return await search_source_candidates(session, src_ids)


async def resolve_mcp_specs(session: AsyncSession, agent: Agent) -> list[tuple[str, dict]]:
    """Khai triển ràng buộc MCP server ngoài → `[(label, config), …]`, để agent gắn làm MCP client."""
    bindings = await list_bindings(session, agent)
    specs: list[tuple[str, dict]] = []
    for b in bindings:
        if b.target_type != BindingTargetType.MCP_SERVER:
            continue
        cfg = b.config or {}
        specs.append((cfg.get("name") or b.target_id or "mcp", cfg))
    return specs


# ── Phiên hội thoại ────────────────────────────────────────────────────────────
async def list_threads(
    session: AsyncSession,
    agent_id: str,
    *,
    archived: bool = False,
    limit: int = THREAD_PAGE_DEFAULT,
    offset: int = 0,
) -> list[Thread]:
    statement = (
        select(Thread)
        .where(Thread.agent_id == agent_id, Thread.archived.is_(archived))
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
    title: str | None = None,
    archived: bool | None = None,
) -> Thread:
    thread = await get_thread(session, agent_id, thread_id)
    if title is not None and title.strip():
        thread.title = title.strip()[:200]
    if archived is not None:
        thread.archived = archived
    await session.commit()
    await session.refresh(thread)
    return thread


async def create_thread(session: AsyncSession, agent: Agent, title: str = "Cuộc trò chuyện mới") -> Thread:
    thread = Thread(agent_id=agent.id, title=title or "Cuộc trò chuyện mới")
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


async def get_thread(session: AsyncSession, agent_id: str, thread_id: str) -> Thread:
    thread = await session.get(Thread, thread_id)
    if thread is None or thread.agent_id != agent_id:
        raise NotFoundError("Phiên hội thoại không tồn tại")
    return thread


async def delete_thread(session: AsyncSession, agent_id: str, thread_id: str) -> None:
    thread = await get_thread(session, agent_id, thread_id)
    await session.delete(thread)
    await session.commit()


async def list_messages_page(
    session: AsyncSession,
    thread_id: str,
    *,
    limit: int = MESSAGE_PAGE_DEFAULT,
    cursor: str | None = None,
) -> MessagePage:
    """Trả về trang tin nhắn gần đây nhất, trong trang giữ thứ tự thời gian tăng dần.

    Cơ sở dữ liệu dùng keyset đọc theo thứ tự giảm dần `(created_at, id)`, chỉ lấy `limit + 1`
    để biết còn tin nhắn cũ hơn hay không; không quét COUNT.
    """
    if limit < 1 or limit > MESSAGE_PAGE_MAX:
        raise ValidationError(
            f"Kích thước trang tin nhắn phải nằm trong khoảng 1 đến {MESSAGE_PAGE_MAX}",
            code=ErrorCode.INVALID_PAGE_LIMIT,
        )

    statement = select(Message).where(Message.thread_id == thread_id)
    if cursor:
        created_at, message_id = _decode_message_cursor(thread_id, cursor)
        statement = statement.where(
            or_(
                Message.created_at < created_at,
                and_(Message.created_at == created_at, Message.id < message_id),
            )
        )
    rows = await session.execute(statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1))
    candidates = list(rows.scalars().all())
    has_more = len(candidates) > limit
    page_desc = candidates[:limit]
    next_cursor = _encode_message_cursor(thread_id, page_desc[-1]) if has_more and page_desc else None
    return MessagePage(
        items=list(reversed(page_desc)),
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def _history(session: AsyncSession, thread_id: str, exclude_id: str) -> list[dict[str, str]]:
    rows = await session.execute(
        select(Message)
        .where(
            Message.thread_id == thread_id,
            Message.id != exclude_id,
            Message.role.in_((MessageRole.USER, MessageRole.ASSISTANT)),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(settings.history_load_limit)
    )
    messages = list(reversed(rows.scalars().all()))
    return [{"role": m.role.value, "content": m.content} for m in messages]


def _history_tokens(history: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) for m in history)


async def compress_history(history: list[dict[str, str]], *, llm=None, budget_tokens: int) -> list[dict[str, str]]:
    """Nén theo ngưỡng ngữ cảnh: khi vượt ngân sách thì nén các tin nhắn cũ hơn thành một đoạn tóm tắt, chỉ giữ N tin mới nhất nguyên văn.

    Có LLM → tóm tắt đoạn cũ (giữ sự kiện/kết luận/xưng hô/việc cần làm); không có LLM/thất bại → cắt từ cuối theo ngân sách.
    """
    if _history_tokens(history) <= budget_tokens:
        return history

    keep = max(2, settings.history_keep_recent)
    recent = history[-keep:]
    older = history[:-keep]
    if not older:
        return recent

    if llm is not None and getattr(llm, "configured", False):
        transcript = "\n".join(f"{'Người dùng' if m['role'] == 'user' else 'Trợ lý'}: {m['content']}" for m in older)[:12000]
        try:
            summary = await llm.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Nén đoạn hội thoại sau thành bản tóm tắt các điểm chính (≤400 ký tự): giữ sự kiện, kết luận, con số, cách xưng hô nhân vật và các việc chưa quyết định; không bình luận."
                        ),
                    },
                    {"role": "user", "content": transcript},
                ]
            )
            return [
                {"role": "user", "content": f"(Tóm tắt hội thoại trước, để tham khảo)\n{summary.strip()}"},
                *recent,
            ]
        except Exception:  # noqa: BLE001
            pass

    # Phương án cuối: nạp từ mới nhất trở về trước, đầy ngân sách thì dừng
    trimmed: list[dict[str, str]] = []
    used = 0
    for m in reversed(history):
        t = estimate_tokens(m["content"])
        if used + t > budget_tokens and trimmed:
            break
        trimmed.append(m)
        used += t
    return list(reversed(trimmed))


# ── Kế hoạch hỏi đáp ─────────────────────────────────────────────────────────
@dataclass
class AskPlan:
    """Kế hoạch prompt cho một lượt hỏi đáp (agent-first: truy vấn do công cụ trong vòng lặp thực hiện theo nhu cầu, không chuẩn bị sẵn vùng tài liệu)."""

    query: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    prompt_preview: str = ""
    source_ids: list[str] | None = None  # Phạm vi @: giới hạn các nguồn mà công cụ truy vấn trong vòng lặp thấy được
    user_message_id: str | None = None


def build_ask_context(
    *,
    agent: Agent,
    query: str,
    history: list[dict[str, str]] | None = None,
    attachments: list[dict] | None = None,
    source_ids: list[str] | None = None,
) -> AskPlan:
    """Ghép các tin nhắn kèm system prompt (không lưu xuống DB, hội thoại và endpoint OpenAI dùng chung). Có truy vấn hay không do model quyết định qua công cụ."""
    messages = build_agent_messages(
        agent.name,
        agent.persona or {},
        query,
        history=history,
        language=settings.sag_language,
        timezone=settings.timezone,
        attachments=attachments,
    )
    return AskPlan(
        query=query,
        messages=messages,
        prompt_preview=build_prompt_preview(messages),
        source_ids=source_ids or None,
    )


async def prepare_ask(
    session: AsyncSession,
    *,
    agent: Agent,
    thread: Thread,
    query: str,
    attachments: list[str] | None = None,
    source_ids: list[str] | None = None,
    llm=None,
) -> AskPlan:
    """Lưu tin nhắn người dùng xuống DB (gồm meta ảnh đính kèm), phân giải lịch sử (chủ động nén khi vượt ngưỡng ngữ cảnh), ghép kế hoạch."""
    from sag_api.api.v1.attachments import attachment_path

    resolved: list[dict] = []
    for aid in attachments or []:
        path = attachment_path(aid)
        if path is None:
            raise ValidationError(f"Tệp đính kèm không tồn tại hoặc đã hết hạn: {aid}")
        ext = aid.rsplit(".", 1)[-1].lower()
        media_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "gif": "image/gif",
        }.get(ext, "image/png")
        resolved.append({"id": aid, "media_type": media_type, "path": path})

    user_msg = Message(
        thread_id=thread.id,
        role=MessageRole.USER,
        content=query,
        citations=[],
        attachments=[{k: a[k] for k in ("id", "media_type")} for a in resolved],
    )
    session.add(user_msg)
    if thread.title in _DEFAULT_TITLES:
        thread.title = query[:40] or "Hội thoại ảnh"
    await session.commit()
    await session.refresh(user_msg)

    history = await _history(session, thread.id, exclude_id=user_msg.id)
    # Ngân sách lịch sử = 40% cửa sổ ngữ cảnh (phần còn lại dành cho vòng công cụ/câu trả lời)
    history = await compress_history(history, llm=llm, budget_tokens=int(settings.llm_context_window * 0.4))
    plan = build_ask_context(
        agent=agent,
        query=query,
        history=history,
        attachments=resolved or None,
        source_ids=source_ids,
    )
    plan.user_message_id = user_msg.id
    return plan


async def delete_message(session: AsyncSession, agent_id: str, thread_id: str, message_id: str) -> None:
    thread = await get_thread(session, agent_id, thread_id)
    message = await session.get(Message, message_id)
    if message is None or message.thread_id != thread.id:
        raise NotFoundError("Tin nhắn không tồn tại")
    await session.delete(message)
    await session.commit()


async def persist_answer(
    session_factory: async_sessionmaker,
    thread_id: str,
    answer: str,
    citations: list[dict],
    steps: list[dict] | None = None,
    prompt_preview: str = "",
) -> str:
    async with session_factory() as session:
        message = Message(
            thread_id=thread_id,
            role=MessageRole.ASSISTANT,
            content=answer,
            citations=citations,
            steps=steps or [],
            prompt_preview=prompt_preview,
        )
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message.id
