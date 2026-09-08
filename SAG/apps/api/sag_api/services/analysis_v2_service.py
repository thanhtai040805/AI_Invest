from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import Document, DocumentFact, DocumentRelation
from sag_api.services.document_structure_service import ACTIVE_DOCUMENT_ROLES, list_active_documents_by_ticker
from sag_api.services.gil_service import GILGraphAnalyzer

MOAT_PILLARS = (
    "Intangibles",
    "Switching Costs",
    "Network Effects",
    "Cost Advantage",
    "Efficient Scale",
)

_PILLAR_KEYWORDS = {
    "Intangibles": ("thương hiệu", "giấy phép", "bằng sáng chế", "nhãn hiệu", "quyền khai thác"),
    "Switching Costs": ("hợp đồng dài hạn", "chi phí chuyển đổi", "khách hàng chiến lược", "tích hợp"),
    "Network Effects": ("mạng lưới", "hệ sinh thái", "người dùng", "đối tác"),
    "Cost Advantage": ("giá vốn", "biên lợi nhuận", "chi phí", "quy mô", "tự chủ", "nguyên liệu"),
    "Efficient Scale": ("thị phần", "công suất", "dự án", "khu vực", "độc quyền", "cảng", "mỏ"),
}


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


async def assess_moat_by_ticker(session: AsyncSession, ticker: str) -> dict[str, Any]:
    _source, documents = await list_active_documents_by_ticker(session, ticker)
    active_roles = sorted(_role_set(documents))
    missing_roles = [role for role in ACTIVE_DOCUMENT_ROLES if role not in active_roles]
    document_by_id = {doc.id: doc for doc in documents}
    facts = []
    if document_by_id:
        facts = list(
            (
                await session.execute(
                    select(DocumentFact).where(DocumentFact.document_id.in_(list(document_by_id)))
                )
            ).scalars()
        )

    pillars: dict[str, dict[str, Any]] = {}
    qualifying_scores: list[float] = []
    evidence_roles: set[str] = set()
    for pillar in MOAT_PILLARS:
        keywords = _PILLAR_KEYWORDS[pillar]
        matched = [
            fact
            for fact in facts
            if any(keyword in (fact.label or "").casefold() for keyword in keywords)
        ][:12]
        evidence = [_evidence_from_fact(fact, document_by_id) for fact in matched]
        for item in evidence:
            if item.get("doc_role"):
                evidence_roles.add(str(item["doc_role"]))
        score = None
        verdict = "NO_EVIDENCE"
        confidence = 0.0
        if evidence:
            score = min(100.0, 50.0 + len(evidence) * 8.0)
            qualifying_scores.append(score)
            verdict = "SUPPORTED"
            confidence = min(0.85, 0.45 + len(evidence) * 0.08)
        pillars[pillar] = {
            "verdict": verdict,
            "score": score,
            "confidence": confidence,
            "evidence": evidence,
            "counter_evidence": [],
        }

    reasons: list[str] = []
    if missing_roles:
        reasons.append(f"Thiếu tài liệu active: {', '.join(missing_roles)}")
    if len(qualifying_scores) < 3:
        reasons.append("Chưa đủ ít nhất 3 trụ MOAT có evidence/counter-evidence đã xác minh")
    if len(evidence_roles) < 2:
        reasons.append("Evidence MOAT chưa đến từ ít nhất 2 loại tài liệu")

    moat_score = None
    multiplier = None
    if not missing_roles and len(qualifying_scores) >= 3 and len(evidence_roles) >= 2:
        moat_score = round(sum(qualifying_scores) / len(qualifying_scores), 2)
        multiplier = round(1.0 + (moat_score - 50.0) / 50.0 * 0.15, 3)
        status = "COMPLETE"
    elif qualifying_scores:
        status = "PARTIAL"
    else:
        status = "INSUFFICIENT"
    return {
        "ticker": ticker.upper().strip(),
        "assessment_status": status,
        "moat_score": moat_score,
        "multiplier": multiplier,
        "coverage_ratio": round(len(qualifying_scores) / len(MOAT_PILLARS), 4),
        "active_roles": active_roles,
        "missing_roles": missing_roles,
        "pillars": pillars,
        "reasons": reasons,
    }


async def assess_gil_by_ticker(
    session: AsyncSession,
    ticker: str,
    *,
    equity_override_vnd: float | None = None,
    equity_provenance: str | None = None,
) -> dict[str, Any]:
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
