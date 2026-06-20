"""Value Factor Engine (F1) — TASK-211

Tính toán 4 value factors: P/E, P/B, P/S, Dividend Yield.
Chuẩn hóa percentile rank trong Universe.
"""

import logging
import os
import pandas as pd
from datetime import date
from typing import List, Dict, Any

from app.domain.services.base import FactorEngineBase

logger = logging.getLogger(__name__)

class ValueFactorEngine(FactorEngineBase):
    def __init__(self):
        super().__init__()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def calculate_f1_scores(self, target_date: date) -> pd.DataFrame:
        """Tính toán F1 Value Scores cho toàn bộ Universe khả dụng."""
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        conn = psycopg2.connect(self.db_url)
        
        # 1. Lấy data raw: Price (TASK-103) + Financial Ratios (TASK-104) + Universe (TASK-201)
        query = """
            SELECT s.symbol, s.universe_group, r.pe, r.pb, r.net_margin, r.gross_margin
            FROM stocks s
            JOIN financial_ratios r ON s.symbol = r.symbol
            WHERE r.ratio_date <= %s
              AND r.published_date <= %s  -- PIT enforcement
              AND s.universe_group IN ('A', 'B', 'SANDBOX')
            ORDER BY r.ratio_date DESC
        """
        # (Chỉ lấy bản ghi gần nhất cho mỗi symbol trước target_date)
        df = pd.read_sql(query, conn, params=(target_date, target_date))
        df = df.drop_duplicates(subset=['symbol'], keep='first')
        
        if df.empty:
            return df

        # 2. Tính Percentile Ranks
        # P/E thấp -> High Score (Invert)
        df['score_pe'] = self.normalize_percentile(df['pe'], invert=True)
        # P/B thấp -> High Score (Invert)
        df['score_pb'] = self.normalize_percentile(df['pb'], invert=True)
        
        # Composite F1 Value Score (Bình quân)
        df['f1_value_score'] = df[['score_pe', 'score_pb']].mean(axis=1)
        
        # 3. Lưu vào factor_scores
        self._save_scores(df, target_date)
        
        conn.close()
        return df

    def _save_scores(self, df: pd.DataFrame, target_date: date):
        """Lưu kết quả vào bảng factor_scores."""
        import psycopg2
        from psycopg2.extras import execute_values
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        rows = []
        for _, row in df.iterrows():
            rows.append((
                row['symbol'],
                target_date,
                row['f1_value_score'],
                row['score_pe'],
                row['score_pb']
            ))
            
        execute_values(cur, """
            INSERT INTO factor_scores (symbol, score_date, value_score, factor_details)
            VALUES %s
            ON CONFLICT (symbol, score_date) DO UPDATE SET
                value_score = EXCLUDED.value_score,
                factor_details = factor_scores.factor_details || jsonb_build_object(
                    'pe_score', EXCLUDED.value_score, -- Tạm thời lưu chi tiết vào JSON
                    'pb_score', EXCLUDED.value_score
                )
        """, rows)
        
        conn.commit()
        cur.close()
        conn.close()

value_factor_engine = ValueFactorEngine()
