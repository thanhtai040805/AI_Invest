"""Database models for SAG-ECCE Session Context Provenance & Decision Ledger (Chặng 2).

Aligned 1:1 with `migrations/0009_session_context_ecce.sql` and compatible with both
PostgreSQL 16 (`TIMESTAMPTZ`, `JSON`, `DOUBLE PRECISION`, `plpgsql`) and SQLite (`PRAGMA foreign_keys=ON`).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime

SESSION_OPEN_ENDED_VALID_TO = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

SESSION_UNIT_KINDS = ("TURN", "WINDOW", "EPISODE")
SESSION_EPISTEMIC_STATUSES = (
    "FACT",
    "PROPOSED",
    "CONSIDERING",
    "CONFIRMED",
    "SUPERSEDED",
    "NEGATED",
    "OPEN_ISSUE",
)
SESSION_LEDGER_KINDS = ("DECISION", "OPEN_TASK", "CONSTRAINT", "OPEN_ISSUE")
SESSION_GRAPH_NODE_TYPES = ("UNIT", "SENTENCE", "ENTITY", "EPISODE", "SOURCE")
SESSION_GRAPH_EDGE_TYPES = (
    "ENTITY_MENTION",
    "SENTENCE_IN_UNIT",
    "ADJACENT_IN_EPISODE",
    "SUPERSEDES",
    "ALIAS_CANDIDATE",
    "CITES_DOCUMENT_SOURCE",
)


def _canonical_uuid_seed(*parts: str | int) -> str:
    """Injective canonical JSON tuple encoding preventing delimiter framing collisions."""
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=False)


def generate_session_unit_id(
    thread_id: str,
    unit_kind: str,
    ordinal: int,
    message_from_id: str,
) -> str:
    """Generate collision-free deterministic UUIDv5 primary key for a `SessionUnit`."""
    seed = _canonical_uuid_seed(
        "sag.session_unit.v1",
        str(thread_id),
        str(unit_kind),
        int(ordinal),
        str(message_from_id),
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def generate_session_unit_point_id(collection_name: str, session_unit_id: str) -> str:
    """Generate collision-free deterministic UUIDv5 Qdrant point ID for `session_units_{project_id}`."""
    seed = _canonical_uuid_seed("sag.session_qdrant_point.v1", str(collection_name), str(session_unit_id))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def generate_session_decision_id(session_unit_id: str, topic_slot_key: str) -> str:
    """Generate collision-free deterministic UUIDv5 primary key for a `SessionDecisionLedger` entry."""
    seed = _canonical_uuid_seed("sag.session_decision.v1", str(session_unit_id), str(topic_slot_key))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def generate_session_graph_edge_id(
    source_unit_id: str,
    source_node_key: str,
    target_node_key: str,
    edge_type: str,
    *,
    source_node_type: str = "",
    target_node_type: str = "",
) -> str:
    """Generate collision-free deterministic UUIDv5 primary key for a `SessionGraphEdge`."""
    seed = _canonical_uuid_seed(
        "sag.session_edge.v1",
        str(source_unit_id),
        str(source_node_type),
        str(source_node_key),
        str(target_node_type),
        str(target_node_key),
        str(edge_type),
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


class SessionUnit(Base):
    """Derived multi-granularity retrieval unit (`TURN | WINDOW | EPISODE`) linked to raw `messages`."""

    __tablename__ = "session_units"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "unit_kind",
            "ordinal",
            name="uq_session_units_thread_kind_ordinal",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "project_id",
            "thread_id",
            name="uq_session_units_scope_identity",
        ),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_session_units_tenant_id"),
        CheckConstraint("length(trim(project_id)) > 0", name="ck_session_units_project_id"),
        CheckConstraint(
            "unit_kind IN ('TURN', 'WINDOW', 'EPISODE')",
            name="ck_session_units_unit_kind",
        ),
        CheckConstraint(
            "unit_kind != 'TURN' OR message_from_id = message_to_id",
            name="ck_session_units_turn_single_msg",
        ),
        CheckConstraint(
            "message_from_id = message_to_id OR json_array_length(source_message_ids_json) >= 2",
            name="ck_session_units_span_source_ids",
        ),
        CheckConstraint("ordinal >= 0", name="ck_session_units_ordinal_nonnegative"),
        CheckConstraint("length(trim(episode_id)) > 0", name="ck_session_units_episode_id"),
        CheckConstraint(
            "episode_ordinal >= 0",
            name="ck_session_units_episode_ordinal_nonnegative",
        ),
        CheckConstraint(
            "epistemic_status IN ('FACT', 'PROPOSED', 'CONSIDERING', 'CONFIRMED', "
            "'SUPERSEDED', 'NEGATED', 'OPEN_ISSUE')",
            name="ck_session_units_epistemic_status",
        ),
        CheckConstraint("doc_word_len >= 0", name="ck_session_units_doc_word_len_nonnegative"),
        CheckConstraint("token_count >= 0", name="ck_session_units_token_count_nonnegative"),
        CheckConstraint("length(content_hash) = 64", name="ck_session_units_content_hash_len"),
        CheckConstraint(
            "length(trim(embedding_model_signature)) > 0",
            name="ck_session_units_embedding_sig",
        ),
        CheckConstraint("valid_to >= valid_from", name="ck_session_units_valid_window"),
        Index(
            "ix_session_units_project_thread",
            "tenant_id",
            "project_id",
            "thread_id",
            "unit_kind",
            "ordinal",
        ),
        Index("ix_session_units_thread_episode", "thread_id", "episode_id", "ordinal"),
        Index("ix_session_units_message_from", "message_from_id"),
        Index("ix_session_units_message_to", "message_to_id"),
        Index(
            "ix_session_units_temporal",
            "thread_id",
            "epistemic_status",
            "valid_from",
            "valid_to",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    thread_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit_kind: Mapped[str] = mapped_column(
        String(16),
        default="TURN",
        server_default="TURN",
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_id: Mapped[str] = mapped_column(String(64), nullable=False)
    episode_ordinal: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    window_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_from_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_to_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_message_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    epistemic_status: Mapped[str] = mapped_column(
        String(24),
        default="FACT",
        server_default="FACT",
        nullable=False,
    )
    topic_slot_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_anaphoric_or_short: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    security_partition_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    cited_source_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    entity_mentions_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    sentences_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    term_freqs_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    doc_word_len: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model_signature: Mapped[str] = mapped_column(
        String(128),
        default="deterministic-ngram-v1",
        server_default="deterministic-ngram-v1",
        nullable=False,
    )
    qdrant_point_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    valid_to: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: SESSION_OPEN_ENDED_VALID_TO,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SessionUnitMessage(Base):
    """Constituent message membership ensuring middle-of-window/episode cascade deletion."""

    __tablename__ = "session_unit_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_unit_id", "tenant_id", "project_id", "thread_id"],
            [
                "session_units.id",
                "session_units.tenant_id",
                "session_units.project_id",
                "session_units.thread_id",
            ],
            name="fk_session_unit_messages_unit_scope",
            ondelete="CASCADE",
        ),
        Index("ix_session_unit_messages_message", "message_id"),
        Index("ix_session_unit_messages_thread_msg", "thread_id", "message_id"),
    )

    session_unit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(32), nullable=False)


class SessionDecisionLedger(Base):
    """Bi-temporal working & decision state ledger (`[valid_from, valid_to)`) with `SUPERSEDES` lineage."""

    __tablename__ = "session_decision_ledger"
    __table_args__ = (
        UniqueConstraint(
            "session_unit_id",
            "topic_slot_key",
            name="uq_session_decision_unit_slot",
        ),
        ForeignKeyConstraint(
            ["session_unit_id", "tenant_id", "project_id", "thread_id"],
            [
                "session_units.id",
                "session_units.tenant_id",
                "session_units.project_id",
                "session_units.thread_id",
            ],
            name="fk_session_decision_unit_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_session_decision_tenant_id"),
        CheckConstraint("length(trim(project_id)) > 0", name="ck_session_decision_project_id"),
        CheckConstraint(
            "ledger_kind IN ('DECISION', 'OPEN_TASK', 'CONSTRAINT', 'OPEN_ISSUE')",
            name="ck_session_decision_ledger_kind",
        ),
        CheckConstraint(
            "length(trim(topic_slot_key)) > 0",
            name="ck_session_decision_topic_slot_key",
        ),
        CheckConstraint(
            "turn_ordinal >= 0",
            name="ck_session_decision_turn_ordinal_nonnegative",
        ),
        CheckConstraint(
            "epistemic_status IN ('FACT', 'PROPOSED', 'CONSIDERING', 'CONFIRMED', "
            "'SUPERSEDED', 'NEGATED', 'OPEN_ISSUE')",
            name="ck_session_decision_epistemic_status",
        ),
        CheckConstraint("valid_to >= valid_from", name="ck_session_decision_valid_window"),
        Index(
            "ix_session_decision_thread_slot_status",
            "thread_id",
            "topic_slot_key",
            "epistemic_status",
            "turn_ordinal",
        ),
        Index(
            "ix_session_decision_project_status",
            "tenant_id",
            "project_id",
            "thread_id",
            "epistemic_status",
        ),
        Index("ix_session_decision_supersedes", "supersedes_id"),
        Index("ix_session_decision_superseded_by", "superseded_by_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    thread_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ledger_kind: Mapped[str] = mapped_column(
        String(24),
        default="DECISION",
        server_default="DECISION",
        nullable=False,
    )
    topic_slot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(24), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_keys_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    superseded_entity_keys_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    companion_unit_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("session_decision_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("session_decision_ledger.id", ondelete="SET NULL"),
        nullable=True,
    )
    supersedes_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    security_partition_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    cited_source_ids_json: Mapped[list] = mapped_column(
        JSON,
        default=list,
        server_default=text("'[]'"),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    valid_to: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: SESSION_OPEN_ENDED_VALID_TO,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SessionGraphEdge(Base):
    """Provenance-anchored graph edge (`Entity ↔ Sentence ↔ Unit`, `ADJACENT`, `SUPERSEDES`, `ALIAS`)."""

    __tablename__ = "session_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_unit_id",
            "source_node_type",
            "source_node_key",
            "target_node_type",
            "target_node_key",
            "edge_type",
            name="uq_session_graph_edges_provenance",
        ),
        ForeignKeyConstraint(
            ["source_unit_id", "tenant_id", "project_id", "thread_id"],
            [
                "session_units.id",
                "session_units.tenant_id",
                "session_units.project_id",
                "session_units.thread_id",
            ],
            name="fk_session_graph_edges_source_unit_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_unit_id", "tenant_id", "project_id", "thread_id"],
            [
                "session_units.id",
                "session_units.tenant_id",
                "session_units.project_id",
                "session_units.thread_id",
            ],
            name="fk_session_graph_edges_target_unit_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("length(trim(tenant_id)) > 0", name="ck_session_graph_edges_tenant_id"),
        CheckConstraint("length(trim(project_id)) > 0", name="ck_session_graph_edges_project_id"),
        CheckConstraint(
            "source_node_type IN ('UNIT', 'SENTENCE', 'ENTITY', 'EPISODE', 'SOURCE')",
            name="ck_session_graph_edges_source_node_type",
        ),
        CheckConstraint(
            "target_node_type IN ('UNIT', 'SENTENCE', 'ENTITY', 'EPISODE', 'SOURCE')",
            name="ck_session_graph_edges_target_node_type",
        ),
        CheckConstraint(
            "length(trim(source_node_key)) > 0",
            name="ck_session_graph_edges_source_node_key",
        ),
        CheckConstraint(
            "length(trim(target_node_key)) > 0",
            name="ck_session_graph_edges_target_node_key",
        ),
        CheckConstraint(
            "edge_type IN ('ENTITY_MENTION', 'SENTENCE_IN_UNIT', 'ADJACENT_IN_EPISODE', "
            "'SUPERSEDES', 'ALIAS_CANDIDATE', 'CITES_DOCUMENT_SOURCE')",
            name="ck_session_graph_edges_edge_type",
        ),
        CheckConstraint("weight >= 0.0", name="ck_session_graph_edges_weight_nonnegative"),
        Index("ix_session_graph_edges_thread_type", "thread_id", "edge_type"),
        Index("ix_session_graph_edges_source_node", "thread_id", "source_node_key"),
        Index("ix_session_graph_edges_target_node", "thread_id", "target_node_key"),
        Index("ix_session_graph_edges_source_unit", "source_unit_id"),
        Index("ix_session_graph_edges_target_unit", "target_unit_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[str] = mapped_column(String(256), nullable=False)
    thread_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_unit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_node_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    target_node_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(
        Float,
        default=1.0,
        server_default=text("1.0"),
        nullable=False,
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
    )


_SQLITE_UNIT_UPDATE_GUARD = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_units_before_update_guard
    BEFORE UPDATE ON session_units
    FOR EACH ROW
    WHEN NEW.tenant_id IS NOT OLD.tenant_id
      OR NEW.project_id IS NOT OLD.project_id
      OR NEW.thread_id IS NOT OLD.thread_id
      OR NEW.unit_kind IS NOT OLD.unit_kind
      OR NEW.message_from_id IS NOT OLD.message_from_id
      OR NEW.message_to_id IS NOT OLD.message_to_id
      OR CAST(NEW.source_message_ids_json AS TEXT) IS NOT CAST(OLD.source_message_ids_json AS TEXT)
    BEGIN
        SELECT RAISE(ABORT, 'SessionUnit provenance identity and constituent messages are immutable');
    END;
    """
)

