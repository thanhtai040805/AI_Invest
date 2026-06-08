"""VN Qualitative RAG Tool: automated PDF download, parsing, and indexing for Vietnamese stock reports (Annual Reports, Financial Statements)."""

from __future__ import annotations

import os
import re
import json
import logging
import tempfile
import requests
import pypdfium2 as pdfium

from app.brain.agents.core.progress import emit_progress
from app.brain.agents.core.tools import BaseTool
from app.services.news_rag import news_rag_svc

logger = logging.getLogger(__name__)


def fetch_and_index_documents(symbol: str) -> dict:
    """Search CafeF/Google for symbol's annual reports, download PDF, parse, and index to news_rag_svc."""
    symbol = symbol.strip().upper()
    if not symbol:
        return {"status": "error", "error": "Symbol must not be empty"}

    # 1. Search CafeF for annual report links
    emit_progress("searching", message=f"searching CafeF for {symbol} annual report")
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return {"status": "error", "error": "ddgs package not found. Run pip install ddgs"}

    search_query = f'site:cafef.vn "{symbol}" "báo cáo thường niên" nam'
    pdf_page_url = None

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=5))
            for r in results:
                href = r.get("href", "")
                if "/du-lieu/" in href and "-bao-cao-thuong-nien-" in href:
                    pdf_page_url = href
                    break
            # Fallback to more general search if no direct data page found
            if not pdf_page_url and results:
                for r in results:
                    href = r.get("href", "")
                    if "cafef.vn" in href:
                        pdf_page_url = href
                        break
    except Exception as e:
        logger.error(f"Search failed for {symbol}: {e}")
        pdf_page_url = f"https://cafef.vn/du-lieu/hose/{symbol.lower()}-tai-lieu.chn"

    if not pdf_page_url:
        return {"status": "error", "error": f"No document link found for symbol {symbol}"}

    # 2. Extract PDF attachment link from the article/document page
    emit_progress("fetching", message=f"GET {pdf_page_url[:60]}")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(pdf_page_url, headers=headers, timeout=20)
        if resp.status_code != 200:
            backup_url = f"https://cafef.vn/du-lieu/hose/{symbol.lower()}-tai-lieu.chn"
            resp = requests.get(backup_url, headers=headers, timeout=20)

        html = resp.text
    except Exception as e:
        return {"status": "error", "error": f"Failed to retrieve document page: {e}"}

    # Find any link ending in .pdf hosted on mediacdn or similar
    pdf_links = re.findall(r'href="([^"]+?\.pdf)"', html)
    if not pdf_links:
        pdf_links = re.findall(r"https?://[^\s\"'>]+?\.pdf", html)

    if not pdf_links:
        return {"status": "error", "error": f"No PDF download link found on page {pdf_page_url}"}

    pdf_url = pdf_links[0]
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/"):
        pdf_url = "https://cafef.vn" + pdf_url

    # 3. Download the PDF file to a temp directory
    emit_progress("downloading", message=f"downloading PDF report for {symbol}")
    try:
        pdf_resp = requests.get(pdf_url, headers=headers, timeout=60)
        if pdf_resp.status_code != 200:
            return {
                "status": "error",
                "error": f"Failed to download PDF from {pdf_url}, HTTP {pdf_resp.status_code}",
            }
    except Exception as e:
        return {"status": "error", "error": f"Failed to download PDF from {pdf_url}: {e}"}

    # Save to temp file
    temp_dir = tempfile.gettempdir()
    temp_pdf_path = os.path.join(temp_dir, f"{symbol}_annual_report.pdf")
    try:
        with open(temp_pdf_path, "wb") as f:
            f.write(pdf_resp.content)
    except Exception as e:
        return {"status": "error", "error": f"Failed to write temporary PDF file: {e}"}

    # 4. Parse PDF using pypdfium2
    emit_progress("parsing", message="extracting text from PDF pages")
    extracted_pages = []
    try:
        pdf = pdfium.PdfDocument(temp_pdf_path)
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            page_text = textpage.get_text_bounded()
            if page_text:
                extracted_pages.append((i + 1, page_text))
    except Exception as e:
        return {"status": "error", "error": f"Failed to parse PDF using pypdfium2: {e}"}
    finally:
        if os.path.exists(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except Exception:
                pass

    if not extracted_pages:
        return {"status": "error", "error": "No text content could be extracted from the PDF"}

    # 5. Chunk the text
    emit_progress("chunking", message="segmenting report text for RAG")
    chunks = []
    chunk_size = 1000
    overlap = 200

    for page_num, text in extracted_pages:
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk_content = text[start:end]
            chunks.append({"page": page_num, "content": chunk_content})
            start += chunk_size - overlap

    # 6. Index to RAG (news_rag_svc)
    emit_progress("indexing", message=f"pushing {len(chunks)} chunks to Vector DB")
    news_list = []
    for idx, c in enumerate(chunks):
        news_list.append(
            {
                "newsId": f"{symbol}_annual_report_page{c['page']}_chunk{idx}",
                "symbol": symbol,
                "title": f"{symbol} Annual Report Page {c['page']} Chunk {idx+1}",
                "content": c["content"],
                "friendlyKeyword": "annual_report",
            }
        )

    try:
        news_rag_svc.add_articles(news_list)
    except Exception as e:
        return {"status": "error", "error": f"Failed to index chunks into news_rag_svc: {e}"}

    return {
        "status": "ok",
        "symbol": symbol,
        "document_url": pdf_url,
        "total_pages": len(extracted_pages),
        "total_chunks_indexed": len(chunks),
    }


class VNQualitativeRAGTool(BaseTool):
    """Automated RAG tool for Vietnamese stock qualitative report fetching and parsing."""

    name = "vn_qualitative_rag"
    description = (
        "Automatically search, download, parse, and index Vietnamese corporate reports "
        "(Annual Reports / Báo cáo thường niên) into the RAG vector database for a given ticker symbol. "
        "Run this tool before asking questions about SWOT, business model, risk factors, or competitive advantages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Vietnamese ticker symbol (e.g., FPT, HPG, VNM)",
            }
        },
        "required": ["symbol"],
    }
    repeatable = True

    def execute(self, **kwargs) -> str:
        """Run the automated fetching and indexing process."""
        symbol = kwargs["symbol"]
        result = fetch_and_index_documents(symbol)
        return json.dumps(result, ensure_ascii=False, indent=2)
