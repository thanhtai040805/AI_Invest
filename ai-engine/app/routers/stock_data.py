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


@router.get("/intraday/{symbol}")
async def get_intraday_ohlcv(
    symbol: str,
    resolution: str = Query("5", description="Candle resolution: 1, 5, 15, 30, 1H, 1D"),
    start: Optional[str] = Query(None, description="Start date (ISO format or unix timestamp)"),
    end: Optional[str] = Query(None, description="End date (ISO format or unix timestamp)"),
):
    """Fetch intraday OHLCV directly from DNSE REST API.

    Independent of the WebSocket stream hub.
    Supports resolutions: 1m, 5m, 15m, 30m, 1H, 1D.
    """
    from app.services.dnse.intraday_tool import get_intraday_tool

    tool = get_intraday_tool()

    from_ts: Optional[int] = None
    to_ts: Optional[int] = None

    if start:
        try:
            from_ts = int(start)
        except ValueError:
            from datetime import datetime
            from_ts = int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp())

    if end:
        try:
            to_ts = int(end)
        except ValueError:
            from datetime import datetime
            to_ts = int(datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp())

    data = tool.fetch(symbol.upper(), resolution=resolution, from_ts=from_ts, to_ts=to_ts)
    return {"symbol": symbol.upper(), "resolution": resolution, "data": data, "source": "dnse-rest"}
