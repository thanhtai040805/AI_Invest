"""
pdf_parser.py — PDF downloading and extraction utilities (with page-by-page Hybrid Visual OCR).

Features:
1. Page-by-page inspection: text pages use digital pdfplumber (instant). Scanned/empty pages use OCR.
2. Hybrid OCR: Evomap Gemini Vision API for core financial table pages (pages 5 to 10), Tesseract for text-heavy pages.
3. Memory safety: batch processing (5 pages at a time) with active gc.collect() to prevent OOM.
4. Non-blocking: all OCR and API calls run asynchronously in threadpools via run_in_executor().
"""

import asyncio
import io
import logging
import os
import gc
from dotenv import load_dotenv, find_dotenv

# Tự động load .env từ thư mục gốc của dự án
load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)

_POPPLER_PATH: str | None = None

def _get_poppler_path() -> str | None:
    global _POPPLER_PATH
    if _POPPLER_PATH is not None:
        return _POPPLER_PATH

    candidates = [
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
            r"\oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
            r"\poppler-25.07.0\Library\bin"
        ),
        r"C:\Program Files\poppler\Library\bin",
        r"C:\Program Files\poppler\bin",
    ]
    for p in candidates:
        if os.path.isfile(os.path.join(p, "pdftoppm.exe")):
            _POPPLER_PATH = p
            return p
    return None


def _tesseract_ocr_image(img) -> str:
    """Chạy Tesseract OCR cục bộ trên một ảnh PIL (chạy đồng bộ)."""
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        return pytesseract.image_to_string(img, lang="vie+eng").strip()
    except Exception as e:
        logger.debug(f"Tesseract page failed: {e}")
        return ""


async def _evomap_ocr_image_vision(img, page_num: int) -> str:
    """Gọi Evomap Gemini Vision API để chuyển đổi trang quét chứa bảng thành Markdown Table."""
    try:
        from openai import OpenAI
        import base64
        
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        base64_image = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')

        evomap_api_key = os.getenv("EVOMAP_API_KEY")
        if not evomap_api_key:
            return ""

        client = OpenAI(
            base_url="https://api.evomap.ai/v1",
            api_key=evomap_api_key
        )
        
        logger.info(f"    Evomap Vision API: Requesting table extraction for page {page_num}...")
        
        # Gọi API trong threadpool để tránh chặn event loop của Python
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.chat.completions.create(
                model="evomap-gemini-3.1-pro-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text", 
                                "text": "Hãy đọc bảng số liệu tài chính trong ảnh này và chuyển đổi chính xác thành cấu trúc bảng Markdown tiếng Việt (gồm các cột: Tài sản / Chỉ tiêu, Mã số, Thuyết minh, Số cuối kỳ, Số đầu năm). Giữ nguyên cấu trúc dòng cột, không bị lệch hàng. Trả về duy nhất bảng Markdown, không có lời thoại."
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                temperature=0.0
            )
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"Evomap Vision failed on page {page_num}: {e}")
        return ""


def _gdrive_ocr_sync(pdf_content: bytes) -> str:
    """Upload PDF to Google Drive → OCR via Google Docs → return extracted text.
    
    Runs synchronously (Google API client is not async). Must be called
    from a threadpool executor when used in async context.
    Returns empty string on failure (no credentials, network error, etc.).
    """
    try:
        import os
        import tempfile
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
    except ImportError:
        logger.debug("Google Drive OCR dependencies not installed")
        return ""

    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds_path = os.path.expanduser("~/.config/google_drive/credentials.json")
    token_path = os.path.join(tempfile.gettempdir(), '_gdrive_token.json')

    if not os.path.exists(creds_path):
        logger.debug("Google Drive credentials not found at %s", creds_path)
        return ""

    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, 'w') as f:
                    f.write(creds.to_json())
        except Exception as e:
            logger.debug("Google Drive token error: %s", e)

    if not creds or not creds.valid:
        logger.debug("Google Drive token invalid and cannot refresh headlessly")
        return ""

    try:
        http = httplib2.Http(timeout=120)
        authed_http = AuthorizedHttp(creds, http=http)
        service = build('drive', 'v3', http=authed_http)

        fd, pdf_path = tempfile.mkstemp(suffix='.pdf')
        os.write(fd, pdf_content)
        os.close(fd)

        try:
            media = MediaFileUpload(pdf_path, mimetype='application/pdf', resumable=False)
            gdoc = service.files().create(
                media_body=media,
                body={'name': '_ocr_temp', 'mimeType': 'application/vnd.google-apps.document'},
                fields='id'
            ).execute()
            file_id = gdoc.get('id')

            resp = service.files().export(fileId=file_id, mimeType='text/plain').execute()
            text = resp.decode('utf-8') if isinstance(resp, bytes) else resp

            service.files().delete(fileId=file_id).execute()
            return text.strip()
        finally:
            try:
                os.unlink(pdf_path)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Google Drive OCR failed: %s", e)
        return ""


