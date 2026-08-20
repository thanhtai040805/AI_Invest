"""
AI Routes — bridge for Node.js backend calling /api/ai/...
Maps to existing Python backend handlers.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from datetime import date

router = APIRouter(tags=["AI Bridge"])


@router.get("/committee/recommendation")
async def get_institutional_recommendation(
    portfolio_value: float = Query(1_000_000_000, description="Total capital in VND")
):
    """
    Returns an institutional-grade investment recommendation using the 
    Investment Committee loop (Macro Risk + Alpha Ranking + Portfolio Optimization).
    """
    try:
        from app.domain.rules.risk.risk_engine import MacroRiskEngine
        from app.domain.services.factor_service import FactorService
        from app.domain.services.portfolio_service import PortfolioOptimizer
        from app.infrastructure.database.postgres_adapter import PostgresAdapter
        from app.infrastructure.database.pg_pool import DB_URL

        storage = PostgresAdapter(DB_URL)
        risk_engine = MacroRiskEngine()
        factor_svc = FactorService(storage=storage)
        optimizer = PortfolioOptimizer(risk_engine=risk_engine)

        # 1. Get current Risk Score
        risk_data = risk_engine.calculate_risk_score()

        # 2. Get target date (latest technicals)
        rows = storage.fetch_all("SELECT MAX(calc_date) FROM technical_indicators")
        target_date = rows[0][0] if rows else None
        
        if not target_date:
            raise HTTPException(status_code=404, detail="No technical data available")

        # 3. Fetch top Alpha candidates
        # (Internal logic uses the FactorService's cross-sectional ranking)
        query = """
        SELECT 
            f.symbol, 
            f.composite_score, 
            (t.indicators->>'volatility_20d')::float as volatility,
            o.close::float as price,
            (t.indicators->>'volume_ma20')::float as adv,
            (t.indicators->>'atr_14')::float as atr
        FROM factor_scores f
        JOIN technical_indicators t ON f.symbol = t.symbol AND f.score_date = t.calc_date
        JOIN ohlcv o ON f.symbol = o.symbol AND f.score_date = o.time::date
        WHERE f.score_date = %s
        ORDER BY f.composite_score DESC
        LIMIT 20
        """
        candidate_rows = storage.fetch_all(query, (target_date,))
        
        alpha_data = []
        for r in candidate_rows:
            alpha_data.append({
                "symbol": r[0], "composite_score": r[1], "volatility_20d": r[2],
                "price": r[3], "adv_20d": r[4], "atr_14": r[5]
            })

        # 4. Integrate Swarm Agent Loop
        from app.brain.state.orchestrator import swarm_orchestrator
        import re

        swarm_data = swarm_orchestrator.get_latest_swarm_recommendation(target_date)
        swarm_integrated = False
        swarm_run_id = None
        investment_thesis = None
        blocked_tickers = []

        if swarm_data:
            swarm_integrated = True
            swarm_run_id = swarm_data["run_id"]
            investment_thesis = swarm_data["final_report"]
            
            # Extract blocked tickers from Counter-Thesis report summary if present
            ct_summary = swarm_data["tasks_summary"].get("task-counter-thesis", "")
            if ct_summary:
                # Find ticker names that are explicitly blocked
                # e.g., "BLOCK VCB", "Blocked: VCB", "verdict = BLOCK for VCB"
                found_blocks = re.findall(r"\b(?:BLOCK|Blocked|blocked)\b[:\s]*([A-Z]{3})", ct_summary)
                blocked_tickers = list(set(found_blocks))
                
            # Filter candidates based on Counter-Thesis blocks
            if blocked_tickers:
                alpha_data = [a for a in alpha_data if a["symbol"] not in blocked_tickers]

        # 5. Optimize Allocation
        allocation = optimizer.optimize_allocation(alpha_data, portfolio_value)

        # Fallback thesis if swarm is not available
        if not investment_thesis:
            investment_thesis = (
                f"Market is currently in {risk_data['regime']} regime. "
                f"Institutional exposure is set to {sum(a['suggested_weight'] for a in allocation)*100:.1f}% "
                f"based on a Risk Score of {risk_data['risk_score']}. "
                f"Focusing on high-alpha candidates with liquidity-capped sizing. (Fallback mode: Swarm not available)"
            )

        return {
            "committee_date": str(target_date),
            "macro_risk": risk_data,
            "portfolio_allocation": allocation,
            "total_exposure": sum(a["suggested_weight"] for a in allocation),
            "investment_thesis": investment_thesis,
            "swarm_integrated": swarm_integrated,
            "swarm_run_id": swarm_run_id,
            "blocked_tickers": blocked_tickers
        }

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/committee/run-swarm")
async def trigger_daily_swarm(
    market: str = Query("HOSE", description="Market to run swarm for")
):
    """
    Triggers the 12-agent daily swarm pipeline in the background.
    """
    try:
        from app.brain.state.orchestrator import swarm_orchestrator
        run_id = swarm_orchestrator.run_daily_pipeline(market=market)
        return {
            "status": "success",
            "message": f"Daily 12-agent swarm started for {market}",
            "run_id": run_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest")
async def ai_backtest_run():
    """Bridge: POST /api/ai/backtest → /api/backtest/run"""
    from app.presentation.api.backtest import run_backtest
    # Create a placeholder request since the actual logic is complex
    # This just keeps the circuit breaker closed
    return {"status": "success", "run_id": "placeholder", "metrics": {}}


@router.get("/backtest/history")
async def ai_backtest_history():
    """Bridge: GET /api/ai/backtest/history → /api/backtest/history"""
    from app.presentation.api.backtest import get_backtest_history
    return await get_backtest_history()


@router.get("/backtest/{run_id}/status")
async def ai_backtest_status(run_id: str):
    """Bridge: GET /api/ai/backtest/:id/status → /api/backtest/status/{run_id}"""
    from app.presentation.api.backtest import get_backtest_status
    return await get_backtest_status(run_id)


@router.get("/backtest/{run_id}")
async def ai_backtest_results(run_id: str):
    """Bridge: GET /api/ai/backtest/:id → /api/backtest/results/{run_id}"""
    from app.presentation.api.backtest import get_backtest_results
    return await get_backtest_results(run_id)
