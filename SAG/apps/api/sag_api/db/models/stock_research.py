"""Database models for Stock Research financial tables and 3-Layer Knowledge Graph (Migration 0013)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime

_JSONB = JSON().with_variant(JSONB(), "postgresql")
_DOUBLE = Float().with_variant(DOUBLE_PRECISION(), "postgresql")


def _new_uuid_str() -> str:
    return str(uuid.uuid4())


class SagExtractedTable(Base):
    __tablename__ = "sag_extracted_tables"
    __table_args__ = (
        Index("idx_sag_extracted_tables_pit", "project_id", "valid_from", "sub_doc_slot"),
        Index("idx_sag_extracted_tables_doc_ver", "document_version_id"),
    )

    table_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sub_doc_slot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fiscal_quarter: Mapped[int | None] = mapped_column(
        SmallInteger, default=0, server_default=text("0"), nullable=True
    )
    table_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(
        String(16), default="VND", server_default=text("'VND'"), nullable=True
    )
    unit_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_multiplier: Mapped[float | None] = mapped_column(
        _DOUBLE, default=1.0, server_default=text("1.0"), nullable=True
    )
    headers_json: Mapped[Any] = mapped_column(_JSONB, nullable=False)
    rows_json: Mapped[Any] = mapped_column(_JSONB, nullable=False)
    footnotes_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sanity_error_ratio: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False)
    table_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class KgNode(Base):
    __tablename__ = "kg_nodes"
    __table_args__ = (
        Index("idx_kg_nodes_type_ticker_pit", "node_type", "ticker", "effective_from", "valid_to"),
    )

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, default=dict, server_default=text("'{}'"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(
        Date,
        default=lambda: date(9999, 12, 31),
        server_default=text("'9999-12-31'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class KgEdge(Base):
    __tablename__ = "kg_edges"
    __table_args__ = (
        Index("idx_kg_edges_src_pit", "src_node_id", "effective_from", "valid_to", "layer", "relation_type"),
        Index("idx_kg_edges_dst_pit", "dst_node_id", "effective_from", "valid_to", "layer", "relation_type"),
    )

    edge_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    src_node_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
    )
    dst_node_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(48), nullable=False)
    layer: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(24), nullable=True)
    weight: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    beta: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    p_value: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    fdr_q_value: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    tercile_stable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    verification_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, default=dict, server_default=text("'{}'"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(
        Date,
        default=lambda: date(9999, 12, 31),
        server_default=text("'9999-12-31'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )


class MacroSensitivitySnapshot(Base):
    __tablename__ = "macro_sensitivity_snapshots"
    __table_args__ = (
        Index(
            "idx_macro_sensitivity_pit",
            "target_id",
            "target_level",
            "macro_factor",
            "effective_from",
            "valid_to",
            "as_of_date",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_uuid_str)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_level: Mapped[str] = mapped_column(String(16), nullable=False)
    macro_factor: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    best_lag_q: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default=text("0"), nullable=False
    )
    beta: Mapped[float] = mapped_column(_DOUBLE, nullable=False)
    hac_t_stat: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    p_value: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    fdr_q_value: Mapped[float | None] = mapped_column(_DOUBLE, nullable=True)
    tercile_stable: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    doc_stance: Mapped[str | None] = mapped_column(String(24), nullable=True)
    verification_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(
        Date,
        default=lambda: date(9999, 12, 31),
        server_default=text("'9999-12-31'"),
        nullable=False,
    )
    sample_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, default=dict, server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
