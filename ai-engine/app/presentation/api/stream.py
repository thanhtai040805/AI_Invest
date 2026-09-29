"""Stream control — register symbols for DNSE WebSocket subscription."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal

from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub

router = APIRouter()


class SubscribeRequest(BaseModel):
    symbols: list[str]


class OhlcRequest(SubscribeRequest):
    resolution: Literal["1", "3", "5", "15", "30", "1H", "1D", "1W"]


@router.post("/subscribe")
async def subscribe_symbols(body: SubscribeRequest):
    hub = get_stream_hub()
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    hub.subscribe_symbols(symbols)
    status = hub.status()
    active = set(status["active_symbol_names"])
    valid = {symbol for symbol in symbols if hub.get_assignment(symbol)["found"]}
    sent = {symbol for symbol in symbols if "trades" in hub.get_assignment(symbol).get("sentFeeds", {})}
    return {
        "requested": symbols,
        "planned": [symbol for symbol in symbols if symbol in active],
        "subscribed": [symbol for symbol in symbols if symbol in sent],
        "rejected": [symbol for symbol in symbols if symbol not in valid],
        "pending": [symbol for symbol in symbols if symbol in valid and symbol not in sent],
        "status": status,
    }


@router.post("/unsubscribe")
async def unsubscribe_symbols(body: SubscribeRequest):
    hub = get_stream_hub()
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    hub.unsubscribe_symbols(symbols)
    return {"unsubscribed": symbols, "status": hub.status()}


@router.post("/subscribe/ohlc")
async def subscribe_ohlc(body: OhlcRequest):
    hub = get_stream_hub()
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    hub.subscribe_ohlc(symbols, body.resolution)
    return {"requested": symbols, "resolution": body.resolution, "status": hub.status()}


@router.post("/unsubscribe/ohlc")
async def unsubscribe_ohlc(body: OhlcRequest):
    hub = get_stream_hub()
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))
    hub.unsubscribe_ohlc(symbols, body.resolution)
    return {"unsubscribed": symbols, "resolution": body.resolution, "status": hub.status()}


@router.get("/status")
async def stream_status():
    return get_stream_hub().status()


@router.get("/assignment/{symbol}")
async def stream_assignment(symbol: str):
    return get_stream_hub().get_assignment(symbol)
