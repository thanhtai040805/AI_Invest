"""HMM Regime Classifier Wrapper

This module is a backward-compatible wrapper for the new RegimeEngineV2.
It maps the new 6-state regime engine back to the legacy 4-state 
interface if needed, or directly exposes the new engine.
"""

import logging
from typing import Dict
from .hmm_regime_engine import hmm_engine, MarketRegimeV2

logger = logging.getLogger(__name__)

class MarketRegime:
    """Standardized Market Regimes for IOS v5.1."""
    BULL_TRENDING = MarketRegimeV2.BULL_MARKET
    BULL_CHOPPY = MarketRegimeV2.BULL_MARKET
    BEAR_TRENDING = MarketRegimeV2.BEAR_MARKET
    BEAR_BOUNCE = MarketRegimeV2.RANGE_BOUND
    RANGE_BOUND = MarketRegimeV2.RANGE_BOUND
    BEAR_PANIC = MarketRegimeV2.BEAR_MARKET

class HMMClassifier:
    """
    Wrapper for RegimeEngineV2 to maintain interface compatibility.
    """
    def __init__(self):
        self.states = MarketRegimeV2.get_all()

    def calculate_posterior(self, vni_vs_ma50: float, breadth_20d: float, vol_trend: float) -> Dict[str, float]:
        """
        Legacy fast rule-based fallback if inference fails.
        """
        probs = {s: 0.0 for s in self.states}
        
        if vni_vs_ma50 > 0.02 and breadth_20d > 60:
            probs[MarketRegimeV2.BULL_MARKET] = 0.8
            probs[MarketRegimeV2.RANGE_BOUND] = 0.2
        elif vni_vs_ma50 < -0.02 and breadth_20d < 30:
            probs[MarketRegimeV2.BEAR_MARKET] = 0.8
            probs[MarketRegimeV2.RANGE_BOUND] = 0.2
        else:
            probs[MarketRegimeV2.RANGE_BOUND] = 0.6
            probs[MarketRegimeV2.BULL_MARKET] = 0.4
            
        total = sum(probs.values())
        if total == 0:
            return {s: 1.0/len(self.states) for s in self.states}
        return {s: v / total for s, v in probs.items()}

    def train_hmm_model(self, days_history: int = 1500) -> bool:
        """
        Triggers the monthly retraining of RegimeEngineV2.
        """
        import psycopg2
        import pandas as pd
        from app.infrastructure.database.pg_pool import DB_URL
        
        try:
            conn = psycopg2.connect(DB_URL)
            query = """
                WITH vni AS (
                    SELECT date, close_adj as close, volume_total as volume,
                           AVG(close_adj) OVER(ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                           AVG(close_adj) OVER(ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) as ma200,
                           AVG(volume_total) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol_ma20
                    FROM market_data_daily
                    WHERE ticker = 'VNINDEX'
                ),
                br AS (
                    SELECT date, breadth_ma50 FROM market_regime
                ),
                macro AS (
                    SELECT indicator_date as date, value as vninbr_interbank_rate
                    FROM macro_indicators
                    WHERE indicator_name = 'vninbr_interbank_rate'
                ),
                ff AS (
                    SELECT trade_date as date, sum(net_value) as net_foreign_value
                    FROM foreign_flow
                    GROUP BY trade_date
                )
                SELECT vni.date, vni.close, vni.volume, vni.ma50, vni.ma200, vni.vol_ma20,
                       COALESCE(br.breadth_ma50, 50.0) as breadth_ma50,
                       macro.vninbr_interbank_rate,
                       COALESCE(ff.net_foreign_value, 0) as net_foreign_value
                FROM vni
                LEFT JOIN br ON vni.date = br.date
                LEFT JOIN macro ON vni.date = macro.date
                LEFT JOIN ff ON vni.date = ff.date
                ORDER BY vni.date DESC
                LIMIT %s
            """
            df = pd.read_sql(query, conn, params=(days_history,))
            conn.close()
            
            df = df.sort_values("date").reset_index(drop=True)
            hmm_engine.fit(df)
            return True
            
        except Exception as e:
            logger.error(f"Failed to train RegimeEngineV2: {e}")
            return False

hmm_classifier = HMMClassifier()
