"""Unit tests for OHLCV Ingestion Service — TASK-103."""

import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from app.infrastructure.data_pipelines.ohlcv_ingestion_service import OHLCVIngestionService

@pytest.fixture
def ingestion_svc():
    return OHLCVIngestionService()

def test_fetch_from_dnse_success(ingestion_svc):
    """Test DNSE primary source success."""
    mock_client = MagicMock()
    mock_client.get_ohlc.return_value = (200, {
        "t": [1781481600], # 2026-06-15
        "o": [100.0],
        "h": [105.0],
        "l": [95.0],
        "c": [102.0],
        "v": [1000000]
    })
    
    with patch('app.infrastructure.data_pipelines.ohlcv_ingestion_service.DNSEClient', return_value=mock_client):
        # Trigger property access to use the mock
        _ = ingestion_svc.dnse_client
        data = ingestion_svc._fetch_from_dnse("VHM", date(2026, 6, 15), date(2026, 6, 15))
        
        assert len(data) == 1
        assert data[0]["ticker"] == "VHM"
        assert data[0]["close"] == 102.0
        assert data[0]["data_source"] == "dnse"

# test_fetch_ohlcv_fallback_to_yfinance removed due to strict DNSE-only mandate

def test_fetch_intraday_for_volume_split(ingestion_svc):
    """Test volume separation logic from intraday data."""
    mock_tool = MagicMock()
    # Timestamps in UTC. VN is UTC+7.
    # 02:15 UTC = 09:15 VN
    # 07:30 UTC = 14:30 VN
    # 07:45 UTC = 14:45 VN
    mock_tool.fetch.return_value = [
        {"time": "2026-06-15T02:15:00Z", "volume": 100},  # ATO (09:15 VN)
        {"time": "2026-06-15T03:00:00Z", "volume": 500},  # Continuous (10:00 VN)
        {"time": "2026-06-15T07:30:00Z", "volume": 400},  # Continuous (14:30 VN)
        {"time": "2026-06-15T07:45:00Z", "volume": 200},  # ATC (14:45 VN)
    ]
    
    with patch('app.infrastructure.data_pipelines.ohlcv_ingestion_service.get_intraday_tool', return_value=mock_tool):
        split = ingestion_svc.fetch_intraday_for_volume_split("VHM", date(2026, 6, 15))
        
        assert split["ato"] == 100
        assert split["atc"] == 200
        assert split["continuous"] == 900 # 500 + 400

@patch('psycopg2.connect')
def test_calculate_adtv20_continuous(mock_connect, ingestion_svc):
    """Test ADTV20 calculation from continuous volume."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    mock_cur.fetchone.return_value = [1500000.0] # Average
    
    adtv = ingestion_svc.calculate_adtv20_continuous("VHM", date(2026, 6, 15))
    
    assert adtv == 1500000.0
    # Verify SQL query uses volume_continuous
    args, _ = mock_cur.execute.call_args_list[0]
    assert "volume_continuous" in args[0]
    assert "LIMIT 20" in args[0]
