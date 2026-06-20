"""Unit tests for Graph Intelligence Layer (GIL) — TASK-203."""

import pytest
from app.domain.rules.graph_intelligence import (
    GraphIntelligenceLayer, Node, Edge, EntityType, RelType
)

@pytest.fixture
def gil():
    return GraphIntelligenceLayer()

def test_add_node_and_edge(gil):
    """Test standard node and edge insertion."""
    n1 = Node("VHM", EntityType.COMPANY, "Vinhomes")
    n2 = Node("VIC", EntityType.COMPANY, "Vingroup")
    
    gil.add_node(n1)
    gil.add_node(n2)
    
    # VIC owns 66% of VHM
    e1 = Edge("VIC", "VHM", RelType.OWNS, value=66.0)
    gil.add_edge(e1)
    
    assert len(gil.nodes) == 2
    assert len(gil.edges) == 1
    assert gil.edges[0].source == "VIC"

def test_cycle_detection(gil):
    """Test detection of transaction cycles."""
    # Build cycle: A -> B -> C -> A
    nodes = [
        Node("A", EntityType.COMPANY, "Co A"),
        Node("B", EntityType.COMPANY, "Co B"),
        Node("C", EntityType.COMPANY, "Co C"),
    ]
    for n in nodes: gil.add_node(n)
    
    gil.add_edge(Edge("A", "B", RelType.TRANSACTION, value=100))
    gil.add_edge(Edge("B", "C", RelType.TRANSACTION, value=100))
    gil.add_edge(Edge("C", "A", RelType.TRANSACTION, value=100))
    
    cycles = gil.find_cycles()
    
    assert len(cycles) >= 1
    assert "A" in cycles[0]
    assert "B" in cycles[0]
    assert "C" in cycles[0]

def test_catastrophic_risk_trigger(gil):
    """Test CATASTROPHIC flag when cycle value > 15% revenue."""
    n1 = Node("TICKER", EntityType.COMPANY, "Main Co")
    n2 = Node("SHELL", EntityType.COMPANY, "Shell Co")
    gil.add_node(n1)
    gil.add_node(n2)
    
    # Transaction cycle value = 200B
    gil.add_edge(Edge("TICKER", "SHELL", RelType.TRANSACTION, value=200_000_000_000))
    gil.add_edge(Edge("SHELL", "TICKER", RelType.TRANSACTION, value=200_000_000_000))
    
    # Revenue = 1000B. 15% = 150B. Cycle 200B > 150B -> CATASTROPHIC
    is_risky = gil.check_catastrophic_risk("TICKER", revenue=1_000_000_000_000, current_assets=5_000_000_000_000)
    assert is_risky is True
    
    # Revenue = 5000B. 15% = 750B. Cycle 200B < 750B -> NOT CATASTROPHIC
    is_risky_low = gil.check_catastrophic_risk("TICKER", revenue=5_000_000_000_000, current_assets=10_000_000_000_000)
    assert is_risky_low is False

def test_ocr_calculation(gil):
    """Test simple OCR calculation."""
    gil.add_node(Node("VHM", EntityType.COMPANY, "VHM"))
    gil.add_node(Node("S1", EntityType.COMPANY, "Shareholder 1"))
    gil.add_node(Node("S2", EntityType.PERSON, "Shareholder 2"))
    
    gil.add_edge(Edge("S1", "VHM", RelType.OWNS, value=40.0))
    gil.add_edge(Edge("S2", "VHM", RelType.OWNS, value=10.0))
    
    ocr = gil.calculate_ocr("VHM")
    assert ocr == 50.0
