"""
news_utils.py — Shared extraction utilities for news crawlers.

Provides:
  - extract_article_data(html, base_url) -> dict (content, images, pdf_urls)
  - download_pdf_text(url) -> str  (text from PDF via pdfminer)
  - batch_has_content(articles) -> list[bool] (pre-check which already have content)
  - upsert_article(...) -> bool
"""
import logging, io, os, re, time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

# ── Content extraction ──────────────────────────────────────────────

CONTENT_SELECTORS = [
    "#vst_detail", "#mainDetailV2", "#newscontent",
    "[id$=divContent]", "[id$=ucStockNewsDetail1_divContent]",
    "#page-content", ".article-content", ".detail-content",
]

# Skip UI/noise images by matching known static icon paths
_UI_IMAGE_RE = re.compile(
    r"/(static|common|Content|theme20nam|icon|avatar|logo)"
    r"|\.(gif|svg)$",
    re.IGNORECASE,
)

PDF_URL_RE = re.compile(r'<a\s[^>]*href=["\']([^"\']+\.pdf)["\']', re.IGNORECASE)
IMG_URL_RE = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
PDF_LINK_TEXT_RE = re.compile(
    r'<a\s[^>]*href=["\'][^"\']+\.pdf["\'][^>]*>([^<]+)</a>',
    re.IGNORECASE,
)

PUBLISH_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4}),?\s*(\d{2}:\d{2})")

# Common stock exchange logos / chart icons to exclude
_UI_DOMAIN_RE = re.compile(
    r"(static|common\.mediacdn|image\.vietstock\.vn/common/)",
    re.IGNORECASE,
)


def _is_content_image(src: str) -> bool:
    """Filter out UI icons, logos, and static assets."""
    if any(x in src for x in ("static/", "/icon", "avatar", "logo", "btdown.gif",
                               "chart.gif", "icon_chart", "fb-message", "icon2a")):
        return False
    if src.endswith(".svg"):
        return False
    return True


def _extract_container_html(tree, selectors: list[str]):
    """Find the first content container element matching selectors. Returns (el, html)."""
    for sel in selectors:
        el = tree.css_first(sel)
        if el:
            return el, el.html or ""
    return None, ""


def extract_article_data(html: str, base_url="") -> dict:
    """Extract content text, image URLs, and PDF links from article HTML.

    Images and PDFs are scoped to the content container (not the whole page)
    to avoid UI noise.

    Returns: {"content": str, "images": list[str], "pdf_urls": list[str],
              "published_date": datetime|None}
    """
    tree = HTMLParser(html)
    base = str(base_url) if not isinstance(base_url, str) else base_url

    # Find content container and scope extraction to it
    container_el, container_html = _extract_container_html(tree, CONTENT_SELECTORS)

    # ── Content text with paragraph separation ──
    content = ""
    if container_el:
        content = container_el.text(separator="\n", strip=True)
        # Fix: inline elements (<strong>, <span>) cause mid-sentence \n breaks
        content = re.sub(r'(?<=[a-zA-Z0-9,;])\n(?=[a-zA-Z0-9])', ' ', content)
        content = re.sub(r'\n{3,}', '\n\n', content)
    else:
        paragraphs = tree.css("p")
        texts = [p.text(strip=True) for p in paragraphs if len(p.text(strip=True)) > 30]
        content = "\n".join(texts) if texts and len("".join(texts)) > 100 else ""
        if not content:
            body = tree.css_first("body")
            if body:
                content = body.text(separator="\n", strip=True)

    # ── Image URLs (scoped to container) ──
    images = []
    scan_html = container_html or html
    for m in IMG_URL_RE.finditer(scan_html):
        src = m.group(1).strip()
        if src and not src.startswith("data:") and _is_content_image(src):
            full_url = urljoin(base, src)
            images.append(full_url)
    images = list(dict.fromkeys(images))

    # ── PDF URLs + their link text (scoped to container) ──
    pdf_urls = []
    pdf_link_texts = {}
    for m in PDF_URL_RE.finditer(scan_html):
        href = m.group(1).strip()
        full_url = urljoin(base, href)
        pdf_urls.append(full_url)
    for m in PDF_LINK_TEXT_RE.finditer(scan_html):
        href_match = re.search(r'href=["\']([^"\']+)["\']', m.group(0))
        if href_match:
            link_url = urljoin(base, href_match.group(1).strip())
            link_text = m.group(1).strip()
            if link_text and link_url in pdf_urls:
                pdf_link_texts[link_url] = link_text
    pdf_urls = list(dict.fromkeys(pdf_urls))

    # ── Published date ──
    published_date = None
    m = PUBLISH_DATE_RE.search(html)
    if m:
        for fmt in ["%d/%m/%Y %H:%M", "%d/%m/%Y"]:
            try:
                published_date = datetime.strptime(
                    f"{m.group(1)} {m.group(2)}" if m.group(2) else m.group(1),
                    fmt
                ).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

    return {
        "content": content,
        "images": images,
        "pdf_urls": pdf_urls,
        "pdf_link_texts": pdf_link_texts,
        "published_date": published_date,
    }


# ── PDF text extraction ─────────────────────────────────────────────

# ── Poppler path for pdf2image ───────────────────────────────────────

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


