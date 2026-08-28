"""PostgreSQL Storage Adapter implementing StoragePort.
Provides high-performance, pooled database access for the domain layer.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple
import psycopg2
from psycopg2.extras import execute_values

from app.application.ports.storage import StoragePort
from app.infrastructure.database.pg_pool import get_conn, DB_URL

logger = logging.getLogger(__name__)


class PostgresAdapter(StoragePort):
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or DB_URL

    def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        """Thực thi câu lệnh SQL qua Connection Pool."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
        except Exception as pool_err:
            logger.warning(f"Pool execution failed ({pool_err}). Attempting direct fallback connection.")
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                    conn.commit()
            except Exception as direct_err:
                logger.error(f"Database execute query failed on both pool and direct connection: {direct_err}", exc_info=True)
                raise direct_err

    def execute_values(self, query: str, values: List[Tuple], page_size: int = 100) -> None:
        """Thực thi bulk insert qua execute_values tối ưu."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    execute_values(cur, query, values, page_size=page_size)
        except Exception as pool_err:
            logger.warning(f"Pool execute_values failed ({pool_err}). Attempting direct fallback connection.")
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        execute_values(cur, query, values, page_size=page_size)
                    conn.commit()
            except Exception as direct_err:
                logger.error(f"Database execute_values failed on both pool and direct connection: {direct_err}", exc_info=True)
                raise direct_err

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Any]:
        """Truy vấn dữ liệu trả về danh sách bản ghi."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return cur.fetchall()
        except Exception as pool_err:
            logger.warning(f"Pool fetch_all failed ({pool_err}). Attempting direct fallback connection.")
            try:
                with psycopg2.connect(self.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(query, params)
                        return cur.fetchall()
            except Exception as direct_err:
                logger.error(f"Database fetch_all failed on both pool and direct connection: {direct_err}", exc_info=True)
                raise direct_err
