"""
cafef_report_crawl.py — Scrape CafeF analyst/macro/industry reports for Moat AI.

Sources:
  cap-nhat-doanh-nghiep-khuyen-nghi → company analysis (24mo backfill)
  bao-cao-nganh                     → industry reports  (36mo backfill)
  bao-cao-vi-mo                     → macro reports     (36mo backfill)

Page 1:  httpx GET base URL directly (pre-rendered Blazor HTML)
Page N:  httpx GET /trang-N.chn (server-rendered HTML, works without JS)

Usage:
  python scripts/cafef_report_crawl.py                   # daily (page 1 only)
  python scripts/cafef_report_crawl.py --backfill        # historical backfill
  python scripts/cafef_report_crawl.py --category macro  # single category
"""

import asyncio, csv, logging, os, re, sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────

BASE = "https://cafef.vn/du-lieu/phan-tich-bao-cao"

CATEGORIES = {
    "company": {
        "slug": "cap-nhat-doanh-nghiep-khuyen-nghi",
        "backfill_months": 3,
        "label": "company_analysis",
    },
    "industry": {
        "slug": "bao-cao-nganh",
        "backfill_months": 3,
        "label": "industry_report",
    },
    "macro": {
        "slug": "bao-cao-vi-mo",
        "backfill_months": 3,
        "label": "macro_report",
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Fixed: page 1 has the items inside content_bcpt_moi_nhat (first featured + item-group),
# paginated pages (trang-N.chn) have a different wrapper. We detect both.
# Symbol extraction from thumbnail filename: thumb_{SYMBOL}_...

# Pages 2+ (trang-N.chn) return duplicate or unfiltered content.
# Only page 1 per category is reliable — yields ~13 reports.

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cafef_reports")


# ── Data model ────────────────────────────────────────────────────────

@dataclass
class Report:
    url: str
    title: str
    date: str             # DD/MM/YYYY
    symbol: str           # extracted from thumbnail or title, may be "GENERAL"
    category: str         # company|industry|macro
    source: str = ""      # broker name if available
    pdf_url: str = ""     # actual download URL if found
    report_id: str = ""   # unique ID from URL
    thumbnail: str = ""


# ── Parse helpers ─────────────────────────────────────────────────────

_SYMBOL_RE = re.compile(r"thumb[_-]([A-Z]{2,5})[_-]", re.IGNORECASE)
_TITLE_SYMBOL_RE = re.compile(r"^([A-Z]{2,5})\b")
_REPORT_ID_RE = re.compile(r"-([a-f0-9]{24,32})\.chn")


def _extract_symbol(title: str, thumbnail: str) -> str:
    m = _SYMBOL_RE.search(thumbnail or "")
    if m:
        return m.group(1).upper()
    m = _TITLE_SYMBOL_RE.search(title or "")
    if m:
        s = m.group(1).upper()
        if len(s) >= 2:
            return s
    return "GENERAL"


def _extract_report_id(url: str) -> str:
    clean = url.split("?", 1)[0]
    m = _REPORT_ID_RE.search(clean)
    return m.group(1) if m else ""


def _parse_item(item, category: str, page_url: str) -> Optional[Report]:
    try:
        a_tag = item.select_one("a[href*='/du-lieu/report/']")
        if not a_tag:
            return None
        href = a_tag.get("href", "")
        url = href if href.startswith("http") else f"https://cafef.vn{href}"

        img_tag = item.select_one("img")
        thumbnail = img_tag.get("src", "") if img_tag else ""

        title_div = item.select_one("[class*='title']")
        title = ""
        if title_div:
            title = title_div.get("title", "") or title_div.get_text(strip=True)

        date = ""
        for cls in ("time-link-time", "footer-left-time", "first-content-footer-left-time"):
            el = item.select_one(f"[class*='{cls}']")
            if el:
                date = el.get_text(strip=True)
                break

        source = ""
        src_el = item.select_one("[class*='source']")
        if src_el:
            source = src_el.get_text(strip=True).removeprefix("Nguồn: ").strip()

        symbol = _extract_symbol(title, thumbnail)
        report_id = _extract_report_id(url)

        if not title and not report_id:
            return None

        return Report(
            url=url,
            title=title,
            date=date,
            symbol=symbol,
            category=category,
            source=source,
            report_id=report_id,
            thumbnail=thumbnail,
        )
    except Exception as e:
        logger.debug("Parse item failed: %s", e)
        return None


def parse_page(html: str, category: str, page_url: str) -> list[Report]:
    soup = BeautifulSoup(html, "html.parser")
    reports = []

    # Strategy 1: items inside item-group (page is paginated, trang-N.chn)
    for group in soup.select(".item-group"):
        for item in group.select(".item-child"):
            r = _parse_item(item, category, page_url)
            if r:
                reports.append(r)

    # Strategy 2: featured first item on page 1
    featured = soup.select_one(".item-first")
    if featured:
        r = _parse_item(featured, category, page_url)
        if r:
            reports.insert(0, r)

    return reports


# ── Fetch helpers ────────────────────────────────────────────────────

async def fetch_page(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning("Fetch failed: %s — %s", url, e)
        return None


async def scrape_category(
    client: httpx.AsyncClient,
    category: str,
    cutoff: Optional[datetime] = None,
) -> list[Report]:
    cfg = CATEGORIES[category]
    url = f"{BASE}/{cfg['slug']}.chn"

    html = await fetch_page(client, url)
    if not html:
        return []

    reports = parse_page(html, category, url)
    if not reports:
        logger.info("  [%s] no reports found", category)
        return []

    result: list[Report] = []
    seen_ids: set[str] = set()

    for r in reports:
        dedup_key = r.report_id or f"{r.title}|{r.date}"
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        if cutoff and r.date:
            try:
                d = datetime.strptime(r.date, "%d/%m/%Y").replace(tzinfo=timezone.utc)
                if d < cutoff:
                    continue
            except ValueError:
                pass

        result.append(r)

    logger.info("  [%s] %d reports (fetched %d, %d after cutoff)", category, len(result), len(reports), len(result))
    return result


# ── Output ────────────────────────────────────────────────────────────




# ── Main ──────────────────────────────────────────────────────────────

async def main():
    args = sys.argv[1:]
    backfill = "--backfill" in args
    single_cat = None

    for a in args:
        if a.startswith("--category="):
            single_cat = a.split("=", 1)[1]
        elif a.startswith("--category"):
            idx = args.index(a)
            if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
                single_cat = args[idx + 1]

    categories_to_run = [single_cat] if single_cat else list(CATEGORIES.keys())

    if single_cat and single_cat not in CATEGORIES:
        print(f"Unknown category: {single_cat}. Choose from: {', '.join(CATEGORIES.keys())}")
        sys.exit(1)

    mode = "BACKFILL" if backfill else "DAILY (page 1 only)"
    logger.info("=== CafeF Report Crawl — %s ===", mode)
    logger.info("Categories: %s", categories_to_run)

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=5)
    async with httpx.AsyncClient(limits=limits, timeout=30) as client:
        all_reports = []
        for cat in categories_to_run:
            if backfill:
                cfg = CATEGORIES[cat]
                cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["backfill_months"] * 30)
            else:
                cutoff = None

            logger.info("Scraping category: %s (cutoff=%s)", cat, cutoff.strftime("%d/%m/%Y") if cutoff else "none")
            reports = await scrape_category(client, cat, cutoff=cutoff)
            all_reports.extend(reports)

        if not all_reports:
            logger.warning("No reports found!")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mode_tag = "backfill" if backfill else "daily"

        # Convert to dictionary format expected by news_repo
        from app.infrastructure.knowledge_base.crawlers.vn.news_repo import upsert_articles
        art_list = []
        for r in all_reports:
            try:
                published_date = datetime.strptime(r.date.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    published_date = datetime.strptime(r.date.strip(), "%d/%m/%Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    published_date = datetime.now(timezone.utc)

            art = {
                "symbol": r.symbol or "GENERAL",
                "published_date": published_date,
                "title": f"[{r.category.upper()}] {r.title}",
                "url": r.url,
                "doc_type": "analyst_report",
                "article_content": "",
                "article_images": [r.thumbnail] if r.thumbnail else [],
                "article_pdf_urls": [r.pdf_url] if r.pdf_url else [],
            }
            art_list.append(art)

        inserted = upsert_articles(art_list, source="cafef_report")
        logger.info("Upserted %d new reports out of %d to database", inserted, len(art_list))

        # Summary
        by_cat = {}
        for r in all_reports:
            by_cat.setdefault(r.category, []).append(r)

        logger.info("=== Summary ===")
        for cat, items in by_cat.items():
            logger.info("  %s: %d reports", cat, len(items))
        logger.info("Total: %d reports processed", len(all_reports))


if __name__ == "__main__":
    asyncio.run(main())
