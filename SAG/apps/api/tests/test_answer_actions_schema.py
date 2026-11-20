"""Real persistence constraints and SQL migration parity for AI answer actions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, event, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from sag_api.db.base import Base
from sag_api.db.models import Agent, AnswerFeedback, AnswerShare, Message, Thread
from sag_api.enums import MessageRole

API_ROOT = Path(__file__).resolve().parents[1]
UP_SQL_PATH = API_ROOT / "migrations" / "0010_ai_answer_actions.sql"
DOWN_SQL_PATH = API_ROOT / "scripts" / "0010_ai_answer_actions_down.sql"


def _enable_sqlite_fk(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")


def _split_sql_statements(raw_sql: str, *, sqlite_compatible: bool = False) -> list[str]:
    cleaned = "\n".join(
        line for line in raw_sql.splitlines() if not line.strip().startswith("--")
    )
    stmts = [
        stmt.strip()
        for stmt in cleaned.split(";")
        if stmt.strip() and stmt.strip().upper() not in {"BEGIN", "COMMIT"}
    ]
    if sqlite_compatible:
        normalized: list[str] = []
        for stmt in stmts:
            # SQLite ALTER TABLE ADD COLUMN / DROP COLUMN does not accept IF [NOT] EXISTS
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
        session.add(Agent(id="agent", name="AI"))
        await session.flush()
        session.add(Thread(id="thread", agent_id="agent"))
        await session.flush()
        session.add_all([
            Message(id="question", thread_id="thread", role=MessageRole.USER, content="Question"),
            Message(id="answer", thread_id="thread", role=MessageRole.ASSISTANT, content="Answer"),
        ])
        await session.commit()
    yield sessions
    await engine.dispose()


def feedback(**overrides):
    fields = dict(
        message_id="answer",
        voter_subject="actor",
        tenant_id="org",
        project_id="project",
        rating="LIKE",
    )
    return AnswerFeedback(**(fields | overrides))


def share(**overrides):
    fields = dict(
        creator_subject="actor",
        message_id="answer",
        question_id="question",
        tenant_id="org",
        project_id="project",
        question_content="Question",
        answer_content="Answer",
        citations=[],
        source_ids=[],
    )
    return AnswerShare(**(fields | overrides))


async def test_feedback_is_unique_per_voter_and_answer_and_can_switch(database):
    async with database() as session:
        first = feedback()
        session.add(first)
        await session.commit()
        first.rating = "DISLIKE"
        first.reason_category = "INCOMPLETE"
        await session.commit()
        assert (await session.scalar(select(AnswerFeedback))).rating == "DISLIKE"
        session.add(feedback())
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.parametrize(
    "fields",
    [
        {"rating": "INVALID"},
        {"rating": "DISLIKE"},
        {"rating": "DISLIKE", "reason_category": "INVALID"},
        {"rating": "DISLIKE", "reason_category": "OTHER"},
        {"rating": "DISLIKE", "reason_category": "OTHER", "reason_text": "   "},
        {"rating": "DISLIKE", "reason_category": "INCOMPLETE", "reason_text": "x" * 1001},
        {"reason_category": "INCOMPLETE"},
        {"reason_text": "Unexpected text on LIKE"},
        {"tenant_id": " "},
        {"project_id": " "},
        {"voter_subject": " "},
    ],
)
async def test_invalid_feedback_cannot_bypass_database_checks(database, fields):
    async with database() as session:
        session.add(feedback(**fields))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_only_one_live_share_exists_and_revocation_allows_a_new_link(database):
    async with database() as session:
        original = share()
        session.add(original)
        await session.commit()
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", original.id)
        original_id = original.id
        session.add(share())
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        original = await session.get(AnswerShare, original_id)
        original.revoked_at = datetime.now(UTC)
        await session.commit()
        session.add(share())
        await session.commit()
        rows = list((await session.scalars(select(AnswerShare))).all())
        assert len(rows) == 2
        assert sum(row.revoked_at is None for row in rows) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"tenant_id": " "},
        {"project_id": " "},
        {"creator_subject": " "},
        {"question_id": "answer"},
    ],
)
async def test_invalid_share_cannot_bypass_database_checks(database, fields):
    async with database() as session:
        session.add(share(**fields))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_deleting_answer_cascades_feedback_and_shares_but_retains_new_version(database):
    async with database() as session:
        session.add_all([feedback(), share()])
        session.add(
            Message(
                id="new-answer",
                thread_id="thread",
                role=MessageRole.ASSISTANT,
                reply_to_message_id="question",
                regenerated_from_message_id="answer",
                content="New answer",
            )
        )
        await session.commit()
        await session.execute(delete(Message).where(Message.id == "answer"))
        await session.commit()
        assert await session.scalar(select(AnswerFeedback)) is None
        assert await session.scalar(select(AnswerShare)) is None
        version = await session.get(Message, "new-answer")
        await session.refresh(version)
        assert version.regenerated_from_message_id is None
        assert version.reply_to_message_id == "question"
        await session.execute(delete(Message).where(Message.id == "question"))
        await session.commit()
        await session.refresh(version)
        assert version.reply_to_message_id is None


async def test_unknown_message_foreign_keys_fail(database):
    async with database() as session:
        session.add(feedback(message_id="missing"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        session.add(
            Message(
                id="orphan-regen",
                thread_id="thread",
                role=MessageRole.ASSISTANT,
                reply_to_message_id="missing-question",
                content="Orphan",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


def test_postgresql_ddl_compiles_cleanly():
    pg_dialect = postgresql.dialect()
    for table in (AnswerFeedback.__table__, AnswerShare.__table__, Message.__table__):
        compiled_table = str(CreateTable(table).compile(dialect=pg_dialect))
        assert table.name in compiled_table
        for index in table.indexes:
            compiled_idx = str(CreateIndex(index).compile(dialect=pg_dialect))
            assert index.name in compiled_idx


async def test_sql_migration_parity_defaults_and_rollback():
    assert UP_SQL_PATH.is_file()
    assert DOWN_SQL_PATH.is_file()

    expected_server_defaults = {
        AnswerFeedback: {"created_at", "updated_at"},
        AnswerShare: {"citations", "source_ids", "created_at", "updated_at"},
    }
    for model_cls, expected in expected_server_defaults.items():
        actual = {
            col.name for col in model_cls.__table__.columns if col.server_default is not None
        }
        assert actual == expected

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
            await conn.exec_driver_sql(
                """
                CREATE TABLE messages (
                    id VARCHAR(32) PRIMARY KEY,
                    thread_id VARCHAR(32) NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
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
            await conn.exec_driver_sql(
                "CREATE INDEX ix_messages_thread_created_id ON messages (thread_id, created_at, id)"
            )
            for stmt in _split_sql_statements(
                UP_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True
            ):
                await conn.exec_driver_sql(stmt)

        async with orm_engine.begin() as orm_conn:
            await orm_conn.run_sync(Base.metadata.create_all)

        async with sql_engine.connect() as conn, orm_engine.connect() as orm_conn:
            for model_cls in (Message, AnswerFeedback, AnswerShare):
                tname = model_cls.__tablename__
                sql_cols = await conn.run_sync(
                    lambda sync_conn, t=tname: {c["name"]: c for c in inspect(sync_conn).get_columns(t)}
                )
                orm_cols = await orm_conn.run_sync(
                    lambda sync_conn, t=tname: {c["name"]: c for c in inspect(sync_conn).get_columns(t)}
                )
                assert set(sql_cols) == set(orm_cols)
                sql_indexes = await conn.run_sync(
                    lambda sync_conn, t=tname: {
                        idx["name"] for idx in inspect(sync_conn).get_indexes(t) if idx.get("name")
                    }
                )
                model_indexes = {idx.name for idx in model_cls.__table__.indexes if idx.name}
                assert model_indexes.issubset(sql_indexes)

        # Verify raw SQL server defaults on ORM-created tables
        async with orm_engine.begin() as orm_conn:
            await orm_conn.execute(
                text(
                    "INSERT INTO agents (id, name, avatar, is_default, persona_json) "
                    "VALUES ('a1', 'AI', '', 0, '{}')"
                )
            )
            await orm_conn.execute(
                text("INSERT INTO threads (id, agent_id, title, archived) VALUES ('t1', 'a1', 'Chat', 0)")
            )
            await orm_conn.execute(
                text(
                    "INSERT INTO messages (id, thread_id, attachments_json, steps_json, role, "
                    "content, citations_json, prompt_preview) "
                    "VALUES ('q1', 't1', '[]', '[]', 'user', 'Q', '[]', '')"
                )
            )
            await orm_conn.execute(
                text(
                    "INSERT INTO messages (id, thread_id, attachments_json, steps_json, role, "
                    "content, citations_json, prompt_preview) "
                    "VALUES ('m1', 't1', '[]', '[]', 'assistant', 'A', '[]', '')"
                )
            )
            await orm_conn.execute(
                text(
                    "INSERT INTO answer_shares (id, creator_subject, message_id, question_id, "
                    "tenant_id, project_id, question_content, answer_content) "
                    "VALUES ('sh_1', 'u1', 'm1', 'q1', 'org_1', 'proj_1', 'Q', 'A')"
                )
            )
            row = (
                await orm_conn.execute(
                    text("SELECT citations, source_ids, created_at FROM answer_shares WHERE id = 'sh_1'")
                )
            ).one()
            assert row[0] == "[]"
            assert row[1] == "[]"
            assert row[2] is not None

        # Verify rollback drops answer-actions tables and restores messages columns cleanly
        async with sql_engine.begin() as conn:
            for stmt in _split_sql_statements(
                DOWN_SQL_PATH.read_text(encoding="utf-8"), sqlite_compatible=True
            ):
                await conn.exec_driver_sql(stmt)
            tables_after = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
            assert "answer_feedback" not in tables_after
            assert "answer_shares" not in tables_after
            msg_cols_after = await conn.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("messages")}
            )
            assert "reply_to_message_id" not in msg_cols_after
            assert "regenerated_from_message_id" not in msg_cols_after
    finally:
        await sql_engine.dispose()
        await orm_engine.dispose()