_SQLITE_UNIT_AFTER_INSERT_LINK = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_units_after_insert_link
    AFTER INSERT ON session_units
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN json_type(NEW.source_message_ids_json) IS NOT 'array' THEN
                RAISE(ABORT, 'source_message_ids_json must be a JSON array')
            WHEN EXISTS (
                SELECT 1 FROM json_each(NEW.source_message_ids_json)
                WHERE type <> 'text'
            ) THEN
                RAISE(ABORT, 'source_message_ids_json elements must all be JSON strings')
            WHEN NEW.message_from_id <> NEW.message_to_id AND (
                NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.source_message_ids_json)
                    WHERE CAST(value AS TEXT) = NEW.message_from_id
                )
                OR NOT EXISTS (
                    SELECT 1 FROM json_each(NEW.source_message_ids_json)
                    WHERE CAST(value AS TEXT) = NEW.message_to_id
                )
            ) THEN
                RAISE(ABORT, 'Multi-message SessionUnit source_message_ids_json must include endpoints')
        END;

        INSERT OR IGNORE INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
        VALUES (NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, NEW.message_from_id);

        INSERT OR IGNORE INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
        VALUES (NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, NEW.message_to_id);

        INSERT OR IGNORE INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
        SELECT NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, CAST(value AS TEXT)
          FROM json_each(NEW.source_message_ids_json);

        INSERT OR IGNORE INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
        SELECT NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, u.message_from_id
          FROM session_units u,
               (SELECT ordinal AS ord_from FROM session_units
                 WHERE thread_id = NEW.thread_id AND unit_kind = 'TURN'
                   AND message_from_id = NEW.message_from_id LIMIT 1) uf,
               (SELECT ordinal AS ord_to FROM session_units
                 WHERE thread_id = NEW.thread_id AND unit_kind = 'TURN'
                   AND message_from_id = NEW.message_to_id LIMIT 1) ut
         WHERE NEW.message_from_id <> NEW.message_to_id
           AND u.thread_id = NEW.thread_id
           AND u.unit_kind = 'TURN'
           AND uf.ord_from <= ut.ord_to
           AND u.ordinal BETWEEN uf.ord_from AND ut.ord_to;

        SELECT CASE
            WHEN EXISTS (
                SELECT 1
                  FROM session_unit_messages sum_link
                  JOIN messages m ON m.id = sum_link.message_id
                 WHERE sum_link.session_unit_id = NEW.id
                   AND m.thread_id <> NEW.thread_id
            ) THEN
                RAISE(ABORT, 'SessionUnit constituent message belongs to a different thread_id')
        END;
    END;
    """
)

_SQLITE_UNIT_MSG_IMMUTABLE = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_unit_messages_before_update_guard
    BEFORE UPDATE ON session_unit_messages
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'session_unit_messages rows are immutable');
    END;
    """
)

