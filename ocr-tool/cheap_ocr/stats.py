"""Builders for the persisted per-document stats and run summaries.

The per-document ``stats.json`` is the completion marker for resumable runs:
it is written last, and only ``status == "succeeded"`` counts as complete.
Everything here builds plain JSON-ready dicts — stats are a serialization
artifact, produced in one place (here) and read back leniently in one place
(:func:`cheap_ocr.runs.select_active_documents`).
"""

import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from cheap_ocr.models import DocumentInput, DocumentResult, RecognizedRegion, TokenUsage

PRIVATE_JSON_KEYS = frozenset({"_ocr_usage", "_ocr_latency_ms", "_ocr_status_code"})


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def percentile(values: list[int], pct: int) -> int | None:
    """Return the nearest-rank percentile of ``values``, or None when empty."""
    if not values:
        return None
    sorted_values = sorted(values)
    index = round((len(sorted_values) - 1) * pct / 100)
    return sorted_values[index]


def latency_summary(values: list[int]) -> dict[str, int | None]:
    """Return p50/p95/max latency percentiles."""
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def sanitize_layout_json(value: Any) -> Any:
    """Remove private OCR metadata keys from a JSON-like object."""
    if isinstance(value, list):
        return [sanitize_layout_json(item) for item in cast(list[Any], value)]
    if isinstance(value, dict):
        mapping = cast(Mapping[str, Any], value)
        return {key: sanitize_layout_json(item) for key, item in mapping.items() if key not in PRIVATE_JSON_KEYS}
    return value


def document_input_summary(document: DocumentInput) -> dict[str, Any]:
    """Return the JSON-safe ``input`` section of a stats file (never the payload bytes)."""
    return {
        "uri": document.uri,
        "relative_path": document.relative_path,
        "input_id": document.input_id,
        "metadata": document.metadata,
    }


def aggregate_region_usage(regions: list[RecognizedRegion]) -> TokenUsage:
    """Aggregate token usage across a document's recognized regions."""
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    for region in regions:
        if region.usage is None:
            continue
        input_tokens += region.usage.input_tokens
        output_tokens += region.usage.output_tokens
        total_tokens += region.usage.total_tokens
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


def build_success_stats(
    *,
    document: DocumentInput,
    result: DocumentResult,
    total_seconds: float,
) -> dict[str, Any]:
    """Build compact per-document success stats (the completion marker payload)."""
    timings = result.timings
    labels = Counter(str(region.get("label", "unknown")) for page in result.json for region in page)
    return {
        "input_id": document.input_id,
        "status": "succeeded",
        "input": document_input_summary(document),
        "pages": {"processed": result.page_count},
        "regions": {
            "detected": result.region_count or sum(labels.values()),
            "ocr": result.ocr_region_count,
            "skipped": result.skipped_region_count,
            "by_label": dict(sorted(labels.items())),
        },
        "tokens": asdict(result.tokens),
        "durations_seconds": {
            "wait": timings.wait_seconds,
            "read": timings.read_seconds,
            "render": timings.render_seconds,
            "layout": timings.layout_seconds,
            "crop": timings.crop_seconds,
            "ocr": timings.ocr_seconds,
            "format": timings.format_seconds,
            "write": 0.0,
            "total": total_seconds,
        },
        "created_at": utc_now_iso(),
    }


def build_failed_stats(
    *,
    document: DocumentInput,
    phase: str,
    error: BaseException,
    total_seconds: float,
) -> dict[str, Any]:
    """Build compact per-document failure stats (failed markers are retried)."""
    return {
        "input_id": document.input_id,
        "status": "failed",
        "input": document_input_summary(document),
        "error": {
            "phase": phase,
            "type": type(error).__name__,
            "message": str(error),
            "traceback_preview": "".join(traceback.format_exception(error))[-2000:],
        },
        "durations_seconds": {"total": total_seconds},
        "created_at": utc_now_iso(),
    }


def add_write_duration(stats: dict[str, Any], write_seconds: float) -> None:
    """Add output-write timing to a per-document stats object in-place."""
    raw_durations = stats.get("durations_seconds")
    if not isinstance(raw_durations, dict):
        return
    durations = cast(dict[str, Any], raw_durations)
    previous = durations.get("write")
    previous_write = float(previous) if isinstance(previous, int | float) else 0.0
    durations["write"] = previous_write + write_seconds
    total = durations.get("total")
    if isinstance(total, int | float):
        durations["total"] = float(total) + write_seconds


def build_run_summary(
    *,
    results: Sequence[Mapping[str, Any]],
    source: str,
    target: str,
    wall_seconds: float,
) -> dict[str, Any]:
    """Build a compact run-level summary from per-document stats."""
    documents_total = len(results)
    pages_processed = _sum_nested_int(results, "pages", "processed")
    regions_ocr = _sum_nested_int(results, "regions", "ocr")
    input_tokens = _sum_nested_int(results, "tokens", "input_tokens")
    output_tokens = _sum_nested_int(results, "tokens", "output_tokens")
    total_tokens = _sum_nested_int(results, "tokens", "total_tokens")
    return {
        "scope": "run",
        "status": "completed" if _status_count(results, "failed") == 0 else "completed_with_failures",
        "source": source,
        "target": target,
        "documents": {
            "total": documents_total,
            "succeeded": _status_count(results, "succeeded"),
            "failed": _status_count(results, "failed"),
            "skipped": _status_count(results, "skipped"),
        },
        "pages": {"processed": pages_processed},
        "regions": {"ocr": regions_ocr},
        "tokens": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "durations_seconds": {"wall": wall_seconds},
        "throughput": {
            "pages_per_second": pages_processed / wall_seconds if wall_seconds > 0 else None,
            "ocr_regions_per_second": regions_ocr / wall_seconds if wall_seconds > 0 else None,
            "input_tokens_per_second": input_tokens / wall_seconds if wall_seconds > 0 else None,
            "output_tokens_per_second": output_tokens / wall_seconds if wall_seconds > 0 else None,
            "total_tokens_per_second": total_tokens / wall_seconds if wall_seconds > 0 else None,
        },
        "created_at": utc_now_iso(),
    }


def _status_count(results: Sequence[Mapping[str, Any]], status: str) -> int:
    return sum(1 for result in results if result.get("status") == status)


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _sum_nested_int(results: Sequence[Mapping[str, Any]], section: str, key: str) -> int:
    total = 0
    for result in results:
        raw_section = result.get(section)
        if not isinstance(raw_section, dict):
            continue
        total += _int_or_zero(cast(Mapping[str, Any], raw_section).get(key))
    return total
