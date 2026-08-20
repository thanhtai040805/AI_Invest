"""Batch planning and the shared resumable batch supervisor.

Every run mode — bare-metal in-process, Modal local-supervised, and Modal
cloud-detached — packs the active documents into batches and drives the *same*
loop: run each batch on a GPU (retrying preempted/failed batches), collect the
per-document stats, then build and persist one run summary. The only things that
differ between modes are how a batch reaches the GPU (``run_batch``) and where
target storage lives (``run_target``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Self, cast

from cheap_ocr.config import env_field, strict_kwargs
from cheap_ocr.models import DocumentInput
from cheap_ocr.stats import build_run_summary

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BatchPolicy:
    """Controls how many PDFs are sent to one GPU worker call, and batch retries.

    Fields fall back to their ``CHEAP_OCR_*`` environment variables like every
    other config (explicit values win).
    """

    max_docs: int = env_field("CHEAP_OCR_BATCH_MAX_DOCS", 64, int)
    max_bytes: int = env_field("CHEAP_OCR_BATCH_MAX_BYTES", 512 * 1024 * 1024, int)
    max_pages: int | None = env_field("CHEAP_OCR_BATCH_MAX_PAGES", None, int)
    retries: int = env_field("CHEAP_OCR_BATCH_RETRIES", 3, int)
    retry_backoff_seconds: float = env_field("CHEAP_OCR_BATCH_RETRY_BACKOFF_SECONDS", 10.0, float)

    def __post_init__(self) -> None:
        """Fail fast on values the supervisor cannot plan with."""
        if min(self.max_docs, self.max_bytes) < 1:
            raise ValueError("max_docs and max_bytes must be >= 1")
        if self.retries < 0 or self.retry_backoff_seconds < 0:
            raise ValueError("retries and retry_backoff_seconds must be >= 0")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        """Create a policy from a mapping, rejecting unknown keys."""
        return cls(**strict_kwargs(set(cls.__dataclass_fields__), "BatchPolicy", value))


@dataclass(frozen=True, slots=True)
class DocumentBatch:
    """A resumable unit of work for one GPU worker call."""

    batch_id: str
    documents: list[DocumentInput]
    bytes_estimate: int | None = None
    pages_estimate: int | None = None


class RunTargetOps(Protocol):
    """Target-side operations a supervisor needs (see :class:`cheap_ocr.runs.RunTarget`)."""

    def refilter(self, documents: list[DocumentInput]) -> list[DocumentInput]:
        """Drop documents whose outputs are complete (used before a batch retry)."""
        ...

    def write_failed_markers(self, documents: list[DocumentInput], error: BaseException) -> list[dict[str, Any]]:
        """Persist per-document failure markers for an exhausted batch."""
        ...

    def write_summary(self, summary: dict[str, Any]) -> None:
        """Persist the run summary."""
        ...


# One batch's worker summary plus its per-document stats, returned by ``run_batch``.
BatchOutcome = tuple[dict[str, Any], list[dict[str, Any]]]
RunBatch = Callable[[list[DocumentInput], DocumentBatch], Awaitable[BatchOutcome]]

_SIZE_METADATA_KEYS = (
    "size_bytes",
    "size",
    "Size",
    "ContentLength",
    "content_length",
    "Content-Length",
)
_PAGE_METADATA_KEYS = ("pages", "page_count", "pdf_pages")


def plan_document_batches(documents: list[DocumentInput], policy: BatchPolicy) -> list[DocumentBatch]:
    """Pack documents into deterministic batches using metadata size/page hints."""
    max_docs = max(1, int(policy.max_docs))
    max_bytes = max(1, int(policy.max_bytes))
    max_pages = int(policy.max_pages) if policy.max_pages is not None and policy.max_pages > 0 else None

    batches: list[DocumentBatch] = []
    current: list[DocumentInput] = []
    current_bytes = 0
    current_pages = 0
    has_size = False
    has_pages = False

    def flush() -> None:
        nonlocal current, current_bytes, current_pages, has_size, has_pages
        if not current:
            return
        batch_index = len(batches) + 1
        batches.append(
            DocumentBatch(
                batch_id=_batch_id(batch_index, current),
                documents=list(current),
                bytes_estimate=current_bytes if has_size else None,
                pages_estimate=current_pages if has_pages else None,
            )
        )
        current = []
        current_bytes = 0
        current_pages = 0
        has_size = False
        has_pages = False

    for document in documents:
        size = document_size_bytes(document)
        pages = document_page_count(document)
        next_bytes = current_bytes + (size or 0)
        next_pages = current_pages + (pages or 0)
        would_exceed = bool(current) and (
            len(current) >= max_docs
            or (size is not None and next_bytes > max_bytes)
            or (max_pages is not None and pages is not None and next_pages > max_pages)
        )
        if would_exceed:
            flush()
        current.append(document)
        if size is not None:
            current_bytes += size
            has_size = True
        if pages is not None:
            current_pages += pages
            has_pages = True

    flush()
    return batches


async def supervise_batches(
    *,
    source: str,
    target: str,
    policy: BatchPolicy,
    documents: list[DocumentInput],
    counts: dict[str, int],
    run_batch: RunBatch,
    run_target: RunTargetOps,
) -> dict[str, Any]:
    """Drive resumable batches and return one persisted run summary.

    ``documents`` are the already-active (incomplete) documents and ``counts``
    the totals from filtering, used for logging and the completed-documents
    tally. ``run_batch`` runs one batch on the GPU and returns its worker
    summary plus per-document stats; ``run_target`` refilters retried batches
    (so a preempted attempt's finished documents are not redone), persists
    failure markers for exhausted batches, and persists the run summary.
    """
    batches = plan_document_batches(documents, policy)
    logger.info(
        "batch_run_start total_documents=%d pending_documents=%d completed_documents=%d "
        "remaining_pending_documents=%d batches=%d batch_policy=%s",
        counts.get("total_documents", 0),
        counts.get("pending_documents", 0),
        counts.get("completed_documents", 0),
        counts.get("remaining_pending_documents", 0),
        len(batches),
        asdict(policy),
    )
    started = time.perf_counter()
    results_by_input_id: dict[str, dict[str, Any]] = {}
    batch_items: list[dict[str, Any]] = []

    for batch in batches:
        summary, stats, status, error = await _run_one_batch(batch, policy, run_batch, run_target)
        for entry in stats:
            input_id = entry.get("input_id")
            if isinstance(input_id, str):
                results_by_input_id[input_id] = entry
        batch_items.append(_batch_summary_item(batch, status, summary, error))

    results = list(results_by_input_id.values())
    summary = build_run_summary(
        results=results, source=source, target=target, wall_seconds=time.perf_counter() - started
    )
    summary["results"] = results
    summary["batches"] = {"total": len(batches), "policy": asdict(policy), "items": batch_items}
    _count_completed_documents(summary, counts.get("completed_documents", 0))
    _add_processing_wall(summary, batch_items)
    run_target.write_summary(summary)
    logger.info(
        "batch_run_completed documents=%s pages=%s wall_seconds=%.3f",
        summary.get("documents"),
        summary.get("pages"),
        summary["durations_seconds"]["wall"],
    )
    return summary


async def _run_one_batch(
    batch: DocumentBatch,
    policy: BatchPolicy,
    run_batch: RunBatch,
    run_target: RunTargetOps,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, BaseException | None]:
    """Run one batch with retries; return (worker_summary, stats, status, error)."""
    pending = batch.documents
    for attempt in range(policy.retries + 1):
        # Re-list the target only after a failed/preempted attempt so a retry does
        # not redo documents whose completion marker was already written.
        if attempt > 0:
            pending = run_target.refilter(pending)
            if not pending:
                return {}, [], "skipped", None
        logger.info(
            "batch_start batch_id=%s documents=%d bytes_estimate=%s pages_estimate=%s attempt=%d",
            batch.batch_id,
            len(pending),
            batch.bytes_estimate,
            batch.pages_estimate,
            attempt + 1,
        )
        try:
            summary, stats = await run_batch(pending, batch)
            logger.info("batch_completed batch_id=%s attempt=%d", batch.batch_id, attempt + 1)
            return summary, stats, "completed", None
        except Exception as exc:
            if attempt >= policy.retries:
                logger.exception("batch_failed batch_id=%s attempts=%d", batch.batch_id, attempt + 1)
                return {}, run_target.write_failed_markers(pending, exc), "failed", exc
            delay = policy.retry_backoff_seconds * (2**attempt)
            logger.warning(
                "batch_retrying batch_id=%s attempt=%d retries=%d delay_seconds=%.1f error=%s: %s",
                batch.batch_id,
                attempt + 1,
                policy.retries,
                delay,
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(delay)
    raise AssertionError(f"unreachable retry state for {batch.batch_id}")


def batch_summary(response: Mapping[str, Any]) -> dict[str, Any]:
    """Return the worker ``summary`` mapping from a batch response."""
    summary = response.get("summary", {})
    return cast(dict[str, Any], summary) if isinstance(summary, dict) else {}


def summary_results(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the per-document ``results`` list from a worker summary."""
    results = summary.get("results", [])
    if not isinstance(results, list):
        return []
    return [cast(dict[str, Any], item) for item in cast(list[Any], results) if isinstance(item, dict)]


def document_size_bytes(document: DocumentInput) -> int | None:
    """Return object/file size from known storage metadata keys, if present."""
    if document.data is not None:
        return len(document.data)
    return _metadata_int(document.metadata, _SIZE_METADATA_KEYS)


def document_page_count(document: DocumentInput) -> int | None:
    """Return page count from metadata, if present."""
    return _metadata_int(document.metadata, _PAGE_METADATA_KEYS)


def _batch_summary_item(
    batch: DocumentBatch,
    status: str,
    summary: dict[str, Any],
    error: BaseException | None,
) -> dict[str, Any]:
    durations = summary.get("durations_seconds", {})
    wall_seconds = cast(dict[str, Any], durations).get("wall") if isinstance(durations, dict) else None
    return {
        "batch_id": batch.batch_id,
        "documents": len(batch.documents),
        "bytes_estimate": batch.bytes_estimate,
        "pages_estimate": batch.pages_estimate,
        "status": status,
        "wall_seconds": wall_seconds,
        "throughput": summary.get("throughput"),
        "ocr_client": summary.get("ocr_client"),
        "error": f"{type(error).__name__}: {error}" if error is not None else None,
    }


def _count_completed_documents(summary: dict[str, Any], completed: int) -> None:
    """Fold already-complete documents into the summary's skipped tally."""
    if completed <= 0:
        return
    raw_documents = summary.get("documents")
    if isinstance(raw_documents, dict):
        documents = cast(dict[str, Any], raw_documents)
        documents["total"] = documents.get("total", 0) + completed
        documents["skipped"] = documents.get("skipped", 0) + completed


def _add_processing_wall(summary: dict[str, Any], batch_items: list[dict[str, Any]]) -> None:
    """Record worker-processing wall time beside end-to-end wall (overhead visibility)."""
    values = [item.get("wall_seconds") for item in batch_items]
    seconds = [float(value) for value in values if isinstance(value, int | float)]
    if not seconds:
        return
    durations = summary["durations_seconds"]
    durations["processing_wall"] = sum(seconds)
    durations["overhead"] = max(0.0, float(durations["wall"]) - sum(seconds))


def _metadata_int(metadata: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        raw = metadata.get(key)
        if isinstance(raw, bool) or raw is None:
            continue
        if isinstance(raw, int):
            return raw if raw >= 0 else None
        if isinstance(raw, float):
            value = int(raw)
            return value if value >= 0 else None
        if isinstance(raw, str):
            try:
                value = int(raw)
            except ValueError:
                continue
            return value if value >= 0 else None
    return None


def _batch_id(index: int, documents: list[DocumentInput]) -> str:
    digest = hashlib.sha1("\n".join(document.input_id for document in documents).encode("utf-8")).hexdigest()[:12]
    return f"batch-{index:05d}-{digest}"
