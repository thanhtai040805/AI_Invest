from datetime import datetime, timezone
import time
import logging
import json
import re
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# Cấu hình logging để theo dõi trạng thái cào dữ liệu
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Headers giả lập trình duyệt để tránh bị chặn chặn dòng tiền dữ liệu
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
}

# Danh sách các chuyên mục cần cào
CAFEF_CATEGORIES = {
    "thi_truong_chung_khoan": "https://cafef.vn/thi-truong-chung-khoan.chn",
    "bat_dong_san":           "https://cafef.vn/bat-dong-san.chn",
    "doanh_nghiep":           "https://cafef.vn/doanh-nghiep.chn",
    "tai_chinh_ngan_hang":    "https://cafef.vn/tai-chinh-ngan-hang.chn",
    "tai_chinh_quoc_te":      "https://cafef.vn/tai-chinh-quoc-te.chn",
    "vi_mo_dau_tu":           "https://cafef.vn/vi-mo-dau-tu.chn",
    "thi_truong":             "https://cafef.vn/thi-truong.chn",
}

SCROLL_TIMES = 3  # Số lần cuộn trang để tải thêm tin cũ hơn


def get_links_by_scrolling(page, url, cat_name):
    """
    Cuộn trang tự động bằng Playwright để load thêm bài viết và thu thập các URL.
    """
    links = set()
    base_url = "https://cafef.vn"
    try:
        logging.info(f"[{cat_name.upper()}] Đang mở danh mục: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        for i in range(1, SCROLL_TIMES + 1):
            logging.info(f"   -> Đang cuộn chuột thực tế lần {i}/{SCROLL_TIMES}...")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        
        # Chọn tất cả các thẻ a chứa link bài viết từ layout khác nhau của CaféF
        flex_items = soup.select(".tlitem h3 a, .tlitem h2 a, .tlitem-flex h3 a, .tlitem-flex h2 a, .box-tin-noi-bat h2 a")
                
        for el in flex_items:
            href = el.get("href")
            if href and ".chn" in href:
                full_url = urljoin(base_url, href.strip())
                links.add(full_url)
    except Exception as e:
        logging.error(f"Lỗi cuộn chuột tại {cat_name}: {str(e)}")
        
    return list(links)  # Đảm bảo trả về list chuẩn để lặp tuần tự


def parse_paragraph_with_links(p_tag):
    """
    Phân tách nội dung của một thẻ <p> thành một danh sách các node text và link xen kẽ nhau.
    """
    parts = []
    for child in p_tag.contents:
        if child.name == 'a':
            href = child.get("href", "")
            text = child.get_text()
            
            # Nếu là link điều hướng nội bộ hoặc bên ngoài hợp lệ
            if href.startswith("http") and text.strip() and not any(ext in href.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.avif']):
                parts.append({
                    "type": "link",
                    "text": text,
                    "url": href.strip()
                })
            else:
                # Nếu là link ảnh bọc rác thì chuyển nó về dạng text thường
                if text.strip():
                    parts.append({"type": "text", "text": text})
        else:
            text = str(child)
            if text.strip():
                parts.append({
                    "type": "text",
                    "text": text
                })
                
    return parts


def extract_article_content(page, url):
    """
    Bóc tách chi tiết bài viết, bóc tách chính xác newsId từ URL.
    """
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
        
        # Lấy tiêu đề bài viết
        title_tag = soup.select_one("h1.title") or soup.select_one(".title")
        title = title_tag.text.strip() if title_tag else "Không tìm thấy tiêu đề"
        
        # Định dạng thời gian thành chuẩn ISO định dạng UTC (Z) phù hợp Prisma/Zod
        publish_date_iso = None
        date_tag = soup.select_one(".pdate")
        if date_tag:
            date_str = date_tag.text.strip()
            try:
                publish_date_iso = datetime.strptime(date_str, "%d/%m/%Y %H:%M").isoformat() + "Z"
            except:
                publish_date_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            publish_date_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Bóc danh mục phụ hiển thị trong bài viết
        cat_tag = soup.select_one(".bc_title a:not(.last)")
        category = cat_tag.text.strip() if cat_tag else "Không xác định"
        
        content_div = soup.select_one("div.detail-content.afcbc-body")
        structured_content = []
        
        if content_div:
            elements = content_div.find_all(['p', 'figure', 'div'], recursive=False)
            
            for el in elements:
                # 1. XỬ LÝ KHỐI ẢNH
                img_tag = el.find("img")
                if img_tag:
                    img_url = img_tag.get("data-src") or img_tag.get("src")
                    if img_url and "avatar" not in img_url and not img_url.startswith("data:"):
                        structured_content.append({
                            "type": "image",
                            "data": img_url.strip()
                        })
                    continue
                
                # 2. XỬ LÝ ĐOẠN VĂN BẢN <p>
                if el.name == 'p' and el.text.strip():
                    p_parts = parse_paragraph_with_links(el)
                    if p_parts:
                        structured_content.append({
                            "type": "text",
                            "data": p_parts
                        })
                        
                # 3. XỬ LÝ KHỐI TEXT PHỤ TRONG THẺ <div> KHÔNG CHỨA ẢNH
                elif el.name == 'div' and el.text.strip():
                    if len(el.find_all()) == 0:
                        structured_content.append({
                            "type": "text",
                            "data": [{"type": "text", "text": el.text.strip()}]
                        })

        # Ép mảng content có cấu trúc thành chuỗi chuỗi JSON string duy nhất theo mong muốn của DB
        content_json_string = json.dumps(structured_content, ensure_ascii=False)
            
        return {
            "newsId": news_id,
            "symbol": "GENERAL",
            "title": title,
            "url": url,
            "content": content_json_string,
            "friendlyKeyword": category,
            "publishDate": publish_date_iso
        }
        
    except Exception as e:
        logging.error(f"Lỗi bóc tách nội dung tại [{url}]: {str(e)}")
        return None


def main():
    final_output_data = []
    
    with sync_playwright() as p:
        # Khởi chạy trình duyệt ẩn danh
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        
        for cat_name, url in CAFEF_CATEGORIES.items():
            logging.info(f"\n================ BẮT ĐẦU CHUYÊN MỤC: {cat_name.upper()} ================")
            
            # Thu thập các link từ trang chuyên mục
            article_links = get_links_by_scrolling(page, url, cat_name)
            logging.info(f"==> Tổng cộng [{cat_name.upper()}] gom được {len(article_links)} bài viết.")
            
            # Duyệt qua từng link bài viết và trích xuất dữ liệu chi tiết
            for idx, link in enumerate(article_links, 1):
                logging.info(f"-> Đang xử lý ({idx}/{len(article_links)}): {link}")
                
                data = extract_article_content(page, link)
                if data:
                    data["friendlyKeyword"] = cat_name  # Đảm bảo giữ đúng key phân loại hệ thống
                    final_output_data.append(data)
                    print(f"   [OK] Đã cấu trúc bài viết mang ID [{data['newsId']}]: {data['title'][:40]}...")
                
                time.sleep(1) # Nghỉ 1 giây để tránh làm quá tải server nguồn
                
            logging.info(f"================ HOÀN THÀNH CHUYÊN MỤC: {cat_name.upper()} ================\n")
            
        browser.close()

    # Xuất toàn bộ mảng dữ liệu sạch ra tệp JSON
    output_filename = "cafef_data_result.json"
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(final_output_data, f, ensure_ascii=False, indent=4)
        
    logging.info(f"\n[THÀNH CÔNG] Toàn bộ dữ liệu {len(final_output_data)} bài viết đã chuẩn hóa 100% theo Schema và ghi vào '{output_filename}'")


if __name__ == "__main__":
    main()