_SQLITE_UNIT_MSG_CASCADE = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_unit_messages_cascade_unit
    AFTER DELETE ON session_unit_messages
    FOR EACH ROW
    BEGIN
        DELETE FROM session_units WHERE id = OLD.session_unit_id;
    END;
    """
)

_SQLITE_DECISION_LINEAGE_INS = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_decision_lineage_scope_ins
    BEFORE INSERT ON session_decision_ledger
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN NEW.supersedes_id IS NOT NULL AND NEW.supersedes_id = NEW.id THEN
                RAISE(ABORT, 'session_decision_ledger cannot supersede itself')
            WHEN NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM session_decision_ledger p
                 WHERE p.id = NEW.supersedes_id
                   AND p.tenant_id = NEW.tenant_id
                   AND p.project_id = NEW.project_id
                   AND p.thread_id = NEW.thread_id
            ) THEN
                RAISE(ABORT, 'supersedes_id must belong to same tenant/project/thread')
            WHEN NEW.superseded_by_id IS NOT NULL AND NEW.superseded_by_id = NEW.id THEN
                RAISE(ABORT, 'session_decision_ledger cannot be superseded by itself')
            WHEN NEW.superseded_by_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM session_decision_ledger n
                 WHERE n.id = NEW.superseded_by_id
                   AND n.tenant_id = NEW.tenant_id
                   AND n.project_id = NEW.project_id
                   AND n.thread_id = NEW.thread_id
            ) THEN
                RAISE(ABORT, 'superseded_by_id must belong to same tenant/project/thread')
        END;
    END;
    """
)

