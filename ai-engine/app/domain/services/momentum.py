"""Momentum Factor Engine (F3) — TASK-213

Tính toán: Price Momentum, Earnings Momentum (SUE), Relative Strength.
"""

import logging
import os
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import List, Dict, Any

from app.domain.services.base import FactorEngineBase

logger = logging.getLogger(__name__)

class MomentumFactorEngine(FactorEngineBase):
    def __init__(self):
        super().__init__()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def calculate_f3_scores(self, target_date: date) -> pd.DataFrame:
        """Tính toán F3 Momentum Scores."""
        import psycopg2
        
        conn = psycopg2.connect(self.db_url)
        
        # 1. Price Momentum (1m, 3m, 12m)
        # Lấy giá đóng cửa đã điều chỉnh (close_adj)
        query = """
            SELECT ticker as symbol, date, close_adj
            FROM market_data_daily
            WHERE date <= %s
              AND date >= %s
            ORDER BY symbol, date DESC
        """
        # Lấy khoảng 1.5 năm để tính 12m return
        start_date = target_date - timedelta(days=500)
        df_price = pd.read_sql(query, conn, params=(target_date, start_date))
        
        if df_price.empty:
            return pd.DataFrame()

        results = []
        for symbol, group in df_price.groupby('symbol'):
            # Đảm bảo dữ liệu sắp xếp theo ngày giảm dần (mới nhất ở index 0)
            group = group.sort_values('date', ascending=False).reset_index(drop=True)
            latest_price = group.iloc[0]['close_adj']
            
            # 1m return (~20 phiên)
            mom_1m = self._get_return(group, latest_price, 20)
            # 3m return (~60 phiên)
            mom_3m = self._get_return(group, latest_price, 60)
            # 12m return (~250 phiên)
            mom_12m = self._get_return(group, latest_price, 250)
            
            results.append({
                'symbol': symbol,
                'mom_1m': mom_1m,
                'mom_3m': mom_3m,
                'mom_12m': mom_12m
            })
            
        df = pd.DataFrame(results)
        
        # 2. Normalize percentile
        df['score_1m'] = self.normalize_percentile(df['mom_1m'])
        df['score_3m'] = self.normalize_percentile(df['mom_3m'])
        df['score_12m'] = self.normalize_percentile(df['mom_12m'])
        
        # Composite F3 Score (Trọng số tùy chỉnh hoặc trung bình)
        df['f3_momentum_score'] = df[['score_1m', 'score_3m', 'score_12m']].mean(axis=1)
        
        # 3. Save
        self._save_scores(df, target_date)
        
        conn.close()
        return df

    def _get_return(self, group: pd.DataFrame, latest_price: float, periods: int) -> float:
        if len(group) <= periods:
            return 0.0
        old_price = group.iloc[periods]['close_adj']
        return (latest_price / old_price) - 1 if old_price > 0 else 0.0

    def _save_scores(self, df: pd.DataFrame, target_date: date):
        import psycopg2
        from psycopg2.extras import execute_values
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        rows = []
        for _, row in df.iterrows():
            rows.append((
                row['symbol'],
                target_date,
                row['mom_1m'],
                row['mom_3m'],
                row['mom_12m']
            ))
            
        execute_values(cur, """
            INSERT INTO factor_scores (
                symbol, score_date, momentum_1m, momentum_3m, momentum_12m
            ) VALUES %s
            ON CONFLICT (symbol, score_date) DO UPDATE SET
                momentum_1m = EXCLUDED.momentum_1m,
                momentum_3m = EXCLUDED.momentum_3m,
                momentum_12m = EXCLUDED.momentum_12m
        """, rows)
        
        conn.commit()
        cur.close()
        conn.close()

momentum_factor_engine = MomentumFactorEngine()
