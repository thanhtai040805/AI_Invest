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
                self.graph.add_edge(
                    source,
                    target,
                    relation_type=rel_type,
                    amount_vnd=amount,
                    ownership_pct=ownership_pct,
                    verified=verified,
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
                        description=desc or f"Sở hữu {pct_val}%",
                    )
                    continue

                # B. Quan hệ tài chính: Vay nợ, bảo lãnh, phải thu/phải trả
                amt_match = re.search(r"(\d+(?:[.,]\d+)*)\s*(tỷ|triệu|nghìn|đồng|vnd)", desc, re.IGNORECASE)
                amount_vnd = 0.0
                if amt_match:
                    num_str = amt_match.group(1).replace(".", "").replace(",", ".")
                    unit = amt_match.group(2).lower()
                    try:
                        num = float(num_str)
                        if "tỷ" in unit:
                            amount_vnd = num * 1_000_000_000
                        elif "triệu" in unit:
                            amount_vnd = num * 1_000_000
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
                        description=desc,
                    )
                elif "vay" in desc_lower or "ngân hàng" in desc_lower or (p_type == "COMPANY" and ev_category == "DEBT_RESTRUCTURING"):
                    self.graph.add_edge(
                        event_subject,
                        p_ent["norm_name"],
                        relation_type="BORROWS_FROM",
                        amount_vnd=amount_vnd,
                        ownership_pct=0.0,
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
            # Chu trình 2 đỉnh phản ánh sở hữu 2 chiều hoặc quan hệ vay - trả
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

        # Ma trận phán quyết cờ gil_flag (IOS v5.1 Hard Laws)
        if len(cycles) > 0:
            gil_flag = "CATASTROPHIC"
            risk_level = "CRITICAL"
            reasons.append(
                f"Phát hiện {len(cycles)} chu trình khép kín sở hữu/dòng vốn nghi vấn rút ruột: {cycles[:3]}"
            )
        elif rpt_ratio > 0.50:
            gil_flag = "CATASTROPHIC"
            risk_level = "CRITICAL"
            reasons.append(
                f"RPT Exposure Ratio vượt ngưỡng nguy hiểm ({rpt_ratio * 100:.1f}% > 50% vốn chủ sở hữu: {total_rpt:,.0f} VND / {self.equity_vnd:,.0f} VND)"
            )
        elif rpt_ratio > 0.25:
            gil_flag = "WARNING"
            risk_level = "HIGH"
            reasons.append(
                f"Tỷ lệ phơi nhiễm nợ vay/bảo lãnh bên liên quan cần thận trọng ({rpt_ratio * 100:.1f}% VCSH)"
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
        )
