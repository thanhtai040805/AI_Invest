"""Unit tests for Universe Manager — TASK-201."""

import pytest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from app.domain.rules.universe_manager import UniverseManager, UniverseGroup

@pytest.fixture
def manager():
    return UniverseManager()

@patch('psycopg2.connect')
def test_classify_vn30_as_group_a(mock_connect, manager):
    """Test that VN30 members are correctly classified as Group A."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    # Mock stock data
    mock_cur.fetchone.return_value = {
        "symbol": "FPT",
        "trading_status": "NORMAL",
        "adtv20_continuous": 100_000_000_000,
        "market_mcap": 100_000_000_000_000,
        "beneish_status": "PASS",
        "gil_flag": "PASS"
    }
    
    with patch.object(manager, '_get_vn30_list', return_value=["FPT"]):
        result = manager.classify_universe(["FPT"], date(2026, 6, 15))
        
        assert result["results"][0]["universe_group"] == "A"
        assert len(result["exclusion_log"]) == 0

@patch('psycopg2.connect')
def test_exclude_on_bad_trading_status(mock_connect, manager):
    """Test that stocks with non-NORMAL status are EXCLUDED."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    mock_cur.fetchone.return_value = {
        "symbol": "ABC",
        "trading_status": "WARNING",
        "adtv20_continuous": 5_000_000_000,
        "market_mcap": 500_000_000_000
    }
    
    result = manager.classify_universe(["ABC"], date(2026, 6, 15))
    
    assert result["results"][0]["universe_group"] == "EXCLUDED"
    assert result["exclusion_log"][0]["ticker"] == "ABC"
    assert "WARNING" in result["exclusion_log"][0]["reason"]

@patch('psycopg2.connect')
def test_sandbox_classification(mock_connect, manager):
    """Test Sandbox group criteria (4 conditions)."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    # Main data
    mock_cur.fetchone.return_value = {
        "symbol": "GROW",
        "trading_status": "NORMAL",
        "adtv20_continuous": 3_000_000_000, # >= 2B
        "market_mcap": 400_000_000_000     # >= 300B
    }
    
    # Financial ratios for growth and debt
    mock_cur.fetchall.return_value = [
        {"yoy_revenue_growth": 0.30, "debt_equity": 0.10},
        {"yoy_revenue_growth": 0.35, "debt_equity": 0.12},
        {"yoy_revenue_growth": 0.28, "debt_equity": 0.11}
    ]
    
    result = manager.classify_universe(["GROW"], date(2026, 6, 15))
    
    assert result["results"][0]["universe_group"] == "SANDBOX"
