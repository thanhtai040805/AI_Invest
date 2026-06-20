"""
Swarm Router - Uses Vibe-Trading swarm orchestration
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import uuid

router = APIRouter(tags=["Swarm"])


class SwarmRequest(BaseModel):
    """Swarm run request."""
    
    preset_name: str = Field(..., description="Preset name from swarm/presets/")
    user_vars: Dict[str, str] = Field(default_factory=dict, description="User variables for prompt templates")
    symbol: Optional[str] = Field(None, description="Stock symbol (e.g., VCB)")


class SwarmResponse(BaseModel):
    """Swarm run response."""
    
    run_id: str
    status: str
    preset_name: str
    message: Optional[str] = None


# In-memory swarm runtime (in production, use proper initialization)
_swarm_runtime = None


def get_swarm_runtime():
    """Get or initialize swarm runtime."""
    global _swarm_runtime
    if _swarm_runtime is None:
        from app.brain.state.runtime import SwarmRuntime
        from app.brain.state.store import SwarmStore
        
        store = SwarmStore()
        _swarm_runtime = SwarmRuntime(store)
    return _swarm_runtime


@router.post("/run", response_model=SwarmResponse)
async def run_swarm(request: SwarmRequest, background_tasks: BackgroundTasks):
    """
    Run swarm preset using Vibe-Trading swarm orchestration.
    
    Args:
        request: Swarm run request with preset name and user variables
        
    Returns:
        Swarm run result
    """
    try:
        runtime = get_swarm_runtime()
        
        # Add symbol to user vars if provided
        user_vars = request.user_vars.copy()
        if request.symbol:
            user_vars["symbol"] = request.symbol
        
        # Start swarm run (background execution)
        run = runtime.start_run(
            preset_name=request.preset_name,
            user_vars=user_vars,
            include_shell_tools=False,
        )
        
        return SwarmResponse(
            run_id=run.id,
            status=run.status.value,
            preset_name=request.preset_name,
            message=f"Swarm run started with preset {request.preset_name}",
        )
        
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Preset {request.preset_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}")
async def get_swarm_run(run_id: str):
    """Get swarm run status."""
    try:
        runtime = get_swarm_runtime()
        loaded = runtime._store.load_run(run_id)
        if not loaded:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        
        run = runtime._store.reconcile_run(loaded, write=True)
        
        return {
            "id": run.id,
            "preset_name": run.preset_name,
            "status": run.status.value,
            "is_stale": runtime._store.is_run_stale(run),
            "user_vars": run.user_vars,
            "agents": [a.model_dump() for a in run.agents],
            "tasks": [t.model_dump() for t in run.tasks],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "final_report": run.final_report,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
async def list_swarm_runs():
    """List all swarm runs."""
    try:
        runtime = get_swarm_runtime()
        runs = runtime._store.list_runs()
        
        return {
            "runs": [
                {
                    "id": r.id,
                    "preset_name": r.preset_name,
                    "status": r.status.value,
                    "created_at": r.created_at,
                    "completed_at": r.completed_at,
                }
                for r in runs
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/runs/{run_id}/cancel")
async def cancel_swarm_run(run_id: str):
    """Cancel an active swarm run."""
    try:
        runtime = get_swarm_runtime()
        ok = runtime.cancel_run(run_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"No active run {run_id}")
        return {"status": "cancelled"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
