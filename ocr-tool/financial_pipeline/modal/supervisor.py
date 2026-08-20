# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUntypedClassDecorator=false, reportUntypedFunctionDecorator=false
"""Batch supervisor for the financial OCR pipeline (financial-ocr app).

A cheap preemptible Modal CPU container orchestrating the whole batch:

1. Classify every URL in parallel on Modal CPU (``classify_one.map``).
2. Resume documents whose checkpoints already exist on the shared volume.
3. Group the remaining pruned documents into GPU batches (policy-bounded).
4. Farm each batch to cheap-ocr ``GpuWorker`` (Cross-app).
5. If detach=True, spawn GPU processing asynchronously and close CPU supervisor IMMEDIATELY.
"""

import json
import os
import time
from typing import Any

import modal

from cheap_ocr.config import OcrConfig
from cheap_ocr.models import DocumentInput

from .app import OUTPUT_VOLUME_PATH, app, classifier_image, outputs_volume
from .classify import classify_one


def _group_batches(
    classified: list[dict[str, Any]], policy: dict[str, Any]
) -> list[list[dict[str, Any]]]:
    """Group classified docs into GPU batches bounded by docs/bytes/pages."""
    max_docs = int(policy.get("max_docs", 16))
    max_bytes = int(policy.get("max_bytes_mb", 512)) * 1024 * 1024
    max_pages = int(policy.get("max_pages", 1200))

    docs = sorted(
        classified,
        key=lambda d: len(d.get("data") or b""),
        reverse=True,
    )
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    current_pages = 0
    for doc in docs:
        size = len(doc.get("data") or b"")
        pages = int(doc.get("retained_pages") or doc.get("total_pages") or 0)
        if current and (
            len(current) >= max_docs
            or current_bytes + size > max_bytes
            or current_pages + pages > max_pages
        ):
            batches.append(current)
            current = []
            current_bytes = 0
            current_pages = 0
        current.append(doc)
        current_bytes += size
        current_pages += pages
    if current:
        batches.append(current)
    return batches


def _document_input(doc: dict[str, Any]) -> DocumentInput:
    return DocumentInput(
        input_id=doc["input_id"],
        uri=doc["url"],
        relative_path=doc["filename"],
        data=doc.get("data"),
    )


