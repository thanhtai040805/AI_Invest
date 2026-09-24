"""Crawl only CafeF documents consumed by the BCTC -> SAG pipeline.

Type 1 is financial statements; type 5 is corporate governance.  No news,
annual reports, resolutions, or PDF bodies are downloaded here.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.infrastructure.database.pg_pool import get_cursor
from app.infrastructure.knowledge_base.crawlers.vn.news_repo import upsert_article

API_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx"
SOURCE = "cafef_docs"
TYPE_MAP = {1: "financial_statement", 5: "governance_report"}
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://cafef.vn/",
    "X-Requested-With": "XMLHttpRequest",
}
_VN_TZ = timezone(timedelta(hours=7))


def _not_future(value: datetime) -> bool:
    now = datetime.now(_VN_TZ)
    return 2000 <= value.year <= now.year and value <= now


def _url_available_at(url: str) -> datetime | None:
    """Extract a timestamp embedded in CafeF's uploaded file name, if present."""
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    match = re.search(r"_(\d{14})(?=\.[^.]+$|$)", filename)
    if match:
        value = match.group(1)
        for fmt in ("%d%m%Y%H%M%S", "%Y%m%d%H%M%S"):
            try:
                parsed = datetime.strptime(value, fmt).replace(tzinfo=_VN_TZ)
                if _not_future(parsed):
                    return parsed
            except ValueError:
                pass

    match = re.search(r"[_-](\d{10})(?=\.[^.]+$|$)", filename)
    if match:
        try:
            parsed = datetime.fromtimestamp(int(match.group(1)), timezone.utc).astimezone(_VN_TZ)
            if _not_future(parsed):
                return parsed
        except (ValueError, OSError, OverflowError):
            pass

    match = re.match(r"(20\d{2})(\d{2})(\d{2})[_-]", filename)
    if match:
        try:
            parsed = datetime.strptime("".join(match.groups()), "%Y%m%d").replace(tzinfo=_VN_TZ)
            if _not_future(parsed):
                return parsed
        except ValueError:
            pass
    # Older CafeF filenames embed DDMMYYYY in the report slug instead of
    # appending a timestamp (for example, "...hoa-sen30102024-...").
    matches = re.finditer(r"(\d{2})(\d{2})(20\d{2})(?=\D|$)", filename)
    for match in reversed(list(matches)):
        prefix_digits = re.search(r"(\d*)$", filename[:match.start()]).group(1)
        if len(prefix_digits) > 2:
            continue
        try:
            parsed = datetime.strptime("".join(match.groups()), "%d%m%Y").replace(tzinfo=_VN_TZ)
            if _not_future(parsed):
                return parsed
        except ValueError:
            continue
    return None


def load_symbols(exchange: str | None = None) -> list[str]:
    with get_cursor() as cur:
        if exchange:
            cur.execute("SELECT symbol FROM stocks WHERE exchange=%s ORDER BY symbol", (exchange,))
        else:
            cur.execute("SELECT symbol FROM stocks ORDER BY symbol")
        return [str(row[0]).upper().strip() for row in cur.fetchall()]


def _date(item: dict[str, Any], title: str, url: str) -> datetime | None:
    # Time/Year in CafeF's API identify a reporting period, not a publication date.
    return _url_available_at(url)


async def fetch(client: httpx.AsyncClient, symbol: str, document_type: int) -> list[dict[str, Any]]:
    response = await client.get(
        API_URL,
        params={"Symbol": symbol, "Type": document_type, "Year": 0},
        headers=HEADERS,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("Success"):
        return []
    rows = payload.get("Data") or []
    result = []
    for row in rows if isinstance(rows, list) else []:
        url = str(row.get("Link") or "").strip()
        title = str(row.get("Name") or "").strip()
        if not url or not title:
            continue
        result.append({
            "symbol": symbol,
            "title": title,
            "url": url,
            "source": SOURCE,
            "doc_type": TYPE_MAP[document_type],
            "published_date": _date(row, title, url),
            "article_content": "",
            "article_images": [],
            "article_pdf_urls": [url],
            "sentiment_score": 0.0,
        })
    return result


async def run(
    symbols: list[str] | None = None,
    exchange: str | None = None,
    types: tuple[int, ...] = (1, 5),
) -> dict[str, Any]:
    selected = [s.upper().strip() for s in (symbols or load_symbols(exchange)) if s]
    selected_types = tuple(t for t in types if t in TYPE_MAP)
    if not selected_types:
        raise ValueError("CafeF crawler chỉ cho phép Type 1 và Type 5")
    inserted = 0
    errors: list[dict[str, str]] = []
    async with httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=5, max_keepalive_connections=5)) as client:
        for document_type in selected_types:
            for offset in range(0, len(selected), 5):
                batch = selected[offset:offset + 5]
                results = await asyncio.gather(
                    *(fetch(client, symbol, document_type) for symbol in batch),
                    return_exceptions=True,
                )
                for symbol, rows in zip(batch, results):
                    if isinstance(rows, Exception):
                        error = {"symbol": symbol, "type": str(document_type), "error": str(rows)}
                        errors.append(error)
                        print(f"CafeF crawl error: {error}")
                        continue
                    if isinstance(rows, list):
                        inserted += sum(int(upsert_article(row, source=SOURCE)) for row in rows)
    return {"status": "success" if not errors else "partial", "source": SOURCE, "symbols": len(selected), "types": list(selected_types), "inserted": inserted, "error_count": len(errors), "errors": errors}


if __name__ == "__main__":
    print(asyncio.run(run()))
