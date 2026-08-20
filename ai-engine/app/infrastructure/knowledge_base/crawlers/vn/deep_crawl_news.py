"""Deep crawl news articles — fetch full HTML content + save PDF text to disk.

Strategy:
   1. Query knowledge_documents for rows missing article_content
   2. Fetch HTML via httpx.AsyncClient (concurrent, semaphore=20)
   3. Parse with selectolax, extract full article body
   4. For /du-lieu/ articles: extract PDF text → save to data/pdf_texts/
      (pdfplumber first; scanned → Google Drive OCR fallback)
   5. Update DB with HTML content; PDF text stored on disk, path in article_pdf_text
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from datetime import timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import pdfplumber
from app.infrastructure.knowledge_base.crawlers.vn.html_parser import extract_article_data
from app.infrastructure.knowledge_base.crawlers.vn.news_repo import (
    get_urls_to_crawl,
    update_content,
    count_missing_content,
)
from app.infrastructure.knowledge_base.crawlers.vn.triage_engine import get_triage_engine

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
MAX_CONCURRENT = 20
TRIAGE_MODE = "deferred"
REQUEST_TIMEOUT = 30
PDF_MAX_SIZE = 5 * 1024 * 1024

# Root folder for PDF extracted texts (one file per document)
PDF_TEXT_ROOT = Path(__file__).parent.parent.parent.parent.parent / "data" / "pdf_texts"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}


def _sanitize_filename(url: str) -> str:
    name = url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in url else "doc"
    name = re.sub(r"[^\w\-_]", "_", name)[:60]
    return name or "doc"


def _extract_pdf_sync(pdf_content: bytes) -> str:
    """Try pdfplumber first, fallback to Google Drive OCR if scanned.
    Returns extracted text or empty string."""
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        pages_text = []
        total_chars = 0
        for page in pdf.pages:
            t = (page.extract_text() or "").strip()
            pages_text.append(t)
            total_chars += len(t)
    combined = "\n\n---\n\n".join(pages_text)
    if total_chars > 100:
        return combined

    # Scanned → try Google Drive OCR
    try:
        from app.infrastructure.knowledge_base.crawlers.vn.pdf_parser import _gdrive_ocr_sync
        gtext = _gdrive_ocr_sync(pdf_content)
        if gtext and len(gtext) > 100:
            logger.info("  GDrive OCR: %d chars", len(gtext))
            return gtext
    except Exception as e:
        logger.debug("  GDrive OCR failed: %s", e)
    return ""


async def _save_pdf_text(client: httpx.AsyncClient, pdf_url: str, symbol: str, doc_id: int) -> str:
    """Download PDF → extract text → save to data/pdf_texts/{symbol}/{id}_{name}.txt.
    Returns relative path string or empty string on failure."""
    try:
        resp = await client.get(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AIInvest/1.0)"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.debug("PDF download failed [%s]: %s", pdf_url[-60:], e)
        return ""

    if not resp.content.startswith(b"%PDF-") or len(resp.content) > PDF_MAX_SIZE:
        return ""

    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _extract_pdf_sync, resp.content)
    if not text:
        return ""

    # Save to disk
    sym_dir = PDF_TEXT_ROOT / (symbol or "UNKNOWN")
    sym_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{doc_id}_{_sanitize_filename(pdf_url)}.txt"
    fpath = sym_dir / fname
    try:
        fpath.write_text(text, encoding="utf-8")
        logger.info("  PDF text saved: %s (%d chars)", fpath.relative_to(PDF_TEXT_ROOT.parent), len(text))
    except Exception as e:
        logger.warning("  Failed to write PDF text: %s", e)
        return ""

    return str(fpath.relative_to(PDF_TEXT_ROOT.parent))


async def _fetch_one(
    client: httpx.AsyncClient, news_id: int, url: str, title: str, symbol: str,
    is_du_lieu: bool, sem: asyncio.Semaphore, extract_pdfs: bool = False,
) -> Tuple[int, str, str, str]:
    async with sem:
        try:
            resp = await client.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                return news_id, title, '', ''

            data = extract_article_data(resp.text, url)
            html_content = data.get("content", "")

            pdf_path = ''
            if extract_pdfs and is_du_lieu and html_content:
                for pdf_url in data.get("pdf_urls", []):
                    pdf_path = await _save_pdf_text(client, pdf_url, symbol, news_id)
                    if pdf_path:
                        break

            return news_id, title, html_content, pdf_path
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return news_id, title, '', ''


async def _crawl_urls(rows: List[Dict[str, Any]], extract_pdfs: bool = False) -> List[Dict[str, Any]]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=MAX_CONCURRENT, max_keepalive_connections=MAX_CONCURRENT),
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
    ) as client:
        tasks = [
            _fetch_one(client, r["id"], r["url"], r["title"], r.get("symbol", ""),
                       "/du-lieu/" in r["url"], sem, extract_pdfs)
            for r in rows
        ]
        fetched_results = await asyncio.gather(*tasks)

    valid_results = []
    for news_id, title, content, pdf_path in fetched_results:
        if content:
            valid_results.append({
                "id": news_id,
                "title": title,
                "article_content": content,
                "article_pdf_text": pdf_path,
            })

    if valid_results and TRIAGE_MODE == "inline":
        logger.info("Triaging %d articles with AI (inline)...", len(valid_results))
        triage_engine = get_triage_engine()
        triaged_results = await triage_engine.triage_batch(valid_results, concurrency=3)
        return triaged_results
    return valid_results


def refresh_deep_crawl(limit: int = 500, extract_pdfs: bool = False) -> Dict[str, Any]:
    articles = get_urls_to_crawl(limit)
    if not articles:
        return {"status": "no_content_needed", "crawled": 0, "total": 0}

    logger.info("Deep crawling %d articles (concurrent=%d, extract_pdfs=%s)...",
                len(articles), MAX_CONCURRENT, extract_pdfs)
    triaged_results = asyncio.run(_crawl_urls(articles, extract_pdfs))

    updated = update_content(triaged_results) if triaged_results else 0

    with_html = len(triaged_results)
    pdf_saved = sum(1 for r in triaged_results if r.get("article_pdf_text"))

    return {
        "status": "success",
        "total": len(articles),
        "crawled": with_html,
        "failed": len(articles) - with_html,
        "updated_db": updated,
        "pdf_saved": pdf_saved,
    }


if __name__ == "__main__":
    import sys as _sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    _limit = 500
    _extract = False
    for _a in _sys.argv[1:]:
        if _a == "--extract-pdfs":
            _extract = True
        elif _a.startswith("--limit="):
            _limit = int(_a.split("=", 1)[1])
        elif _a == "--daily":
            _extract = True
            _limit = 200

    missing = count_missing_content()
    logger.info("%d articles missing content (extract_pdfs=%s, limit=%d)", missing, _extract, _limit)
    result = refresh_deep_crawl(limit=_limit, extract_pdfs=_extract)
    logger.info("Result: %s", result)
