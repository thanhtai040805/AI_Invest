"""Database connection utilities and raw connection helpers."""

import os
import psycopg2
from app.infrastructure.database.pg_pool import DB_URL, get_conn, get_cursor, get_pool

def get_db_url() -> str:
    """Get the active database connection URL."""
    return os.getenv("DATABASE_URL", DB_URL)

def get_raw_connection():
    """Get a raw psycopg2 connection for migrations or direct execution."""
    return psycopg2.connect(get_db_url())

__all__ = ["get_db_url", "get_raw_connection", "DB_URL", "get_conn", "get_cursor", "get_pool"]
