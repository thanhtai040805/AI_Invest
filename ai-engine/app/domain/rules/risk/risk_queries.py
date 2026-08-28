"""Risk Queries — Query risk assessments, hard blocks, and warning flags from PostgreSQL."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.infrastructure.database.pg_pool import get_cursor

logger = logging.getLogger(__name__)


def get_latest_risk_assessment(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch the latest CRS risk assessment record for a symbol."""
    if not symbol:
        return None
    sym = symbol.strip().upper()
    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT symbol, assessment_date, crs_score, risk_level,
                          hard_blocked, soft_blocked, recommendation,
                          hard_flags, soft_flags, all_flags, detail
                   FROM risk_assessments
                   WHERE symbol = %s
                   ORDER BY assessment_date DESC
                   LIMIT 1""",
                (sym,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "symbol": row[0],
                "assessment_date": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                "crs_score": float(row[2]) if row[2] is not None else 0.0,
                "risk_level": row[3] or "LOW",
                "hard_blocked": bool(row[4]),
                "soft_blocked": bool(row[5]),
                "recommendation": row[6] or "",
                "hard_flags": row[7] if isinstance(row[7], list) else [],
                "soft_flags": row[8] if isinstance(row[8], list) else [],
                "all_flags": row[9] if isinstance(row[9], list) else [],
                "detail": row[10] if isinstance(row[10], dict) else {},
            }
    except Exception as e:
        logger.warning(f"Error querying risk assessment for {sym}: {e}")
        return None


def get_hard_blocked(symbol: str) -> bool:
    """Return True if the symbol has an active Hard Risk Block (e.g. GIL CATASTROPHIC, audit denial)."""
    assessment = get_latest_risk_assessment(symbol)
    if assessment:
        return bool(assessment.get("hard_blocked", False))
    return False


def get_soft_flag_count(symbol: str) -> int:
    """Return the number of active soft warning flags for a symbol."""
    assessment = get_latest_risk_assessment(symbol)
    if assessment:
        return len(assessment.get("soft_flags", []))
    return 0


def get_active_flags(symbol: str) -> List[str]:
    """Return all active risk flags (both hard and soft) for a symbol."""
    assessment = get_latest_risk_assessment(symbol)
    if assessment:
        flags = assessment.get("all_flags", [])
        if not flags:
            flags = list(set((assessment.get("hard_flags", []) or []) + (assessment.get("soft_flags", []) or [])))
        return flags
    return []


def get_all_risk_assessments(target_date: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    """Fetch risk assessments for all symbols for a given date (or latest)."""
    results: Dict[str, Dict[str, Any]] = {}
    try:
        with get_cursor() as cur:
            if target_date:
                cur.execute(
                    """SELECT symbol, assessment_date, crs_score, risk_level,
                              hard_blocked, soft_blocked, recommendation,
                              hard_flags, soft_flags, all_flags
                       FROM risk_assessments
                       WHERE assessment_date = %s""",
                    (target_date,),
                )
            else:
                cur.execute(
                    """SELECT DISTINCT ON (symbol)
                              symbol, assessment_date, crs_score, risk_level,
                              hard_blocked, soft_blocked, recommendation,
                              hard_flags, soft_flags, all_flags
                       FROM risk_assessments
                       ORDER BY symbol, assessment_date DESC"""
                )
            rows = cur.fetchall()
            for r in rows:
                results[r[0]] = {
                    "symbol": r[0],
                    "assessment_date": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                    "crs_score": float(r[2]) if r[2] is not None else 0.0,
                    "risk_level": r[3] or "LOW",
                    "hard_blocked": bool(r[4]),
                    "soft_blocked": bool(r[5]),
                    "recommendation": r[6] or "",
                    "hard_flags": r[7] if isinstance(r[7], list) else [],
                    "soft_flags": r[8] if isinstance(r[8], list) else [],
                    "all_flags": r[9] if isinstance(r[9], list) else [],
                }
    except Exception as e:
        logger.warning(f"Error querying bulk risk assessments: {e}")
    return results
