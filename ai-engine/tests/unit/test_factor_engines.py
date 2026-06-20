"""Unit tests for Factor Engines (F1, F2, F3)."""

import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from app.domain.services.base import FactorEngineBase
from app.domain.services.value import ValueFactorEngine
from app.domain.services.quality import QualityFactorEngine
from app.domain.services.momentum import MomentumFactorEngine

@pytest.fixture
def base_engine():
    return FactorEngineBase()

def test_normalize_percentile(base_engine):
    """Test percentile normalization logic."""
    series = pd.Series([10, 20, 30, 40, 50])
    # Rank: 10->20%, 20->40%, 30->60%, 40->80%, 50->100%
    normalized = base_engine.normalize_percentile(series)
    assert normalized.iloc[0] == 20.0
    assert normalized.iloc[4] == 100.0
    
    # Inverted: 10->80%, 20->60%, 30->40%, 40->20%, 50->0%
    # Wait, rank 1 is lowest. rank(pct=True) for 10 is 0.2. 100 - 20 = 80.
    inverted = base_engine.normalize_percentile(series, invert=True)
    assert inverted.iloc[0] == 80.0
    assert inverted.iloc[4] == 0.0

@patch('psycopg2.connect')
def test_value_factor_calculation(mock_connect):
    """Test F1 Value Factor calculation and normalization."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    
    # Mock SQL data for 3 stocks
    data = {
        'symbol': ['AAA', 'BBB', 'CCC'],
        'pe': [10.0, 20.0, 5.0],  # CCC is cheapest (PE=5)
        'pb': [1.0, 2.0, 0.5],    # CCC is cheapest (PB=0.5)
        'universe_group': ['A', 'A', 'A']
    }
    df_mock = pd.DataFrame(data)
    
    engine = ValueFactorEngine()
    with patch('pandas.read_sql', return_value=df_mock):
        with patch.object(engine, '_save_scores'):
            result = engine.calculate_f1_scores(date(2026, 6, 15))
            
            # CCC should have highest score because it has lowest PE and PB
            # CCC PE rank: 1/3 = 33.3%. Inverted = 66.6%.
            # CCC is lowest PE, rank is 1. pct rank is 1/3. 
            # In pandas, rank(pct=True) for [10, 20, 5] gives: 5->0.33, 10->0.66, 20->1.0
            # Inverted: 5->0.66, 10->0.33, 20->0.0
            ccc_score = result[result['symbol'] == 'CCC']['f1_value_score'].values[0]
            bbb_score = result[result['symbol'] == 'BBB']['f1_value_score'].values[0]
            assert ccc_score > bbb_score

@patch('psycopg2.connect')
def test_momentum_factor_calculation(mock_connect):
    """Test F3 Momentum calculation (Price returns)."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    
    # Create mock price history for 250+ days with distinct dates
    dates = [date(2026, 6, 15) - timedelta(days=i) for i in range(300)]
    prices = [100.0] * 300
    prices[0] = 110.0 # Latest price (today)
    prices[20] = 100.0 # 20 days ago
    prices[250] = 50.0 # 250 days ago
    
    df_mock = pd.DataFrame({
        'symbol': ['FPT'] * 300,
        'date': dates,
        'close_adj': prices
    })
    
    engine = MomentumFactorEngine()
    with patch('pandas.read_sql', return_value=df_mock):
        with patch.object(engine, '_save_scores'):
            result = engine.calculate_f3_scores(date(2026, 6, 15))
            
            # 1m return = 110/100 - 1 = 10%
            # 12m return = 110/50 - 1 = 120%
            fpt = result[result['symbol'] == 'FPT']
            assert fpt['mom_1m'].values[0] == pytest.approx(0.1)
            assert fpt['mom_12m'].values[0] == pytest.approx(1.2)
