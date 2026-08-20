# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false
"""Modal CPU page classifier: download + prune one BCTC PDF before any GPU work.

Runs page classification (and the small Tesseract fallback for hybrid PDFs) on
cheap Modal CPU so a batch never depends on the local machine. The payload is
JSON-safe (bytes + page metadata); the supervisor maps it back to URLs via the
filename-derived ``input_id``, which is stable across list reorderings so
checkpoint resumes keep matching.
"""

import re
import time
import urllib.request
from typing import Any

import modal

from .app import app, classifier_image

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _input_id_for(filename: str) -> str:
    stem = filename[:-4] if filename.endswith(".pdf") else filename
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)
    return f"financial_{safe}"


def _filename_for(url: str, index: int) -> str:
    filename = url.split("/")[-1].split("?")[0] or f"document_{index + 1}.pdf"
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    return filename


@app.function(image=classifier_image, cpu=2.0, timeout=30 * 60, max_containers=24)
def classify_one(url: str, index: int, enable_filtering: bool = True) -> dict[str, Any]:
    """Download one PDF from ``url`` and prune non-essential pages (Modal CPU)."""
    from financial_pipeline.config import load_profile
    from financial_pipeline.page_classifier import PageClassifier

    started = time.time()
    filename = _filename_for(url, index)
    input_id = _input_id_for(filename)

    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=180) as resp:
            pdf_bytes = resp.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to download PDF from {url}: {exc}") from None
    download_seconds = time.time() - started

    if not enable_filtering:
        return {
            "input_id": input_id,
            "url": url,
            "filename": filename,
            "data": pdf_bytes,
            "pdf_size_bytes": len(pdf_bytes),
            "total_pages": 0,
            "retained_pages": 0,
            "skipped_pages": 0,
            "pages_meta": [],
            "retained_page_indices": [],
            "download_seconds": round(download_seconds, 3),
            "classify_seconds": 0.0,
        }

    result = PageClassifier(load_profile("financial_profile.yaml")).classify_and_prune(pdf_bytes)

    return {
        "input_id": input_id,
        "url": url,
        "filename": filename,
        "data": result.pruned_pdf_bytes,
        "pdf_size_bytes": len(pdf_bytes),
        "total_pages": result.total_pages,
        "retained_pages": result.retained_pages_count,
        "skipped_pages": result.skipped_pages_count,
        "pages_meta": [
            {
                "page_number": meta.page_number,
                "page_type": meta.page_type,
                "matched_signature": meta.matched_signature,
                "decision": meta.decision,
                "snippet": meta.snippet,
            }
            for meta in result.pages_meta
        ],
        "retained_page_indices": result.retained_page_indices,
        "download_seconds": round(download_seconds, 3),
        "classify_seconds": round(time.time() - started, 3),
    }
