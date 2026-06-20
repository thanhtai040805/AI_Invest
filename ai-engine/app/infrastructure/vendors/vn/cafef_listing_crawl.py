"""Crawl CafeF news listing pages — multi-category, paginated, cron-friendly.

Categories:
  du-lieu layout: /du-lieu/tin-doanh-nghiep  (ul>li + span.timeTitle, date %d/%m/%Y %H:%M)
  root layout:    /thi-truong-chung-khoan, /bat-dong-san, /tai-chinh-ngan-hang
                  (div.tlitem-flex, date ISO prefix in text, title from a[title])

Upserts into news_events (ON CONFLICT symbol+url SKIP).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psycopg2
import psycopg2.extras

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://cafef.vn/",
}

# ── Category registry ────────────────────────────────────────────────────
# (slug, layout) — layout determines HTML parsing strategy
CATEGORIES: Dict[str, Tuple[str, str]] = {
    "corporate-disclosures": ("du-lieu/tin-doanh-nghiep", "du-lieu"),
    "stock-market":          ("thi-truong-chung-khoan", "root"),
    "real-estate":           ("bat-dong-san",           "root"),
    "banking-finance":       ("tai-chinh-ngan-hang",    "root"),
}

# ── Sentiment lexicon ────────────────────────────────────────────────────

_POSITIVE_KW = [
    "tăng", "lợi nhuận", "khởi sắc", "vượt", "thành công",
    "mở rộng", "tăng trưởng", "ký kết", "mua", "nâng",
    "phát triển", "đột phá", "tích cực", "hiệu quả",
    "cổ tức", "thưởng",
]
_NEGATIVE_KW = [
    "giảm", "cắt lỗ", "thu hẹp", "sụt", "thất bại",
    "thua lỗ", "cắt giảm", "hủy bỏ", "rút lui", "bán",
    "cảnh báo", "rủi ro", "đình chỉ", "phá sản",
    "trái phiếu xấu", "nợ xấu",
]


def _classify_sentiment(title: str) -> float:
    t = title.lower()
    pos = sum(1 for kw in _POSITIVE_KW if kw in t)
    neg = sum(1 for kw in _NEGATIVE_KW if kw in t)
    total = pos + neg
    if total == 0:
        return 0.0
    raw = (pos - neg) / total
    return max(-1.0, min(1.0, raw))


def _parse_time_dulieu(raw: str) -> Optional[datetime]:
    """Parse dd/mm/yyyy HH:MM from span.timeTitle."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=TZ_VN)
        except ValueError:
            pass
    return None


def _parse_time_iso(raw: str) -> Optional[datetime]:
    """Parse ISO datetime 2026-06-11T19:19:00 from tlitem-flex text."""
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", raw)
    if m:
        try:
            return datetime.fromisoformat(m.group(1)).replace(tzinfo=TZ_VN)
        except ValueError:
            pass
    return None


def _extract_symbol(title: str) -> Optional[str]:
    """Extract symbol from "[SYMBOL]: Title" or "SYMBOL: Title"."""
    m = re.match(r"^\[?([A-Z]{2,5})\]?\s*:\s*(.*)", title)
    if m:
        sym = m.group(1)
        if sym in ("HOSE", "UPCOM", "HNX", "HSX"):
            return None
        return sym
    return None


# ── Page URL helpers ─────────────────────────────────────────────────────

def _category_url(slug: str, page: int) -> str:
    if page == 1:
        return f"https://cafef.vn/{slug}.chn"
    return f"https://cafef.vn/{slug}/trang-{page}.chn"


def _abs_url(href: str) -> str:
    if href.startswith("/"):
        return "https://cafef.vn" + href
    return href


# ── Parsers per layout ──────────────────────────────────────────────────

def _parse_dulieu_page(
    tree: "HTMLParser",
    seen_urls: set,
) -> List[Dict[str, Any]]:
    """Parse /du-lieu/ listing page: ul > li > a + span.timeTitle."""
    items: List[Dict[str, Any]] = []
    for ul in tree.css("ul"):
        for li in ul.css("li"):
            a = li.css_first("a")
            time_el = li.css_first("span.timeTitle")
            if not a:
                continue
            href = a.attributes.get("href", "")
            title = a.text(strip=True)
            if not title or not href:
                continue
            href = _abs_url(href)
            if not href.startswith("http"):
                continue
            clean_url = href.split("?")[0]
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)

            published = _parse_time_dulieu(time_el.text(strip=True)) if time_el else None
            if published is None:
                continue
            symbol = _extract_symbol(title)
            items.append({
                "symbol": symbol or "GENERAL",
                "published_date": published,
                "title": title,
                "url": clean_url,
                "source": "cafef",
                "sentiment_score": _classify_sentiment(title),
            })
    return items