_SQLITE_DECISION_LINEAGE_UPD = DDL(
    """
    CREATE TRIGGER IF NOT EXISTS trg_session_decision_lineage_scope_upd
    BEFORE UPDATE ON session_decision_ledger
    FOR EACH ROW
    BEGIN
        SELECT CASE
            WHEN NEW.tenant_id IS NOT OLD.tenant_id
              OR NEW.project_id IS NOT OLD.project_id
              OR NEW.thread_id IS NOT OLD.thread_id
              OR NEW.session_unit_id IS NOT OLD.session_unit_id THEN
                RAISE(ABORT, 'session_decision_ledger scope identity is immutable')
            WHEN NEW.supersedes_id IS NOT NULL AND NEW.supersedes_id IS NOT OLD.supersedes_id
              AND NEW.supersedes_id = NEW.id THEN
                RAISE(ABORT, 'session_decision_ledger cannot supersede itself')
            WHEN NEW.supersedes_id IS NOT NULL AND NEW.supersedes_id IS NOT OLD.supersedes_id
              AND NOT EXISTS (
                SELECT 1 FROM session_decision_ledger p
                 WHERE p.id = NEW.supersedes_id
                   AND p.tenant_id = NEW.tenant_id
                   AND p.project_id = NEW.project_id
                   AND p.thread_id = NEW.thread_id
            ) THEN
                RAISE(ABORT, 'supersedes_id must belong to same tenant/project/thread')
            WHEN NEW.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS NOT OLD.superseded_by_id
              AND NEW.superseded_by_id = NEW.id THEN
                RAISE(ABORT, 'session_decision_ledger cannot be superseded by itself')
            WHEN NEW.superseded_by_id IS NOT NULL AND NEW.superseded_by_id IS NOT OLD.superseded_by_id
              AND NOT EXISTS (
                SELECT 1 FROM session_decision_ledger n
                 WHERE n.id = NEW.superseded_by_id
                   AND n.tenant_id = NEW.tenant_id
                   AND n.project_id = NEW.project_id
                   AND n.thread_id = NEW.thread_id
            ) THEN
                RAISE(ABORT, 'superseded_by_id must belong to same tenant/project/thread')
        END;
    END;
    """
)

