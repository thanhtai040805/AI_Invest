import pytest
import pandas as pd
import numpy as np

def detect_look_ahead(features: pd.DataFrame, target: pd.Series) -> bool:
    """
    Detects if any feature has a suspicious correlation with the *future* target
    that could indicate look-ahead bias (e.g., using future prices to compute a current feature).
    
    Returns True if look-ahead bias is detected, False otherwise.
    """
    # Simple correlation check with the target (which is already shifted back, so target(t) = return(t+1 to t+5))
    # If a feature uses data from t+1, its correlation with target(t) will be extremely high.
    correlations = features.corrwith(target)
    
    # Absolute correlation > 0.95 is a strong indicator of look-ahead bias in financial data
    if any(correlations.abs() > 0.95):
        return True
        
    return False

def test_no_look_ahead_bias():
    """Test that a clean dataset does not trigger the look-ahead detector."""
    np.random.seed(42)
    # Generate random features
    df = pd.DataFrame({
        'feature_1': np.random.randn(100),
        'feature_2': np.random.randn(100)
    })
    # Target is independent random noise (no look-ahead)
    target = pd.Series(np.random.randn(100))
    
    has_bias = detect_look_ahead(df, target)
    assert not has_bias, "False positive look-ahead bias detected in clean data"

def test_detects_look_ahead_bias():
    """Test that a dataset with a leaking feature triggers the detector."""
    np.random.seed(42)
    df = pd.DataFrame({
        'feature_1': np.random.randn(100),
    })
    # Target
    target = pd.Series(np.random.randn(100))
    
    # Feature 2 is leaking the future target (e.g., feature_2(t) = target(t) * 0.99)
    df['feature_2'] = target * 0.99 + np.random.randn(100) * 0.01
    
    has_bias = detect_look_ahead(df, target)
    assert has_bias, "Failed to detect look-ahead bias in leaking data"
