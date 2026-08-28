"""Common Core Utilities — reusable data helpers for ai-engine."""

import math
import re
from typing import Any, Dict, List, Optional


def clean_nan(data: Dict[str, Any]) -> Dict[str, Any]:
    """Replace NaN/Inf in dict values with None for safe JSON serialization."""
    if not isinstance(data, dict):
        return data
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in data.items()
    }


def safe_div(numerator: Optional[float], denominator: Optional[float], default: float = 0.0) -> float:
    """Safely divide two numbers without throwing ZeroDivisionError."""
    if numerator is None or denominator is None:
        return default
    try:
        if denominator == 0 or math.isnan(denominator) or math.isnan(numerator):
            return default
        res = numerator / denominator
        if math.isinf(res) or math.isnan(res):
            return default
        return res
    except (ZeroDivisionError, TypeError):
        return default


def validate_ticker(ticker: str) -> str:
    """Validate and sanitize a VN stock ticker."""
    if not ticker or not isinstance(ticker, str):
        raise ValueError("Invalid ticker: must be a non-empty string")
    clean = ticker.strip().upper()
    if not re.match(r"^[A-Z0-9_]{2,10}$", clean):
        raise ValueError(f"Invalid ticker format: {ticker}")
    return clean


def sanitize_input(text: str, max_len: int = 1000) -> str:
    """Basic sanitization for user text input."""
    if not text:
        return ""
    clean = str(text).strip()[:max_len]
    return clean
