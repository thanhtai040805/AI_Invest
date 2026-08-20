"""Provider-neutral async OCR pipeline.

The pipeline owns the document workflow: read PDF bytes, then stream the
document through the stages in page chunks — render, layout, crop/encode, and
recognize each chunk as soon as it is ready — before formatting the assembled
result. Chunk streaming means a document's first OCR requests are in flight
while its remaining pages still queue for the serialized render and layout
threads, instead of waiting for the whole document to clear each stage.

The pipeline never touches storage: input bytes come from ``DocumentInput.data``
or the injected ``read`` callable, and persistence is the caller's job. Callers
are also expected to pre-filter already-complete documents (the batch supervisor
does this with one target listing); the pipeline processes every document it is
given. The render/layout/OCR services can be local GPU objects or any other
implementation of the protocols below.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from concurrent.futures import Executor
from dataclasses import asdict
from typing import Any, Protocol

from cheap_ocr.batches import document_size_bytes
from cheap_ocr.config import OcrConfig
from cheap_ocr.crops import prepare_regions
from cheap_ocr.format import format_document
from cheap_ocr.models import (
    DocumentInput,
    DocumentResult,
    DocumentTimings,
    ProcessedDoc,
    RecognizedRegion,
    Region,
    ResultSettings,
    StageTiming,
)
from cheap_ocr.stats import aggregate_region_usage, build_failed_stats, build_success_stats, latency_summary

logger = logging.getLogger(__name__)

# Reads one document's PDF bytes (used when ``DocumentInput.data`` is unset).
Reader = Callable[[DocumentInput], bytes]


class RenderService(Protocol):
    """Async PDF renderer used by the pipeline."""

    async def page_count(self, pdf_bytes: bytes, config: OcrConfig) -> int:
        """Count one PDF's pages."""
        ...

    async def render(
        self, pdf_bytes: bytes, config: OcrConfig, page_indices: Sequence[int] | None = None
    ) -> tuple[list[Any], StageTiming]:
        """Render pages (all, or ``page_indices``) with queue/exec timing."""
        ...


class LayoutService(Protocol):
    """Async layout detector used by the pipeline."""

    @property
    def backend(self) -> str:
        """Describe the active detector backend (reported in results)."""
        ...

    async def detect(self, pages: list[Any]) -> tuple[dict[int, list[Region]], StageTiming]:
        """Detect layout regions for rendered pages with queue/exec timing."""
        ...


class OcrService(Protocol):
    """Async OCR recognizer used by the pipeline."""

    async def recognize(self, regions: list[Any]) -> list[RecognizedRegion]:
        """Recognize prepared image regions."""
        ...


