"""Quality Factor Engine (F2) — TASK-212

Tính toán: ROIC, GPM Stability, Accrual Ratio, Piotroski F-Score.
"""

import logging
import os
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import List, Dict, Any

from app.domain.services.base import FactorEngineBase

logger = logging.getLogger(__name__)

class QualityFactorEngine(FactorEngineBase):
    def __init__(self):
        super().__init__()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def calculate_f2_scores(self, target_date: date) -> pd.DataFrame:
        """Tính toán F2 Quality Scores."""
        import psycopg2
        
        conn = psycopg2.connect(self.db_url)
        
        # 1. Fetch data required for F2 factors
        # ROIC, Accrual Ratio đã được tính sơ bộ trong TASK-104 (FinancialIngestionService)
        # GPM Stability cần history 8 quý.
        query = """
            SELECT symbol, period_end, data
            FROM financial_statements
            WHERE period_end <= %s
              AND published_date <= %s
              AND frequency = 'quarterly'
            ORDER BY symbol, period_end DESC
        """
        df_fs = pd.read_sql(query, conn, params=(target_date, target_date))
        
        if df_fs.empty:
            return pd.DataFrame()

        results = []
        for symbol, group in df_fs.groupby('symbol'):
            # GPM Stability (YoY quarterly)
            gpm_stability = self._calculate_gpm_stability(group)
            
            # Lấy record gần nhất để lấy ROIC, Accrual đã tính sẵn
            latest_data = group.iloc[0]['data']
            roic = latest_data.get('roic', 0)
            accrual = latest_data.get('accrual_ratio', 0)
            
            results.append({
                'symbol': symbol,
                'roic': roic,
                'accrual_ratio': accrual,
                'gpm_stability': gpm_stability
            })
            
        df = pd.DataFrame(results)
        
        # 2. Normalize percentile
        df['score_roic'] = self.normalize_percentile(df['roic'])
        df['score_accrual'] = self.normalize_percentile(df['accrual_ratio'], invert=True) # Low accrual is good
        df['score_gpm'] = self.normalize_percentile(df['gpm_stability'])
        
        df['f2_quality_score'] = df[['score_roic', 'score_accrual', 'score_gpm']].mean(axis=1)
        
        # 3. Save
        self._save_scores(df, target_date)
        
        conn.close()
        return df

    def _calculate_gpm_stability(self, group: pd.DataFrame) -> float:
        """Tính GPM Stability YoY quarterly (1 - std(GPM_Qx,n - GPM_Qx,n-1))."""
        # Parse GPM từ JSON data
        def extract_gpm(d):
            return d.get('gross_margin') or (d.get('Gross profit') / d.get('Net revenue') if d.get('Net revenue') else 0)
            
        group['gpm'] = group['data'].apply(extract_gpm)
        
        # Cần history 8 quý để có 4 cặp YoY
        if len(group) < 8:
            return 0.0
            
        # Tính diff YoY: Q(t) - Q(t-4)
        gpm_values = group['gpm'].values
        yoy_diffs = []
        for i in range(min(4, len(gpm_values) - 4)):
            yoy_diffs.append(gpm_values[i] - gpm_values[i+4])
            
        if not yoy_diffs:
            return 0.0
            
        stability = 1.0 - np.std(yoy_diffs)
        return max(0.0, stability)

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
                row['f2_quality_score']
            ))
            
        execute_values(cur, """
            INSERT INTO factor_scores (symbol, score_date, quality_score)
            VALUES %s
            ON CONFLICT (symbol, score_date) DO UPDATE SET
                quality_score = EXCLUDED.quality_score
        """, rows)
        
        conn.commit()
        cur.close()
        conn.close()

quality_factor_engine = QualityFactorEngine()
