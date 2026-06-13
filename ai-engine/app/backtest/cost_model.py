"""HOSE Transaction Cost Model — Chi phí giao dịch thực tế.

Thông số HOSE thực tế (cập nhật theo DNSE):
- Phí môi giới: 0.10% mua + 0.10% bán
- Thuế bán: 0.10% (chỉ chiều bán, kể cả lỗ)
- Phí tối thiểu: 10,000 VND/lệnh
- Lô giao dịch: 100 cổ phiếu
"""
import math
from typing import Literal, Optional

HOSE_COST_PARAMS = {
    "brokerage_rate_buy": 0.0010,
    "brokerage_rate_sell": 0.0010,
    "tax_rate_sell": 0.0010,
    "min_brokerage_fee": 10_000,
    "lot_size": 100,
}

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


def estimate_cost(
    side: str,
    price: float,
    quantity: int,
    adv_20d: float = 0,
    spread_pct: float = 0.001,
) -> dict:
    """Tính chi phí giao dịch đầy đủ cho lệnh HOSE.

    Market impact model: Almgren-Chriss simplified
        impact = spread/2 + sigma * sqrt(qty_value / adv_20d) * impact_coeff

    KHÔNG được dùng giá close lý tưởng — phải có slippage tối thiểu 1 bước giá.

    Args:
        side: "BUY" | "SELL"
        price: Giá khớp (VND)
        quantity: Số lượng cổ phiếu (đã làm tròn lô)
        adv_20d: Average daily value 20 ngày (VND)
        spread_pct: Estimated bid-ask spread

    Returns:
        Dict with chi phí breakdown
    """
    notional = price * quantity

    brokerage = max(
        notional * HOSE_COST_PARAMS[f"brokerage_rate_{side.lower()}"],
        HOSE_COST_PARAMS["min_brokerage_fee"],
    )

    tax = notional * HOSE_COST_PARAMS["tax_rate_sell"] if side == "SELL" else 0

    price_step = get_hose_price_step(price)
    min_slippage = price_step / price if price > 0 else 0

    participation = (notional / adv_20d) if adv_20d > 0 else 1.0
    impact_pct = spread_pct / 2 + 0.005 * (participation ** 0.5)

    total_slippage = max(min_slippage, impact_pct) * notional

    return {
        "brokerage": round(brokerage, 2),
        "tax": round(tax, 2),
        "slippage": round(total_slippage, 2),
        "total_cost": round(brokerage + tax + total_slippage, 2),
        "total_cost_pct": round(
            (brokerage + tax + total_slippage) / notional, 6
        ) if notional > 0 else 0,
    }
