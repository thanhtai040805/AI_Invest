"""Run the production BCTC-to-SAG pipeline across a representative sector set.

This is an integration benchmark, not a fixture audit.  Each ticker is resolved
by ActiveDocumentSelector and then processed by BctcToSagPipeline, including
the classifier, canonical R2 artifacts, SAG ingestion, extraction, embeddings,
and GIL assessment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Windows consoles may default to cp1258; benchmark rows contain Vietnamese
# error details and must not terminate the entire queue while being printed.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx
import psycopg2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.sag_connector import SAGConnector
from app.domain.pipeline.bctc_to_sag_pipeline import (
    BctcToSagPipeline,
    OCR_DOCUMENT_CONCURRENCY,
    OCR_UPLOAD_CONCURRENCY,
)
from app.domain.services.document_selector import ActiveDocumentSelector


DEFAULT_TICKERS = ("HPG", "TCB", "HCM", "VNM", "VIC")
POLL_SECONDS = 5
READY_TIMEOUT_SECONDS = 900
# Five tickers may submit documents together.  MinerUClient remains the single
# global gate: with two keys at five slots/key this yields at most ten OCR
# files, regardless of which ticker owns them.
BENCHMARK_CONCURRENCY = max(1, min(len(DEFAULT_TICKERS), int(os.getenv("SAG_BENCHMARK_TICKER_CONCURRENCY", "5"))))
REQUIRED_DOCUMENT_ROLES = {
    "ANNUAL_BACKBONE",
    "LATEST_QUARTER",
    "GOVERNANCE_REPORT",
}
EXPECTED_EXTRACTION_PROMPT_VERSION = "sag-evidence-annotation-v30-vi"
EXPECTED_ANNUAL_PROMPT_VERSION = "sag-evidence-annotation-v31-vi"
EXPECTED_GOVERNANCE_PROMPT_VERSION = "governance-evidence-annotation-v5-vi"


async def fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    """Read a SAG endpoint resiliently; processing may briefly restart a socket."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            last_error = error
            if attempt == 2:
                raise
            await asyncio.sleep(1.0 * (attempt + 1))
    raise RuntimeError("unreachable") from last_error


async def wait_for_document(
    client: httpx.AsyncClient, api_base: str, document_id: str
) -> dict[str, Any]:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await fetch_json(client, f"{api_base}/documents/{document_id}")
        if last.get("status") in {"READY", "FAILED", "CANCELLED"}:
            return last
        await asyncio.sleep(POLL_SECONDS)
    last["benchmark_timeout"] = True
    return last


async def collect_sag_evidence(
    client: httpx.AsyncClient, api_base: str, ticker: str, document_ids: set[str]
) -> dict[str, Any]:
    evidence = await fetch_json(client, f"{api_base}/tickers/{ticker}/evidence-graph")
    observations = 0
    for document_id in document_ids:
        rows = await fetch_json(client, f"{api_base}/documents/{document_id}/observations")
        observations += len(rows)

    facets = await fetch_json(client, f"{api_base}/tickers/{ticker}/facets")
    documents = await fetch_json(client, f"{api_base}/tickers/{ticker}/documents")
    document_fact_counts = {document["id"]: int(document.get("fact_count") or 0) for document in documents}
    return {
        "facts": sum(document_fact_counts.get(document_id, 0) for document_id in document_ids),
        "observations": observations,
        "facets": sum(1 for facet in facets if facet.get("document_id") in document_ids),
        "relations": sum(
            1
            for relation in evidence.get("relations") or []
            if relation.get("source_document_id") in document_ids
            or relation.get("target_document_id") in document_ids
        ),
    }


def extraction_quality(status: dict[str, Any]) -> dict[str, Any]:
    """Expose server-side extraction diagnostics as benchmark evidence.

    Counts alone are not a quality signal: the SAG document status already
    carries grounding, table-enrichment and prompt-version diagnostics.
    """
    coverage = status.get("coverage") or {}
    extraction = coverage.get("extraction") or {}
    dropped = extraction.get("dropped_ungrounded_paths") or []
    repaired = extraction.get("repair_paths") or []
    return {
        "prompt_version": extraction.get("prompt_version"),
        "taxonomy_version": extraction.get("taxonomy_version"),
        "mode": extraction.get("mode"),
        "attempts": extraction.get("attempts"),
        "finish_reason": extraction.get("finish_reason"),
        "ungrounded_facts_dropped": int(extraction.get("ungrounded_facts_dropped") or 0),
        "ungrounded_relation_amounts_stripped": int(
            extraction.get("ungrounded_relation_amounts_stripped") or 0
        ),
        "ungrounded_relation_pcts_stripped": int(
            extraction.get("ungrounded_relation_pcts_stripped") or 0
        ),
        "dropped_ungrounded_count": len(dropped),
        "repaired_grounding_count": len(repaired),
        "table_fact_added": int(extraction.get("table_fact_added") or 0),
        "deterministic_node_annotations_added": int(
            extraction.get("deterministic_node_annotations_added") or 0
        ),
        "coverage_ratio": coverage.get("coverage_ratio"),
        "node_count": coverage.get("node_count"),
        "fact_count": status.get("fact_count"),
    }


