"""Asynchronous database engine and session management."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sag_api.core.config import settings
from sag_api.db.base import Base


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory if it does not exist for SQLite."""
    marker = "sqlite+aiosqlite:///"
    if url.startswith(marker):
        path = url[len(marker) :]
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

_engine_url = make_url(settings.database_url)
_connect_args: dict[str, object] = {}
if _engine_url.drivername == "postgresql+asyncpg":
    _query = dict(_engine_url.query)
    _search_path = _query.pop("search_path", None)
    if _search_path is not None:
        if not isinstance(_search_path, str) or not _search_path.strip():
            raise ValueError("PostgreSQL search_path must be a non-empty string.")
        _engine_url = _engine_url.set(query=_query)
        _connect_args["server_settings"] = {"search_path": _search_path}

engine: AsyncEngine = create_async_engine(
    _engine_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

# Session-level PostgreSQL advisory locks span Qdrant network I/O. Keep these
# bounded lock connections out of the request/session pool so slow publishes
# cannot consume the connections reserved for normal API traffic.
if engine.dialect.name == "postgresql":
    publish_lock_engine: AsyncEngine = create_async_engine(
        _engine_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=0,
        pool_timeout=1.0,
        connect_args=_connect_args,
    )
else:
    publish_lock_engine = engine

# SQLite: foreign keys enabled + concurrency friendly (WAL parallel read/write, busy_timeout
# lets writes wait instead of locking immediately; 30s ceiling covers slow CI disk contention)
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# Incremental columns for existing tables (lightweight dev migrations; production uses Alembic).
# create_all only creates new tables and does not alter existing tables, so we idempotently
# ADD COLUMN for evolving fields.
_COLUMN_UPGRADES: dict[str, dict[str, str]] = {
    "agents": {"is_default": "BOOLEAN NOT NULL DEFAULT FALSE", "owner_key": "VARCHAR(64)"},
    "documents": {
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "token_usage": "BIGINT NOT NULL DEFAULT 0",
        "token_usage_json": "JSON",
        "error_layer": "VARCHAR(16)",
        "error_stage": "VARCHAR(16)",
        "octx_installation_id": "VARCHAR(32)",
        "octx_document_id": "VARCHAR(36)",
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "parser_provider": "VARCHAR(16)",
        "mineru_provider": "VARCHAR(16)",
        "mineru_model": "VARCHAR(16)",
        "parser_status": "VARCHAR(16)",
        "fallback_from": "VARCHAR(16)",
        "fallback_reason": "TEXT",
        "vector_identity_json": "JSON",
        "tenant_id": "VARCHAR(64) NOT NULL DEFAULT 'tenant_continuum_default'",
        "project_id": "VARCHAR(64)",
        "owner_id": "VARCHAR(256)",
        "logical_source_id": "VARCHAR(128)",
    },
    "threads": {
        "archived": "BOOLEAN NOT NULL DEFAULT FALSE",
        "owner_key": "VARCHAR(64)",
        "parent_thread_id": "VARCHAR(32) REFERENCES threads(id) ON DELETE RESTRICT",
        "branch_point_message_id": "VARCHAR(32) REFERENCES messages(id) ON DELETE RESTRICT",
        "deleted_by_owner_at": "TIMESTAMP WITH TIME ZONE",
    },
    "messages": {
        "reply_to_message_id": "VARCHAR(32) REFERENCES messages(id) ON DELETE SET NULL",
        "regenerated_from_message_id": "VARCHAR(32) REFERENCES messages(id) ON DELETE SET NULL",
        "attachments_json": "JSON",
        "steps_json": "JSON",
        "prompt_preview": "TEXT NOT NULL DEFAULT ''",
        "status": "VARCHAR(16) NOT NULL DEFAULT 'ok'",
        "error_json": "JSON",
    },
    "universe_dirty_sources": {"revision": "INTEGER NOT NULL DEFAULT 1"},
    "tree_manifests": {"manifest_json": "JSON NOT NULL DEFAULT '{}'"},
    "tree_routing_profiles": {"document_version_id": "VARCHAR(128) NOT NULL DEFAULT ''"},
    "knowledge_jobs": {"result_json": "JSON", "usage_json": "JSON", "extractor_version": "VARCHAR(128)", "tree_requested_at": "TIMESTAMP WITH TIME ZONE"},
    "knowledge_graph_builds": {"input_fingerprint": "VARCHAR(64)"},
    "canonical_blocks": {"dedup_signature_json": "JSON"},
    "research_tasks": {"execution_json": "JSON NOT NULL DEFAULT '{}'"},
    "ingestion_runs": {
        "content_hash": "VARCHAR(64)",
        "research_task_id": "VARCHAR(36) REFERENCES research_tasks(id) ON DELETE SET NULL",
    },
}

# Existing tables also need newly introduced hot-path indexes. Keep these
# idempotent for local/embedded upgrades; production deployments can express
# the same DDL in their migration runner.
_INDEX_UPGRADES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_graph_input ON knowledge_graph_builds (tenant_id, project_id, input_fingerprint)",
    "CREATE INDEX IF NOT EXISTS ix_messages_thread_created_id ON messages (thread_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_reply_to_message_id ON messages (reply_to_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_regenerated_from_message_id ON messages (regenerated_from_message_id)",
    "CREATE INDEX IF NOT EXISTS ix_agents_owner_key ON agents (owner_key)",
    "CREATE INDEX IF NOT EXISTS ix_threads_owner_key ON threads (owner_key)",
    "CREATE INDEX IF NOT EXISTS ix_threads_parent_thread_id ON threads (parent_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_threads_branch_point_message_id ON threads (branch_point_message_id)",
    (
        "CREATE INDEX IF NOT EXISTS ix_threads_owner_visible "
        "ON threads (agent_id, owner_key, archived, updated_at DESC) "
        "WHERE deleted_by_owner_at IS NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_content_hash "
        "ON ingestion_runs (tenant_id, project_id, content_hash)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_research_task ON ingestion_runs (research_task_id)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_sag_source ON documents (source_id, sag_source_id)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_active_created ON documents (source_id, is_active, created_at)",
    (
        "CREATE INDEX IF NOT EXISTS ix_documents_tenant_project_logical "
        "ON documents (tenant_id, project_id, logical_source_id)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_tree_routing_profiles_version_scope "
        "ON tree_routing_profiles (project_id, tree_version, source_id, document_version_id, partition_id)"
    ),
)


async def _ensure_columns() -> None:
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                        """
                    )
                )
            ).all()
            cols_by_table: dict[str, set[str]] = {}
            for tname, cname in rows:
                cols_by_table.setdefault(tname, set()).add(cname)
            for table, cols in _COLUMN_UPGRADES.items():
                existing = cols_by_table.get(table)
                if existing is None:
                    continue
                for col, ddl in cols.items():
                    if col not in existing:
                        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        return

    from sqlalchemy import inspect as sa_inspect

    def _existing(sync_conn, table: str) -> set[str] | None:
        insp = sa_inspect(sync_conn)
        if not insp.has_table(table):
            return None
        return {c["name"] for c in insp.get_columns(table)}

    async with engine.begin() as conn:
        for table, cols in _COLUMN_UPGRADES.items():
            existing = await conn.run_sync(_existing, table)
            if existing is None:
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


async def _ensure_indexes() -> None:
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            existing_tables = set(
                (
                    await conn.scalars(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                    )
                ).all()
            )
            existing_indexes = set(
                (
                    await conn.scalars(
                        text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
                    )
                ).all()
            )
            for ddl in _INDEX_UPGRADES:
                table = ddl.split(" ON ", 1)[1].split(" ", 1)[0]
                if table not in existing_tables:
                    continue
                idx_name = ddl.split("IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0].strip()
                if idx_name in existing_indexes:
                    continue
                await conn.exec_driver_sql(ddl)
        return

    from sqlalchemy import inspect as sa_inspect

    async with engine.begin() as conn:
        for ddl in _INDEX_UPGRADES:
            table = ddl.split(" ON ", 1)[1].split(" ", 1)[0]
            table_exists = await conn.run_sync(lambda sync_conn, table=table: sa_inspect(sync_conn).has_table(table))
            if not table_exists:
                continue
            await conn.exec_driver_sql(ddl)


async def _ensure_document_schema_widths() -> None:
    """Widen legacy document columns for canonical IDs and signed principal subjects."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        # Avoid reacquiring table locks during startup after the one-time widening is complete.
        needs_upgrade = await conn.scalar(
            text(
                """
                SELECT
                  NOT EXISTS (
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'documents'::regclass AND attname = 'id'
                      AND attnum > 0 AND NOT attisdropped
                      AND atttypid = 'character varying'::regtype AND atttypmod = 40
                  )
                  OR NOT EXISTS (
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'documents'::regclass AND attname = 'owner_id'
                      AND attnum > 0 AND NOT attisdropped
                      AND atttypid = 'character varying'::regtype AND atttypmod = 260
                  )
                  OR EXISTS (
                    SELECT 1
                    FROM pg_constraint fk
                    CROSS JOIN LATERAL unnest(fk.conkey, fk.confkey) AS keys(local_attnum, target_attnum)
                    JOIN pg_attribute local_col
                      ON local_col.attrelid = fk.conrelid AND local_col.attnum = keys.local_attnum
                    JOIN pg_attribute target_col
                      ON target_col.attrelid = fk.confrelid AND target_col.attnum = keys.target_attnum
                    WHERE fk.contype = 'f' AND fk.confrelid = 'documents'::regclass
                      AND target_col.attname = 'id'
                      AND (
                        local_col.atttypid <> 'character varying'::regtype
                        OR local_col.atttypmod <> 40
                      )
                  )
                """
            )
        )
        if not needs_upgrade:
            return
        await conn.exec_driver_sql(
            """
            DO $$
            DECLARE fk RECORD;
            BEGIN
              CREATE TEMP TABLE sag_document_fks ON COMMIT DROP AS
                SELECT conrelid::regclass AS rel, conname, pg_get_constraintdef(oid) AS definition
                FROM pg_constraint
                WHERE contype = 'f' AND confrelid = 'documents'::regclass;
              CREATE TEMP TABLE sag_document_fk_columns ON COMMIT DROP AS
                SELECT DISTINCT c.conrelid::regclass AS rel, local_col.attname AS column_name
                FROM pg_constraint c
                CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS keys(local_attnum, target_attnum)
                JOIN pg_attribute local_col ON local_col.attrelid = c.conrelid AND local_col.attnum = keys.local_attnum
                JOIN pg_attribute target_col ON target_col.attrelid = c.confrelid
                  AND target_col.attnum = keys.target_attnum
                WHERE c.contype = 'f' AND c.confrelid = 'documents'::regclass AND target_col.attname = 'id';
              FOR fk IN SELECT * FROM sag_document_fks LOOP
                EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', fk.rel, fk.conname);
              END LOOP;
              ALTER TABLE documents ALTER COLUMN id TYPE VARCHAR(36);
              ALTER TABLE documents ALTER COLUMN owner_id TYPE VARCHAR(256);
              FOR fk IN SELECT * FROM sag_document_fk_columns LOOP
                EXECUTE format('ALTER TABLE %s ALTER COLUMN %I TYPE VARCHAR(36)', fk.rel, fk.column_name);
              END LOOP;
              FOR fk IN SELECT * FROM sag_document_fks LOOP
                EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', fk.rel, fk.conname, fk.definition);
              END LOOP;
            END $$;
            """
        )


async def init_db() -> None:
    """Initialize tables in development (production uses Alembic). Import models to register into metadata."""
    from sag_api.db import models  # noqa: F401

    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            existing_tables = set(
                (
                    await conn.scalars(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                    )
                ).all()
            )
            required_tables = {t.name for t in Base.metadata.tables.values()}
            if not required_tables.issubset(existing_tables):
                await conn.run_sync(Base.metadata.create_all)
    else:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await _ensure_columns()
    await _ensure_document_schema_widths()
    await _ensure_indexes()


async def dispose_db() -> None:
    await engine.dispose()
    if publish_lock_engine is not engine:
        await publish_lock_engine.dispose()
