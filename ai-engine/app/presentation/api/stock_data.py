"""
Stock Data router — profile, OHLCV, quote, orderbook, trades, fundamentals.
"""

from fastapi import APIRouter, Query
from typing import Any, Dict, Optional

from app.infrastructure.external_api.market_data_service import market_data_svc

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
    from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

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


@router.get("/{symbol}/insider")
async def get_insider(symbol: str):
    """Get insider trading data (shareholders, officers, ownership)."""
    from app.infrastructure.data_pipelines.scraper_insider import get_insider_data

    result = await get_insider_data(symbol.upper())
    return result


@router.get("/macro")
async def get_macro_data():
    """Get macro economic indicators (oil, exchange rate, gold, interest rates)."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    return DataEnricher.get_macro_indicators()


@router.get("/{symbol}/technical-indicators")
async def get_technical_indicators(symbol: str):
    """Get technical indicators calculated from OHLCV."""
    from app.infrastructure.external_api.market_data_service import market_data_svc
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    import pandas as pd
    from datetime import datetime, timedelta
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    ohlcv = await market_data_svc.get_ohlcv(symbol.upper(), interval="1D", start=start, end=end)
    bars = ohlcv.get("data", [])
    if not bars:
        return {"error": f"No price data available for {symbol.upper()} to compute indicators."}
    
    df = pd.DataFrame(bars)
    df = df.rename(columns={"time": "date"})
    if "close" not in df.columns:
        return {"error": "Missing close prices in historical data."}
    
    indicators = DataEnricher.compute_technical_indicators(df)
    return {"symbol": symbol.upper(), "indicators": indicators}


@router.get("/{symbol}/financials-extended")
async def get_financials_extended(symbol: str):
    """Get complete raw financial statements and calculated ratios."""
    from app.infrastructure.external_api.market_data_service import market_data_svc
    fund = await market_data_svc.get_fundamentals(symbol.upper())
    return fund


@router.get("/{symbol}/disclosures")
async def get_disclosures(symbol: str):
    """Get regulatory disclosures and sanctions (CRS 7-layer)."""
    from app.domain.rules.risk.risk_queries import get_active_flags, get_hard_blocked

    sym = symbol.upper()
    flags = get_active_flags(sym)
    hard_blocked = get_hard_blocked(sym)
    return {
        "symbol": sym,
        "disclosures": flags,
        "totalDisclosures": len(flags),
        "hard_blocked": hard_blocked,
        "hasRedFlags": hard_blocked,
        "summary": f"{'HARD BLOCKED' if hard_blocked else 'No hard blocks'} | {len(flags)} active flag(s)",
    }


@router.get("/{symbol}/ai-context")
async def get_ai_context(symbol: str):
    """Get unified AI Brain context — all data in one call.
    
    Returns: profile, financials, ratios, technicals, risk, returns,
    factor scores, market extras, macro, foreign flow, dividends,
    sentiment, risk flags, and a compact ai_summary.
    """
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    import pandas as pd
    from datetime import datetime, timedelta

    # Fetch OHLCV for technicals/risk
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    ohlcv = await market_data_svc.get_ohlcv(symbol.upper(), interval="1D", start=start, end=end)
    bars = ohlcv.get("data", [])

    ohlcv_df = None
    if bars:
        ohlcv_df = pd.DataFrame(bars)
        if "time" in ohlcv_df.columns:
            ohlcv_df.index = pd.to_datetime(ohlcv_df["time"])
        for col in ["open", "high", "low", "close", "volume"]:
            if col in ohlcv_df.columns:
                ohlcv_df[col] = pd.to_numeric(ohlcv_df[col], errors="coerce")

    # Fetch real news from DB for sentiment rolling
    from app.infrastructure.database.pg_pool import get_cursor
    news_items = []
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT published_date, sentiment_score, investment_impact, materiality,
                       materiality_score, surprise_score, business_horizon, pricing_horizon,
                       persistence, persistence_score, reversibility, apparent_novelty,
                       novelty, evidence_strength, credibility, event_type, severity, source
                FROM knowledge_documents
                WHERE symbol = %s AND triaged_at IS NOT NULL
                ORDER BY published_date DESC
                LIMIT 100
            """, (symbol.upper(),))
            cols = [desc[0] for desc in cur.description]
            news_items = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        pass

    ctx = await DataEnricher.build_ai_context(symbol.upper(), ohlcv_df, news_items)
    return ctx


