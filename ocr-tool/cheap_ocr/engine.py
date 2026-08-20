"""The single-GPU document engine and its public API.

One process holds both models on one GPU: PP-DocLayoutV3 layout detection
(TensorRT/ONNX or PyTorch) and GLM-OCR recognition served by an in-process vLLM
OpenAI-compatible server. The layout model is loaded first so vLLM measures the
remaining free GPU memory correctly. An async pipeline processes many documents
concurrently, streaming OCR requests to the local server so vLLM's continuous
batcher keeps the GPU saturated.

The engine has no provider SDK dependency, and its lifecycle is deliberately
explicit: :meth:`DocumentEngine.start` takes minutes (model load + warmup + vLLM
boot), so engines are created once and kept alive — there is no context
manager. Long-lived workers typically never call :meth:`DocumentEngine.stop`.

    engine = DocumentEngine().start()
    result = await engine.ocr(pdf_bytes)          # one PDF, in memory
    async for doc in engine.stream(docs, config): # many, as they finish
        ...
    summary = await engine.run(source, target)    # storage-to-storage, resumable
"""

import asyncio
import contextlib
import faulthandler
import logging
import subprocess
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Self

import aiohttp

from cheap_ocr.batches import BatchPolicy, DocumentBatch, batch_summary, summary_results, supervise_batches
from cheap_ocr.config import EngineConfig, OcrConfig
from cheap_ocr.layout import LayoutEngine
from cheap_ocr.models import DocumentInput, DocumentResult, OcrError, ProcessedDoc
from cheap_ocr.ocr import VllmOcrClient
from cheap_ocr.ocr.server import base_url, start_vllm_server, stop_vllm_server, wait_for_health, _server_log_tail
from cheap_ocr.pdf import RenderEngine
from cheap_ocr.pipeline import Pipeline
from cheap_ocr.runs import (
    RunTarget,
    document_payload,
    output_paths_for_document,
    select_active_documents,
    stamp_output_paths,
    write_outputs,
)
from cheap_ocr.stats import build_run_summary
from cheap_ocr.storage import resolve_storage

logger = logging.getLogger(__name__)


