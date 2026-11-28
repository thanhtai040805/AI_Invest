from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime
from sag_api.enums import BindingTargetType, MessageRole, MessageStatus


class Agent(IDMixin, TimestampMixin, Base):
    """Agent —— 名字 + 系统提示 + 挂载的信源/工具（经 MCP）。"""

    __tablename__ = "agents"

    owner_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    avatar: Mapped[str] = mapped_column(String(64), default="")  # emoji / 首字母
    # 默认 agent：开箱即用的主对话入口，知识库=全部信源（resolve_sources 特判）
    is_default: Mapped[bool] = mapped_column(default=False, index=True)
    # 配置：{ system_prompt, greeting, tools[] }（tools 为额外启用的工具/MCP 名）
    persona: Mapped[dict] = mapped_column("persona_json", JSON, default=dict)


class AgentBinding(IDMixin, TimestampMixin, Base):
    """Agent 挂载的东西：一个信源，或一个 MCP server（工具来源）。"""

    __tablename__ = "agent_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "target_type", "target_id", name="uq_agent_binding"),)

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[BindingTargetType] = mapped_column(SAEnum(BindingTargetType, native_enum=False, length=16))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    # MCP server 连接配置（url 或 command/args/env）；信源绑定为空
    config: Mapped[dict] = mapped_column("config_json", JSON, default=dict)


class Thread(IDMixin, TimestampMixin, Base):
    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint(
            "(parent_thread_id IS NULL AND branch_point_message_id IS NULL) OR "
            "(parent_thread_id IS NOT NULL AND branch_point_message_id IS NOT NULL)",
            name="ck_threads_branch_pair",
        ),
        CheckConstraint(
            "parent_thread_id IS NULL OR parent_thread_id <> id",
            name="ck_threads_no_self_parent",
        ),
        Index(
            "ix_threads_owner_visible",
            "agent_id",
            "owner_key",
            "archived",
            text("updated_at DESC"),
            sqlite_where=text("deleted_by_owner_at IS NULL"),
            postgresql_where=text("deleted_by_owner_at IS NULL"),
        ),
    )

    owner_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="New chat")
    archived: Mapped[bool] = mapped_column(default=False, index=True)
    parent_thread_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("threads.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    branch_point_message_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey(
            "messages.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_threads_branch_point_message_id",
        ),
        nullable=True,
        index=True,
    )
    deleted_by_owner_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        default=None,
        nullable=True,
    )


