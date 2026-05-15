"""Stream control — register symbols for DNSE WebSocket subscription."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.dnse.stream_hub import get_stream_hub

router = APIRouter()


class SubscribeRequest(BaseModel):
    symbols: list[str]


@router.post("/subscribe")
async def subscribe_symbols(body: SubscribeRequest):
    hub = get_stream_hub()
    symbols = [s.upper() for s in body.symbols if s.strip()]
    hub.subscribe_symbols(symbols)
    return {"subscribed": symbols, "status": hub.status()}


@router.get("/status")
async def stream_status():
    return get_stream_hub().status()