@app.cls(
    image=classifier_image,
    cpu=4.0,
    timeout=6 * 60 * 60,
    volumes={OUTPUT_VOLUME_PATH: outputs_volume},
)
class BatchSupervisor:
    """Orchestrates a financial OCR batch: classify → group → GPU → filter."""

    @modal.enter()
    def start(self) -> None:
        """Resolve the cross-app GPU worker handle once per container."""
        self.gpu_worker = modal.Cls.from_name("cheap-ocr", "GpuWorker")()

    @modal.method()
    def run(
        self,
        urls: list[str],
        config: dict[str, Any] | None = None,
        batch_policy: dict[str, Any] | None = None,
        enable_filtering: bool = True,
        max_retries: int = 3,
        detach: bool = False,
    ) -> dict[str, Any]:
        """Run a full financial OCR batch and return JSON-safe outputs."""
        ocr_config = OcrConfig.from_dict(config or {"force": True, "pdf_dpi": 150})
        policy = batch_policy or {"max_docs": 16, "max_bytes_mb": 512, "max_pages": 1200}
        self._run_started = time.time()
        n = len(urls)
        print(f"[supervisor] Classifying {n} URLs on Modal CPU...")

        # --- 1. Parallel page classification on Modal CPU ---------------------
        raw = list(
            classify_one.map(
                [u for u in urls],
                list(range(len(urls))),
                kwargs={"enable_filtering": enable_filtering},
                order_outputs=True,
                return_exceptions=True,
            )
        )
        classified: list[dict[str, Any]] = []
        for i, item in enumerate(raw):
            if isinstance(item, Exception):
                last_error: BaseException = item
                for attempt in range(max_retries):
                    try:
                        item = classify_one.remote(urls[i], i, enable_filtering)
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                        time.sleep(min(2 ** attempt, 30))
                else:
                    print(f"⚠️ [supervisor] Skipping unreadable/broken URL after retries ({urls[i]}): {last_error}")
                    continue
            classified.append(item)
        print(
            f"[supervisor] Classify done in {time.time() - self._run_started:.1f}s "
            f"across {len(classified)} docs."
        )

        # --- 2. Resume completed documents from checkpoints -------------------
        completed = {} if ocr_config.force else self._load_checkpoints()
        if completed:
            done = sum(1 for c in classified if c["input_id"] in completed)
            if done:
                print(f"[supervisor] Resuming: {done}/{len(classified)} docs already complete.")
        pending = [c for c in classified if c["input_id"] not in completed]

        # --- 3. TRUE ZERO CPU IDLE DETACHED MODE: Spawn directly to GPU Worker & EXIT ---
        if detach and pending:
            batches = _group_batches(pending, policy)
            print(
                f"[supervisor] TRUE ZERO CPU IDLE DETACH MODE: Spawning {len(batches)} GPU batches directly to GpuWorker..."
            )
            kwargs: dict[str, Any] = {
                "config": ocr_config,
                "source": "ainvest_financial",
                "target": "output",
                "write": True,
                "return_payloads": True,
            }
            for batch in batches:
                batch_items = [_document_input(d) for d in batch]
                self.gpu_worker.process_batch.spawn(batch_items, **kwargs)

            cpu_elapsed = round(time.time() - self._run_started, 3)
            print(
                f"[supervisor] All {len(batches)} GPU batches spawned! CPU Supervisor shutting down NOW ({cpu_elapsed}s)."
            )
            return {
                "status": "detached_true_zero_cpu_idle",
                "gpu_batches": len(batches),
                "classified_docs": len(classified),
                "pending_docs": len(pending),
                "cpu_elapsed_seconds": cpu_elapsed,
            }

        # --- Standard Synchronous Flow -----------------------------------------
        md_by_id: dict[str, str] = {}
        gpu_seconds_by_id: dict[str, float] = {}
        batches: list[list[dict[str, Any]]] = []
        if pending:
            batches = _group_batches(pending, policy)
            batch_items = [[_document_input(d) for d in batch] for batch in batches]
            print(
                f"[supervisor] {len(batches)} GPU batches "
                f"({len(pending)} docs, "
                f"{sum(len(d.get('data') or b'') for d in pending) // (1024 * 1024)} MB pruned)."
            )
            kwargs: dict[str, Any] = {
                "config": ocr_config,
                "source": "ainvest_financial",
                "target": "output",
                "write": False,
                "return_payloads": True,
            }
            results = list(
                self.gpu_worker.process_batch.map(
                    batch_items,
                    kwargs=kwargs,
                    order_outputs=True,
                    return_exceptions=True,
                )
            )

            failed = [i for i, r in enumerate(results) if isinstance(r, Exception)]
            attempt = 0
            while failed and attempt < max_retries:
                attempt += 1
                print(
                    f"[supervisor] Retrying {len(failed)} failed batches "
                    f"(attempt {attempt}/{max_retries})..."
                )
                for i in failed:
                    try:
                        results[i] = self.gpu_worker.process_batch.remote(batch_items[i], **kwargs)
                    except Exception as exc:  # noqa: BLE001
                        results[i] = exc
                failed = [i for i, r in enumerate(results) if isinstance(r, Exception)]
            if failed:
                raise RuntimeError(
                    f"{len(failed)} GPU batches still failed after {max_retries} retries"
                )

            for result in results:
                if isinstance(result, Exception):
                    continue
                for entry in result.get("documents", []):
                    stats = entry.get("stats") or {}
                    sid = stats.get("input_id")
                    if sid is None:
                        continue
                    md_by_id[sid] = entry.get("markdown", "")
                    durations = stats.get("durations_seconds") or {}
                    gpu_seconds_by_id[sid] = durations.get("total") or 0.0

        outputs, failed_urls = self._assemble_outputs(
            classified, completed, md_by_id, gpu_seconds_by_id, enable_filtering
        )
        return {
            "outputs": outputs,
            "failed": failed_urls,
            "elapsed_seconds": round(time.time() - self._run_started, 3),
            "gpu_batches": len(batches),
        }

    # ------------------------------------------------------------------ helpers

    def _checkpoint_dir(self) -> str:
        return os.path.join(OUTPUT_VOLUME_PATH, "checkpoints")

    def _load_checkpoints(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        root = self._checkpoint_dir()
        if not os.path.isdir(root):
            return found
        for name in os.listdir(root):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if payload.get("status") == "succeeded":
                    found[payload["input_id"]] = payload
            except Exception:  # noqa: BLE001
                continue
        return found

    def _write_checkpoint(self, payload: dict[str, Any]) -> None:
        try:
            os.makedirs(self._checkpoint_dir(), exist_ok=True)
            path = os.path.join(self._checkpoint_dir(), f"{payload['input_id']}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[supervisor] Checkpoint write failed for {payload.get('input_id')}: {exc}")

    def _assemble_outputs(
        self,
        classified: list[dict[str, Any]],
        completed: dict[str, dict[str, Any]],
        md_by_id: dict[str, str],
        gpu_seconds_by_id: dict[str, float],
        enable_filtering: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        from financial_pipeline.config import load_profile
        from financial_pipeline.region_classifier import RegionClassifier

        region_classifier = RegionClassifier(load_profile("financial_profile.yaml"))
        outputs: list[dict[str, Any]] = []
        failed_urls: list[str] = []
        for doc in classified:
            input_id = doc["input_id"]
            if input_id in completed:
                outputs.append(completed[input_id])
                continue

            markdown = md_by_id.get(input_id, "")
            gpu_seconds = gpu_seconds_by_id.get(input_id, 0.0)
            if input_id not in md_by_id:
                print(f"[supervisor] No GPU result for {input_id} → failed, not checkpointed.")
                failed_urls.append(doc["url"])
                continue

            region_seconds = 0.0
            if enable_filtering and markdown:
                t0 = time.time()
                markdown, _ = region_classifier.filter_markdown_sections(markdown)
                region_seconds = time.time() - t0

            payload = self._build_payload(doc, markdown, gpu_seconds, region_seconds)
            self._write_checkpoint(payload)
            outputs.append(payload)
        return outputs, failed_urls

    def _build_payload(
        self,
        doc: dict[str, Any],
        markdown: str,
        gpu_seconds: float,
        region_seconds: float,
    ) -> dict[str, Any]:
        classify_sec = float(doc.get("download_seconds", 0.0)) + float(
            doc.get("classify_seconds", 0.0)
        )
        total_pages = int(doc.get("total_pages", 0))
        skipped_pages = int(doc.get("skipped_pages", 0))
        reduction = round((skipped_pages / total_pages * 100.0), 1) if total_pages else 0.0
        total = classify_sec + gpu_seconds + region_seconds
        metrics = {
            "document_id": doc["input_id"],
            "filename": doc["filename"],
            "pdf_size_bytes": int(doc.get("pdf_size_bytes", 0)),
            "total_pdf_pages": total_pages,
            "retained_pages": int(doc.get("retained_pages", 0)),
            "skipped_pages": skipped_pages,
            "page_reduction_ratio_pct": reduction,
            "time_page_classification_sec": round(classify_sec, 3),
            "time_modal_ocr_sec": round(gpu_seconds, 3),
            "time_region_filtering_sec": round(region_seconds, 3),
            "total_elapsed_sec": round(total, 3),
            "markdown_char_count": len(markdown),
            "estimated_tokens": int(len(markdown) / 3.5),
            "modal_gpu": "L40S",
        }
        return {
            "input_id": doc["input_id"],
            "url": doc["url"],
            "filename": doc["filename"],
            "markdown": markdown,
            "metrics": metrics,
            "page_result": {
                "total_pages": total_pages,
                "retained_pages_count": int(doc.get("retained_pages", 0)),
                "skipped_pages_count": skipped_pages,
                "pages_meta": doc.get("pages_meta", []),
                "retained_page_indices": doc.get("retained_page_indices", []),
            },
            "status": "succeeded",
        }
