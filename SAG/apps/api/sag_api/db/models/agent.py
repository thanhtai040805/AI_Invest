from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin
from sag_api.enums import BindingTargetType, MessageRole


class Agent(IDMixin, TimestampMixin, Base):
    """Agent — tên + system prompt + nguồn/công cụ được gắn (qua MCP)."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(120))
    avatar: Mapped[str] = mapped_column(String(64), default="")  # emoji / chữ cái đầu
    # Agent mặc định: lối vào hội thoại chính dùng ngay được, kho kiến thức = tất cả nguồn (resolve_sources xử lý đặc biệt)
    is_default: Mapped[bool] = mapped_column(default=False, index=True)
    # Cấu hình: { system_prompt, greeting, tools[] } (tools là tên tool/MCP được bật thêm)
    persona: Mapped[dict] = mapped_column("persona_json", JSON, default=dict)


class AgentBinding(IDMixin, TimestampMixin, Base):
    """Thứ Agent gắn vào: một nguồn, hoặc một MCP server (nguồn công cụ)."""

    __tablename__ = "agent_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "target_type", "target_id", name="uq_agent_binding"),)

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[BindingTargetType] = mapped_column(SAEnum(BindingTargetType, native_enum=False, length=16))
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    # Cấu hình kết nối MCP server (url hoặc command/args/env); rỗng khi ràng buộc nguồn
    config: Mapped[dict] = mapped_column("config_json", JSON, default=dict)


class Thread(IDMixin, TimestampMixin, Base):
    __tablename__ = "threads"

    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="Phiên mới")
    archived: Mapped[bool] = mapped_column(default=False, index=True)


class Message(IDMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_thread_created_id", "thread_id", "created_at", "id"),)

    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    # meta tệp đính kèm ảnh: [{id, name, media_type}] (file trong upload_dir/attachments/)
    attachments: Mapped[list] = mapped_column("attachments_json", JSON, default=list)
    # Dấu vết thực thi Agentic: [{kind:thinking|tool, step, name?, args?, ms, count?}] (tin nhắn trợ lý)
    steps: Mapped[list] = mapped_column("steps_json", JSON, default=list)
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, native_enum=False, length=16))
    content: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column("citations_json", JSON, default=list)
    # Frozen initial model input for this assistant turn. It deliberately
    # excludes tool results and the generated answer so historical playback
    # can audit the same role-separated input that was shown live.
    prompt_preview: Mapped[str] = mapped_column(Text, default="")
