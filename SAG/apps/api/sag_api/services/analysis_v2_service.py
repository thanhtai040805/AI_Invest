from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import Document, DocumentFact, DocumentRelation
from sag_api.services.document_structure_service import ACTIVE_DOCUMENT_ROLES, list_active_documents_by_ticker
from sag_api.services.gil_service import GILGraphAnalyzer
from sag_api.services.gil_service import assess_gil as assess_gil_v2

def _role_set(documents: list[Document]) -> set[str]:
    return {str(doc.doc_role) for doc in documents if doc.doc_role}


def _evidence_from_fact(fact: DocumentFact, document_by_id: dict[str, Document]) -> dict[str, Any]:
    doc = document_by_id.get(fact.document_id)
    return {
        "document_id": fact.document_id,
        "doc_role": doc.doc_role if doc else None,
        "node_id": fact.node_id,
        "line_span": [fact.start_line, fact.end_line],
        "quote_hash": fact.quote_hash,
        "label": fact.label,
        "period": fact.period,
    }


async def assess_financial_quality_by_ticker(session: AsyncSession, ticker: str) -> dict[str, Any]:
    """Tổng hợp chất lượng evidence hiện có, không chấm lợi thế cạnh tranh."""
    _source, documents = await list_active_documents_by_ticker(session, ticker)
    document_ids = [doc.id for doc in documents]
    facts = []
    if document_ids:
        facts = list((await session.execute(
            select(DocumentFact).where(DocumentFact.document_id.in_(document_ids))
        )).scalars())
    evidence = [_evidence_from_fact(fact, {doc.id: doc for doc in documents}) for fact in facts]
    return {
        "ticker": ticker.upper().strip(),
        "assessment_status": "COMPLETE" if evidence else "INSUFFICIENT",
        "quality_score": None,
        "evidence_status": "VERIFIED" if evidence else "INSUFFICIENT",
        "coverage_ratio": 1.0 if evidence else 0.0,
        "active_roles": sorted(_role_set(documents)),
        "missing_roles": [role for role in ACTIVE_DOCUMENT_ROLES if role not in _role_set(documents)],
        "evidence": evidence,
        "reasons": [] if evidence else ["Chưa có evidence định lượng hoặc mô tả đã xác minh"],
    }


async def assess_gil_by_ticker(
    session: AsyncSession,
    ticker: str,
    *,
    equity_override_vnd: float | None = None,
    equity_provenance: str | None = None,
) -> dict[str, Any]:
    """Compatibility adapter; the V2 database-backed GIL is canonical."""
    return await assess_gil_v2(session, ticker)
