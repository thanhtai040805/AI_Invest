import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CAFEF_SEARCH_URL = "https://cafef.vn/tim-kiem.chn"

KEYWORDS_T3 = ["cầm cố", "giải chấp", "call margin", "bán giải chấp"]
KEYWORDS_T6 = [
    "bị khởi tố", "tạm giam", "hủy niêm yết", "đình chỉ giao dịch",
    "truy thu thuế", "thao túng thị trường", "vi phạm công bố thông tin",
    "thanh tra ủy ban", "xử phạt",
]

SYMBOL_PATTERN = re.compile(r'\b([A-Z]{2,4})\b')
SKIP_WORDS = {"VN", "VND", "USD", "TỶ", "CEO", "CFO", "TP", "NDT", "NH", "GP", "ĐT"}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}


def fetch_search(keyword: str, timeout: int = 10) -> Optional[str]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(
                CAFEF_SEARCH_URL,
                params={"keywords": keyword},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError as e:
        logger.warning("CafeF search failed for '%s': %s", keyword, e)
        return None


def parse_articles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    articles = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or len(title) < 20:
            continue
        if not href.startswith("http") and not href.startswith("/"):
            continue
        if any(sub in href for sub in ["/tim-kiem", "/quang-cao", "/tag"]):
            continue
        link = href if href.startswith("http") else f"https://cafef.vn{href}"
        articles.append({"title": title, "link": link, "source": "CafeF"})
    return articles


def extract_symbols(title: str, known_symbols: set) -> list[str]:
    candidates = SYMBOL_PATTERN.findall(title.upper())
    return [
        c for c in candidates
        if c in known_symbols and c not in SKIP_WORDS
    ]


def fetch_cafef_news(
    keywords: Optional[list[str]] = None,
    known_symbols: Optional[set] = None,
) -> dict[str, list[dict]]:
    if keywords is None:
        keywords = list(set(KEYWORDS_T3 + KEYWORDS_T6))
    seen_links: set = set()
    all_articles: list[dict] = []
    for kw in keywords:
        html = fetch_search(kw)
        if not html:
            continue
        articles = parse_articles(html)
        for a in articles:
            if a["link"] not in seen_links:
                seen_links.add(a["link"])
                a["matched_keyword"] = kw
                all_articles.append(a)

    logger.info("CafeF proxy: %d unique articles from %d keywords", len(all_articles), len(keywords))
    return all_articles


def map_news_to_symbols(
    articles: list[dict],
    known_symbols: set,
) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for a in articles:
        symbols = extract_symbols(a["title"], known_symbols)
        for sym in symbols:
            result.setdefault(sym, []).append({
                "title": a["title"],
                "link": a["link"],
                "source": a.get("source", "CafeF"),
                "matched_keyword": a.get("matched_keyword", ""),
            })
    return result
