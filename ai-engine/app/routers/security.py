"""
Security Router - Security features integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

router = APIRouter(tags=["Security"])


class SecurityRequest(BaseModel):
    """Security request."""
    
    action: str = Field(..., description="Security action")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Action parameters")


class SecurityResponse(BaseModel):
    """Security response."""
    
    action: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/validate")
async def validate_security(request: SecurityRequest):
    """
    Validate security using Vibe-Trading security features.
    
    Args:
        request: Security validation request
        
    Returns:
        Security validation result
    """
    try:
        from app.brain.security import validate_input
        
        if request.action == "validate_input":
            result = validate_input(**(request.parameters or {}))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")
        
        return SecurityResponse(
            action=request.action,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_security_status():
    """Get security system status."""
    return {
        "status": "ok",
        "security_enabled": True,
    }


@router.get("/risk-flags/{symbol}")
async def get_risk_flags(symbol: str):
    """Detect warning/risk signals for a stock symbol (v2 computed flags)."""
    from app.brain.risk.queries import get_active_flags, get_hard_blocked, get_soft_flag_count

    try:
        flags = get_active_flags(symbol)
        return {
            "symbol": symbol.upper(),
            "hard_blocked": get_hard_blocked(symbol),
            "soft_flag_count": get_soft_flag_count(symbol),
            "flags": flags,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TradingRulesRequest(BaseModel):
    portfolio_json: Dict[str, Any]
    peak_prices_json: Optional[Dict[str, float]] = None


@router.post("/trading-rules/check")
async def check_trading_rules(request: TradingRulesRequest):
    """Run trading rules against current portfolio state."""
    from app.services.trading_rules import (
        PortfolioState,
        PositionInfo,
        TradingRulesConfig,
        check_all_rules,
    )

    try:
        portfolio_raw = request.portfolio_json
        positions = [
            PositionInfo(
                symbol=p["symbol"],
                entry_price=float(p["entryPrice"]),
                current_price=float(p["currentPrice"]),
                quantity=int(p["quantity"]),
                pnl_pct=float(p.get("pnlPct", 0)),
                days_held=int(p.get("daysHeld", 0)),
                entry_value=float(p.get("entryValue", 0)),
                current_value=float(p.get("currentValue", 0)),
            )
            for p in portfolio_raw.get("positions", [])
        ]
        portfolio = PortfolioState(
            total_value=float(portfolio_raw.get("totalValue", 0)),
            cash=float(portfolio_raw.get("cash", 0)),
            positions=positions,
            peak_value=float(portfolio_raw.get("peakValue", 0)),
        )

        cfg = TradingRulesConfig()
        result = check_all_rules(portfolio, peak_prices=request.peak_prices_json, config=cfg)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PositionSizeRequest(BaseModel):
    symbol: str
    price: float
    portfolio_value: float
    atr: Optional[float] = None


@router.post("/trading-rules/position-size")
async def calculate_position_size(request: PositionSizeRequest):
    """Calculate suggested position size for a new trade."""
    from app.services.trading_rules import calc_suggested_position_size

    try:
        result = calc_suggested_position_size(
            request.symbol, request.price, request.portfolio_value, atr=request.atr,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
