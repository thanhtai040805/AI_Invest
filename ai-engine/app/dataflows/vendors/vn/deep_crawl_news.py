"""Deep crawl news articles — fetch full content + PDFs from CafeF URLs.

Strategy:
   1. Query news_events for rows missing article_content
   2. Fetch HTML via httpx.AsyncClient (concurrent, semaphore=20)
   3. Parse with selectolax, extract full article body (HTML + images)
   4. For /du-lieu/ articles: extract PDF text (hybrid pypdf + skip scanned pages)
   5. Update DB with HTML content + extracted PDF text

Content extraction:
  - /du-lieu/ disclosure pages: div.KenhF_Content_News3
  - Editorial CafeF articles: div.detail-content → find first <p> tag, skip sidebar noise
  - Fallback: largest text block

PDF extraction (only for /du-lieu/ disclosure):
  - Find <a href="...pdf"> links in article HTML
  - Skip if filename contains BCTC/bao-cao-tai-chinh/financial keywords
  - HEAD request → skip > 5 MB
  - pdfplumber hybrid: extract text pages, skip blank/scan pages
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import tempfile
from datetime import timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
MAX_CONCURRENT = 20
REQUEST_TIMEOUT = 30
PDF_MAX_SIZE = 5 * 1024 * 1024  # 5 MB

_SKIP_PDF_KEYWORDS = [
    "bao-cao-tai-chinh", "bc-tc", "bctc", "financial", "annual",
    "bao-cao-thuong-nien", "annual-report",
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://cafef.vn/",
}


def _extract_text_from_html(html_content: str) -> str:
    """Strip HTML tags from content, returning clean plain text."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _find_pdf_links(html: str) -> List[str]:
    """Extract PDF download URLs from article HTML."""
    links = []
    for m in re.finditer(r'<a[^>]+href="([^"]+\.pdf)"', html, re.IGNORECASE):
        url = m.group(1)
        if any(kw in url.lower() for kw in _SKIP_PDF_KEYWORDS):
            logger.debug("Skipping BCTC PDF: %s", url)
            continue
        links.append(url)
    return links


def _extract_pdf_text_sync(pdf_url: str, http_client: httpx.Client) -> str:
    """Download PDF and extract text via pdfplumber hybrid approach.
    Returns empty string if PDF is scanned (no text layer).
    """
    try:
        resp = http_client.head(pdf_url, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return ""
        size = int(resp.headers.get("Content-Length", 0))
        if size > PDF_MAX_SIZE or size == 0:
            return ""

        resp = http_client.get(pdf_url, headers=_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return ""
    except Exception:
        return ""

    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            chunks = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text.strip())
                # Tables: extract as markdown-like rows
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        row_str = " | ".join(str(c or "") for c in row)
                        if row_str.strip():
                            chunks.append(row_str)
            result = "\n\n".join(chunks)
            return result if len(result) > 50 else ""
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(resp.content))
            chunks = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text.strip())
            result = "\n\n".join(chunks)
            return result if len(result) > 50 else ""
        except Exception:
            return ""


async def _fetch_pdf_text(pdf_url: str) -> str:
    """Async wrapper around _extract_pdf_text_sync."""
    with httpx.Client(headers=_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        return await asyncio.to_thread(_extract_pdf_text_sync, pdf_url, client)


def _extract_content(html: str) -> str:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)

    # 1. Try disclosure page selector (du-lieu pages)
    node = tree.css_first('div.KenhF_Content_News3')
    if node:
        inner = node.html or ''
        inner = re.sub(r'^<div[^>]*>', '', inner)
        inner = re.sub(r'</div>\s*$', '', inner)
        inner = inner.strip()
        if len(inner) > 50:
            return inner

    # 2. Try editorial article selector
    node = tree.css_first('div.detail-content')
    if node:
        inner = node.html or ''
        first_p = inner.find('<p')
        if first_p >= 0:
            body_html = inner[first_p:]
            body_html = re.sub(r'\s*</div>\s*$', '', body_html).strip()
            # Verify it has real content (not just empty tags)
            text = _extract_text_from_html(body_html)
            if len(text) > 80:
                return body_html

    # 3. Try other known selectors
    for sel in ['div#mainContent', 'div#divContent', 'div.content', 'article']:
        node = tree.css_first(sel)
        if node:
            inner = node.html or ''
            text = _extract_text_from_html(inner)
            if len(text) > 80:
                return inner

    # 4. Fallback: find largest <p> blocks
    body = tree.css_first('body')
    if body:
        candidates = sorted(body.css('p'), key=lambda n: len(n.text()), reverse=True)
        if candidates:
            combined_html = ''.join(n.html or '' for n in candidates[:5])
            text = _extract_text_from_html(combined_html)
            if len(text) > 80:
                return combined_html

    return ''


