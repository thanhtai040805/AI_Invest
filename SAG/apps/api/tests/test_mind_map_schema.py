"""Persistence constraints and SQL migration parity for Project Mind Map snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from sag_api.db.base import Base
from sag_api.db.models import ProjectMindMapSnapshot

API_ROOT = Path(__file__).resolve().parents[1]
UP_SQL_PATH = API_ROOT / "migrations" / "0011_project_mind_map_snapshots.sql"
DOWN_SQL_PATH = API_ROOT / "scripts" / "0011_project_mind_map_snapshots_down.sql"


def _split_sql_statements(raw_sql: str) -> list[str]:
    cleaned = "\n".join(
        line for line in raw_sql.splitlines() if not line.strip().startswith("--")
    )
    return [
        stmt.strip()
        for stmt in cleaned.split(";")
        if stmt.strip() and stmt.strip().upper() not in {"BEGIN", "COMMIT"}
    ]


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


def _snapshot(**overrides) -> ProjectMindMapSnapshot:
    defaults = dict(
        tenant_id="org_alpha",
        project_id="651a2b3c4d5e6f7a8b9c0d99",
        partition_scope_hash="a" * 64,
        topic_key="",
        topic_display=None,
        scope_fingerprint="b" * 64,
        generation_mode="llm",
        fallback_reason=None,
        coverage_json={
            "accessibleReadyDocumentCount": 2,
            "usedDocumentCount": 2,
            "omittedDocumentCount": 0,
            "pendingDocumentCount": 0,
            "truncatedDocumentCount": 0,
            "evidenceUnitCount": 8,
        },
        tree_json={
            "id": "root",
            "label": "Project Overview",
            "summary": "Overview",
            "citationNumbers": [],
            "children": [],
        },
        citations_json=[],
        used_document_version_ids=["ver-1", "ver-2"],
        generated_by_subject="user_1",
        generated_at=datetime(2026, 10, 31, 5, 0, tzinfo=UTC),
    )
    return ProjectMindMapSnapshot(**(defaults | overrides))


@pytest.mark.asyncio
async def test_persists_unique_snapshot_per_project_partition_and_topic(database) -> None:
    async with database() as session:
        overview = _snapshot()
        focused = _snapshot(topic_key="api contract", topic_display="API Contract")
        other_project = _snapshot(project_id="651a2b3c4d5e6f7a8b9c0daa")
        session.add_all([overview, focused, other_project])
        await session.commit()

        rows = (await session.scalars(select(ProjectMindMapSnapshot))).all()
        assert len(rows) == 3

    async with database() as session:
        session.add(_snapshot())
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"tenant_id": "   "},
        {"project_id": ""},
        {"partition_scope_hash": ""},
        {"scope_fingerprint": "   "},
        {"generated_by_subject": ""},
        {"generation_mode": "none"},
        {"generation_mode": "llm", "fallback_reason": "LLM_UNAVAILABLE"},
        {"generation_mode": "outline_fallback", "fallback_reason": None},
        {"generation_mode": "outline_fallback", "fallback_reason": "UNKNOWN"},
        {"topic_key": "", "topic_display": "Should be null"},
        {"topic_key": "architecture", "topic_display": None},
    ],
)
async def test_rejects_invalid_snapshot_invariants(database, invalid_fields: dict) -> None:
    async with database() as session:
        session.add(_snapshot(**invalid_fields))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_postgresql_ddl_and_sql_migration_round_trip() -> None:
    table = Base.metadata.tables["project_mind_maps"]
    pg_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE project_mind_maps" in pg_ddl
    assert "uq_project_mind_maps_scope_topic" in pg_ddl

    idx_ddls = [
        str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
        for idx in table.indexes
    ]
    assert any("ix_project_mind_maps_project_updated" in ddl for ddl in idx_ddls)

    assert UP_SQL_PATH.is_file()
    assert DOWN_SQL_PATH.is_file()

    up_sql = UP_SQL_PATH.read_text(encoding="utf-8")
    down_sql = DOWN_SQL_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS project_mind_maps" in up_sql
    assert "DROP TABLE IF EXISTS project_mind_maps" in down_sql

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            for stmt in _split_sql_statements(up_sql):
                await conn.exec_driver_sql(stmt)
            has_table = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).has_table("project_mind_maps")
            )
            assert has_table is True

        async with engine.begin() as conn:
            for stmt in _split_sql_statements(down_sql):
                await conn.exec_driver_sql(stmt)
            has_table_after = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).has_table("project_mind_maps")
            )
            assert has_table_after is False
    finally:
        await engine.dispose()
