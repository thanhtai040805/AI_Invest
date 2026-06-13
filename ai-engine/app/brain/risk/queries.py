"""Query helpers — CRS assessment data access for agents & routers."""
import logging
from typing import Optional

import psycopg2

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)


def get_active_flags(symbol: str, cur=None) -> list[dict]:
    """Get active risk flags from both risk_flags and risk_assessments tables.

    Merges:
      - Hard flags from risk_flags (CANH_BAO_TC, CHAM_BAO_TC, DEBT_DANGER, etc.)
      - Soft flags from risk_flags (FLOOR_TRAP, SHARP_DROP, etc.)
      - CRS layer names from risk_assessments.all_flags

    Returns list of dicts with flag_type, effective_date, description, source.
    """
    own_conn = False
    if cur is None:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        own_conn = True
    try:
        flags = []

        # 1. risk_flags table — real HARD/SOFT flags
        cur.execute(
            """SELECT flag_type, effective_date, description
               FROM risk_flags
               WHERE symbol = %s AND is_active = TRUE
               ORDER BY effective_date DESC NULLS LAST""",
            (symbol,),
        )
        for row in cur.fetchall():
            flags.append({
                "flag_type": row[0],
                "effective_date": str(row[1]) if row[1] else "",
                "description": row[2] or "",
                "source": "risk_flags",
            })

        # 2. risk_assessments table — CRS layer names + hard/soft flags
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

            # Add CRS-specific flags
            added = {f["flag_type"] for f in flags}
            for f in all_flags:
                if f not in added:
                    flags.append({
                        "flag_type": f,
                        "effective_date": "",
                        "description": f"CRS layer flag, risk_level={risk_level}, CRS={crs_score}",
                        "source": "risk_assessments",
                    })
                    added.add(f)
            for f in hard_flags:
                if f not in added:
                    flags.append({
                        "flag_type": f,
                        "effective_date": "",
                        "description": f"Hard flag from CRS, risk_level={risk_level}",
                        "source": "risk_assessments",
                    })
                    added.add(f)
            for f in soft_flags:
                if f not in added:
                    flags.append({
                        "flag_type": f,
                        "effective_date": "",
                        "description": f"Soft flag from CRS, risk_level={risk_level}",
                        "source": "risk_assessments",
                    })
                    added.add(f)

            # Add a synthetic CRS flag if hard_blocked
            if hard_blocked and "CRS_HARD_BLOCK" not in added:
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