def extraction_quality_failures(
    quality: dict[str, Any], expected_prompt_version: str = EXPECTED_EXTRACTION_PROMPT_VERSION
) -> list[str]:
    failures: list[str] = []
    if quality.get("prompt_version") != expected_prompt_version:
        failures.append(
            f"prompt_version_mismatch:{quality.get('prompt_version')}!={expected_prompt_version}"
        )
    if quality.get("ungrounded_facts_dropped", 0) > 0:
        failures.append(f"ungrounded_facts_dropped:{quality['ungrounded_facts_dropped']}")
    if quality.get("dropped_ungrounded_count", 0) > 0:
        failures.append(f"dropped_ungrounded_items:{quality['dropped_ungrounded_count']}")
    return failures


async def run_ticker(
    ticker: str,
    pipeline: BctcToSagPipeline,
    selector: ActiveDocumentSelector,
    connector: SAGConnector,
    *,
    ocr_only: bool = False,
    extraction_only: bool = False,
    force_reprocess: bool = False,
) -> dict[str, Any]:
    selected = selector.select_active_documents(ticker)
    selected_roles = {doc.role for doc in selected.all_documents}
    selector_roles_complete = REQUIRED_DOCUMENT_ROLES.issubset(selected_roles)
    started = time.monotonic()
    result = await pipeline.process_ticker(
        ticker,
        ocr_only=ocr_only,
        extraction_only=extraction_only,
        force_reprocess=force_reprocess,
    )
    elapsed_seconds = round(time.monotonic() - started, 1)
    documents = result.get("documents") or []
    ids = {doc["sag_doc_id"] for doc in documents if doc.get("sag_doc_id")}

    if ocr_only:
        return {
            "ticker": ticker,
            "selector_complete": selected.is_complete,
            "selected_roles": sorted(selected_roles),
            "required_roles_complete": selector_roles_complete,
            "pipeline_status": result.get("status"),
            "elapsed_seconds": elapsed_seconds,
            "document_concurrency": OCR_DOCUMENT_CONCURRENCY,
            "upload_concurrency": OCR_UPLOAD_CONCURRENCY,
            "documents": documents,
            "sag_statuses": {},
            "evidence": {},
            "gil": None,
            "passed": selector_roles_complete
            and result.get("status") == "OCR_COMPLETED"
            and bool(documents)
            and all(
                doc.get("status") in {"OCR_COMPLETED", "SUCCESS", "SUCCESS_CACHED", "INGESTED_TO_SAG_FROM_R2_MARKDOWN"}
                and bool(doc.get("r2_md_key"))
                for doc in documents
            ),
        }

    headers = connector._headers()
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60.0)) as client:
        statuses = {
            document_id: await wait_for_document(client, connector.api_base, document_id)
            for document_id in ids
        }
        evidence = await collect_sag_evidence(client, connector.api_base, ticker, ids) if ids else {}

    quality_by_document = {
        document_id: extraction_quality(status)
        for document_id, status in statuses.items()
    }
    role_by_document_id = {
        document_id: str(status.get("doc_role") or "").upper()
        for document_id, status in statuses.items()
    }
    quality_failures = {}
    for document_id, quality in quality_by_document.items():
        expected = (
            EXPECTED_GOVERNANCE_PROMPT_VERSION
            if role_by_document_id.get(document_id) == "GOVERNANCE_REPORT"
            else EXPECTED_ANNUAL_PROMPT_VERSION
            if role_by_document_id.get(document_id) == "ANNUAL_BACKBONE"
            else EXPECTED_EXTRACTION_PROMPT_VERSION
        )
        failures = extraction_quality_failures(quality, expected)
        if failures:
            quality_failures[document_id] = failures

    documents_ready = bool(ids) and all(
        status.get("status") == "READY"
        and status.get("extraction_status") == "COMPLETE"
        and status.get("embedding_status") == "COMPLETE"
        for status in statuses.values()
    )
    actual_roles = {
        str(status.get("doc_role") or "").upper()
        for status in statuses.values()
        if status.get("doc_role")
    }
    # Do not fall back to selector roles: selected input is not evidence that
    # SAG actually ingested the corresponding document.
    roles_complete = REQUIRED_DOCUMENT_ROLES.issubset(actual_roles)
    gil = result.get("gil_result") or {}
    gil_technical_error = gil.get("analysis_status") == "TECHNICAL_ERROR"
    return {
        "ticker": ticker,
        "selector_complete": selected.is_complete,
        "selected_roles": sorted(selected_roles),
        "required_roles_complete": selector_roles_complete,
        "pipeline_status": result.get("status"),
        "elapsed_seconds": elapsed_seconds,
        "documents": documents,
        "sag_statuses": statuses,
        "extraction_quality": quality_by_document,
        "extraction_quality_failures": quality_failures,
        "actual_roles": sorted(actual_roles),
        "roles_complete": roles_complete,
        "evidence": evidence,
        "gil": result.get("gil_result"),
        "passed": selector_roles_complete
        and roles_complete
        and documents_ready
        and not quality_failures
        and not gil_technical_error
        and not any(doc.get("status") == "FAILED" for doc in documents),
    }


