"""Vietnam market hooks and symbol classification.

For VN stocks, symbols are bare codes like "FPT", "VCB" with no suffix.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from backtest.models import Position


def _detect_market(code: str) -> str:
    """Infer market type from symbol format. Always returns vn_equity.

    Args:
        code: Ticker / symbol string.

    Returns:
        "vn_equity" for all VN stocks.
    """
    return "vn_equity"
