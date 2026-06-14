import psycopg2
from psycopg2.extras import execute_values
from typing import List, Tuple, Any, Optional
from app.ports.storage import StoragePort

class PostgresAdapter(StoragePort):
    def __init__(self, db_url: str):
        self.db_url = db_url

    def execute(self, query: str, params: Optional[Tuple] = None) -> None:
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
            conn.commit()

    def execute_values(self, query: str, values: List[Tuple], page_size: int = 100) -> None:
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                execute_values(cur, query, values, page_size=page_size)
            conn.commit()

    def fetch_all(self, query: str, params: Optional[Tuple] = None) -> List[Any]:
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
