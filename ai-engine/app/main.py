"""
AIInvest AI Engine — FastAPI + DNSE + Vibe-Trading Backtest
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

from app.brain.lifespan import lifespan
from app.config.settings import get_settings
from app.services.dnse.stream_hub import get_stream_hub
from app.routers import market_data, stock_data, screener, stream, backtest, agent, swarm, skills, trading_agents, shadow_account, factors, tools, session, providers, config, memory, dataflows, graph, core, llm_clients, security, hypotheses, preflight, ui_services, vibe_routes, runs

app = FastAPI(
    title="AIInvest AI Engine",
    description="Vietnam Stock Market — DNSE + AI Analysis",
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


# Include DNSE market data routes
app.include_router(stream.router, prefix="/api/stream", tags=["DNSE Stream"])
app.include_router(market_data.router, prefix="/api/market", tags=["Market Data"])
app.include_router(stock_data.router, prefix="/api/stock", tags=["Stock Data"])
app.include_router(screener.router, prefix="/api/screener", tags=["Screener"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["Backtest"])
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
app.include_router(swarm.router, prefix="/api/swarm", tags=["Swarm"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])
app.include_router(trading_agents.router, prefix="/api/trading-agents", tags=["TradingAgents"])
app.include_router(shadow_account.router, prefix="/api/shadow-account", tags=["ShadowAccount"])
app.include_router(factors.router, prefix="/api/factors", tags=["Factors"])
app.include_router(tools.router, prefix="/api/tools", tags=["Tools"])
app.include_router(session.router, prefix="/api/session", tags=["Session"])
app.include_router(providers.router, prefix="/api/providers", tags=["Providers"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(memory.router, prefix="/api/memory", tags=["Memory"])
app.include_router(dataflows.router, prefix="/api/dataflows", tags=["Dataflows"])
app.include_router(graph.router, prefix="/api/graph", tags=["Graph"])
app.include_router(core.router, prefix="/api/core", tags=["Core"])
app.include_router(llm_clients.router, prefix="/api/llm-clients", tags=["LLMClients"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(hypotheses.router, prefix="/api/hypotheses", tags=["Hypotheses"])
app.include_router(preflight.router, prefix="/api/preflight", tags=["Preflight"])
app.include_router(ui_services.router, prefix="/api/ui-services", tags=["UIServices"])
app.include_router(vibe_routes.router, prefix="/api/vibe-api", tags=["VibeAPI"])
app.include_router(runs.router, prefix="/api", tags=["Runs"])
