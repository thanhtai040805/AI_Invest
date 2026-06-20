"""Sentiment & Altdata Factor Engine (F4, F6) — TASK-214

Tính toán các nhân tố:
F4: Foreign Flow Momentum, Insider Signal.
F6: Altdata (Placeholder cho Google Trends/SVI).
"""

import logging
import os
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Any

from app.domain.services.base import FactorEngineBase

logger = logging.getLogger(__name__)

class SentimentFactorEngine(FactorEngineBase):
    def __init__(self):
        super().__init__()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def calculate_f4_scores(self, target_date: date) -> pd.DataFrame:
        """Tính toán F4 Sentiment Scores (Foreign Flow)."""
        import psycopg2
        conn = psycopg2.connect(self.db_url)
        
        # F4.1: Foreign Flow Momentum (5 phiên gần nhất)
        # AC: Loại ngày ETF rebalance (giả định có flag is_etf_rebalance_day)
        query = """
            SELECT ticker as symbol, trade_date as date, net_value, is_etf_rebalance_day
            FROM foreign_flow
            WHERE trade_date <= %s AND trade_date >= %s
            ORDER BY symbol, trade_date DESC
        """
        start_date = target_date - timedelta(days=20)
        df_flow = pd.read_sql(query, conn, params=(target_date, start_date))
        
        if df_flow.empty:
            conn.close()
            return pd.DataFrame()

        results = []
        for symbol, group in df_flow.groupby('symbol'):
            # Loại bỏ các ngày ETF rebalance
            group = group[group['is_etf_rebalance_day'] == False]
            if len(group) < 5:
                continue
                
            # Tổng net flow 5 phiên gần nhất
            net_flow_5d = group.iloc[:5]['net_value'].sum()
            results.append({'symbol': symbol, 'foreign_flow_5d': net_flow_5d})
            
        df = pd.DataFrame(results)
        if not df.empty:
            df['score_f4'] = self.normalize_percentile(df['foreign_flow_5d'])
            
        conn.close()
        return df

    def calculate_f6_insider_signal(self, target_date: date) -> pd.DataFrame:
        """Tính toán F6 Insider Signal."""
        import psycopg2
        conn = psycopg2.connect(self.db_url)
        
        # F4.3/F6: Insider Transactions ( disclosure_date <= target_date )
        # Chỉ dùng BUY_MARKET, SELL_MARKET...
        query = """
            SELECT ticker as symbol, disclosure_date, transaction_type, volume
            FROM insider_transactions
            WHERE disclosure_date <= %s AND disclosure_date >= %s
              AND transaction_type IN ('BUY_MARKET', 'SELL_MARKET', 'BUY_AGREEMENT', 'SELL_AGREEMENT')
        """
        start_date = target_date - timedelta(days=30)
        df_insider = pd.read_sql(query, conn, params=(target_date, start_date))
        
        if df_insider.empty:
            conn.close()
            return pd.DataFrame()

        # Đơn giản hóa: Sum volume (Buy > 0, Sell < 0)
        df_insider['net_vol'] = df_insider.apply(
            lambda x: x['volume'] if 'BUY' in x['transaction_type'] else -x['volume'], axis=1
        )
        
        summary = df_insider.groupby('symbol')['net_vol'].sum().reset_index()
        summary['score_f6'] = self.normalize_percentile(summary['net_vol'])
        
        conn.close()
        return summary

sentiment_factor_engine = SentimentFactorEngine()
