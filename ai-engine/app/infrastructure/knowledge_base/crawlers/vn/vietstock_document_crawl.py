"""Fetch shareholder documents from Vietstock's document API."""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from app.infrastructure.knowledge_base.crawlers.vn.news_repo import upsert_article
from app.infrastructure.database.pg_pool import get_cursor

BASE = "https://finance.vietstock.vn"
DOCUMENT_TYPES = {
    1: "financial_statement",
    2: "annual_report",
    4: "agm_resolution",
    9: "governance_report",
}
DOCUMENT_SOURCE = "vnstock_docs"


def load_symbols(exchange: str | None = None) -> list[str]:
    from app.infrastructure.database.pg_pool import get_cursor

    with get_cursor() as cur:
        if exchange:
            cur.execute("SELECT symbol FROM stocks WHERE exchange = %s ORDER BY symbol", (exchange,))
        else:
            cur.execute("SELECT symbol FROM stocks ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


def _request(opener, url: str, data: bytes | None = None) -> str:
    req = Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": f"{BASE}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        method="POST" if data is not None else "GET",
    )
    with opener.open(req, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def _normalize_pdf_url(url: str) -> str:
    parts = urlsplit(url.replace("http://", "https://", 1).strip())
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"), parts.query, parts.fragment))


def _token(opener, symbol: str, document_type: int) -> str:
    html = _request(opener, f"{BASE}/{symbol}/tai-tai-lieu.htm?doctype={document_type}")
    token_match = re.search(
        r"name=[\"']?__RequestVerificationToken[\"']?[^>]+value=[\"']?([^\"' >]+)",
        html,
        re.I,
    )
    if not token_match:
        raise RuntimeError(f"Vietstock anti-forgery token missing for {symbol}")
    return token_match.group(1)


class VietstockSessionManager:
    """Manages a shared, thread-safe ASP.NET session and anti-forgery token.
    
    Instead of fetching an HTML page for every symbol (400 GET requests),
    we fetch the token ONCE at startup and reuse it across all symbols and threads.
    Auto-refreshes if TTL expires or if server rejects the token.
    """
    _lock = threading.Lock()
    _opener = None
    _token = None
    _created_at = 0.0
    _TTL = 7200.0  # 2 hours

    @classmethod
    def get_session(cls, fallback_symbol: str = "HPG") -> tuple:
        now = time.time()
        if cls._opener and cls._token and (now - cls._created_at < cls._TTL):
            return cls._opener, cls._token

        with cls._lock:
            if cls._opener and cls._token and (now - cls._created_at < cls._TTL):
                return cls._opener, cls._token

            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            token = _token(opener, fallback_symbol, 1)
            cls._opener = opener
            cls._token = token
            cls._created_at = now
            return cls._opener, cls._token

    @classmethod
    def invalidate(cls) -> None:
        with cls._lock:
            cls._token = None
            cls._opener = None
            cls._created_at = 0.0


def fetch_documents(
    symbol: str,
    document_type: int,
    page: int = 1,
    *,
    opener=None,
    token: str | None = None,
    retries: int = 2,
) -> list[dict]:
    """Return normalized Vietstock document rows for one symbol/type."""
    symbol = symbol.upper().strip()

    payload = None
    for attempt in range(retries):
        if opener is None or token is None:
            active_opener, active_token = VietstockSessionManager.get_session(fallback_symbol=symbol)
        else:
            active_opener, active_token = opener, token

        body = urlencode(
            {
                "code": symbol,
                "page": page,
                "type": document_type,
                "__RequestVerificationToken": active_token,
            }
        ).encode()

        try:
            raw_response = _request(active_opener, f"{BASE}/data/getdocument", body)
            payload = json.loads(raw_response)
            break
        except Exception:
            VietstockSessionManager.invalidate()
            if attempt == retries - 1:
                raise
            opener = None
            token = None

    result = []
    for row in payload or []:
        file_id = row.get("FileInfoID")
        url = _normalize_pdf_url(str(row.get("Url") or ""))
        if not file_id or not url:
            continue
        title = str(row.get("Title") or row.get("FullName") or "").strip()
        millis = re.search(r"-?(\d+)", str(row.get("LastUpdate") or ""))
        published = (
            datetime.fromtimestamp(int(millis.group(1)) / 1000, tz=timezone.utc)
            if millis
            else datetime.now(timezone.utc)
        )
        result.append(
            {
                "symbol": symbol,
                "published_date": published,
                "title": title,
                "url": f"{BASE}/{symbol}/tai-tai-lieu.htm?doctype={document_type}&fileinfoid={file_id}",
                "source": DOCUMENT_SOURCE,
                "doc_type": DOCUMENT_TYPES.get(document_type, "other"),
                "article_pdf_urls": [url],
            }
        )
    return result


def refresh_documents(
    symbol: str,
    document_types: tuple[int, ...] = (1, 2, 4, 9),
    max_years: int = 3,
    opener=None,
    token: str | None = None,
) -> int:
    """Upsert document metadata; PDF contents are fetched later by SAG."""
    total = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * max_years)
    if opener is None or token is None:
        opener, token = VietstockSessionManager.get_session(fallback_symbol=symbol)

    for document_type in document_types:
        page = 1
        while True:
            rows = fetch_documents(symbol, document_type, page=page, opener=opener, token=token)
            if not rows:
                break
            for article in rows:
                if article["published_date"] < cutoff:
                    continue
                total += int(upsert_article(article, source=DOCUMENT_SOURCE))
                with get_cursor() as cur:
                    cur.execute(
                        """UPDATE knowledge_documents
                           SET source = %s, article_pdf_urls = %s
                           WHERE symbol = %s AND url = %s""",
                        (DOCUMENT_SOURCE, article["article_pdf_urls"], article["symbol"], article["url"]),
                    )
            # Vietstock is newest-first in practice. Stop once the whole page
            # is older than the requested window; avoid crawling deep history.
            if max(article["published_date"] for article in rows) < cutoff:
                break
            page += 1
    return total


def run(
    types: list[int] | None = None,
    symbols: list[str] | None = None,
    exchange: str | None = None,
    max_years: int = 5,
) -> dict:
    """Refresh Vietstock document metadata for the requested universe."""
    selected = [s.upper().strip() for s in (symbols or load_symbols(exchange)) if s]
    selected_types = tuple(types or (1, 2, 4, 9))
    inserted = 0
    new_symbols = []

    # Warm up shared session once before launching thread pool
    if selected:
        VietstockSessionManager.get_session(fallback_symbol=selected[0])

    # ponytail: bounded workers; enough throughput without hammering Vietstock.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(
            lambda symbol: (symbol, refresh_documents(symbol, selected_types, max_years=max_years)),
            selected,
        )
        for symbol, count in results:
            inserted += count
            if count:
                new_symbols.append(symbol)
    return {
        "status": "success",
        "source": DOCUMENT_SOURCE,
        "symbols": len(selected),
        "types": list(selected_types),
        "inserted": inserted,
        "new_symbols": new_symbols,
        "total": len(selected),
        "max_years": max_years,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    args = parser.parse_args()
    print(refresh_documents(args.symbol))
