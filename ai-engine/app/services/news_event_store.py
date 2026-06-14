import logging
from typing import Optional, List, Dict, Any
from app.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter
from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

class NewsEventStore:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter(DB_URL)
        self._ensure_table()

    def _ensure_table(self):
        query = """
        CREATE TABLE IF NOT EXISTS news_events (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20),
            published_date TIMESTAMP,
            title TEXT,
            url TEXT,
            source VARCHAR(50),
            config_id INT,
            sentiment_score FLOAT,
            ingest_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            latency_seconds FLOAT,
            UNIQUE(symbol, url)
        );
        """
        try:
            self.storage.execute(query)
            logger.info("Checked/created news_events table with ingest_time/latency.")
        except Exception as e:
            logger.error(f"Failed to create news_events table: {e}")

    def store_events(self, events: List[Dict[str, Any]]):
        rows = []
        for e in events:
            # Latency is ingest_time (now) - published_date
            # But here we do it in DB side or Python side
            rows.append((
                e["symbol"],
                e["published_date"],
                e["title"],
                e["url"],
                e["source"],
                e.get("config_id", 0),
                e.get("sentiment_score", 0.0)
            ))
            
        # Using EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - published_date)) for latency
        query = """
        INSERT INTO news_events
        (symbol, published_date, title, url, source, config_id, sentiment_score, latency_seconds)
        VALUES %s
        ON CONFLICT (symbol, url) DO NOTHING
        """
        
        # Modify the values clause to include latency calculation during insert
        modified_query = """
        INSERT INTO news_events
        (symbol, published_date, title, url, source, config_id, sentiment_score, latency_seconds)
        SELECT 
            v.column1, v.column2::timestamp, v.column3, v.column4, v.column5, v.column6::int, v.column7::float,
            EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - v.column2::timestamp))
        FROM (VALUES %s) AS v
        ON CONFLICT (symbol, url) DO NOTHING
        """
        
        try:
            self.storage.execute_values(modified_query, rows, page_size=100)
            logger.info(f"Stored {len(rows)} news events.")
        except Exception as e:
            logger.error(f"Failed to store news events: {e}")
