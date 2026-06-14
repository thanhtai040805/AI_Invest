"""Query helpers — CRS 7-layer risk assessment data for agents & routers."""
import logging
from typing import Optional

import psycopg2

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)


def get_active_flags(symbol: str, cur=None) -> list[dict]:
    """Get CRS 7-layer risk flags for a symbol from risk_assessments.

    Returns list of dicts with flag_type, risk_level, crs_score, source.
    """
    own_conn = False
    if cur is None:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        own_conn = True
    try:
        flags = []

        cur.execute(
            """SELECT all_flags, hard_flags, soft_flags, risk_level, crs_score, hard_blocked
               FROM risk_assessments
               WHERE symbol = %s
               ORDER BY assessment_date DESC
               LIMIT 1""",
            (symbol,),
        )
        row = cur.fetchone()
        if row:
            all_flags = row[0] or []
            hard_flags = row[1] or []
            soft_flags = row[2] or []
            risk_level = row[3]
            crs_score = row[4]
            hard_blocked = row[5]

            for f in all_flags:
                flags.append({
                    "flag_type": f,
                    "effective_date": "",
                    "description": f"CRS layer flag, risk_level={risk_level}, CRS={crs_score}",
                    "source": "risk_assessments",
                })
            for f in hard_flags:
                flags.append({
                    "flag_type": f,
                    "effective_date": "",
                    "description": f"Hard flag from CRS, risk_level={risk_level}",
                    "source": "risk_assessments",
                })
            for f in soft_flags:
                flags.append({
                    "flag_type": f,
                    "effective_date": "",
                    "description": f"Soft flag from CRS, risk_level={risk_level}",
                    "source": "risk_assessments",
                })

            if hard_blocked and "CRS_HARD_BLOCK" not in {f["flag_type"] for f in flags}:
                flags.append({
                    "flag_type": "CRS_HARD_BLOCK",
                    "effective_date": "",
                    "description": f"Hard blocked by CRS, risk_level={risk_level}",
                    "source": "risk_assessments",
                })

        return flags
    finally:
        if own_conn:
            cur.close()
            conn.close()


def get_hard_blocked(symbol: str) -> bool:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT hard_blocked FROM risk_assessments
               WHERE symbol = %s
               ORDER BY assessment_date DESC
               LIMIT 1""",
            (symbol,),
        )
        row = cur.fetchone()
        return bool(row[0]) if row else False
    finally:
        cur.close()
        conn.close()


def get_soft_flag_count(symbol: str) -> int:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT soft_blocked FROM risk_assessments
               WHERE symbol = %s
               ORDER BY assessment_date DESC
               LIMIT 1""",
            (symbol,),
        )
        row = cur.fetchone()
        if row and row[0]:
            cur.execute(
                """SELECT cardinality(soft_flags) FROM risk_assessments
                   WHERE symbol = %s
                   ORDER BY assessment_date DESC
                   LIMIT 1""",
                (symbol,),
            )
            cnt = cur.fetchone()
            return cnt[0] if cnt else 0
        return 0
    finally:
        cur.close()
        conn.close()
