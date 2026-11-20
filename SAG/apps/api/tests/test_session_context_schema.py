"""Schema, migration (`up -> down -> up`), scope-FK, default parity, and window cascade tests for Chặng 2."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from sag_api.db.base import Base
from sag_api.db.models import (
    Agent,
    Message,
    SessionDecisionLedger,
    SessionGraphEdge,
    SessionUnit,
    SessionUnitMessage,
    Thread,
)
from sag_api.db.models.session_context import (
    SESSION_OPEN_ENDED_VALID_TO,
    generate_session_decision_id,
    generate_session_graph_edge_id,
    generate_session_unit_id,
    generate_session_unit_point_id,
)
from sag_api.enums import MessageRole

API_ROOT = Path(__file__).resolve().parents[1]
UP_SQL_PATH = API_ROOT / "migrations" / "0009_session_context_ecce.sql"
DOWN_SQL_PATH = API_ROOT / "scripts" / "0009_session_context_ecce_down.sql"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _create_fk_sqlite_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record) -> None:  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA recursive_triggers=ON")
        cur.close()

    return engine


def _split_sql_statements(raw_sql: str, *, sqlite_compatible: bool = False) -> list[str]:
    """Split SQL script into executable statements while respecting `$$ ... $$` blocks."""
    cleaned_lines = [
        line for line in raw_sql.splitlines() if not line.strip().startswith("--")
    ]
    body = "\n".join(cleaned_lines)
    stmts: list[str] = []
    buf: list[str] = []
    in_dollar = False
    idx = 0
    n = len(body)

    while idx < n:
        if body[idx : idx + 2] == "$$":
            in_dollar = not in_dollar
            buf.append("$$")
            idx += 2
            continue
        ch = body[idx]
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            buf.clear()
            if stmt and stmt.upper() not in {"BEGIN", "COMMIT"}:
                stmts.append(stmt)
            idx += 1
            continue
        buf.append(ch)
        idx += 1

    tail = "".join(buf).strip()
    if tail and tail.upper() not in {"BEGIN", "COMMIT"}:
        stmts.append(tail)

    if sqlite_compatible:
        pg_only_prefixes = (
            "CREATE OR REPLACE FUNCTION ",
            "DROP FUNCTION ",
            "DROP TRIGGER ",
            "CREATE TRIGGER ",
        )
        stmts = [
            s for s in stmts if not s.upper().startswith(pg_only_prefixes)
        ]
    return stmts


def test_deterministic_uuidv5_generators_and_no_delimiter_collision() -> None:
    u1 = generate_session_unit_id("thr_01", "TURN", 0, "msg_01")
    u1_repeat = generate_session_unit_id("thr_01", "TURN", 0, "msg_01")
    u2 = generate_session_unit_id("thr_01", "TURN", 1, "msg_02")
    u_win = generate_session_unit_id("thr_01", "WINDOW", 0, "msg_01")

    assert u1 == u1_repeat
    assert u1 != u2
    assert u1 != u_win
    assert len(u1) == 36
    assert uuid.UUID(u1).version == 5

    # Finding 4 hardening: delimiter-framing inputs containing `:` must NEVER collide
    assert generate_session_unit_id("thr:a", "TURN", 1, "m1") != generate_session_unit_id(
        "thr", "a:TURN", 1, "m1"
    )
    assert generate_session_unit_point_id("col:a", "b:u1") != generate_session_unit_point_id(
        "col:a:b", "u1"
    )
    assert generate_session_decision_id("unit:a", "b:slot") != generate_session_decision_id(
        "unit:a:b", "slot"
    )
    assert generate_session_graph_edge_id(
        "u1", "entity:a:b", "c", "ALIAS_CANDIDATE"
    ) != generate_session_graph_edge_id("u1", "entity:a", "b:c", "ALIAS_CANDIDATE")
    assert generate_session_graph_edge_id(
        "u1:x", "y", "z", "SUPERSEDES"
    ) != generate_session_graph_edge_id("u1", "x:y", "z", "SUPERSEDES")
    assert generate_session_graph_edge_id(
        u1, "k1", "k2", "ENTITY_MENTION", source_node_type="UNIT", target_node_type="ENTITY"
    ) != generate_session_graph_edge_id(
        u1, "k1", "k2", "ENTITY_MENTION", source_node_type="SENTENCE", target_node_type="ENTITY"
    )

    pt1 = generate_session_unit_point_id("session_units_proj_a", u1)
    assert pt1 == generate_session_unit_point_id("session_units_proj_a", u1)
    assert pt1 != generate_session_unit_point_id("session_units_proj_b", u1)
    assert uuid.UUID(pt1).version == 5

    d1 = generate_session_decision_id(u1, "slot:database")
    assert d1 == generate_session_decision_id(u1, "slot:database")
    assert d1 != generate_session_decision_id(u1, "slot:cache")
    assert uuid.UUID(d1).version == 5

    e1 = generate_session_graph_edge_id(u1, f"unit:{u1}", "entity:postgresql", "ENTITY_MENTION")
    assert e1 == generate_session_graph_edge_id(u1, f"unit:{u1}", "entity:postgresql", "ENTITY_MENTION")
    assert e1 != generate_session_graph_edge_id(u1, f"unit:{u1}", "entity:postgresql", "SUPERSEDES")
    assert uuid.UUID(e1).version == 5


@pytest.mark.asyncio
async def test_sql_migration_up_down_up_and_orm_server_default_parity() -> None:
    assert UP_SQL_PATH.is_file(), f"Missing forward migration: {UP_SQL_PATH}"
    assert DOWN_SQL_PATH.is_file(), f"Missing rollback script: {DOWN_SQL_PATH}"

    up_stmts = _split_sql_statements(UP_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True)
    down_stmts = _split_sql_statements(DOWN_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True)

    # Finding 2 check A: Every defaulted SQL column must also have `server_default` on ORM metadata
    expected_server_default_cols = {
        SessionUnit: {
            "unit_kind",
            "episode_ordinal",
            "source_message_ids_json",
            "epistemic_status",
            "is_anaphoric_or_short",
            "security_partition_ids_json",
            "cited_source_ids_json",
            "entity_mentions_json",
            "sentences_json",
            "term_freqs_json",
            "doc_word_len",
            "token_count",
            "embedding_model_signature",
            "valid_from",
            "created_at",
            "updated_at",
        },
        SessionDecisionLedger: {
            "ledger_kind",
            "entity_keys_json",
            "superseded_entity_keys_json",
            "companion_unit_ids_json",
            "supersedes_ids_json",
            "security_partition_ids_json",
            "cited_source_ids_json",
            "valid_from",
            "created_at",
            "updated_at",
        },
        SessionGraphEdge: {
            "weight",
            "metadata_json",
            "created_at",
        },
    }
    for model_cls, expected_cols in expected_server_default_cols.items():
        actual_cols = {
            col.name for col in model_cls.__table__.columns if col.server_default is not None
        }
        assert actual_cols == expected_cols, (
            f"server_default mismatch on {model_cls.__tablename__}: {actual_cols ^ expected_cols}"
        )

    engine = _create_fk_sqlite_engine()
    orm_engine = _create_fk_sqlite_engine()
    try:
        # 1. Create parent truth tables (`agents`, `threads`, `messages`) in SQL-migrated DB
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Agent.__table__, Thread.__table__, Message.__table__],
            )
            await conn.execute(
                text(
                    "INSERT INTO agents (id, name, avatar, is_default, persona_json) "
                    "VALUES ('ag1', 'Test Agent', '', 0, '{}')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO threads (id, owner_key, agent_id, title, archived) "
                    "VALUES ('thr1', 'continuum:u1', 'ag1', 'Session 1', 0)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO messages (id, thread_id, attachments_json, steps_json, "
                    "role, content, citations_json, prompt_preview, status) "
                    "VALUES ('msg1', 'thr1', '[]', '[]', 'user', 'Chốt PostgreSQL 18', '[]', '', 'ok')"
                )
            )

        # 2. Apply `0009_session_context_ecce.sql` twice (idempotency check) and create ORM DB
        for _ in range(2):
            async with engine.begin() as conn:
                for stmt in up_stmts:
                    await conn.exec_driver_sql(stmt)

        async with orm_engine.begin() as orm_conn:
            await orm_conn.run_sync(Base.metadata.create_all)

        # Compare columns, indexes, and DB-level default presence across all 4 derived tables
        derived_models = (
            SessionUnit,
            SessionUnitMessage,
            SessionDecisionLedger,
            SessionGraphEdge,
        )
        async with engine.connect() as conn, orm_engine.connect() as orm_conn:
            for model_cls in derived_models:
                table_name = model_cls.__tablename__
                sql_col_map = await conn.run_sync(
                    lambda sync_conn, t=table_name: {
                        c["name"]: c for c in inspect(sync_conn).get_columns(t)
                    }
                )
                orm_col_map = await orm_conn.run_sync(
                    lambda sync_conn, t=table_name: {
                        c["name"]: c for c in inspect(sync_conn).get_columns(t)
                    }
                )
                orm_cols = {col.name for col in model_cls.__table__.columns}
                assert set(sql_col_map) == orm_cols, (
                    f"Column drift in {table_name}: {set(sql_col_map) ^ orm_cols}"
                )
                for col_name, sql_col in sql_col_map.items():
                    orm_db_col = orm_col_map[col_name]
                    assert (sql_col["default"] is not None) == (orm_db_col["default"] is not None), (
                        f"DB default presence drift on {table_name}.{col_name}: "
                        f"sql={sql_col['default']!r} vs orm={orm_db_col['default']!r}"
                    )

                sql_indexes = await conn.run_sync(
                    lambda sync_conn, t=table_name: {
                        idx["name"] for idx in inspect(sync_conn).get_indexes(t) if idx.get("name")
                    }
                )
                orm_indexes = {idx.name for idx in model_cls.__table__.indexes if idx.name}
                assert orm_indexes.issubset(sql_indexes), (
                    f"Missing index in {table_name}: {orm_indexes - sql_indexes}"
                )

        # Finding 2 check B: Raw SQL INSERT on ORM-created tables omitting `sentences_json` & JSON defaults
        async with orm_engine.begin() as orm_conn:
            await orm_conn.execute(
                text(
                    "INSERT INTO agents (id, name, avatar, is_default, persona_json) "
                    "VALUES ('ag1', 'ORM Agent', '', 0, '{}')"
                )
            )
            await orm_conn.execute(
                text(
                    "INSERT INTO threads (id, owner_key, agent_id, title, archived) "
                    "VALUES ('thr1', 'continuum:u1', 'ag1', 'ORM Session', 0)"
                )
            )
            await orm_conn.execute(
                text(
                    "INSERT INTO messages (id, thread_id, attachments_json, steps_json, "
                    "role, content, citations_json, prompt_preview, status) "
                    "VALUES ('msg1', 'thr1', '[]', '[]', 'user', 'ORM raw insert check', '[]', '', 'ok')"
                )
            )
            raw_unit_id = generate_session_unit_id("thr1", "TURN", 0, "msg1")
            await orm_conn.execute(
                text(
                    "INSERT INTO session_units "
                    "(id, tenant_id, project_id, thread_id, ordinal, episode_id, "
                    " message_from_id, message_to_id, content_hash, valid_to) "
                    "VALUES (:uid, 'org_1', 'proj_1', 'thr1', 0, 'ep:0', "
                    " 'msg1', 'msg1', :chash, '9999-12-31 23:59:59')"
                ),
                {"uid": raw_unit_id, "chash": _sha256("ORM raw insert check")},
            )
            row = (
                await orm_conn.execute(
                    text(
                        "SELECT unit_kind, sentences_json, entity_mentions_json, "
                        "term_freqs_json, source_message_ids_json, doc_word_len, token_count "
                        "FROM session_units WHERE id = :uid"
                    ),
                    {"uid": raw_unit_id},
                )
            ).one()
            assert row[0] == "TURN"
            assert row[1] == "[]"
            assert row[2] == "[]"
            assert row[3] == "{}"
            assert row[4] == "[]"
            assert row[5] == 1
            assert row[6] == 0

        # 3. Apply `0009_session_context_ecce_down.sql` -> all 4 derived tables removed, `messages` intact
        async with engine.begin() as conn:
            for stmt in down_stmts:
                await conn.exec_driver_sql(stmt)

        async with engine.connect() as conn:
            remaining_tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            for dropped in (
                "session_units",
                "session_unit_messages",
                "session_decision_ledger",
                "session_graph_edges",
            ):
                assert dropped not in remaining_tables
            msg_count = (
                await conn.execute(text("SELECT COUNT(*) FROM messages WHERE id = 'msg1'"))
            ).scalar_one()
            assert msg_count == 1

        # 4. Re-apply `0009_session_context_ecce.sql` (`up -> down -> up` round-trip)
        async with engine.begin() as conn:
            for stmt in up_stmts:
                await conn.exec_driver_sql(stmt)
            restored_tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            assert {
                "session_units",
                "session_unit_messages",
                "session_decision_ledger",
                "session_graph_edges",
            }.issubset(restored_tables)
    finally:
        await engine.dispose()
        await orm_engine.dispose()


@pytest.mark.asyncio
async def test_cascade_cleanup_including_middle_of_window_message_delete() -> None:
    engine = _create_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            agent = Agent(owner_key="continuum:thang", name="ECCE Schema Agent")
            session.add(agent)
            await session.flush()

            thread = Thread(agent_id=agent.id, owner_key="continuum:thang", title="DB Lineage Session")
            session.add(thread)
            await session.flush()

            # Messages A, B, C (with TURN units + WINDOW A-B-C) and standalone X, Y, Z (WINDOW X-Y-Z only)
            m_a = Message(thread_id=thread.id, role=MessageRole.USER, content="A: Khởi tạo dự án SAG.")
            m_b = Message(
                thread_id=thread.id,
                role=MessageRole.USER,
                content="B: Đề xuất dùng MongoDB, anh Minh đồng ý.",
            )
            m_c = Message(thread_id=thread.id, role=MessageRole.USER, content="C: Bỏ MongoDB, chốt dùng PostgreSQL 18.")
            m_x = Message(thread_id=thread.id, role=MessageRole.USER, content="X: Mở đầu đoạn phụ.")
            m_y = Message(thread_id=thread.id, role=MessageRole.USER, content="Y: Bí mật ở giữa window X-Y-Z.")
            m_z = Message(thread_id=thread.id, role=MessageRole.ASSISTANT, content="Z: Kết đoạn phụ.")
            session.add_all([m_a, m_b, m_c, m_x, m_y, m_z])
            await session.flush()

            u_a = SessionUnit(
                id=generate_session_unit_id(thread.id, "TURN", 0, m_a.id),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                owner_key=thread.owner_key,
                unit_kind="TURN",
                ordinal=0,
                episode_id=f"ep:{thread.id}:0",
                episode_ordinal=0,
                window_id=f"win:{thread.id}:0",
                message_from_id=m_a.id,
                message_to_id=m_a.id,
                role="user",
                epistemic_status="FACT",
                content_hash=_sha256(m_a.content),
                token_count=8,
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            u_b = SessionUnit(
                id=generate_session_unit_id(thread.id, "TURN", 1, m_b.id),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                owner_key=thread.owner_key,
                unit_kind="TURN",
                ordinal=1,
                episode_id=f"ep:{thread.id}:0",
                episode_ordinal=0,
                window_id=f"win:{thread.id}:0",
                message_from_id=m_b.id,
                message_to_id=m_b.id,
                role="user",
                epistemic_status="SUPERSEDED",
                topic_slot_key="slot:database",
                content_hash=_sha256(m_b.content),
                token_count=12,
                valid_from=now + timedelta(minutes=1),
                valid_to=now + timedelta(minutes=5),
            )
            u_c = SessionUnit(
                id=generate_session_unit_id(thread.id, "TURN", 2, m_c.id),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                owner_key=thread.owner_key,
                unit_kind="TURN",
                ordinal=2,
                episode_id=f"ep:{thread.id}:0",
                episode_ordinal=0,
                window_id=f"win:{thread.id}:0",
                message_from_id=m_c.id,
                message_to_id=m_c.id,
                role="user",
                epistemic_status="CONFIRMED",
                topic_slot_key="slot:database",
                content_hash=_sha256(m_c.content),
                token_count=11,
                valid_from=now + timedelta(minutes=5),
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add_all([u_a, u_b, u_c])
            await session.flush()

            # Window A-B-C containing derived text/sentences from middle message B
            w_abc_text = f"{m_a.content}\n{m_b.content}\n{m_c.content}"
            w_abc = SessionUnit(
                id=generate_session_unit_id(thread.id, "WINDOW", 0, m_a.id),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                owner_key=thread.owner_key,
                unit_kind="WINDOW",
                ordinal=0,
                episode_id=f"ep:{thread.id}:0",
                episode_ordinal=0,
                window_id=f"win:{thread.id}:0",
                message_from_id=m_a.id,
                message_to_id=m_c.id,
                source_message_ids_json=[m_a.id, m_b.id, m_c.id],
                epistemic_status="CONFIRMED",
                sentences_json=[m_a.content, m_b.content, m_c.content],
                entity_mentions_json=["mongodb", "postgresql"],
                content_hash=_sha256(w_abc_text),
                token_count=31,
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            # Standalone Window X-Y-Z (no TURN units) containing middle message Y
            w_xyz_text = f"{m_x.content}\n{m_y.content}\n{m_z.content}"
            w_xyz = SessionUnit(
                id=generate_session_unit_id(thread.id, "WINDOW", 1, m_x.id),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                owner_key=thread.owner_key,
                unit_kind="WINDOW",
                ordinal=1,
                episode_id=f"ep:{thread.id}:1",
                episode_ordinal=1,
                window_id=f"win:{thread.id}:1",
                message_from_id=m_x.id,
                message_to_id=m_z.id,
                source_message_ids_json=[m_x.id, m_y.id, m_z.id],
                epistemic_status="FACT",
                sentences_json=[m_x.content, m_y.content, m_z.content],
                content_hash=_sha256(w_xyz_text),
                token_count=20,
                valid_from=now + timedelta(minutes=6),
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add_all([w_abc, w_xyz])
            await session.flush()

            d_b = SessionDecisionLedger(
                id=generate_session_decision_id(u_b.id, "slot:database"),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                session_unit_id=u_b.id,
                ledger_kind="DECISION",
                topic_slot_key="slot:database",
                turn_ordinal=1,
                epistemic_status="SUPERSEDED",
                summary_text=m_b.content,
                entity_keys_json=["mongodb"],
                valid_from=now + timedelta(minutes=1),
                valid_to=now + timedelta(minutes=5),
            )
            d_win = SessionDecisionLedger(
                id=generate_session_decision_id(w_abc.id, "slot:window_summary"),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                session_unit_id=w_abc.id,
                ledger_kind="DECISION",
                topic_slot_key="slot:window_summary",
                turn_ordinal=2,
                epistemic_status="FACT",
                summary_text=w_abc_text,
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add_all([d_b, d_win])
            await session.flush()

            d_c = SessionDecisionLedger(
                id=generate_session_decision_id(u_c.id, "slot:database"),
                tenant_id="org_01",
                project_id="proj_01",
                thread_id=thread.id,
                session_unit_id=u_c.id,
                ledger_kind="DECISION",
                topic_slot_key="slot:database",
                turn_ordinal=2,
                epistemic_status="CONFIRMED",
                summary_text=m_c.content,
                entity_keys_json=["postgresql"],
                superseded_entity_keys_json=["mongodb"],
                supersedes_id=d_b.id,
                supersedes_ids_json=[d_b.id],
                valid_from=now + timedelta(minutes=5),
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add(d_c)
            await session.flush()
            d_b.superseded_by_id = d_c.id

            edges = [
                SessionGraphEdge(
                    id=generate_session_graph_edge_id(
                        w_abc.id, f"unit:{w_abc.id}", "entity:mongodb", "ENTITY_MENTION"
                    ),
                    tenant_id="org_01",
                    project_id="proj_01",
                    thread_id=thread.id,
                    source_unit_id=w_abc.id,
                    target_unit_id=None,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{w_abc.id}",
                    target_node_type="ENTITY",
                    target_node_key="entity:mongodb",
                    edge_type="ENTITY_MENTION",
                    weight=0.9,
                ),
                SessionGraphEdge(
                    id=generate_session_graph_edge_id(
                        u_c.id, f"unit:{u_c.id}", f"unit:{u_b.id}", "SUPERSEDES"
                    ),
                    tenant_id="org_01",
                    project_id="proj_01",
                    thread_id=thread.id,
                    source_unit_id=u_c.id,
                    target_unit_id=u_b.id,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{u_c.id}",
                    target_node_type="UNIT",
                    target_node_key=f"unit:{u_b.id}",
                    edge_type="SUPERSEDES",
                    weight=1.0,
                ),
                SessionGraphEdge(
                    id=generate_session_graph_edge_id(
                        u_a.id, f"unit:{u_a.id}", f"unit:{u_c.id}", "ADJACENT_IN_EPISODE"
                    ),
                    tenant_id="org_01",
                    project_id="proj_01",
                    thread_id=thread.id,
                    source_unit_id=u_a.id,
                    target_unit_id=u_c.id,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{u_a.id}",
                    target_node_type="UNIT",
                    target_node_key=f"unit:{u_c.id}",
                    edge_type="ADJACENT_IN_EPISODE",
                    weight=0.85,
                ),
            ]
            session.add_all(edges)
            await session.commit()

            m_b_id, m_y_id = m_b.id, m_y.id
            u_a_id, u_c_id, d_c_id, thread_id = u_a.id, u_c.id, d_c.id, thread.id

            # Finding 1 regression check:
            # Delete ONLY middle messages `m_b` (middle of window A-B-C) and `m_y` (middle of window X-Y-Z)!
            await session.execute(
                text("DELETE FROM messages WHERE id IN (:mb, :my)"),
                {"mb": m_b_id, "my": m_y_id},
            )
            await session.commit()
            session.expire_all()

            # Both windows (`w_abc`, `w_xyz`), `u_b`, `d_b`, `d_win`, and derived edges from B/window
            # MUST be gone; only `u_a` and `u_c` survive!
            surviving_units = (await session.scalars(select(SessionUnit.id))).all()
            assert set(surviving_units) == {u_a_id, u_c_id}

            surviving_links = (await session.scalars(select(SessionUnitMessage.session_unit_id))).all()
            assert set(surviving_links) == {u_a_id, u_c_id}

            surviving_decisions = (await session.scalars(select(SessionDecisionLedger))).all()
            assert len(surviving_decisions) == 1
            assert surviving_decisions[0].id == d_c_id
            assert surviving_decisions[0].supersedes_id is None

            surviving_edges = (await session.scalars(select(SessionGraphEdge))).all()
            assert [e.edge_type for e in surviving_edges] == ["ADJACENT_IN_EPISODE"]

            # Delete thread -> 100% of remaining units, unit-message links, decisions, and edges cascade-delete!
            await session.execute(text("DELETE FROM threads WHERE id = :tid"), {"tid": thread_id})
            await session.commit()
            session.expire_all()

            assert (await session.scalars(select(SessionUnit.id))).all() == []
            assert (await session.scalars(select(SessionUnitMessage.session_unit_id))).all() == []
            assert (await session.scalars(select(SessionDecisionLedger.id))).all() == []
            assert (await session.scalars(select(SessionGraphEdge.id))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_tenant_project_thread_and_window_integrity_constraints() -> None:
    engine = _create_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            agent = Agent(owner_key="continuum:thang", name="Scope Guard Agent")
            session.add(agent)
            await session.flush()

            t1 = Thread(agent_id=agent.id, owner_key="continuum:u1", title="Tenant 1 Thread")
            t2 = Thread(agent_id=agent.id, owner_key="continuum:u2", title="Tenant 2 Thread")
            session.add_all([t1, t2])
            await session.flush()

            m1_a = Message(thread_id=t1.id, role=MessageRole.USER, content="T1 msg A")
            m1_b = Message(thread_id=t1.id, role=MessageRole.ASSISTANT, content="T1 msg B")
            m2_a = Message(thread_id=t2.id, role=MessageRole.USER, content="T2 msg A")
            session.add_all([m1_a, m1_b, m2_a])
            await session.flush()

            u1 = SessionUnit(
                id=generate_session_unit_id(t1.id, "TURN", 0, m1_a.id),
                tenant_id="tenant_1",
                project_id="proj_1",
                thread_id=t1.id,
                unit_kind="TURN",
                ordinal=0,
                episode_id="ep:t1:0",
                message_from_id=m1_a.id,
                message_to_id=m1_a.id,
                epistemic_status="CONFIRMED",
                content_hash=_sha256(m1_a.content),
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            u2 = SessionUnit(
                id=generate_session_unit_id(t2.id, "TURN", 0, m2_a.id),
                tenant_id="tenant_2",
                project_id="proj_2",
                thread_id=t2.id,
                unit_kind="TURN",
                ordinal=0,
                episode_id="ep:t2:0",
                message_from_id=m2_a.id,
                message_to_id=m2_a.id,
                epistemic_status="CONFIRMED",
                content_hash=_sha256(m2_a.content),
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add_all([u1, u2])
            await session.flush()

            d1 = SessionDecisionLedger(
                id=generate_session_decision_id(u1.id, "slot:arch"),
                tenant_id="tenant_1",
                project_id="proj_1",
                thread_id=t1.id,
                session_unit_id=u1.id,
                ledger_kind="DECISION",
                topic_slot_key="slot:arch",
                turn_ordinal=0,
                epistemic_status="CONFIRMED",
                summary_text="T1 decision",
                valid_from=now,
                valid_to=SESSION_OPEN_ENDED_VALID_TO,
            )
            session.add(d1)
            await session.commit()

            t1_id, t2_id = t1.id, t2.id
            m1_a_id, m1_b_id, m2_a_id = m1_a.id, m1_b.id, m2_a.id
            u1_id, u2_id, d1_id = u1.id, u2.id, d1.id

        # Finding 3 check 1: Ledger in tenant_2/proj_2/t2 referencing unit in tenant_1/proj_1/t1 -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionDecisionLedger(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_2",
                    thread_id=t2_id,
                    session_unit_id=u1_id,
                    ledger_kind="DECISION",
                    topic_slot_key="slot:leak",
                    turn_ordinal=0,
                    epistemic_status="CONFIRMED",
                    summary_text="Cross-tenant unit reference",
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Finding 3 check 2: Ledger in tenant_2/proj_1/t1 (mismatched tenant on same thread) -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionDecisionLedger(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_1",
                    thread_id=t1_id,
                    session_unit_id=u1_id,
                    ledger_kind="DECISION",
                    topic_slot_key="slot:tenant_mismatch",
                    turn_ordinal=0,
                    epistemic_status="CONFIRMED",
                    summary_text="Mismatched tenant",
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Finding 3 check 3: Graph edge in tenant_2/proj_2/t2 referencing unit in tenant_1 -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionGraphEdge(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_2",
                    thread_id=t2_id,
                    source_unit_id=u1_id,
                    target_unit_id=None,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{u1_id}",
                    target_node_type="ENTITY",
                    target_node_key="entity:leak",
                    edge_type="ENTITY_MENTION",
                    weight=1.0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                SessionGraphEdge(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_2",
                    thread_id=t2_id,
                    source_unit_id=u2_id,
                    target_unit_id=u1_id,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{u2_id}",
                    target_node_type="UNIT",
                    target_node_key=f"unit:{u1_id}",
                    edge_type="SUPERSEDES",
                    weight=1.0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Finding 3 check 4: SessionUnit in t2 referencing Message belonging to t1 -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_2",
                    thread_id=t2_id,
                    unit_kind="TURN",
                    ordinal=99,
                    episode_id="ep:t2:99",
                    message_from_id=m1_a_id,
                    message_to_id=m1_a_id,
                    epistemic_status="FACT",
                    content_hash=_sha256("cross_thread_msg"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Finding 3 check 5: Ledger in tenant_2 setting `supersedes_id` to tenant_1 decision -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionDecisionLedger(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_2",
                    project_id="proj_2",
                    thread_id=t2_id,
                    session_unit_id=u2_id,
                    ledger_kind="DECISION",
                    topic_slot_key="slot:arch",
                    turn_ordinal=0,
                    epistemic_status="CONFIRMED",
                    summary_text="Cross-tenant supersedes_id",
                    supersedes_id=d1_id,
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Finding 1 & 3 guard checks: Multi-message WINDOW missing source IDs, missing endpoint,
        # or smuggling a middle message from another thread -> REJECTED
        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="WINDOW",
                    ordinal=9,
                    episode_id="ep:t1:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_b_id,
                    source_message_ids_json=[m1_a_id, m2_a_id, m1_b_id],
                    epistemic_status="FACT",
                    content_hash=_sha256("cross_thread_middle_msg"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="WINDOW",
                    ordinal=10,
                    episode_id="ep:t1:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_b_id,
                    source_message_ids_json=[],
                    epistemic_status="FACT",
                    content_hash=_sha256("missing_source_ids"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="WINDOW",
                    ordinal=11,
                    episode_id="ep:t1:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_b_id,
                    source_message_ids_json=[m1_a_id, m1_a_id],
                    epistemic_status="FACT",
                    content_hash=_sha256("missing_to_endpoint"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        # Immutability guard: tampering with `session_unit_messages` or `session_units` provenance -> REJECTED
        async with session_factory() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    text("UPDATE session_unit_messages SET message_id = :m WHERE session_unit_id = :u"),
                    {"m": m1_b_id, "u": u1_id},
                )
                await session.commit()

        # Original CHECK / UNIQUE guards (`epistemic_status`, `valid_to < valid_from`,
        # duplicate ordinal, negative weight)
        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="TURN",
                    ordinal=50,
                    episode_id="ep:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_a_id,
                    epistemic_status="INVALID_STATE",
                    content_hash=_sha256("x"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="TURN",
                    ordinal=51,
                    episode_id="ep:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_a_id,
                    epistemic_status="FACT",
                    content_hash=_sha256("x"),
                    valid_from=now,
                    valid_to=now - timedelta(seconds=1),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                SessionUnit(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    unit_kind="TURN",
                    ordinal=0,
                    episode_id="ep:0",
                    message_from_id=m1_a_id,
                    message_to_id=m1_a_id,
                    epistemic_status="FACT",
                    content_hash=_sha256("dup"),
                    valid_from=now,
                    valid_to=SESSION_OPEN_ENDED_VALID_TO,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

        async with session_factory() as session:
            session.add(
                SessionGraphEdge(
                    id=str(uuid.uuid4()),
                    tenant_id="tenant_1",
                    project_id="proj_1",
                    thread_id=t1_id,
                    source_unit_id=u1_id,
                    source_node_type="UNIT",
                    source_node_key=f"unit:{u1_id}",
                    target_node_type="ENTITY",
                    target_node_key="entity:pg",
                    edge_type="ENTITY_MENTION",
                    weight=-0.1,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
    finally:
        await engine.dispose()
