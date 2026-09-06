"""
cafef_document_crawl.py — Crawl CafeF corporate documents via Ajax API.

API: GET /du-lieu/Ajax/PageNew/FileBCTC.ashx?Symbol={symbol}&Type={type}&Year=0

Type → doc_type mapping:
  1 → financial_statement    (BCTC: balance sheet, P&L, cash flow PDFs)
  3 → annual_report          (annual reports, prospectus, charter PDFs)
  4 → agm_resolution         (AGM + BOD resolutions PDFs)
  5 → governance_report      (corporate governance reports PDFs)

Lưu ý:
  - Báo chí (News) là HTML thuần túy, được cào và parse riêng bởi cafef_listing_crawl / html_parser.
  - File này chỉ cào và lập chỉ mục metadata + URL file PDF gốc của Doanh nghiệp.
  - Nội dung PDF sẽ được tải và chuyển đổi thành Markdown bởi PageClassifier & MinerU trong SAG pipeline.

Usage:
  python -m app.infrastructure.knowledge_base.crawlers.vn.cafef_document_crawl
  python -m app.infrastructure.knowledge_base.crawlers.vn.cafef_document_crawl --type 1,5
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────

API_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx"
CONCURRENT = 5        # lower concurrency to avoid rate-limit
BATCH_DELAY = 0.3     # seconds between batches
MAX_PDF_SIZE = 10 * 1024 * 1024  # 10 MB

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://cafef.vn/",
    "X-Requested-With": "XMLHttpRequest",
}

# Type → doc_type mapping
TYPE_MAP: Dict[int, str] = {
    1: "financial_statement",
    3: "annual_report",
    4: "agm_resolution",
    5: "governance_report",
}

# ── Symbol loading ─────────────────────────────────────────────────────────

def load_symbols(exchange: Optional[str] = None) -> List[str]:
    """Load symbols from stocks table optionally filtered by exchange."""
    from app.infrastructure.database.pg_pool import get_cursor

    with get_cursor() as cur:
        if exchange:
            cur.execute("SELECT symbol FROM stocks WHERE exchange = %s ORDER BY symbol", (exchange,))
        else:
            cur.execute("SELECT symbol FROM stocks ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


# ── API call ───────────────────────────────────────────────────────────────

async def fetch_documents(client: httpx.AsyncClient, symbol: str, doc_type: int, retries: int = 3) -> List[Dict[str, Any]]:
    """Call CafeF API for a given symbol + type. Returns list of document dicts."""
    import asyncio as _asyncio

    for attempt in range(1, retries + 1):
        try:
            resp = await client.get(
                API_URL,
                headers=HEADERS,
                params={"Symbol": symbol, "Type": doc_type, "Year": 0},
                timeout=15,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()

            if not isinstance(data, dict) or not data.get("Success"):
                logger.debug("  [%s] Type=%d API returned no success (attempt %d/%d): %s",
                             symbol, doc_type, attempt, retries, str(data)[:100])
                if attempt < retries:
                    await _asyncio.sleep(2.0 * attempt)
                    continue
                return []

            items = data.get("Data") or []
            if not isinstance(items, list):
                return []

            results = []
            seen_links: set = set()
            for item in items:
                link = (item.get("Link") or "").strip()
                name = (item.get("Name") or "").strip()
                if not link or not name:
                    continue
                if any(kw in name.lower() for kw in ("cáo bạch", "điều lệ")):
                    continue
                if link in seen_links:
                    continue
                seen_links.add(link)

                results.append({
                    "symbol": symbol,
                    "title": name,
                    "url": link,
                    "doc_type": TYPE_MAP.get(doc_type, "news"),
                    "published_date": _parse_doc_date(item),
                    "source": "cafef_docs",
                    "article_content": "",
                    "article_images": [],
                    "article_pdf_urls": [link],
                    "sentiment_score": 0.0,
                })

            return results
        except Exception as e:
            logger.debug("  [%s] Type=%d fetch error (attempt %d/%d): %s", symbol, doc_type, attempt, retries, e)
            if attempt < retries:
                await _asyncio.sleep(2.0 * attempt)
    return []


_TIME_RE = re.compile(r"^(?:Q([1-4])|CN)\s*/\s*(\d{4})$")
_TIME_DDMM_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")

def _parse_doc_date(item: dict) -> datetime:
    """Parse document date from API response item.
    
    Primary source is the `Time` field ("Q1/2026", "CN/2025", or "DD-MM-YYYY" for agm_resolution).
    Falls back to Year + Quarter if Time is unparseable.
    """
    time_str = (item.get("Time") or "").strip()

    # Format: Q1/2026 or CN/2025
    m = _TIME_RE.match(time_str)
    if m:
        year = int(m.group(2))
        if year < 100:
            year += 2000
        quarter_str = m.group(1)
        if quarter_str:
            q = int(quarter_str)
            month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
            day = {1: 31, 2: 30, 3: 30, 4: 31}[q]
        else:
            month, day = 12, 31
        return datetime(year, month, day, tzinfo=timezone.utc)

    # Format: DD-MM-YYYY (used by Type=4 agm_resolution)
    m = _TIME_DDMM_RE.match(time_str)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2000 <= year <= 2100:
            return datetime(year, month, day, tzinfo=timezone.utc)

    # Fallback: Year + Quarter fields
    raw_year = item.get("Year")
    if not raw_year or not isinstance(raw_year, int) or raw_year <= 0:
        year = datetime.now(timezone.utc).year
    elif raw_year < 100:
        year = raw_year + 2000
    elif raw_year < 1000:
        year = datetime.now(timezone.utc).year
    else:
        year = raw_year

    raw_quarter = item.get("Quarter")
    if not raw_quarter or not isinstance(raw_quarter, int) or raw_quarter < 1:
        quarter = 4
    else:
        quarter = raw_quarter

    if quarter == 5:
        month, day = 12, 31
    elif quarter == 4:
        month, day = 10, 15
    elif quarter == 3:
        month, day = 7, 15
    elif quarter == 2:
        month, day = 4, 15
    else:
        month, day = 1, 15

    return datetime(year, month, day, tzinfo=timezone.utc)


# ── Quarterly Master Document Filter for RAG & Ingestion ─────────────────

def select_target_bctc(documents_in_quarter: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Tự động nhận diện và chọn đúng BCTC MASTER cho mọi loại doanh nghiệp (Cả FPT và FTS).
    
    Ưu tiên:
    1. BCTC Hợp nhất (+100 điểm)
    2. BCTC Kiểm toán / Soát xét (+50 / +30 điểm)
    3. Fallback cho Công ty Đơn thể (FTS) không có bản Hợp nhất.
    """
    if not documents_in_quarter:
        return None

    def calculate_priority(doc: Dict[str, Any]) -> int:
        title = (doc.get("title") or "").lower()
        score = 0
        if "hợp nhất" in title:
            score += 100
        if "kiểm toán" in title:
            score += 50
        elif "soát xét" in title:
            score += 30
        return score

    sorted_docs = sorted(documents_in_quarter, key=calculate_priority, reverse=True)
    return sorted_docs[0]


