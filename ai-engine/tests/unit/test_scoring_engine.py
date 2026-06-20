"""Unit tests for Phase 2: TASK-214 and TASK-215."""

import pytest
import pandas as pd
from datetime import date
from unittest.mock import MagicMock, patch
from app.domain.services.sentiment import SentimentFactorEngine
from app.domain.rules.scoring import CSSScoringEngine
from app.domain.rules.market.hmm_classifier import MarketRegime

@pytest.fixture
def sentiment_engine():
    return SentimentFactorEngine()

@pytest.fixture
def scoring_engine():
    return CSSScoringEngine()

@patch('psycopg2.connect')
def test_sentiment_factor_f4_calculation(mock_connect, sentiment_engine):
    """Test F4 Sentiment calculation (Foreign Flow)."""
    mock_conn = MagicMock()
    mock_connect.return_value = mock_conn
    
    # 10 days of flow for 1 ticker
    df_mock = pd.DataFrame({
        'symbol': ['VHM'] * 10,
        'net_value': [100.0] * 10,
        'is_etf_rebalance_day': [False] * 10
    })
    
    with patch('pandas.read_sql', return_value=df_mock):
        result = sentiment_engine.calculate_f4_scores(date(2026, 6, 15))
        assert len(result) == 1
        assert result.iloc[0]['foreign_flow_5d'] == 500.0

def test_css_scoring_regime_weighting(scoring_engine):
    """Test that CSS weights change correctly based on Market Regime."""
    # Mock factor scores: Momentum=90, Value=20
    data = {'symbol': ['ABC'], 'f1_value': [20.0], 'f3_momentum': [90.0]}
    df = pd.DataFrame(data)
    
    # In BULL_TRENDING: Momentum weight (0.4) > Value weight (0.1) -> High CSS
    df_bull = scoring_engine.calculate_css(df.copy(), MarketRegime.BULL_TRENDING)
    css_bull = df_bull.iloc[0]['css']
    
    # In BEAR_TRENDING: Value weight (0.4) > Momentum weight (0.1) -> Lower CSS
    # Plus: Bear Trending reduces total CSS by 50%
    df_bear = scoring_engine.calculate_css(df.copy(), MarketRegime.BEAR_TRENDING)
    css_bear = df_bear.iloc[0]['css']
    
    assert css_bull > css_bear

def test_conviction_level_assignment(scoring_engine):
    """Test Conviction Level classification."""
    assert scoring_engine._get_conviction(90.0) == "A+"
    assert scoring_engine._get_conviction(78.0) == "A"
    assert scoring_engine._get_conviction(30.0) == "D"
