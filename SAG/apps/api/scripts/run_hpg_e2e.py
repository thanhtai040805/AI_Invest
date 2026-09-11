from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))
os.chdir(API_ROOT)


def _load_api_env() -> None:
    """Always load SAG/apps/api/.env before importing sag_api.

    Keeps the script rooted at the API root so .env, .data, DB URL, LLM key,
    embedding key, and MinerU config resolve correctly. Existing process env
    wins over file values.
    """
    env_path = API_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_api_env()

from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from sag_api.core.config import settings  # noqa: E402
from sag_api.core.db import SessionLocal, dispose_db  # noqa: E402
from sag_api.db.models import Document, DocumentAsset, DocumentTreeNode, EmbeddingChunk, EvidenceSpan, Fact, Issuer, MoatSignal, Relation  # noqa: E402
from sag_api.schemas.v2 import DocumentCreateIn  # noqa: E402
from sag_api.services.financial_v2_service import (  # noqa: E402
    assess_gil,
    assess_moat,
    activate_document,
    create_financial_document,
    hydrate_markdown,
    rebuild_structure_and_embeddings,
)


FIXTURE_ROOT = API_ROOT / ".data" / "uploads" / "e4ad6a479a9043edaa6f5c865d8e9154"


DOCUMENTS = [
    {
        "label": "latest_quarter",
        "path": FIXTURE_ROOT / "e24ba28484e84a0cbe0a7ca1a5187e99_HPG_2026_Q2_SEPARATE_vlm_clean.md.clean.1.md",
        "body": {
            "title": "HPG 2026 Q2 BCTC",
            "doc_role": "LATEST_QUARTER",
            "fiscal_year": 2026,
            "fiscal_quarter": 2,
            "period_end": "2026-06-30",
            "activate": True,
        },
    },
]


def _log(message: str) -> None:
    # Logs go to stderr so stdout carries only the final JSON document.
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True, file=sys.stderr)