async def main(
    tickers: tuple[str, ...],
    output: Path,
    *,
    ocr_only: bool = False,
    extraction_only: bool = False,
    force_reprocess: bool = False,
) -> int:
    selector = ActiveDocumentSelector()
    connector = SAGConnector()
    pipeline = BctcToSagPipeline(selector=selector, connector=connector)
    results: list[dict[str, Any]] = []
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for index, ticker in enumerate(tickers):
        queue.put_nowait((index, ticker))
    results_by_index: dict[int, dict[str, Any]] = {}
    checkpoint_lock = asyncio.Lock()

    async def write_checkpoint() -> None:
        ordered = [results_by_index[index] for index in sorted(results_by_index)]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "kind": "multisector_bctc_to_sag_integration_benchmark",
            "created_at": datetime.now(UTC).isoformat(),
            "tickers": list(tickers),
            "completed_tickers": len(ordered),
            "concurrency": BENCHMARK_CONCURRENCY,
            "passed": all(item.get("passed") for item in ordered),
            "results": ordered,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    async def worker(worker_id: int) -> None:
        while True:
            try:
                index, ticker = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            mode = (
                "selector -> R2 Markdown -> SAG extraction"
                if extraction_only
                else "selector -> source URL -> SAG OCR -> R2 Markdown"
                if ocr_only
                else "selector -> source URL -> SAG -> GIL"
            )
            print(f"\n[worker {worker_id}] === {ticker}: {mode} ===", flush=True)
            try:
                row = await run_ticker(
                    ticker, pipeline, selector, connector,
                    ocr_only=ocr_only, force_reprocess=force_reprocess,
                    extraction_only=extraction_only,
                )
            except Exception as error:  # Continue so one issuer cannot hide other failures.
                row = {"ticker": ticker, "passed": False, "error": f"{type(error).__name__}: {error}"}
            results_by_index[index] = row
            print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
            async with checkpoint_lock:
                await write_checkpoint()
            queue.task_done()

    await asyncio.gather(*(worker(worker_id) for worker_id in range(BENCHMARK_CONCURRENCY)))
    results = [results_by_index[index] for index in sorted(results_by_index)]

    report = {
        "kind": "multisector_bctc_to_sag_integration_benchmark",
        "created_at": datetime.now(UTC).isoformat(),
        "tickers": list(tickers),
        "concurrency": BENCHMARK_CONCURRENCY,
        "document_concurrency": OCR_DOCUMENT_CONCURRENCY,
        "upload_concurrency": OCR_UPLOAD_CONCURRENCY,
        "passed": all(row.get("passed") for row in results),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {output}", flush=True)
    print(f"Overall: {'PASS' if report['passed'] else 'FAIL'}", flush=True)
    return 0 if report["passed"] else 1


def load_all_db_tickers() -> tuple[str, ...]:
    """Load every issuer with at least one source PDF from the shared DB."""
    selector = ActiveDocumentSelector()
    with psycopg2.connect(selector.db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT upper(symbol)
                FROM knowledge_documents
                WHERE symbol IS NOT NULL
                  AND article_pdf_urls IS NOT NULL
                  AND cardinality(article_pdf_urls) > 0
                ORDER BY upper(symbol)
            """)
            return tuple(row[0] for row in cur.fetchall() if row[0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".data" / "benchmarks" / "multisector_bctc_to_sag.json",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="Chỉ chạy OCR trực tiếp từ URL nguồn và lưu Markdown lên R2; bỏ qua extraction, embedding và GIL.",
    )
    parser.add_argument(
        "--extraction-only",
        action="store_true",
        help="Chỉ ingest Markdown OCR đã có trên R2 vào SAG để chạy extraction/embedding; không gọi MinerU.",
    )
    parser.add_argument(
        "--all-db",
        action="store_true",
        help="Tự lấy toàn bộ mã có PDF trong knowledge_documents.",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Bỏ qua cache Markdown/DB của pipeline và chạy lại các tài liệu đã chọn.",
    )
    args = parser.parse_args()
    selected_tickers = load_all_db_tickers() if args.all_db else tuple(
        item.strip().upper() for item in args.tickers.split(",") if item.strip()
    )
    print(f"Selected tickers: {len(selected_tickers)}", flush=True)
    raise SystemExit(asyncio.run(main(
        selected_tickers,
        args.output,
        ocr_only=args.ocr_only,
        extraction_only=args.extraction_only,
        force_reprocess=args.force_reprocess,
    )))
