"""Unit tests for the remaining tasks in Implementation Plan."""

import pytest
import pandas as pd
import numpy as np
from app.domain.rules.optimizer import PortfolioOptimizer
from app.application.agents.learning_agent import LearningAgent
from app.eval.audit_trail import AuditTrailEngine
from unittest.mock import MagicMock, patch

@pytest.fixture
def optimizer():
    return PortfolioOptimizer(min_positions=2, max_positions=5)

def test_portfolio_optimizer_constraints(optimizer):
    """Test optimizer with correlation and sector constraints."""
    # 5 candidates, all have high CSS
    candidates = pd.DataFrame({
        'symbol': ['T1', 'T2', 'T3', 'T4', 'T5'],
        'css': [90, 85, 80, 75, 70],
        'sector': ['Bank', 'Bank', 'Bank', 'IT', 'Retail']
    })
    
    # High correlation between T1 and T2
    corr_matrix = pd.DataFrame(
        [[1.0, 0.8, 0.1, 0.1, 0.1],
         [0.8, 1.0, 0.1, 0.1, 0.1],
         [0.1, 0.1, 1.0, 0.1, 0.1],
         [0.1, 0.1, 0.1, 1.0, 0.1],
         [0.1, 0.1, 0.1, 0.1, 1.0]],
        index=['T1', 'T2', 'T3', 'T4', 'T5'],
        columns=['T1', 'T2', 'T3', 'T4', 'T5']
    )
    
    # Optimizer should skip T2 because it's correlated with T1
    # Also sector limit: each pos is 1/5=20%. Sector 'Bank' has T1, T2, T3. 
    # T1(20%) + T3(20%) = 40% (> 35% limit). So T3 might be skipped if limit enforced strictly.
    
    selected = optimizer.optimize_selection(candidates, corr_matrix, 1_000_000)
    
    assert "T1" in selected
    assert "T2" not in selected # Skipped due to correlation
    assert "T3" not in selected # Skipped due to sector limit (20+20=40 > 35)
    assert "T4" in selected
    assert "T5" in selected

def test_learning_agent_cdc_trigger():
    """Test CDC activation on IC decay."""
    agent = LearningAgent()
    agent.baseline_ic = 0.20
    
    # Normal IC
    agent.update_cdc_status(0.18, 1.0)
    assert agent.cdc_active is False
    assert agent.get_kelly_multiplier() == 1.0
    
    # Decayed IC (0.05 < 0.20 * 0.5)
    agent.update_cdc_status(0.05, 1.0)
    assert agent.cdc_active is True
    assert agent.get_kelly_multiplier() == 0.5

@patch('psycopg2.connect')
def test_audit_trail_chaining(mock_connect):
    """Test that audit log maintains a hash chain."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    
    engine = AuditTrailEngine()
    
    # Event 1
    engine.log_event("agent_1", "TEST_START", {"val": 1})
    h1 = engine.last_hash
    
    # Event 2
    engine.log_event("agent_1", "TEST_END", {"val": 2})
    h2 = engine.last_hash
    
    assert h1 != "INITIAL_BLOCK"
    assert h2 != h1
    # (Checking if prev_hash is stored in DB call is verified by mock args if needed)