def _emit_json(payload: dict[str, Any]) -> None:
    """Print final JSON as UTF-8 bytes (Windows console is often cp1258)."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except (AttributeError, OSError):
        print(text, flush=True)


def _error_payload(exc: Exception) -> dict[str, str]:
    return {"error_type": type(exc).__name__, "error": str(exc)[:2000]}


def _safe_database_url() -> str:
    return str(make_url(settings.database_url).render_as_string(hide_password=True))


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}***{text[-4:]}"


def _preflight_snapshot() -> dict[str, Any]:
    """Runner preflight: confirm DB/LLM/extraction/embedding config with masked secrets."""
    return {
        "database_url": _safe_database_url(),
        "db_host_port": f"{settings.sag_pg_host}:{settings.sag_pg_port}",
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "routed_model": settings.routed_llm_model,
            "base_url": settings.llm_base_url,
            "api_key_configured": bool(settings.llm_api_key),
            "api_key_masked": _mask_secret(settings.llm_api_key),
            "context_window": settings.llm_context_window,
            "max_tokens": settings.llm_max_tokens,
            "timeout_ms": settings.llm_timeout_ms,
            "max_retries": settings.llm_max_retries,
        },
        "extraction": {
            "model": settings.extraction_llm_model or settings.llm_model,
            "routed_model": settings.routed_extraction_llm_model,
            "base_url": settings.effective_extraction_llm_base_url,
            "api_key_configured": bool(settings.effective_extraction_llm_api_key),
            "api_key_masked": _mask_secret(settings.effective_extraction_llm_api_key),
            "mode": "full_document_single_call",
        },
        "embedding": {
            "model": settings.embedding_model,
            "routed_model": settings.routed_embedding_model,
            "base_url": settings.effective_embedding_base_url,
            "api_key_configured": bool(settings.effective_embedding_api_key),
            "api_key_masked": _mask_secret(settings.effective_embedding_api_key),
            "dimensions": settings.embedding_dimensions,
        },
        "mineru": {
            "base_url": settings.mineru_base_url,
            "configured": bool(settings.mineru_configured),
            "api_key_configured": bool(settings.mineru_api_key),
            "api_key_masked": _mask_secret(settings.mineru_api_key),
            "version": settings.mineru_version,
            "parse_method": settings.mineru_parse_method,
            "model_version": settings.mineru_model_version,
            "mode": settings.mineru_mode,
        },
        "document": {
            "chunk_mode": settings.document_chunk_mode,
            "chunk_max_tokens": settings.document_chunk_max_tokens,
            "extraction_calls_per_file": 1,
        },
    }


async def _count(session, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    for item in criteria:
        stmt = stmt.where(item)
    return int(await session.scalar(stmt) or 0)


async def main(labels: list[str] | None = None, *, reprocess: bool = False) -> None:
    _log("Starting HPG E2E")
    preflight = _preflight_snapshot()
    targets = [item for item in DOCUMENTS if labels is None or item["label"] in labels]
    if labels is not None and not targets:
        raise SystemExit(f"Unknown --labels {labels!r}; expected subset of {[d['label'] for d in DOCUMENTS]}")
    output: dict[str, Any] = {
        "ticker": "HPG",
        "status": "RUNNING",
        "database_url": preflight["database_url"],
        "preflight": preflight,
        "labels": [item["label"] for item in targets],
        "documents": [],
    }
    for item in targets:
        if not item["path"].exists():
            raise SystemExit(f"Missing fixture: {item['path']}")
        _log(f"Fixture OK: {item['label']} -> {item['path'].name}")
    _log(
        "Preflight: db=" + preflight["database_url"]
        + f" llm={preflight['llm']['provider']}/{preflight['llm']['model']}@{preflight['llm']['base_url']}"
        + f" key_configured={preflight['llm']['api_key_configured']}"
        + f" extraction={preflight['extraction']['routed_model']}@{preflight['extraction']['base_url']}"
        + f" key_configured={preflight['extraction']['api_key_configured']}"
        + f" embedding={preflight['embedding']['routed_model']}@{preflight['embedding']['base_url']}"
        + f" dims={preflight['embedding']['dimensions']} key_configured={preflight['embedding']['api_key_configured']}"
        + f" chunk_mode={preflight['document']['chunk_mode']} calls_per_file=1"
    )

    try:
        async with SessionLocal() as session:
            await asyncio.wait_for(session.scalar(text("select 1")), timeout=15)
            _log("Database preflight OK")
    except Exception as exc:  # noqa: BLE001
        _log(f"Database preflight failed: {type(exc).__name__}: {exc}")
        output["status"] = "ERROR"
        output["coverage"] = {
            **_error_payload(exc),
            "stage": "database_preflight",
            "hint": "Start the configured PostgreSQL/pgvector E2E database and run Alembic migrations.",
        }
        _emit_json(output)
        await dispose_db()
        return

    try:
        async with SessionLocal() as session:
            # Each HPG document runs as exactly one full-document LLM extraction
            # request (see extraction_v2_service.extract_and_persist_manifest).
            # No validation retry, no chunked fallback.
            for item in targets:
                _log(f"Ingest/process start: {item['label']} (full-document single LLM call)")
                body = DocumentCreateIn(object_uri=str(item["path"]), **item["body"])
                try:
                    document, deduplicated = await asyncio.wait_for(
                        create_financial_document(session, "HPG", body),
                        timeout=1800,
                    )
                    if reprocess and deduplicated:
                        issuer = await session.get(Issuer, document.issuer_id)
                        asset = await session.get(DocumentAsset, document.asset_id) if document.asset_id else None
                        if issuer is None:
                            raise RuntimeError(f"Issuer missing for document {document.id}")
                        _log(f"Reprocess forced: {item['label']} (full-document single LLM call)")
                        markdown = await hydrate_markdown(document, asset, session)
                        await asyncio.wait_for(
                            rebuild_structure_and_embeddings(session, issuer, document, markdown),
                            timeout=1800,
                        )
                        if document.extraction_status == "COMPLETE" and document.embedding_status == "COMPLETE":
                            await activate_document(session, issuer.id, document)
                    await session.commit()
                except TimeoutError as exc:
                    await session.rollback()
                    _log(f"Ingest/process timeout: {item['label']}")
                    output["documents"].append(
                        {
                            "label": item["label"],
                            "document_id": None,
                            "deduplicated": False,
                            "status": "TIMEOUT",
                            "active": False,
                            "structure_status": None,
                            "extraction_status": "FAILED",
                            "embedding_status": "INCOMPLETE",
                            "fact_count": 0,
                            "coverage": {**_error_payload(exc), "error": "document processing timeout"},
                        }
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    _log(f"Ingest/process error: {item['label']} {type(exc).__name__}: {exc}")
                    output["documents"].append(
                        {
                            "label": item["label"],
                            "document_id": None,
                            "deduplicated": False,
                            "status": "ERROR",
                            "active": False,
                            "structure_status": None,
                            "extraction_status": "FAILED",
                            "embedding_status": "INCOMPLETE",
                            "fact_count": 0,
                            "coverage": _error_payload(exc),
                        }
                    )
                    continue
                _log(
                    "Ingest/process done: "
                    f"{item['label']} status={document.status.value if hasattr(document.status, 'value') else document.status} "
                    f"structure={document.structure_status} extraction={document.extraction_status} embedding={document.embedding_status}"
                )
                output["documents"].append(
                    {
                        "label": item["label"],
                        "document_id": document.id,
                        "deduplicated": deduplicated,
                        "status": document.status.value if hasattr(document.status, "value") else document.status,
                        "active": bool(document.is_active),
                        "structure_status": document.structure_status,
                        "extraction_status": document.extraction_status,
                        "embedding_status": document.embedding_status,
                        "fact_count": document.fact_count,
                        "coverage": document.coverage,
                    }
                )

            _log("Building assessment summary")
            assessment_errors: dict[str, Any] = {}
            try:
                issuer = await session.scalar(select(Issuer).where(Issuer.ticker == "HPG"))
                if issuer is not None:
                    output["counts"] = {
                        "documents": await _count(session, Document, Document.issuer_id == issuer.id),
                        "active_documents": await _count(
                            session,
                            Document,
                            Document.issuer_id == issuer.id,
                            Document.is_active.is_(True),
                        ),
                        "nodes": await _count(session, DocumentTreeNode, DocumentTreeNode.issuer_id == issuer.id),
                        "evidence_spans": await _count(session, EvidenceSpan, EvidenceSpan.issuer_id == issuer.id),
                        "facts": await _count(session, Fact, Fact.issuer_id == issuer.id),
                        "relations": await _count(session, Relation, Relation.issuer_id == issuer.id),
                        "moat_signals": await _count(session, MoatSignal, MoatSignal.issuer_id == issuer.id),
                        "embedding_chunks": await _count(session, EmbeddingChunk, EmbeddingChunk.issuer_id == issuer.id),
                    }
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                _log(f"Assessment counts error: {type(exc).__name__}: {exc}")
                assessment_errors["counts"] = _error_payload(exc)
            try:
                output["moat"] = await assess_moat(session, "HPG")
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                _log(f"Assessment MOAT error: {type(exc).__name__}: {exc}")
                assessment_errors["moat"] = _error_payload(exc)
            try:
                output["gil"] = await assess_gil(session, "HPG")
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                _log(f"Assessment GIL error: {type(exc).__name__}: {exc}")
                assessment_errors["gil"] = _error_payload(exc)
            if assessment_errors:
                output["assessment_error"] = assessment_errors

        output["status"] = (
            "OK"
            if output["documents"]
            and all(doc["status"] == "READY" for doc in output["documents"])
            and "assessment_error" not in output
            else "ERROR"
        )
        _log("HPG E2E finished")
        _emit_json(output)
    finally:
        await dispose_db()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HPG E2E single-file LLM extraction")
    parser.add_argument(
        "--labels",
        default=None,
        help="Comma-separated subset of {latest_quarter}; default runs all entries.",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Rebuild a deduplicated fixture to validate the current extraction implementation.",
    )
    args = parser.parse_args()
    selected = [part.strip() for part in args.labels.split(",") if part.strip()] if args.labels else None
    asyncio.run(main(selected, reprocess=args.reprocess))
