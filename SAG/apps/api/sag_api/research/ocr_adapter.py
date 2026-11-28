"""Concrete MinerU Official API v4 adapter implementing `OcrAdapterProtocol` for HOSE filings.

Wraps `sag_api.parsing.mineru_official.OfficialMinerUClient` so `batch_ingest.py` and
`input_builder.py` can perform fail-closed targeted OCR on scanned / BAD_FONT Vietnamese
PDF slices while preserving MinerU's Markdown headings, `<!-- PAGE N -->` markers, and
HTML `<table>` blocks for `table_engine.py`.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import tempfile

import fitz

from sag_api.core.config import Settings
from sag_api.core.config import settings as global_settings
from sag_api.core.errors import ConfigurationError
from sag_api.parsing.mineru_official import (
    OfficialMinerUClient,
    normalize_mineru_language,
)

_PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)
_MINERU_RESULT_TOO_LARGE_CUES = (
    "解析结果超过允许大小",
    "超过允许大小",
    "exceeds allowed size",
    "result too large",
    "file too large",
    "413",
)


def _is_mineru_size_limit_error(exc: Exception) -> bool:
    msg = str(exc or "").lower()
    return any(cue in msg for cue in _MINERU_RESULT_TOO_LARGE_CUES)


_SLICE_SCRUB_CATALOG_KEYS: tuple[str, ...] = (
    "StructTreeRoot",
    "Outlines",
    "Names",
    "Dests",
    "Metadata",
    "PieceInfo",
    "OCProperties",
    "AcroForm",
    "MarkInfo",
    "PageLabels",
    "Threads",
)


def _extract_pdf_page_subset(pdf_bytes: bytes, zero_based_indices: list[int]) -> bytes:
    """Extract a compact sub-PDF via catalog scrub + `Document.select()` + `garbage=2, deflate=True`."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if len(zero_based_indices) == doc.page_count and zero_based_indices == list(range(doc.page_count)):
            return pdf_bytes
        cat = doc.pdf_catalog()
        if cat:
            for key in _SLICE_SCRUB_CATALOG_KEYS:
                try:
                    doc.xref_set_key(cat, key, "null")
                except Exception:  # noqa: BLE001
                    pass
        doc.select(zero_based_indices)
        return doc.tobytes(garbage=2, deflate=True)
    finally:
        doc.close()


def map_mineru_markdown_to_original_pages(
    markdown: str,
    page_numbers: list[int],
) -> dict[int, str]:
    """Map MinerU's `<!-- PAGE k -->` slice output back to original 1-based PDF page numbers."""
    if not page_numbers:
        return {}
    cleaned = (markdown or "").strip()
    if not cleaned:
        return {}

    matches = list(_PAGE_MARKER_RE.finditer(cleaned))
    if not matches:
        return {page_numbers[0]: cleaned}

    mapped: dict[int, str] = {}
    preamble = cleaned[: matches[0].start()].strip()
    for idx, m in enumerate(matches):
        slice_pno = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(cleaned)
        body = cleaned[start:end].strip()
        if idx == 0 and preamble:
            body = f"{preamble}\n\n{body}".strip() if body else preamble
        if not body:
            continue
        orig_idx = slice_pno - 1
        orig_pno = page_numbers[orig_idx] if 0 <= orig_idx < len(page_numbers) else page_numbers[-1]
        if orig_pno in mapped:
            mapped[orig_pno] = f"{mapped[orig_pno]}\n\n{body}"
        else:
            mapped[orig_pno] = body
    return mapped


