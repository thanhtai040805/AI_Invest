"""Factors Router — alpha factor zoo compute, list, and metadata."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import numpy as np
import pandas as pd
import yfinance as yf

from app.brain.quant.factors.registry import get_default_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Factors"])


# ── schemas ────────────────────────────────────────────────────────────────

class FactorComputeRequest(BaseModel):
    alpha_id: str = Field(..., description="Factor id, e.g. qlib158_beta5, alpha101_001")
    symbol: str = Field(..., description="Stock ticker (e.g. AAPL, VNM)")
    start_date: str = Field(..., description="Start date YYYY-MM-DD")
    end_date: str = Field(..., description="End date YYYY-MM-DD")
    universe: Optional[list[str]] = Field(None, description="Additional tickers for cross-sectional rank")


class FactorComputeResponse(BaseModel):
    alpha_id: str
    status: str
    result: Optional[list] = None
    error: Optional[str] = None


class FactorMetaResponse(BaseModel):
    alpha_id: str
    zoo: str
    theme: list[str]
    universe: list[str]
    columns_required: list[str]
    formula_latex: str
    notes: str
    nickname: str


# ── helpers ────────────────────────────────────────────────────────────────

def _fetch_panel(symbol: str, start_date: str, end_date: str,
                 universe: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data and build a factor-compatible panel.

    Returns ``{"open": df, "high": df, …}`` where each DataFrame has
    ``index = DatetimeIndex`` and ``columns = [ticker, …]``.
    """
    tickers = [symbol.upper()]
    if universe:
        tickers.extend(t.upper() for t in universe if t.upper() != symbol.upper())

    data = yf.download(
        tickers,
        start=start_date,
        end=end_date,
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )

    if data.empty:
        raise ValueError(f"No data returned for {tickers}")

    panel: dict[str, list] = {
        "open": [], "high": [], "low": [], "close": [],
        "volume": [], "adj_close": [],
    }

    for ticker in tickers:
        if ticker in data.columns.levels[1] if isinstance(data.columns, pd.MultiIndex) else False:
            df = data[ticker]
        else:
            # single-ticker case
            df = data

        for col in panel:
            if col in df.columns:
                s = df[col].copy()
                s.name = ticker
                panel[col].append(s)

    result = {}
    for col, series_list in panel.items():
        if series_list:
            merged = pd.concat(series_list, axis=1)
            merged.index = pd.to_datetime(merged.index)
            result[col] = merged
    return result


# ── endpoints ──────────────────────────────────────────────────────────────

@router.get("/list")
async def list_factors():
    """List all registered factors grouped by zoo."""
    registry = get_default_registry()
    manifest = registry.export_manifest()
    return {
        "factors": [
            {"alpha_id": aid, "zoo": info["zoo"], "theme": info.get("theme", []),
             "nickname": info.get("nickname", ""), "universe": info.get("universe", [])}
            for aid, info in manifest["alphas"].items()
        ]
    }


@router.get("/info/{alpha_id}", response_model=FactorMetaResponse)
async def factor_info(alpha_id: str):
    """Get metadata for a specific alpha factor."""
    registry = get_default_registry()
    alpha = registry.get(alpha_id)
    if not alpha:
        raise HTTPException(404, f"Factor '{alpha_id}' not found")
    meta = alpha.meta
    return FactorMetaResponse(
        alpha_id=alpha.id,
        zoo=alpha.zoo,
        theme=meta.get("theme", []),
        universe=meta.get("universe", []),
        columns_required=meta.get("columns_required", []),
        formula_latex=meta.get("formula_latex", ""),
        notes=meta.get("notes", ""),
        nickname=meta.get("nickname", ""),
    )


@router.post("/compute", response_model=FactorComputeResponse)
async def compute_factor(request: FactorComputeRequest):
    """Compute an alpha factor for a given symbol and date range."""
    try:
        registry = get_default_registry()
        alpha = registry.get(request.alpha_id)
        if not alpha:
            raise HTTPException(404, detail=f"Factor '{request.alpha_id}' not found")

        panel = _fetch_panel(
            request.symbol,
            request.start_date,
            request.end_date,
            universe=request.universe,
        )

        result_df = registry.compute(request.alpha_id, panel)

        # Convert to serialisable format
        if result_df is None or result_df.empty:
            return FactorComputeResponse(alpha_id=request.alpha_id, status="empty")

        result_df = result_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        records = []
        for date, row in result_df.iterrows():
            for ticker, val in row.items():
                if val is not None:
                    records.append({
                        "date": str(date.date()),
                        "ticker": ticker,
                        "value": round(float(val), 6),
                    })

        return FactorComputeResponse(
            alpha_id=request.alpha_id,
            status="success",
            result=records,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Factor compute failed")
        raise HTTPException(500, detail=str(e))


@router.get("/presets")
async def list_factor_presets():
    """List factor presets grouped by theme."""
    registry = get_default_registry()
    manifest = registry.export_manifest()

    themes: dict[str, list] = {}
    for aid, info in manifest["alphas"].items():
        for theme in info.get("theme", ["uncategorised"]):
            themes.setdefault(theme, []).append(aid)

    return {
        "presets": [
            {"name": theme, "description": f"{theme.title()} factors",
             "factors": alphas}
            for theme, alphas in sorted(themes.items())
        ]
    }


@router.get("/health")
async def factor_health():
    """Registry health — loaded / failed count."""
    registry = get_default_registry()
    return registry.health()
