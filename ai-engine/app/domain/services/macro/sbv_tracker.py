"""
SBV Tracker (State Bank of Vietnam Macro-Economic Tracker)
Fetches and processes macroeconomic indicators from PostgreSQL (`macro_indicators` table)
that critically affect the VN-Index (VNIBOR, Refinancing Rate, USD Index, etc.).
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Any
from app.infrastructure.database.pg_pool import get_cursor

logger = logging.getLogger(__name__)

class SBVTracker:
    def __init__(self):
        pass

    def fetch_indicator(self, indicator_name: str, days: int = 30) -> pd.DataFrame:
        """
        Fetches historical records for a specific macro indicator from PostgreSQL.
        """
        try:
            with get_cursor() as cur:
                query = """
                    SELECT indicator_date, value
                    FROM macro_indicators
                    WHERE indicator_name = %s
                    ORDER BY indicator_date DESC
                    LIMIT %s
                """
                cur.execute(query, (indicator_name, days))
                rows = cur.fetchall()
                if not rows:
                    logger.warning(f"No records found in macro_indicators for '{indicator_name}'.")
                    return pd.DataFrame(columns=['date', 'value']).set_index('date')
                
                df = pd.DataFrame(rows, columns=['date', 'value'])
                df['date'] = pd.to_datetime(df['date'])
                return df.sort_values('date').set_index('date')
        except Exception as e:
            logger.error(f"Error fetching macro indicator {indicator_name}: {e}")
            return pd.DataFrame(columns=['date', 'value']).set_index('date')

    def get_macro_state_vector(self) -> Dict[str, Any]:
        """
        Aggregates real SBV and macroeconomic indicators from PostgreSQL.
        Used as static/past covariates for market regime classification and Deep Learning.
        """
        try:
            # Query real indicators from Postgres
            vnibor_on = self.fetch_indicator('interest_rate_on', days=10)
            vnibor_1w = self.fetch_indicator('interest_rate_1w', days=10)
            usd_idx = self.fetch_indicator('usd_index', days=10)

            fx_momentum = 0.0
            if len(usd_idx) >= 2:
                fx_momentum = (usd_idx['value'].iloc[-1] / usd_idx['value'].iloc[0]) - 1.0

            vnibor_spread = 0.0
            if len(vnibor_1w) > 0 and len(vnibor_on) > 0:
                vnibor_spread = float(vnibor_1w['value'].iloc[-1] - vnibor_on['value'].iloc[-1])

            latest_on_rate = float(vnibor_on['value'].iloc[-1]) if len(vnibor_on) > 0 else 0.0

            return {
                "fx_momentum_5d": float(fx_momentum),
                "vnibor_spread_bps": float(vnibor_spread * 100),
                "interbank_on_rate": latest_on_rate,
                "is_liquidity_squeeze": bool(latest_on_rate > 6.0 or vnibor_spread < 0)
            }
        except Exception as e:
            logger.error(f"Failed to compile real macro state vector: {e}")
            return {
                "fx_momentum_5d": 0.0,
                "vnibor_spread_bps": 0.0,
                "interbank_on_rate": 0.0,
                "is_liquidity_squeeze": False
            }

sbv_tracker = SBVTracker()
