"""Unit tests for Beneish M-Score Engine — TASK-202."""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from app.domain.rules.beneish import BeneishMScoreEngine

@pytest.fixture
def engine():
    return BeneishMScoreEngine()

@patch('psycopg2.connect')
def test_calculate_m_score_pending_on_missing_data(mock_connect, engine):
    """Test status = PENDING when less than 2 yearly reports available."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    # Only 1 report
    mock_cur.fetchall.return_value = [{"symbol": "ABC", "data": {}}]
    
    result = engine.calculate_m_score("ABC", date(2026, 6, 15))
    assert result["status"] == "PENDING"
    assert "Thiếu BCTC" in result["reason"]

@patch('psycopg2.connect')
def test_calculate_m_score_pass_scenario(mock_connect, engine):
    """Test a scenario where M-Score should PASS (low risk)."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    # Mock stable data for 2 years
    fs_data = {
        "revenue": 1000, "cogs": 600, "receivables": 100, 
        "current_assets": 500, "ppe_net": 1000, "total_assets": 2000,
        "depreciation": 100, "sga_expense": 150, "net_income": 100,
        "cfo": 110, "long_term_debt": 400, "current_liabilities": 200
    }
    
    mock_cur.fetchall.return_value = [
        {"symbol": "GOOD", "data": fs_data},
        {"symbol": "GOOD", "data": fs_data} # Same data -> indices = 1.0
    ]
    
    result = engine.calculate_m_score("GOOD", date(2026, 6, 15))
    
    assert result["status"] == "PASS"
    assert result["m_score"] < -1.78

@patch('psycopg2.connect')
def test_calculate_m_score_fail_scenario(mock_connect, engine):
    """Test a scenario with aggressive revenue recognition (High DSRI) causing FAIL."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    # Year T: Receivables jump significantly relative to revenue
    fs_t = {
        "revenue": 1000, "cogs": 600, "receivables": 500, # High receivables
        "current_assets": 800, "ppe_net": 1000, "total_assets": 2500,
        "depreciation": 100, "sga_expense": 150, "net_income": 200,
        "cfo": 10, # Low cash flow relative to income
        "long_term_debt": 400, "current_liabilities": 200
    }
    
    fs_t1 = {
        "revenue": 1000, "cogs": 600, "receivables": 100,
        "current_assets": 500, "ppe_net": 1000, "total_assets": 2000,
        "depreciation": 100, "sga_expense": 150, "net_income": 100,
        "cfo": 110, "long_term_debt": 400, "current_liabilities": 200
    }
    
    mock_cur.fetchall.return_value = [
        {"symbol": "BAD", "data": fs_t},
        {"symbol": "BAD", "data": fs_t1}
    ]
    
    result = engine.calculate_m_score("BAD", date(2026, 6, 15))
    
    # DSRI will be (500/1000) / (100/1000) = 5.0 (Very high)
    # TATA will be (200 - 10) / 2500 = 0.076
    assert result["status"] == "FAIL"
    assert result["m_score"] > -1.78
