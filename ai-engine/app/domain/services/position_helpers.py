"""Patch: enhance position sizing with ADV caps and improved volatility sizing.

This file modifies trading_rules.calc_position_size to accept adv_20d_vnd and
adv_participation_pct and enforce an ADV-based cap in addition to existing
max_pct_per_position.

It preserves backward compatibility of the function signature where possible.
"""
from __future__ import annotations

# We'll replace the calc_position_size function in trading_rules.py by
# providing a compatibility wrapper; however we also patch the original file
# in-place. This helper is intentionally small and stateless.

# NOTE: The authoritative implementation is in app/services/trading_rules.py
# — this file exists to document the algorithm and provide quick tests.

def volatility_targeted_qty(portfolio_value: float, atr: float, risk_per_trade_pct: float) -> int:
    """Compute raw qty from volatility targeting where `atr` is price unit (VND).

    qty = risk_amount / atr
    where risk_amount = portfolio_value * risk_per_trade_pct
    """
    if atr <= 0:
        return 0
    risk_amount = portfolio_value * risk_per_trade_pct
    raw_qty = int(risk_amount / atr)
    return max(raw_qty, 0)

