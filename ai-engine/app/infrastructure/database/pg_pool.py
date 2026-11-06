"""PostgreSQL connection helper — simple pool pattern for ai-engine."""

import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
DB_POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN_CONN", "2"))
DB_POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", "20"))

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_lock = threading.Lock()


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=DB_POOL_MIN_CONN,
                    maxconn=DB_POOL_MAX_CONN,
                    dsn=DB_URL,
                )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor():
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def check_connection() -> bool:
    """Verify PostgreSQL connectivity by executing a lightweight query."""
    with get_cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        return bool(row and row[0] == 1)


def migrate() -> None:
    """Validate DB connectivity.

    NOTE: All table schemas, DDL, and migrations are strictly managed by Prisma
    as the single source of truth (back-end/prisma/schema.prisma).
    ai-engine does not perform runtime DDL migrations.
    """
    check_connection()


def run_agent_migrations() -> None:
    """Deprecated: All agent tables are migrated and tracked in back-end/prisma/schema.prisma."""
    pass