@router.get("/{symbol}/factor-scores")
async def get_factor_scores(symbol: str):
    """Get multi-factor investment scores (value, momentum, quality, etc.)."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    import pandas as pd
    from datetime import datetime, timedelta

    # Need financials + technicals + risk to compute scores
    fundamentals = DataEnricher.fetch_vnstock_financials(symbol.upper())

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    ohlcv = await market_data_svc.get_ohlcv(symbol.upper(), interval="1D", start=start, end=end)
    bars = ohlcv.get("data", [])

    technicals = {}
    risk = {}
    if bars:
        df = pd.DataFrame(bars)
        if "time" in df.columns:
            df.index = pd.to_datetime(df["time"])
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        technicals = DataEnricher.compute_technical_indicators(df)
        if "close" in df.columns:
            close_prices = df["close"].astype(float).tolist()
            dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d) for d in df.index]
            risk = DataEnricher.compute_risk_metrics(symbol.upper(), close_prices, dates)

    scores = DataEnricher.compute_factor_scores(symbol.upper(), fundamentals, technicals, risk)
    return {"symbol": symbol.upper(), "factor_scores": scores}


@router.get("/{symbol}/foreign-flow")
async def get_foreign_flow(symbol: str):
    """Get foreign investor buy/sell flow data."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    flow = DataEnricher.fetch_foreign_flow(symbol.upper())
    return {"symbol": symbol.upper(), "foreign_flow": flow}


@router.get("/{symbol}/dividends")
async def get_dividends(symbol: str):
    """Get dividend history and corporate events."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    events = DataEnricher.fetch_dividend_events(symbol.upper())
    return {"symbol": symbol.upper(), **events}


@router.get("/{symbol}/market-extras")
async def get_market_extras(symbol: str):
    """Get avg volumes, turnover rate, VWAP, value from OHLCV."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    import pandas as pd
    from datetime import datetime, timedelta

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    ohlcv = await market_data_svc.get_ohlcv(symbol.upper(), interval="1D", start=start, end=end)
    bars = ohlcv.get("data", [])

    if not bars:
        return {"error": f"No OHLCV data for {symbol.upper()}"}

    df = pd.DataFrame(bars)
    if "time" in df.columns:
        df.index = pd.to_datetime(df["time"])
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    profile = DataEnricher.fetch_vnstock_profile(symbol.upper())
    shares = profile.get("shares_outstanding", 0)
    extras = DataEnricher.compute_market_extras(df, shares)
    return {"symbol": symbol.upper(), "market_extras": extras}


@router.get("/{symbol}/sentiment")
async def get_sentiment(symbol: str):
    """Get rolling sentiment scores and news counts (1d/5d/10d)."""
    from app.infrastructure.data_pipelines.data_enricher import DataEnricher
    # Fetch real news from DB by symbol
    from app.infrastructure.database.pg_pool import get_cursor
    news_items = []
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT published_date, sentiment_score, investment_impact, materiality,
                       materiality_score, surprise_score, business_horizon, pricing_horizon,
                       persistence, persistence_score, reversibility, apparent_novelty,
                       novelty, evidence_strength, credibility, event_type, severity, source
                FROM knowledge_documents
                WHERE symbol = %s AND triaged_at IS NOT NULL
                ORDER BY published_date DESC
                LIMIT 100
            """, (symbol.upper(),))
            cols = [desc[0] for desc in cur.description]
            news_items = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        pass
    sentiment = DataEnricher.compute_sentiment_rolling(news_items)
    return {"symbol": symbol.upper(), "sentiment": sentiment}

