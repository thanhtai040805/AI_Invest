"""
Hypotheses Router - Hypothesis testing integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Hypotheses"])


class HypothesisRequest(BaseModel):
    """Hypothesis testing request."""
    
    hypothesis_name: str = Field(..., description="Hypothesis name")
    symbol: str = Field(..., description="Stock symbol")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Hypothesis parameters")


class HypothesisResponse(BaseModel):
    """Hypothesis response."""
    
    hypothesis_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.get("/list")
async def list_hypotheses():
    """List available hypotheses from Vibe-Trading."""
    hypotheses_dir = Path(__file__).parent.parent / "analysis" / "hypotheses"
    
    # List hypothesis files
    hypothesis_files = [
        "momentum_hypothesis.py",
        "mean_reversion_hypothesis.py",
        "factor_hypothesis.py",
    ]
    
    available_hypotheses = []
    for hypothesis_file in hypothesis_files:
        hypothesis_path = hypotheses_dir / hypothesis_file
        if hypothesis_path.exists():
            hypothesis_name = hypothesis_file.replace("_hypothesis.py", "").replace(".py", "")
            available_hypotheses.append({
                "name": hypothesis_name,
                "file": hypothesis_file,
            })
    
    return {"hypotheses": available_hypotheses}


@router.post("/test", response_model=HypothesisResponse)
async def test_hypothesis(request: HypothesisRequest):
    """
    Test a hypothesis using Vibe-Trading.
    
    Args:
        request: Hypothesis testing request
        
    Returns:
        Hypothesis test result
    """
    try:
        from app.quant.hypotheses import test_hypothesis
        
        # Test hypothesis
        result = test_hypothesis(
            hypothesis_name=request.hypothesis_name,
            symbol=request.symbol,
            **(request.parameters or {}),
        )
        
        return HypothesisResponse(
            hypothesis_name=request.hypothesis_name,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
