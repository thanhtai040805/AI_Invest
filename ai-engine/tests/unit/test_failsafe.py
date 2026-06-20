"""Unit tests for Failsafe & Heartbeat System — TASK-112."""

import pytest
import time
from unittest.mock import MagicMock
from app.domain.rules.failsafe import FailsafeEngine, FailsafeStatus

@pytest.fixture
def engine():
    # Use small intervals for testing
    return FailsafeEngine(
        heartbeat_interval=0.1, 
        latency_threshold_ms=100.0, 
        missed_heartbeats_limit=3
    )

def test_heartbeat_records_success(engine):
    """Test normal heartbeat recording."""
    engine.record_heartbeat(latency_ms=50.0)
    assert engine.status == FailsafeStatus.INACTIVE
    assert engine.missed_heartbeats == 0

def test_failsafe_activation_on_missed_heartbeats(engine):
    """Test activation after 3 missed heartbeats."""
    # Last heartbeat was at initialization
    time.sleep(0.4) # > 3 * 0.1s
    engine.check_health()
    
    assert engine.status == FailsafeStatus.ACTIVE

def test_failsafe_activation_on_high_latency(engine):
    """Test activation on sustained high latency (> 5s)."""
    # Simulate high latency over 6 seconds
    start_time = time.time()
    
    # First heartbeat with high latency
    engine.record_heartbeat(latency_ms=200.0)
    assert engine.status == FailsafeStatus.INACTIVE
    
    # Mock time.time to simulate 6 seconds passing
    with MagicMock() as mock_time:
        mock_time.return_value = start_time + 6.0
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(time, 'time', mock_time)
            engine.record_heartbeat(latency_ms=200.0)
            
    assert engine.status == FailsafeStatus.ACTIVE

def test_callback_execution_on_activation(engine):
    """Test that registered callbacks are executed upon activation."""
    mock_cb = MagicMock()
    engine.register_activation_callback(mock_cb)
    
    engine._activate("Test Reason")
    
    assert engine.status == FailsafeStatus.ACTIVE
    mock_cb.assert_called_once_with("Test Reason")
