"""Insider Trading Scraper for Vietnamese equities.

Data sources (in priority order):
  1. vnstock API (shareholders, officers, ownership breakdown)
  2. CafeF news search for insider transaction announcements
  3. HOSE/HNX public disclosure pages (fallback)

Output: standardized insider trading records.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))


def _get_vnstock_shareholders(symbol: str) -> List[Dict[str, Any]]:
    """Get major shareholders from vnstock."""
    try:
        from vnstock.api.company import Company
        c = Company(symbol=symbol, source="KBS")
        data = c.shareholders(symbol=symbol)
        if data is not None and not data.empty:
            records = []
            for _, row in data.iterrows():
                records.append({
                    "type": "MAJOR_SHAREHOLDER",
                    "name": row.get("name", ""),
                    "sharesOwned": int(row.get("shares_owned", 0) or 0),
                    "ownershipPct": float(row.get("ownership_percentage", 0) or 0),
                    "updateDate": str(row.get("update_date", ""))[:10],
                    "source": "vnstock",
                })
            return records
    except Exception as e:
        logger.debug("vnstock shareholders failed for %s: %s", symbol, e)
    return []


def _get_vnstock_officers(symbol: str) -> List[Dict[str, Any]]:
    """Get board of directors / key officers from vnstock."""
    try:
        from vnstock.api.company import Company
        c = Company(symbol=symbol, source="KBS")
        data = c.officers(symbol=symbol)
        if data is not None and not data.empty:
            records = []
            for _, row in data.iterrows():
                records.append({
                    "type": "OFFICER",
                    "name": row.get("name", ""),
                    "position": row.get("position_en", row.get("position", "")),
                    "fromDate": str(row.get("from_date", ""))[:10] if row.get("from_date") else "",
                    "source": "vnstock",
                })
            return records
    except Exception as e:
        logger.debug("vnstock officers failed for %s: %s", symbol, e)
    return []


def _get_vnstock_ownership(symbol: str) -> List[Dict[str, Any]]:
    """Get ownership breakdown (state, institutional, insider, retail)."""
    try:
        from vnstock.api.company import Company
        c = Company(symbol=symbol, source="KBS")
        data = c.ownership(symbol=symbol)
        if data is not None and not data.empty:
            records = []
            for _, row in data.iterrows():
                records.append({
                    "type": "OWNERSHIP",
                    "ownerType": row.get("owner_type", ""),
                    "ownershipPct": float(row.get("ownership_percentage", 0) or 0),
                    "sharesOwned": int(row.get("shares_owned", 0) or 0),
                    "updateDate": str(row.get("update_date", ""))[:10],
                    "source": "vnstock",
                })
            return records
    except Exception as e:
        logger.debug("vnstock ownership failed for %s: %s", symbol, e)
    return []


async def _search_cafef_insider_news(symbol: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Search CafeF / knowledge_documents for insider transaction announcements."""
    from app.infrastructure.database.pg_pool import get_cursor

    results: List[Dict[str, Any]] = []
    try:
        with get_cursor() as cur:
            cur.execute(
                """SELECT title, article_content, url, published_date
                   FROM knowledge_documents
                   WHERE symbol = %s
                     AND (title ILIKE '%%bán%%' OR title ILIKE '%%mua%%' OR title ILIKE '%%thoái vốn%%' OR title ILIKE '%%nội bộ%%' OR title ILIKE '%%cổ đông lớn%%')
                   ORDER BY published_date DESC
                   LIMIT %s""",
                (symbol.upper(), max_results),
            )
            for row in cur.fetchall():
                results.append({
                    "type": "INSIDER_NEWS",
                    "title": row[0],
                    "content": (row[1] or "")[:500],
                    "url": row[2],
                    "publishedDate": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                    "source": "knowledge_documents",
                })
    except Exception as e:
        logger.debug("insider news query failed for %s: %s", symbol, e)

    return results[:max_results]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_insider_data(symbol: str) -> Dict[str, Any]:
    """Get all available insider data for a symbol from all sources.

    Returns:
        Dict with:
          - symbol
          - shareholders: list of major shareholders
          - officers: board of directors / key management
          - ownership: ownership breakdown
          - news: recent insider-related news
          - summary: text summary
    """
    symbol = symbol.strip().upper()

    shareholders = _get_vnstock_shareholders(symbol)
    officers = _get_vnstock_officers(symbol)
    ownership = _get_vnstock_ownership(symbol)
    news = await _search_cafef_insider_news(symbol)

    # Build summary
    parts: List[str] = []
    if shareholders:
        top = sorted(shareholders, key=lambda x: x.get("ownershipPct", 0), reverse=True)[:3]
        for s in top:
            parts.append(f"Major shareholder {s['name']}: {s['ownershipPct']:.1f}%")
    if officers:
        top_positions = [o for o in officers if o.get("position") in ("CEO", "Chairman", "CTHĐQT", "TGĐ")]
        for o in top_positions:
            parts.append(f"{o['position']}: {o['name']}")
    if ownership:
        for o in ownership:
            parts.append(f"{o['ownerType']}: {o['ownershipPct']:.1f}%")
    if news:
        parts.append(f"{len(news)} recent insider-related news articles")

    summary = "; ".join(parts) if parts else f"No insider data available for {symbol}."

    return {
        "symbol": symbol,
        "shareholders": shareholders,
        "officers": officers,
        "ownership": ownership,
        "news": news,
        "totalRecords": len(shareholders) + len(officers) + len(ownership) + len(news),
        "summary": summary,
    }
