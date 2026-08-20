"""
vneconomy_crawl.py — Crawl VnEconomy via sitemap discovery + meta-tag filtering.

Sources (sitemap-based, no pagination needed):
  - Monthly sitemaps:  /sitemap/news-YYYY-MM.xml  (2007–present, ~1600 articles/month)
  - Latest sitemap:    /sitemap/latest-news.xml    (500 most recent)

Pipeline:
  1. Fetch sitemap index → list monthly sitemaps
  2. Fetch each monthly sitemap → list article URLs
  3. Quick GET each URL → read meta[article:section]
  4. Filter: only keep articles from target categories
  5. Upsert to knowledge_documents via news_repo.upsert_articles()
  6. (Optional) trigger deep crawl for new articles

Target categories (from meta[property="article:section"]):
  - "Chứng khoán"              → AGENT-01, AGENT-03
  - "Doanh nghiệp niêm yết"    → AGENT-09
  - "Tài chính"                → AGENT-04

Usage:
  python -m app.infrastructure.knowledge_base.crawlers.vn.vneconomy_crawl
  python -m app.infrastructure.knowledge_base.crawlers.vn.vneconomy_crawl --backfill
  python -m app.infrastructure.knowledge_base.crawlers.vn.vneconomy_crawl --backfill --months 12
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import html

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────

SITEMAP_INDEX = "https://vneconomy.vn/sitemap.xml"
LATEST_SITEMAP = "https://vneconomy.vn/sitemap/latest-news.xml"
BASE_URL = "https://vneconomy.vn"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

# Target categories — must match meta[property="article:section"] content
TARGET_CATEGORIES: Set[str] = {
    "Chứng khoán",
    "Doanh nghiệp niêm yết",
    "Tài chính",
}

# How many months to backfill (default 6)
DEFAULT_BACKFILL_MONTHS = 6

# Concurrency for meta-tag fetching
CONCURRENT_META = 20

# ── Helpers ───────────────────────────────────────────────────────────────

_TITLE_CLEAN_RE = re.compile(r"\s*[-–|]\s*VnEconomy.*$", re.IGNORECASE)


def _parse_iso_date(raw: str) -> Optional[datetime]:
    """Parse ISO 8601 datetime from meta tag."""
    try:
        return datetime.fromisoformat(raw.replace("&#x2B;", "+"))
    except (ValueError, TypeError):
        pass
    return None


def _parse_vn_date(raw: str) -> Optional[datetime]:
    """Parse 'HH:MM, DD/MM/YYYY' format from time tag."""
    m = re.match(r"(\d{2}:\d{2}),\s*(\d{2}/\d{2}/\d{4})", raw.strip())
    if m:
        try:
            dt = datetime.strptime(f"{m.group(2)} {m.group(1)}", "%d/%m/%Y %H:%M")
            return dt.replace(tzinfo=timezone(timedelta(hours=7)))
        except ValueError:
            pass
    return None


def _clean_title(raw: str) -> str:
    return _TITLE_CLEAN_RE.sub("", raw).strip()


_META_ATTR = r'(?:name|property)'

def _extract_meta(raw_html: str) -> dict:
    """Extract meta info from VnEconomy article HTML.

    Returns dict with keys: title, published_date, section, author.
    Dates parsed to datetime objects where possible.
    """
    result: dict = {"title": None, "published_date": None, "section": None, "author": None}

    # section from meta[name|property="article:section"]
    m = re.search(r'<meta\s+' + _META_ATTR + r'="article:section"\s+content="([^"]+)"', raw_html)
    if m:
        result["section"] = html.unescape(m.group(1))

    # published_date: try name="article:published_time" first, then property
    for attr_type in ("name", "property"):
        m = re.search(r'<meta\s+' + attr_type + r'="article:published_time"\s+content="([^"]+)"', raw_html)
        if m:
            result["published_date"] = _parse_iso_date(m.group(1))
            break

    if result["published_date"] is None:
        # fallback: parse from <time> tag
        m = re.search(r'<time[^>]*>(\d{2}:\d{2},\s*\d{2}/\d{2}/\d{4})</time>', raw_html)
        if m:
            result["published_date"] = _parse_vn_date(m.group(1))

    # author from <meta name="article:author"> or <meta name="author">
    for pat in (
        r'<meta\s+' + _META_ATTR + r'="article:author"\s+content="([^"]+)"',
        r'<meta\s+name="author"\s+content="([^"]+)"',
    ):
        m = re.search(pat, raw_html)
        if m:
            result["author"] = html.unescape(m.group(1)).strip()
            break

    # title from <title> tag
    m = re.search(r'<title>(.*?)</title>', raw_html, re.DOTALL)
    if m:
        result["title"] = html.unescape(_clean_title(m.group(1)))

    return result


_KNOWN_SYMBOLS: Set[str] | None = None


def load_known_symbols() -> Set[str]:
    global _KNOWN_SYMBOLS
    if _KNOWN_SYMBOLS is not None:
        return _KNOWN_SYMBOLS
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM stocks")
        _KNOWN_SYMBOLS = {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning("Failed to load known symbols for VnEconomy: %s", e)
        _KNOWN_SYMBOLS = set()
    return _KNOWN_SYMBOLS


def _extract_symbol(title: str, section: str) -> str:
    """Extract stock symbol from title. Returns 'GENERAL' if none found."""
    # 1. Check start of title
    m = re.match(r"^([A-Z]{2,5})\b", title)
    if m:
        sym = m.group(1)
        if sym not in ("HOSE", "UPCOM", "HNX", "HSX", "CEO", "CFO", "GDP", "FDI", "USD", "VND", "EUR", "FED"):
            return sym
            
    # 2. Check for colon patterns mid-title
    m = re.search(r"\b([A-Z]{2,5})\s*:", title)
    if m:
        sym = m.group(1)
        if sym not in ("HOSE", "UPCOM", "HNX", "HSX", "CEO", "CFO", "GDP", "FDI", "USD", "VND", "EUR", "FED"):
            return sym

    # 3. Check for any known ticker inside the title words
    known = load_known_symbols()
    if known:
        words = re.findall(r"\b([A-Z]{3,5})\b", title)
        for w in words:
            if w in known and w not in ("HOSE", "UPCOM", "HNX", "HSX", "CEO", "CFO", "GDP", "FDI", "USD", "VND", "EUR", "FED"):
                return w

    return "GENERAL"



# ── Sitemap discovery ─────────────────────────────────────────────────────

async def _fetch_text(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.debug("Fetch failed: %s — %s", url, e)
        return None


async def _get_monthly_sitemaps(client: httpx.AsyncClient) -> List[str]:
    """Fetch sitemap index, return list of monthly news sitemap URLs."""
    text = await _fetch_text(client, SITEMAP_INDEX)
    if not text:
        return []
    return re.findall(r"<loc>(https://vneconomy\.vn/sitemap/news-\d{4}-\d{2}\.xml)</loc>", text)


async def _get_latest_urls(client: httpx.AsyncClient) -> List[str]:
    """Fetch latest-news.xml, return list of article URLs."""
    text = await _fetch_text(client, LATEST_SITEMAP)
    if not text:
        return []
    return re.findall(r"<loc>(https://vneconomy\.vn/[^<]+\.htm)</loc>", text)


async def _get_month_urls(client: httpx.AsyncClient, sitemap_url: str) -> List[str]:
    """Fetch a monthly sitemap, return list of article URLs."""
    text = await _fetch_text(client, sitemap_url)
    if not text:
        return []
    return re.findall(r"<loc>(https://vneconomy\.vn/[^<]+\.htm)</loc>", text)


def _sitemap_month(sitemap_url: str) -> str:
    """Extract 'YYYY-MM' from sitemap URL for sorting."""
    m = re.search(r"news-(\d{4}-\d{2})", sitemap_url)
    return m.group(1) if m else "0000-00"


def _filter_sitemaps_by_months(sitemaps: List[str], months: int) -> List[str]:
    """Keep only sitemaps from the last N months."""
    if months <= 0:
        return sitemaps
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    cutoff_str = cutoff.strftime("%Y-%m")
    return sorted([s for s in sitemaps if _sitemap_month(s) >= cutoff_str], reverse=True)


# ── Meta-tag fetching ─────────────────────────────────────────────────────

async def _check_article(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
) -> Optional[Dict[str, Any]]:
    """Fetch article page, extract meta, return dict if target category."""
    async with sem:
        try:
            html = await _fetch_text(client, url)
            if not html:
                return None

            meta = _extract_meta(html)
            section = meta.get("section")
            if section not in TARGET_CATEGORIES:
                return None

            title = meta.get("title")
            if not title:
                return None

            published_date = meta.get("published_date")
            if published_date is None:
                return None

            symbol = _extract_symbol(title, section)

            return {
                "symbol": symbol,
                "published_date": published_date,
                "title": title,
                "url": url,
                "source": "vneconomy",
                "doc_type": "news",
                "article_content": "",
                "article_images": [],
                "article_pdf_urls": [],
                "sentiment_score": 0.0,
            }
        except Exception as e:
            logger.debug("Meta check failed for %s: %s", url, e)
            return None


async def _check_articles_batch(urls: List[str]) -> List[Dict[str, Any]]:
    """Check a batch of article URLs, return only target-category articles."""
    sem = asyncio.Semaphore(CONCURRENT_META)
    async with httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=CONCURRENT_META,
            max_keepalive_connections=CONCURRENT_META,
        ),
        timeout=httpx.Timeout(20),
    ) as client:
        tasks = [_check_article(client, url, sem) for url in urls]
        results = await asyncio.gather(*tasks)

    return [r for r in results if r is not None]


# ── Fetch listing ─────────────────────────────────────────────────────────

async def fetch_from_sitemaps(
    sitemap_urls: List[str],
) -> List[Dict[str, Any]]:
    """Fetch articles from a list of monthly sitemaps.

    Process each month sequentially (avoid hammering server),
    but check article metas concurrently within each month.
    """
    all_articles: List[Dict[str, Any]] = []
    total_urls = 0

    async with httpx.AsyncClient(timeout=15) as client:
        for sitemap_url in sitemap_urls:
            month = _sitemap_month(sitemap_url)
            urls = await _get_month_urls(client, sitemap_url)
            if not urls:
                continue
            total_urls += len(urls)
            articles = await _check_articles_batch(urls)
            all_articles.extend(articles)
            logger.info(
                "  %s: %d/%d relevant (%.0f%%)",
                month, len(articles), len(urls),
                len(articles) / len(urls) * 100 if urls else 0,
            )

    logger.info(
        "Sitemaps: %d months, %d URLs total, %d relevant (%.1f%%)",
        len(sitemap_urls), total_urls, len(all_articles),
        len(all_articles) / total_urls * 100 if total_urls else 0,
    )
    return all_articles


async def fetch_latest() -> List[Dict[str, Any]]:
    """Fetch latest articles from latest-news.xml."""
    sem = asyncio.Semaphore(CONCURRENT_META)
    async with httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=CONCURRENT_META,
            max_keepalive_connections=CONCURRENT_META,
        ),
        timeout=httpx.Timeout(20),
    ) as client:
        urls = await _get_latest_urls(client)

    if not urls:
        logger.warning("No URLs found in latest-news.xml")
        return []

    return await _check_articles_batch(urls)


def fetch_listing(
    months: int = 0,
    backfill: bool = False,
) -> List[Dict[str, Any]]:
    """Fetch VnEconomy articles matching target categories.

    Args:
        months: Number of months to backfill (0 = all available).
                Only used when backfill=True.
        backfill: If True, crawl monthly sitemaps. If False, use latest-news.xml.

    Returns:
        List of article dicts matching target categories.
    """
    if not backfill:
        return asyncio.run(fetch_latest())

    async def _get_sitemaps_and_fetch():
        async with httpx.AsyncClient(timeout=15) as client:
            sitemaps = await _get_monthly_sitemaps(client)
            if not sitemaps:
                return []
            if months > 0:
                sitemaps = _filter_sitemaps_by_months(sitemaps, months)
            logger.info("Fetching %d monthly sitemaps (%s → %s)...",
                        len(sitemaps),
                        _sitemap_month(sitemaps[-1]) if sitemaps else "?",
                        _sitemap_month(sitemaps[0]) if sitemaps else "?")
            return sitemaps

    sitemaps = asyncio.run(_get_sitemaps_and_fetch())
    if not sitemaps:
        logger.warning("No monthly sitemaps found!")
        return []

    return asyncio.run(fetch_from_sitemaps(sitemaps))


# ── Upsert ────────────────────────────────────────────────────────────────

def upsert_articles(articles: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert into knowledge_documents via news_repo."""
    if not articles:
        return {"inserted": 0, "skipped": 0}

    from app.infrastructure.knowledge_base.crawlers.vn.news_repo import upsert_articles as _upsert_batch

    inserted = _upsert_batch(articles, source="vneconomy")
    return {"inserted": inserted, "skipped": len(articles) - inserted}


