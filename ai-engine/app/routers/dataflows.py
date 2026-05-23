"""Dataflows Router — expose trading/dataflows/interface.py tool dispatch via REST."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from app.brain.dataflows import TOOLS_CATEGORIES, route_to_vendor

router = APIRouter(tags=["Dataflows"])


class ExecuteRequest(BaseModel):
    method: str = Field(..., description="Tool method name, e.g. get_stock_data, get_fundamentals")
    args: List[Any] = Field(default_factory=list, description="Positional arguments")
    kwargs: Dict[str, Any] = Field(default_factory=dict, description="Keyword arguments")


class ExecuteResponse(BaseModel):
    method: str
    status: str
    result: Any = None
    error: Optional[str] = None


@router.get("/tools")
async def list_tools():
    """List available data tool categories and their methods."""
    return {"categories": TOOLS_CATEGORIES}


@router.post("/execute", response_model=ExecuteResponse)
async def execute_tool(request: ExecuteRequest):
    """Execute a data tool method via vendor-routed dispatch."""
    try:
        result = route_to_vendor(request.method, *request.args, **request.kwargs)
        return ExecuteResponse(method=request.method, status="success", result=result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