event.listen(
    SessionUnitMessage.__table__,
    "after_create",
    _SQLITE_UNIT_UPDATE_GUARD.execute_if(dialect="sqlite"),
)
event.listen(
    SessionUnitMessage.__table__,
    "after_create",
    _SQLITE_UNIT_AFTER_INSERT_LINK.execute_if(dialect="sqlite"),
)
event.listen(
    SessionUnitMessage.__table__,
    "after_create",
    _SQLITE_UNIT_MSG_IMMUTABLE.execute_if(dialect="sqlite"),
)
event.listen(
    SessionUnitMessage.__table__,
    "after_create",
    _SQLITE_UNIT_MSG_CASCADE.execute_if(dialect="sqlite"),
)
event.listen(
    SessionDecisionLedger.__table__,
    "after_create",
    _SQLITE_DECISION_LINEAGE_INS.execute_if(dialect="sqlite"),
)
event.listen(
    SessionDecisionLedger.__table__,
    "after_create",
    _SQLITE_DECISION_LINEAGE_UPD.execute_if(dialect="sqlite"),
)

_PG_UNIT_MSG_DDLS = (
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_session_units_before_update_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.project_id IS DISTINCT FROM OLD.project_id
               OR NEW.thread_id IS DISTINCT FROM OLD.thread_id
               OR NEW.unit_kind IS DISTINCT FROM OLD.unit_kind
               OR NEW.message_from_id IS DISTINCT FROM OLD.message_from_id
               OR NEW.message_to_id IS DISTINCT FROM OLD.message_to_id
               OR CAST(NEW.source_message_ids_json AS TEXT)
                  IS DISTINCT FROM CAST(OLD.source_message_ids_json AS TEXT) THEN
                RAISE EXCEPTION 'SessionUnit provenance identity and constituent messages are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_session_units_before_update_guard ON session_units"),
    DDL(
        """
        CREATE TRIGGER trg_session_units_before_update_guard
        BEFORE UPDATE ON session_units
        FOR EACH ROW
        EXECUTE FUNCTION fn_session_units_before_update_guard()
        """
    ),
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_session_units_after_insert_link()
        RETURNS TRIGGER AS $$
        DECLARE
            bad_thread_count INTEGER;
            has_from BOOLEAN;
            has_to BOOLEAN;
        BEGIN
            IF json_typeof(NEW.source_message_ids_json) <> 'array' THEN
                RAISE EXCEPTION 'source_message_ids_json must be a JSON array'
                    USING ERRCODE = '23514';
            END IF;

            IF EXISTS (
                SELECT 1 FROM json_array_elements(NEW.source_message_ids_json) AS raw_elem
                WHERE json_typeof(raw_elem) <> 'string'
            ) THEN
                RAISE EXCEPTION 'source_message_ids_json elements must all be JSON strings'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.message_from_id <> NEW.message_to_id THEN
                SELECT EXISTS (
                    SELECT 1 FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
                    WHERE elem = NEW.message_from_id
                ) INTO has_from;
                SELECT EXISTS (
                    SELECT 1 FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
                    WHERE elem = NEW.message_to_id
                ) INTO has_to;
                IF NOT has_from OR NOT has_to THEN
                    RAISE EXCEPTION
                        'Multi-message SessionUnit source_message_ids_json must include endpoints'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            INSERT INTO session_unit_messages (session_unit_id, tenant_id, project_id, thread_id, message_id)
            SELECT DISTINCT NEW.id, NEW.tenant_id, NEW.project_id, NEW.thread_id, candidate_msg_id
            FROM (
                SELECT NEW.message_from_id AS candidate_msg_id
                UNION ALL
                SELECT NEW.message_to_id AS candidate_msg_id
                UNION ALL
                SELECT elem AS candidate_msg_id
                  FROM json_array_elements_text(NEW.source_message_ids_json) AS elem
                UNION ALL
                SELECT u.message_from_id AS candidate_msg_id
                  FROM session_units u,
                       (SELECT ordinal AS ord_from FROM session_units
                         WHERE thread_id = NEW.thread_id
                           AND unit_kind = 'TURN'
                           AND message_from_id = NEW.message_from_id
                         LIMIT 1) uf,
                       (SELECT ordinal AS ord_to FROM session_units
                         WHERE thread_id = NEW.thread_id
                           AND unit_kind = 'TURN'
                           AND message_from_id = NEW.message_to_id
                         LIMIT 1) ut
                 WHERE NEW.message_from_id <> NEW.message_to_id
                   AND u.thread_id = NEW.thread_id
                   AND u.unit_kind = 'TURN'
                   AND uf.ord_from <= ut.ord_to
                   AND u.ordinal BETWEEN uf.ord_from AND ut.ord_to
            ) AS candidates
            ON CONFLICT (session_unit_id, message_id) DO NOTHING;

            SELECT COUNT(*) INTO bad_thread_count
              FROM session_unit_messages sum_link
              JOIN messages m ON m.id = sum_link.message_id
             WHERE sum_link.session_unit_id = NEW.id
               AND m.thread_id <> NEW.thread_id;

            IF bad_thread_count > 0 THEN
                RAISE EXCEPTION 'SessionUnit constituent message belongs to a different thread_id'
                    USING ERRCODE = '23503';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_session_units_after_insert_link ON session_units"),
    DDL(
        """
        CREATE TRIGGER trg_session_units_after_insert_link
        AFTER INSERT ON session_units
        FOR EACH ROW
        EXECUTE FUNCTION fn_session_units_after_insert_link()
        """
    ),
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_session_unit_messages_before_update_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'session_unit_messages rows are immutable'
                USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_session_unit_messages_before_update_guard ON session_unit_messages"),
    DDL(
        """
        CREATE TRIGGER trg_session_unit_messages_before_update_guard
        BEFORE UPDATE ON session_unit_messages
        FOR EACH ROW
        EXECUTE FUNCTION fn_session_unit_messages_before_update_guard()
        """
    ),
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_session_unit_messages_cascade_unit()
        RETURNS TRIGGER AS $$
        BEGIN
            DELETE FROM session_units WHERE id = OLD.session_unit_id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_session_unit_messages_cascade_unit ON session_unit_messages"),
    DDL(
        """
        CREATE TRIGGER trg_session_unit_messages_cascade_unit
        AFTER DELETE ON session_unit_messages
        FOR EACH ROW
        EXECUTE FUNCTION fn_session_unit_messages_cascade_unit()
        """
    ),
)