# ── Public API ────────────────────────────────────────────────────────────

def refresh_listing(
    backfill: bool = False,
    months: int = DEFAULT_BACKFILL_MONTHS,
    deep_crawl: bool = True,
) -> Dict[str, Any]:
    """Full flow: fetch → upsert → (optional) deep crawl.

    Args:
        backfill: If True, backfill monthly sitemaps. If False, daily latest.
        months: Months to backfill (only when backfill=True).
        deep_crawl: Trigger deep crawl for newly inserted articles.

    Returns:
        Stats dict with status, total, inserted, skipped, deep_crawl.
    """
    # Phase 1: discover + filter
    articles = fetch_listing(months=months, backfill=backfill)
    if not articles:
        return {"status": "no_items", "total": 0, "inserted": 0}

    # Phase 2: upsert
    stats = upsert_articles(articles)

    result: Dict[str, Any] = {
        "status": "success",
        "total": len(articles),
        **stats,
    }

    # Phase 3: deep crawl (optional)
    if deep_crawl and stats.get("inserted", 0) > 0:
        try:
            from app.infrastructure.knowledge_base.crawlers.vn.deep_crawl_news import refresh_deep_crawl

            deep_result = asyncio.run(refresh_deep_crawl(limit=stats["inserted"] * 5))
            result["deep_crawl"] = deep_result
            logger.info("Deep crawl result: %s", deep_result)
        except Exception as e:
            logger.warning("Deep crawl failed: %s", e)
            result["deep_crawl"] = {"status": "error", "error": str(e)}

    return result


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    )

    args = sys.argv[1:]
    backfill = "--backfill" in args
    months = DEFAULT_BACKFILL_MONTHS

    for i, a in enumerate(args):
        if a == "--months" and i + 1 < len(args):
            try:
                months = int(args[i + 1])
            except ValueError:
                pass

    mode = "BACKFILL" if backfill else "DAILY"
    logger.info("=== VnEconomy Crawl — %s%s ===", mode,
                f" ({months} months)" if backfill else "")

    result = refresh_listing(backfill=backfill, months=months, deep_crawl=True)
    logger.info("Result: %s", result)
