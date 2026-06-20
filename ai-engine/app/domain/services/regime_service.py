import logging
from datetime import date, timedelta
from typing import Dict, Any, List, Optional
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter
from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

class RegimeService:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter(DB_URL)
        self._ensure_table()

    def _ensure_table(self):
        query = """
        CREATE TABLE IF NOT EXISTS market_regime (
            date DATE PRIMARY KEY,
            breadth_ma50 FLOAT,  -- % of stocks > MA50
            breadth_ma200 FLOAT, -- % of stocks > MA200
            breadth_rsi_oversold FLOAT, -- % of stocks RSI < 30
            breadth_rsi_overbought FLOAT, -- % of stocks RSI > 70
            market_volume_sma20_ratio FLOAT, -- Current Volume / 20D Avg Volume
            net_foreign_flow_bil FLOAT, -- Total foreign net flow in billion VND
            net_prop_flow_bil FLOAT,    -- Total proprietary net flow in billion VND
            regime_label VARCHAR(50), -- BULL, BEAR, SIDEWAYS, VOLATILE
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        self.storage.execute(query)

    def compute_daily_breadth(self, target_date: date):
        """Compute breadth and flow metrics for a specific date."""
        # 1. Breadth Metrics
        breadth_query = """
        WITH latest_indicators AS (
            SELECT symbol, indicators
            FROM technical_indicators
            WHERE indicator_date = %s
        ),
        counts AS (
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN (indicators->>'close')::float > (indicators->>'ma50')::float THEN 1 ELSE 0 END) as above_ma50,
                SUM(CASE WHEN (indicators->>'close')::float > (indicators->>'ma200')::float THEN 1 ELSE 0 END) as above_ma200,
                SUM(CASE WHEN (indicators->>'rsi_14')::float < 30 THEN 1 ELSE 0 END) as rsi_oversold,
                SUM(CASE WHEN (indicators->>'rsi_14')::float > 70 THEN 1 ELSE 0 END) as rsi_overbought
            FROM latest_indicators
        )
        SELECT 
            (above_ma50::float / NULLIF(total, 0)) * 100,
            (above_ma200::float / NULLIF(total, 0)) * 100,
            (rsi_oversold::float / NULLIF(total, 0)) * 100,
            (rsi_overbought::float / NULLIF(total, 0)) * 100
        FROM counts;
        """
        breadth_rows = self.storage.fetch_all(breadth_query, (target_date,))
        if not breadth_rows or breadth_rows[0][0] is None:
            logger.warning(f"No indicator data for {target_date} to compute breadth.")
            return

        ma50, ma200, rsi_os, rsi_ob = breadth_rows[0]

        # 2. Aggregate Foreign Flow
        flow_query = """
        SELECT SUM(net_value) / 1000000000.0 -- Billion VND
        FROM foreign_flow
        WHERE trade_date = %s
        """
        flow_rows = self.storage.fetch_all(flow_query, (target_date,))
        net_foreign = flow_rows[0][0] or 0.0
        
        # Determine regime label
        label = "SIDEWAYS"
        if ma50 > 60 and ma200 > 50:
            label = "BULL"
        elif ma50 < 40 and ma200 < 40:
            label = "BEAR"
        elif rsi_os > 20:
            label = "VOLATILE"

        insert_query = """
        INSERT INTO market_regime (date, breadth_ma50, breadth_ma200, breadth_rsi_oversold, breadth_rsi_overbought, net_foreign_flow_bil, regime_label)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            breadth_ma50 = EXCLUDED.breadth_ma50,
            breadth_ma200 = EXCLUDED.breadth_ma200,
            breadth_rsi_oversold = EXCLUDED.breadth_rsi_oversold,
            breadth_rsi_overbought = EXCLUDED.breadth_rsi_overbought,
            net_foreign_flow_bil = EXCLUDED.net_foreign_flow_bil,
            regime_label = EXCLUDED.regime_label
        """
        self.storage.execute(insert_query, (target_date, ma50, ma200, rsi_os, rsi_ob, net_foreign, label))
        logger.info(f"Computed market regime for {target_date}: {label}, Net Foreign: {net_foreign:.1f}B")

    def get_latest_regime(self) -> Dict[str, Any]:
        query = "SELECT * FROM market_regime ORDER BY date DESC LIMIT 1"
        rows = self.storage.fetch_all(query)
        if not rows:
            return {"regime_label": "UNKNOWN", "breadth_ma50": 50.0}
        cols = ['date', 'breadth_ma50', 'breadth_ma200', 'breadth_rsi_oversold', 'breadth_rsi_overbought', 'market_volume_sma20_ratio', 'regime_label', 'created_at']
        return dict(zip(cols, rows[0]))

    def get_regime_for_date(self, target_date: date) -> Dict[str, Any]:
        """Fetch market regime as available on a specific date."""
        query = "SELECT * FROM market_regime WHERE date <= %s ORDER BY date DESC LIMIT 1"
        rows = self.storage.fetch_all(query, (target_date,))
        if not rows:
            return {"regime_label": "UNKNOWN", "breadth_ma50": 50.0}
        cols = ['date', 'breadth_ma50', 'breadth_ma200', 'breadth_rsi_oversold', 'breadth_rsi_overbought', 'market_volume_sma20_ratio', 'net_foreign_flow_bil', 'net_prop_flow_bil', 'regime_label', 'created_at']
        return dict(zip(cols, rows[0]))
