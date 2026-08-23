"""
Production Training Pipeline
Executes Weekly RAES Engine Retraining and Monthly HMM Retraining.
Designed to be run via cron on weekends/off-market hours.
"""

import os
import sys
import logging
import argparse
from datetime import datetime
import pandas as pd

# Setup paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.domain.rules.market.hmm_classifier import hmm_classifier
from app.domain.services.ml.raes_engine import raes_engine
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.triple_barrier import get_events, get_bins
from app.domain.services.ml.sample_weights import compute_sample_weights_pipeline
from app.infrastructure.database.pg_pool import DB_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TrainPipeline")

def fetch_historical_data(days: int = 1500) -> pd.DataFrame:
    """Fetch OHLCV for Universe."""
    import psycopg2
    try:
        conn = psycopg2.connect(DB_URL)
        # We need a proper universe filter. For now, fetch top 100 tickers by volume
        query = f"""
            SELECT date, ticker, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_total as volume
            FROM market_data_daily
            WHERE date >= CURRENT_DATE - INTERVAL '{days} days'
            AND ticker IN (
                SELECT ticker FROM market_data_daily 
                WHERE date = (SELECT MAX(date) FROM market_data_daily)
                ORDER BY volume_total DESC LIMIT 100
            )
        """
        df = pd.read_sql(query, conn)
        conn.close()
        return df
    except Exception as e:
        logger.error(f"DB Error: {e}")
        return pd.DataFrame()

def train_monthly_hmm():
    """Run monthly HMM Regime retraining."""
    logger.info("Starting Monthly HMM Training...")
    success = hmm_classifier.train_hmm_model(days_history=1500)
    if success:
        logger.info("Monthly HMM Training Completed Successfully.")
    else:
        logger.error("Monthly HMM Training Failed.")

def train_weekly_raes():
    """Run weekly RAES ensemble retraining with Triple Barrier."""
    logger.info("Starting Weekly RAES Training...")
    
    df_raw = fetch_historical_data(days=1000)
    if df_raw.empty:
        logger.error("No data fetched for RAES training.")
        return
        
    df_raw['date'] = pd.to_datetime(df_raw['date'])
    
    features_list = []
    labels_list = []
    weights_list = []
    
    # Process per ticker
    for ticker, group in df_raw.groupby('ticker'):
        group = group.sort_values('date').set_index('date')
        if len(group) < 200:
            continue
            
        # 1. Feature Forge
        feats = feature_forge.generate(group, ticker)
        
        # 2. Triple Barrier Labels
        # Volatility target for barriers
        daily_vol = group['close'].pct_change().rolling(20).std().fillna(0.02)
        
        # We evaluate every day as a potential event
        events = get_events(
            close=group['close'],
            t_events=group.index[120:-10], # Skip initial warmup and last few days without targets
            pt_sl=[2.0, 2.0], # PT = 2 * vol, SL = 2 * vol
            target=daily_vol,
            min_ret=0.01,
            t1=10, # Max hold 10 days
            t_settle=2 # VN T+2
        )
        
        if events.empty:
            continue
            
        bins = get_bins(events, group['close'])
        
        # 3. Sample Weights (Uniqueness)
        weights = compute_sample_weights_pipeline(events['t1'], time_decay=True)
        
        # Align features and labels
        aligned_idx = bins.dropna().index
        features_list.append(feats.loc[aligned_idx])
        labels_list.append(bins.loc[aligned_idx, 'bin'])
        weights_list.append(weights.loc[aligned_idx])
        
    if not features_list:
        logger.error("No valid features/labels generated.")
        return
        
    X_train = pd.concat(features_list)
    y_train = pd.concat(labels_list)
    w_train = pd.concat(weights_list)
    
    # Optional: Purged CV for hyperparameter tuning. Here we just train the final model on all data.
    logger.info(f"Training RAES with {len(X_train)} samples across {len(features_list)} tickers.")
    raes_engine.fit(X_train, y_train, sample_weights=w_train)
    
    logger.info("Weekly RAES Training Completed Successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=["hmm", "raes", "all"], default="all")
    args = parser.parse_args()
    
    if args.job in ["hmm", "all"]:
        train_monthly_hmm()
        
    if args.job in ["raes", "all"]:
        train_weekly_raes()
