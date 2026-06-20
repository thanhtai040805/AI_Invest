"""Unit tests for Hard Law Enforcement Engine — TASK-111."""

import pytest
from app.domain.rules.hard_laws import (
    HardLawEngine, ProposedOrder, PortfolioState, HardLaw
)

@pytest.fixture
def engine():
    return HardLawEngine()

@pytest.fixture
def portfolio():
    return PortfolioState(
        nav=1_000_000_000, # 1 tỷ VND
        positions={
            "VHM": {"quantity": 5000, "current_price": 40000, "sector": "Bất động sản"}
        },
        sector_exposure={
            "Bất động sản": 200_000_000 # 20% NAV
        }
    )

def test_check_dieu_1_violation(engine, portfolio):
    """Test Điều 1: Position loss > 2% NAV."""
    # Loss = (50 - 45) * 5000 = 25,000,000. 2% NAV = 20,000,000.
    order = ProposedOrder("VIC", "BUY", 5000, 50000, stop_loss_price=45000)
    
    check = engine.check_order(order, portfolio, adtv20_continuous=1_000_000)
    
    assert check.passed is False
    assert check.violated_law == HardLaw.DIEU_1
    assert "vượt 2% NAV" in check.reason

def test_check_dieu_2_violation(engine, portfolio):
    """Test Điều 2: Exit > 5 sessions (Total quantity > ADTV20)."""
    # Total VHM = 5000 (existing) + 6000 (new) = 11000. ADTV20 = 10000.
    # Price = 40,000, SL = 39,000 -> Loss = 1000 * 6000 = 6,000,000 (0.6% NAV) - PASS DIEU 1
    order = ProposedOrder("VHM", "BUY", 6000, 40000, stop_loss_price=39000, sector="Bất động sản")
    
    check = engine.check_order(order, portfolio, adtv20_continuous=10000)
    
    assert check.passed is False
    assert check.violated_law == HardLaw.DIEU_2

def test_check_dieu_4_single_stock_violation(engine, portfolio):
    """Test Điều 4: Single stock > 15% NAV."""
    # VHM existing value = 200,000,000. 
    # New order = 40,000 * 2,000 = 80,000,000.
    # Total = 280,000,000 (28% NAV). Limit = 15%.
    # Price = 40,000, SL = 35,000 -> Loss = 5000 * 2000 = 10,000,000 (1% NAV) - PASS DIEU 1
    order = ProposedOrder("VHM", "BUY", 2000, 40000, stop_loss_price=35000, sector="Bất động sản")
    
    check = engine.check_order(order, portfolio, adtv20_continuous=1_000_000)
    
    assert check.passed is False
    assert check.violated_law == HardLaw.DIEU_4
    assert "vượt 15% NAV" in check.reason

def test_check_dieu_4_sector_violation(engine):
    """Test Điều 4: Sector > 35% NAV."""
    # Portfolio already has 30% in Banking
    portfolio = PortfolioState(
        nav=1_000_000_000,
        positions={
            "VCB": {"quantity": 3000, "current_price": 100000, "sector": "Ngân hàng"}
        },
        sector_exposure={
            "Ngân hàng": 300_000_000 # 30% NAV
        }
    )
    # New order in Banking: 10% NAV.
    # Total Banking = 40%. Individual stock = 10% (passes 15% limit).
    order = ProposedOrder("CTG", "BUY", 2000, 50000, stop_loss_price=49000, sector="Ngân hàng")
    
    check = engine.check_order(order, portfolio, adtv20_continuous=1_000_000)
    
    assert check.passed is False
    assert check.violated_law == HardLaw.DIEU_4
    assert "vượt 35% NAV" in check.reason

def test_check_all_pass(engine, portfolio):
    """Test a valid order that passes all laws."""
    order = ProposedOrder("VIC", "BUY", 1000, 50000, stop_loss_price=49000, sector="Bất động sản")
    # Loss = 1000 * 1000 = 1,000,000 (0.1% NAV)
    # Value = 50,000,000 (5% NAV)
    # Total BĐS = 250,000,000 (25% NAV)
    
    check = engine.check_order(order, portfolio, adtv20_continuous=1_000_000)
    assert check.passed is True
