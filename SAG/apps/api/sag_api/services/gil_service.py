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
    gil_flag: str  # "PASS" | "WARNING" | "CATASTROPHIC"
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
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
        "GUARANTEES_FOR",
        "TRANSACTS_WITH",
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

            if source and target:
                self.graph.add_edge(
                    source,
                    target,
                    relation_type=rel_type,
                    amount_vnd=amount,
                    ownership_pct=ownership_pct,
                )

    def detect_capital_tunneling_cycles(self) -> list[list[str]]:
        """Phát hiện các chu trình khép kín (A -> B -> C -> A) luân chuyển dòng vốn hoặc sở hữu."""
        # Chỉ xét đồ thị con chứa các quan hệ dòng tiền hoặc sở hữu chéo
        sub_edges = [
            (u, v, d)
            for u, v, d in self.graph.edges(data=True)
            if d.get("relation_type") in self.FINANCIAL_FLOW_RELATIONS
            or d.get("relation_type") in self.OWNERSHIP_RELATIONS
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

            if rel_type in {"LOANS_TO", "GUARANTEES_FOR", "RECEIVABLE_FROM"} and amount > 0:
                total_exposure += amount
                exposure_details.append(f"{u} -> {rel_type} -> {v}: {amount:,.0f} VND")

        return total_exposure, exposure_details

    def evaluate(self) -> GILAnalysisResult:
        """Thực thi toàn bộ kiểm định toán học và gán nhãn gil_flag."""
        cycles = self.detect_capital_tunneling_cycles()
        total_rpt, details = self.calculate_rpt_exposure()

        rpt_ratio = (total_rpt / self.equity_vnd) if self.equity_vnd > 0 else 0.0
        reasons: list[str] = []

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
