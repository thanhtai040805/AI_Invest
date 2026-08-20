"""
vietstock_news_crawl.py — Vietstock news scraper via _Partials/TopPageArticle.

API: POST https://vietstock.vn/_Partials/TopPageArticle
     data: {channelid: N, item: M}

Channels: chung-khoan=144, doanh-nghiep=733, bat-dong-san=763, ...

Entry point: refresh_listing(items=100, channel_ids=None, deep_crawl=True) -> dict
CLI: python -m app.infrastructure.knowledge_base.crawlers.vn.vietstock_news_crawl [--channels 144,733] [--no-deep]
"""
import asyncio, logging, re, sys
from datetime import datetime, timezone

import httpx

from app.infrastructure.knowledge_base.crawlers.vn.html_parser import extract_article_data
from app.infrastructure.knowledge_base.crawlers.vn.pdf_parser import async_download_pdf_text
from app.infrastructure.knowledge_base.crawlers.vn.news_repo import batch_has_content, upsert_articles as upsert

logger = logging.getLogger(__name__)

CHANNELS = {
    144: "chung-khoan", 733: "doanh-nghiep", 763: "bat-dong-san",
    734: "tai-chinh", 5307: "kinh-te", 736: "the-gioi", 2: "hang-hoa",
    579: "nhan-dinh-phan-tich", 4259: "tai-chinh-ca-nhan", 1317: "dong-duong",
}

CONCURRENT_DEEP = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_KNOWN_SYMBOLS: set[str] | None = None


def load_known_symbols() -> set[str]:
    global _KNOWN_SYMBOLS
    if _KNOWN_SYMBOLS is not None:
        return _KNOWN_SYMBOLS
    from app.infrastructure.database.pg_pool import get_cursor
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM stocks")
        _KNOWN_SYMBOLS = {r[0] for r in cur.fetchall()}
    return _KNOWN_SYMBOLS


def extract_symbol_from_url(url: str) -> str:
    m = re.search(r"/([A-Z]{2,5})[/-]", url.upper())
    if m:
        sym = m.group(1)
        if sym in load_known_symbols():
            return sym
    return "GENERAL"


# ── Listing phase ───────────────────────────────────────────────────

