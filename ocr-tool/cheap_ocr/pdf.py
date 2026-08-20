"""PDF page counting and rendering, serialized on one worker thread.

PDFium is NOT thread-safe: concurrent calls from multiple threads corrupt its
global state and crash the process with a native SIGSEGV. All PDF opening and
rendering is therefore funneled through :class:`RenderEngine`'s single dedicated
worker thread. This does not bottleneck the pipeline: rendering is far faster
than the GPU-bound layout+OCR stages, and benchmarking a process-pool renderer
showed no throughput gain (the limiter is the shared GPU / serialized layout
feed, not rendering).

Documents are rendered in page chunks (each chunk re-opens the PDF from bytes,
which is cheap relative to rasterization) so the pipeline can stream early pages
into layout and OCR while later pages of the same document still render.
"""

import asyncio
import importlib
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cheap_ocr.config import OcrConfig
from cheap_ocr.models import Page, StageTiming


def pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of pages in one PDF document."""
    pdfium = _pdfium_module()
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf_pages(
    pdf_bytes: bytes,
    dpi: int = 200,
    max_width_or_height: int = 3500,
    page_indices: Sequence[int] | None = None,
) -> list[Page]:
    """Render pages (all, or just ``page_indices``) from one PDF document."""
    return _render_pdf_pages_pdfium(pdf_bytes, dpi, max_width_or_height, page_indices)


class RenderEngine:
    """Serialize PDF rendering on a single worker thread (PDFium is not thread-safe)."""

    def __init__(self) -> None:
        """Create the single-thread render executor."""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")

    async def page_count(self, pdf_bytes: bytes, config: OcrConfig) -> int:
        """Count one PDF's pages on the dedicated render thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, pdf_page_count, pdf_bytes)

    async def render(
        self, pdf_bytes: bytes, config: OcrConfig, page_indices: Sequence[int] | None = None
    ) -> tuple[list[Page], StageTiming]:
        """Render pages (all, or ``page_indices``) with queue/exec timing."""
        loop = asyncio.get_running_loop()
        submitted = time.perf_counter()

        def _render() -> tuple[list[Page], StageTiming]:
            started = time.perf_counter()
            pages = render_pdf_pages(
                pdf_bytes,
                config.pdf_dpi,
                config.pdf_max_side,
                page_indices,
            )
            finished = time.perf_counter()
            return pages, StageTiming(
                queue_seconds=started - submitted,
                exec_seconds=finished - started,
                wall_seconds=finished - submitted,
            )

        return await loop.run_in_executor(self._executor, _render)

    def close(self) -> None:
        """Shut down the render worker thread."""
        self._executor.shutdown(wait=False)


def _pdfium_module() -> Any:
    try:
        return importlib.import_module("pypdfium2")
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency error path.
        raise RuntimeError("pypdfium2 is required for PDF rendering") from exc


def _render_pdf_pages_pdfium(
    pdf_bytes: bytes,
    dpi: int,
    max_width_or_height: int,
    page_indices: Sequence[int] | None,
) -> list[Page]:
    pdfium = _pdfium_module()
    pdf = pdfium.PdfDocument(pdf_bytes)
    pages: list[Page] = []
    try:
        for page_index in range(len(pdf)) if page_indices is None else page_indices:
            page = pdf[page_index]
            try:
                width_pt, height_pt = page.get_size()
                scale = dpi / 72.0
                long_side_pt = max(float(width_pt), float(height_pt))
                if long_side_pt * scale > max_width_or_height:
                    scale = max_width_or_height / long_side_pt
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    pages.append(Page(page_index=page_index, width=image.width, height=image.height, image=image))
                finally:
                    bitmap.close()
            finally:
                page.close()
    finally:
        pdf.close()
    return pages
