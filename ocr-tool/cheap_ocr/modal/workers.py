# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedClassDecorator=false, reportUntypedFunctionDecorator=false
"""Modal workers: a thin GpuWorker delegating to DocumentEngine, plus a storage helper.

The GPU container loads the layout model first, then starts the vLLM OCR server
(both on one GPU), then serves many documents concurrently through one shared
:class:`cheap_ocr.engine.DocumentEngine`.
"""

import asyncio
import logging
import os
import json
import time
from typing import Any

import modal

from cheap_ocr.batches import BatchPolicy, DocumentBatch, batch_summary, summary_results, supervise_batches
from cheap_ocr.config import OcrConfig
from cheap_ocr.engine import DocumentEngine
from cheap_ocr.logging import configure_logging
from cheap_ocr.modal.app import (
    CPU_CORES,
    MODAL,
    MODAL_SECRETS,
    app,
    hf_cache_vol,
    image,
    storage_image,
    vllm_cache_vol,
)
from cheap_ocr.models import DocumentInput
from cheap_ocr.runs import RunTarget, select_active_documents
from cheap_ocr.storage import resolve_storage

logger = logging.getLogger(__name__)

outputs_volume = modal.Volume.from_name("financial-ocr-outputs", create_if_missing=True)


@app.cls(
    image=image,
    gpu=MODAL.gpu,
    cpu=CPU_CORES,
    timeout=MODAL.timeout_seconds,
    startup_timeout=MODAL.startup_timeout_seconds,
    scaledown_window=MODAL.scaledown_window_seconds,
    max_containers=MODAL.max_containers,
    secrets=MODAL_SECRETS,
    volumes={
        "/root/.cache/huggingface": hf_cache_vol,
        "/root/.cache/vllm": vllm_cache_vol,
        "/root/outputs": outputs_volume,
    },
)
class GpuWorker:
    """One GPU container running both the layout and OCR models."""

    @modal.enter()
    def start(self) -> None:
        """Load the layout model, then start and warm the vLLM OCR server."""
        configure_logging()
        self.engine = DocumentEngine().start()

    @modal.method()
    async def process_batch(
        self,
        documents: list[DocumentInput],
        config: OcrConfig,
        source: str,
        target: str,
        batch_id: str | None = None,
        write: bool = True,
        return_payloads: bool = False,
    ) -> dict[str, Any]:
        """Process one pre-filtered batch of documents (bytes payloads or cloud refs)."""
        res = await self.engine.process_batch(
            documents,
            config,
            source=source,
            target=target,
            batch_id=batch_id,
            write=write,
            return_payloads=return_payloads,
        )

        if source == "ainvest_financial":
            try:
                from financial_pipeline.config import load_profile
                from financial_pipeline.region_classifier import RegionClassifier

                prof_path = "/root/app/financial_profile.yaml" if os.path.exists("/root/app/financial_profile.yaml") else "financial_profile.yaml"
                region_classifier = RegionClassifier(load_profile(prof_path))
                checkpoint_dir = "/root/outputs/checkpoints"
                os.makedirs(checkpoint_dir, exist_ok=True)

                for entry in res.get("documents", []):
                    stats = entry.get("stats") or {}
                    sid = stats.get("input_id")
                    if not sid:
                        continue
                    raw_md = entry.get("markdown", "")
                    clean_md, _ = region_classifier.filter_markdown_sections(raw_md)
                    entry["markdown"] = clean_md

                    input_info = stats.get("input") if isinstance(stats.get("input"), dict) else {}
                    url = input_info.get("uri") or stats.get("uri") or ""
                    filename = input_info.get("relative_path") or stats.get("relative_path") or f"{sid}.pdf"

                    payload = {
                        "input_id": sid,
                        "url": url,
                        "filename": filename,
                        "markdown": clean_md,
                        "metrics": stats,
                        "status": "succeeded",
                    }
                    path = os.path.join(checkpoint_dir, f"{sid}.json")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False)
                outputs_volume.commit()
            except Exception as exc:
                logger.error("Error post-processing financial batch directly on GPU worker: %s", exc)

        return res

    @modal.exit()
    def stop(self) -> None:
        """Tear down the vLLM server and worker threads."""
        engine = getattr(self, "engine", None)
        if engine is not None:
            engine.stop()


@app.cls(
    image=storage_image,
    cpu=1.0,
    timeout=MODAL.timeout_seconds,
    startup_timeout=MODAL.startup_timeout_seconds,
    scaledown_window=MODAL.scaledown_window_seconds,
    secrets=MODAL_SECRETS,
    nonpreemptible=True,
)
class StorageWorker:
    """Small Modal-side storage helper for cloud listing/filtering/writes."""

    @modal.enter()
    def start(self) -> None:
        """Configure logging for storage helper calls."""
        configure_logging()

    @modal.method()
    def list_pdfs(self, source: str) -> list[DocumentInput]:
        """List source PDFs using Modal-side cloud credentials."""
        return resolve_storage(source).list_pdfs(source)

    @modal.method()
    def filter_active(
        self,
        documents: list[DocumentInput],
        target: str,
        config: OcrConfig,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Filter documents against a target using Modal-side cloud credentials."""
        target_storage = resolve_storage(target)
        active, counts = select_active_documents(target_storage, target, documents, config, limit)
        return {"documents": active, "counts": counts}

    @modal.method()
    def write_run_summary(self, target: str, summary: dict[str, Any]) -> None:
        """Write the run summary using Modal-side cloud credentials."""
        storage = resolve_storage(target)
        storage.write_json(storage.join(target, "_run_summary.json"), summary)

    @modal.method()
    def run_detached(
        self,
        source: str,
        target: str,
        config: OcrConfig,
        limit: int | None = None,
        batch_policy: BatchPolicy | None = None,
    ) -> dict[str, Any]:
        policy = batch_policy or BatchPolicy()
        source_storage = resolve_storage(source)
        target_storage = resolve_storage(target)
        documents = source_storage.list_pdfs(source)
        active, counts = select_active_documents(target_storage, target, documents, config, limit)
        run_target = RunTarget(target_storage, target, config, failed_phase="modal_cloud_batch")
        gpu_worker = GpuWorker()

        async def run_batch(
            batch_documents: list[DocumentInput], batch: DocumentBatch
        ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            response = await asyncio.to_thread(
                gpu_worker.process_batch.remote,
                batch_documents,
                config,
                source,
                target,
                batch.batch_id,
                True,
                False,
            )
            summary = batch_summary(response)
            return summary, summary_results(summary)

        return asyncio.run(
            supervise_batches(
                source=source,
                target=target,
                policy=policy,
                documents=active,
                counts=counts,
                run_batch=run_batch,
                run_target=run_target,
            )
        )
