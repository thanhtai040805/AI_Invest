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
    """Compatibility adapter; the V2 database-backed GIL is canonical.

    The former DocumentFact/DocumentRelation analyzer is intentionally no
    longer used for ticker assessments. V1 keeps its direct graph endpoint
    for clients that explicitly submit an in-memory graph.
    """
    return await assess_gil_v2(session, ticker)

    # Legacy implementation retained below temporarily for source-level
    # migration reference; it is unreachable and must not be used by routes.
    _source, documents = await list_active_documents_by_ticker(session, ticker)
    active_roles = sorted(_role_set(documents))
    missing_roles = [role for role in ACTIVE_DOCUMENT_ROLES if role not in active_roles]
    document_ids = [doc.id for doc in documents]
    facts: list[DocumentFact] = []
    relations: list[DocumentRelation] = []
    if document_ids:
        facts = list((await session.execute(select(DocumentFact).where(DocumentFact.document_id.in_(document_ids)))).scalars())
        relations = list(
            (
                await session.execute(
                    select(DocumentRelation).where(
                        DocumentRelation.document_id.in_(document_ids),
                        DocumentRelation.verified.is_(True),
                    )
                )
            ).scalars()
        )

    equity = equity_override_vnd if equity_override_vnd and equity_override_vnd > 0 else _latest_equity(facts)
    reasons: list[str] = []
    if missing_roles:
        reasons.append(f"Thiếu tài liệu active: {', '.join(missing_roles)}")
    if not equity or equity <= 0:
        reasons.append("Thiếu vốn chủ sở hữu có evidence từ BCTC quý/năm")
    if not relations:
        reasons.append("Chưa có quan hệ GIL verified")
    if missing_roles or not equity or equity <= 0 or not relations:
        return {
            "ticker": ticker.upper().strip(),
            "analysis_status": "DATA_INSUFFICIENT",
            "gil_flag": "DATA_INSUFFICIENT",
            "risk_level": "UNKNOWN",
            "rpt_ratio": None,
            "total_rpt_exposure_vnd": 0.0,
            "equity_vnd": equity,
            "cycles_detected": 0,
            "cycle_paths": [],
            "reasons": reasons,
            "nodes_count": 0,
            "edges_count": 0,
        }

    nodes = [{"id": ticker.upper().strip(), "name": ticker.upper().strip(), "entity_type": "TICKER"}]
    edges = [
        {
            "source": rel.subject,
            "target": rel.object,
            "relation_type": rel.relation_type,
            "amount_vnd": rel.amount_vnd or 0.0,
            "ownership_pct": rel.ownership_pct or 0.0,
            "verified": rel.verified,
        }
        for rel in relations
    ]
    analyzer = GILGraphAnalyzer(ticker=ticker, equity_vnd=equity)
    analyzer.build_graph(nodes, edges)
    result = analyzer.evaluate()
    out = result.to_dict()
    out["analysis_status"] = "COMPLETE"
    if equity_override_vnd and equity_override_vnd > 0:
        out["equity_provenance"] = equity_provenance or "override"
    return out


def _latest_equity(facts: list[DocumentFact]) -> float | None:
    candidates = []
    for fact in facts:
        if fact.fact_type != "EQUITY":
            continue
        amount = (fact.metadata_json or {}).get("amount_vnd")
        try:
            value = float(amount)
        except (TypeError, ValueError):
            continue
        candidates.append((fact.period or "", value))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
