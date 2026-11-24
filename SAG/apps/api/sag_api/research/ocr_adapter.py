"""Concrete MinerU Official API v4 adapter implementing `OcrAdapterProtocol` for HOSE filings.

Wraps `sag_api.parsing.mineru_official.OfficialMinerUClient` so `batch_ingest.py` and
`input_builder.py` can perform fail-closed targeted OCR on scanned / BAD_FONT Vietnamese
PDF slices while preserving MinerU's Markdown headings, `<!-- PAGE N -->` markers, and
HTML `<table>` blocks for `table_engine.py`.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

from sag_api.core.config import Settings
from sag_api.core.config import settings as global_settings
from sag_api.core.errors import ConfigurationError
from sag_api.parsing.mineru_official import (
    OfficialMinerUClient,
    normalize_mineru_language,
)

_PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)


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
    ) -> None:
        base_settings = settings or global_settings
        if not base_settings.mineru_configured:
            raise ConfigurationError(
                "MinerU Official API v4 is not configured (`SAG_MINERU_OFFICIAL_API_TOKEN` missing)."
            )
        self._base_settings = base_settings
        self._parse_method = parse_method
        self._official_model = official_model or base_settings.mineru_official_model

    async def ocr_pdf_pages(
        self,
        pdf_bytes: bytes,
        page_numbers: list[int],
        *,
        language: str = "latin",
    ) -> dict[int, str]:
        if not page_numbers or not pdf_bytes:
            return {}

        scoped_settings = self._base_settings.model_copy(
            update={
                "mineru_parse_method": self._parse_method,
                "mineru_official_model": self._official_model,
            }
        )
        # Map `vi` -> `latin` (MinerU Official v4 canonical pack for Vietnamese extended Latin script)
        resolved_lang = normalize_mineru_language(language, default="latin")
        client = OfficialMinerUClient(scoped_settings, language=resolved_lang)

        with tempfile.TemporaryDirectory(prefix="sag_mineru_hose_") as tmp_dir:
            slice_name = f"hose_slice_p{page_numbers[0]}_p{page_numbers[-1]}.pdf"
            tmp_pdf = pathlib.Path(tmp_dir) / slice_name
            tmp_pdf.write_bytes(pdf_bytes)
            markdown = await client.parse(str(tmp_pdf))

        return map_mineru_markdown_to_original_pages(markdown, page_numbers)
