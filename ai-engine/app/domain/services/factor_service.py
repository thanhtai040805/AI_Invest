import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import date
from app.application.ports.storage import StoragePort
from app.infrastructure.database.postgres_adapter import PostgresAdapter
from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

class FactorService:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter(DB_URL)

    def compute_daily_factors(self, target_date: date):
        """
        Compute and normalize factors cross-sectionally for all active stocks.
        Uses point-in-time technical and fundamental data.
        """
        # 1. Fetch data for all stocks
        # Joining technical_indicators (JSONB), ohlcv (Columns), and financial_ratios (Columns)
        query = """
        SELECT 
            s.symbol,
            (t.indicators->>'rsi_14')::float as rsi,
            o.close::float as close,
            (t.indicators->>'ma200')::float as ma200,
            f.roe,
            f.pe,
            f.pb
        FROM stocks s
        JOIN ohlcv o ON s.symbol = o.symbol AND o.time::date = %s
        JOIN technical_indicators t ON s.symbol = t.symbol AND t.calc_date = %s
        LEFT JOIN financial_ratios f ON s.symbol = f.symbol AND f.ratio_date <= %s
        WHERE s.exchange IN ('HOSE', 'HSX')
        AND o.volume > 0
        """
        try:
            rows = self.storage.fetch_all(query, (target_date, target_date, target_date))
        except Exception as e:
            logger.error(f"Failed to fetch data for factors: {e}")
            return

        if not rows:
            logger.warning(f"No data to compute factors for {target_date}")
            return

        df = pd.DataFrame(rows, columns=['symbol', 'rsi', 'close', 'ma200', 'roe', 'pe', 'pb'])
        
        # 2. Handle missing data and outliers
        for col in ['roe', 'pe', 'pb', 'rsi', 'close', 'ma200']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Fill missing fundamentals with median
        for col in ['roe', 'pe', 'pb']:
            df[col] = df[col].fillna(df[col].median())

        # 3. Compute raw factor values
        # Momentum: Relative Strength (Distance from 200DMA as proxy for trend quality)
        df['raw_mom'] = df['close'] / df['ma200'].replace(0, np.nan) - 1.0
        
        # Quality: Return on Equity
        df['raw_qual'] = df['roe']
        
        # Value: Earnings Yield (E/P) - inverted PE is more stable
        df['raw_val'] = 1.0 / df['pe'].replace(0, np.nan)

        # Handle infinite values from division
        df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

        # 4. Winsorization (Institutional standard: clip outliers at 3 std devs)
        for col in ['raw_mom', 'raw_qual', 'raw_val']:
            mean = df[col].mean()
            std = df[col].std()
            df[col] = df[col].clip(lower=mean - 3*std, upper=mean + 3*std)

        # 5. Z-Score Normalization (Cross-sectional)
        for col in ['raw_mom', 'raw_qual', 'raw_val']:
            mean = df[col].mean()
            std = df[col].std()
            df[f'z_{col}'] = (df[col] - mean) / (std if std > 0 else 1.0)

        # 6. Composite Score
        # 40% Momentum, 30% Quality, 30% Value
        df['composite_score'] = df['z_raw_mom'] * 0.4 + df['z_raw_qual'] * 0.3 + df['z_raw_val'] * 0.3

        # 7. Persist to factor_scores table using existing column names
        # Ensure unique symbols for target_date to avoid CardinalityViolation
        df = df.drop_duplicates(subset=['symbol'])
        
        insert_rows = []
        for _, row in df.iterrows():
            insert_rows.append((
                row['symbol'],
                target_date,
                float(row['z_raw_val']),   # value_score
                float(row['z_raw_qual']),  # quality_score
                float(row['z_raw_mom']),   # momentum_3m (as proxy)
                float(row['composite_score'])
            ))

        insert_query = """
        INSERT INTO factor_scores (symbol, score_date, value_score, quality_score, momentum_3m, composite_score)
        VALUES %s
        ON CONFLICT (symbol, score_date) DO UPDATE SET
            value_score = EXCLUDED.value_score,
            quality_score = EXCLUDED.quality_score,
            momentum_3m = EXCLUDED.momentum_3m,
            composite_score = EXCLUDED.composite_score,
            updated_at = CURRENT_TIMESTAMP
        """
        self.storage.execute_values(insert_query, insert_rows)
        logger.info(f"Computed factor scores for {len(df)} symbols on {target_date}")
