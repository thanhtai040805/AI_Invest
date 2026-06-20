"""Unit tests for HMM Regime Classifier — TASK-301."""

import pytest
from app.domain.rules.market.hmm_classifier import HMMRegimeClassifier, MarketRegime

@pytest.fixture
def classifier():
    return HMMRegimeClassifier()

def test_hysteresis_no_immediate_transition(classifier):
    """Test that regime does not change immediately if threshold met only once."""
    # Day 0: Bull Trending stable
    post_stable = {
        MarketRegime.BULL_TRENDING: 0.8,
        MarketRegime.BEAR_TRENDING: 0.2,
        MarketRegime.BULL_CHOPPY: 0.0,
        MarketRegime.BEAR_BOUNCE: 0.0
    }
    classifier.classify(post_stable)
    assert classifier.last_stable_state == MarketRegime.BULL_TRENDING
    
    # Day 1: Bear probability jumps (Diff > 15%)
    post_shift = {
        MarketRegime.BULL_TRENDING: 0.3,
        MarketRegime.BEAR_TRENDING: 0.7, # 0.7 - 0.3 = 0.4 > 0.15
        MarketRegime.BULL_CHOPPY: 0.0,
        MarketRegime.BEAR_BOUNCE: 0.0
    }
    state = classifier.classify(post_shift)
    
    # Should still be Bull Trending (pending session 1)
    assert state == MarketRegime.BULL_TRENDING
    assert classifier.pending_state == MarketRegime.BEAR_TRENDING
    assert classifier.pending_count == 1

def test_hysteresis_consecutive_transition(classifier):
    """Test that regime changes after 3 consecutive sessions of threshold violation."""
    # Setup: Stable Bull
    post_bull = {MarketRegime.BULL_TRENDING: 0.8, MarketRegime.BEAR_TRENDING: 0.2}
    classifier.classify(post_bull)
    
    # Day 1, 2, 3: Bear dominant
    post_bear = {MarketRegime.BULL_TRENDING: 0.3, MarketRegime.BEAR_TRENDING: 0.7}
    
    classifier.classify(post_bear) # Day 1
    assert classifier.last_stable_state == MarketRegime.BULL_TRENDING
    
    classifier.classify(post_bear) # Day 2
    assert classifier.last_stable_state == MarketRegime.BULL_TRENDING
    
    state = classifier.classify(post_bear) # Day 3
    assert state == MarketRegime.BEAR_TRENDING
    assert classifier.last_stable_state == MarketRegime.BEAR_TRENDING

def test_hysteresis_reset_on_reversion(classifier):
    """Test that pending count resets if condition not met on Day 2."""
    post_bull = {MarketRegime.BULL_TRENDING: 0.8, MarketRegime.BEAR_TRENDING: 0.2}
    classifier.classify(post_bull)
    
    # Day 1: Bear jump
    post_bear = {MarketRegime.BULL_TRENDING: 0.3, MarketRegime.BEAR_TRENDING: 0.7}
    classifier.classify(post_bear)
    assert classifier.pending_count == 1
    
    # Day 2: Revert to Bull
    classifier.classify(post_bull)
    assert classifier.pending_count == 0
    assert classifier.last_stable_state == MarketRegime.BULL_TRENDING

def test_calculate_posterior_heuristics(classifier):
    """Test that heuristics yield expected high probabilities."""
    # Bull Trending: Price up, Breadth high
    post = classifier.calculate_posterior(vni_vs_ma50=0.05, breadth_20d=80, vol_trend=0.1)
    assert post[MarketRegime.BULL_TRENDING] > 0.5
    
    # Bear Trending: Price down, Breadth low
    post = classifier.calculate_posterior(vni_vs_ma50=-0.05, breadth_20d=20, vol_trend=0.1)
    assert post[MarketRegime.BEAR_TRENDING] > 0.5
