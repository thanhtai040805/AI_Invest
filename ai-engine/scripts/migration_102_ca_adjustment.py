"""Migration for TASK-102: add corporate_actions.applied + market_data_daily table."""
import os

import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:123@localhost:5432/aiinvest",
)


def migrate_ca():
    """Add applied column to corporate_actions if not exists."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        ALTER TABLE corporate_actions
        ADD COLUMN IF NOT EXISTS applied BOOLEAN DEFAULT FALSE
    """)
    cur.execute("""
        ALTER TABLE corporate_actions
        ADD COLUMN IF NOT EXISTS adjustment_factor DOUBLE PRECISION
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("  [OK] corporate_actions: added applied, adjustment_factor columns")


def create_market_data_daily():
    """Create market_data_daily table per DATA_SCHEMA.md."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_data_daily (
            ticker              TEXT NOT NULL,
            date                DATE NOT NULL,
            open_adj            DOUBLE PRECISION,
            high_adj            DOUBLE PRECISION,
            low_adj             DOUBLE PRECISION,
            close_adj           DOUBLE PRECISION,
            close_unadj         DOUBLE PRECISION,
            vwap                DOUBLE PRECISION,
            volume_continuous   BIGINT DEFAULT 0,
            volume_atc          BIGINT DEFAULT 0,
            volume_ato          BIGINT DEFAULT 0,
            volume_total        BIGINT DEFAULT 0,
            foreign_buy_vol     BIGINT DEFAULT 0,
            foreign_sell_vol    BIGINT DEFAULT 0,
            foreign_net_vol     BIGINT DEFAULT 0,
            is_etf_rebalance_day BOOLEAN DEFAULT FALSE,
            adtv20_continuous   DOUBLE PRECISION,
            market_cap          DOUBLE PRECISION,
            adj_factor          DOUBLE PRECISION DEFAULT 1.0,
            data_source         TEXT DEFAULT 'manual',
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (ticker, date)
        )
    """)
    # Create index for efficient lookups
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_data_daily_date
        ON market_data_daily (date DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_data_daily_ticker
        ON market_data_daily (ticker, date DESC)
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("  [OK] market_data_daily: table created with indexes")


def run():
    print("TASK-102 migration starting...")
    migrate_ca()
    create_market_data_daily()
    print("Migration complete.")


if __name__ == "__main__":
    run()
