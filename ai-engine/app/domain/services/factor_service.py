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

    def _calc_zscore_by_sector(self, df: pd.DataFrame, col: str, new_col: str) -> pd.DataFrame:
        """Calculate Z-Score grouped by sector (ICB Level 2/3 equivalent)."""
        df[new_col] = df.groupby('sector')[col].transform(lambda x: (x - x.mean()) / x.std(ddof=0))
        # Fill NaN if sector has too few stocks (std=0 or NaN)
        df[new_col] = df[new_col].fillna(0.0)
        return df

    def compute_daily_factors(self, target_date: date):
        """
        Compute F1-F6 factors cross-sectionally for all active stocks based on Master Plan.
        """
        query = """
        SELECT 
            s.symbol,
            s.sector,
            o.close::float as close,
            o.volume::float as volume,
            (t.indicators->>'ma200')::float as ma200,
            (t.indicators->>'mom_3m')::float as mom_3m,
            (t.indicators->>'mom_6m')::float as mom_6m,
            (t.indicators->>'mom_12m')::float as mom_12m,
            f.pe,
            f.pb,
            f.ev_ebitda,
            f.roe,
            f.roic,
            f.accrual_ratio,
            f.gpm_stability,
            f.nim,
            f.npl_ratio,
            f.casa_ratio,
            f.net_debt_equity,
            f.presales_inventory,
            f.rnav_discount,
            f.core_earnings_growth,
            f.earnings_yield,
            f.sue,
            (m.foreign_net_flow_20d)::float as foreign_net_flow_20d,
            (m.prop_trading_flow_20d)::float as prop_trading_flow_20d,
            (m.inst_holdings_change)::float as inst_holdings_change
        FROM stocks s
        JOIN ohlcv o ON s.symbol = o.symbol AND o.time::date = %s
        LEFT JOIN technical_indicators t ON s.symbol = t.symbol AND t.calc_date = %s
        LEFT JOIN financial_ratios f ON s.symbol = f.symbol AND f.ratio_date <= %s
        LEFT JOIN market_data_daily m ON s.symbol = m.ticker AND m.date = %s
        WHERE s.exchange IN ('HOSE', 'HSX')
        AND o.volume > 0
        """
        try:
            rows = self.storage.fetch_all(query, (target_date, target_date, target_date, target_date))
        except Exception as e:
            logger.error(f"Failed to fetch data for factors: {e}")
            return

        if not rows:
            logger.warning(f"No data to compute factors for {target_date}")
            return

        df = pd.DataFrame(rows)
        # Ensure numeric columns
        numeric_cols = df.columns.drop(['symbol', 'sector'])
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        df['sector'] = df['sector'].fillna('Unknown')

        # --- 1. F1: Value (Sector Z-Score) ---
        # Non-Fin: EV/EBITDA, P/E (inverted for higher=better)
        # Bank: P/B, P/E (inverted)
        # Real Estate: RNAV Discount, P/B (inverted)
        df['val_metric1'] = np.where(df['sector'].str.contains('Ngân hàng', case=False, na=False), 1.0 / df['pb'].replace(0, np.nan),
                            np.where(df['sector'].str.contains('Bất động sản', case=False, na=False), df['rnav_discount'],
                            1.0 / df['ev_ebitda'].replace(0, np.nan)))
        df['val_metric2'] = np.where(df['sector'].str.contains('Bất động sản', case=False, na=False), 1.0 / df['pb'].replace(0, np.nan),
                            1.0 / df['pe'].replace(0, np.nan))
        
        df = df.replace([np.inf, -np.inf], np.nan).fillna(0)
        df = self._calc_zscore_by_sector(df, 'val_metric1', 'z_val1')
        df = self._calc_zscore_by_sector(df, 'val_metric2', 'z_val2')
        df['f1_value'] = (df['z_val1'] + df['z_val2']) / 2 * 10 + 50 # scale to 0-100

        # --- 2. F2: Quality (Sector Z-Score) ---
        # Non-Fin: ROIC, Accrual (inv), GPM
        # Bank: NIM, NPL (inv), CASA
        # Real Estate: Net Debt/Eq (inv), Presales/Inv
        df['qual_metric1'] = np.where(df['sector'].str.contains('Ngân hàng', case=False, na=False), df['nim'],
                             np.where(df['sector'].str.contains('Bất động sản', case=False, na=False), -df['net_debt_equity'],
                             df['roic']))
        df['qual_metric2'] = np.where(df['sector'].str.contains('Ngân hàng', case=False, na=False), -df['npl_ratio'],
                             np.where(df['sector'].str.contains('Bất động sản', case=False, na=False), df['presales_inventory'],
                             -df['accrual_ratio']))
        df['qual_metric3'] = np.where(df['sector'].str.contains('Ngân hàng', case=False, na=False), df['casa_ratio'],
                             np.where(df['sector'].str.contains('Bất động sản', case=False, na=False), 0,
                             df['gpm_stability']))
                             
        df = self._calc_zscore_by_sector(df, 'qual_metric1', 'z_qual1')
        df = self._calc_zscore_by_sector(df, 'qual_metric2', 'z_qual2')
        df = self._calc_zscore_by_sector(df, 'qual_metric3', 'z_qual3')
        df['f2_quality'] = (df['z_qual1'] + df['z_qual2'] + df['z_qual3']) / 3 * 10 + 50
        # Calculate Percentile for Gatekeeper
        df['f2_quality_percentile'] = df['f2_quality'].rank(pct=True) * 100

        # --- 3. F3: Momentum ---
        # 3M, 6M, 12M. Drop 1M.
        df['raw_mom'] = (df['mom_3m'] + df['mom_6m'] + df['mom_12m']) / 3
        # Peak Penalty: If 12M > 100% and Core Earnings Growth <= 0 -> penalize
        peak_penalty_cond = (df['mom_12m'] > 1.0) & (df['core_earnings_growth'] <= 0)
        df.loc[peak_penalty_cond, 'raw_mom'] -= 0.5
        
        df = self._calc_zscore_by_sector(df, 'raw_mom', 'z_mom')
        df['f3_momentum'] = df['z_mom'] * 10 + 50

        # --- 4. F4: Earnings (Sector Z-Score) ---
        df['raw_earn'] = df['core_earnings_growth'] + df['earnings_yield'] + df['sue']
        df = self._calc_zscore_by_sector(df, 'raw_earn', 'z_earn')
        df['f4_earnings'] = df['z_earn'] * 10 + 50

        # --- 5. F5: Flow (Dòng tiền thông minh) ---
        df['raw_flow'] = df['foreign_net_flow_20d'] + df['prop_trading_flow_20d'] + df['inst_holdings_change']
        df['z_flow'] = (df['raw_flow'] - df['raw_flow'].mean()) / (df['raw_flow'].std(ddof=0) + 1e-9)
        df['f5_flow'] = df['z_flow'].fillna(0) * 10 + 50

        # --- 6. F6: Technical (VCP, Seasonality) ---
        # Proxy: price relative to MA200 and Volume
        df['raw_tech'] = (df['close'] / df['ma200'].replace(0, np.nan)) - 1.0
        df['z_tech'] = (df['raw_tech'] - df['raw_tech'].mean()) / (df['raw_tech'].std(ddof=0) + 1e-9)
        df['f6_technical'] = df['z_tech'].fillna(0) * 10 + 50
        
        # Clip all factors 0-100
        for col in ['f1_value', 'f2_quality', 'f3_momentum', 'f4_earnings', 'f5_flow', 'f6_technical']:
            df[col] = df[col].clip(0, 100)

        # 7. Persist to factor_scores
        df = df.drop_duplicates(subset=['symbol'])
        
        insert_rows = []
        for _, row in df.iterrows():
            insert_rows.append((
                row['symbol'],
                target_date,
                float(row['f1_value']),
                float(row['f2_quality']),
                float(row['f3_momentum']),
                float(row['f4_earnings']),
                float(row['f5_flow']),
                float(row['f6_technical']),
                float(row['f2_quality_percentile'])
            ))

        # We assume factor_scores table has these columns. If not, this might need migration.
        # But this code implements the business logic required by the Master Plan.
        insert_query = """
        INSERT INTO factor_scores (
            symbol, score_date, f1_value, f2_quality, f3_momentum, 
            f4_earnings, f5_flow, f6_technical, f2_quality_percentile
        )
        VALUES %s
        ON CONFLICT (symbol, score_date) DO UPDATE SET
            f1_value = EXCLUDED.f1_value,
            f2_quality = EXCLUDED.f2_quality,
            f3_momentum = EXCLUDED.f3_momentum,
            f4_earnings = EXCLUDED.f4_earnings,
            f5_flow = EXCLUDED.f5_flow,
            f6_technical = EXCLUDED.f6_technical,
            f2_quality_percentile = EXCLUDED.f2_quality_percentile,
            updated_at = CURRENT_TIMESTAMP
        """
        try:
            self.storage.execute_values(insert_query, insert_rows)
            logger.info(f"Computed factor scores for {len(df)} symbols on {target_date}")
        except Exception as e:
            logger.error(f"Error persisting factor scores: {e}. Check if schema matches.")