class MinerUOcrAdapter:
    """`OcrAdapterProtocol` implementation backed by MinerU Official API v4 (`https://mineru.net`)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        parse_method: str = "ocr",
        official_model: str | None = None,
        chunk_pages: int | None = None,
        max_slice_bytes: int = 60 * 1024 * 1024,
    ) -> None:
        base_settings = settings or global_settings
        if not base_settings.mineru_configured:
            raise ConfigurationError(
                "MinerU Official API v4 is not configured (`SAG_MINERU_OFFICIAL_API_TOKEN` missing)."
            )
        self._base_settings = base_settings
        self._parse_method = parse_method
        self._official_model = official_model or base_settings.mineru_official_model
        self._max_slice_bytes = max(1, int(max_slice_bytes))
        if chunk_pages is not None:
            self._chunk_pages = int(chunk_pages)
        elif self._official_model == "vlm":
            # VLM GPU queue latency (~5-9 min) is per-job; avoid chopping normal 40-100p filings
            # into tiny 20p sub-jobs that multiply queue overhead. Parallelize at document level instead.
            self._chunk_pages = max(120, int(getattr(base_settings, "mineru_ocr_chunk_pages", 20)))
        else:
            self._chunk_pages = int(getattr(base_settings, "mineru_ocr_chunk_pages", 20))

    async def _ocr_single_slice(
        self,
        slice_bytes: bytes,
        slice_pages: list[int],
        *,
        resolved_lang: str,
    ) -> dict[int, str]:
        scoped_settings = self._base_settings.model_copy(
            update={
                "mineru_parse_method": self._parse_method,
                "mineru_official_model": self._official_model,
            }
        )
        client = OfficialMinerUClient(scoped_settings, language=resolved_lang)
        with tempfile.TemporaryDirectory(
            prefix="sag_mineru_hose_", ignore_cleanup_errors=True
        ) as tmp_dir:
            slice_name = f"hose_slice_p{slice_pages[0]}_p{slice_pages[-1]}.pdf"
            tmp_pdf = pathlib.Path(tmp_dir) / slice_name
            tmp_pdf.write_bytes(slice_bytes)
            markdown = await client.parse(str(tmp_pdf))
        return map_mineru_markdown_to_original_pages(markdown, slice_pages)

    async def _ocr_slice_with_adaptive_split(
        self,
        slice_bytes: bytes,
        slice_pages: list[int],
        *,
        resolved_lang: str,
        sem: asyncio.Semaphore | None = None,
    ) -> dict[int, str]:
        """OCR one slice; if MinerU rejects the upload or parsed result ZIP as too large, halve and retry."""
        try:
            if sem is not None:
                async with sem:
                    return await self._ocr_single_slice(
                        slice_bytes, slice_pages, resolved_lang=resolved_lang
                    )
            return await self._ocr_single_slice(
                slice_bytes, slice_pages, resolved_lang=resolved_lang
            )
        except Exception as exc:
            if not _is_mineru_size_limit_error(exc) or len(slice_pages) <= 1:
                raise
            doc = fitz.open(stream=slice_bytes, filetype="pdf")
            try:
                page_count = doc.page_count
            finally:
                doc.close()
            if page_count != len(slice_pages):
                raise
            mid = len(slice_pages) // 2
            left_bytes = _extract_pdf_page_subset(slice_bytes, list(range(0, mid)))
            right_bytes = _extract_pdf_page_subset(slice_bytes, list(range(mid, len(slice_pages))))
            left_res, right_res = await asyncio.gather(
                self._ocr_slice_with_adaptive_split(
                    left_bytes, slice_pages[:mid], resolved_lang=resolved_lang, sem=sem
                ),
                self._ocr_slice_with_adaptive_split(
                    right_bytes, slice_pages[mid:], resolved_lang=resolved_lang, sem=sem
                ),
            )
            merged = dict(left_res)
            merged.update(right_res)
            return merged

    async def ocr_pdf_pages(
        self,
        pdf_bytes: bytes,
        page_numbers: list[int],
        *,
        language: str = "latin",
    ) -> dict[int, str]:
        if not page_numbers or not pdf_bytes:
            return {}

        # Map `vi` -> `latin` (MinerU Official v4 canonical pack for Vietnamese extended Latin script)
        resolved_lang = normalize_mineru_language(language, default="latin")
        slice_limit = max(1, self._chunk_pages)
        # MinerU Official API v4 caps single uploads at 200 MB AND caps parsed output ZIP size;
        # auto-slice high-DPI PDFs (> 60 MB by default) so both upload and result ZIP stay well under limits.
        max_slice_bytes = self._max_slice_bytes
        if len(pdf_bytes) > max_slice_bytes and len(page_numbers) > 1:
            byte_based_pages = max(1, int(len(page_numbers) * (max_slice_bytes / len(pdf_bytes)) * 0.85))
            slice_limit = min(slice_limit, byte_based_pages)
        pool_tokens = getattr(self._base_settings, "effective_mineru_official_api_tokens", ())
        configured_parallel = max(
            1, int(getattr(self._base_settings, "effective_mineru_io_concurrency", 16))
        )
        max_parallel = configured_parallel if len(pool_tokens) >= 2 else min(4, configured_parallel)
        sem = asyncio.Semaphore(max_parallel)

        # Always slice large PDFs (> slice_limit pages or > max_slice_bytes) via compact `Document.select()`
        # so multi-hundred-page / high-DPI filings never hit MinerU limits and stitch back to exact original pages.
        if len(page_numbers) > slice_limit:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                page_count = doc.page_count
            finally:
                doc.close()

            chunks: list[tuple[bytes, list[int]]] = []
            if page_count == len(page_numbers):
                for start in range(0, len(page_numbers), slice_limit):
                    end = min(start + slice_limit, len(page_numbers))
                    chunks.append(
                        (
                            _extract_pdf_page_subset(pdf_bytes, list(range(start, end))),
                            page_numbers[start:end],
                        )
                    )

            if len(chunks) > 1:
                results = await asyncio.gather(
                    *(
                        self._ocr_slice_with_adaptive_split(
                            c_bytes, c_pages, resolved_lang=resolved_lang, sem=sem
                        )
                        for c_bytes, c_pages in chunks
                    )
                )
                merged: dict[int, str] = {}
                for part in results:
                    merged.update(part)
                return merged

        return await self._ocr_slice_with_adaptive_split(
            pdf_bytes, page_numbers, resolved_lang=resolved_lang, sem=sem
        )
