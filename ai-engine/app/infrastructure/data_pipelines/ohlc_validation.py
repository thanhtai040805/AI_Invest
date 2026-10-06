"""Validation shared by daily and historical OHLC ingestion paths."""

import math


def is_valid_ohlc(open_price, high_price, low_price, close_price) -> bool:
    """Return whether a candle has finite positive prices within its range."""
    try:
        open_price, high_price, low_price, close_price = map(
            float, (open_price, high_price, low_price, close_price)
        )
    except (TypeError, ValueError, OverflowError):
        return False

    if not all(math.isfinite(value) and value > 0 for value in (open_price, high_price, low_price, close_price)):
        return False
    return low_price <= open_price <= high_price and low_price <= close_price <= high_price
