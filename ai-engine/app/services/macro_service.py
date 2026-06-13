"""Macro Indicators Service — fetch, persist, and retrieve from PostgreSQL.

Replaces on-demand computation in data_enricher.py.get_macro_indicators().
ETL writes daily snapshot; app reads from DB with 24h TTL fallback.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
TZ_UTC = timezone.utc

# ── In-memory read cache (24h TTL, cleared on explicit write) ──────────
_read_cache: Dict[str, Any] = {}
_read_cache_ts: Optional[datetime] = None
_CACHE_TTL = timedelta(hours=24)


def _get_db_url() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")


@contextmanager
def _get_cursor():
    import psycopg2
    conn = psycopg2.connect(_get_db_url())
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ── Public API ──────────────────────────────────────────────────────────

def get_latest_macro(
    indicator_names: Optional[List[str]] = None,
    max_age_days: int = 7,
    refetch_if_stale: bool = True,
) -> Dict[str, Any]:
    """Fetch most recent macro_indicators from DB, with fallback fetch.

    Returns flat dict: {indicator_name: value, ...}
    Falls back to on-demand fetch if:
      - No DB rows exist for requested indicators
      - Newest row is older than *max_age_days*
    """
    global _read_cache, _read_cache_ts

    now = datetime.now(TZ_UTC)

    # Return cached result if still fresh
    if _read_cache and _read_cache_ts and (now - _read_cache_ts) < _CACHE_TTL:
        if indicator_names:
            return {k: v for k, v in _read_cache.items() if k in indicator_names}
        return dict(_read_cache)

    # Read from DB
    try:
        db_rows = _fetch_latest_from_db(indicator_names, max_age_days)
    except Exception:
        logger.warning("macro_service: DB read failed, fallback to on-demand fetch")
        db_rows = {}

    stale = False
    if db_rows:
        newest_ts = max(
            (v for k, v in db_rows.items() if k.endswith("_fetched_at")),
            default=None,
        )
        if newest_ts and (now - newest_ts).days > max_age_days:
            stale = True

    if not db_rows or (stale and refetch_if_stale):
        logger.info("macro_service: fetching fresh macro data from sources...")
        fresh = _fetch_all_macro()
        _persist_macro(fresh)
        _read_cache = fresh
        _read_cache_ts = now
        if indicator_names:
            return {k: v for k, v in fresh.items() if k in indicator_names}
        return dict(fresh)

    _read_cache = db_rows
    _read_cache_ts = now
    if indicator_names:
        return {k: v for k, v in db_rows.items() if k in indicator_names}
    return dict(db_rows)


def refresh_macro() -> Dict[str, Any]:
    """Force-refresh macro data from sources and persist to DB."""
    global _read_cache, _read_cache_ts
    fresh = _fetch_all_macro()
    _persist_macro(fresh)
    _read_cache = fresh
    _read_cache_ts = datetime.now(TZ_UTC)
    return dict(fresh)


def get_macro_history(
    indicator_name: str,
    days: int = 365,
) -> List[Dict[str, Any]]:
    """Get historical values for a single indicator."""
    cutoff = (datetime.now(TZ_UTC) - timedelta(days=days)).date()
    try:
        with _get_cursor() as cur:
            cur.execute(
                """SELECT indicator_date, value, unit, source
                   FROM macro_indicators
                   WHERE indicator_name = %s AND indicator_date >= %s
                   ORDER BY indicator_date""",
                (indicator_name, cutoff),
            )
            return [
                {
                    "date": str(r[0]),
                    "value": float(r[1]),
                    "unit": r[2],
                    "source": r[3],
                }
                for r in cur.fetchall()
            ]
    except Exception as e:
        logger.warning("macro_service: history read failed for %s: %s", indicator_name, e)
        return []


# ── Internal: DB read ────────────────────────────────────────────────────

def _fetch_latest_from_db(
    indicator_names: Optional[List[str]] = None,
    max_age_days: int = 7,
) -> Dict[str, Any]:
    """Get latest value per indicator from macro_indicators table."""
    import psycopg2.extras

    with _get_cursor() as cur:
        if indicator_names:
            placeholders = ",".join("%s" for _ in indicator_names)
            cur.execute(
                f"""SELECT DISTINCT ON (indicator_name)
                        indicator_name, value, unit, source, created_at
                    FROM macro_indicators
                    WHERE indicator_name IN ({placeholders})
                    ORDER BY indicator_name, indicator_date DESC""",
                indicator_names,
            )
        else:
            cur.execute(
                """SELECT DISTINCT ON (indicator_name)
                        indicator_name, value, unit, source, created_at
                    FROM macro_indicators
                    ORDER BY indicator_name, indicator_date DESC"""
            )
        rows = cur.fetchall()

    result: Dict[str, Any] = {}
    for r in rows:
        name = r[0]
        result[name] = float(r[1])
        result[f"{name}_unit"] = r[2] or ""
        result[f"{name}_source"] = r[3] or ""
        result[f"{name}_fetched_at"] = r[4] if isinstance(r[4], datetime) else datetime.now(TZ_UTC)
    return result


# ── Internal: Fetch from public APIs ─────────────────────────────────────

def _fetch_all_macro() -> Dict[str, Any]:
    """Fetch all macro indicators from public APIs.

    Returns flat dict like {indicator_name: value, ...}
    with _unit and _source suffixes for metadata.
    """
    res: Dict[str, Any] = {}

    # ── 1. yfinance global ──────────────────────────────────────────
    _fetch_yfinance(res)

    # ── 2. VietFin VNINDEX returns ──────────────────────────────────
    _fetch_vnindex_returns(res)

    # ── 3. CPI from vi.money ────────────────────────────────────────
    _fetch_cpi(res)

    # ── 4. SBV policy rates ─────────────────────────────────────────
    _fetch_sbv_rates(res)

    # ── 5. CafeF deposit rates (replaces hardcode) ───────────────────
    _fetch_cafef_deposit_rates(res)

    # ── 6. Vimo MCP lending rates (optional) ─────────────────────────
    _fetch_vimo_lending(res)

    # ── Fill fallbacks for any missing ───────────────────────────────
    _apply_fallbacks(res)

    return res


def _fetch_yfinance(res: Dict[str, Any]):
    tickers = {
        "oil_price_brent": "BZ=F",
        "usd_index": "DX-Y.NYB",
        "usd_10y_yield": "^TNX",
        "vix": "^VIX",
        "usd_vnd_exchange": "VND=X",
    }
    for k, ticker in tickers.items():
        try:
            obj = yf.Ticker(ticker)
            hist = obj.history(period="5d")
            if not hist.empty:
                res[k] = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    try:
        gold = yf.Ticker("GC=F")
        gold_hist = gold.history(period="5d")
        if not gold_hist.empty:
            usd_vnd = res.get("usd_vnd_exchange", 25450.0)
            gold_usd = float(gold_hist["Close"].iloc[-1])
            res["gold_price_vnd"] = gold_usd * usd_vnd * 1.21528
    except Exception:
        pass


def _fetch_vnindex_returns(res: Dict[str, Any]):
    try:
        from vietfin import vf

        end = datetime.now()
        start = end - timedelta(days=400)
        r = vf.index.price.historical(
            symbol="vnindex",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            interval="1d",
            provider="dnse",
        )
        vni_hist = r.to_df()
        if vni_hist is not None and not vni_hist.empty:
            closes = vni_hist["close"].values
            if len(closes) >= 2:
                res["vnindex_return_1d"] = round((closes[-1] / closes[-2] - 1) * 100, 2)
            if len(closes) >= 22:
                res["vnindex_return_1m"] = round((closes[-1] / closes[-22] - 1) * 100, 2)
            if len(closes) >= 66:
                res["vnindex_return_3m"] = round((closes[-1] / closes[-66] - 1) * 100, 2)
            if len(closes) >= 252:
                res["vnindex_return_1y"] = round((closes[-1] / closes[-252] - 1) * 100, 2)
            res["vnindex_last_close"] = float(closes[-1])
    except Exception:
        pass


def _fetch_cpi(res: Dict[str, Any]):
    try:
        resp = httpx.get(
            "https://vi.money/api/v1/data/cpi",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                latest = data[-1]
                res["cpi"] = float(latest.get("yoyChangePct", 3.45))
                res["cpi_headline_index"] = float(latest.get("headlineIndex", 0))
                res["cpi_mom_pct"] = float(latest.get("momChangePct", 0))
                res["cpi_year"] = int(latest.get("year", 0))
                res["cpi_month"] = int(latest.get("month", 0))
    except Exception:
        pass


def _fetch_sbv_rates(res: Dict[str, Any]):
    try:
        resp = httpx.get(
            "https://sbv.gov.vn/vi/l%C3%A3i-su%E1%BA%A5t1",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            },
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code == 200:
            html = resp.text
            refi = re.search(
                r"Lãi\s*suất\s*tái\s*cấp\s*vốn\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*%",
                html, re.IGNORECASE | re.DOTALL,
            )
            if refi:
                res["refinancing_rate"] = float(refi.group(1).replace(",", "."))
            disc = re.search(
                r"Lãi\s*suất\s*tái\s*chiết\s*khấu\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*%",
                html, re.IGNORECASE | re.DOTALL,
            )
            if disc:
                res["discount_rate"] = float(disc.group(1).replace(",", "."))
    except Exception:
        pass


def _fetch_cafef_deposit_rates(res: Dict[str, Any]):
    """Scrape CafeF bank interest rate table for deposit rates.

    Falls back silently — rates change slowly so hardcode fallback is OK.
    """
    try:
        resp = httpx.get(
            "https://cafef.vn/du-lieu/lai-suat-ngan-hang.chn",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            follow_redirects=True,
            timeout=15,
        )
        if resp.status_code != 200:
            return

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for the deposit rate table — usually a <table> with rate data
        # CafeF structure: each row is a bank, columns are terms (1M, 3M, 6M, 12M, etc.)
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 6:
                    header_text = row.get_text(" ", strip=True).lower()
                    if "kỳ hạn" in header_text or "ky han" in header_text:
                        continue
                    # Try to extract Big4 average rates from first data row
                    texts = [c.get_text(strip=True) for c in cells]
                    # Look for rows containing known bank names
                    row_text = " ".join(texts).lower()
                    if any(b in row_text for b in ["agribank", "vietcombank", "bidv", "vietinbank"]):
                        continue  # Skip header-like rows
        # If we got here without structured data, try regex from the article text
        # Pattern: "kỳ hạn X tháng là Y%/năm"
        import re
        patterns = {
            "deposit_1m":  r"kỳ\s*hạn\s*1\s*tháng[^0-9]*([\d,]+)\s*%",
            "deposit_3m":  r"kỳ\s*hạn\s*3\s*tháng[^0-9]*([\d,]+)\s*%",
            "deposit_6m":  r"kỳ\s*hạn\s*6\s*tháng[^0-9]*([\d,]+)\s*%",
            "deposit_12m": r"kỳ\s*hạn\s*12\s*tháng[^0-9]*([\d,]+)\s*%",
        }
        for key, pat in patterns.items():
            m = re.search(pat, resp.text, re.IGNORECASE | re.DOTALL)
            if m:
                res[key] = float(m.group(1).replace(",", "."))
    except Exception:
        pass


def _fetch_vimo_lending(res: Dict[str, Any]):
    vimo_key = os.environ.get("VIMO_API_KEY", "")
    if not vimo_key:
        return
    try:
        resp = httpx.get(
            "https://vimo.cuthongthai.vn/api/finance/macro-data",
            headers={"x-api-key": vimo_key, "User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "lending_rate_12m_big4" in data:
                res["lending_rate_12m_big4"] = float(data["lending_rate_12m_big4"])
            if "lending_rate_12m_commercial" in data:
                res["lending_rate_12m_commercial"] = float(data["lending_rate_12m_commercial"])
    except Exception:
        pass


_FALLBACKS: Dict[str, Any] = {
    "oil_price_brent": 78.5,
    "usd_index": 104.2,
    "usd_10y_yield": 4.25,
    "vix": 14.2,
    "usd_vnd_exchange": 25450.0,
    "refinancing_rate": 4.5,
    "discount_rate": 3.0,
    "lending_rate_12m_big4": 9.9,
    "lending_rate_12m_commercial": 12.4,
    "cpi": 3.45,
    "gold_price_vnd": 85_000_000,
    "vnindex_return_1d": 0.45,
    "vnindex_return_1m": 2.34,
    "vnindex_return_3m": 5.67,
    "vnindex_return_1y": 12.50,
    "vnindex_last_close": 1300.0,
    "interest_rate_cod": 6.80,
    "interest_rate_on": 4.75,
    "interest_rate_1w": 4.75,
    "interest_rate_1m": 4.75,
    "interest_rate_3m": 4.75,
    "interest_rate_6m": 6.60,
    "interest_rate_1y": 6.80,
    "ppi": 2.1,
    "gdp_growth": 6.2,
    "inflation_rate": 3.2,
    "unemployment_rate": 2.3,
}

_UNITS: Dict[str, str] = {
    "oil_price_brent": "USD/bbl",
    "usd_index": "index",
    "usd_10y_yield": "%",
    "vix": "index",
    "usd_vnd_exchange": "VND/USD",
    "refinancing_rate": "%",
    "discount_rate": "%",
    "lending_rate_12m_big4": "%",
    "lending_rate_12m_commercial": "%",
    "cpi": "% yoy",
    "cpi_headline_index": "index",
    "cpi_mom_pct": "% mom",
    "gold_price_vnd": "VND/tael",
    "vnindex_return_1d": "%",
    "vnindex_return_1m": "%",
    "vnindex_return_3m": "%",
    "vnindex_return_1y": "%",
    "vnindex_last_close": "index",
    "ppi": "%",
    "gdp_growth": "%",
    "inflation_rate": "%",
    "unemployment_rate": "%",
}

_SOURCES: Dict[str, str] = {
    "oil_price_brent": "yfinance",
    "usd_index": "yfinance",
    "usd_10y_yield": "yfinance",
    "vix": "yfinance",
    "usd_vnd_exchange": "yfinance",
    "gold_price_vnd": "yfinance",
    "cpi": "vi.money (GSO)",
    "cpi_headline_index": "vi.money (GSO)",
    "cpi_mom_pct": "vi.money (GSO)",
    "cpi_year": "vi.money (GSO)",
    "cpi_month": "vi.money (GSO)",
    "refinancing_rate": "SBV web",
    "discount_rate": "SBV web",
    "lending_rate_12m_big4": "Vimo MCP",
    "lending_rate_12m_commercial": "Vimo MCP",
    "vnindex_return_1d": "VietFin (DNSE)",
    "vnindex_return_1m": "VietFin (DNSE)",
    "vnindex_return_3m": "VietFin (DNSE)",
    "vnindex_return_1y": "VietFin (DNSE)",
    "vnindex_last_close": "VietFin (DNSE)",
}

_READ_ONLY_KEYS = {"cpi_year", "cpi_month"}


def _apply_fallbacks(res: Dict[str, Any]):
    for k, default in _FALLBACKS.items():
        if k not in res:
            res[k] = default


# ── Internal: Persist to DB ──────────────────────────────────────────────

def _persist_macro(data: Dict[str, Any]):
    """Upsert all scalar indicators into macro_indicators table.

    Non-scalar keys (ending in _fetched_at, _unit, _source) are skipped.
    """
    today = date.today()
    rows: List[Tuple[date, str, float, str, str]] = []
    for k, v in data.items():
        if k.endswith("_fetched_at") or k.endswith("_unit") or k.endswith("_source"):
            continue
        if k in _READ_ONLY_KEYS:
            continue
        if not isinstance(v, (int, float)):
            continue
        unit = _UNITS.get(k, "")
        source = _SOURCES.get(k, "fallback")
        rows.append((today, k, float(v), unit, source))

    if not rows:
        return

    try:
        with _get_cursor() as cur:
            cur.executemany(
                """INSERT INTO macro_indicators (indicator_date, indicator_name, value, unit, source)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (indicator_date, indicator_name)
                   DO UPDATE SET value = EXCLUDED.value,
                                 unit = EXCLUDED.unit,
                                 source = EXCLUDED.source,
                                 created_at = NOW()""",
                rows,
            )
        logger.info("macro_service: persisted %d indicators for %s", len(rows), today)
    except Exception as e:
        logger.warning("macro_service: persist failed: %s", e)


# ── Clear cache (call after manual refresh) ──────────────────────────────

def clear_cache():
    global _read_cache, _read_cache_ts
    _read_cache = {}
    _read_cache_ts = None
