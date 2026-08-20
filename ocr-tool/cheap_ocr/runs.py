"""The storage-facing run layer: output paths, resumability, and persistence.

Everything that connects pipeline output to storage lives here — the pipeline
itself never touches storage. The per-document ``stats.json`` is the completion
marker: it is written last, and only readable stats with ``status ==
"succeeded"`` (plus the Markdown and layout JSON files present) count as
complete, so failed or corrupt outputs are retried by default.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from cheap_ocr.config import OcrConfig
from cheap_ocr.models import DocumentInput, OutputPaths, ProcessedDoc
from cheap_ocr.stats import add_write_duration, build_failed_stats, sanitize_layout_json
from cheap_ocr.storage import Storage, normalize_relative_path

logger = logging.getLogger(__name__)


def output_paths_for_document(
    storage: Storage,
    target: str,
    document: DocumentInput,
) -> OutputPaths:
    """Return the expected output paths for one document."""
    return output_paths_for_stem(storage, target, document.output_stem)


def output_paths_for_stem(storage: Storage, target: str, output_stem: str) -> OutputPaths:
    """Return output paths below one directory named after the document stem."""
    document_dir = storage.join(target, normalize_relative_path(output_stem) or "document")
    return OutputPaths(
        markdown=storage.join(document_dir, "content.md"),
        json=storage.join(document_dir, "layout.json"),
        stats=storage.join(document_dir, "stats.json"),
    )


def stamp_output_paths(stats: dict[str, Any], paths: OutputPaths) -> None:
    """Record a document's output paths in its stats (for humans reading stats.json)."""
    stats["output"] = {"markdown": paths.markdown, "json": paths.json, "stats": paths.stats}


def write_outputs(storage: Storage, processed: ProcessedDoc, paths: OutputPaths) -> None:
    """Persist one processed document, writing stats last as the completion marker.

    Output-write time is folded into the document's stats before the stats file
    (the completion marker) is written.
    """
    started = time.perf_counter()
    result = processed.result
    if result is not None:
        storage.write_text(paths.markdown, result.markdown)
        storage.write_json(paths.json, sanitize_layout_json(result.json))
    add_write_duration(processed.stats, time.perf_counter() - started)
    storage.write_json(paths.stats, processed.stats)


def document_payload(processed: ProcessedDoc) -> dict[str, Any]:
    """Return a transport-friendly payload for a processed document."""
    result = processed.result
    return {
        "relative_path": processed.document.relative_path,
        "output_stem": processed.document.output_stem,
        "markdown": result.markdown if result is not None else "",
        "json": sanitize_layout_json(result.json if result is not None else []),
        "stats": processed.stats,
    }


def select_active_documents(
    target_storage: Storage,
    target: str,
    documents: list[DocumentInput],
    config: OcrConfig,
    limit: int | None,
) -> tuple[list[DocumentInput], dict[str, int]]:
    """Return incomplete docs and counts using one target listing.

    For cloud storage this avoids multiple ``exists`` calls per document. The
    target prefix is listed once, then candidate stats files are read in a small
    thread pool only when both output files are present.
    """
    max_active = None if limit is None else max(0, int(limit))
    if config.force:
        active = documents if max_active is None else documents[:max_active]
        return active, _counts(total=len(documents), completed=0, pending=len(documents), limit=limit)

    existing_files = set(target_storage.list_files(target))
    if not existing_files:
        active = documents if max_active is None else documents[:max_active]
        return active, _counts(total=len(documents), completed=0, pending=len(documents), limit=limit)

    candidates: list[tuple[str, OutputPaths]] = []
    for document in documents:
        paths = output_paths_for_document(target_storage, target, document)
        if _output_files_present(existing_files, paths):
            candidates.append((document.input_id, paths))

    completed_ids = _read_successful_stats(target_storage, candidates, config.storage_check_workers)
    active: list[DocumentInput] = []
    completed = 0
    pending = 0
    for document in documents:
        if document.input_id in completed_ids:
            completed += 1
            continue
        pending += 1
        if max_active is None or len(active) < max_active:
            active.append(document)
    return active, _counts(total=len(documents), completed=completed, pending=pending, limit=limit)


class RunTarget:
    """One run's target storage plus the operations every batch supervisor needs."""

    def __init__(self, storage: Storage, target: str, config: OcrConfig, *, failed_phase: str = "batch") -> None:
        """Bind the target storage, prefix, and config."""
        self.storage = storage
        self.target = target
        self.config = config
        self.failed_phase = failed_phase

    def refilter(self, documents: list[DocumentInput]) -> list[DocumentInput]:
        """Drop documents whose outputs are complete (used before a batch retry)."""
        return select_active_documents(self.storage, self.target, documents, self.config, None)[0]

    def write_failed_markers(self, documents: list[DocumentInput], error: BaseException) -> list[dict[str, Any]]:
        """Write per-document failure markers for an exhausted batch and return their stats."""
        stats: list[dict[str, Any]] = []
        for document in documents:
            paths = output_paths_for_document(self.storage, self.target, document)
            item = build_failed_stats(document=document, phase=self.failed_phase, error=error, total_seconds=0.0)
            stamp_output_paths(item, paths)
            stats.append(item)
            self.storage.write_json(paths.stats, item)
        return stats

    def write_summary(self, summary: dict[str, Any]) -> None:
        """Persist the run summary at the target."""
        self.storage.write_json(self.storage.join(self.target, "_run_summary.json"), summary)


def _counts(*, total: int, completed: int, pending: int, limit: int | None) -> dict[str, int]:
    active = pending if limit is None else min(pending, max(0, int(limit)))
    return {
        "total_documents": total,
        "pending_documents": active,
        "completed_documents": completed,
        "remaining_pending_documents": max(0, pending - active),
    }


def _output_files_present(existing_files: set[str], paths: OutputPaths) -> bool:
    return paths.stats in existing_files and paths.markdown in existing_files and paths.json in existing_files


def _read_successful_stats(storage: Storage, candidates: list[tuple[str, OutputPaths]], workers: int) -> set[str]:
    if not candidates:
        return set()
    workers = max(1, workers)

    def read_one(item: tuple[str, OutputPaths]) -> str | None:
        input_id, paths = item
        try:
            stats = storage.read_json(paths.stats)
        except Exception:
            return None
        if isinstance(stats, dict) and cast(dict[str, Any], stats).get("status") == "succeeded":
            return input_id
        return None

    completed: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as executor:
        for input_id in executor.map(read_one, candidates):
            if input_id is not None:
                completed.add(input_id)
    return completed