def _ocr_pdf(content: bytes, max_pages: int = 5, dpi: int = 200) -> str:
    """OCR a scanned PDF using pdf2image + pytesseract. Returns extracted text."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except ImportError:
        logger.debug("OCR: pdf2image or pytesseract not installed")
        return ""

    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    poppler = _get_poppler_path()

    try:
        images = convert_from_bytes(
            content, first_page=1, last_page=max_pages,
            dpi=dpi, poppler_path=poppler, thread_count=2,
        )
    except Exception as e:
        logger.debug("OCR: pdf2image conversion failed: %s", e)
        return ""

    parts = []
    for i, img in enumerate(images):
        try:
            page_text = pytesseract.image_to_string(img, lang="vie+eng")
            if page_text.strip():
                parts.append(page_text.strip())
        except Exception as e:
            logger.debug("OCR: page %d failed: %s", i + 1, e)

    return "\n\n---\n\n".join(parts) if parts else ""


async def async_download_pdf_text(client: "httpx.AsyncClient", pdf_url: str,
                                  timeout_sec: int = 30, ocr: bool = True) -> str:
    """Download a PDF and extract text. Falls back to OCR for scanned PDFs."""
    try:
        resp = await client.get(pdf_url, headers={
            "User-Agent": "Mozilla/5.0",
        }, timeout=timeout_sec, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.debug("PDF download failed [%s]: %s", pdf_url[-50:], e)
        return ""

    if not resp.content.startswith(b"%PDF-"):
        return ""

    # Phase 1: text extraction (pdfplumber + pdfminer)
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

    # Phase 2: OCR fallback for scanned PDFs
    if not text and ocr:
        ocr_text = _ocr_pdf(resp.content)
        if ocr_text:
            logger.info("  OCR: %dB extracted from scanned PDF", len(ocr_text))
            return ocr_text

    # Phase 3: still empty — mark as scanned
    if not text:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                if len(pdf.pages) > 0:
                    return "[SCANNED_PDF]"
        except Exception:
            pass
        return ""

    return text


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
        ocr_text = _ocr_pdf(resp.content)
        if ocr_text:
            logger.info("  OCR: %dB extracted from scanned PDF", len(ocr_text))
            return ocr_text

    if not text:
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                if len(pdf.pages) > 0:
                    return "[SCANNED_PDF]"
        except Exception:
            pass
        return ""

    return text


# ── DB pre-check ────────────────────────────────────────────────────

def batch_has_content(articles: list[dict]) -> list[bool]:
    """Check which articles already have content in DB. Returns same-length list.

    Each article must have 'url' key. Returns bool per article:
      True = already has content (skip deep crawl)
      False = needs deep crawl
    """
    from app.infrastructure.database.pg_pool import get_cursor

    if not articles:
        return []

    with get_cursor() as cur:
        urls = [art["url"] for art in articles]

        # Batch query: check all URLs at once
        cur.execute(
            """SELECT url FROM news_events
               WHERE url = ANY(%s)
               AND (article_content IS NOT NULL AND article_content != '')""",
            (urls,),
        )
        existing_urls = {row[0] for row in cur.fetchall()}

    return [art["url"] in existing_urls for art in articles]


# ── Upsert ──────────────────────────────────────────────────────────

def upsert_article(art: dict, source: str) -> bool:
    """Upsert one article into news_events. Returns True if inserted, False if existed."""
    from app.infrastructure.database.pg_pool import get_cursor

    pub_date = art.get("published_date") or datetime.now(timezone.utc)
    content = art.get("article_content", "")
    images = art.get("article_images") or []
    pdf_urls = art.get("article_pdf_urls") or []
    pdf_text = art.get("article_pdf_text", "")

    has_data = bool(content or images or pdf_urls or pdf_text)
    fetched_at = datetime.now(timezone.utc) if has_data else None

    try:
        with get_cursor() as cur:
            # Phase 1: INSERT if not exists
            cur.execute(
                """INSERT INTO news_events
                   (symbol, published_date, title, url, source,
                    article_content, article_images, article_pdf_urls,
                    article_pdf_text, content_fetched_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, url) DO NOTHING""",
                (art["symbol"], pub_date, art["title"], art["url"], source,
                 content or None, images or None, pdf_urls or None,
                 pdf_text or None, fetched_at),
            )
            if cur.rowcount > 0:
                return True  # newly inserted

            # Phase 2: existed — update only if existing row is missing data
            if has_data:
                cur.execute(
                    """UPDATE news_events SET
                       article_content = COALESCE(news_events.article_content, %s),
                       article_images = CASE
                         WHEN news_events.article_images IS NULL THEN %s
                         ELSE news_events.article_images END,
                       article_pdf_urls = CASE
                         WHEN news_events.article_pdf_urls IS NULL THEN %s
                         ELSE news_events.article_pdf_urls END,
                       article_pdf_text = COALESCE(news_events.article_pdf_text, %s),
                       content_fetched_at = COALESCE(news_events.content_fetched_at, %s)
                       WHERE symbol = %s AND url = %s
                       AND (article_content IS NULL OR article_content = '')""",
                    (content or None, images or None, pdf_urls or None,
                     pdf_text or None, fetched_at,
                     art["symbol"], art["url"]),
                )
                if cur.rowcount > 0:
                    return True  # updated missing content

    except Exception as e:
        logger.debug("Upsert skip [%s]: %s", art.get("symbol"), e)
    return False


def upsert_articles(articles: list[dict], source: str) -> int:
    """Upsert batch. Returns count of newly inserted."""
    count = 0
    for art in articles:
        if upsert_article(art, source):
            count += 1
    return count
