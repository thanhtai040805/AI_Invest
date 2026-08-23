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
        
        # Determine regime label via HMM Engine (fallback to rule-based if HMM unavailable)
        label = "RANGE_BOUND"
        try:
            from app.domain.rules.market.hmm_regime_engine import hmm_engine, MarketRegimeV2
            if hmm_engine.is_trained:
                # Build minimal DataFrame for daily inference if needed
                import pandas as pd
                dummy_df = pd.DataFrame([{
                    "close": 1200.0,
                    "ma50": 1200.0 * (1.0 + (ma50 - 50) / 100.0),
                    "ma200": 1200.0 * (1.0 + (ma200 - 50) / 100.0),
                    "breadth_ma50": ma50,
                    "volume": 1e8,
                    "vol_ma20": 1e8
                }])
                probs = hmm_engine.infer_daily(dummy_df)
                if probs:
                    label = max(probs, key=probs.get)
            else:
                # Rule-based fallback mapped to 6 HMM states
                if ma50 > 60 and ma200 > 50:
                    label = "BULL_MOMENTUM"
                elif ma50 > 50 and ma200 <= 50:
                    label = "BULL_DISTRIBUTION"
                elif ma50 < 40 and ma200 < 40:
                    label = "BEAR_PANIC" if rsi_os > 20 else "BEAR_GRINDING"
                elif rsi_os > 15:
                    label = "RECOVERY_EARLY"
                else:
                    label = "RANGE_BOUND"
        except Exception as e:
            logger.warning(f"Failed to infer regime via HMM engine, using fallback: {e}")
            if ma50 > 60:
                label = "BULL_MOMENTUM"
            elif ma50 < 40:
                label = "BEAR_GRINDING"

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
