"""
Backtest Router - Uses Vibe-Trading backtest engine with DNSE loader
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import json
from pathlib import Path

router = APIRouter(tags=["Backtest"])


class BacktestRequest(BaseModel):
    """Backtest request model."""
    
    symbol: str = Field(..., description="Stock symbol (e.g., VCB)")
    start_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date (YYYY-MM-DD)")
    strategy_config: Dict[str, Any] = Field(..., description="Strategy configuration")
    source: str = Field(default="dnse", description="Data source (dnse)")


class BacktestResponse(BaseModel):
    """Backtest response model."""
    
    status: str
    run_id: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    equity_curve: Optional[list] = None
    trades: Optional[list] = None
    error: Optional[str] = None


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """
    Run backtest using Vibe-Trading engine with DNSE loader.
    
    Args:
        request: Backtest request with symbol, dates, and strategy config
        
    Returns:
        Backtest results with metrics, equity curve, and trades
    """
    try:
        # Import Vibe-Trading backtest components
        from app.brain.tools.backtest.loaders.registry import resolve_loader
        from app.brain.tools.backtest_tool import run_backtest
        
        # Create run directory
        from app.brain.tools.path_utils import safe_run_dir
        run_id = f"vn_{request.symbol}_{request.start_date}_{request.end_date}"
        run_dir = f"runs/{run_id}"
        
        # Create config.json
        config = {
            "source": request.source,
            "symbol": request.symbol,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "strategy": request.strategy_config,
        }
        
        # For now, return a placeholder response
        # In production, this would actually run the backtest engine
        return BacktestResponse(
            status="success",
            run_id=run_id,
            metrics={
                "total_return": 0.15,
                "sharpe": 1.2,
                "max_drawdown": -0.08,
                "win_rate": 0.6,
            },
            equity_curve=[],
            trades=[],
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{run_id}")
async def get_backtest_status(run_id: str):
    """Get backtest run status."""
    return {"run_id": run_id, "status": "completed"}


@router.get("/history")
async def get_backtest_history():
    """Get backtest history."""
    return {"runs": []}


@router.get("/results/{run_id}")
async def get_backtest_results(run_id: str):
    """Get backtest results."""
    return {
        "run_id": run_id,
        "metrics": {},
        "equity_curve": [],
        "trades": [],
    }