class Message(IDMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_thread_created_id", "thread_id", "created_at", "id"),)

    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    regenerated_from_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Chat image upload metadata, not Source/Document metadata; files are scoped to the conversation owner.
    attachments: Mapped[list] = mapped_column("attachments_json", JSON, default=list)
    # Agentic 执行轨迹：[{kind:thinking|tool, step, name?, args?, ms, count?}]（助手消息）
    steps: Mapped[list] = mapped_column("steps_json", JSON, default=list)
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, native_enum=False, length=16))
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column("citations_json", JSON, default=list)
    # Frozen initial model input for this assistant turn. It deliberately
    # excludes tool results and the generated answer so historical playback
    # can audit the same role-separated input that was shown live.
    prompt_preview: Mapped[str] = mapped_column(Text, default="")
    # 助手回复的终态；对话历史需要保留失败/取消气泡才能与实时会话一致。
    # values_callable 强制以枚举 value（小写 ok/failed/cancelled）而不是 name（大写）存储，
    # 与 server_default 保持一致，避免 SAEnum 在读回时按名字查表报 LookupError。
    status: Mapped[MessageStatus] = mapped_column(
        SAEnum(
            MessageStatus,
            native_enum=False,
            length=16,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=MessageStatus.OK,
        server_default=MessageStatus.OK.value,
    )
    # 失败/取消时的错误结构 {code, message, retryable, details}；成功时为 None。
    error: Mapped[dict | None] = mapped_column("error_json", JSON, default=None, nullable=True)


_SQLITE_THREADS_BRANCH_SCOPE_INS = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_threads_branch_scope_ins
    BEFORE INSERT ON threads
    FOR EACH ROW
    WHEN NEW.parent_thread_id IS NOT NULL
    BEGIN
        SELECT CASE
            WHEN NOT EXISTS (
                SELECT 1 FROM threads p
                 WHERE p.id = NEW.parent_thread_id
                   AND p.agent_id = NEW.agent_id
                   AND p.owner_key IS NEW.owner_key
            ) THEN
                RAISE(ABORT, 'Branched thread must share agent_id and owner_key with parent_thread_id')
            WHEN NOT EXISTS (
                SELECT 1 FROM messages m
                 WHERE m.id = NEW.branch_point_message_id
                   AND m.thread_id = NEW.parent_thread_id
            ) THEN
                RAISE(ABORT, 'branch_point_message_id must belong directly to parent_thread_id')
        END;
    END;
    """
)

_SQLITE_THREADS_BRANCH_IMMUTABLE_UPD = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_threads_branch_scope_upd
    BEFORE UPDATE ON threads
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN NEW.parent_thread_id IS NOT OLD.parent_thread_id
              OR NEW.branch_point_message_id IS NOT OLD.branch_point_message_id THEN
                RAISE(ABORT, 'Thread branch lineage pointers are immutable after creation')
            WHEN (NEW.agent_id IS NOT OLD.agent_id OR NEW.owner_key IS NOT OLD.owner_key)
              AND (
                  OLD.parent_thread_id IS NOT NULL
                  OR EXISTS (SELECT 1 FROM threads c WHERE c.parent_thread_id = OLD.id)
              ) THEN
                RAISE(ABORT, 'Thread agent_id and owner_key are immutable in a branch lineage')
        END;
    END;
    """
)

_PG_THREADS_BRANCH_SCOPE_DDLS = (
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_threads_branch_scope_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.parent_thread_id IS DISTINCT FROM OLD.parent_thread_id
                   OR NEW.branch_point_message_id IS DISTINCT FROM OLD.branch_point_message_id THEN
                    RAISE EXCEPTION 'Thread branch lineage pointers are immutable after creation'
                        USING ERRCODE = '23514';
                END IF;
                IF (NEW.agent_id IS DISTINCT FROM OLD.agent_id
                    OR NEW.owner_key IS DISTINCT FROM OLD.owner_key)
                   AND (
                       OLD.parent_thread_id IS NOT NULL
                       OR EXISTS (SELECT 1 FROM threads c WHERE c.parent_thread_id = OLD.id)
                   ) THEN
                    RAISE EXCEPTION 'Thread agent_id and owner_key are immutable in a branch lineage'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF NEW.parent_thread_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM threads p
                     WHERE p.id = NEW.parent_thread_id
                       AND p.agent_id = NEW.agent_id
                       AND p.owner_key IS NOT DISTINCT FROM NEW.owner_key
                ) THEN
                    RAISE EXCEPTION 'Branched thread must share agent_id and owner_key with parent_thread_id'
                        USING ERRCODE = '23514';
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM messages m
                     WHERE m.id = NEW.branch_point_message_id
                       AND m.thread_id = NEW.parent_thread_id
                ) THEN
                    RAISE EXCEPTION 'branch_point_message_id must belong directly to parent_thread_id'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_threads_branch_scope_guard ON threads"),
    DDL(
        """
        CREATE TRIGGER trg_threads_branch_scope_guard
        BEFORE INSERT OR UPDATE ON threads
        FOR EACH ROW
        EXECUTE FUNCTION fn_threads_branch_scope_guard()
        """
    ),
)

event.listen(
    Message.__table__,
    "after_create",
    _SQLITE_THREADS_BRANCH_SCOPE_INS.execute_if(dialect="sqlite"),
)
event.listen(
    Message.__table__,
    "after_create",
    _SQLITE_THREADS_BRANCH_IMMUTABLE_UPD.execute_if(dialect="sqlite"),
)
for _pg_ddl in _PG_THREADS_BRANCH_SCOPE_DDLS:
    event.listen(
        Message.__table__,
        "after_create",
        _pg_ddl.execute_if(dialect="postgresql"),
    )
