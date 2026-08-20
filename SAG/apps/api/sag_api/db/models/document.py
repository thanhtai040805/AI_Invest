from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin
from sag_api.enums import DocumentStatus


class Document(IDMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_source_sag_source", "source_id", "sag_source_id"),
    )

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, native_enum=False, length=16), default=DocumentStatus.PENDING
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phân định lỗi: tầng trách nhiệm (api/engine/llm/store) và khâu trong chuỗi (parse/chunk/extract/...),
    # giúp nghiên cứu phát triển định vị trực tiếp từ log xuất ra. Chỉ có giá trị khi status=failed.
    error_layer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # source_id do zleap-sag ingest trả về (dùng để truy vết)
    sag_source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
