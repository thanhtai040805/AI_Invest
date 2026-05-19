import asyncio
import logging
import httpx
import os
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import time
import re
from app.services.sentiment_scorer import sentiment_scorer
from app.services.news_rag import news_rag_svc
from app.services.market_data_service import market_data_svc
from app.services.ai_service import ai_svc

logger = logging.getLogger("ai_engine.news_ingestion")

CAFEF_CATEGORIES = {
    "thi_truong_chung_khoan": "https://cafef.vn/thi-truong-chung-khoan.chn",
    "bat_dong_san":           "https://cafef.vn/bat-dong-san.chn",
    "doanh_nghiep":           "https://cafef.vn/doanh-nghiep.chn",
    "tai_chinh_ngan_hang":    "https://cafef.vn/tai-chinh-ngan-hang.chn",
    "tai_chinh_quoc_te":      "https://cafef.vn/tai-chinh-quoc-te.chn",
    "vi_mo_dau_tu":           "https://cafef.vn/vi-mo-dau-tu.chn",
    "thi_truong":             "https://cafef.vn/thi-truong.chn",
}

BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:3001/api/v1/community/news/ingest")
BACKEND_BOT_POST_URL = os.getenv("BACKEND_BOT_POST_URL", "http://localhost:3001/api/v1/community/bot/posts")


