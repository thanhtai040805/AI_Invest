# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false
"""Modal entrypoint for cheap-ocr (``modal run -m cheap_ocr.modal.run::run``).

Both run modes drive the same batch supervisor; they differ only in where
batches are prepared and stored:

* Default: the local machine supervises the batches. Local-source PDFs are
  uploaded batch-by-batch (as ``DocumentInput.data``); cloud-source PDFs are
  listed/read inside Modal via Modal secrets so they do not bounce through the
  caller. Outputs are written by the worker for a cloud target, or returned and
  written locally for a local one.
* ``--detach`` (cloud source and target only): a non-preemptible Modal
  supervisor pulls batches from cloud storage and the call returns immediately.

Run settings arrive as one strict-JSON ``--config`` blob (see the ``cheap-ocr
modal`` CLI command), so this entrypoint cannot drift from the CLI's flag list.
"""

import asyncio
import json
import logging
import time
from typing import Any, cast
from urllib.parse import urlparse

from cheap_ocr.batches import BatchPolicy, DocumentBatch, batch_summary, summary_results, supervise_batches
from cheap_ocr.config import OcrConfig
from cheap_ocr.logging import configure_logging
from cheap_ocr.modal.app import app
from cheap_ocr.modal.workers import GpuWorker, StorageWorker
from cheap_ocr.models import DocumentInput, output_stem_for
from cheap_ocr.runs import (
    RunTarget,
    output_paths_for_document,
    output_paths_for_stem,
    select_active_documents,
    stamp_output_paths,
)
from cheap_ocr.stats import add_write_duration, build_failed_stats, sanitize_layout_json
from cheap_ocr.storage import Storage, resolve_storage

logger = logging.getLogger(__name__)

__all__ = ["GpuWorker", "StorageWorker", "app", "run"]


