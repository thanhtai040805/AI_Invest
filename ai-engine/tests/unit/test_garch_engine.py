"""Unit tests for GARCH Cash Engine — TASK-302."""

import pytest
import pandas as pd
import numpy as np
from app.domain.rules.market.garch_engine import GARCHCashEngine

@pytest.fixture
def engine():
    return GARCHCashEngine()

def test_forecast_volatility_stable(engine):
    """Test vol forecast with stable zero returns."""
    returns = pd.Series([0.0] * 50)
    # With omega=0.00001, alpha=0.1, beta=0.85
    # sigma^2 will converge towards steady state
    vol = engine.forecast_volatility(returns)
    assert vol > 0
    assert vol < 0.2 # Should be low (annualized)

def test_forecast_volatility_spike(engine):
    """Test vol forecast reacting to a return spike."""
    # Stable 1% returns then a 5% drop
    returns = pd.Series([0.01] * 40 + [-0.05])
    vol_spike = engine.forecast_volatility(returns)
    
    # Stable only
    returns_stable = pd.Series([0.01] * 41)
    vol_stable = engine.forecast_volatility(returns_stable)
    
    assert vol_spike > vol_stable

def test_calculate_cash_allocation(engine):
    """Test cash scaling logic."""
    # Low Vol
    assert engine.calculate_cash_allocation(0.10) == 0.10
    
    # Normal Vol (20%)
    # 0.1 + (0.2 - 0.15) * 2 = 0.1 + 0.05*2 = 0.2
    assert engine.calculate_cash_allocation(0.20) == pytest.approx(0.20)
    
    # High Vol (30%)
    # 0.3 + (0.3 - 0.25) * 3 = 0.3 + 0.05*3 = 0.45
    assert engine.calculate_cash_allocation(0.30) == pytest.approx(0.45)
