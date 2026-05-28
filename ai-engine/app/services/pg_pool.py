"""PostgreSQL connection helper — simple pool pattern for ai-engine."""

import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_lock = threading.Lock()


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=5,
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


def migrate():
    """Create job_states table if not exists."""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_states (
                job_name    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                started_at  TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                metadata    JSONB DEFAULT '{}',
                error       TEXT,
                PRIMARY KEY (job_name)
            )
        """)
