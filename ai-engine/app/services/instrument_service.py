import logging
from typing import Optional, List, Dict, Any
from app.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter
from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

class InstrumentService:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter(DB_URL)
        self._ensure_table()

    def _ensure_table(self):
        """Create instrument_master table if it doesn't exist."""
        query = """
        CREATE TABLE IF NOT EXISTS instrument_master (
            symbol VARCHAR(20) PRIMARY KEY,
            isin VARCHAR(20),
            start_date DATE,
            delist_date DATE,
            exchange VARCHAR(20),
            free_float FLOAT,
            shares_outstanding BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS corporate_actions (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) REFERENCES instrument_master(symbol),
            action_type VARCHAR(50), -- SPLIT, DIVIDEND_CASH, DIVIDEND_STOCK
            ex_date DATE,
            ratio FLOAT, -- e.g., 2.0 for 2:1 split
            amount FLOAT, -- e.g., cash amount
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS adv_20d (
            symbol VARCHAR(20),
            date DATE,
            adv_volume FLOAT,
            adv_value FLOAT,
            PRIMARY KEY (symbol, date)
        );
        """
        try:
            self.storage.execute(query)
            logger.info("Checked/created instrument_master and adv_20d tables.")
        except Exception as e:
            logger.error(f"Failed to create instrument tables: {e}")

    def upsert_instrument(self, data: Dict[str, Any]):
        query = """
        INSERT INTO instrument_master (symbol, isin, start_date, exchange, free_float, shares_outstanding)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            isin = EXCLUDED.isin,
            free_float = EXCLUDED.free_float,
            shares_outstanding = EXCLUDED.shares_outstanding,
            updated_at = CURRENT_TIMESTAMP
        """
        self.storage.execute(query, (
            data.get("symbol"),
            data.get("isin"),
            data.get("start_date"),
            data.get("exchange"),
            data.get("free_float"),
            data.get("shares_outstanding")
        ))
        
    def get_instrument(self, symbol: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM instrument_master WHERE symbol = %s"
        rows = self.storage.fetch_all(query, (symbol,))
        if not rows:
            return None
        # Convert tuple to dict based on column order
        cols = ['symbol', 'isin', 'start_date', 'delist_date', 'exchange', 'free_float', 'shares_outstanding', 'created_at', 'updated_at']
        return dict(zip(cols, rows[0]))
        
    def get_active_symbols(self) -> List[str]:
        query = "SELECT symbol FROM instrument_master WHERE delist_date IS NULL"
        rows = self.storage.fetch_all(query)
        return [r[0] for r in rows]

    def record_adv(self, symbol: str, date_val: str, volume: float, value: float):
        query = """
        INSERT INTO adv_20d (symbol, date, adv_volume, adv_value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol, date) DO UPDATE SET
            adv_volume = EXCLUDED.adv_volume,
            adv_value = EXCLUDED.adv_value
        """
        self.storage.execute(query, (symbol, date_val, volume, value))
