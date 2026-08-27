"""
Train Production Hybrid Stacking Ranker (T+2.5 Engine)
Saves trained 3-Branch Model bundle to data/models/hybrid_stacking_ranker.pkl
"""

import os
import sys
import logging
import numpy as np
import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.hybrid_stacking_ranker import hybrid_stacking_ranker, beneish_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TrainHybridStacking")

def train_and_export():
    logger.info("=== [START] Training Production Hybrid Stacking Ranker ===")
    
    # 1. Fetch Top 150 liquid tickers
    query_tickers = """
        SELECT ticker, SUM(close_adj * volume_continuous) as total_val
        FROM market_data_daily
        WHERE date >= '2020-01-01' AND ticker != 'VNINDEX'
        GROUP BY ticker
        ORDER BY total_val DESC
        LIMIT 150;
    """
    with get_conn() as conn:
        df_tickers = pd.read_sql(query_tickers, conn)
        tickers = df_tickers['ticker'].tolist()
        logger.info(f"Loaded {len(tickers)} Universe tickers from PostgreSQL.")

        query_data = f"""
            SELECT ticker, date, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_continuous as volume
            FROM market_data_daily
            WHERE ticker IN ({','.join([f"'{t}'" for t in tickers])})
            AND date >= '2018-01-01' AND date <= '2026-12-31'
            ORDER BY ticker, date;
        """
        df_data = pd.read_sql(query_data, conn)

    df_data['date'] = pd.to_datetime(df_data['date'])
    data_dict = {}
    for ticker in tickers:
        df_ticker = df_data[df_data['ticker'] == ticker].copy()
        if len(df_ticker) >= 120:
            data_dict[ticker] = df_ticker.set_index('date').sort_index()

    logger.info(f"Prepared {len(data_dict)} valid historical tickers for feature extraction.")

    # 2. Layer 0 Beneish Gate
    df_beneish = beneish_engine.fetch_and_compute_scores(list(data_dict.keys()))

    # 3. Generate Features + T+2.5 Lookaheads
    base_features_dict = {}
    for ticker, df in data_dict.items():
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            feats['high'] = df['high']
            feats['low'] = df['low']
            val_20d = (df['close'] * df['volume']).rolling(20, min_periods=5).mean() / 1e6
            feats['adtv20_bil'] = val_20d

            feats['fwd_ret_3d'] = (df['close'].shift(-3) - df['close']) / df['close']
            for d in [1, 2, 3]:
                feats[f'fwd_high_{d}d'] = (df['high'].shift(-d) - df['close']) / df['close']
                feats[f'fwd_low_{d}d'] = (df['low'].shift(-d) - df['close']) / df['close']
            base_features_dict[ticker] = feats

    logger.info("Computing Graph Contagion & Lead-Lag Alpha...")
    graph_dict = graph_engine.extract_graph_contagion_signals(data_dict)

    combined_list = []
    for ticker, feats in base_features_dict.items():
        g_feats = graph_dict.get(ticker)
        if g_feats is not None and not g_feats.empty:
            merged = pd.concat([feats, g_feats], axis=1).dropna()
        else:
            merged = feats.dropna()
        combined_list.append(merged)

    master_df = pd.concat(combined_list).sort_index()
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)

    fwd_cols = [c for c in master_df.columns if c.startswith('fwd_')]
    exclude_cols = (
        {'ticker', 'close', 'high', 'low', 'forward_ret', 'alpha_forward_ret',
         'rank_label', 'adtv20_bil', 'published_date', 'beneish_m_score', 'is_manipulator'}
        | set(fwd_cols)
    )
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]

    logger.info(f"Master training matrix: {len(master_df)} rows, {len(feature_cols)} features.")

    # 4. Train Hybrid Stacking Ranker
    logger.info("Fitting 3-Branch Ensemble (LambdaMART + 3D Momentum Ridge + Survival Gate)...")
    hybrid_stacking_ranker.fit(master_df, feature_cols)

    # 5. Export Model Artifact
    export_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data/models/hybrid_stacking_ranker.pkl"))
    hybrid_stacking_ranker.save_model(export_path)

    file_size_kb = os.path.getsize(export_path) / 1024
    logger.info(f"=== [SUCCESS] Model exported to {export_path} ({file_size_kb:.2f} KB) ===")

if __name__ == "__main__":
    train_and_export()
