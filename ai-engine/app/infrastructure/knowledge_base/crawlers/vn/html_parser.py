"""
html_parser.py — HTML extraction utilities for news crawlers.
"""
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

CONTENT_SELECTORS = [
    "#vst_detail", "#mainDetailV2", "#newscontent",
    "[id$=divContent]", "[id$=ucStockNewsDetail1_divContent]",
    "#page-content", ".article-content", ".detail-content",
    # VnEconomy
    ".article-editor",
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
