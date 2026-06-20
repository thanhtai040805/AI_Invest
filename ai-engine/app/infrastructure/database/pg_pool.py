"""PostgreSQL connection helper — simple pool pattern for ai-engine."""

import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

_pool: Optional[pg_pool.ThreadedConnectionPool] = None
_lock = threading.Lock()


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = pg_pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=5,
                    dsn=DB_URL,
                )
    return _pool


@contextmanager
def get_conn():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor():
    with get_conn() as conn:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def migrate():
    """Create core tables if not exists (job_states, macro_indicators, +others)."""
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_states (
                job_name    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                started_at  TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                metadata    JSONB DEFAULT '{}',
                error       TEXT,
                PRIMARY KEY (job_name)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS macro_indicators (
                indicator_date DATE NOT NULL,
                indicator_name TEXT NOT NULL,
                value          FLOAT NOT NULL,
                unit           TEXT,
                source         TEXT,
                created_at     TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (indicator_date, indicator_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS financial_statements (
                symbol          TEXT NOT NULL,
                period_end      DATE NOT NULL,
                statement_type  TEXT NOT NULL,
                frequency       TEXT NOT NULL,
                data            JSONB NOT NULL,
                source          TEXT DEFAULT 'vnstock',
                fetched_at      TIMESTAMPTZ DEFAULT NOW(),
                published_date  DATE,
                PRIMARY KEY (symbol, period_end, statement_type, frequency)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS technical_indicators (
                symbol      TEXT NOT NULL,
                calc_date   DATE NOT NULL,
                indicators  JSONB NOT NULL,
                updated_at  TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (symbol, calc_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS financial_ratios (
                symbol      TEXT NOT NULL,
                ratio_date  DATE NOT NULL,
                pe          FLOAT, pb FLOAT, roe FLOAT, roa FLOAT,
                debt_equity FLOAT, current_ratio FLOAT, gross_margin FLOAT,
                net_margin  FLOAT, fcf_yield FLOAT, ev_ebitda FLOAT,
                yoy_revenue_growth FLOAT, yoy_earnings_growth FLOAT,
                published_date DATE,
                updated_at  TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (symbol, ratio_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS factor_scores (
                symbol      TEXT NOT NULL,
                score_date  DATE NOT NULL,
                value_score FLOAT, quality_score FLOAT,
                momentum_1m FLOAT, momentum_3m FLOAT, momentum_12m FLOAT,
                size_score  FLOAT, volatility_score FLOAT,
                liquidity_score FLOAT, composite_score FLOAT,
                percentile  FLOAT,
                -- VN-specific factor scores (added Jun 2026)
                earnings_yield_score FLOAT,
                accrual_score FLOAT,
                foreign_flow_score FLOAT,
                insider_score FLOAT,
                conditional_mom_score FLOAT,
                -- Tier B factor scores
                earnings_surprise_score FLOAT,
                distress_score FLOAT,
                piotroski_score FLOAT,
                -- Per-factor detail storage
                factor_details JSONB,
                updated_at  TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (symbol, score_date)
            )
        """)
        # Migration for existing tables (PG 9.6+)
        for col in [
            "earnings_yield_score FLOAT",
            "accrual_score FLOAT",
            "foreign_flow_score FLOAT",
            "insider_score FLOAT",
            "conditional_mom_score FLOAT",
            "earnings_surprise_score FLOAT",
            "distress_score FLOAT",
            "piotroski_score FLOAT",
            "factor_details JSONB",
        ]:
            try:
                cur.execute(f"ALTER TABLE factor_scores ADD COLUMN IF NOT EXISTS {col}")
            except Exception:
                pass  # table might not exist yet

        cur.execute("DROP TABLE IF EXISTS risk_flags CASCADE")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS insider_trades (
                id                  SERIAL PRIMARY KEY,
                symbol              TEXT NOT NULL,
                trade_date          DATE NOT NULL,
                trader_name         TEXT,
                trader_position     TEXT,
                trade_type          TEXT,
                quantity            BIGINT,
                related_man         TEXT,
                related_man_position TEXT,
                before_volume       BIGINT,
                after_volume        BIGINT,
                ownership_pct       FLOAT,
                plan_buy_volume     BIGINT,
                plan_sell_volume    BIGINT,
                plan_begin_date     DATE,
                plan_end_date       DATE,
                real_end_date       DATE,
                created_at          TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS alpha_signals (
                symbol      TEXT NOT NULL,
                signal_date DATE NOT NULL,
                alpha_id    TEXT NOT NULL,
                raw_value   FLOAT,
                ranked_value FLOAT,
                ic_trailing_20d FLOAT,
                PRIMARY KEY (symbol, signal_date, alpha_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS foreign_flow (
                symbol          TEXT NOT NULL,
                trade_date      DATE NOT NULL,
                buy_volume      BIGINT DEFAULT 0,
                sell_volume     BIGINT DEFAULT 0,
                buy_value       FLOAT DEFAULT 0,
                sell_value      FLOAT DEFAULT 0,
                net_volume      BIGINT DEFAULT 0,
                net_value       FLOAT DEFAULT 0,
                room_remaining  BIGINT DEFAULT 0,
                room_limit      BIGINT DEFAULT 0,
                ownership_pct   FLOAT DEFAULT 0,
                source          TEXT DEFAULT 'cafef',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (symbol, trade_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS news_events (
                id              SERIAL PRIMARY KEY,
                symbol          TEXT NOT NULL,
                published_date  TIMESTAMPTZ NOT NULL,
                title           TEXT NOT NULL,
                url             TEXT,
                source          TEXT DEFAULT 'cafef',
                config_id       INT DEFAULT 0,
                sentiment_score FLOAT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (symbol, url)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_events_symbol_date
            ON news_events(symbol, published_date DESC)
        """)
        cur.execute("ALTER TABLE news_events ADD COLUMN IF NOT EXISTS article_content TEXT")
        cur.execute("ALTER TABLE news_events ADD COLUMN IF NOT EXISTS article_pdf_text TEXT")
        cur.execute("ALTER TABLE news_events ADD COLUMN IF NOT EXISTS article_images TEXT[]")
        cur.execute("ALTER TABLE news_events ADD COLUMN IF NOT EXISTS article_pdf_urls TEXT[]")
        cur.execute("ALTER TABLE news_events ADD COLUMN IF NOT EXISTS content_fetched_at TIMESTAMPTZ")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS corporate_actions (
                symbol       TEXT NOT NULL,
                action_date  DATE NOT NULL,
                action_type  TEXT NOT NULL,
                value        FLOAT,
                ratio        FLOAT,
                currency     TEXT DEFAULT 'VND',
                source       TEXT DEFAULT 'vnstock',
                record_date  DATE,
                note         TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (symbol, action_date, action_type)
            )
        """)

        for col in ["record_date DATE", "note TEXT"]:
            try:
                cur.execute(f"ALTER TABLE corporate_actions ADD COLUMN IF NOT EXISTS {col}")
            except Exception:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS risk_assessments (
                id               SERIAL PRIMARY KEY,
                symbol           TEXT NOT NULL,
                assessment_date  DATE NOT NULL,
                sector           TEXT,
                crs_score        FLOAT,
                risk_level       TEXT,
                hard_blocked     BOOLEAN DEFAULT FALSE,
                soft_blocked     BOOLEAN DEFAULT FALSE,
                recommendation   TEXT,
                score_quant      FLOAT,
                score_fundamental FLOAT,
                score_market_vn  FLOAT,
                score_macro_vn   FLOAT,
                score_global     FLOAT,
                score_regulatory FLOAT,
                score_behavioral FLOAT,
                hard_flags       TEXT[],
                soft_flags       TEXT[],
                all_flags        TEXT[],
                detail           JSONB,
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (symbol, assessment_date)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_risk_date
            ON risk_assessments(assessment_date DESC)
        """)

        # ── TASK-102: Corporate Action Adjustment Engine ──────────────
        cur.execute("""
            ALTER TABLE corporate_actions
            ADD COLUMN IF NOT EXISTS applied BOOLEAN DEFAULT FALSE
        """)
        cur.execute("""
            ALTER TABLE corporate_actions
            ADD COLUMN IF NOT EXISTS adjustment_factor DOUBLE PRECISION
        """)
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
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_data_daily_date
            ON market_data_daily (date DESC)
        """)