class Pipeline:
    """Stream documents through render → layout → crop → OCR → format."""

    def __init__(
        self,
        *,
        config: OcrConfig,
        render: RenderService,
        layout: LayoutService,
        ocr: OcrService,
        crop_executor: Executor,
        read: Reader,
        batch_id: str | None = None,
    ) -> None:
        """Wire the pipeline to its stage services."""
        self.config = config
        self.render = render
        self.layout = layout
        self.ocr = ocr
        self.crop_executor = crop_executor
        self.read = read
        self.batch_id = batch_id

    async def stream(self, documents: list[DocumentInput]) -> AsyncIterator[ProcessedDoc]:
        """Process documents concurrently, yielding each as soon as it finishes.

        Failures never raise: a failed document yields a ``ProcessedDoc`` with
        ``result=None`` and failure stats. Closing the generator early cancels
        all in-flight documents.
        """
        # Largest inputs first: a big document admitted late would keep OCR
        # draining alone at the end of the batch, so start the long chains early.
        ordered = sorted(documents, key=self._document_size, reverse=True)
        logger.info(
            "batch_documents_selected batch_id=%s active_documents=%d max_inflight_pdfs=%d",
            self.batch_id,
            len(ordered),
            self.config.max_inflight_pdfs,
        )
        semaphore = asyncio.Semaphore(max(1, int(self.config.max_inflight_pdfs)))
        tasks = [asyncio.create_task(self._run_one(document, semaphore)) for document in ordered]
        try:
            for task in asyncio.as_completed(tasks):
                yield await task
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_one(self, document: DocumentInput, semaphore: asyncio.Semaphore) -> ProcessedDoc:
        started = time.perf_counter()
        phase = "wait_inflight_slot"
        wait_seconds = 0.0
        read_seconds = 0.0
        input_pdf_bytes = 0
        prepared_image_bytes = 0
        page_count = 0
        page_regions: dict[int, list[Region]] = {}
        skipped_regions: list[RecognizedRegion] = []
        ocr_batches: list[asyncio.Task[list[RecognizedRegion]]] = []
        render_totals = _StageTotals()
        layout_totals = _StageTotals()
        crop_seconds = 0.0
        first_ocr_queued: float | None = None
        try:
            async with semaphore:
                acquired = time.perf_counter()
                wait_seconds = acquired - started

                phase = "read_input"
                pdf_bytes = document.data
                if pdf_bytes is None:
                    pdf_bytes = await asyncio.to_thread(self.read, document)
                read_seconds = time.perf_counter() - acquired
                input_pdf_bytes = len(pdf_bytes)

                phase = "render"
                page_count = await self.render.page_count(pdf_bytes, self.config)
                if page_count == 0:
                    return self._finish_empty(document, started, wait_seconds=wait_seconds, read_seconds=read_seconds)

                chunk_size = max(1, int(self.config.page_chunk_size))
                for chunk_start in range(0, page_count, chunk_size):
                    chunk_indices = range(chunk_start, min(chunk_start + chunk_size, page_count))

                    phase = "render"
                    pages, render_timing = await self.render.render(pdf_bytes, self.config, chunk_indices)
                    render_totals.add(render_timing)

                    phase = "layout"
                    chunk_regions, layout_timing = await self.layout.detect(pages)
                    layout_done = time.perf_counter()
                    layout_totals.add(layout_timing)

                    phase = "crop"
                    prepared_chunk, skipped_chunk = await prepare_regions(
                        pages,
                        chunk_regions,
                        self.config,
                        self.crop_executor,
                    )
                    crop_seconds += time.perf_counter() - layout_done

                    for page in pages:
                        page.image.close()

                    page_regions.update(chunk_regions)
                    skipped_regions.extend(skipped_chunk)
                    prepared_image_bytes += sum(region.image_size_bytes for region in prepared_chunk)
                    if prepared_chunk:
                        if first_ocr_queued is None:
                            first_ocr_queued = time.perf_counter()
                        ocr_batches.append(asyncio.create_task(self.ocr.recognize(prepared_chunk)))

            phase = "ocr"
            recognized_regions = [region for batch in await asyncio.gather(*ocr_batches) for region in batch]
            ocr_done = time.perf_counter()
            ocr_started = first_ocr_queued if first_ocr_queued is not None else ocr_done

            phase = "format"
            all_regions: list[RecognizedRegion] = [*recognized_regions, *skipped_regions]
            json_result, markdown_result = format_document(page_count, all_regions)
            formatted = time.perf_counter()

            timings = DocumentTimings(
                total_seconds=formatted - started,
                wait_seconds=wait_seconds,
                read_seconds=read_seconds,
                render_seconds=render_totals.wall,
                render_queue_seconds=render_totals.queue,
                render_exec_seconds=render_totals.exec,
                layout_seconds=layout_totals.wall,
                layout_queue_seconds=layout_totals.queue,
                layout_exec_seconds=layout_totals.exec,
                crop_seconds=crop_seconds,
                ocr_seconds=ocr_done - ocr_started,
                format_seconds=formatted - ocr_done,
            )
            result = self._result_payload(
                filename=document.filename,
                page_count=page_count,
                page_regions=page_regions,
                recognized_regions=recognized_regions,
                skipped_regions=skipped_regions,
                json_result=json_result,
                markdown_result=markdown_result,
                timings=timings,
            )
            stats = build_success_stats(document=document, result=result, total_seconds=time.perf_counter() - started)
            stats["bytes"] = {"input_pdf": input_pdf_bytes, "prepared_images": prepared_image_bytes}
            stats["ocr_requests"] = _ocr_request_stats(recognized_regions)
            logger.info(
                "document_completed batch_id=%s input=%s pages=%d ocr_regions=%d tokens=%d total_seconds=%.3f "
                "wait=%.3f read=%.3f render=%.3f render_queue=%.3f render_exec=%.3f "
                "layout=%.3f layout_queue=%.3f layout_exec=%.3f crop=%.3f ocr=%.3f",
                self.batch_id,
                document.relative_path,
                page_count,
                len(recognized_regions),
                result.tokens.total_tokens,
                timings.total_seconds,
                wait_seconds,
                read_seconds,
                render_totals.wall,
                render_totals.queue,
                render_totals.exec,
                layout_totals.wall,
                layout_totals.queue,
                layout_totals.exec,
                crop_seconds,
                timings.ocr_seconds or 0.0,
            )
            return ProcessedDoc(document=document, stats=stats, result=result)
        except BaseException as exc:
            # OCR tasks already in flight for this document are useless now.
            for task in ocr_batches:
                task.cancel()
            if ocr_batches:
                await asyncio.gather(*ocr_batches, return_exceptions=True)
            if not isinstance(exc, Exception):  # cancellation and friends propagate
                raise
            stats = build_failed_stats(
                document=document,
                phase=phase,
                error=exc,
                total_seconds=time.perf_counter() - started,
            )
            return ProcessedDoc(document=document, stats=stats, result=None)

    def _finish_empty(
        self,
        document: DocumentInput,
        started: float,
        *,
        wait_seconds: float = 0.0,
        read_seconds: float = 0.0,
    ) -> ProcessedDoc:
        result = DocumentResult(
            filename=document.filename,
            page_count=0,
            region_count=0,
            ocr_region_count=0,
            skipped_region_count=0,
            json=[],
            markdown="",
            tokens=aggregate_region_usage([]),
            timings=DocumentTimings(
                total_seconds=time.perf_counter() - started,
                wait_seconds=wait_seconds,
                read_seconds=read_seconds,
            ),
            settings=self._result_settings(),
        )
        stats = build_success_stats(document=document, result=result, total_seconds=time.perf_counter() - started)
        return ProcessedDoc(document=document, stats=stats, result=result)

    def _document_size(self, document: DocumentInput) -> int:
        return document_size_bytes(document) or 0

    def _result_payload(
        self,
        *,
        filename: str,
        page_count: int,
        page_regions: dict[int, list[Region]],
        recognized_regions: list[RecognizedRegion],
        skipped_regions: list[RecognizedRegion],
        json_result: list[list[dict[str, Any]]],
        markdown_result: str,
        timings: DocumentTimings,
    ) -> DocumentResult:
        region_counts = {str(k): len(v) for k, v in sorted(page_regions.items())}
        return DocumentResult(
            filename=filename,
            page_count=page_count,
            region_count=sum(region_counts.values()),
            region_counts_by_page=region_counts,
            ocr_region_count=len(recognized_regions),
            skipped_region_count=len(skipped_regions),
            json=json_result,
            markdown=markdown_result,
            tokens=aggregate_region_usage(recognized_regions),
            timings=timings,
            settings=self._result_settings(),
        )

    def _result_settings(self) -> ResultSettings:
        return ResultSettings(
            pdf_dpi=self.config.pdf_dpi,
            layout_model="PaddlePaddle/PP-DocLayoutV3_safetensors",
            layout_backend=self.layout.backend,
            ocr_backend="vllm_online_server",
            ocr_request_concurrency=self.config.ocr_request_concurrency,
        )


