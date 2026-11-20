"""Database constraints, scope/immutability triggers, and SQL migration parity for AI chat branch lineage."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, event, inspect, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from sag_api.core.db import _COLUMN_UPGRADES, _INDEX_UPGRADES
from sag_api.db.base import Base
from sag_api.db.models import Agent, Message, Thread
from sag_api.enums import MessageRole

API_ROOT = Path(__file__).resolve().parents[1]
UP_SQL_PATH = API_ROOT / "migrations" / "0012_ai_chat_branch_lineage.sql"
DOWN_SQL_PATH = API_ROOT / "scripts" / "0012_ai_chat_branch_lineage_down.sql"


def _enable_sqlite_fk(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")


def _split_sql_statements(raw_sql: str, *, sqlite_compatible: bool = False) -> list[str]:
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
        normalized: list[str] = []
        pg_only_prefixes = (
            "CREATE OR REPLACE FUNCTION ",
            "DROP FUNCTION ",
            "DROP TRIGGER ",
            "CREATE TRIGGER ",
        )
        for stmt in stmts:
            upper = stmt.upper()
            if upper.startswith(pg_only_prefixes):
                continue
            if re.match(r"(?is)^ALTER\s+TABLE\s+\w+\s+(ADD|DROP)\s+CONSTRAINT\b", stmt):
                continue
            s = re.sub(
                r"(?i)^ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+",
                r"ALTER TABLE \1 ADD COLUMN ",
                stmt,
            )
            s = re.sub(
                r"(?i)^ALTER\s+TABLE\s+(\w+)\s+DROP\s+COLUMN\s+IF\s+EXISTS\s+",
                r"ALTER TABLE \1 DROP COLUMN ",
                s,
            )
            normalized.append(s)
        return normalized
    return stmts


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    _enable_sqlite_fk(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all(
            [
                Agent(id="agent_a", name="Continuum AI", owner_key="owner_1"),
                Agent(id="agent_b", name="Other Agent", owner_key="owner_1"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Thread(id="thread_root", agent_id="agent_a", owner_key="owner_1", title="Root Chat"),
                Thread(id="thread_other_owner", agent_id="agent_a", owner_key="owner_2", title="Other Owner"),
                Thread(id="thread_other_agent", agent_id="agent_b", owner_key="owner_1", title="Other Agent"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Message(id="msg_q1", thread_id="thread_root", role=MessageRole.USER, content="Q1"),
                Message(id="msg_a1", thread_id="thread_root", role=MessageRole.ASSISTANT, content="A1"),
                Message(id="msg_other", thread_id="thread_other_owner", role=MessageRole.ASSISTANT, content="Other"),
            ]
        )
        await session.commit()
    yield sessions
    await engine.dispose()


async def test_valid_branch_lineage_and_tombstone_timestamp_roundtrip(database):
    now = datetime(2026, 10, 31, 14, 0, 0, tzinfo=UTC)
    async with database() as session:
        child = Thread(
            id="thread_branch_1",
            agent_id="agent_a",
            owner_key="owner_1",
            title="Root Chat",
            parent_thread_id="thread_root",
            branch_point_message_id="msg_a1",
        )
        session.add(child)
        await session.commit()

        root = await session.get(Thread, "thread_root")
        assert root is not None
        root.deleted_by_owner_at = now
        await session.commit()

    async with database() as session:
        reloaded_root = await session.get(Thread, "thread_root")
        reloaded_child = await session.get(Thread, "thread_branch_1")
        assert reloaded_root is not None
        assert reloaded_root.deleted_by_owner_at == now
        assert reloaded_root.deleted_by_owner_at.tzinfo is not None
        assert reloaded_child is not None
        assert reloaded_child.parent_thread_id == "thread_root"
        assert reloaded_child.branch_point_message_id == "msg_a1"
        assert reloaded_child.deleted_by_owner_at is None


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        # Half-null branch pair checks
        {"id": "bad_only_parent", "parent_thread_id": "thread_root", "branch_point_message_id": None},
        {"id": "bad_only_msg", "parent_thread_id": None, "branch_point_message_id": "msg_a1"},
        # Self-parent check
        {"id": "bad_self", "parent_thread_id": "bad_self", "branch_point_message_id": "msg_a1"},
        # Missing FK targets
        {"id": "bad_missing_parent", "parent_thread_id": "missing_t", "branch_point_message_id": "msg_a1"},
        {"id": "bad_missing_msg", "parent_thread_id": "thread_root", "branch_point_message_id": "missing_m"},
        # Cross-thread message (message belongs to thread_other_owner, not thread_root)
        {"id": "bad_cross_thread_msg", "parent_thread_id": "thread_root", "branch_point_message_id": "msg_other"},
        # Cross-owner parent (child owner_1 branching from thread_other_owner owner_2)
        {"id": "bad_cross_owner", "parent_thread_id": "thread_other_owner", "branch_point_message_id": "msg_other"},
        # Cross-agent parent
        {
            "id": "bad_cross_agent",
            "agent_id": "agent_b",
            "parent_thread_id": "thread_root",
            "branch_point_message_id": "msg_a1",
        },
    ],
)
async def test_invalid_branch_lineage_rejected_by_database(database, bad_kwargs):
    defaults = {
        "agent_id": "agent_a",
        "owner_key": "owner_1",
        "title": "Invalid Branch",
    }
    async with database() as session:
        session.add(Thread(**(defaults | bad_kwargs)))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_branch_lineage_pointers_are_immutable_after_insert(database):
    async with database() as session:
        session.add(
            Thread(
                id="thread_child",
                agent_id="agent_a",
                owner_key="owner_1",
                title="Child",
                parent_thread_id="thread_root",
                branch_point_message_id="msg_a1",
            )
        )
        await session.commit()

    # Attempt to form a cycle by mutating thread_root.parent_thread_id -> thread_child
    async with database() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(Thread)
                .where(Thread.id == "thread_root")
                .values(parent_thread_id="thread_child", branch_point_message_id="msg_a1")
            )
            await session.commit()

    # Attempt to clear or re-point an existing child's lineage
    async with database() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(Thread)
                .where(Thread.id == "thread_child")
                .values(branch_point_message_id="msg_q1")
            )
            await session.commit()

    # Attempt to mutate owner_key or agent_id on child or parent participating in a lineage
    for target_id, bad_values in [
        ("thread_child", {"owner_key": "owner_2"}),
        ("thread_child", {"agent_id": "agent_b"}),
        ("thread_root", {"owner_key": "owner_2"}),
        ("thread_root", {"agent_id": "agent_b"}),
    ]:
        async with database() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    update(Thread).where(Thread.id == target_id).values(**bad_values)
                )
                await session.commit()

    # Safe updates (title, archived, deleted_by_owner_at) remain allowed on lineage threads
    async with database() as session:
        await session.execute(
            update(Thread).where(Thread.id == "thread_child").values(title="Renamed Child")
        )
        await session.commit()


async def test_restrict_prevents_deleting_parent_thread_or_branch_message_until_child_removed(database):
    async with database() as session:
        session.add(
            Thread(
                id="thread_child",
                agent_id="agent_a",
                owner_key="owner_1",
                title="Child",
                parent_thread_id="thread_root",
                branch_point_message_id="msg_a1",
            )
        )
        await session.commit()

    # Physical delete of parent thread fails while child exists
    async with database() as session:
        with pytest.raises(IntegrityError):
            await session.execute(delete(Thread).where(Thread.id == "thread_root"))
            await session.commit()

    # Physical delete of branch_point_message fails while child exists
    async with database() as session:
        with pytest.raises(IntegrityError):
            await session.execute(delete(Message).where(Message.id == "msg_a1"))
            await session.commit()

    # Non-branch-point message on parent CAN still be deleted cleanly at DB level
    async with database() as session:
        await session.execute(delete(Message).where(Message.id == "msg_q1"))
        await session.commit()
        assert await session.get(Message, "msg_q1") is None

    # Once child thread is deleted, physical GC of parent thread succeeds and cascades msg_a1
    async with database() as session:
        await session.execute(delete(Thread).where(Thread.id == "thread_child"))
        await session.flush()
        await session.execute(delete(Thread).where(Thread.id == "thread_root"))
        await session.commit()
        assert await session.scalar(select(Thread).where(Thread.id == "thread_root")) is None
        assert await session.scalar(select(Message).where(Message.id == "msg_a1")) is None


def test_postgresql_ddl_and_core_db_upgrades_compile_cleanly():
    pg_dialect = postgresql.dialect()
    compiled_threads = str(CreateTable(Thread.__table__).compile(dialect=pg_dialect))
    assert "parent_thread_id" in compiled_threads
    assert "branch_point_message_id" in compiled_threads
    assert "deleted_by_owner_at" in compiled_threads
    assert "ck_threads_branch_pair" in compiled_threads
    assert "ck_threads_no_self_parent" in compiled_threads

    fk_names = {fk.name for fk in Thread.__table__.foreign_key_constraints if fk.name}
    assert "fk_threads_branch_point_message_id" in fk_names
    for fk in Thread.__table__.foreign_key_constraints:
        if fk.use_alter:
            compiled_fk = str(AddConstraint(fk).compile(dialect=pg_dialect))
            assert "REFERENCES messages (id) ON DELETE RESTRICT" in compiled_fk

    for idx in Thread.__table__.indexes:
        compiled_idx = str(CreateIndex(idx).compile(dialect=pg_dialect))
        assert idx.name in compiled_idx

    # Verify core/db.py incremental upgrade registry matches parser contract (` ON <table> (`)
    thread_cols = _COLUMN_UPGRADES["threads"]
    assert {"parent_thread_id", "branch_point_message_id", "deleted_by_owner_at"}.issubset(thread_cols)
    for ddl in _INDEX_UPGRADES:
        table = ddl.split(" ON ", 1)[1].split(" ", 1)[0]
        idx_name = ddl.split("IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0].strip()
        assert table and "(" not in table
        assert idx_name


async def test_sql_migration_parity_and_rollback():
    assert UP_SQL_PATH.is_file()
    assert DOWN_SQL_PATH.is_file()

    sql_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    orm_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    _enable_sqlite_fk(sql_engine)
    _enable_sqlite_fk(orm_engine)

    try:
        async with sql_engine.begin() as conn:
            await conn.exec_driver_sql(
                """
                CREATE TABLE agents (
                    id VARCHAR(32) PRIMARY KEY,
                    owner_key VARCHAR(64),
                    name VARCHAR(120) NOT NULL,
                    avatar VARCHAR(64) NOT NULL DEFAULT '',
                    is_default BOOLEAN NOT NULL DEFAULT 0,
                    persona_json JSON NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.exec_driver_sql(
                """
                CREATE TABLE threads (
                    id VARCHAR(32) PRIMARY KEY,
                    owner_key VARCHAR(64),
                    agent_id VARCHAR(32) NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                    title VARCHAR(300) NOT NULL DEFAULT 'New chat',
                    archived BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.exec_driver_sql("CREATE INDEX ix_threads_owner_key ON threads (owner_key)")
            await conn.exec_driver_sql("CREATE INDEX ix_threads_agent_id ON threads (agent_id)")
            await conn.exec_driver_sql("CREATE INDEX ix_threads_archived ON threads (archived)")
            await conn.exec_driver_sql(
                """
                CREATE TABLE messages (
                    id VARCHAR(32) PRIMARY KEY,
                    thread_id VARCHAR(32) NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    reply_to_message_id VARCHAR(32) REFERENCES messages(id) ON DELETE SET NULL,
                    regenerated_from_message_id VARCHAR(32) REFERENCES messages(id) ON DELETE SET NULL,
                    attachments_json JSON NOT NULL DEFAULT '[]',
                    steps_json JSON NOT NULL DEFAULT '[]',
                    role VARCHAR(16) NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    citations_json JSON NOT NULL DEFAULT '[]',
                    prompt_preview TEXT NOT NULL DEFAULT '',
                    status VARCHAR(16) NOT NULL DEFAULT 'ok',
                    error_json JSON,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for stmt in _split_sql_statements(
                UP_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True
            ):
                await conn.exec_driver_sql(stmt)

        async with orm_engine.begin() as orm_conn:
            await orm_conn.run_sync(Base.metadata.create_all)

        async with sql_engine.connect() as conn, orm_engine.connect() as orm_conn:
            sql_cols = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("threads")}
            )
            orm_cols = await orm_conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("threads")}
            )
            assert sql_cols == orm_cols
            sql_indexes = await conn.run_sync(
                lambda sync_conn: {
                    idx["name"] for idx in inspect(sync_conn).get_indexes("threads") if idx.get("name")
                }
            )
            model_indexes = {idx.name for idx in Thread.__table__.indexes if idx.name}
            assert model_indexes.issubset(sql_indexes)

        # Rollback drops the 3 columns and new indexes cleanly
        async with sql_engine.begin() as conn:
            for stmt in _split_sql_statements(
                DOWN_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True
            ):
                await conn.exec_driver_sql(stmt)
            cols_after = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("threads")}
            )
            assert "parent_thread_id" not in cols_after
            assert "branch_point_message_id" not in cols_after
            assert "deleted_by_owner_at" not in cols_after

            # Re-applying UP succeeds cleanly
            for stmt in _split_sql_statements(
                UP_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True
            ):
                await conn.exec_driver_sql(stmt)
            cols_reapplied = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("threads")}
            )
            assert {"parent_thread_id", "branch_point_message_id", "deleted_by_owner_at"}.issubset(
                cols_reapplied
            )
    finally:
        await sql_engine.dispose()
        await orm_engine.dispose()
