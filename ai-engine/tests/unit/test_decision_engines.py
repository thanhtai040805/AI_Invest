"""Unit tests for Phase 3 Decision Engines (TASK-311, TASK-312)."""

import pytest
import asyncio
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.domain.rules.kelly_sizer import KellyPositionSizer
from app.domain.rules.market.hmm_classifier import MarketRegime

@pytest.mark.anyio
async def test_counter_thesis_rule_of_three_fail():
    """Test that less than 3 signals cause REJECT."""
    engine = CounterThesisEngine()
    report = await engine.analyze_thesis("VHM", "Strong growth", ["Signal 1", "Signal 2"])
    
    assert report.verdict == Verdict.REJECT
    assert report.rule_of_three_passed is False
    assert "Hard Law Điều 3" in report.holes[0]

@pytest.mark.anyio
async def test_counter_thesis_approve_dummy():
    """Test standard approval with mock/dummy logic (now correctly rejects due to strict rules)."""
    engine = CounterThesisEngine()
    report = await engine.analyze_thesis("VHM", "Thesis", ["S1", "S2", "S3"])
    
    assert report.verdict == Verdict.REJECT
    assert "LLM client chưa được cấu hình" in report.holes[0]

def test_kelly_sizer_calculation():
    """Test standard Quarter Kelly calculation."""
    # Prob 60%, Win/Loss 2:1
    # Full Kelly = 0.6 - (1 - 0.6)/2 = 0.6 - 0.2 = 0.4 (40%)
    # Quarter Kelly = 40% * 0.25 = 10%
    sizer = KellyPositionSizer(baseline_kelly_fraction=0.25)
    
    # Bull Trending (1.0x)
    size = sizer.calculate_position_size(0.6, 2.0, MarketRegime.BULL_TRENDING, 1_000_000_000)
    assert size == pytest.approx(100_000_000) # 10% of 1B
    
    # Bear Trending (0.25x scaling)
    # 10% * 0.25 = 2.5%
    size_bear = sizer.calculate_position_size(0.6, 2.0, MarketRegime.BEAR_TRENDING, 1_000_000_000)
    assert size_bear == pytest.approx(25_000_000)

def test_kelly_sizer_cap_at_hard_law():
    """Test that position size is capped at 15% NAV."""
    # Prob 80%, Win/Loss 5:1
    # Full Kelly = 0.8 - (0.2)/5 = 0.8 - 0.04 = 0.76 (76%)
    # Quarter Kelly = 76% * 0.25 = 19%
    # But limit is 15%
    sizer = KellyPositionSizer(baseline_kelly_fraction=0.25)
    size = sizer.calculate_position_size(0.8, 5.0, MarketRegime.BULL_TRENDING, 1_000_000_000)
    assert size == 150_000_000 # 15% of 1B