def _parse_root_page(
    tree: "HTMLParser",
    seen_urls: set,
) -> List[Dict[str, Any]]:
    """Parse root-level listing page: div.tlitem-flex.
    
    Date is ISO-prefix in div text, title from a[title] attribute.
    """
    items: List[Dict[str, Any]] = []
    for div in tree.css("div.tlitem-flex"):
        a = div.css_first("a")
        if not a:
            continue
        href = a.attributes.get("href", "")
        title = a.attributes.get("title", "")
        if not title or not href:
            continue
        href = _abs_url(href)
        if not href.startswith("http"):
            continue
        clean_url = href.split("?")[0]
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        txt = div.text(strip=True)
        published = _parse_time_iso(txt) if txt else None
        if published is None:
            continue
        symbol = _extract_symbol(title)
        items.append({
            "symbol": symbol or "GENERAL",
            "published_date": published,
            "title": title,
            "url": clean_url,
            "source": "cafef",
            "sentiment_score": _classify_sentiment(title),
        })
    return items


_PARSERS = {
    "du-lieu": _parse_dulieu_page,
    "root":    _parse_root_page,
}


# ── Fetch ────────────────────────────────────────────────────────────────

def fetch_listing(
    max_pages: int = 1,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Fetch listing pages for given categories, return deduplicated items.

    Args:
        max_pages: Pages per category (1 = cron, 50 = backfill).
        categories: Category keys to crawl (default: all).
    """
    from selectolax.parser import HTMLParser

    cats = {k: v for k, v in CATEGORIES.items() if categories is None or k in categories}
    items: List[Dict[str, Any]] = []
    seen_urls: set = set()
    cat_stats: Dict[str, int] = {}

    with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
        for cat_key, (slug, layout) in cats.items():
            parser = _PARSERS[layout]
            total = 0
            for page in range(1, max_pages + 1):
                url = _category_url(slug, page)
                try:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        break
                except Exception as e:
                    logger.debug("[%s] page %d error: %s", cat_key, page, e)
                    break

                tree = HTMLParser(resp.text)
                page_items = parser(tree, seen_urls)
                total += len(page_items)
                items.extend(page_items)

            cat_stats[cat_key] = total
            logger.info(
                "[%s] %d items from %d pages (layout=%s)",
                cat_key, total, min(max_pages, page), layout,
            )

    logger.info("Total: %d items across %d categories", len(items), len(cats))
    return items


# ── Upsert ───────────────────────────────────────────────────────────────

def upsert_news(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """Bulk upsert into news_events. Returns {inserted, skipped}."""
    if not items:
        return {"inserted": 0, "skipped": 0}

    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            inserted = 0
            skipped = 0
            for item in items:
                cur.execute(
                    """INSERT INTO news_events
                       (symbol, published_date, title, url, source, sentiment_score)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, url) DO NOTHING""",
                    (
                        item["symbol"],
                        item["published_date"],
                        item["title"],
                        item["url"],
                        item["source"],
                        item["sentiment_score"],
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            conn.commit()
            logger.info("Upserted %d new, %d existing skipped", inserted, skipped)
            return {"inserted": inserted, "skipped": skipped}
    finally:
        conn.close()


# ── Public API ───────────────────────────────────────────────────────────

def refresh_listing(
    max_pages: int = 1,
    categories: Optional[List[str]] = None,
    deep_crawl: bool = True,
) -> Dict[str, Any]:
    """Full flow: fetch listing → upsert → deep crawl new URLs → return stats.
    
    Args:
        max_pages: 1 for cron, 50 for backfill.
        categories: None = all, or list of keys from CATEGORIES.
        deep_crawl: If True, fetch full article content for new URLs immediately.
    """
    items = fetch_listing(max_pages=max_pages, categories=categories)
    if not items:
        return {"status": "no_items", "total": 0, "inserted": 0}

    stats = upsert_news(items)

    result: Dict[str, Any] = {
        "status": "success",
        "total": len(items),
        **stats,
    }

    if deep_crawl and stats.get("inserted", 0) > 0:
        try:
            from app.infrastructure.vendors.vn.deep_crawl_news import refresh_deep_crawl
            import asyncio
            deep_result = asyncio.run(refresh_deep_crawl(limit=stats["inserted"] * 5))
            result["deep_crawl"] = deep_result
            logger.info("Deep crawl result: %s", deep_result)
        except Exception as e:
            logger.warning("Deep crawl failed: %s", e)
            result["deep_crawl"] = {"status": "error", "error": str(e)}

    return result


def refresh_single_category(
    cat_key: str,
    max_pages: int = 1,
) -> Dict[str, Any]:
    """Convenience: crawl a single category by key."""
    return refresh_listing(max_pages=max_pages, categories=[cat_key])


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    result = refresh_listing(max_pages=pages)
    logger.info("Listing crawl result: %s", result)