class NewsIngestionService:
    def __init__(self):
        self._running = False
        self._task = None
        self.last_premarket_date: Optional[str] = None
        self.last_eod_date: Optional[str] = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._scan_loop())
            logger.info("News ingestion service started.")

    def stop(self):
        if self._running:
            self._running = False
            if self._task:
                self._task.cancel()
            logger.info("News ingestion service stopped.")

    async def _scan_loop(self):
        while self._running:
            await asyncio.sleep(900)
            await self._check_and_run_scheduled_reports()
            await self._run_scan()

    async def _run_scan(self):
        logger.info("Starting scheduled daily news scan (Cafef - no scroll, top 5)...")
        all_news_payload = []

        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                page = context.new_page()

                for cat_name, url in CAFEF_CATEGORIES.items():
                    try:
                        logger.info(f"Fetching top 5 from category: {cat_name}")
                        article_links = self._get_top_5_links(page, url, cat_name)
                        logger.info(f"  {cat_name}: got {len(article_links)} links")

                        for link in article_links:
                            news_id = hashlib.md5(link.encode()).hexdigest()

                            if news_rag_svc.has_article(news_id):
                                continue

                            crawled_article = self._crawl_article_content(page, link, cat_name)
                            if crawled_article:
                                crawled_article['newsId'] = news_id

                                sentiment_res = sentiment_scorer.analyze(
                                    crawled_article['title'] + " " + (crawled_article.get('content') or "")
                                )
                                crawled_article['sentimentLabel'] = sentiment_res['label']
                                crawled_article['sentimentScore'] = sentiment_res['score']

                                all_news_payload.append(crawled_article)
                                logger.info(f"    Added: {crawled_article['title'][:50]}...")

                            await asyncio.sleep(1.5)

                    except Exception as e:
                        logger.error(f"Error processing category {cat_name}: {e}")

                browser.close()

        except Exception as e:
            logger.error(f"Failed to launch Playwright: {e}")

        if all_news_payload:
            news_rag_svc.add_articles(all_news_payload)
            await self._send_to_backend(all_news_payload)
            logger.info(f"Finished scheduled news scan. Sent {len(all_news_payload)} new items.")
        else:
            logger.info("Finished scheduled news scan. No new items found.")

    def _get_top_5_links(self, page, url: str, cat_name: str) -> List[str]:
        links = set()
        base_url = "https://cafef.vn"

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            flex_items = soup.select(".tlitem a")
            
            count = 0
            for el in flex_items:
                if count >= 5:
                    break
                href = el.get("href")
                if href and ".chn" in href:
                    full_url = urljoin(base_url, href.strip())
                    links.add(full_url)
                    count += 1

        except Exception as e:
            logger.error(f"Error fetching links from {cat_name}: {e}")

        return list(links)[:5]

    def parse_paragraph_with_links(p_tag):
        """
        Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen kẽ nhau.
        """
        parts = []
        
        # Duyệt qua từng phần tử con (chữ thường hoặc thẻ <a>) bên trong thẻ <p>
        for child in p_tag.contents:
            if child.name == 'a':
                href = child.get("href", "")
                text = child.get_text()
                
                # Nếu là link điều hướng hợp lệ (không phải link ảnh bọc)
                if href.startswith("http") and text.strip() and not any(ext in href.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.avif']):
                    parts.append({
                        "type": "link",
                        "text": text,
                        "url": href.strip()
                    })
                else:
                    # Nếu là link ảnh bọc rác thì hạ cấp nó về text thường
                    if text.strip():
                        parts.append({"type": "text", "text": text})
            else:
                # Là chuỗi văn bản thường (NavigableString)
                text = str(child)
                if text.strip():
                    parts.append({
                        "type": "text",
                        "text": text
                    })
        return parts

    def _crawl_article_content(self, page, url: str, category: str) -> Optional[Dict]:
        try:
            # Sử dụng Regex bóc newsId trực tiếp từ đuôi URL của CaféF (Ví dụ: ...188260518200410614.chn)
            match = re.search(r'(\d+)\.chn$', url)
            if not match:
                logging.warning(f"Không thể trích xuất ID từ URL bài viết này: {url}")
                return None
            
            news_id = match.group(1) # Lưu chuỗi số ID độc nhất này lại
            
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            title_tag = soup.select_one("h1.title") or soup.select_one(".title")
            title = title_tag.text.strip() if title_tag else "Không có tiêu đề"

            date_tag = soup.select_one(".pdate")
            publish_date_iso = None
            if date_tag:
                date_str = date_tag.text.strip()
                try:
                    publish_date_iso = datetime.strptime(date_str, "%d/%m/%Y %H:%M").isoformat() + "Z"
                except:
                    publish_date_iso = datetime.utcnow().isoformat() + "Z"
            else:
                publish_date_iso = datetime.utcnow().isoformat() + "Z"

            content_div = soup.select_one("div.detail-content.afcbc-body")
            structured_content = []

            if content_div:
                # Lấy tất cả các tag con trực tiếp nhằm bảo toàn thứ tự từ trên xuống
                elements = content_div.find_all(['p', 'figure', 'div'], recursive=False)
                
                for el in elements:
                    # 1. XỬ LÝ KHỐI ẢNH (figure hoặc div chứa img)
                    img_tag = el.find("img")
                    if img_tag:
                        img_url = img_tag.get("data-src") or img_tag.get("src")
                        if img_url and "avatar" not in img_url and not img_url.startswith("data:"):
                            structured_content.append({
                                "type": "image",
                            "data": img_url.strip()
                        })
                    continue
                
                # 2. XỬ LÝ KHỐI VĂN BẢN (thẻ <p>) - Có bóc tách link inline lồng bên trong
                if el.name == 'p' and el.text.strip():
                    p_parts = self.parse_paragraph_with_links(el)
                    if p_parts:
                        structured_content.append({
                            "type": "text",
                            "data": p_parts  # Gán mảng các mảnh text/link vào đây
                        })
                        
                # 3. XỬ LÝ KHỐI ĐOẠN CHỮ PHỤ (thẻ <div> không chứa ảnh)
                elif el.name == 'div' and el.text.strip():
                    if len(el.find_all()) == 0:
                        structured_content.append({
                            "type": "text",
                            "data": [{"type": "text", "text": el.text.strip()}]
                        })

            # Chuỗi hóa toàn bộ mảng hỗn hợp này thành một String JSON duy nhất
            content_json_string = json.dumps(structured_content, ensure_ascii=False)    

            return {
                "newsId": news_id,
                "symbol": "GENERAL",
                "title": title,
                "url": url,
                "content": content_json_string,
                "publishDate": publish_date_iso,
                "friendlyKeyword": category,
            }

        except Exception as e:
            logger.error(f"Error crawling article {url}: {e}")
            return None

    async def _send_to_backend(self, payload: list):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(BACKEND_API_URL, json=payload, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"Backend ingestion response: {data}")
                    newly_inserted = data.get("newlyInserted", [])
                    if newly_inserted:
                        logger.info(f"Triggering AI analysis for {len(newly_inserted)} new articles.")
                        asyncio.create_task(self._analyze_and_post(newly_inserted))
                else:
                    logger.error(f"Backend ingestion failed with status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send news to backend: {e}")

    async def _analyze_and_post(self, news_items: list):
        for item in news_items:
            try:
                symbol = item.get("symbol", "GENERAL")
                title = item.get("title")
                url = item.get("url")
                sentiment_label = item.get("sentimentLabel", "NEUTRAL")

                if symbol != "GENERAL":
                    quote = await market_data_svc.get_quote(symbol)
                else:
                    quote = {}

                rag_results = news_rag_svc.query(title, symbol=symbol if symbol != "GENERAL" else None, top_k=3)
                rag_context = ""
                if rag_results:
                    rag_context = "\n".join([f"- {a.get('publishDate')}: {a.get('title')} (Sentiment: {a.get('sentimentLabel')})" for a in rag_results])

                prompt = f"""
Bạn là một Giám đốc phân tích định lượng và cơ bản cứng rắn. Cấm sử dụng các cụm từ sáo rỗng như 'có thể', 'cần theo dõi thêm', 'tùy thuộc vào thị trường'. Bắt buộc đưa ra kết luận: Tích cực, Tiêu cực hay Trung lập.

[TIN TỨC MỚI NHẤT]
Mã CP: {symbol}
Tiêu đề: {title}
Sentiment sơ bộ: {sentiment_label}
Link: {url}

[DỮ LIỆU THỊ TRƯỜNG HIỆN TẠI]
Giá: {quote.get('price')}
Biến động: {quote.get('changePercent')}%
Khối lượng: {quote.get('volume')}

[TIN TỨC LIÊN QUAN TRONG QUÁ KHỨ (RAG CONTEXT)]
{rag_context if rag_context else "Không có tin tức liên quan trong quá khứ."}

Vui lòng phân tích dựa trên sự kiện này và xuất ra bản nhận định ngắn gọn theo cấu trúc chính xác sau:
- Mức độ tác động: [1 đến 10]
- Phe nào đang kiểm soát: [Bò / Gấu]
- Hành động giá dự kiến: [Ghi rõ mốc Kháng cự / Hỗ trợ]
- Rủi ro phản chứng: [Trường hợp nào nhận định sai?]

(Lưu ý: Không giải thích thêm, chỉ output đúng cấu trúc trên)
"""
                ai_response = ai_svc._generate_analysis(prompt, {"indices": []})

                post_payload = {
                    "content": f"🚨 **Tin Tức: {symbol}** 🚨\n\n**Tiêu đề**: {title}\n\n**Sentiment**: {sentiment_label}\n\n**Nhận định AI**:\n{ai_response}",
                    "taggedSymbols": [symbol] if symbol != "GENERAL" else ["VNINDEX"]
                }

                await self._post_to_community(post_payload)
            except Exception as e:
                logger.error(f"AI analysis failed for {item.get('title', 'unknown')}: {e}")

    async def _check_and_run_scheduled_reports(self):
        vn_tz = timezone(timedelta(hours=7))
        now_vn = datetime.now(timezone.utc).astimezone(vn_tz)
        today_str = now_vn.strftime("%Y-%m-%d")

        if now_vn.hour == 8 and now_vn.minute >= 30:
            if self.last_premarket_date != today_str:
                self.last_premarket_date = today_str
                asyncio.create_task(self._generate_premarket_report())

        if now_vn.hour == 15 and now_vn.minute >= 15:
            if self.last_eod_date != today_str:
                self.last_eod_date = today_str
                asyncio.create_task(self._generate_eod_report())

    async def _generate_premarket_report(self):
        logger.info("Generating Pre-market outlook report...")
        try:
            indices_data = await market_data_svc.get_indices()
            indices_text = "\n".join([f"- {idx.get('name')}: {idx.get('value')} ({idx.get('changePercent'):+.2f}%)" for idx in indices_data.get("indices", [])])

            prompt = f"""
Bạn là một Giám đốc phân tích định lượng chuyên nghiệp. Hãy tạo một bản tin nhận định "Bản Tin Trước Giờ Mở Cửa" cho thị trường chứng khoán Việt Nam hôm nay.
Sử dụng dữ liệu đóng cửa phiên trước:
{indices_text}

Bản tin phải có 3 phần:
1. **Toàn Cảnh Phiên Trước**: Tóm tắt biến động chỉ số.
2. **Kịch Bản Giao Dịch Hôm Nay**: Dự kiến biên độ biến động VN-INDEX, kháng cự và hỗ trợ.
3. **Khuyến Nghị Hành Động**: Rõ ràng và kiên quyết cho nhà đầu tư (Không nói nước đôi).
"""
            ai_response = ai_svc._generate_analysis(prompt, {"indices": []})

            post_payload = {
                "content": f"🌅 **BẢN TIN TRƯỚC GIỜ MỞ CỬA** 🌅\n\n{ai_response}",
                "taggedSymbols": ["VNINDEX"]
            }
            await self._post_to_community(post_payload)
            logger.info("Pre-market outlook report posted successfully.")
        except Exception as e:
            logger.error(f"Failed to generate Pre-market report: {e}")

    async def _generate_eod_report(self):
        logger.info("Generating End-of-Day recap report...")
        try:
            indices_data = await market_data_svc.get_indices()
            indices_text = "\n".join([f"- {idx.get('name')}: {idx.get('value')} ({idx.get('changePercent'):+.2f}%)" for idx in indices_data.get("indices", [])])

            prompt = f"""
Bạn là một Giám đốc phân tích định lượng chuyên nghiệp. Hãy tạo một bản tin nhận định "Tổng Kết Phiên Giao Dịch & Nhận Định Phiên Kế Tiếp" cho thị trường chứng khoán Việt Nam.
Dữ liệu chỉ số đóng cửa hôm nay:
{indices_text}

Bản tin phải có 3 phần:
1. **Tổng Quan Phiên Giao Dịch**: Diễn biến chính trong ngày.
2. **Dòng Tiền & Điểm Sáng**: Nhóm ngành dẫn dắt nổi bật.
3. **Dự Báo & Chiến Lược Phiên Mai**: Nhận định xu hướng và hỗ trợ/kháng cự kèm khuyến nghị cụ thể.
"""
            ai_response = ai_svc._generate_analysis(prompt, {"indices": []})

            post_payload = {
                "content": f"📊 **TỔNG KẾT PHIÊN GIAO DỊCH & XU HƯỚNG MAI** 📊\n\n{ai_response}",
                "taggedSymbols": ["VNINDEX"]
            }
            await self._post_to_community(post_payload)
            logger.info("End-of-Day recap report posted successfully.")
        except Exception as e:
            logger.error(f"Failed to generate EOD report: {e}")

    async def _post_to_community(self, payload: dict):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    BACKEND_BOT_POST_URL,
                    json=payload,
                    headers={"Authorization": "Bearer AI_BOT_SECRET_KEY"},
                    timeout=30.0
                )
                if resp.status_code == 201:
                    logger.info("Successfully posted to Community.")
                else:
                    logger.error(f"Failed to post to Community: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Failed to call Community API: {e}")


_news_service = NewsIngestionService()


def get_news_ingestion_service() -> NewsIngestionService:
    return _news_service