"""crawl_test_data.py — Script scratch để cào và trích xuất dữ liệu BCTC/BCTN làm dữ liệu test.

Cào đa dạng các ngành:
  - HPG (Thép / Công nghiệp nặng)
  - FPT (Công nghệ thông tin)
  - VHM (Bất động sản)
  - VNM (Hàng tiêu dùng)
  - VCB (Ngân hàng / Tài chính)
  - REE (Năng lượng / Cơ điện lạnh)
"""

import os
import json
import asyncio
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any

from app.config.settings import get_settings
from app.infrastructure.knowledge_base.crawlers.vn.cafef_document_crawl import fetch_documents
from app.infrastructure.knowledge_base.crawlers.vn.pdf_parser import async_download_pdf_text

# Cấu hình logging ra console
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("crawl_test_data")

# Danh sách 22 cổ phiếu đại diện cho 22 nhóm ngành khác nhau để xây dựng dữ liệu test diện rộng
TEST_SYMBOLS = [
    "HPG",  # 1. Thép
    "FPT",  # 2. Công nghệ thông tin
    "VHM",  # 3. Bất động sản nhà ở
    "VNM",  # 4. Hàng tiêu dùng / Sữa
    "VCB",  # 5. Ngân hàng
    "REE",  # 6. Cơ điện / Năng lượng hạ tầng
    "PNJ",  # 7. Bán lẻ trang sức
    "MWG",  # 8. Bán lẻ điện máy / bách hóa
    "PVD",  # 9. Dầu khí thượng nguồn (khoan)
    "GAS",  # 10. Dầu khí trung nguồn (khí)
    "PLX",  # 11. Dầu khí hạ nguồn (bán lẻ xăng dầu)
    "DGC",  # 12. Hóa chất / Phốt pho
    "VHC",  # 13. Thủy sản / Cá tra
    "VJC",  # 14. Hàng không
    "GMD",  # 15. Cảng biển & Logistics
    "SZC",  # 16. Bất động sản Khu công nghiệp
    "SSI",  # 17. Chứng khoán
    "BVH",  # 18. Bảo hiểm
    "DBC",  # 19. Nông nghiệp / Chăn nuôi
    "MSH",  # 20. Dệt may
    "BMP",  # 21. Vật liệu xây dựng / Nhựa
    "DHG"   # 22. Dược phẩm / Y tế
]

# Cấu hình thư mục lưu trữ test data
TEST_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "test_data"))

async def crawl_and_save_test_data():
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    logger.info(f"Target directory for test data: {TEST_DATA_DIR}")

    manifest_path = os.path.join(TEST_DATA_DIR, "manifest.json")
    manifest = []
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            logger.info(f"Loaded existing manifest with {len(manifest)} items.")
        except Exception:
            manifest = []

    # Sử dụng httpx client với header thích hợp
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://cafef.vn/",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for symbol in TEST_SYMBOLS:
            logger.info(f"Starting crawl for symbol: {symbol}")
            
            # Cào Type 1 (BCTC), Type 3 (BCTN) và Type 4 (Nghị quyết ĐHĐCĐ & HĐQT)
            doc_types = [1, 3, 4]
            for dtype in doc_types:
                if dtype == 1:
                    doc_type_name = "BCTC"
                elif dtype == 3:
                    doc_type_name = "BCTN"
                else:
                    doc_type_name = "NQ"

                logger.info(f"  Fetching metadata for {symbol} - Type {doc_type_name}")
                
                # Fetch metadata
                docs = await fetch_documents(client, symbol, dtype)
                if not docs:
                    logger.warning(f"  No documents found for {symbol} - {doc_type_name}")
                    continue
                
                # Chỉ lấy 1 tài liệu mới nhất để tránh quá tải dung lượng và thời gian cào
                doc = docs[0]
                url = doc.get("url")
                title = doc.get("title", "")
                
                # Clean up file name
                safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).rstrip()
                safe_title = safe_title.replace(" ", "_")
                filename = f"raw_text_{symbol}_{doc_type_name}_{safe_title}.txt"
                file_path = os.path.join(TEST_DATA_DIR, filename)
                
                # Kiểm tra cache file cục bộ để tăng tốc
                if os.path.exists(file_path) and os.path.getsize(file_path) > 100:
                    logger.info(f"  File already exists, skipping: {filename}")
                    # Bảo đảm có trong manifest
                    if not any(item["local_file"] == filename for item in manifest):
                        manifest.append({
                            "symbol": symbol,
                            "doc_type": "agm_resolution" if dtype == 4 else ("financial_statement" if dtype == 1 else "annual_report"),
                            "title": title,
                            "url": url,
                            "local_file": filename,
                            "char_count": os.path.getsize(file_path),
                            "crawled_at": datetime.now().isoformat()
                        })
                    continue

                logger.info(f"  Downloading and parsing PDF: {title} ({url})")
                text = await async_download_pdf_text(client, url, timeout_sec=60, ocr=False)
                
                if text:
                    # Ghi text thô vào file
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    
                    logger.info(f"  Saved raw text to: {file_path} ({len(text)} chars)")
                    
                    # Cập nhật manifest (xóa item cũ cùng file nếu có)
                    manifest = [item for item in manifest if item["local_file"] != filename]
                    manifest.append({
                        "symbol": symbol,
                        "doc_type": "agm_resolution" if dtype == 4 else ("financial_statement" if dtype == 1 else "annual_report"),
                        "title": title,
                        "url": url,
                        "local_file": filename,
                        "char_count": len(text),
                        "crawled_at": datetime.now().isoformat()
                    })
                else:
                    logger.warning(f"  Failed to extract text from {url}")
                
                # Delay nhẹ giữa các request tránh bị chặn
                await asyncio.sleep(1.0)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        
    logger.info(f"Crawl completed. Manifest saved to: {manifest_path}")

if __name__ == "__main__":
    # Đặt PYTHONPATH trước khi chạy
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    
    asyncio.run(crawl_and_save_test_data())