async def fetch_channel_articles(client: httpx.AsyncClient, channel_id: int,
                                 channel_name: str, items: int) -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"https://vietstock.vn/{channel_name}.htm",
    }
    try:
        r = await client.post(
            "https://vietstock.vn/_Partials/TopPageArticle",
            data={"channelid": channel_id, "item": items},
            headers=headers, timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("  [%s] TopPageArticle failed: %s", channel_name, e)
        return []

    if not isinstance(data, list):
        return []

    articles = []
    for art in data:
        url = art.get("URL", "")
        if not url:
            continue
        full_url = url if url.startswith("http") else f"https://vietstock.vn{url}"
        title = (art.get("Title") or "").strip()
        if not title:
            continue
        articles.append({"url": full_url, "title": title, "symbol": extract_symbol_from_url(url), "doc_type": "news"})

    return articles


async def crawl_all_channels(client: httpx.AsyncClient,
                             channel_ids: list[int] | None = None,
                             items: int = 100) -> list[dict]:
    channels_to_crawl = {cid: CHANNELS[cid] for cid in (channel_ids or CHANNELS)}
    seen_urls: set[str] = set()
    all_articles: list[dict] = []

    for cid, cname in channels_to_crawl.items():
        articles = await fetch_channel_articles(client, cid, cname, items)
        new_count = 0
        for art in articles:
            if art["url"] not in seen_urls:
                seen_urls.add(art["url"])
                all_articles.append(art)
                new_count += 1
        logger.info("  [%s] (ch=%d) → %d new (total %d)", cname, cid, new_count, len(all_articles))

    return all_articles


# ── Deep crawl ──────────────────────────────────────────────────────

async def deep_crawl_one(client: httpx.AsyncClient, art: dict, download_pdf: bool = True) -> dict | None:
    try:
        resp = await client.get(art["url"], headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return None

    data = extract_article_data(resp.text, base_url=art["url"])
    content = data["content"]
    pdf_urls = data["pdf_urls"]
    pdf_link_texts = data.get("pdf_link_texts", {})

    pdf_text = ""
    if download_pdf and pdf_urls:
        pdf_parts = []
        for pdf_url in pdf_urls:
            text = await async_download_pdf_text(client, pdf_url, timeout_sec=15)
            label = pdf_link_texts.get(pdf_url, "")
            if text == "[SCANNED_PDF]":
                pdf_parts.append("[Tài liệu đính kèm (file scan): %s]" % (label or pdf_url.split("/")[-1]))
            elif text:
                header = "\n[Nội dung file đính kèm: %s]\n" % (label or pdf_url.split("/")[-1])
                pdf_parts.append(header + text + "\n[Kết thúc file đính kèm]")
            else:
                pdf_parts.append("[File đính kèm: %s]" % (label or pdf_url.split("/")[-1]))
        pdf_text = "\n\n".join(pdf_parts)

    merged = content
    if pdf_text:
        if len(content) < 300:
            merged = pdf_text
        else:
            merged = content + "\n\n---\n" + pdf_text

    return {
        **art,
        "article_content": merged,
        "article_images": data["images"],
        "article_pdf_urls": pdf_urls,
        "article_pdf_text": pdf_text,
        "published_date": data["published_date"],
    }


async def deep_crawl_all(articles: list[dict]) -> list[dict]:
    from asyncio import Semaphore

    has_content = batch_has_content(articles)
    to_crawl = [art for art, hc in zip(articles, has_content) if not hc]
    skipped = sum(1 for hc in has_content if hc)
    if skipped:
        logger.info("Deep crawl pre-check: %d already have content, skipping", skipped)

    if not to_crawl:
        return articles

    sem = Semaphore(CONCURRENT_DEEP)
    limits = httpx.Limits(max_keepalive_connections=CONCURRENT_DEEP, max_connections=CONCURRENT_DEEP)

    async with httpx.AsyncClient(limits=limits, timeout=30, follow_redirects=True) as client:
        async def fetch_one(art: dict):
            async with sem:
                return await deep_crawl_one(client, art)

        done = await asyncio.gather(*[fetch_one(art) for art in to_crawl])

    crawl_idx = 0
    with_content = 0
    for i in range(len(articles)):
        if has_content[i]:
            continue
        result = done[crawl_idx]
        if result and result.get("article_content"):
            articles[i] = result
            with_content += 1
        crawl_idx += 1

    logger.info("Deep crawl: %d/%d crawled, %d with content (skipped %d)",
                len(to_crawl), len(articles), with_content, skipped)
    return articles


# ── Entry point ──────────────────────────────────────────────────────

async def _run(items: int = 100, channel_ids: list[int] | None = None,
               deep_crawl: bool = True) -> dict:
    from app.infrastructure.database.pg_pool import migrate
    migrate()

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=5)
    async with httpx.AsyncClient(limits=limits, timeout=30, follow_redirects=True) as client:
        articles = await crawl_all_channels(client, channel_ids, items)
        if not articles:
            return {"status": "ok", "articles": 0, "inserted": 0, "with_content": 0}

        inserted = upsert(articles, "vietstock")

        if deep_crawl:
            articles = await deep_crawl_all(articles)
            # Update DB with deep crawled content
            upsert(articles, "vietstock")

        with_content = sum(1 for a in articles if a.get("article_content"))
        with_images = sum(1 for a in articles if a.get("article_images"))

    return {
        "status": "ok",
        "articles": len(articles),
        "inserted": inserted,
        "with_content": with_content,
        "with_images": with_images,
        "pdf_urls_found": sum(len(a.get("article_pdf_urls", [])) for a in articles),
    }


def refresh_listing(items: int = 100, channel_ids: list[int] | None = None,
                    deep_crawl: bool = True) -> dict:
    return asyncio.run(_run(items=items, channel_ids=channel_ids, deep_crawl=deep_crawl))


# ── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    channels = None
    deep = True
    i = 0
    while i < len(args):
        if args[i] == "--channels" and i + 1 < len(args):
            channels = [int(x) for x in args[i + 1].split(",")]
            i += 2
        elif args[i] == "--no-deep":
            deep = False
            i += 1
        else:
            print(f"Usage: python -m app.infrastructure.knowledge_base.crawlers.vn.vietstock_news_crawl [--channels 144,733] [--no-deep]")
            sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = refresh_listing(channel_ids=channels, deep_crawl=deep)
    print(result)