_PG_DECISION_DDLS = (
    DDL(
        """
        CREATE OR REPLACE FUNCTION fn_session_decision_lineage_scope_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
                   OR NEW.project_id IS DISTINCT FROM OLD.project_id
                   OR NEW.thread_id IS DISTINCT FROM OLD.thread_id
                   OR NEW.session_unit_id IS DISTINCT FROM OLD.session_unit_id THEN
                    RAISE EXCEPTION 'session_decision_ledger scope identity is immutable'
                        USING ERRCODE = '23514';
                END IF;
            END IF;

            IF NEW.supersedes_id IS NOT NULL
               AND (TG_OP = 'INSERT' OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id) THEN
                IF NEW.supersedes_id = NEW.id THEN
                    RAISE EXCEPTION 'session_decision_ledger cannot supersede itself'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM session_decision_ledger p
                     WHERE p.id = NEW.supersedes_id
                       AND p.tenant_id = NEW.tenant_id
                       AND p.project_id = NEW.project_id
                       AND p.thread_id = NEW.thread_id
                ) THEN
                    RAISE EXCEPTION 'supersedes_id must belong to the same tenant_id, project_id, and thread_id'
                        USING ERRCODE = '23503';
                END IF;
            END IF;

            IF NEW.superseded_by_id IS NOT NULL
               AND (TG_OP = 'INSERT' OR NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id) THEN
                IF NEW.superseded_by_id = NEW.id THEN
                    RAISE EXCEPTION 'session_decision_ledger cannot be superseded by itself'
                        USING ERRCODE = '23514';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM session_decision_ledger n
                     WHERE n.id = NEW.superseded_by_id
                       AND n.tenant_id = NEW.tenant_id
                       AND n.project_id = NEW.project_id
                       AND n.thread_id = NEW.thread_id
                ) THEN
                    RAISE EXCEPTION 'superseded_by_id must belong to the same tenant_id, project_id, and thread_id'
                        USING ERRCODE = '23503';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    ),
    DDL("DROP TRIGGER IF EXISTS trg_session_decision_lineage_scope_guard ON session_decision_ledger"),
    DDL(
        """
        CREATE TRIGGER trg_session_decision_lineage_scope_guard
        BEFORE INSERT OR UPDATE ON session_decision_ledger
        FOR EACH ROW
        EXECUTE FUNCTION fn_session_decision_lineage_scope_guard()
        """
    ),
)

for _pg_ddl in _PG_UNIT_MSG_DDLS:
    event.listen(
        SessionUnitMessage.__table__,
        "after_create",
        _pg_ddl.execute_if(dialect="postgresql"),
    )

for _pg_ddl in _PG_DECISION_DDLS:
    event.listen(
        SessionDecisionLedger.__table__,
        "after_create",
        _pg_ddl.execute_if(dialect="postgresql"),
    )
