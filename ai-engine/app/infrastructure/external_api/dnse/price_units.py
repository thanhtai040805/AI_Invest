"""Price unit conversion for DNSE quotes, which use thousand-VND units."""

from typing import Any


def to_vnd_price(value: Any) -> float:
    """Normalize DNSE equity prices to the VND units used by stored OHLCV."""
    try:
        price = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return price * 1000 if 0 < abs(price) < 500 else price