async def async_download_pdf_text(
    client,
    pdf_url: str,
    timeout_sec: int = 60,
    ocr: bool = True,
    ocr_max_pages: int = 35,
) -> str:
    """Download a PDF and extract text. Three-layer strategy:

    Layer 1 — pdfplumber (instant, free): if > 50% pages have digital text layer.
    Layer 2 — Google Drive OCR (free, ~15s): for mostly scanned PDFs (BCTC, Nghị quyết).
    Layer 3 — Evomap Vision + Tesseract fallback (page-by-page).
    """
    try:
        resp = await client.get(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AIInvest/1.0)"},
            timeout=timeout_sec,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.debug("PDF download failed [%s]: %s", pdf_url[-60:], e)
        return ""

    if not resp.content.startswith(b"%PDF-"):
        logger.debug("Response is not a PDF: %s", pdf_url[-60:])
        return ""

    content = resp.content
    poppler = _get_poppler_path()
    evomap_enabled = bool(os.getenv("EVOMAP_API_KEY"))

    # Layer 1: Try pdfplumber first
    import pdfplumber
    pages_to_process = []
    
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            total_pages = len(pdf.pages)
            limit_pages = min(total_pages, ocr_max_pages)
            
            for p_idx in range(limit_pages):
                page = pdf.pages[p_idx]
                page_num = p_idx + 1
                page_text = (page.extract_text() or "").strip()
                
                if len(page_text) > 50:
                    pages_to_process.append({
                        "page_num": page_num,
                        "type": "digital",
                        "text": page_text
                    })
                else:
                    pages_to_process.append({
                        "page_num": page_num,
                        "type": "scanned",
                        "text": ""
                    })
    except Exception as e:
        logger.warning(f"Failed to inspect PDF pages: {e}")
        return ""

    scanned_count = sum(1 for p in pages_to_process if p["type"] == "scanned")
    pct_scanned = scanned_count / len(pages_to_process) if pages_to_process else 0

    # If fully digital text → return immediately
    if scanned_count == 0:
        logger.info("  PDF has full text layer. Instant extraction.")
        return "\n\n---\n\n".join(p["text"] for p in pages_to_process)

    logger.info(f"  PDF: {len(pages_to_process)} pages, {scanned_count} scanned ({pct_scanned:.0%})")

    # Layer 2: If mostly scanned, try Google Drive OCR (handles entire doc in one shot)
    if pct_scanned > 0.5 and ocr:
        logger.info("  Attempting Google Drive OCR (whole document)...")
        loop = asyncio.get_event_loop()
        gdrive_text = await loop.run_in_executor(None, _gdrive_ocr_sync, content)
        if gdrive_text and len(gdrive_text) > 100:
            logger.info("  Google Drive OCR succeeded: %d chars", len(gdrive_text))
            return gdrive_text
        logger.info("  Google Drive OCR skipped/failed, falling back to page-by-page OCR")

    # Layer 3: Fallback to page-by-page Evomap Vision + Tesseract
    logger.info("  Running page-by-page OCR fallback...")
    table_pages = {5, 6, 7, 8, 9, 10}
    final_parts = []
    
    batch_size = 5
    from pdf2image import convert_from_bytes
    
    for start_page in range(1, limit_pages + 1, batch_size):
        end_page = min(start_page + batch_size - 1, limit_pages)
        
        batch_items = pages_to_process[start_page - 1:end_page]
        needs_render = any(item["type"] == "scanned" for item in batch_items)
        
        if not needs_render:
            for item in batch_items:
                final_parts.append(f"\n\n--- [TRANG {item['page_num']} - DIGITAL TEXT] ---\n\n{item['text']}")
            continue

        try:
            images = convert_from_bytes(
                content,
                first_page=start_page,
                last_page=end_page,
                dpi=150,
                poppler_path=poppler,
                thread_count=2,
            )
            
            for idx, item in enumerate(batch_items):
                page_num = item["page_num"]
                
                if item["type"] == "digital":
                    final_parts.append(f"\n\n--- [TRANG {page_num} - DIGITAL TEXT] ---\n\n{item['text']}")
                    continue
                
                img = images[idx]
                
                if page_num in table_pages and evomap_enabled:
                    table_text = await _evomap_ocr_image_vision(img, page_num)
                    if table_text:
                        final_parts.append(f"\n\n--- [TRANG {page_num} - BẢNG SỐ TRÍCH XUẤT VISION] ---\n\n{table_text}")
                        continue
                
                logger.info(f"      Tesseract OCR: Page {page_num}...")
                loop = asyncio.get_event_loop()
                ocr_text = await loop.run_in_executor(None, lambda: _tesseract_ocr_image(img))
                if ocr_text:
                    final_parts.append(f"\n\n--- [TRANG {page_num} - VĂN BẢN OCR] ---\n\n{ocr_text}")
            
            del images
            gc.collect()
            
        except Exception as e:
            logger.warning(f"Error processing batch {start_page}-{end_page}: {e}")
            for item in batch_items:
                if item["text"]:
                    final_parts.append(item["text"])

    final_text = "\n\n".join(final_parts)
    if final_text.strip():
        logger.info(f"  Page-by-page OCR completed: {len(final_text)} chars")
        return final_text
    
    return "[SCANNED_PDF]"


def download_pdf_text(pdf_url: str, timeout_sec: int = 30, ocr: bool = True) -> str:
    """Sync version — download a PDF and extract text. Falls back to OCR for scanned PDFs."""
    try:
        import httpx
        resp = httpx.get(pdf_url, headers={
            "User-Agent": "Mozilla/5.0",
        }, timeout=timeout_sec, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.debug("PDF download failed [%s]: %s", pdf_url[-50:], e)
        return ""

    if not resp.content.startswith(b"%PDF-"):
        return ""

    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            if len(pdf.pages) > 0:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception:
        pass

    if not text:
        try:
            from pdfminer.high_level import extract_text
            text = extract_text(io.BytesIO(resp.content)).strip()
        except Exception:
            pass

    if not text and ocr:
        # Sync version falls back to standard Tesseract OCR
        poppler = _get_poppler_path()
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(resp.content, first_page=1, last_page=8, dpi=150, poppler_path=poppler)
            ocr_parts = [_tesseract_ocr_image(img) for img in images]
            ocr_text = "\n\n---\n\n".join(ocr_parts)
            if ocr_text.strip():
                return ocr_text
        except Exception:
            pass

    if not text:
        return "[SCANNED_PDF]"

    return text

