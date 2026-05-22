"""
AIInvest AI Engine — FastAPI + DNSE Open API (WebSocket market data).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

from app.core.lifespan import lifespan
from app.config.settings import get_settings
from app.services.dnse.stream_hub import get_stream_hub
from app.routers import market_data, stock_data, screener, ai_chat, stream

app = FastAPI(
    title="AIInvest AI Engine",
    description="Vietnam Stock Market — DNSE Open API + AI Analysis",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    settings = get_settings()
    hub = get_stream_hub()
    return {
        "status": "ok",
        "dataProvider": "dnse",
        "dnse": {
            "enabled": settings.dnse_enabled,
            "configured": settings.dnse_configured,
            "stream": hub.status(),
        },
    }


@app.get("/health/detailed")
async def health_detailed():
    from app.services.dnse.redis_pub import get_rate_limiter
    hub = get_stream_hub()
    limiter = get_rate_limiter()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "stream_hub": hub.status(),
        "rate_limiter": limiter.stats,
        "market_session": {
            "state": hub._session_mgr.get_market_state().value,
            "is_open": hub._session_mgr.is_market_open(),
            "is_connected": hub._session_mgr.is_connected(),
        },
    }


app.include_router(stream.router, prefix="/api/stream", tags=["DNSE Stream"])
app.include_router(market_data.router, prefix="/api/market", tags=["Market Data"])
app.include_router(stock_data.router, prefix="/api/stock", tags=["Stock Data"])
app.include_router(screener.router, prefix="/api/screener", tags=["Screener"])
app.include_router(ai_chat.router, prefix="/api/ai", tags=["AI"])
