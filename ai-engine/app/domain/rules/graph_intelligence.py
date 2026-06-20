"""Graph Intelligence Layer (GIL) — TASK-203

Xây dựng đồ thị sở hữu và phát hiện cấu trúc rủi ro hệ thống.
Hỗ trợ:
1. Cycle Detection (Phát hiện vòng lặp sở hữu/giao dịch).
2. OCR Score (Ownership Concentration Ratio).
3. Catastrophic Flag (Rủi ro hệ thống cực cao).
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any, Optional, Set

logger = logging.getLogger(__name__)

class EntityType(Enum):
    COMPANY = "COMPANY"
    PERSON = "PERSON"
    LEGAL_ENTITY = "LEGAL_ENTITY"

class RelType(Enum):
    OWNS = "OWNS"
    TRANSACTION = "TRANSACTION"
    GUARANTEES = "GUARANTEES"
    TRANSFER = "TRANSFER"

@dataclass(frozen=True, eq=True)
class Node:
    id: str
    type: EntityType
    name: str

@dataclass
class Edge:
    source: str
    target: str
    type: RelType
    value: float = 0.0  # Ownership % hoặc giá trị giao dịch (VND)
    metadata: Dict[str, Any] = field(default_factory=dict)

class GraphIntelligenceLayer:
    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._adj: Dict[str, List[Edge]] = {}

    def add_node(self, node: Node):
        self.nodes[node.id] = node
        if node.id not in self._adj:
            self._adj[node.id] = []

    def add_edge(self, edge: Edge):
        if edge.source not in self.nodes or edge.target not in self.nodes:
            logger.error(f"Missing node for edge {edge.source} -> {edge.target}")
            return
        self.edges.append(edge)
        self._adj[edge.source].append(edge)

    def find_cycles(self) -> List[List[str]]:
        """Phát hiện các vòng lặp trong đồ thị bằng thuật toán DFS."""
        visited = set()
        stack = []
        cycles = []

        def dfs(u, path_nodes):
            visited.add(u)
            stack.append(u)
            
            for edge in self._adj.get(u, []):
                v = edge.target
                if v in stack:
                    # Tìm thấy vòng lặp
                    cycle_start_idx = stack.index(v)
                    cycles.append(stack[cycle_start_idx:].copy())
                elif v not in visited:
                    dfs(v, path_nodes)
            
            stack.pop()

        for node_id in self.nodes:
            if node_id not in visited:
                dfs(node_id, [])
        
        return cycles

    def calculate_ocr(self, ticker: str) -> float:
        """Tính Ownership Concentration Ratio (OCR).
        OCR = sum ownership pct của các entities liên kết về cùng 1 controller.
        """
        # Đơn giản hóa: OCR = Tổng % sở hữu của Top 5 cổ đông lớn nhất
        # AC yêu cầu: sum ownership của entities liên kết.
        # TODO: Implement BFS để tìm tất cả entities có chung "ultimate controller"
        relevant_edges = [e for e in self.edges if e.target == ticker and e.type == RelType.OWNS]
        return sum(e.value for e in relevant_edges)

    def check_catastrophic_risk(self, ticker: str, revenue: float, current_assets: float) -> bool:
        """Kiểm tra rủi ro Catastrophic.
        Trigger khi: cycle value > 15% revenue HOẶC > 15% current assets.
        """
        cycles = self.find_cycles()
        
        # Lọc các cycle có liên quan đến ticker này
        relevant_cycles = [c for c in cycles if ticker in c]
        
        for cycle in relevant_cycles:
            cycle_value = self._get_cycle_transaction_value(cycle)
            
            if revenue > 0 and cycle_value > 0.15 * revenue:
                logger.warning(f"CATASTROPHIC RISK: Cycle value {cycle_value:,.0f} > 15% Revenue for {ticker}")
                return True
                
            if current_assets > 0 and cycle_value > 0.15 * current_assets:
                logger.warning(f"CATASTROPHIC RISK: Cycle value {cycle_value:,.0f} > 15% Assets for {ticker}")
                return True
                
        return False

    def _get_cycle_transaction_value(self, cycle: List[str]) -> float:
        """Tính tổng giá trị giao dịch trong một vòng lặp."""
        total_value = 0.0
        for i in range(len(cycle)):
            u = cycle[i]
            v = cycle[(i + 1) % len(cycle)]
            # Tìm edge TRANSACTION giữa u và v
            edges = [e for e in self._adj.get(u, []) if e.target == v and e.type == RelType.TRANSACTION]
            if edges:
                total_value += sum(e.value for e in edges)
        return total_value

gil_layer = GraphIntelligenceLayer()
