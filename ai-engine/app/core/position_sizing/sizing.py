from typing import Optional, Tuple
from app.core.utils.market_utils import round_to_lot

def volatility_targeted_size(
    symbol: str,
    price: float,
    portfolio_value: float,
    target_vol_pct: float = 0.02,
    atr: Optional[float] = None,
    adv_20d_volume: Optional[float] = None,
    max_adv_pct: float = 0.05,
    max_pct_per_position: float = 0.10,
    lot_size: int = 100
) -> Tuple[int, str]:
    """Calculate position size targeting a specific portfolio volatility,
    capped by ADV and max portfolio concentration.

    Args:
        symbol: The stock symbol
        price: Current price
        portfolio_value: Total portfolio value
        target_vol_pct: Risk budget per trade as a % of portfolio (e.g., 0.02 for 2%)
        atr: Average True Range (represents stock's volatility)
        adv_20d_volume: 20-day Average Daily Volume in shares
        max_adv_pct: Max allowed participation of ADV
        max_pct_per_position: Max allowed portfolio weight
        lot_size: Exchange lot size (100 for HOSE)
        
    Returns:
        (quantity, method_description)
    """
    if price <= 0:
        return 0, "price_zero"

    max_value = portfolio_value * max_pct_per_position
    max_shares_by_value = max_value / price

    if atr is not None and atr > 0:
        # Risk target size: (Portfolio * Risk %) / ATR
        risk_amount = portfolio_value * target_vol_pct
        raw_size = risk_amount / atr
        method = f"volatility_targeted (ATR={atr:,.0f}, risk={target_vol_pct*100:.1f}%)"
    else:
        # Fallback to max allowed size
        raw_size = max_shares_by_value
        method = f"fixed_fraction ({max_pct_per_position*100:.0f}%)"

    # 1. Cap by max portfolio value concentration
    if raw_size > max_shares_by_value:
        raw_size = max_shares_by_value
        method += f" [cap: max_pct={max_pct_per_position*100:.0f}%]"

    # 2. Cap by ADV limit
    if adv_20d_volume is not None and adv_20d_volume > 0:
        max_shares_by_adv = adv_20d_volume * max_adv_pct
        if raw_size > max_shares_by_adv:
            raw_size = max_shares_by_adv
            method += f" [cap: ADV={max_adv_pct*100:.1f}%]"

    # 3. Round to lot size
    quantity = round_to_lot(raw_size, lot_size=lot_size)
    return quantity, method
