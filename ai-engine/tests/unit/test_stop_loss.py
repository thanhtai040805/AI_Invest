"""Unit tests for Stop-Loss Engine — TASK-113."""

import pytest
from app.domain.rules.stop_loss import StopLossEngine

@pytest.fixture
def engine():
    return StopLossEngine()

def test_stop_loss_triggered(engine):
    """Test stop-loss trigger at -2% NAV loss."""
    nav = 1_000_000_000 # 1 tỷ
    # Position: VHM, 5000 units, entry 50,000.
    # Price drops to 45,000.
    # Loss = (45000 - 50000) * 5000 = -25,000,000.
    # % NAV = -25M / 1B = -2.5% (Violates -2% limit).
    
    order = engine.check_position(
        ticker="VHM",
        quantity=5000,
        entry_price=50000,
        current_price=45000,
        nav=nav
    )
    
    assert order is not None
    assert order.ticker == "VHM"
    assert order.urgency == "EMERGENCY"
    assert "Điều 1" in order.reason

def test_stop_loss_not_triggered(engine):
    """Test stop-loss not triggered at -1.5% NAV loss."""
    nav = 1_000_000_000
    # Loss = (47000 - 50000) * 5000 = -15,000,000.
    # % NAV = -1.5%.
    
    order = engine.check_position(
        ticker="VHM",
        quantity=5000,
        entry_price=50000,
        current_price=47000,
        nav=nav
    )
    
    assert order is None

def test_profit_no_trigger(engine):
    """Test no trigger on profit."""
    nav = 1_000_000_000
    order = engine.check_position(
        ticker="VHM",
        quantity=5000,
        entry_price=50000,
        current_price=60000,
        nav=nav
    )
    assert order is None
