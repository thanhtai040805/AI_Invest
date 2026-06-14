import httpx
import logging
import re
import json
import hashlib
from typing import List, Optional, Dict
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
from ..domain.models import NewsArticle
from ..domain.ports import INewsCrawler

logger = logging.getLogger(__name__)

class CafeFCrawler(INewsCrawler):
    BASE_URL = "https://cafef.vn"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async def fetch_latest_links(self, category_url: str) -> List[str]:
        links = set()
        try:
            async with httpx.AsyncClient(headers=self.HEADERS, follow_redirects=True) as client:
                resp = await client.get(category_url, timeout=30.0)
                resp.raise_for_status()
                
                soup = BeautifulSoup(resp.text, "html.parser")
                # Common selector for CafeF category items
                flex_items = soup.select(".tlitem a")
                
                for el in flex_items:
                    href = el.get("href")
                    if href and ".chn" in href:
                        full_url = urljoin(self.BASE_URL, href.strip())
                        links.add(full_url)
                        if len(links) >= 10:
                            break
        except Exception as e:
            logger.error(f"Error fetching links from {category_url}: {e}")
            
        return list(links)[:5]

    async def crawl_article(self, url: str, category: str) -> Optional[NewsArticle]:
        try:
            match = re.search(r'(\d+)\.chn$', url)
            if not match:
                return None
            
            async with httpx.AsyncClient(headers=self.HEADERS, follow_redirects=True) as client:
                resp = await client.get(url, timeout=20.0)
                resp.raise_for_status()
                
                soup = BeautifulSoup(resp.text, "html.parser")
                
                title_tag = soup.select_one("h1.title") or soup.select_one(".title")
                title = title_tag.text.strip() if title_tag else "No Title"

                date_tag = soup.select_one(".pdate")
                publish_date_iso = datetime.utcnow().isoformat() + "Z"
                if date_tag:
                    try:
                        publish_date_iso = datetime.strptime(date_tag.text.strip(), "%d/%m/%Y %H:%M").isoformat() + "Z"
                    except:
                        pass

                content_div = soup.select_one("div.detail-content.afcbc-body")
                structured_content = []

                if content_div:
                    elements = content_div.find_all(['p', 'figure', 'div'], recursive=False)
                    for el in elements:
                        img_tag = el.find("img")
                        if img_tag:
                            img_url = img_tag.get("data-src") or img_tag.get("src")
                            if img_url and "avatar" not in img_url and not img_url.startswith("data:"):
                                structured_content.append({"type": "image", "data": img_url.strip()})
                            continue
                        
                        if el.name == 'p' and el.text.strip():
                            p_parts = self._parse_paragraph(el)
                            if p_parts:
                                structured_content.append({"type": "text", "data": p_parts})
                        elif el.name == 'div' and el.text.strip() and len(el.find_all()) == 0:
                            structured_content.append({"type": "text", "data": [{"type": "text", "text": el.text.strip()}]})

                return NewsArticle(
                    newsId=hashlib.md5(url.encode()).hexdigest(),
                    symbol="GENERAL",
                    title=title,
                    url=url,
                    content=json.dumps(structured_content, ensure_ascii=False),
                    publishDate=publish_date_iso,
                    friendlyKeyword=category
                )
        except Exception as e:
            logger.error(f"Error crawling article {url}: {e}")
            return None

    def _parse_paragraph(self, p_tag) -> List[Dict]:
        parts = []
        for child in p_tag.contents:
            if child.name == 'a':
                href = child.get("href", "")
                text = child.get_text()
                if href.startswith("http") and text.strip() and not any(ext in href.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.avif']):
                    parts.append({"type": "link", "text": text, "url": href.strip()})
                elif text.strip():
                    parts.append({"type": "text", "text": text})
            else:
                text = str(child)
                if text.strip():
                    parts.append({"type": "text", "text": text})
        return parts
