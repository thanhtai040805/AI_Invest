"""
Stock Data router — profile, OHLCV, quote, orderbook, trades, fundamentals.
"""

from fastapi import APIRouter, Query
from typing import Any, Dict, Optional

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


@router.get("/{symbol}/insider")
async def get_insider(symbol: str):
    """Get insider trading data (shareholders, officers, ownership)."""
    from app.services.scraper_insider import get_insider_data

    result = await get_insider_data(symbol.upper())
    return result


@router.get("/macro")
async def get_macro_data():
    """Get macro economic indicators (oil, exchange rate, gold, interest rates)."""
    from app.services.data_enricher import DataEnricher
    return DataEnricher.get_macro_indicators()


@router.get("/{symbol}/technical-indicators")
async def get_technical_indicators(symbol: str):
    """Get technical indicators calculated from OHLCV."""
    from app.services.market_data_service import market_data_svc
    from app.services.data_enricher import DataEnricher
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
    from app.services.market_data_service import market_data_svc
    fund = await market_data_svc.get_fundamentals(symbol.upper())
    return fund


@router.get("/{symbol}/disclosures")
async def get_disclosures(symbol: str):
    """Get regulatory disclosures and sanctions."""
    from app.services.risk_flags_v2 import get_active_flags

    flags = get_active_flags(symbol.upper())
    hard = [f for f in flags if f["flag_type"] in ("CANH_BAO_TC", "CHAM_BAO_TC")]
    soft = [f for f in flags if f["flag_type"] not in ("CANH_BAO_TC", "CHAM_BAO_TC")]
    return {
        "symbol": symbol.upper(),
        "disclosures": flags,
        "totalDisclosures": len(flags),
        "criticalCount": len(hard),
        "warningCount": len(soft),
        "hasRedFlags": len(hard) > 0,
        "summary": f"{len(hard)} hard + {len(soft)} soft flag(s)",
    }


@router.get("/{symbol}/ai-context")
async def get_ai_context(symbol: str):
    """Get unified AI Brain context — all data in one call.
    
    Returns: profile, financials, ratios, technicals, risk, returns,
    factor scores, market extras, macro, foreign flow, dividends,
    sentiment, risk flags, and a compact ai_summary.
    """
    from app.services.data_enricher import DataEnricher
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

    # TODO: fetch real news from DB for sentiment rolling
    news_items = []

    ctx = await DataEnricher.build_ai_context(symbol.upper(), ohlcv_df, news_items)
    return ctx


@router.get("/{symbol}/factor-scores")
async def get_factor_scores(symbol: str):
    """Get multi-factor investment scores (value, momentum, quality, etc.)."""
    from app.services.data_enricher import DataEnricher
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
    from app.services.data_enricher import DataEnricher
    flow = DataEnricher.fetch_foreign_flow(symbol.upper())
    return {"symbol": symbol.upper(), "foreign_flow": flow}


@router.get("/{symbol}/dividends")
async def get_dividends(symbol: str):
    """Get dividend history and corporate events."""
    from app.services.data_enricher import DataEnricher
    events = DataEnricher.fetch_dividend_events(symbol.upper())
    return {"symbol": symbol.upper(), **events}


@router.get("/{symbol}/market-extras")
async def get_market_extras(symbol: str):
    """Get avg volumes, turnover rate, VWAP, value from OHLCV."""
    from app.services.data_enricher import DataEnricher
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
    from app.services.data_enricher import DataEnricher
    # TODO: fetch real news from DB by symbol
    news_items = []
    sentiment = DataEnricher.compute_sentiment_rolling(news_items)
    return {"symbol": symbol.upper(), "sentiment": sentiment}

