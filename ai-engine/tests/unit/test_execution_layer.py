"""Unit tests for Phase 4 Execution Layer (TASK-401, TASK-402)."""

import pytest
from datetime import datetime
from app.domain.rules.execution.eae import ExecutionAdaptationEngine
from app.domain.rules.execution.hedge_controller import VNHedgeController
from app.domain.rules.market.hmm_classifier import MarketRegime

@pytest.fixture
def eae():
    return ExecutionAdaptationEngine()

@pytest.fixture
def hedge():
    return VNHedgeController()

def test_eae_slicing_emergency(eae):
    """Test that EMERGENCY orders are NOT sliced."""
    slices = eae.slice_order("VHM", "SELL", 10000, 1000000, urgency="EMERGENCY")
    assert len(slices) == 1
    assert slices[0].quantity == 10000
    assert slices[0].price_type == "MP"

def test_eae_slicing_normal(eae):
    """Test that large orders are sliced appropriately."""
    # ADTV = 100,000. Slice limit = 5% = 5,000.
    # Total = 12,000. Should result in 3 slices (5k, 5k, 2k).
    slices = eae.slice_order("VHM", "BUY", 12000, 100000, urgency="NORMAL")
    assert len(slices) == 3
    assert slices[0].quantity == 5000
    assert slices[2].quantity == 2000

def test_hedge_trigger_on_breadth(hedge):
    """Test hedge trigger when market breadth is very low."""
    res = hedge.calculate_hedge_requirement(
        portfolio_value=1_000_000_000,
        vn30_index=1200,
        hmm_bear_prob=0.3,
        market_breadth=10.0, # < 15%
        regime=MarketRegime.BULL_CHOPPY
    )
    assert res["cdc_status"] == "ACTIVE"
    # 1B / (1200 * 100k) = 1B / 120M = 8.33 -> 8 contracts
    assert res["short_contracts"] == 8

def test_hedge_no_trigger_on_bull(hedge):
    """Test no hedge in healthy market."""
    res = hedge.calculate_hedge_requirement(
        portfolio_value=1_000_000_000,
        vn30_index=1200,
        hmm_bear_prob=0.1,
        market_breadth=60.0,
        regime=MarketRegime.BULL_TRENDING
    )
    assert res["cdc_status"] == "INACTIVE"
    assert res["short_contracts"] == 0
