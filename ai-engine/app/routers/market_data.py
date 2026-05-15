"""
Market Data router — wraps vnstock v4 for VN market indices, breadth, snapshot.
"""

from fastapi import APIRouter, Query
from typing import Optional
import traceback

from app.services.market_data_service import market_data_svc

router = APIRouter()


@router.get("/indices")
async def get_indices():
    """Get VN-Index, HNX-Index, UPCOM real-time values."""
    try:
        return await market_data_svc.get_indices()
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e), "indices": []}


@router.get("/breadth")
async def get_breadth():
    """Get market breadth: advancers, decliners, unchanged."""
    try:
        return await market_data_svc.get_breadth()
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


@router.get("/snapshot")
async def get_snapshot(exchange: Optional[str] = Query(None, description="HOSE, HNX, or UPCOM")):
    """Get full market board with all symbol prices."""
    try:
        return await market_data_svc.get_snapshot(exchange)
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e), "stocks": []}


@router.get("/stocks")
async def get_stock_list(exchange: Optional[str] = Query(None)):
    """Get list of all stocks."""
    try:
        return await market_data_svc.get_stock_list(exchange)
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e), "stocks": []}


@router.get("/liquidity")
async def get_liquidity():
    """Intraday volume vs average liquidity."""
    try:
        return await market_data_svc.get_liquidity()
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}


@router.get("/heatmap")
async def get_heatmap():
    """Sector heatmap grouped by industry."""
    try:
        return await market_data_svc.get_heatmap()
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e), "sectors": []}


@router.get("/search")
async def search_symbol(q: str = Query(..., min_length=1)):
    """Search symbols by name or code."""
    try:
        return await market_data_svc.search(q)
    except Exception as e:
        traceback.print_exc()
        return []