async def _fetch_one(client: httpx.AsyncClient, url: str, is_du_lieu: bool, sem: asyncio.Semaphore) -> Tuple[str, str, str]:
    async with sem:
        try:
            resp = await client.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                return url, '', ''
            html_content = _extract_content(resp.text)

            pdf_text = ''
            if is_du_lieu and html_content:
                pdf_links = _find_pdf_links(html_content)
                for pdf_url in pdf_links:
                    pdf_text = await _fetch_pdf_text(pdf_url)
                    if pdf_text:
                        break

            return url, html_content, pdf_text
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return url, '', ''


async def _crawl_urls(urls: List[str], is_du_lieu_flags: List[bool]) -> Dict[str, Dict[str, str]]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=MAX_CONCURRENT, max_keepalive_connections=MAX_CONCURRENT),
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
    ) as client:
        tasks = [_fetch_one(client, url, flag, sem) for url, flag in zip(urls, is_du_lieu_flags)]
        results = await asyncio.gather(*tasks)
    return {r[0]: {"html": r[1], "pdf_text": r[2]} for r in results}


def _get_urls_to_crawl(limit: int = 500) -> List[Dict[str, Any]]:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, symbol, url, title FROM news_events
                WHERE (article_content IS NULL OR article_content = '')
                  AND url IS NOT NULL
                ORDER BY published_date DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _get_urls_missing_pdf_text(limit: int = 500) -> List[Dict[str, Any]]:
    """Get du-lieu articles that have HTML content but missing PDF text."""
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, symbol, url, title FROM news_events
                WHERE article_content IS NOT NULL
                  AND article_content != ''
                  AND (article_pdf_text IS NULL OR article_pdf_text = '')
                  AND url LIKE '%/du-lieu/%'
                ORDER BY published_date DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _update_content(rows: List[Tuple[int, str, str]]) -> int:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            updated = 0
            for news_id, content, pdf_text in rows:
                if content:
                    cur.execute("""
                        UPDATE news_events
                        SET article_content = %s,
                            article_pdf_text = CASE WHEN %s != '' THEN %s ELSE article_pdf_text END,
                            content_fetched_at = NOW()
                        WHERE id = %s AND (article_content IS NULL OR article_content = '')
                    """, (content, pdf_text, pdf_text, news_id))
                    updated += cur.rowcount
            conn.commit()
            return updated
    finally:
        conn.close()


def refresh_deep_crawl(limit: int = 500) -> Dict[str, Any]:
    articles = _get_urls_to_crawl(limit)
    if not articles:
        return {"status": "no_content_needed", "crawled": 0, "total": 0}

    urls = [a["url"] for a in articles]
    id_map = {a["url"]: a["id"] for a in articles}
    is_du_lieu = ["/du-lieu/" in a["url"] for a in articles]

    logger.info("Deep crawling %d articles (concurrent=%d)...", len(urls), MAX_CONCURRENT)
    content_map = asyncio.run(_crawl_urls(urls, is_du_lieu))

    update_rows = []
    pdf_extracted = 0
    for url, data in content_map.items():
        html = data["html"]
        pdf_text = data["pdf_text"]
        if html and url in id_map:
            update_rows.append((id_map[url], html, pdf_text))
            if pdf_text:
                pdf_extracted += 1

    updated = _update_content(update_rows) if update_rows else 0

    with_html = sum(1 for d in content_map.values() if d["html"])
    failed = sum(1 for d in content_map.values() if not d["html"])

    return {
        "status": "success",
        "total": len(articles),
        "crawled": with_html,
        "failed": failed,
        "updated_db": updated,
        "pdf_extracted": pdf_extracted,
        "concurrent": MAX_CONCURRENT,
    }


def refresh_deep_crawl_pdfs(limit: int = 500) -> Dict[str, Any]:
    """Re-crawl only /du-lieu/ articles that have HTML but missing PDF text."""
    articles = _get_urls_missing_pdf_text(limit)
    if not articles:
        return {"status": "no_content_needed", "crawled": 0, "total": 0}

    urls = [a["url"] for a in articles]
    id_map = {a["url"]: a["id"] for a in articles}
    is_du_lieu = [True] * len(urls)

    logger.info("Deep crawling PDFs for %d articles...", len(urls))
    content_map = asyncio.run(_crawl_urls(urls, is_du_lieu))

    update_rows = []
    pdf_extracted = 0
    for url, data in content_map.items():
        pdf_text = data["pdf_text"]
        if pdf_text and url in id_map:
            update_rows.append((id_map[url], data["html"], pdf_text))
            pdf_extracted += 1

    if update_rows:
        _update_content(update_rows)

    return {
        "status": "success",
        "total": len(articles),
        "pdf_extracted": pdf_extracted,
        "skipped": len(articles) - pdf_extracted,
    }


def count_missing_content() -> int:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM news_events
                WHERE (article_content IS NULL OR article_content = '')
                  AND url IS NOT NULL
            """)
            return cur.fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    missing = count_missing_content()
    logger.info("%d articles missing content", missing)
    result = refresh_deep_crawl(limit=200)
    logger.info("Result: %s", result)
