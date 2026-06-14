"""Market utility functions (price steps, lot sizes, etc)."""
import math
from typing import Literal

HOSE_PRICE_STEPS = [
    (10_000, 10),
    (50_000, 50),
    (100_000, 100),
    (200_000, 500),
    (float("inf"), 1_000),
]

def get_hose_price_step(price: float) -> float:
    """Lấy bước giá HOSE cho mức giá hiện tại."""
    for limit, step in HOSE_PRICE_STEPS:
        if price <= limit:
            return step
    return 1_000

def snap_to_price_step(price: float, side: Literal["BUY", "SELL"] = "BUY") -> float:
    """Làm tròn giá về bước giá hợp lệ.

    Args:
        price: Giá cần làm tròn
        side: "BUY" → round down, "SELL" → round up
    """
    step = get_hose_price_step(price)
    if side == "BUY":
        return math.floor(price / step) * step
    return math.ceil(price / step) * step

def round_to_lot(quantity: float, lot_size: int = 100) -> int:
    """Làm tròn xuống về bội số của lot_size."""
    return int(quantity // lot_size) * lot_size