def filter_master_financial_statements(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gộp nhóm BCTC theo (symbol, year, quarter/month) cho loại financial_statement,
    chỉ giữ lại DUY NHẤT 1 FILE MASTER tối ưu nhất mỗi kỳ để đưa vào RAG & Database,
    loại bỏ hoàn toàn trùng lặp và tiết kiệm chi phí GPU/Embedding.
    """
    from collections import defaultdict

    non_bctc = [d for d in docs if d.get("doc_type") != "financial_statement"]
    bctc_docs = [d for d in docs if d.get("doc_type") == "financial_statement"]

    grouped: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for d in bctc_docs:
        pub_date = d.get("published_date")
        if isinstance(pub_date, datetime):
            year = pub_date.year
            month = pub_date.month
        else:
            year, month = 0, 0
        title_lower = (d.get("title") or "").lower()
        is_parent = any(k in title_lower for k in ["công ty mẹ", "riêng", "cong ty me", "don the"])
        key = (d.get("symbol"), year, month, is_parent)
        grouped[key].append(d)

    selected_bctc = []
    for key, group in grouped.items():
        master_doc = select_target_bctc(group)
        if master_doc:
            selected_bctc.append(master_doc)

    return selected_bctc + non_bctc



# ── Upsert ─────────────────────────────────────────────────────────────────

def upsert_documents(documents: List[Dict[str, Any]]) -> tuple[int, List[str]]:
    """Upsert document batch into knowledge_documents. Returns (count inserted, list of inserted symbols)."""
    if not documents:
        return 0, []

    from app.infrastructure.knowledge_base.crawlers.vn.news_repo import upsert_article

    inserted = 0
    new_symbols = set()
    for doc in documents:
        if upsert_article(doc, source="cafef_docs"):
            inserted += 1
            sym = doc.get("symbol")
            if sym:
                new_symbols.add(sym.upper().strip())
    return inserted, sorted(list(new_symbols))


# ── Main entry point ───────────────────────────────────────────────────────

async def run(
    types: Optional[List[int]] = None,
    symbols: Optional[List[str]] = None,
    exchange: Optional[str] = None,
    max_years: int = 5,
) -> Dict[str, Any]:
    """Run the corporate document crawl pipeline.

    Args:
        types: List of type IDs to crawl. Default all (1, 3, 4, 5).
        symbols: Specific symbols to crawl. If None, load from stocks table.
        exchange: Filter symbols by exchange (e.g. 'HOSE'). Only used when symbols=None.
        max_years: Max age of documents to keep (0 = keep all). Default 5 years.

    Returns:
        Stats dict.
    """
    if types is None:
        types = [1, 3, 4, 5]

    cutoff = None
    if max_years > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_years * 365)

    # Phase 1: load symbols
    if symbols is None:
        symbols = load_symbols(exchange)
    if not symbols:
        return {"status": "error", "error": "No symbols to crawl"}

    logger.info("Cafef Document Crawl: %d symbols, types=%s", len(symbols), types)

    # Phase 2: fetch API for each symbol + type
    all_docs: List[Dict[str, Any]] = []
    seen_urls: set = set()

    limits = httpx.Limits(max_connections=CONCURRENT, max_keepalive_connections=CONCURRENT)
    async with httpx.AsyncClient(limits=limits, timeout=15) as client:
        for doc_type in types:
            for i in range(0, len(symbols), CONCURRENT):
                batch = symbols[i:i + CONCURRENT]
                tasks = [fetch_documents(client, sym, doc_type) for sym in batch]
                results = await asyncio.gather(*tasks)
                for docs in results:
                    for d in docs:
                        if d["url"] not in seen_urls:
                            seen_urls.add(d["url"])
                            all_docs.append(d)
                if i + CONCURRENT < len(symbols):
                    await asyncio.sleep(BATCH_DELAY)

            type_name = TYPE_MAP.get(doc_type, f"type_{doc_type}")
            type_docs = [d for d in all_docs if d["doc_type"] == type_name]
            logger.info("  Type=%d (%s): %d documents", doc_type, type_name, len(type_docs))

    if not all_docs:
        return {"status": "ok", "total": 0, "inserted": 0, "new_symbols": []}

    # Phase 2b: filter by date cutoff
    if cutoff:
        before_cutoff = len(all_docs)
        all_docs = [d for d in all_docs if d.get("published_date") and d["published_date"] >= cutoff]
        dropped = before_cutoff - len(all_docs)
        if dropped:
            logger.info("Date filter: dropped %d documents older than %d years", dropped, max_years)

    # Phase 2c: Filter master BCTC per quarter (eliminates duplicate parent / un-audited files for RAG)
    before_master = len(all_docs)
    all_docs = filter_master_financial_statements(all_docs)
    logger.info("Master BCTC filter: selected %d master documents from %d total", len(all_docs), before_master)

    # Phase 3: upsert
    inserted, new_symbols = upsert_documents(all_docs)
    logger.info("Upsert: %d/%d new (others existed), %d new symbols", inserted, len(all_docs), len(new_symbols))

    return {
        "status": "success",
        "symbols": len(symbols),
        "total": len(all_docs),
        "inserted": inserted,
        "new_symbols": new_symbols,
    }


def crawl(
    types: Optional[List[int]] = None,
    symbols: Optional[List[str]] = None,
    exchange: Optional[str] = None,
    max_years: int = 5,
) -> Dict[str, Any]:
    """Sync entry point."""
    return asyncio.run(run(types=types, symbols=symbols, exchange=exchange, max_years=max_years))


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    )

    args = sys.argv[1:]
    types = None
    exchange = None
    max_years = 5

    for a in args:
        if a.startswith("--type="):
            types = [int(t) for t in a.split("=", 1)[1].split(",")]
        elif a.startswith("--exchange="):
            exchange = a.split("=", 1)[1].upper()
        elif a.startswith("--max-years="):
            max_years = int(a.split("=", 1)[1])

    logger.info("=== CafeF Document Crawl (max_years=%d) ===", max_years)
    result = crawl(types=types, exchange=exchange, max_years=max_years)
    logger.info("Result: %s", result)
