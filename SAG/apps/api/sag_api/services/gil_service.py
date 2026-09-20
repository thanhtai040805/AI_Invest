"""GIL (Graph Intelligence Layer) - Thuật toán phân tích cấu trúc đồ thị sở hữu và dòng vốn.

Hoạt động 100% bằng toán học đồ thị thuần túy (Deterministic Graph Theory), KHÔNG TỐN TOKEN LLM:
1. Cycle Detection (Tarjan / DFS simple cycles): Phát hiện các chu trình khép kín luân chuyển dòng vốn hoặc sở hữu chéo (A -> B -> C -> A).
2. RPT Exposure Ratio: Tính toán tỷ lệ phơi nhiễm nợ vay, bảo lãnh, phải thu giữa các bên liên quan so với Vốn chủ sở hữu (Equity).
3. Quyết định cờ gil_flag: PASS / WARNING / CATASTROPHIC (Phục vụ Agent-02 lọc trong 5ms và Agent-05 phản biện).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

import networkx as nx

logger = logging.getLogger("sag.gil")


def _parse_vn_number(raw: str) -> float:
    value = raw.strip()
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(",") == 1:
        value = value.replace(",", ".")
    elif value.count(",") > 1:
        value = value.replace(",", "")
    elif value.count(".") > 1 or (value.count(".") == 1 and len(value.rsplit(".", 1)[1]) == 3):
        value = value.replace(".", "")
    return float(value)


@dataclass
class GILAnalysisResult:
    ticker: str
    gil_flag: str  # "PASS" | "WARNING" | "CATASTROPHIC" | "DATA_INSUFFICIENT"
    risk_level: str  # "LOW" | "HIGH" | "CRITICAL"
    rpt_ratio: float  # Tỷ lệ RPT Exposure / Equity
    total_rpt_exposure_vnd: float
    equity_vnd: float
    cycles_detected: int
    cycle_paths: list[list[str]]
    reasons: list[str]
    nodes_count: int
    edges_count: int
    summary: str
    rpt_metrics: dict[str, Any] = field(default_factory=dict)
    rpt_breakdown: dict[str, Any] = field(default_factory=dict)
    risk_components: dict[str, Any] = field(default_factory=dict)
    circular_flow_proven: bool = False
    tunneling_signals: int = 0
    catastrophic_triggered: bool = False
    analysis_status: str = "COMPLETE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "analysis_status": self.analysis_status,
            "gil_flag": self.gil_flag,
            "risk_level": self.risk_level,
            "rpt_ratio": round(self.rpt_ratio, 4),
            "total_rpt_exposure_vnd": self.total_rpt_exposure_vnd,
            "equity_vnd": self.equity_vnd,
            "cycles_detected": self.cycles_detected,
            "cycle_paths": self.cycle_paths,
            "reasons": self.reasons,
            "nodes_count": self.nodes_count,
            "edges_count": self.edges_count,
            "summary": self.summary,
            "rpt_metrics": self.rpt_metrics,
            "rpt_breakdown": self.rpt_breakdown,
            "risk_components": self.risk_components,
            "circular_flow_proven": self.circular_flow_proven,
            "tunneling_signals": self.tunneling_signals,
            "catastrophic_triggered": self.catastrophic_triggered,
        }


class GILGraphAnalyzer:
    """Bộ phân tích đồ thị cấu trúc sở hữu và rủi ro quan hệ bên liên quan."""

    FINANCIAL_FLOW_RELATIONS = {
        "LOANS_TO",
        "BORROWS_FROM",
        "RECEIVABLE_FROM",
        "PAYABLE_TO",
    }

    OWNERSHIP_RELATIONS = {
        "OWNS",
        "SUBSIDIARY_OF",
        "AFFILIATE_OF",
        "INVESTS_IN",
    }

    def __init__(self, ticker: str, equity_vnd: float = 0.0) -> None:
        self.ticker = ticker.upper().strip()
        self.equity_vnd = float(equity_vnd)
        self.graph = nx.DiGraph()

    def build_graph(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        """Nạp danh sách Nodes và Edges vào DiGraph."""
        for n in nodes:
            node_id = str(n.get("id") or n.get("name") or "").strip().upper()
            if not node_id:
                continue
            self.graph.add_node(
                node_id,
                name=n.get("name") or node_id,
                entity_type=n.get("entity_type") or "COMPANY",
            )

        for e in edges:
            source = str(e.get("source") or e.get("from") or "").strip().upper()
            target = str(e.get("target") or e.get("to") or "").strip().upper()
            rel_type = str(e.get("relation_type") or e.get("type") or "").strip().upper()
            amount = float(e.get("amount_vnd") or e.get("value") or 0.0)
            ownership_pct = float(e.get("ownership_pct") or e.get("pct") or 0.0)
            verified = bool(e.get("verified", True))

            if source and target:
                source_type = str(self.graph.nodes.get(source, {}).get("entity_type", "")).upper()
                target_type = str(self.graph.nodes.get(target, {}).get("entity_type", "")).upper()
                semantic_class = str(e.get("semantic_class") or "").upper()
                if not semantic_class:
                    if "SUBSIDIARY" in target_type or "SUBSIDIARY" in source_type:
                        semantic_class = "INTRA_GROUP"
                    elif rel_type in {"LOANS_TO", "BORROWS_FROM", "GUARANTEES_FOR", "RECEIVABLE_FROM", "PAYABLE_TO"}:
                        semantic_class = "LOAN_GUARANTEE"
                    elif rel_type == "TRANSACTS_WITH":
                        semantic_class = "NORMAL_OPERATING"
                    else:
                        semantic_class = "UNCLASSIFIED"
                self.graph.add_edge(
                    source,
                    target,
                    relation_type=rel_type,
                    amount_vnd=amount,
                    ownership_pct=ownership_pct,
                    verified=verified,
                    semantic_class=semantic_class,
                )

    def build_from_source_graph(
        self,
        entities: list[Any],
        events: list[Any],
        associations: list[Any],
        default_ticker: str | None = None,
    ) -> None:
        """Chiếu đồ thị lưỡng phân (Events - Mentions - Entities) thành đồ thị tài chính định hướng Entity-to-Entity."""
        import re

        ticker_symbol = (default_ticker or self.ticker).upper().strip()

        # 1. Thêm các Entity thành Node
        entity_by_id: dict[str, dict[str, str]] = {}
        for ent in entities:
            ent_id = getattr(ent, "id", None) or (ent.get("id") if isinstance(ent, dict) else None)
            ent_name = getattr(ent, "name", None) or (ent.get("name") if isinstance(ent, dict) else None) or ent_id
            ent_type = getattr(ent, "type", None) or (ent.get("type") if isinstance(ent, dict) else None) or "COMPANY"
            if not ent_id:
                continue
            norm_name = str(ent_name).strip().upper()
            entity_by_id[str(ent_id)] = {
                "id": str(ent_id),
                "name": str(ent_name),
                "type": str(ent_type),
                "norm_name": norm_name,
            }
            self.graph.add_node(norm_name, name=ent_name, entity_type=ent_type, id=ent_id)

        # Xác định Node chủ thể chính (Main Ticker / Company)
        subject_node = None
        for data in entity_by_id.values():
            if data["norm_name"] == ticker_symbol or ticker_symbol in data["norm_name"]:
                subject_node = data["norm_name"]
                break
        if not subject_node:
            subject_node = ticker_symbol
            self.graph.add_node(subject_node, name=ticker_symbol, entity_type="TICKER")

        # 2. Gom nhóm Associations theo Event ID
        event_assocs: dict[str, list[Any]] = {}
        for assoc in associations:
            ev_id = (
                getattr(assoc, "source_id", None)
                or getattr(assoc, "event_id", None)
                or (assoc.get("source_id") or assoc.get("event_id") if isinstance(assoc, dict) else None)
            )
            if ev_id:
                event_assocs.setdefault(str(ev_id), []).append(assoc)

        # 3. Phân tích ngữ cảnh từng Event để tạo quan hệ Entity -> Entity
        for ev in events:
            ev_id = str(getattr(ev, "id", None) or (ev.get("id") if isinstance(ev, dict) else "") or "")
            ev_title = getattr(ev, "title", None) or (ev.get("title") if isinstance(ev, dict) else "") or ""
            ev_category = str(getattr(ev, "category", None) or (ev.get("category") if isinstance(ev, dict) else "") or "").upper()
            assocs = event_assocs.get(ev_id, [])
            if not assocs:
                continue

            participating_entities: list[tuple[dict[str, str], str]] = []
            for a in assocs:
                target_id = str(
                    getattr(a, "target_id", None)
                    or getattr(a, "entity_id", None)
                    or (a.get("target_id") or a.get("entity_id") if isinstance(a, dict) else None)
                    or ""
                )
                desc = str(
                    getattr(a, "description", None)
                    or (a.get("description") if isinstance(a, dict) else "")
                    or ""
                )
                if target_id in entity_by_id:
                    participating_entities.append((entity_by_id[target_id], desc))

            event_subject = subject_node
            for p_ent, _ in participating_entities:
                if p_ent["type"] in ("TICKER", "COMPANY") and (p_ent["norm_name"] == ticker_symbol or ticker_symbol in p_ent["norm_name"]):
                    event_subject = p_ent["norm_name"]
                    break

            for p_ent, desc in participating_entities:
                if p_ent["norm_name"] == event_subject:
                    continue

                p_type = p_ent["type"].upper()
                desc_lower = desc.casefold()

                # A. Quan hệ sở hữu (OWNS / SUBSIDIARY_OF)
                pct_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", desc)
                pct_val = float(pct_match.group(1).replace(",", ".")) if pct_match else 0.0

                if (
                    p_type == "SUBSIDIARY_AFFILIATE"
                    or "công ty con" in desc_lower
                    or "công ty liên kết" in desc_lower
                    or ev_category in ("OWNERSHIP_CHANGE", "SUBSIDIARY_PROFIT_RECOGNITION")
                ):
                    self.graph.add_edge(
                        event_subject,
                        p_ent["norm_name"],
                        relation_type="OWNS",
                        ownership_pct=pct_val,
                        amount_vnd=0.0,
                        semantic_class="INTRA_GROUP" if p_type == "SUBSIDIARY_AFFILIATE" else "UNCLASSIFIED",
                        description=desc or f"Sở hữu {pct_val}%",
                    )
                    continue

                # B. Quan hệ tài chính: Vay nợ, bảo lãnh, phải thu/phải trả
                amt_match = re.search(r"(\d+(?:[.,]\d+)*)\s*(tỷ|triệu|nghìn|đồng|vnd)", desc, re.IGNORECASE)
                amount_vnd = 0.0
                if amt_match:
                    unit = amt_match.group(2).lower()
                    try:
                        num = _parse_vn_number(amt_match.group(1))
                        if "tỷ" in unit:
                            amount_vnd = num * 1_000_000_000
                        elif "triệu" in unit:
                            amount_vnd = num * 1_000_000
                        elif "nghìn" in unit:
                            amount_vnd = num * 1_000
                        else:
                            amount_vnd = num
                    except ValueError:
                        pass
                elif re.search(r"\d{9,}", desc):
                    raw_digits = re.search(r"\d{9,}", desc.replace(".", "").replace(",", ""))
                    if raw_digits:
                        amount_vnd = float(raw_digits.group(0))

                if "cho vay" in desc_lower or "phải thu" in desc_lower:
                    rel = "LOANS_TO" if "cho vay" in desc_lower else "RECEIVABLE_FROM"
                    self.graph.add_edge(
                        event_subject,
                        p_ent["norm_name"],
                        relation_type=rel,
                        amount_vnd=amount_vnd,
                        ownership_pct=0.0,
                        semantic_class="LOAN_GUARANTEE",
                        description=desc,
                    )
                elif "vay" in desc_lower or "ngân hàng" in desc_lower or (p_type == "COMPANY" and ev_category == "DEBT_RESTRUCTURING"):
                    self.graph.add_edge(
                        event_subject,
                        p_ent["norm_name"],
                        relation_type="BORROWS_FROM",
                        amount_vnd=amount_vnd,
                        ownership_pct=0.0,
                        semantic_class="LOAN_GUARANTEE",
                        description=desc,
                    )
                elif "bên liên quan" in desc_lower or p_type == "RELATED_PARTY" or ev_category == "RELATED_PARTY_TRANSACTION":
                    rel = "TRANSACTS_WITH"
                    if "bảo lãnh" in desc_lower:
                        rel = "GUARANTEES_FOR"
                    elif "phải trả" in desc_lower:
                        rel = "PAYABLE_TO"
                    elif "phải thu" in desc_lower:
                        rel = "RECEIVABLE_FROM"
                    self.graph.add_edge(
                        event_subject,
                        p_ent["norm_name"],
                        relation_type=rel,
                        amount_vnd=amount_vnd,
                        ownership_pct=0.0,
                        semantic_class="NORMAL_OPERATING" if p_type != "RELATED_PARTY" else "INSIDER_RELATED",
                        description=desc,
                    )

    def detect_capital_tunneling_cycles(self) -> list[list[str]]:
        """Phát hiện các chu trình khép kín (A -> B -> C -> A) luân chuyển dòng vốn hoặc sở hữu."""
        # Chỉ xét đồ thị con chứa các quan hệ dòng tiền hoặc sở hữu chéo
        sub_edges = [
            (u, v, d)
            for u, v, d in self.graph.edges(data=True)
            if d.get("verified", True)
            and (
                d.get("relation_type") in self.FINANCIAL_FLOW_RELATIONS
                or d.get("relation_type") in self.OWNERSHIP_RELATIONS
            )
        ]

        sub_graph = nx.DiGraph()
        for u, v, d in sub_edges:
            sub_graph.add_edge(u, v, **d)

        try:
            raw_cycles = list(nx.simple_cycles(sub_graph))
        except Exception as err:
            logger.warning(f"Error computing cycles: {err}")
            return []

        meaningful_cycles: list[list[str]] = []
        for cycle in raw_cycles:
            # Bỏ qua chu trình tự thân (length 1)
            if len(cycle) < 2:
                continue
            cycle_edges = [sub_graph.get_edge_data(cycle[i], cycle[(i + 1) % len(cycle)]) or {} for i in range(len(cycle))]
            flow_edges = [edge for edge in cycle_edges if edge.get("relation_type") in self.FINANCIAL_FLOW_RELATIONS]
            # Ownership-only cycles are structural review signals, not proven round-tripping.
            if len(flow_edges) >= 2 and any(float(edge.get("amount_vnd", 0.0) or 0.0) > 0 for edge in flow_edges):
                meaningful_cycles.append([*cycle, cycle[0]])

        return meaningful_cycles

    def calculate_rpt_exposure(self) -> tuple[float, list[str]]:
        """Tính tổng số tiền phơi nhiễm qua các giao dịch bên liên quan (RPT)."""
        total_exposure = 0.0
        exposure_details: list[str] = []

        for u, v, d in self.graph.edges(data=True):
            rel_type = d.get("relation_type", "")
            amount = float(d.get("amount_vnd", 0.0))

            if not d.get("verified", True):
                continue
            if rel_type in {"LOANS_TO", "GUARANTEES_FOR", "RECEIVABLE_FROM"} and amount > 0:
                total_exposure += amount
                exposure_details.append(f"{u} -> {rel_type} -> {v}: {amount:,.0f} VND")

        return total_exposure, exposure_details

    def _composition(self, total_exposure: float, rpt_ratio: float) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        buckets = {"INTRA_GROUP": 0.0, "NORMAL_OPERATING": 0.0, "INSIDER_RELATED": 0.0, "LOAN_GUARANTEE": 0.0, "ASSET_TRANSFER": 0.0, "SUSPICIOUS": 0.0, "UNCLASSIFIED": 0.0}
        for _, _, edge in self.graph.edges(data=True):
            if edge.get("relation_type") not in {"LOANS_TO", "GUARANTEES_FOR", "RECEIVABLE_FROM", "PAYABLE_TO", "TRANSACTS_WITH"}:
                continue
            if not edge.get("verified", True):
                continue
            amount = float(edge.get("amount_vnd", 0.0) or 0.0)
            semantic = str(edge.get("semantic_class") or "UNCLASSIFIED").upper()
            if semantic not in buckets:
                semantic = "UNCLASSIFIED"
            buckets[semantic] += max(amount, 0.0)
        denominator = total_exposure or 1.0
        breakdown = {key.lower(): {"amount_vnd": value, "share": round(value / denominator, 4)} for key, value in buckets.items()}
        exposure_score = min(100.0, max(0.0, rpt_ratio * 100.0))
        semantic_score = min(100.0, (buckets["INSIDER_RELATED"] + buckets["SUSPICIOUS"]) / denominator * 100.0 * 1.5)
        structural_score = 0.0
        metrics = {"exposure_vnd": total_exposure, "equity_ratio": round(rpt_ratio, 4), "asset_ratio": None, "revenue_ratio": None, "receivables_ratio": None, "loans_to_cash_equity_ratio": None}
        components = {"exposure_score": round(exposure_score, 2), "semantic_risk_score": round(min(100.0, semantic_score), 2), "structural_risk_score": structural_score}
        return metrics, breakdown, components

    def evaluate(self) -> GILAnalysisResult:
        """Thực thi toàn bộ kiểm định toán học và gán nhãn gil_flag."""
        cycles = self.detect_capital_tunneling_cycles()
        total_rpt, details = self.calculate_rpt_exposure()

        reasons: list[str] = []
        if self.equity_vnd <= 0:
            reasons.append("Thiếu vốn chủ sở hữu có provenance; GIL không được phép PASS khi mẫu số chưa xác minh.")
            return GILAnalysisResult(
                ticker=self.ticker,
                gil_flag="DATA_INSUFFICIENT",
                analysis_status="DATA_INSUFFICIENT",
                risk_level="UNKNOWN",
                rpt_ratio=0.0,
                total_rpt_exposure_vnd=total_rpt,
                equity_vnd=self.equity_vnd,
                cycles_detected=len(cycles),
                cycle_paths=cycles,
                reasons=reasons,
                nodes_count=self.graph.number_of_nodes(),
                edges_count=self.graph.number_of_edges(),
                summary="GIL DATA_INSUFFICIENT: thiếu vốn chủ sở hữu đã xác minh",
            )

        rpt_ratio = total_rpt / self.equity_vnd
        rpt_metrics, rpt_breakdown, risk_components = self._composition(total_rpt, rpt_ratio)

        # Ma trận phán quyết GIL: exposure cao không đồng nghĩa circular flow.
        # CATASTROPHIC chỉ được kích hoạt bởi chu trình dòng vốn đã xác minh.
        if len(cycles) > 0:
            gil_flag = "CATASTROPHIC"
            risk_level = "CRITICAL"
            risk_components["structural_risk_score"] = 100.0
            reasons.append(
                f"Phát hiện {len(cycles)} chu trình khép kín sở hữu/dòng vốn nghi vấn rút ruột: {cycles[:3]}"
            )
        elif rpt_ratio > 0.25:
            gil_flag = "WARNING"
            risk_level = "HIGH"
            reasons.append(
                f"RPT exposure cao nhưng chưa chứng minh circular flow ({rpt_ratio * 100:.1f}% VCSH); yêu cầu review."
            )
        else:
            gil_flag = "PASS"
            risk_level = "LOW"
            reasons.append(
                f"Đồ thị sở hữu và dòng tiền minh bạch. RPT Ratio = {rpt_ratio * 100:.1f}%, không phát hiện chu trình khép kín."
            )

        summary = (
            f"GIL Flag: {gil_flag} | Risk: {risk_level} | Nodes: {self.graph.number_of_nodes()} | "
            f"Edges: {self.graph.number_of_edges()} | Cycles: {len(cycles)} | RPT Ratio: {rpt_ratio * 100:.1f}%"
        )

        return GILAnalysisResult(
            ticker=self.ticker,
            gil_flag=gil_flag,
            risk_level=risk_level,
            rpt_ratio=rpt_ratio,
            total_rpt_exposure_vnd=total_rpt,
            equity_vnd=self.equity_vnd,
            cycles_detected=len(cycles),
            cycle_paths=cycles,
            reasons=reasons,
            nodes_count=self.graph.number_of_nodes(),
            edges_count=self.graph.number_of_edges(),
            summary=summary,
            rpt_metrics=rpt_metrics,
            rpt_breakdown=rpt_breakdown,
            risk_components=risk_components,
            circular_flow_proven=bool(cycles),
            tunneling_signals=len(cycles),
            catastrophic_triggered=bool(cycles),
        )

import hashlib
import json
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import AssessmentRun, Document, DocumentFacet, Entity, Fact, Issuer, Observation, Relation
from sag_api.enums import FactType, ProcessingStageStatus, RelationType

GIL_POLICY_VERSION = "gil-policy-v43-rc1-shared-evidence-universe-v3"
def _relation_flow_kind(rel: Relation) -> str | None:
    value = (rel.metadata_json or {}).get("flow_kind") if rel.metadata_json else None
    return str(value).strip().lower() if value else None


def _insider_metrics(observations: list[Observation], equity: float | None) -> dict[str, Any]:
    """Quantify insider evidence without treating ownership as a transaction."""
    import unicodedata

    insider_markers = (
        "chu tich", "can bo quan ly noi bo", "nguoi lien quan", "gia dinh",
        "vo", "chong", "insider", "related person", "director", "executive",
    )
    transaction_markers = (
        "giao dich", "cho vay", "di vay", "vay", "gop von", "chuyen nhuong",
        "mua", "ban", "tra no", "co tuc", "thanh toan", "khoan phai thu",
    )
    ownership_pct = 0.0
    transaction_exposure = 0.0
    ownership_evidence = 0
    transaction_evidence = 0
    amount_re = re.compile(r"(\d[\d.,]*)\s*(nghìn tỷ|nghin ty|tỷ|ty|triệu|trieu|đồng|dong|vnd)", re.IGNORECASE)
    pct_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

    for observation in observations:
        text = unicodedata.normalize("NFD", observation.statement).casefold()
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = re.sub(r"\s+", " ", text)
        def has_marker(marker: str) -> bool:
            return bool(re.search(rf"\b{re.escape(marker)}\b", text)) if " " not in marker else marker in text

        if not any(has_marker(marker) for marker in insider_markers):
            continue
        ownership_context = any(
            token in text for token in ("so huu", "co phan", "ty le so huu", " von")
        )
        percentage_matches = list(pct_re.finditer(observation.statement)) if ownership_context else []
        insider_positions = [
            text.find(marker) for marker in insider_markers
            if marker in text
        ]
        if percentage_matches and insider_positions:
            anchor = min(insider_positions)
            nearest = min(percentage_matches, key=lambda match: abs(match.start() - anchor))
            percentages = [float(nearest.group(1).replace(",", "."))]
        else:
            percentages = []
        if percentages:
            ownership_pct = max(ownership_pct, max(percentages))
            ownership_evidence += 1
        if not any(has_marker(marker) for marker in transaction_markers):
            continue
        for raw_value, unit in amount_re.findall(observation.statement):
            value = _parse_vn_number(raw_value)
            unit = unit.casefold()
            multiplier = 1_000_000_000_000 if "nghìn tỷ" in unit or "nghin ty" in unit else 1_000_000_000 if "tỷ" in unit or "ty" in unit else 1_000_000 if "triệu" in unit or "trieu" in unit else 1.0
            transaction_exposure += value * multiplier
            transaction_evidence += 1

    return {
        "ownership_pct": round(ownership_pct, 4) if ownership_pct else None,
        "ownership_exposure_vnd": round(equity * ownership_pct / 100.0, 2) if equity and ownership_pct else None,
        "transaction_exposure_vnd": round(transaction_exposure, 2) if transaction_evidence else None,
        "ownership_evidence_count": ownership_evidence,
        "transaction_evidence_count": transaction_evidence,
        "basis": "EXPLICIT_TRANSACTION" if transaction_exposure else ("OWNERSHIP_PERCENTAGE" if ownership_pct else "NARRATIVE_ONLY"),
        "transaction_status": "QUANTIFIED" if transaction_exposure else ("NOT_DISCLOSED" if ownership_pct else "NOT_APPLICABLE"),
    }


def _deduplicate_gil_relations(relations: list[Relation]) -> list[Relation]:
    """Remove repeated projections of the same economic edge.

    Ownership is a structural fact and may be repeated in Annual and Quarter
    documents, so its period is intentionally excluded.  Flow/balance edges
    retain period and amount: a new quarter or a different transaction must
    not disappear merely because the endpoints are the same.
    """
    seen: dict[tuple[Any, ...], Relation] = {}
    structural = {RelationType.OWNS.value, RelationType.CONTROLS.value}
    for rel in relations:
        subject = rel.subject_entity_id or _entity_name_key(rel.subject)
        object_ = rel.object_entity_id or _entity_name_key(rel.object)
        kind = _relation_flow_kind(rel)
        key: tuple[Any, ...] = (
            subject,
            object_,
            str(rel.relation_type or "").lower(),
            kind,
            None if rel.relation_type in structural else rel.document_id,
            rel.ownership_pct if rel.relation_type in structural else None,
            None if rel.relation_type in structural else rel.amount_vnd,
            None if rel.relation_type in structural else str(rel.period_end or rel.as_of or ""),
        )
        current = seen.get(key)
        if current is None:
            seen[key] = rel
            continue
        # Prefer the record with a resolvable evidence span and then the
        # record with a real amount/ownership value.  The DB row itself is
        # never deleted; this is assessment-time graph projection only.
        current_score = bool(current.evidence_span_id) + bool(current.amount_vnd is not None or current.ownership_pct is not None)
        candidate_score = bool(rel.evidence_span_id) + bool(rel.amount_vnd is not None or rel.ownership_pct is not None)
        if candidate_score > current_score:
            seen[key] = rel
    return list(seen.values())


def _gil_evidence(relations: list[Relation], docs: list[Document]) -> list[dict[str, Any]]:
    """Return compact, reusable provenance for the graph decision."""
    roles = {doc.id: doc.doc_role for doc in docs}
    periods = {doc.id: str(doc.period_end) if doc.period_end else None for doc in docs}
    output: list[dict[str, Any]] = []
    for rel in relations:
        if rel.relation_type not in {
            RelationType.OWNS.value, RelationType.CONTROLS.value,
            RelationType.INVESTS_IN.value, RelationType.LENDS_TO.value,
            RelationType.CREDITOR_OF.value, RelationType.GUARANTEES_FOR.value,
            RelationType.TRANSACTS_WITH.value,
        }:
            continue
        item: dict[str, Any] = {
            "type": "relation",
            "relation_id": rel.id,
            "document_id": rel.document_id,
            "doc_role": roles.get(rel.document_id),
            "period_end": periods.get(rel.document_id),
            "subject": rel.subject,
            "object": rel.object,
            "relation_type": rel.relation_type,
            "node_id": rel.node_id,
            "evidence_span_id": rel.evidence_span_id,
        }
        flow_kind = _relation_flow_kind(rel)
        if flow_kind:
            item["flow_kind"] = flow_kind
        if rel.amount_vnd is not None:
            item["amount_vnd"] = rel.amount_vnd
        if rel.ownership_pct is not None:
            item["ownership_pct"] = rel.ownership_pct
        output.append(item)
    return output


async def assess_gil(session: AsyncSession, ticker: str) -> dict[str, Any]:
    from sag_api.services.financial_v2_service import active_documents
    issuer, docs = await active_documents(session, ticker)
    doc_ids = [doc.id for doc in docs if doc.extraction_status == ProcessingStageStatus.COMPLETE.value]
    historical_docs = list(
        (
            await session.execute(
                select(Document).where(
                    Document.issuer_id == issuer.id,
                    Document.extraction_status == ProcessingStageStatus.COMPLETE.value,
                )
            )
        ).scalars().all()
    )
    historical_doc_ids = [doc.id for doc in historical_docs]
    relations = []
    entities = []
    facts = []
    facets = []
    observations = []
    historical_relations = []
    historical_facts = []
    if doc_ids:
        relations = (
            await session.execute(
                select(Relation).where(
                    Relation.issuer_id == issuer.id,
                    Relation.document_id.in_(doc_ids),
                    Relation.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
        relations = _deduplicate_gil_relations(relations)
        entities = (
            await session.execute(
                select(Entity).where(Entity.issuer_id == issuer.id)
            )
        ).scalars().all()
        facts = (
            await session.execute(
                select(Fact).where(
                    Fact.issuer_id == issuer.id,
                    Fact.document_id.in_(doc_ids),
                    Fact.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
        facets = (
            await session.execute(
                select(DocumentFacet).where(
                    DocumentFacet.issuer_id == issuer.id,
                    DocumentFacet.document_id.in_(doc_ids),
                )
            )
        ).scalars().all()
        observations = (
            await session.execute(
                select(Observation).where(
                    Observation.issuer_id == issuer.id,
                    Observation.document_id.in_(doc_ids),
                )
            )
        ).scalars().all()
    if historical_doc_ids:
        historical_relations = (
            await session.execute(
                select(Relation).where(
                    Relation.issuer_id == issuer.id,
                    Relation.document_id.in_(historical_doc_ids),
                    Relation.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
        historical_facts = (
            await session.execute(
                select(Fact).where(
                    Fact.issuer_id == issuer.id,
                    Fact.document_id.in_(historical_doc_ids),
                    Fact.validation_status == "VALIDATED",
                )
            )
        ).scalars().all()
        historical_relations = _deduplicate_gil_relations(historical_relations)
    evidence_digest = hashlib.sha256(
        json.dumps(
            {
                "issuer": [issuer.id, issuer.ticker, issuer.legal_name, issuer.metadata_json],
                "documents": [[doc.id, doc.content_sha256, doc.extraction_status] for doc in docs],
                "historical_documents": [[doc.id, doc.content_sha256] for doc in historical_docs],
                "entities": [[row.id, row.canonical_name, row.display_name, row.entity_type, row.raw_label, row.metadata_json] for row in entities],
                "relations": [[row.id, row.relation_type, row.subject, row.object, row.amount_vnd, row.ownership_pct, row.validation_status, row.metadata_json, _relation_flow_kind(row)] for row in relations],
                "historical_relations": [[row.id, row.relation_type, row.subject, row.object, row.amount_vnd, row.period_end] for row in historical_relations],
                "facts": [[row.id, row.semantic_key, row.value_text, row.value_numeric, row.period_end] for row in facts],
                "historical_facts": [[row.id, row.semantic_key, row.value_numeric, row.period_end] for row in historical_facts],
                "facets": [[row.id, row.facet, row.confidence] for row in facets],
                "observations": [[row.id, row.statement, row.predicate, row.document_id] for row in observations],
                "policy_version": GIL_POLICY_VERSION,
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cached = await session.scalar(
        select(AssessmentRun)
        .where(AssessmentRun.issuer_id == issuer.id, AssessmentRun.assessment_kind == "gil")
        .order_by(AssessmentRun.updated_at.desc())
    )
    if cached is not None and cached.evidence_digest == evidence_digest and cached.result_json:
        return cached.result_json
    equity = _latest_equity(facts)
    latest_quarter_docs = [doc for doc in docs if doc.doc_role == "LATEST_QUARTER"]
    latest_quarter_facts = [
        fact for fact in facts
        if any(doc.id == fact.document_id for doc in latest_quarter_docs)
    ]
    latest_quarter_equity = _latest_equity(latest_quarter_facts)
    document_denominators = _document_denominator_coverage(facts, latest_quarter_facts)
    reasons = []
    facet_names = {facet.facet for facet in facets}
    structural_relations = {
        RelationType.OWNS.value,
        RelationType.CONTROLS.value,
        RelationType.INVESTS_IN.value,
    }
    transaction_relations = {
        RelationType.TRANSACTS_WITH.value,
        RelationType.GUARANTEES_FOR.value,
        RelationType.LENDS_TO.value,
        RelationType.CREDITOR_OF.value,
    }
    has_structure = any(rel.relation_type in structural_relations for rel in relations) or bool(
        facet_names.intersection({"corporate_structure", "ownership_structure", "investment_in_subsidiaries", "related_party"})
    )
    has_transaction_scope = any(rel.relation_type in transaction_relations for rel in relations) or bool(
        facet_names.intersection({"related_party", "guarantees", "intercompany_financing"})
    )
    if not equity:
        reasons.append("Thiáº¿u vá»‘n chá»§ sá»Ÿ há»¯u validated")
    if latest_quarter_docs and not latest_quarter_equity:
        reasons.append("LATEST_QUARTER thiếu equity validated từ tài liệu; không dùng equity cũ hoặc market DB thay thế")
    if not has_structure:
        reasons.append("Thiáº¿u evidence vá» cáº¥u trĂºc sá»Ÿ há»¯u/Ä‘áº§u tÆ° Ä‘á»ƒ dá»±ng graph GIL")
    if not has_transaction_scope:
        reasons.append("Thiáº¿u evidence vá» giao dá»‹ch liĂªn quan, báº£o lĂ£nh hoáº·c tĂ i trá»£ ná»™i bá»™")
    if any(doc.extraction_status != ProcessingStageStatus.COMPLETE.value for doc in docs):
        reasons.append("Extraction cá»§a bá»™ tĂ i liá»‡u active chÆ°a COMPLETE")
    if reasons:
        result = _gil_insufficient(issuer.ticker, reasons, equity)
        if cached is None:
            cached = AssessmentRun(issuer_id=issuer.id, assessment_kind="gil")
            session.add(cached)
        cached.status = result["analysis_status"]
        cached.policy_version = GIL_POLICY_VERSION
        cached.document_ids_json = doc_ids
        cached.result_json = result
        cached.evidence_digest = evidence_digest
        await session.flush()
        return result
    capital_edges = [
        rel for rel in relations if rel.relation_type in {RelationType.OWNS.value, RelationType.INVESTS_IN.value, RelationType.LENDS_TO.value, RelationType.CREDITOR_OF.value}
    ]
    cycles = _detect_cycles(capital_edges)
    owned_entities = _owned_entities(relations, issuer.ticker)
    owned_entity_ids = _owned_entity_ids(relations)
    policy_context = _gil_policy_context_v2(
        ticker=issuer.ticker,
        legal_name=issuer.legal_name,
        metadata=issuer.metadata_json or {},
        relations=relations,
        owned_entities=owned_entities,
    )
    entity_types = {entity.id: str(entity.entity_type or "").lower() for entity in entities}
    issuer_entity_id = next(
        (
            entity.id for entity in entities
            if str(entity.entity_type or "").casefold() == "issuer"
        ),
        None,
    ) or next(
        (
            entity.id for entity in entities
            if _entity_name_key(entity.canonical_name or entity.display_name)
            == _entity_name_key(issuer.legal_name)
            or _entity_name_key(issuer.ticker)
            == _entity_name_key(entity.canonical_name or entity.display_name)
        ),
        None,
    )
    exposure_relations = [
        rel for rel in _issuer_outgoing_relations(relations, issuer_entity_id)
        if rel.relation_type in {
            RelationType.TRANSACTS_WITH.value,
            RelationType.GUARANTEES_FOR.value,
            RelationType.LENDS_TO.value,
            RelationType.CREDITOR_OF.value,
            RelationType.INVESTS_IN.value,
        }
    ]
    exposure_buckets = _rpt_exposure_buckets(relations, issuer_entity_id=issuer_entity_id)
    balance_exposures = {
        "loan_exposure_vnd": exposure_buckets["loan_exposure_vnd"],
        "receivable_payable_exposure_vnd": exposure_buckets["receivable_payable_exposure_vnd"],
        "guarantee_exposure_vnd": exposure_buckets["guarantee_exposure_vnd"],
    }
    # Never aggregate flows, balances and investments into one denominator.
    # The legacy rpt_ratio is retained as the maximum material balance ratio
    # for compatibility; the canonical output is rpt_metrics below.
    facts_by_doc = {}
    relations_by_doc = {}
    for fact in facts:
        facts_by_doc.setdefault(fact.document_id, []).append(fact)
    for rel in relations:
        relations_by_doc.setdefault(rel.document_id, []).append(rel)
    period_metrics = {}
    for doc in docs:
        doc_facts = facts_by_doc.get(doc.id, [])
        doc_relations = relations_by_doc.get(doc.id, [])
        doc_buckets = _rpt_exposure_buckets(doc_relations, issuer_entity_id=issuer_entity_id)
        doc_flow_buckets = _denominator_exposure_buckets(doc_relations, issuer_entity_id=issuer_entity_id)
        doc_equity = _latest_equity(doc_facts)
        doc_coverage = _document_denominator_coverage(doc_facts, doc_facts)
        doc_ratios = _document_denominator_ratios(
            _denominator_exposure_buckets(doc_relations, issuer_entity_id=issuer_entity_id), doc_coverage, doc_equity
        )
        doc_balance = {
            "current_balance_loan_vnd": doc_flow_buckets["loan_balance_vnd"] or doc_flow_buckets["loan_vnd"],
            "current_balance_receivable_payable_vnd": doc_flow_buckets["receivable_vnd"],
            "current_balance_guarantee_vnd": doc_flow_buckets["guarantee_exposure_vnd"],
        }
        doc_ratio_candidates = {
            key: value / doc_equity for key, value in doc_balance.items() if doc_equity and value > 0
        }
        period_metrics[doc.id] = {
            "doc_role": doc.doc_role,
            "period_end": str(doc.period_end or doc.fiscal_year or ""),
            "ratio_candidates": doc_ratio_candidates,
            "ratio": max(doc_ratio_candidates.values(), default=None),
            "headline_metric": max(doc_ratio_candidates, key=doc_ratio_candidates.get) if doc_ratio_candidates else None,
            "denominator_ratios": doc_ratios,
        }
    comparable_docs = latest_quarter_docs or [doc for doc in docs if doc.doc_role == "ANNUAL_BACKBONE"]
    latest_doc = max(comparable_docs, key=lambda doc: str(doc.period_end or doc.fiscal_year or ""), default=None)
    latest_metric = period_metrics.get(latest_doc.id, {}) if latest_doc else {}
    ratio_candidates = latest_metric.get("ratio_candidates", {})
    raw_ratio = latest_metric.get("ratio")
    raw_headline_metric = latest_metric.get("headline_metric")
    raw_denominator_ratios = latest_metric.get("denominator_ratios", {})
    scope_verified = document_denominators.get("accounting_scope") in {"STANDALONE", "CONSOLIDATED"}
    missing_denominators = document_denominators.get("missing_latest_quarter", [])
    ratio = raw_ratio
    headline_metric = raw_headline_metric
    denominator_ratios = raw_denominator_ratios
    ratio_invariant_violations = _ratio_invariant_violations(denominator_ratios)
    guarantee_state = _guarantee_disclosure_state(
        relations,
        latest_quarter_facts,
        issuer_entity_id=issuer_entity_id,
    )
    denominator_ratios["guarantee_to_equity"] = (
        0.0 if guarantee_state["status"] == "CONFIRMED_NONE" else None
    )
    ratio_candidates["current_balance_guarantee_vnd"] = (
        0.0 if guarantee_state["status"] == "CONFIRMED_NONE" else None
    )
    latest_flow_buckets = _denominator_exposure_buckets(
        relations_by_doc.get(latest_doc.id, []) if latest_doc else [],
        issuer_entity_id=issuer_entity_id,
    )
    total_receivables = document_denominators.get("metrics", {}).get("total_receivables")
    receivable_concentration = {
        "related_party_receivables_vnd": latest_flow_buckets.get("receivable_vnd", 0.0),
        "total_receivables_vnd": total_receivables,
        "ratio": denominator_ratios.get("related_party_receivables_to_total_receivables"),
        "status": (
            "HIGH_CONCENTRATION"
            if (denominator_ratios.get("related_party_receivables_to_total_receivables") or 0.0) >= 0.75
            else "NORMAL"
        ),
    }
    exposure = max(balance_exposures.values(), default=0.0)
    breadth_score = sum(1 for value in ratio_candidates.values() if value is not None and value > 0.10)
    breakdown_amounts = _relationship_breakdown(
        exposure_relations, owned_entities, owned_entity_ids, entity_types
    )
    insider_markers = {
        "hÄ‘qt", "há»™i Ä‘á»“ng quáº£n trá»‹", "chá»§ tá»‹ch", "ban kiá»ƒm soĂ¡t", "ban Ä‘iá»u hĂ nh",
        "ngÆ°á»i liĂªn quan", "gia Ä‘Ă¬nh", "vá»£", "chá»“ng", "board", "chairman", "director",
        "executive", "family", "related person", "insider",
    }
    insider_markers.update({
        "hoidongquantri", "chutich", "bankiemsoat", "bandieuhanh",
        "nguoilienquan", "giadinh", "vo", "chong",
    })
    insider_observations = [
        row for row in observations
        if any(marker in _normalize_metric_key(row.statement) for marker in insider_markers)
    ]
    insider_metrics = _insider_metrics(observations, equity)
    doc_roles = {doc.id: str(doc.doc_role or "") for doc in docs}
    person_entity_ids = {
        entity.id for entity in entities
        if str(entity.entity_type or "").casefold() in {
            "person", "director", "executive", "family_member",
            "major_shareholder", "insider", "insider_controlled_entity",
        }
    }
    governance_person_evidence = any(
        doc_roles.get(rel.document_id) == "GOVERNANCE_REPORT"
        and (rel.subject_entity_id in person_entity_ids or rel.object_entity_id in person_entity_ids)
        for rel in relations
    )
    insider_evidence_present = bool(insider_observations or governance_person_evidence)
    relationship_total = sum(breakdown_amounts.values())
    external_share = breakdown_amounts["external_affiliate"] / relationship_total if relationship_total else 0.0
    unresolved_share = breakdown_amounts["unclassified"] / relationship_total if relationship_total else 0.0
    classified_share = 1.0 - unresolved_share if relationship_total else 0.0
    external_material = external_share >= 0.10 and relationship_total > 0
    activity_ratio = denominator_ratios.get("related_party_service_revenue_to_revenue")
    activity_material = bool(
        activity_ratio and activity_ratio >= 0.40
        and policy_context["company_archetype"] != "HOLDING"
    )
    capital_material = bool(
        denominator_ratios.get("capital_allocation_to_total_assets")
        and denominator_ratios["capital_allocation_to_total_assets"] >= 0.40
    )
    breakdown = {
        key: {"amount_vnd": value, "share": round(value / relationship_total, 4) if relationship_total else 0.0}
        for key, value in breakdown_amounts.items()
    }
    flow_risk = _flow_risk_breakdown(exposure_relations, guarantee_state=guarantee_state)
    ownership_edges = [
        rel for rel in relations
        if rel.relation_type in {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
    ]
    ownership_cycles = _detect_ownership_loops(ownership_edges)
    multi_hop_flows = _multi_hop_flow_count(relations, owned_entities)
    relationship_score = _relationship_risk_score(breakdown_amounts, insider_evidence_present)
    flow_score = flow_risk["score"]
    suspicious_multi_hop_flows = _suspicious_multi_hop_flow_count(relations, owned_entities, entity_types)
    external_outflow = _external_capital_outflow_evidence(
        exposure_relations, breakdown_amounts, entity_types,
        suspicious_multi_hop_flows=suspicious_multi_hop_flows,
        cycles=len(cycles),
    )
    potential_leakage = external_outflow["status"] in {"POTENTIAL_LEAKAGE", "TUNNELING_EVIDENCE"}
    structural_score = _structural_risk_score(
        cycles=len(cycles), ownership_loops=len(ownership_cycles),
        multi_hop=suspicious_multi_hop_flows, external_leakage=potential_leakage,
    )
    materiality = _materiality_components(
        ratio, denominator_ratios.get("related_party_service_revenue_to_revenue"),
        denominator_ratios.get("capital_allocation_to_total_assets"),
        denominator_ratios.get("related_party_receivables_to_total_receivables"),
        denominator_ratios.get("payable_to_total_payables"),
        denominator_ratios.get("loan_to_cash_plus_equity"),
        denominator_ratios.get("guarantee_to_equity"),
        denominator_ratios.get("dividend_to_financial_income"),
        denominator_ratios.get("purchase_to_cost_of_goods_sold"),
        external_share,
        policy_context["company_archetype"],
    )
    materiality_score = materiality["score"]
    temporal = _temporal_risk(historical_relations, historical_facts, historical_docs, issuer_entity_id=issuer_entity_id)
    temporal_score = temporal.get("score")
    score_terms = [
        (0.25, relationship_score),
        (0.20, flow_score),
        (0.25, structural_score),
        (0.30, materiality_score),
    ]
    if temporal_score is not None:
        score_terms.append((0.15, temporal_score))
        score_terms = [(weight * (0.85 if index < 4 else 1.0), value) for index, (weight, value) in enumerate(score_terms)]
    available_weight = sum(weight for weight, value in score_terms if value is not None)
    gil_score = round(
        sum(weight * value for weight, value in score_terms if value is not None) / available_weight
        if available_weight else 0.0,
        2,
    )
    cycle_materiality_ratio = _cycle_materiality_ratio(cycles, relations, equity)
    cycle_materiality = bool(cycles and cycle_materiality_ratio >= 0.15)
    graph_layers = {
        "relationship_risk": {
            "subsidiary": round(breakdown_amounts["intra_group"] / relationship_total, 4) if relationship_total else 0.0,
            "associate": 0.0,
            "insider_related": round(breakdown_amounts["insider_related"] / relationship_total, 4) if relationship_total else 0.0,
            "external_affiliate": round(external_share, 4),
            "unclassified": round(unresolved_share, 4),
            "unknown": round(unresolved_share, 4),
            "insider_evidence_present": insider_evidence_present,
            "insider_exposure_status": "QUANTIFIED_TRANSACTION" if insider_metrics["basis"] == "EXPLICIT_TRANSACTION" else ("QUANTIFIED_OWNERSHIP" if insider_metrics["basis"] == "OWNERSHIP_PERCENTAGE" else ("EVIDENCE_PRESENT_UNQUANTIFIED" if insider_evidence_present else "NOT_DETECTED")),
            "insider_ownership_pct": insider_metrics["ownership_pct"],
            "insider_transaction_exposure_vnd": insider_metrics["transaction_exposure_vnd"],
            "score": relationship_score,
        },
        "flow_risk": flow_risk,
        "ownership_risk": {
            "ownership_loops": len(ownership_cycles),
            "ownership_depth": _ownership_depth(relations),
            "score": min(100.0, len(ownership_cycles) * 60.0 + max(0, _ownership_depth(relations) - 2) * 10.0),
        },
        "structural_risk": {
            "cycles": len(cycles),
            "ownership_loops": len(ownership_cycles),
            "circular_flow_proven": bool(cycles),
            "multi_hop_flows": multi_hop_flows,
            "suspicious_multi_hop_flows": suspicious_multi_hop_flows,
            "external_capital_outflow": external_outflow["detected"],
            "external_capital_outflow_evidence": external_outflow,
            "potential_leakage": potential_leakage,
            "score": structural_score,
        },
        "materiality": {
            "largest_exposure_to_equity": ratio,
            "headline_metric": headline_metric,
            "breadth_score": breadth_score,
            "external_share": round(external_share, 4),
            "unresolved_share": round(unresolved_share, 4),
            "components": materiality["components"],
            "economic_materiality_score": materiality["economic_score"],
            "risk_adjusted_materiality_score": materiality["risk_adjusted_score"],
            "external_materiality_trigger": external_material,
            "activity_materiality_trigger": activity_material,
            "capital_allocation_materiality_trigger": capital_material,
            "score": materiality_score,
            "status": "UNVERIFIED" if materiality_score is None else ("MATERIAL" if materiality_score >= 35 else "LOW"),
            "cycle_to_equity": cycle_materiality_ratio,
            "scope_consistency": "VERIFIED" if scope_verified else "UNVERIFIED",
        },
    }
    effective_threshold = policy_context["effective_threshold"]
    review_required = bool(
        unresolved_share > 0
        or external_outflow["detected"]
        or temporal.get("trend_confidence") == "LOW"
        or materiality["economic_score"] >= 35
        or ratio_invariant_violations
    )
    economic_risk_trigger = bool(
        structural_score >= 60.0
        or (materiality["risk_adjusted_score"] >= 35.0 and flow_score >= 35.0)
        or capital_material
        or (ratio is not None and ratio > effective_threshold)
    )
    flag = "PASS"
    risk = "LOW"
    if cycle_materiality:
        flag = "CATASTROPHIC"
        risk = "CATASTROPHIC"
        reasons.append("PhĂ¡t hiá»‡n verified capital-flow cycle")
    if cycle_materiality:
        flag = "CATASTROPHIC"
        risk = "CATASTROPHIC"
        reasons.append("PhÄ‚Â¡t hiĂ¡Â»â€¡n verified capital-flow cycle")
    if ratio_invariant_violations:
        reasons.append("Accounting denominator invariant violation requires data repair before ratio interpretation.")
    elif economic_risk_trigger:
        flag = "WARNING"
        risk = "MEDIUM"
        reasons.append("Material related-party activity and unresolved/external relationship exposure require review; no proven circular flow, tunneling, or other catastrophic structural pattern.")
    elif review_required:
        flag = "WATCH"
        risk = "LOW_MEDIUM"
        reasons.append("No proven structural risk; external capital outflow and relationship uncertainty require analyst review.")
    else:
        reasons.append("KhĂ´ng phĂ¡t hiá»‡n capital-flow cycle hoáº·c exposure vÆ°á»£t ngÆ°á»¡ng trong evidence validated")
    result = {
        "ticker": issuer.ticker,
        "analysis_status": "COMPLETE",
        "gil_flag": flag,
        "risk_level": risk,
        "review_status": "REVIEW_REQUIRED" if review_required else "CLEAR",
        "gil_score": gil_score,
        "rpt_ratio": ratio,
        # Legacy field: keep it aligned with the canonical current-balance ratio.
        # Aggregate evidence remains in rpt_metrics.observed_evidence_amount_vnd.
        "total_rpt_exposure_vnd": round((ratio or 0.0) * equity, 2),
        "rpt_metrics": {
            "all_evidence_transaction_flow_vnd": exposure_buckets["transaction_flow_vnd"],
            "all_evidence_loan_amount_vnd": exposure_buckets["loan_exposure_vnd"],
            "all_evidence_receivable_payable_amount_vnd": exposure_buckets["receivable_payable_exposure_vnd"],
            "current_balance_loan_vnd": _denominator_exposure_buckets(relations_by_doc.get(latest_doc.id, []) if latest_doc else [], issuer_entity_id=issuer_entity_id).get("loan_balance_vnd", 0.0),
            "current_balance_receivable_vnd": _denominator_exposure_buckets(relations_by_doc.get(latest_doc.id, []) if latest_doc else [], issuer_entity_id=issuer_entity_id).get("receivable_vnd", 0.0),
            "guarantee_exposure_vnd": guarantee_state["value_vnd"],
            "guarantee_exposure": guarantee_state,
            "all_evidence_investment_amount_vnd": exposure_buckets["investment_exposure_vnd"],
            "current_capital_allocation_flow_vnd": _denominator_exposure_buckets(relations_by_doc.get(latest_doc.id, []) if latest_doc else [], issuer_entity_id=issuer_entity_id).get("capital_allocation_vnd", 0.0),
            "observed_evidence_amount_vnd": exposure,
            "receivable_concentration": receivable_concentration,
            "equity_ratio": ratio,
            "raw_equity_ratio": raw_ratio,
            "ratio_by_metric": {
                "current_balance_loan_to_equity": ratio_candidates.get("current_balance_loan_vnd"),
                "current_balance_receivable_to_equity": ratio_candidates.get("current_balance_receivable_payable_vnd"),
            },
            "headline_metric": headline_metric,
            "breadth_score": breadth_score,
            "asset_ratio": denominator_ratios.get("capital_allocation_to_total_assets"),
            "revenue_ratio": denominator_ratios.get("related_party_service_revenue_to_revenue"),
            "receivables_ratio": denominator_ratios.get("related_party_receivables_to_total_receivables"),
            "loans_to_cash_equity_ratio": denominator_ratios.get("loan_to_cash_plus_equity"),
            "denominator_flow_buckets": {
                **latest_flow_buckets,
                "guarantee_exposure_vnd": guarantee_state["value_vnd"],
            },
            "denominator_ratios": denominator_ratios,
            "raw_denominator_ratios": raw_denominator_ratios,
            "ratio_invariant_violations": ratio_invariant_violations,
            "denominator_scope_consistency": "VERIFIED" if scope_verified else "UNVERIFIED",
        },
        "evidence": _gil_evidence(relations, docs),
        "financial_denominators": document_denominators,
        "rpt_breakdown": breakdown,
        "insider_evidence": {
            "status": "QUANTIFIED_TRANSACTION" if insider_metrics["basis"] == "EXPLICIT_TRANSACTION" else ("QUANTIFIED_OWNERSHIP" if insider_metrics["basis"] == "OWNERSHIP_PERCENTAGE" else ("EVIDENCE_PRESENT_UNQUANTIFIED" if insider_evidence_present else "NOT_DETECTED")),
            "observation_count": len(insider_observations),
            "transaction_exposure_vnd": insider_metrics["transaction_exposure_vnd"],
            "ownership_pct": insider_metrics["ownership_pct"],
            "ownership_exposure_vnd": insider_metrics["ownership_exposure_vnd"],
            "ownership_evidence_count": insider_metrics["ownership_evidence_count"],
            "transaction_evidence_count": insider_metrics["transaction_evidence_count"],
            "basis": insider_metrics["basis"],
            "transaction_status": insider_metrics["transaction_status"],
            "requires_classification": insider_evidence_present and insider_metrics["basis"] == "NARRATIVE_ONLY",
            "ownership_control_score": insider_metrics["ownership_pct"],
            "insider_transaction_risk_score": _insider_transaction_risk_score(insider_metrics),
        },
        "risk_components": {
            "relationship_score": relationship_score,
            "flow_score": flow_score,
            "structural_score": structural_score,
            "materiality_score": materiality_score,
            "temporal_score": temporal_score,
            "gil_score": gil_score,
            "exposure_score": _nonlinear_exposure_score(ratio),
            "ownership_control_score": insider_metrics["ownership_pct"],
            "insider_transaction_risk_score": _insider_transaction_risk_score(insider_metrics),
            "semantic_risk_score": round((breakdown_amounts["insider_related"] / relationship_total) * 100.0, 2) if relationship_total else 0.0,
            "structural_risk_score": 100.0 if cycles else (25.0 if external_material else 0.0),
        },
        "dimensions": {
            "relationship": {"score": relationship_score, "status": "REVIEW" if relationship_score >= 20 else "LOW"},
            "flow": {"score": flow_score, "status": "MATERIAL" if flow_score >= 35 else "LOW"},
            "structural": {"score": structural_score, "status": "HARD_TRIGGER" if cycle_materiality else ("WATCH" if structural_score > 0 else "LOW")},
            "materiality": {"score": materiality_score, "status": "UNVERIFIED" if materiality_score is None else ("MATERIAL" if materiality_score >= 35 else "LOW")},
            "temporal": {"score": temporal_score, "status": temporal.get("status", "INSUFFICIENT_HISTORY"), "confidence": temporal.get("trend_confidence")},
        },
        "policy_context": policy_context,
        "company_context": {
            "sector": policy_context["sector"],
            "archetype": policy_context["company_archetype"],
        },
        "exposure": {
            "current_balance_loan_to_equity": ratio_candidates.get("current_balance_loan_vnd"),
            "current_balance_receivable_to_equity": ratio_candidates.get("current_balance_receivable_payable_vnd"),
            "current_balance_guarantee_to_equity": ratio_candidates.get("current_balance_guarantee_vnd"),
            "headline_ratio": ratio,
            "raw_headline_ratio": raw_ratio,
            "headline_metric": headline_metric,
        },
        "scores": {
            "relationship": relationship_score,
            "flow": flow_score,
            "materiality": materiality_score,
            "exposure": _nonlinear_exposure_score(ratio),
            "semantic": round((breakdown_amounts["insider_related"] / relationship_total) * 100.0, 2) if relationship_total else 0.0,
            "structural": 100.0 if cycles else (25.0 if external_material else 0.0),
            "temporal": temporal_score,
            "breadth": breadth_score,
            "gil": gil_score,
        },
        "graph": {
            "cycles_detected": len(cycles),
            "circular_flow_proven": bool(cycles),
        },
        "policy": {
            "base_threshold": policy_context["base_threshold"],
            "effective_threshold": effective_threshold,
            "sector_policy_applied": policy_context["sector_policy_applied"],
            "archetype_policy_applied": policy_context["archetype_policy_applied"],
        },
        **graph_layers,
        "circular_flow_proven": bool(cycles),
        "tunneling_signals": len(cycles),
        "catastrophic_triggered": cycle_materiality,
        "equity_vnd": equity,
        "cycles_detected": len(cycles),
        "cycle_paths": cycles,
        "reasons": reasons,
        "nodes_count": len({rel.subject for rel in relations} | {rel.object for rel in relations}),
        "edges_count": len(relations),
        "policy_version": GIL_POLICY_VERSION,
        "hard_triggers": {
            "circular_flow": bool(cycle_materiality),
            "tunneling": False,
            "hidden_ownership": False,
            "catastrophic": bool(cycle_materiality),
        },
        "decision": {
            "trade_blocked": bool(cycle_materiality),
            "review_required": review_required,
        },
        "data_quality": {
            "accounting_scope": "VERIFIED" if scope_verified else "UNVERIFIED",
            "scope_consistency": "PASS" if scope_verified else "UNVERIFIED",
            "relationship_resolution": round(classified_share, 4),
            "unresolved_relationship_share": round(unresolved_share, 4),
            "unknown_relationship_share": round(breakdown.get("unclassified", {}).get("share", 0.0), 4),
            "temporal_history": temporal.get("status", "INSUFFICIENT_HISTORY"),
            "missing_denominators": missing_denominators,
            "status": "PARTIAL" if (unresolved_share > 0 or not scope_verified or missing_denominators) else "VERIFIED",
            "confidence": "LOW" if (unresolved_share > 0 or missing_denominators or temporal.get("trend_confidence") == "LOW") else "HIGH",
            "ratio_invariant_violations": ratio_invariant_violations,
        },
        "temporal_risk": temporal,
    }
    if cached is None:
        cached = AssessmentRun(issuer_id=issuer.id, assessment_kind="gil")
        session.add(cached)
    cached.status = result["analysis_status"]
    cached.policy_version = GIL_POLICY_VERSION
    cached.document_ids_json = doc_ids
    cached.result_json = result
    cached.evidence_digest = evidence_digest
    await session.flush()
    return result


def _gil_insufficient(ticker: str, reasons: list[str], equity: float | None) -> dict[str, Any]:
    return {
        "ticker": ticker,
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
        "policy_context": {
            "sector": "UNKNOWN",
            "company_archetype": "UNKNOWN",
            "policy_profile": "universal-core-v1",
            "hard_trigger_requires_structural_evidence": True,
        },
        "policy_version": GIL_POLICY_VERSION,
    }


def _issuer_outgoing_relations(relations: list[Relation], issuer_entity_id: str | None) -> list[Relation]:
    if not issuer_entity_id or not any(getattr(rel, "subject_entity_id", None) for rel in relations):
        return relations
    return [rel for rel in relations if rel.subject_entity_id == issuer_entity_id]


def _guarantee_disclosure_state(
    relations: list[Relation], facts: list[Fact], *, issuer_entity_id: str | None = None
) -> dict[str, Any]:
    """Keep missing guarantee evidence distinct from an evidenced zero."""
    outgoing = _issuer_outgoing_relations(relations, issuer_entity_id)
    guarantee_relations = [
        rel for rel in outgoing
        if rel.relation_type == RelationType.GUARANTEES_FOR.value
    ]
    positive_relations = [rel for rel in guarantee_relations if float(rel.amount_vnd or 0.0) > 0]
    if positive_relations:
        return {
            "value_vnd": round(sum(float(rel.amount_vnd or 0.0) for rel in positive_relations), 2),
            "status": "DISCLOSED",
        }
    guarantee_facts = [
        fact for fact in facts
        if str(fact.fact_type or "") == FactType.GUARANTEE_BALANCE.value
        or "guarantee" in _normalize_metric_key(fact.semantic_key)
    ]
    values = [fact.value_numeric for fact in guarantee_facts if fact.value_numeric is not None]
    if values and all(float(value) == 0.0 for value in values):
        return {"value_vnd": 0.0, "status": "CONFIRMED_NONE"}
    if values:
        return {"value_vnd": None, "status": "UNRESOLVED"}
    return {"value_vnd": None, "status": "NOT_DISCLOSED"}


def _rpt_exposure_buckets(
    relations: list[Relation], *, issuer_entity_id: str | None = None
) -> dict[str, float]:
    """Separate transaction flows, balances, guarantees and investments."""
    relations = _issuer_outgoing_relations(relations, issuer_entity_id)
    buckets = {
        "transaction_flow_vnd": 0.0,
        "loan_exposure_vnd": 0.0,
        "receivable_payable_exposure_vnd": 0.0,
        "guarantee_exposure_vnd": 0.0,
        "investment_exposure_vnd": 0.0,
    }
    for rel in relations:
        amount = float(rel.amount_vnd or 0.0)
        relation_type = str(rel.relation_type or "").lower()
        kind = _relation_flow_kind(rel)
        if kind in {"loan", "loan_balance", "borrowing", "borrowing_balance"} or relation_type == RelationType.LENDS_TO.value:
            buckets["loan_exposure_vnd"] += amount
        elif kind in {"receivable", "receivable_balance"} or (
            relation_type == RelationType.CREDITOR_OF.value
            and kind not in {"loan", "loan_balance", "borrowing", "borrowing_balance"}
        ):
            buckets["receivable_payable_exposure_vnd"] += amount
        elif relation_type == RelationType.GUARANTEES_FOR.value:
            buckets["guarantee_exposure_vnd"] += amount
        elif relation_type == RelationType.INVESTS_IN.value:
            buckets["investment_exposure_vnd"] += amount
        elif relation_type == RelationType.TRANSACTS_WITH.value:
            buckets["transaction_flow_vnd"] += amount
    return buckets


def _relationship_breakdown(
    relations: list[Relation],
    owned_entities: set[str],
    owned_entity_ids: set[str],
    entity_types: dict[str, str],
) -> dict[str, float]:
    """Classify exposure by relationship, without treating all RPT as equal."""
    owned_name_keys = {_entity_name_key(name) for name in owned_entities}
    amounts = {
        "intra_group": 0.0,
        "associate": 0.0,
        "insider_related": 0.0,
        "external_affiliate": 0.0,
        "unclassified": 0.0,
    }
    insider_tokens = (
        "person", "individual", "insider", "board", "director", "executive",
        "family", "relative", "member",
    )
    for rel in relations:
        amount = float(rel.amount_vnd or 0.0)
        # Prefer persisted entity identity.  Names vary between full legal
        # names and abbreviations (CTCP/CĂ´ng ty Cá»• pháº§n), so string equality
        # must only be a fallback.
        if (
            (rel.object_entity_id and rel.object_entity_id in owned_entity_ids)
            or rel.object in owned_entities
            or _entity_name_key(rel.object) in owned_name_keys
        ):
            bucket = "intra_group"
        else:
            object_type = entity_types.get(rel.object_entity_id or "", "")
            raw = f"{object_type} {rel.raw_label or ''} {rel.object}".lower()
            if any(token in raw for token in insider_tokens):
                bucket = "insider_related"
            elif any(token in raw for token in ("affiliate", "associate", "joint venture", "jv", "liĂªn káº¿t", "liĂªn doanh")):
                bucket = "associate"
            elif object_type in {"related_party", "other", "", "none"}:
                # "Related party" alone does not prove subsidiary, associate,
                # or external leakage. Keep it unresolved until ownership or
                # controller evidence links the entity.
                bucket = "unclassified"
            else:
                bucket = "external_affiliate"
        amounts[bucket] += amount
    return amounts


def _nonlinear_exposure_score(ratio: float | None) -> float:
    """Convert a raw ratio to a conservative, non-linear screening score."""
    if ratio is None or ratio <= 0:
        return 0.0
    if ratio <= 0.10:
        return round(ratio * 100.0, 2)
    if ratio <= 0.25:
        return round(10.0 + (ratio - 0.10) / 0.15 * 25.0, 2)
    if ratio <= 0.50:
        return round(35.0 + (ratio - 0.25) / 0.25 * 35.0, 2)
    return round(min(100.0, 70.0 + (ratio - 0.50) / 0.50 * 30.0), 2)


def _entity_name_key(value: str | None) -> str:
    """Normalize Vietnamese legal-name variants for graph fallback matching."""
    import unicodedata

    raw = unicodedata.normalize("NFD", str(value or "")).lower()
    raw = "".join(char for char in raw if unicodedata.category(char) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    replacements = (
        ("ctcp", "cong ty co phan"),
        ("c p", "cong ty co phan"),
        ("cty cp", "cong ty co phan"),
        ("cty", "cong ty"),
        ("tnhh mtv", "cong ty tnhh mot thanh vien"),
    )
    for source, target in replacements:
        raw = raw.replace(source, target)
    return re.sub(r"\s+", " ", raw).strip()


async def _load_market_financials(ticker: str) -> dict[str, Any]:
    """Read the latest quarterly BS/IS/CF row from the market master DB."""
    global _market_engine
    if _market_engine is None:
        _market_engine = create_async_engine(
            settings.market_database_url,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
        )
    query = text(
        """
        SELECT period_end, statement_type, frequency, data
        FROM public.financial_statements
        WHERE symbol = :ticker AND frequency = 'quarterly'
        ORDER BY period_end DESC
        """
    )
    try:
        async with _market_engine.connect() as connection:
            rows = (await connection.execute(query, {"ticker": ticker.upper()})).mappings().all()
    except Exception as exc:  # GIL remains usable if the optional market DB is unavailable.
        logger.warning("market financials unavailable for %s: %s", ticker, exc)
        return {"status": "UNAVAILABLE"}
    if not rows:
        return {"status": "MISSING", "ticker": ticker.upper()}
    latest_period = rows[0]["period_end"]
    latest = {row["statement_type"]: row["data"] or {} for row in rows if row["period_end"] == latest_period}
    bs, inc, cf = latest.get("BS", {}), latest.get("IS", {}), latest.get("CF", {})
    values = {
        "total_assets": _json_metric(bs, ("tá»•ng cá»™ng tĂ i sáº£n", "tong cong tai san")),
        "cash": _json_metric(bs, ("tiá»n vĂ  cĂ¡c khoáº£n tÆ°Æ¡ng Ä‘Æ°Æ¡ng tiá»n", "tien va cac khoan tuong duong tien")),
        "total_receivables": _json_metric(bs, ("cĂ¡c khoáº£n pháº£i thu ngáº¯n háº¡n", "cac khoan phai thu ngan han")),
        "total_payables": _json_metric(bs, ("ná»£ pháº£i tráº£", "no phai tra")),
        "equity": _json_metric(bs, ("vá»‘n chá»§ sá»Ÿ há»¯u", "von chu so huu")),
        "revenue": _json_metric(inc, ("doanh thu thuáº§n", "doanh thu ban hang va cung cap dich vu")),
        "profit": _json_metric(inc, ("lá»£i nhuáº­n sau thuáº¿", "loi nhuan sau thue")),
        "interest_expense": _json_metric(inc, ("chi phĂ­ lĂ£i vay", "chi phi lai vay")),
        "operating_cash_flow": _json_metric(cf, ("lÆ°u chuyá»ƒn tiá»n thuáº§n tá»« hoáº¡t Ä‘á»™ng kinh doanh", "luu chuyen tien thuan tu hoat dong kinh doanh")),
        "investing_cash_flow": _json_metric(cf, ("lÆ°u chuyá»ƒn tiá»n thuáº§n tá»« hoáº¡t Ä‘á»™ng Ä‘áº§u tÆ°", "luu chuyen tien thuan tu hoat dong dau tu")),
        "financing_cash_flow": _json_metric(cf, ("lÆ°u chuyá»ƒn tiá»n thuáº§n tá»« hoáº¡t Ä‘á»™ng tĂ i chĂ­nh", "luu chuyen tien thuan tu hoat dong tai chinh")),
    }
    return {
        "status": "OK", "ticker": ticker.upper(), "period_end": str(latest_period),
        "frequency": "quarterly", "accounting_scope": "UNKNOWN",
        "accounting_scope_verified": False, **values,
    }


def _json_metric(data: dict[str, Any], labels: tuple[str, ...]) -> float | None:
    normalized = {_normalize_metric_key(key): value for key, value in data.items()}
    for label in labels:
        needle = _normalize_metric_key(label)
        for key, value in normalized.items():
            if needle in key and isinstance(value, (int, float)):
                return float(value)
    return None


def _document_denominator_coverage(
    facts: list[Fact], latest_quarter_facts: list[Fact]
) -> dict[str, Any]:
    required = {
        "equity": {"equity", "total_equity"},
        "total_assets": {"total_assets"},
        "total_receivables": {"total_receivables"},
        "total_payables": {"total_payables", "payable_balance"},
        "cash_and_equivalents": {"cash_and_equivalents"},
        "revenue": {"revenue"},
        "financial_income": {"financial_income"},
        "cost_of_goods_sold": {"cost_of_goods_sold"},
    }
    available = {str(fact.semantic_key or "") for fact in latest_quarter_facts}
    scopes = {
        str((fact.metadata_json or {}).get("accounting_scope") or (fact.metadata_json or {}).get("statement_scope"))
        for fact in latest_quarter_facts
        if (fact.metadata_json or {}).get("accounting_scope") or (fact.metadata_json or {}).get("statement_scope")
    }
    metrics = {
        name: _document_metric(latest_quarter_facts, keys)
        for name, keys in required.items()
    }
    return {
        "status": "DOCUMENT_FACTS_ONLY",
        "source": "sag_document_facts",
        "accounting_scope": next(iter(scopes)) if len(scopes) == 1 else "UNKNOWN",
        "scope_consistent": len(scopes) == 1 and bool(latest_quarter_facts),
        "scope_reason": "single document/period facts" if len(scopes) == 1 and latest_quarter_facts else "missing or mixed accounting scope",
        "available_semantic_keys": sorted(available),
        "metrics": metrics,
        "missing_latest_quarter": sorted(name for name, value in metrics.items() if value is None),
        "historical_fact_count": len(facts),
    }


def _document_metric(facts: list[Fact], keys: set[str]) -> float | None:
    candidates = [
        fact for fact in facts
        if fact.semantic_key in keys
        and fact.value_numeric is not None
        and (fact.metadata_json or {}).get("source") == "DETERMINISTIC_STATEMENT_TABLE"
    ]
    if not candidates:
        return None
    balance_keys = {"equity", "total_equity", "total_assets", "total_receivables", "total_payables", "cash_and_equivalents"}
    period_current = [
        fact for fact in candidates
        if any(token in _normalize_metric_key((fact.metadata_json or {}).get("statement_column"))
               for token in ("kynay", "namnay", "currentperiod", "thisperiod"))
        and "thuyetminh" not in _normalize_metric_key((fact.metadata_json or {}).get("statement_column"))
    ]
    balance_current = [
        fact for fact in candidates
        if "cuoiky" in _normalize_metric_key((fact.metadata_json or {}).get("statement_column"))
        and "thuyetminh" not in _normalize_metric_key((fact.metadata_json or {}).get("statement_column"))
    ]
    clean = [
        fact for fact in candidates
        if "thuyetminh" not in _normalize_metric_key((fact.metadata_json or {}).get("statement_column"))
    ]
    preferred = balance_current if keys.intersection(balance_keys) else period_current
    return float((preferred or period_current or clean or candidates)[0].value_numeric)


def _denominator_exposure_buckets(
    relations: list[Relation], *, issuer_entity_id: str | None = None
) -> dict[str, float]:
    """Map only explicitly classified flows to their valid denominator."""
    relations = _issuer_outgoing_relations(relations, issuer_entity_id)
    buckets = {
        "service_revenue_vnd": 0.0,
        "capital_allocation_vnd": 0.0,
        "receivable_vnd": 0.0,
        "payable_vnd": 0.0,
        "interest_payable_vnd": 0.0,
        "loan_vnd": 0.0,
        "loan_balance_vnd": 0.0,
        "guarantee_exposure_vnd": 0.0,
        "dividend_vnd": 0.0,
        "purchase_vnd": 0.0,
        "loan_repayment_vnd": 0.0,
        "profit_transfer_vnd": 0.0,
        "interest_expense_vnd": 0.0,
        "lease_commitment_vnd": 0.0,
        "non_ratio_flow_vnd": 0.0,
        "non_ratio_flow_kinds": {},
    }
    for rel in relations:
        amount = float(rel.amount_vnd or 0.0)
        if amount <= 0:
            continue
        kind = _relation_flow_kind(rel)
        relation_type = str(rel.relation_type or "").lower()
        if kind in {"service_revenue"}:
            bucket = "service_revenue_vnd"
        elif kind in {"investment", "capital_contribution", "capital_contribution_in_kind", "capital_increase", "capital_transfer", "asset_contribution", "divestment"} or (not kind and relation_type == RelationType.INVESTS_IN.value):
            bucket = "capital_allocation_vnd"
        elif kind in {"loan_balance", "borrowing_balance"}:
            bucket = "loan_balance_vnd"
        elif kind in {"loan", "borrowing"} or (not kind and relation_type == RelationType.LENDS_TO.value):
            bucket = "loan_vnd"
        elif kind in {"receivable", "receivable_balance"} or (not kind and relation_type == RelationType.CREDITOR_OF.value):
            bucket = "receivable_vnd"
        elif kind in {"payable", "payable_balance"}:
            bucket = "payable_vnd"
        elif kind == "interest_payable":
            bucket = "interest_payable_vnd"
        elif kind == "guarantee" or relation_type == RelationType.GUARANTEES_FOR.value:
            bucket = "guarantee_exposure_vnd"
        elif kind == "dividend":
            bucket = "dividend_vnd"
        elif kind == "purchase":
            bucket = "purchase_vnd"
        elif kind == "loan_repayment":
            bucket = "loan_repayment_vnd"
        elif kind == "profit_transfer":
            bucket = "profit_transfer_vnd"
        elif kind == "interest_expense":
            bucket = "interest_expense_vnd"
        elif kind == "operating_lease":
            bucket = "lease_commitment_vnd"
        else:
            buckets["non_ratio_flow_vnd"] += amount
            label = kind or relation_type or "unknown"
            kinds = buckets["non_ratio_flow_kinds"]
            kinds[label] = kinds.get(label, 0.0) + amount
            continue
        buckets[bucket] += amount
    return buckets


def _document_denominator_ratios(
    buckets: dict[str, float], document: dict[str, Any], equity: float | None
) -> dict[str, float | None]:
    if not document.get("scope_consistent"):
        return {
            "related_party_service_revenue_to_revenue": None,
            "capital_allocation_to_total_assets": None,
            "related_party_receivables_to_total_receivables": None,
            "payable_to_total_payables": None,
            "interest_payable_to_total_payables": None,
            "loan_to_cash_plus_equity": None,
            "guarantee_to_equity": None,
            "dividend_to_financial_income": None,
            "purchase_to_cost_of_goods_sold": None,
            "loan_repayment_to_loan": None,
            "profit_transfer_to_financial_income": None,
        }
    metrics = document.get("metrics", {})
    assets = metrics.get("total_assets")
    cash = metrics.get("cash_and_equivalents")
    revenue = metrics.get("revenue")
    total_receivables = metrics.get("total_receivables")
    total_payables = metrics.get("total_payables")
    financial_income = metrics.get("financial_income")
    cost_of_goods_sold = metrics.get("cost_of_goods_sold")
    return {
        "related_party_service_revenue_to_revenue": buckets["service_revenue_vnd"] / revenue if revenue else None,
        "capital_allocation_to_total_assets": buckets["capital_allocation_vnd"] / assets if assets else None,
        "related_party_receivables_to_total_receivables": buckets["receivable_vnd"] / total_receivables if total_receivables else None,
        "payable_to_total_payables": buckets["payable_vnd"] / total_payables if total_payables else None,
        "interest_payable_to_total_payables": buckets["interest_payable_vnd"] / total_payables if total_payables else None,
        "loan_to_cash_plus_equity": (buckets["loan_balance_vnd"] or buckets["loan_vnd"]) / (cash + equity) if cash is not None and equity is not None else None,
        "guarantee_to_equity": buckets["guarantee_exposure_vnd"] / equity if equity and buckets["guarantee_exposure_vnd"] else None,
        "dividend_to_financial_income": buckets["dividend_vnd"] / financial_income if financial_income else None,
        "purchase_to_cost_of_goods_sold": buckets["purchase_vnd"] / cost_of_goods_sold if cost_of_goods_sold else None,
        "loan_repayment_to_loan": buckets["loan_repayment_vnd"] / buckets["loan_vnd"] if buckets["loan_vnd"] else None,
        "profit_transfer_to_financial_income": buckets["profit_transfer_vnd"] / financial_income if financial_income else None,
    }


def _ratio_invariant_violations(ratios: dict[str, float | None]) -> list[dict[str, Any]]:
    """Flag subset-to-total ratios that exceed one instead of scoring them."""
    subset_totals = {
        "related_party_receivables_to_total_receivables": "RATIO_INVARIANT_VIOLATION",
        "payable_to_total_payables": "RATIO_INVARIANT_VIOLATION",
        "interest_payable_to_total_payables": "RATIO_INVARIANT_VIOLATION",
    }
    return [
        {"metric": metric, "code": code, "ratio": value}
        for metric, code in subset_totals.items()
        if (value := ratios.get(metric)) is not None and value > 1.0
    ]


def _denominator_ratios(
    buckets: dict[str, float], market: dict[str, Any], equity: float | None
) -> dict[str, float | None]:
    """Calculate only ratios whose numerator and denominator are available."""
    assets = market.get("total_assets")
    cash = market.get("cash")
    revenue = market.get("revenue")
    total_receivables = market.get("total_receivables")
    return {
        "related_party_service_revenue_to_revenue": buckets["transaction_flow_vnd"] / revenue if revenue else None,
        "capital_allocation_to_total_assets": buckets["investment_exposure_vnd"] / assets if assets else None,
        "related_party_receivables_to_total_receivables": buckets["receivable_payable_exposure_vnd"] / total_receivables if total_receivables else None,
        "loan_to_cash_plus_equity": buckets["loan_exposure_vnd"] / (cash + equity) if cash and equity else None,
        "guarantee_to_equity": buckets["guarantee_exposure_vnd"] / equity if equity and buckets["guarantee_exposure_vnd"] else None,
    }


def _normalize_metric_key(value: Any) -> str:
    import unicodedata
    raw = unicodedata.normalize("NFD", str(value)).lower()
    return "".join(char for char in raw if unicodedata.category(char) != "Mn" and char.isalnum())


def _gil_policy_context(
    *,
    ticker: str,
    legal_name: str | None,
    metadata: dict[str, Any],
    relations: list[Relation],
    owned_entities: set[str],
) -> dict[str, Any]:
    """Resolve a conservative sector/archetype context without changing the core trigger."""
    raw_sector = str(
        metadata.get("industry")
        or metadata.get("sector")
        or metadata.get("industry_name")
        or ""
    ).strip()
    # Do not infer the issuer's sector from subsidiary names: a diversified
    # holding can own real-estate, agriculture, steel and finance entities.
    label = f"{ticker} {legal_name or ''} {raw_sector}".lower()
    if any(token in label for token in ("ngĂ¢n hĂ ng", "bank")):
        sector, archetype = "BANKING", "BANK"
    elif any(token in label for token in ("chá»©ng khoĂ¡n", "securities", "broker")):
        sector, archetype = "FINANCIAL_SERVICES", "BROKER"
    elif any(token in label for token in ("báº¥t Ä‘á»™ng sáº£n", "real estate", "property")):
        sector, archetype = "REAL_ESTATE", "REAL_ESTATE"
    elif any(token in label for token in ("thĂ©p", "steel", "váº­t liá»‡u")):
        sector = "MATERIALS"
        archetype = "HOLDING" if owned_entities else "OPERATING_COMPANY"
    else:
        sector = raw_sector.upper().replace(" ", "_") if raw_sector else "UNKNOWN"
        ownership_edges = [
            rel for rel in relations
            if rel.relation_type in {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
        ]
        archetype = "HOLDING" if len(ownership_edges) >= 2 and owned_entities else "OPERATING_COMPANY"
    return {
        "sector": sector,
        "company_archetype": archetype,
        "policy_profile": "universal-core-v1+sector-archetype-v1",
        "base_threshold": 0.25,
        "effective_threshold": _effective_threshold(sector, archetype),
        "sector_policy_applied": sector != "UNKNOWN",
        "archetype_policy_applied": archetype != "UNKNOWN",
        "hard_trigger_requires_structural_evidence": True,
        "exposure_is_non_fatal": True,
    }


def _effective_threshold(sector: str, archetype: str) -> float:
    """Conservative screening threshold; never changes the hard cycle trigger."""
    archetype_multiplier = {
        "HOLDING": 1.60,
        "CONGLOMERATE": 1.50,
        "REAL_ESTATE": 1.25,
        "REAL_ESTATE_DEVELOPER": 1.25,
        "BANK": 1.00,
        "BROKER": 1.00,
        "FINANCIAL_INSTITUTION": 1.00,
        "OPERATING_COMPANY": 1.00,
        "INFRASTRUCTURE_PROJECT": 1.20,
        "UTILITY": 1.15,
    }.get(archetype, 1.00)
    sector_multiplier = {
        "MATERIALS": 1.10,
        "BASIC_RESOURCES": 1.10,
        "BANKING": 1.00,
        "BANKS": 1.00,
        "FINANCIAL_SERVICES": 1.00,
        "REAL_ESTATE": 1.05,
        "CONSTRUCTION": 1.05,
        "CONSTRUCTION_MATERIALS": 1.05,
        "CHEMICALS": 1.00,
        "OIL_GAS": 1.05,
        "FOOD_BEVERAGE": 1.00,
        "TECHNOLOGY": 0.90,
        "INDUSTRIAL_GOODS": 1.00,
        "TRANSPORTATION": 1.05,
        "RETAIL_TRADE": 0.90,
        "HEALTHCARE": 0.95,
        "UTILITIES": 1.10,
        "AGRICULTURE": 1.05,
        "OTHER_INDUSTRIALS": 1.00,
    }.get(sector, 1.00)
    return round(0.25 * archetype_multiplier * sector_multiplier, 4)


def _flow_risk_breakdown(
    relations: list[Relation], *, guarantee_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    amounts = {"loan": 0.0, "receivable": 0.0, "guarantee": 0.0, "investment": 0.0, "transaction": 0.0}
    for rel in relations:
        amount = float(rel.amount_vnd or 0.0)
        relation_type = str(rel.relation_type or "").lower()
        if relation_type == RelationType.LENDS_TO.value:
            amounts["loan"] += amount
        elif relation_type == RelationType.CREDITOR_OF.value:
            amounts["receivable"] += amount
        elif relation_type == RelationType.GUARANTEES_FOR.value:
            amounts["guarantee"] += amount
        elif relation_type == RelationType.INVESTS_IN.value:
            amounts["investment"] += amount
        elif relation_type == RelationType.TRANSACTS_WITH.value:
            amounts["transaction"] += amount
    state = guarantee_state or {"value_vnd": amounts["guarantee"], "status": "DISCLOSED"}
    known = {"loan": True, "receivable": True, "investment": True, "transaction": True}
    known["guarantee"] = state["status"] in {"DISCLOSED", "CONFIRMED_NONE"}
    total = sum(amounts[key] for key, is_known in known.items() if is_known)
    mix = {
        key: (round(amounts[key] / total, 4) if total and is_known else None)
        for key, is_known in known.items()
    }
    weights = {"loan": 0.55, "receivable": 0.65, "guarantee": 0.85, "investment": 0.35, "transaction": 0.20}
    coverage = round(
        sum(weight for key, weight in weights.items() if known[key]) / sum(weights.values()), 4
    )
    mix.update({
        "guarantee_status": state["status"],
        "score": _flow_risk_score(mix),
        "score_basis": "OBSERVED_EVIDENCE_ONLY",
        "flow_evidence_coverage": coverage,
    })
    return mix


def _relationship_risk_score(breakdown: dict[str, float], insider_evidence: bool) -> float:
    """Score who the issuer is exposed to; unknown is uncertainty, not zero."""
    total = sum(max(0.0, float(value)) for value in breakdown.values())
    if not total:
        return 20.0 if insider_evidence else 0.0
    weights = {
        "intra_group": 0.10,
        "associate": 0.30,
        "insider_related": 1.00,
        "external_affiliate": 0.50,
        "unclassified": 0.60,
    }
    score = sum((float(value) / total) * weights[key] for key, value in breakdown.items())
    if insider_evidence and breakdown.get("insider_related", 0.0) <= 0:
        score = max(score, 0.20)
    return round(min(100.0, score * 100.0), 2)


def _flow_risk_score(flow_mix: dict[str, float | None]) -> float:
    weights = {"loan": 0.55, "receivable": 0.65, "guarantee": 0.85, "investment": 0.35, "transaction": 0.20}
    return round(min(100.0, sum((flow_mix.get(key) or 0.0) * weight for key, weight in weights.items()) * 100.0), 2)


def _structural_risk_score(*, cycles: int, ownership_loops: int, multi_hop: int, external_leakage: bool) -> float:
    if cycles:
        return 100.0
    score = 0.0
    if ownership_loops:
        score += 60.0
    if multi_hop:
        score += min(30.0, multi_hop * 10.0)
    if external_leakage:
        score += 25.0
    return min(100.0, score)


def _materiality_components(
    balance_ratio: float | None,
    service_ratio: float | None,
    capital_ratio: float | None,
    receivable_ratio: float | None,
    payable_ratio: float | None,
    loan_ratio: float | None,
    guarantee_ratio: float | None,
    dividend_ratio: float | None,
    purchase_ratio: float | None,
    external_share: float,
    archetype: str = "UNKNOWN",
) -> dict[str, Any]:
    balance = _nonlinear_exposure_score(balance_ratio)
    activity = _nonlinear_exposure_score(max((v or 0.0) for v in (service_ratio, purchase_ratio)))
    distribution = _nonlinear_exposure_score(dividend_ratio)
    external = round(min(100.0, max(0.0, external_share * 100.0)), 2)
    capital = _nonlinear_exposure_score(capital_ratio)
    economic_score = round(
        0.35 * balance + 0.30 * max(activity, distribution) + 0.20 * external + 0.15 * capital,
        2,
    )
    context_activity = activity * (0.50 if archetype == "HOLDING" else 1.0)
    context_distribution = distribution * (0.15 if archetype == "HOLDING" else 0.50)
    risk_adjusted_score = round(
        0.40 * balance + 0.20 * max(context_activity, context_distribution)
        + 0.25 * external + 0.15 * capital,
        2,
    )
    return {
        "components": {
            "balance": balance,
            "activity": activity,
            "distribution": distribution,
            "external": external,
            "capital_allocation": capital,
        },
        "economic_score": economic_score,
        "risk_adjusted_score": risk_adjusted_score,
        "score": risk_adjusted_score,
    }


def _insider_transaction_risk_score(metrics: dict[str, Any]) -> float | None:
    if metrics.get("basis") != "EXPLICIT_TRANSACTION":
        return None
    return _nonlinear_exposure_score(metrics.get("transaction_exposure_vnd"))


def _external_capital_outflow_evidence(
    relations: list[Relation], breakdown: dict[str, float], entity_types: dict[str, str],
    *, suspicious_multi_hop_flows: int = 0, cycles: int = 0,
) -> dict[str, Any]:
    capital_kinds = {"capital_contribution", "capital_increase", "capital_transfer", "asset_contribution", "investment", "divestment"}
    external = []
    for rel in relations:
        raw = f"{entity_types.get(rel.object_entity_id or '', '')} {rel.raw_label or ''} {rel.object}".lower()
        kind = _relation_flow_kind(rel)
        if (
            float(rel.amount_vnd or 0.0) > 0
            and (kind in capital_kinds or rel.relation_type == RelationType.INVESTS_IN.value)
            and any(token in raw for token in ("affiliate", "associate", "joint venture", "jv", "external"))
        ):
            external.append(rel)
    detected = bool(external)
    status = "NONE"
    if detected:
        status = "TUNNELING_EVIDENCE" if cycles else ("POTENTIAL_LEAKAGE" if suspicious_multi_hop_flows else "EXTERNAL_OUTFLOW")
    return {
        "detected": detected,
        "status": status,
        "outbound_amount_vnd": round(sum(float(rel.amount_vnd or 0.0) for rel in external), 2),
        "counterparty_count": len({rel.object_entity_id or rel.object for rel in external}),
        "paths": [],
        "flow_types": sorted({str(_relation_flow_kind(rel) or rel.relation_type) for rel in external}),
    }


def _ownership_depth(relations: list[Relation], max_depth: int = 8) -> int:
    edges = {}
    ownership_types = {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
    for rel in relations:
        if rel.relation_type in ownership_types:
            edges.setdefault(rel.subject, set()).add(rel.object)
    best = 0
    for root in edges:
        stack = [(root, 0, set())]
        while stack:
            node, depth, seen = stack.pop()
            best = max(best, depth)
            if depth >= max_depth:
                continue
            for child in edges.get(node, set()):
                if child not in seen:
                    stack.append((child, depth + 1, seen | {child}))
    return best


def _detect_ownership_loops(relations: list[Relation]) -> list[list[str]]:
    graph: dict[str, set[str]] = {}
    for rel in relations:
        graph.setdefault(rel.subject, set()).add(rel.object)
    found: set[tuple[str, ...]] = set()
    for start in graph:
        stack = [(start, [start])]
        while stack:
            node, path = stack.pop()
            for child in graph.get(node, set()):
                if child == start and len(path) > 1:
                    found.add(tuple(path + [start]))
                elif child not in path and len(path) < 8:
                    stack.append((child, path + [child]))
    return [list(path) for path in sorted(found)]


def _multi_hop_flow_count(relations: list[Relation], owned_entities: set[str] | None = None) -> int:
    """Count only cross-boundary two-hop flows, not normal holding-company paths.

    ponytail: this is intentionally conservative until beneficial-owner and
    counterparty resolution are complete; upgrade to amount-weighted paths then.
    """
    flow_types = {
        RelationType.LENDS_TO.value,
        RelationType.CREDITOR_OF.value,
        RelationType.GUARANTEES_FOR.value,
        RelationType.TRANSACTS_WITH.value,
    }
    adjacency: dict[str, set[str]] = {}
    for rel in relations:
        if rel.relation_type in flow_types and rel.amount_vnd and rel.amount_vnd > 0:
            adjacency.setdefault(rel.subject, set()).add(rel.object)
    owned_keys = {_entity_name_key(value) for value in (owned_entities or set())}
    count = 0
    for source, targets in adjacency.items():
        for target in targets:
            if target in adjacency:
                for endpoint in adjacency[target]:
                    if (
                        _entity_name_key(source) not in owned_keys
                        or _entity_name_key(target) not in owned_keys
                        or _entity_name_key(endpoint) not in owned_keys
                    ):
                        count += 1
    return count


def _suspicious_multi_hop_flow_count(
    relations: list[Relation], owned_entities: set[str], entity_types: dict[str, str]
) -> int:
    """Count only quantified paths that cross into a resolved external party."""
    flow_types = {
        RelationType.LENDS_TO.value,
        RelationType.CREDITOR_OF.value,
        RelationType.GUARANTEES_FOR.value,
        RelationType.TRANSACTS_WITH.value,
    }
    adjacency: dict[str, set[str]] = {}
    for rel in relations:
        if rel.relation_type in flow_types and float(rel.amount_vnd or 0.0) > 0:
            adjacency.setdefault(rel.subject, set()).add(rel.object)
    owned = {_entity_name_key(value) for value in owned_entities}
    suspicious = 0
    for source, targets in adjacency.items():
        for middle in targets:
            for endpoint in adjacency.get(middle, set()):
                raw = f"{entity_types.get(endpoint, '')} {endpoint}".lower()
                if _entity_name_key(endpoint) not in owned and any(token in raw for token in ("affiliate", "associate", "person", "insider", "external")):
                    suspicious += 1
    return suspicious


def _gil_policy_context_v2(
    *,
    ticker: str,
    legal_name: str | None,
    metadata: dict[str, Any],
    relations: list[Relation],
    owned_entities: set[str],
) -> dict[str, Any]:
    """Full sector-family/archetype policy context with conservative fallback."""
    raw = str(
        metadata.get("sector")
        or metadata.get("industry")
        or metadata.get("industry_name")
        or ""
    ).lower()
    aliases = {
        "basic_resources": "MATERIALS", "materials": "MATERIALS",
        "banks": "BANKING", "banking": "BANKING", "bank": "BANKING",
        "financial_services": "FINANCIAL_SERVICES", "financials": "FINANCIAL_SERVICES",
        "real_estate": "REAL_ESTATE", "real estate": "REAL_ESTATE",
        "construction_materials": "CONSTRUCTION_MATERIALS",
        "food_beverage": "FOOD_BEVERAGE", "retail_trade": "RETAIL_TRADE",
        "healthcare": "HEALTHCARE", "utilities": "UTILITIES",
        "agriculture": "AGRICULTURE", "technology": "TECHNOLOGY",
        "industrial_goods": "INDUSTRIAL_GOODS", "transportation": "TRANSPORTATION",
        "chemicals": "CHEMICALS", "oil_gas": "OIL_GAS",
        "other_industrials": "OTHER_INDUSTRIALS",
    }
    sector = next((value for key, value in aliases.items() if key in raw), None)
    keyword_sectors = {
        "ngĂ¢n hĂ ng": "BANKING", "chá»©ng khoĂ¡n": "FINANCIAL_SERVICES",
        "báº£o hiá»ƒm": "FINANCIAL_SERVICES", "báº¥t Ä‘á»™ng sáº£n": "REAL_ESTATE",
        "váº­t liá»‡u": "CONSTRUCTION_MATERIALS", "xĂ¢y dá»±ng": "CONSTRUCTION",
        "thĂ©p": "MATERIALS", "hĂ³a cháº¥t": "CHEMICALS", "dáº§u khĂ­": "OIL_GAS",
        "thá»±c pháº©m": "FOOD_BEVERAGE", "cĂ´ng nghá»‡": "TECHNOLOGY",
        "váº­n táº£i": "TRANSPORTATION", "bĂ¡n láº»": "RETAIL_TRADE",
        "y táº¿": "HEALTHCARE", "Ä‘iá»‡n": "UTILITIES", "nĂ´ng nghiá»‡p": "AGRICULTURE",
    }
    if sector is None:
        sector = next((value for key, value in keyword_sectors.items() if key in raw), "UNKNOWN")
    ownership_edges = [
        rel for rel in relations
        if rel.relation_type in {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
    ]
    if sector == "BANKING":
        archetype = "BANK"
    elif sector == "FINANCIAL_SERVICES":
        archetype = "FINANCIAL_INSTITUTION"
    elif sector == "REAL_ESTATE":
        archetype = "REAL_ESTATE_DEVELOPER"
    elif sector == "UTILITIES":
        archetype = "UTILITY"
    elif sector in {"CONSTRUCTION", "CONSTRUCTION_MATERIALS"}:
        archetype = "INFRASTRUCTURE_PROJECT"
    elif len(ownership_edges) >= 2 and owned_entities:
        archetype = "HOLDING"
    else:
        archetype = "OPERATING_COMPANY"
    return {
        "sector": sector,
        "company_archetype": archetype,
        "policy_profile": "universal-core-v1+sector-archetype-v2",
        "base_threshold": 0.25,
        "effective_threshold": _effective_threshold(sector, archetype),
        "sector_policy_applied": sector != "UNKNOWN",
        "archetype_policy_applied": archetype != "UNKNOWN",
        "hard_trigger_requires_structural_evidence": True,
        "exposure_is_non_fatal": True,
    }


def _latest_equity(facts: list[Fact]) -> float | None:
    # Ownership/share-count rows can be mislabeled as equity by the generic
    # table classifier (for example ``CĂ¡c cá»• Ä‘Ă´ng khĂ¡c = 6.732``). They are
    # not valid monetary denominators. Require a VND-scale amount and prefer
    # explicit total-equity labels when available.
    equities = [
        fact for fact in facts
        if fact.fact_type == FactType.EQUITY.value
        and fact.value_numeric
        and float(fact.value_numeric) >= 1_000_000_000
        and str(fact.unit or "VND").casefold() in {"vnd", "Ä‘á»“ng", "dong", ""}
    ]
    if not equities:
        return None
    # Prefer an explicitly parsed statement total. Governance/shareholder
    # ownership rows can share the generic EQUITY enum but are not a balance
    # sheet denominator.
    statement_equities = [
        fact for fact in equities
        if (fact.metadata_json or {}).get("source") == "DETERMINISTIC_STATEMENT_TABLE"
        and fact.semantic_key in {"equity", "total_equity"}
    ]
    if statement_equities:
        equities = statement_equities
    # Prefer the total-equity fact over a component (e.g. contributed capital)
    # when both are reported for the same balance-sheet date.
    equities.sort(
        key=lambda fact: (
            1 if str(fact.semantic_key or "").startswith("total_equity") else 0,
            str(fact.as_of or fact.period_end or fact.period_start or ""),
        ),
        reverse=True,
    )
    return float(equities[0].value_numeric or 0)


def _temporal_risk(
    relations: list[Relation], facts: list[Fact], docs: list[Document],
    *, issuer_entity_id: str | None = None,
) -> dict[str, Any]:
    """Measure deterioration of the largest quantified balance exposure.

    This is deliberately deterministic and conservative: it only compares
    periods for which both an extracted equity denominator and quantified flow
    edges exist. Missing history is reported as such, never as zero or a
    fabricated trend.
    """
    doc_by_id = {doc.id: doc for doc in docs}
    facts_by_doc: dict[str, list[Fact]] = {}
    for fact in facts:
        facts_by_doc.setdefault(fact.document_id, []).append(fact)
    flow_types = {
        RelationType.LENDS_TO.value,
        RelationType.CREDITOR_OF.value,
        RelationType.GUARANTEES_FOR.value,
        RelationType.TRANSACTS_WITH.value,
    }
    relations_by_doc: dict[str, list[Relation]] = {}
    for rel in relations:
        if rel.relation_type in flow_types and rel.amount_vnd and rel.amount_vnd > 0:
            relations_by_doc.setdefault(rel.document_id, []).append(rel)
    points: list[dict[str, Any]] = []
    for doc_id, doc in doc_by_id.items():
        equity = _latest_equity(facts_by_doc.get(doc_id, []))
        bucket = _denominator_exposure_buckets(
            relations_by_doc.get(doc_id, []), issuer_entity_id=issuer_entity_id
        )
        amount = max(
            bucket.get("loan_balance_vnd", 0.0) or bucket.get("loan_vnd", 0.0),
            bucket.get("receivable_vnd", 0.0),
            bucket.get("guarantee_exposure_vnd", 0.0),
        )
        if not equity or not amount:
            continue
        period = doc.period_end or doc.fiscal_year or ""
        points.append({
            "document_id": doc_id,
            "doc_role": doc.doc_role,
            "accounting_scope": "STANDALONE" if str(doc.doc_role or "") in {"ANNUAL_BACKBONE", "LATEST_QUARTER"} else "UNKNOWN",
            "metric_semantics": "largest_balance_exposure_to_equity",
            "measurement_type": "BALANCE",
            "period_end": str(period),
            "exposure_vnd": round(amount, 2),
            "equity_vnd": round(equity, 2),
            "exposure_to_equity": round(amount / equity, 6),
        })
    points.sort(key=lambda item: item["period_end"])
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for point in points:
        key = (point["accounting_scope"], point["metric_semantics"], point["measurement_type"])
        groups.setdefault(key, []).append(point)
    comparable = max(groups.values(), key=lambda group: (len(group), group[-1]["period_end"]), default=[])
    if len(comparable) < 2:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "score": None,
            "points": comparable,
            "candidate_points": points,
            "comparability": "SEMANTIC_BALANCE_COMPATIBLE" if len(points) >= 2 else "INSUFFICIENT_SEMANTIC_POINTS",
            "latest_ratio": comparable[-1]["exposure_to_equity"] if comparable else None,
            "previous_ratio": None,
            "delta": None,
        }
    points = comparable
    if len(points) < 2:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "score": None,
            "points": points,
            "latest_ratio": points[-1]["exposure_to_equity"] if points else None,
            "previous_ratio": None,
            "delta": None,
        }
    previous, latest = points[-2], points[-1]
    delta = round(latest["exposure_to_equity"] - previous["exposure_to_equity"], 6)
    score = round(min(100.0, max(0.0, delta / 0.25 * 100.0)), 2)
    return {
        "status": "DETERIORATING_OBSERVED" if delta > 0 else "IMPROVING_OBSERVED",
        "score": score,
        "observation_count": len(points),
        "trend_confidence": "LOW" if len(points) < 3 else "MEDIUM",
        "points": points,
        "latest_ratio": latest["exposure_to_equity"],
        "previous_ratio": previous["exposure_to_equity"],
        "delta": delta,
        "comparability": "SEMANTIC_BALANCE_COMPATIBLE",
        "comparison_mode": "BALANCE_COMPARISON",
    }


def _cycle_materiality_ratio(cycles: list[list[str]], relations: list[Relation], equity: float | None) -> float:
    """Return the largest quantified closed-flow value / same extracted equity."""
    if not cycles or not equity:
        return 0.0
    amounts: dict[tuple[str, str], float] = {}
    for rel in relations:
        if rel.relation_type in {
            RelationType.TRANSACTS_WITH.value, RelationType.GUARANTEES_FOR.value,
            RelationType.LENDS_TO.value, RelationType.CREDITOR_OF.value,
        }:
            key = (str(rel.subject), str(rel.object))
            amounts[key] = max(amounts.get(key, 0.0), float(rel.amount_vnd or 0.0))
    best = 0.0
    for path in cycles:
        total = sum(amounts.get((path[index], path[index + 1]), 0.0) for index in range(len(path) - 1))
        best = max(best, total / equity)
    return round(best, 6)


def _detect_cycles(relations: list[Relation]) -> list[list[str]]:
    # Ownership/investment is a structure edge, not evidence of round-tripping.
    # ``creditor_of`` is already stored as creditor -> debtor in the current
    # extraction contract, so it must not be reversed here.
    # A hard GIL trigger requires at least two distinct economic flow edges and
    # at least one quantified flow.
    flow_types = {
        RelationType.TRANSACTS_WITH.value,
        RelationType.GUARANTEES_FOR.value,
        RelationType.LENDS_TO.value,
        RelationType.CREDITOR_OF.value,
    }
    graph: dict[str, list[tuple[str, str, float]]] = {}
    seen_edges: set[tuple[str, str, str]] = set()
    for rel in relations:
        if rel.relation_type not in flow_types:
            continue
        edge_key = (str(rel.subject), str(rel.object), str(rel.relation_type))
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        graph.setdefault(rel.subject, []).append(
            (rel.object, rel.relation_type, float(rel.amount_vnd or 0.0))
        )
    cycles: list[list[str]] = []
    for start in graph:
        stack = [(start, [start], 0, 0, set())]
        while stack:
            current, path, flow_edges, quantified_flows, edge_types = stack.pop()
            for nxt, relation_type, amount in graph.get(current, []):
                next_flow_edges = flow_edges + int(relation_type in flow_types)
                next_quantified_flows = quantified_flows + int(
                    relation_type in flow_types and amount > 0
                )
                next_edge_types = edge_types | {relation_type}
                # A two-entity reciprocal relationship is commonly one
                # intercompany loan/transaction represented twice. It is not
                # sufficient proof of round-tripping; require >=3 entities.
                if nxt == start and len(path) > 2:
                    if next_flow_edges >= 3 and next_quantified_flows >= 2 and len(next_edge_types) >= 2:
                        cycles.append([*path, start])
                elif nxt not in path and len(path) < 8:
                    stack.append(
                        (
                            nxt,
                            [*path, nxt],
                            next_flow_edges,
                            next_quantified_flows, next_edge_types,
                        )
                    )
    unique = []
    seen = set()
    for cycle in cycles:
        key = tuple(cycle)
        if key not in seen:
            seen.add(key)
            unique.append(cycle)
    return unique


def _owned_entities(relations: list[Relation], issuer_ticker: str) -> set[str]:
    """Return direct and transitive controlled entities for RPT composition."""
    ownership_types = {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
    owned: set[str] = set()
    ownership_edges = [rel for rel in relations if rel.relation_type in ownership_types]
    subjects = {rel.subject for rel in ownership_edges}
    objects = {rel.object for rel in ownership_edges}
    # Extracted relations generally use the legal entity name rather than the
    # ticker. Roots are therefore inferred from the ownership graph itself.
    # The ticker remains a useful seed when the relation extractor did preserve it.
    frontier = {issuer_ticker} | (subjects - objects)
    while frontier:
        next_frontier: set[str] = set()
        for rel in ownership_edges:
            if rel.subject not in frontier:
                continue
            if rel.object not in owned and rel.object != issuer_ticker:
                owned.add(rel.object)
                next_frontier.add(rel.object)
        frontier = next_frontier
    return owned


def _owned_entity_ids(relations: list[Relation]) -> set[str]:
    """Return controlled entity IDs using the persisted relation endpoints.

    Entity IDs survive name variants across documents and prevent a subsidiary
    written as ``CTCP ...`` in one document from being treated as external just
    because the ownership table used ``CĂ´ng ty Cá»• pháº§n ...``.
    """
    ownership_types = {RelationType.OWNS.value, RelationType.INVESTS_IN.value}
    edges = [
        rel for rel in relations
        if rel.relation_type in ownership_types
        and rel.subject_entity_id
        and rel.object_entity_id
    ]
    if not edges:
        return set()
    subjects = {rel.subject_entity_id for rel in edges}
    objects = {rel.object_entity_id for rel in edges}
    frontier = subjects - objects
    owned: set[str] = set()
    while frontier:
        next_frontier: set[str] = set()
        for rel in edges:
            if rel.subject_entity_id not in frontier:
                continue
            if rel.object_entity_id not in owned:
                owned.add(rel.object_entity_id)
                next_frontier.add(rel.object_entity_id)
        frontier = next_frontier
    return owned
