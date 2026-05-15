"""
Stock Data router — profile, OHLCV, quote, orderbook, trades, fundamentals.
"""

from fastapi import APIRouter, Query
from typing import Optional

from app.services.market_data_service import market_data_svc

router = APIRouter()


@router.get("/{symbol}/profile")
async def get_profile(symbol: str):
    return await market_data_svc.get_profile(symbol.upper())


@router.get("/{symbol}/ohlcv")
async def get_ohlcv(
    symbol: str,
    interval: str = Query("1D"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    return await market_data_svc.get_ohlcv(symbol.upper(), interval, start, end)


@router.get("/{symbol}/quote")
async def get_quote(symbol: str):
    return await market_data_svc.get_quote(symbol.upper())


@router.get("/{symbol}/orderbook")
async def get_orderbook(symbol: str):
    return await market_data_svc.get_order_book(symbol.upper())


@router.get("/{symbol}/trades")
async def get_trades(symbol: str):
    return await market_data_svc.get_trades(symbol.upper())


@router.get("/{symbol}/fundamentals")
async def get_fundamentals(symbol: str):
    return await market_data_svc.get_fundamentals(symbol.upper())
