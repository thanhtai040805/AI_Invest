from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import func, select  # noqa: E402

from sag_api.core.db import SessionLocal, dispose_db  # noqa: E402
from sag_api.db.models import Document, DocumentTreeNode, EmbeddingChunk, EvidenceSpan, Fact, Issuer, MoatSignal, Relation  # noqa: E402
from sag_api.schemas.v2 import DocumentCreateIn  # noqa: E402
from sag_api.services.financial_v2_service import assess_gil, assess_moat, create_financial_document  # noqa: E402


FIXTURE_ROOT = API_ROOT / ".data" / "uploads" / "2c9cd9cd348c499f957b8543f30b3cc6"


DOCUMENTS = [
    {
        "label": "annual",
        "path": FIXTURE_ROOT / "9c52025b765d43d19e9004a01ef9e428_HPG_2025_YEAR_SEPARATE_pruned.pdf.parsed.mineru-4.0-ocr.md.clean.1.md",
        "body": {
            "title": "HPG 2025 Annual BCTC",
            "doc_role": "ANNUAL_BACKBONE",
            "fiscal_year": 2025,
            "period_end": "2025-12-31",
            "activate": True,
        },
    },
    {
        "label": "latest_quarter",
        "path": FIXTURE_ROOT / "a8029a53af3747639b421180578c0a60_HPG_2026_Q2_SEPARATE_pruned.pdf.parsed.mineru-4.0-ocr.md.clean.1.md",
        "body": {
            "title": "HPG 2026 Q2 BCTC",
            "doc_role": "LATEST_QUARTER",
            "fiscal_year": 2026,
            "fiscal_quarter": 2,
            "period_end": "2026-06-30",
            "activate": True,
        },
    },
    {
        "label": "governance",
        "path": FIXTURE_ROOT / "b2f607eefa5249f9ad479a26c98590c4_HPG_2026_Q2_GOVERNANCE_pruned.pdf.parsed.mineru-4.0-ocr.md.clean.1.md",
        "body": {
            "title": "HPG 2026 Q2 Governance Report",
            "doc_role": "GOVERNANCE_REPORT",
            "period_start": "2026-01-01",
            "period_end": "2026-06-30",
            "activate": True,
        },
    },
]


def _log(message: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


async def _count(session, model, *criteria) -> int:
    stmt = select(func.count()).select_from(model)
    for item in criteria:
        stmt = stmt.where(item)
    return int(await session.scalar(stmt) or 0)


async def main() -> None:
    _log("Starting HPG E2E")
    for item in DOCUMENTS:
        if not item["path"].exists():
            raise SystemExit(f"Missing fixture: {item['path']}")
        _log(f"Fixture OK: {item['label']} -> {item['path'].name}")

    output: dict[str, Any] = {"ticker": "HPG", "documents": []}
    async with SessionLocal() as session:
        for item in DOCUMENTS:
            _log(f"Ingest/process start: {item['label']}")
            body = DocumentCreateIn(object_uri=str(item["path"]), **item["body"])
            try:
                document, deduplicated = await asyncio.wait_for(
                    create_financial_document(session, "HPG", body),
                    timeout=1200,
                )
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
                        "coverage": {"error_type": type(exc).__name__, "error": "document processing timeout"},
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
                        "coverage": {"error_type": type(exc).__name__, "error": str(exc)[:2000]},
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
        issuer = await session.scalar(select(Issuer).where(Issuer.ticker == "HPG"))
        if issuer is not None:
            output["counts"] = {
                "documents": await _count(session, Document, Document.issuer_id == issuer.id),
                "active_documents": await _count(session, Document, Document.issuer_id == issuer.id, Document.is_active.is_(True)),
                "nodes": await _count(session, DocumentTreeNode, DocumentTreeNode.issuer_id == issuer.id),
                "evidence_spans": await _count(session, EvidenceSpan, EvidenceSpan.issuer_id == issuer.id),
                "facts": await _count(session, Fact, Fact.issuer_id == issuer.id),
                "relations": await _count(session, Relation, Relation.issuer_id == issuer.id),
                "moat_signals": await _count(session, MoatSignal, MoatSignal.issuer_id == issuer.id),
                "embedding_chunks": await _count(session, EmbeddingChunk, EmbeddingChunk.issuer_id == issuer.id),
            }
        output["moat"] = await assess_moat(session, "HPG")
        output["gil"] = await assess_gil(session, "HPG")

    _log("HPG E2E finished")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str), flush=True)
    await dispose_db()


if __name__ == "__main__":
    asyncio.run(main())
