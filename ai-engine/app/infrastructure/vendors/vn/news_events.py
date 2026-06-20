"""News Events Pipeline — CafeF Events_RelatedNews_New.aspx.
Scrape news events per symbol, compute sentiment scores.
Feeds NEWS_SENTIMENT_5D factor in factor_scores.py.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from app.infrastructure.database.pg_pool import DB_URL
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)

API_URL = "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx"
RATE_LIMIT_DELAY = 0.2
BATCH_SIZE = 50
TZ_VN = timezone(timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://cafef.vn/",
}

FLOOR_MAP = {"HOSE": 2, "HSX": 2, "HNX": 3, "UPCOM": 4}

# Vietnamese sentiment lexicon
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
    """Lexicon-based sentiment score in [-1.0, 1.0]."""
    t = title.lower()
    pos = sum(1 for kw in _POSITIVE_KW if kw in t)
    neg = sum(1 for kw in _NEGATIVE_KW if kw in t)
    total = pos + neg
    if total == 0:
        return 0.0
    raw = (pos - neg) / total
    return max(-1.0, min(1.0, raw))


def _get_floor_id(exchange: str) -> int:
    return FLOOR_MAP.get(exchange.upper(), 2)


def _parse_published_date(raw: str) -> Optional[datetime]:
    """Parse 'DD/MM/YYYY HH:mm' from span.timeTitle."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%d/%m/%Y %H:%M").replace(tzinfo=TZ_VN)
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%d/%m/%Y").replace(tzinfo=TZ_VN)
    except ValueError:
        pass
    return None


def _fetch_page(
    symbol: str, floor_id: int, config_id: int, page: int = 1,
) -> list[dict]:
    """Fetch one page of news from CafeF. Returns list of {title, url, published_date}."""
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        try:
            resp = client.get(
                API_URL,
                params={
                    "Symbol": symbol,
                    "floorID": floor_id,
                    "configID": config_id,
                    "PageIndex": page,
                    "PageSize": 30,
                    "Type": 2,
                },
            )
            if resp.status_code != 200:
                logger.debug("  HTTP %d for %s config=%d page=%d", resp.status_code, symbol, config_id, page)
                return []
            html = resp.text
        except Exception as e:
            logger.debug("  Error fetching %s page %d: %s", symbol, page, e)
            return []

    items = []
    # Parse HTML: find all li items inside #divEvents > ul
    li_pattern = re.compile(
        r'<li[^>]*>.*?'
        r'<span\s+class="timeTitle">(.*?)</span>.*?'
        r'<a[^>]+href="(.*?)"[^>]+title="(.*?)".*?</li>',
        re.DOTALL,
    )
    for m in li_pattern.finditer(html):
        time_raw = m.group(1).strip()
        url_raw = m.group(2).strip()
        title_raw = m.group(3).strip()
        published = _parse_published_date(time_raw)
        if published is None:
            continue
        if not url_raw.startswith("http"):
            url_raw = "https://cafef.vn" + url_raw
        items.append({
            "symbol": symbol,
            "published_date": published,
            "title": title_raw,
            "url": url_raw,
            "source": "cafef",
            "config_id": config_id,
        })
    return items


def fetch_for_symbol(
    symbol: str, floor_id: int, config_ids: Optional[list[int]] = None,
    max_pages: int = 3,
) -> list[dict]:
    """Fetch news for a symbol across config_ids, up to max_pages each."""
    if config_ids is None:
        config_ids = [0, 5]
    all_items = []
    seen_urls = set()
    for config_id in config_ids:
        for page in range(1, max_pages + 1):
            items = _fetch_page(symbol, floor_id, config_id, page)
            if not items:
                break
            for item in items:
                if item["url"] not in seen_urls:
                    item["sentiment_score"] = _classify_sentiment(item["title"])
                    all_items.append(item)
                    seen_urls.add(item["url"])
            time.sleep(RATE_LIMIT_DELAY)
    return all_items


def _process_symbols(symbols: list[tuple[str, str]], storage: Optional[StoragePort] = None) -> dict:
    """Process symbols list of (symbol, exchange) tuples."""
    if storage is None:
        storage = PostgresAdapter(DB_URL)

    total_new = 0
    total_err = 0
    for idx, (sym, exchange) in enumerate(symbols):
        if idx > 0 and idx % BATCH_SIZE == 0:
            logger.info("  Progress: %d/%d, %d new rows", idx, len(symbols), total_new)
            time.sleep(1)
        try:
            floor_id = _get_floor_id(exchange)
            items = fetch_for_symbol(sym, floor_id)
            if not items:
                continue
            rows = []
            for item in items:
                rows.append((
                    item["symbol"],
                    item["published_date"],
                    item["title"],
                    item["url"],
                    item["source"],
                    item["config_id"],
                    item["sentiment_score"],
                ))
            
            storage.execute_values(
                """INSERT INTO news_events
                   (symbol, published_date, title, url, source, config_id, sentiment_score)
                   VALUES %s
                   ON CONFLICT (symbol, url) DO NOTHING""",
                rows,
                page_size=100,
            )
            total_new += len(rows)
        except Exception as e:
            logger.warning("Failed for %s: %s", sym, e)
            total_err += 1
            time.sleep(RATE_LIMIT_DELAY * 3)

    return {"new_rows": total_new, "errors": total_err, "symbols": len(symbols)}


def refresh_all(storage: Optional[StoragePort] = None) -> dict:
    """Full refresh: fetch news events for all HOSE symbols."""
    if storage is None:
        storage = PostgresAdapter(DB_URL)
    try:
        symbols = storage.fetch_all("SELECT symbol, exchange FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        logger.info("News events full refresh: %d symbols", len(symbols))
        result = _process_symbols(symbols, storage=storage)
        logger.info("News events done: %d new rows, %d errors", result["new_rows"], result["errors"])
        return result
    except Exception as e:
        logger.error(f"Error in refresh_all: {e}")
        return {"new_rows": 0, "errors": 1, "symbols": 0}


def refresh_incremental() -> dict:
    """Incremental: same as full (idempotent via ON CONFLICT DO NOTHING).
    News events are cheap to fetch and ON CONFLICT deduplicates.
    """
    return refresh_all()


def compute_sentiment_5d(symbols: list[str], score_date: date, cur) -> dict[str, float]:
    """Compute NEWS_SENTIMENT_5D factor: average sentiment over last 5 trading days.
    Returns dict of {symbol: avg_sentiment_score}.
    """
    end = score_date
    start = score_date - timedelta(days=10)

    cur.execute(
        """SELECT symbol, AVG(sentiment_score)
           FROM news_events
           WHERE symbol = ANY(%s)
             AND published_date::date >= %s
             AND published_date::date <= %s
             AND sentiment_score IS NOT NULL
           GROUP BY symbol""",
        (symbols, start, end),
    )
    result = {}
    for row in cur.fetchall():
        result[row[0]] = float(row[1])
    return result