@app.local_entrypoint()
def run(
    source: str = "./input",
    target: str = "./output",
    config: str = "",
    policy: str = "",
    limit: int | None = None,
    detach: bool = False,
) -> None:
    """Run cheap-ocr on Modal with resumable all-in-worker batches.

    ``config`` and ``policy`` are JSON objects with :class:`OcrConfig` /
    :class:`BatchPolicy` fields (unknown keys are rejected). With ``--detach``
    (cloud source and target only) a non-preemptible Modal supervisor pulls
    batches from cloud storage and the call returns immediately; otherwise the
    local machine supervises the batches.
    """
    configure_logging()
    ocr_config = OcrConfig.from_dict(json.loads(config)) if config else OcrConfig()
    batch_policy = BatchPolicy.from_dict(json.loads(policy)) if policy else BatchPolicy()

    if detach:
        if _is_local(source) or _is_local(target):
            raise ValueError("--detach requires both --source and --target to be cloud storage URIs")
        call = StorageWorker().run_detached.spawn(source, target, ocr_config, limit, batch_policy)
        print(json.dumps({"status": "submitted", "function_call_id": getattr(call, "object_id", None)}))
        return

    summary = asyncio.run(_supervise_locally(source, target, ocr_config, limit, batch_policy))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def _supervise_locally(
    source: str,
    target: str,
    config: OcrConfig,
    limit: int | None,
    policy: BatchPolicy,
) -> dict[str, Any]:
    """Supervise batches from the local machine, farming each to a GPU worker."""
    source_is_local = _is_local(source)
    target_is_local = _is_local(target)
    documents = _list_documents(source, source_is_local)
    active, counts = _select_active(target, target_is_local, documents, config, limit)

    worker = GpuWorker()
    # Cloud target storage is never constructed locally (its credentials live in
    # Modal secrets); the worker writes outputs and StorageWorker writes summaries.
    source_storage = resolve_storage(source) if source_is_local else None
    target_storage = resolve_storage(target) if target_is_local else None
    write_in_worker = not target_is_local
    return_payloads = target_is_local

    async def run_batch(
        batch_documents: list[DocumentInput], batch: DocumentBatch
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if source_is_local:
            if source_storage is None:
                raise RuntimeError("source_storage is required for local-source batches")
            batch_documents = await asyncio.to_thread(_attach_payloads, source_storage, batch_documents)
        response = await asyncio.to_thread(
            worker.process_batch.remote,
            batch_documents,
            config,
            source,
            target,
            batch.batch_id,
            write_in_worker,
            return_payloads,
        )
        summary = batch_summary(response)
        if target_is_local:
            if target_storage is None:
                raise RuntimeError("target_storage is required for local-target batches")
            return summary, _write_local_outputs(target_storage, target, response)
        return summary, summary_results(summary)

    run_target: _LocalRunTarget | RunTarget
    if target_is_local:
        assert target_storage is not None
        run_target = RunTarget(target_storage, target, config, failed_phase="modal_batch")
    else:
        run_target = _LocalRunTarget(target, config)

    return await supervise_batches(
        source=source,
        target=target,
        policy=policy,
        documents=active,
        counts=counts,
        run_batch=run_batch,
        run_target=run_target,
    )


class _LocalRunTarget:
    """Run-target operations for a cloud target whose credentials live in Modal.

    Refiltering and summary writes go through the StorageWorker RPC; failure
    markers are built (for the run summary) but not persisted, since the local
    machine cannot reach the target.
    """

    def __init__(self, target: str, config: OcrConfig) -> None:
        self.target = target
        self.config = config

    def refilter(self, documents: list[DocumentInput]) -> list[DocumentInput]:
        return _select_active(self.target, False, documents, self.config, None)[0]

    def write_failed_markers(self, documents: list[DocumentInput], error: BaseException) -> list[dict[str, Any]]:
        # Path computation only touches storage.join (no network), so the cloud
        # storage wrapper is safe to build without Modal-side credentials.
        path_storage = resolve_storage(self.target)
        stats: list[dict[str, Any]] = []
        for document in documents:
            item = build_failed_stats(document=document, phase="modal_batch", error=error, total_seconds=0.0)
            stamp_output_paths(item, output_paths_for_document(path_storage, self.target, document))
            stats.append(item)
        return stats

    def write_summary(self, summary: dict[str, Any]) -> None:
        StorageWorker().write_run_summary.remote(self.target, summary)


def _attach_payloads(storage: Storage, documents: list[DocumentInput]) -> list[DocumentInput]:
    """Read local source PDFs so their bytes travel with the batch RPC."""
    return [
        DocumentInput(
            uri=document.uri,
            relative_path=document.relative_path,
            input_id=document.input_id,
            metadata=document.metadata,
            data=storage.read_bytes(document.uri),
        )
        for document in documents
    ]


def _list_documents(source: str, source_is_local: bool) -> list[DocumentInput]:
    if source_is_local:
        return resolve_storage(source).list_pdfs(source)
    return StorageWorker().list_pdfs.remote(source)


def _select_active(
    target: str,
    target_is_local: bool,
    documents: list[DocumentInput],
    config: OcrConfig,
    limit: int | None,
) -> tuple[list[DocumentInput], dict[str, int]]:
    if target_is_local:
        return select_active_documents(resolve_storage(target), target, documents, config, limit)
    response = StorageWorker().filter_active.remote(documents, target, config, limit)
    response_documents: object = response.get("documents", [])
    response_counts: object = response.get("counts", {})
    active = (
        [doc for doc in response_documents if isinstance(doc, DocumentInput)]
        if isinstance(response_documents, list)
        else []
    )
    counts = cast(dict[str, int], response_counts) if isinstance(response_counts, dict) else {}
    return active, counts


def _write_local_outputs(storage: Storage, target: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    """Write worker-returned outputs to the local target and return their stats."""
    stats: list[dict[str, Any]] = []
    for entry in response.get("documents", []):
        if not isinstance(entry, dict):
            continue
        stem = _entry_output_stem(entry)
        output_paths = output_paths_for_stem(storage, target, stem)
        doc_stats = entry.get("stats")
        if not isinstance(doc_stats, dict):
            continue
        written = dict(doc_stats)
        write_started = time.perf_counter()
        if written.get("status") == "succeeded":
            storage.write_text(output_paths.markdown, str(entry.get("markdown") or ""))
            storage.write_json(output_paths.json, sanitize_layout_json(entry.get("json") or []))
        add_write_duration(written, time.perf_counter() - write_started)
        stamp_output_paths(written, output_paths)
        storage.write_json(output_paths.stats, written)
        stats.append(written)
    return stats


def _entry_output_stem(entry: dict[str, Any]) -> str:
    raw = str(entry.get("output_stem") or entry.get("relative_path") or "document")
    return output_stem_for(raw)


def _is_local(uri: str) -> bool:
    return urlparse(str(uri)).scheme.lower() in {"", "file"}