class DocumentEngine:
    """One GPU process running both the layout and OCR models.

    ``config`` carries the startup-scoped knobs (what gets loaded onto the GPU
    and how); everything that can vary per call lives on
    :class:`~cheap_ocr.config.OcrConfig`, passed per call or as the engine-wide
    ``run_config`` default. Any config field left unset falls back to its
    ``CHEAP_OCR_*`` environment variable, then the built-in default.
    """

    def __init__(self, config: EngineConfig | None = None, *, run_config: OcrConfig | None = None) -> None:
        """Create an unstarted engine. Call :meth:`start` before processing."""
        self.config = config or EngineConfig()
        self.run_config = run_config or OcrConfig()
        self._started = False
        self.render_engine: RenderEngine | None = None
        self.layout_engine: LayoutEngine | None = None
        self.vllm_process: subprocess.Popen[bytes] | None = None
        self._crop_executor: ThreadPoolExecutor | None = None
        self._vllm_monitor: threading.Thread | None = None

    def _watch_vllm_process(self) -> None:
        """Reap the vLLM server subprocess and log an unexpected exit."""
        process = self.vllm_process
        if process is None:
            return
        code = process.wait()
        if self._started:
            logger.warning(
                "vllm_server_unexpected_exit code=%s log_tail=%s", code, _server_log_tail()
            )

    def start(self) -> Self:
        """Load the layout model, then start and warm the vLLM OCR server.

        Takes minutes on first use (model downloads, TensorRT engine build,
        vLLM startup); idempotent, and returns the engine for chaining. A
        failed start tears down whatever it had brought up, so it can be
        retried.
        """
        if self._started:
            return self
        try:
            self._start()
        except BaseException:
            self.stop()
            raise
        self._started = True
        return self

    def _start(self) -> None:
        # Dump native + Python tracebacks for all threads on a fatal signal
        # (e.g. a SIGSEGV from a CUDA fault), which otherwise leaves no trace.
        faulthandler.enable(all_threads=True)
        startup_started = time.perf_counter()
        self.render_engine = RenderEngine()
        self._crop_executor = ThreadPoolExecutor(
            max_workers=self.config.crop_encode_workers, thread_name_prefix="crop-encode"
        )
        logger.info("loading_layout_detector")
        layout_load_started = time.perf_counter()
        self.layout_engine = LayoutEngine(self.config)
        logger.info("layout_detector_loaded seconds=%.3f", time.perf_counter() - layout_load_started)
        layout_warmup_started = time.perf_counter()
        self.layout_engine.warmup()
        logger.info(
            "layout_ready backend=%s warmup_seconds=%.3f",
            self.layout_engine.backend,
            time.perf_counter() - layout_warmup_started,
        )

        logger.info("starting_vllm_ocr_server")
        vllm_startup_started = time.perf_counter()
        self.vllm_process = start_vllm_server(self.config)
        wait_for_health(self.vllm_process, port=self.config.vllm_port, timeout=self.config.vllm_health_timeout_seconds)
        logger.info(
            "gpu_worker_ready vllm_startup_seconds=%.3f startup_seconds=%.3f",
            time.perf_counter() - vllm_startup_started,
            time.perf_counter() - startup_started,
        )
        # Watch the vLLM server subprocess so an unexpected exit (after a
        # successful health check) is both reaped and reported in the logs.
        self._vllm_monitor = threading.Thread(
            target=self._watch_vllm_process, name="vllm-monitor", daemon=True
        )
        self._vllm_monitor.start()

    def stop(self) -> None:
        """Tear down the vLLM server and worker threads (optional for long-lived workers)."""
        self._started = False
        stop_vllm_server(self.vllm_process)
        self.vllm_process = None
        if self.layout_engine is not None:
            self.layout_engine.close()
            self.layout_engine = None
        if self.render_engine is not None:
            self.render_engine.close()
            self.render_engine = None
        if self._crop_executor is not None:
            self._crop_executor.shutdown(wait=False)
            self._crop_executor = None

    async def ocr(self, pdf: bytes, *, config: OcrConfig | None = None) -> DocumentResult:
        """OCR one in-memory PDF and return its result.

        The primitive: no storage, no stats files. Raises :class:`OcrError` if
        the document fails to process.
        """
        cfg = config or self.run_config
        document = DocumentInput(
            uri="memory://document.pdf",
            relative_path="document.pdf",
            input_id="memory-document",
            data=pdf,
        )
        async for processed in self.stream([document], cfg):
            if processed.result is None:
                error = processed.stats.get("error", {})
                raise OcrError(phase=str(error.get("phase", "unknown")), message=str(error.get("message", "")))
            return processed.result
        raise OcrError(phase="pipeline", message="pipeline yielded no result")

    async def stream(
        self,
        documents: list[DocumentInput],
        config: OcrConfig | None = None,
        *,
        batch_id: str | None = None,
    ) -> AsyncIterator[ProcessedDoc]:
        """Process documents concurrently, yielding each as soon as it finishes.

        Documents carry bytes via ``DocumentInput.data`` or are read from their
        ``uri`` (local path or cloud URI). The caller decides persistence; a
        failed document yields ``result=None`` with failure stats instead of
        raising.
        """
        cfg = config or self.run_config
        async with self._pipeline(cfg, batch_id=batch_id) as (pipeline, _client):
            async for processed in pipeline.stream(documents):
                yield processed

    async def run(
        self,
        source: str,
        target: str,
        config: OcrConfig | None = None,
        *,
        limit: int | None = None,
        batch_policy: BatchPolicy | None = None,
    ) -> dict[str, Any]:
        """Process PDFs from a source location and write outputs back to a target.

        ``source`` and ``target`` are local paths or cloud URIs (``s3://``,
        ``gs://``, ``az://``). Resumable: documents with complete outputs are
        skipped (unless ``config.force``), and failed batches are retried per
        ``batch_policy``.
        """
        cfg = config or self.run_config
        policy = batch_policy or BatchPolicy()
        source_storage = resolve_storage(source)
        target_storage = resolve_storage(target)
        documents = source_storage.list_pdfs(source)
        active, counts = select_active_documents(target_storage, target, documents, cfg, limit)
        run_target = RunTarget(target_storage, target, cfg, failed_phase="run_batch")

        async def run_batch(
            batch_documents: list[DocumentInput], batch: DocumentBatch
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            response = await self.process_batch(
                batch_documents,
                cfg,
                source=source,
                target=target,
                batch_id=batch.batch_id,
                write=True,
                return_payloads=False,
            )
            summary = batch_summary(response)
            return summary, summary_results(summary)

        return await supervise_batches(
            source=source,
            target=target,
            policy=policy,
            documents=active,
            counts=counts,
            run_batch=run_batch,
            run_target=run_target,
        )

    async def process_batch(
        self,
        documents: list[DocumentInput],
        config: OcrConfig,
        *,
        source: str,
        target: str,
        batch_id: str | None = None,
        write: bool = True,
        return_payloads: bool = False,
    ) -> dict[str, Any]:
        """Process one pre-filtered batch; the RPC-facing worker call.

        Consumes :meth:`stream` and, per document, writes outputs to ``target``
        (``write``, for targets the worker can reach) and/or collects a
        transport-friendly payload (``return_payloads``, for targets only the
        caller can reach). Returns ``{"batch_id", "summary", "documents"}``.
        Already-complete documents must be filtered out by the caller (the batch
        supervisor does this with one target listing per attempt).
        """
        target_storage = resolve_storage(target)
        logger.info(
            "process_batch_start batch_id=%s documents=%d write=%s return_payloads=%s",
            batch_id,
            len(documents),
            write,
            return_payloads,
        )
        run_started = time.perf_counter()
        stats: list[dict[str, Any]] = []
        payloads: list[dict[str, Any]] = []
        write_tasks: list[asyncio.Task[None]] = []

        async def persist(processed: ProcessedDoc) -> None:
            paths = output_paths_for_document(target_storage, target, processed.document)
            stamp_output_paths(processed.stats, paths)
            if write:
                await asyncio.to_thread(write_outputs, target_storage, processed, paths)
            if return_payloads:
                payloads.append(document_payload(processed))
            stats.append(processed.stats)

        async with self._pipeline(config, batch_id=batch_id) as (pipeline, ocr_client):
            try:
                async for processed in pipeline.stream(documents):
                    # Writes run concurrently with in-flight documents, exactly like
                    # the per-document sink they replace; a write failure fails the
                    # batch (the supervisor retries it after refiltering).
                    write_tasks.append(asyncio.create_task(persist(processed)))
                await asyncio.gather(*write_tasks)
            except BaseException:
                for task in write_tasks:
                    task.cancel()
                if write_tasks:
                    await asyncio.gather(*write_tasks, return_exceptions=True)
                raise

            wall_seconds = time.perf_counter() - run_started
            summary = build_batch_summary(
                stats=stats,
                source=source,
                target=target,
                wall_seconds=wall_seconds,
                batch_id=batch_id,
                ocr_client=ocr_client,
            )
        return {"batch_id": batch_id, "summary": summary, "documents": payloads}

    @contextlib.asynccontextmanager
    async def _pipeline(
        self, config: OcrConfig, *, batch_id: str | None
    ) -> AsyncGenerator[tuple[Pipeline, VllmOcrClient]]:
        """Bind a fresh aiohttp session and OCR client to a pipeline for one call."""
        if self.render_engine is None or self.layout_engine is None or self._crop_executor is None:
            raise RuntimeError("DocumentEngine.start() must be called before processing documents")
        connector = aiohttp.TCPConnector(limit=config.ocr_request_concurrency + 16, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            ocr_client = VllmOcrClient(
                session=session,
                base_url=base_url(self.config.vllm_port),
                config=config,
            )
            pipeline = Pipeline(
                config=config,
                render=self.render_engine,
                layout=self.layout_engine,
                ocr=ocr_client,
                crop_executor=self._crop_executor,
                read=_read_document_bytes,
                batch_id=batch_id,
            )
            yield pipeline, ocr_client


def build_batch_summary(
    *,
    stats: list[dict[str, Any]],
    source: str,
    target: str,
    wall_seconds: float,
    batch_id: str | None,
    ocr_client: VllmOcrClient,
) -> dict[str, Any]:
    """Build one batch's summary with per-document results and client metrics."""
    summary = build_run_summary(results=stats, source=source, target=target, wall_seconds=wall_seconds)
    summary["results"] = stats
    summary["batch_id"] = batch_id
    summary["ocr_client"] = ocr_client.metrics_snapshot()
    logger.info(
        "batch_documents_completed batch_id=%s documents=%s pages=%s regions=%s tokens=%s "
        "wall_seconds=%.3f throughput=%s",
        batch_id,
        summary.get("documents"),
        summary.get("pages"),
        summary.get("regions"),
        summary.get("tokens"),
        wall_seconds,
        summary.get("throughput"),
    )
    return summary


def _read_document_bytes(document: DocumentInput) -> bytes:
    """Read one document's PDF bytes from its (self-describing) URI."""
    return resolve_storage(document.uri).read_bytes(document.uri)
