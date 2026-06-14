"""Instrument Master Service

Provides a small service to manage instrument master metadata required for
survivorship-free universe construction and corporate action adjustments.

This module creates a simple `instrument_master` table (if not exists) and
exposes helper functions to upsert instruments and query instrument state as-of a date.

Note: This is intentionally lightweight and uses psycopg2 sync calls. In a
production system you should migrate to migrations, connection pooling and
structured DA layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS instrument_master (
    symbol TEXT PRIMARY KEY,
    isin TEXT,
    name TEXT,
    first_listed DATE,
    delist_date DATE,
    free_float numeric,
    shares_outstanding numeric,
    metadata jsonb,
    corporate_actions jsonb,
    updated_at timestamptz DEFAULT now()
);
"""


@dataclass
class Instrument:
    symbol: str
    isin: Optional[str] = None
    name: Optional[str] = None
    first_listed: Optional[date] = None
    delist_date: Optional[date] = None
    free_float: Optional[float] = None
    shares_outstanding: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    corporate_actions: Optional[Dict[str, Any]] = None


class InstrumentService:
    def __init__(self, db_url: str = DB_URL):
        self.db_url = db_url
        self._ensure_table()

    def _get_conn(self):
        return psycopg2.connect(self.db_url)

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(CREATE_SQL)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.exception("Failed to ensure instrument_master table: %s", e)

    def upsert_instrument(self, instrument: Instrument) -> None:
        """Insert or update instrument metadata."""
        sql = """
        INSERT INTO instrument_master (symbol, isin, name, first_listed, delist_date, free_float, shares_outstanding, metadata, corporate_actions, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
        ON CONFLICT (symbol) DO UPDATE SET
          isin = EXCLUDED.isin,
          name = EXCLUDED.name,
          first_listed = EXCLUDED.first_listed,
          delist_date = EXCLUDED.delist_date,
          free_float = EXCLUDED.free_float,
          shares_outstanding = EXCLUDED.shares_outstanding,
          metadata = COALESCE(instrument_master.metadata, '{}'::jsonb) || EXCLUDED.metadata,
          corporate_actions = COALESCE(instrument_master.corporate_actions, '{}'::jsonb) || EXCLUDED.corporate_actions,
          updated_at = now();
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                sql,
                (
                    instrument.symbol,
                    instrument.isin,
                    instrument.name,
                    instrument.first_listed,
                    instrument.delist_date,
                    instrument.free_float,
                    instrument.shares_outstanding,
                    json.dumps(instrument.metadata or {}),
                    json.dumps(instrument.corporate_actions or {}),
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.exception("Failed upsert instrument %s: %s", instrument.symbol, e)

    def get_instrument_as_of(self, symbol: str, as_of: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Return instrument row if exists and active as_of date (or current date)."""
        as_of = as_of or datetime.utcnow().date()
        sql = """
        SELECT symbol, isin, name, first_listed, delist_date, free_float, shares_outstanding, metadata, corporate_actions
        FROM instrument_master
        WHERE symbol = %s
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, (symbol,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                return None
            # if delist_date exists and <= as_of then consider not active
            dd = row.get("delist_date")
            if dd and dd <= as_of:
                return None
            return dict(row)
        except Exception as e:
            logger.exception("Failed get instrument %s: %s", symbol, e)
            return None


instrument_svc = InstrumentService()
