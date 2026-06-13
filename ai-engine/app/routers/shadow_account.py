"""
Shadow Account Router - Paper trading using Vibe-Trading shadow account
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import uuid

router = APIRouter(tags=["ShadowAccount"])


class ShadowProfileRequest(BaseModel):
    """Shadow profile creation request."""
    
    name: str = Field(..., description="Account name")
    initial_capital: float = Field(..., description="Initial capital in VND")
    symbol: str = Field(..., description="Default symbol for paper trading")


class ShadowProfileResponse(BaseModel):
    """Shadow profile response."""
    
    shadow_id: str
    name: str
    initial_capital: float
    current_capital: float
    status: str


class TradeRequest(BaseModel):
    """Trade execution request."""
    
    shadow_id: str = Field(..., description="Shadow account ID")
    symbol: str = Field(..., description="Stock symbol")
    action: str = Field(..., description="buy or sell")
    quantity: int = Field(..., description="Number of shares")
    price: Optional[float] = Field(None, description="Limit price (optional)")


# In-memory shadow account storage (in production, use database)
shadow_accounts: Dict[str, Dict[str, Any]] = {}


@router.post("/create", response_model=ShadowProfileResponse)
async def create_shadow_profile(request: ShadowProfileRequest):
    """
    Create a shadow account for paper trading.
    
    Args:
        request: Shadow profile creation request
        
    Returns:
        Created shadow profile
    """
    try:
        from app.shadow_account.storage import new_shadow_id
        from app.shadow_account.models import ShadowProfile
        
        shadow_id = new_shadow_id()
        
        # Create shadow profile
        profile = {
            "id": shadow_id,
            "name": request.name,
            "initial_capital": request.initial_capital,
            "current_capital": request.initial_capital,
            "symbol": request.symbol,
            "status": "active",
            "trades": [],
            "positions": {},
            "created_at": "2026-05-23T00:00:00Z",
        }
        
        shadow_accounts[shadow_id] = profile
        
        return ShadowProfileResponse(
            shadow_id=shadow_id,
            name=request.name,
            initial_capital=request.initial_capital,
            current_capital=request.initial_capital,
            status="active",
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{shadow_id}")
async def get_shadow_profile(shadow_id: str):
    """Get shadow account details."""
    if shadow_id not in shadow_accounts:
        raise HTTPException(status_code=404, detail="Shadow account not found")
    return shadow_accounts[shadow_id]


@router.post("/trade")
async def execute_trade(request: TradeRequest):
    """
    Execute a trade in shadow account.
    
    Args:
        request: Trade execution request
        
    Returns:
        Trade execution result
    """
    try:
        if request.shadow_id not in shadow_accounts:
            raise HTTPException(status_code=404, detail="Shadow account not found")
        
        account = shadow_accounts[request.shadow_id]
        
        # Execute trade logic (simplified)
        trade = {
            "id": str(uuid.uuid4()),
            "symbol": request.symbol,
            "action": request.action,
            "quantity": request.quantity,
            "price": request.price or 0,
            "timestamp": "2026-05-23T00:00:00Z",
        }
        
        account["trades"].append(trade)
        
        return {
            "status": "success",
            "trade": trade,
            "account": {
                "shadow_id": request.shadow_id,
                "current_capital": account["current_capital"],
            },
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{shadow_id}/trades")
async def get_shadow_trades(shadow_id: str):
    """Get trade history for shadow account."""
    if shadow_id not in shadow_accounts:
        raise HTTPException(status_code=404, detail="Shadow account not found")
    return {
        "shadow_id": shadow_id,
        "trades": shadow_accounts[shadow_id]["trades"],
    }


@router.get("/{shadow_id}/positions")
async def get_shadow_positions(shadow_id: str):
    """Get current positions for shadow account."""
    if shadow_id not in shadow_accounts:
        raise HTTPException(status_code=404, detail="Shadow account not found")
    return {
        "shadow_id": shadow_id,
        "positions": shadow_accounts[shadow_id]["positions"],
    }