class _StageTotals:
    """Accumulate queue/exec/wall seconds across a document's page chunks."""

    __slots__ = ("exec", "queue", "wall")

    def __init__(self) -> None:
        self.queue = 0.0
        self.exec = 0.0
        self.wall = 0.0

    def add(self, timing: StageTiming) -> None:
        self.queue += timing.queue_seconds
        self.exec += timing.exec_seconds
        self.wall += timing.wall_seconds


def _ocr_request_stats(regions: list[RecognizedRegion]) -> dict[str, Any]:
    by_task: dict[str, dict[str, Any]] = {}
    for recognized in regions:
        task = str(recognized.region.task_type)
        item = by_task.setdefault(
            task,
            {
                "requests": 0,
                "failed": 0,
                "latencies_ms": [],
                "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        item["requests"] += 1
        if recognized.status_code != 200:
            item["failed"] += 1
        if recognized.latency_ms is not None:
            item["latencies_ms"].append(recognized.latency_ms)
        usage = recognized.usage
        if usage is not None:
            tokens = item["tokens"]
            tokens["input_tokens"] += usage.input_tokens
            tokens["output_tokens"] += usage.output_tokens
            tokens["total_tokens"] += usage.total_tokens

    total_latencies: list[int] = []
    failed = 0
    for item in by_task.values():
        latencies: list[int] = item.pop("latencies_ms")
        item["latency_ms"] = latency_summary(latencies)
        total_latencies.extend(latencies)
        failed += int(item["failed"])

    return {
        "requests": len(regions),
        "failed": failed,
        "latency_ms": latency_summary(total_latencies),
        "tokens": asdict(aggregate_region_usage(regions)),
        "by_task": by_task,
    }


__all__ = [
    "LayoutService",
    "OcrService",
    "Pipeline",
    "Reader",
    "RenderService",
]
