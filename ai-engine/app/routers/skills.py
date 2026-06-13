"""
Skills Router - Uses Vibe-Trading skills for stock analysis
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import json
from pathlib import Path

router = APIRouter(tags=["Skills"])


class SkillRequest(BaseModel):
    """Skill execution request."""
    
    skill_name: str = Field(..., description="Skill name (e.g., technical-basic, candlestick)")
    symbol: str = Field(..., description="Stock symbol (e.g., VCB)")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Skill parameters")


class SkillResponse(BaseModel):
    """Skill execution response."""
    
    skill_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.get("/list")
async def list_skills():
    """List available skills for Vietnam market."""
    from app.quant.skills import SKILLS_DIR as skills_dir
    
    # Filter skills relevant to stock trading
    vn_relevant_skills = [
        "technical-basic",
        "candlestick",
        "ichimoku",
        "chanlun",
        "elliott-wave",
        "harmonic",
        "smc",
        "factor-research",
        "risk-analysis",
        "valuation-model",
        "earnings-forecast",
        "dividend-analysis",
    ]
    
    available_skills = []
    for skill_name in vn_relevant_skills:
        skill_path = skills_dir / skill_name
        if skill_path.exists():
            available_skills.append({
                "name": skill_name,
                "path": str(skill_path),
            })
    
    return {"skills": available_skills}


@router.post("/execute", response_model=SkillResponse)
async def execute_skill(request: SkillRequest):
    """
    Execute a skill using Vibe-Trading skill system.
    
    Args:
        request: Skill execution request with skill name and parameters
        
    Returns:
        Skill execution result
    """
    try:
        # Import Vibe-Trading skill system
        from app.quant.skills import load_skill
        
        # Load and execute skill
        skill = load_skill(request.skill_name)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill {request.skill_name} not found")
        
        # Execute skill with parameters
        result = skill.execute(
            symbol=request.symbol,
            **(request.parameters or {}),
        )
        
        return SkillResponse(
            skill_name=request.skill_name,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/presets")
async def list_skill_presets():
    """List skill presets for common analysis tasks."""
    presets = [
        {
            "name": "technical_analysis",
            "description": "Basic technical analysis",
            "skills": ["technical-basic", "candlestick"],
        },
        {
            "name": "advanced_patterns",
            "description": "Advanced pattern recognition",
            "skills": ["ichimoku", "elliott-wave", "harmonic"],
        },
        {
            "name": "fundamental_analysis",
            "description": "Fundamental analysis",
            "skills": ["valuation-model", "earnings-forecast", "dividend-analysis"],
        },
        {
            "name": "risk_assessment",
            "description": "Risk analysis",
            "skills": ["risk-analysis", "factor-research"],
        },
    ]
    
    return {"presets": presets}
