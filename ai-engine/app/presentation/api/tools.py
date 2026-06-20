"""
Tools Router - Uses Vibe-Trading tools for agent execution
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from pathlib import Path

router = APIRouter(tags=["Tools"])


class ToolRequest(BaseModel):
    """Tool execution request."""
    
    tool_name: str = Field(..., description="Tool name")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Tool parameters")


class ToolResponse(BaseModel):
    """Tool execution response."""
    
    tool_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.get("/list")
async def list_tools():
    """List available tools from Vibe-Trading."""
    tools_dir = Path(__file__).parent.parent / "core" / "tools"
    
    # List tool files
    tool_files = [
        "backtest_tool.py",
        "doc_reader_tool.py",
        "factor_analysis_tool.py",
        "hypothesis_tool.py",
        "pattern_tool.py",
        "web_reader_tool.py",
        "web_search_tool.py",
    ]
    
    available_tools = []
    for tool_file in tool_files:
        tool_path = tools_dir / tool_file
        if tool_path.exists():
            tool_name = tool_file.replace("_tool.py", "").replace(".py", "")
            available_tools.append({
                "name": tool_name,
                "file": tool_file,
            })
    
    return {"tools": available_tools}


@router.post("/execute", response_model=ToolResponse)
async def execute_tool(request: ToolRequest):
    """
    Execute a tool using Vibe-Trading tool system.
    
    Args:
        request: Tool execution request
        
    Returns:
        Tool execution result
    """
    try:
        from app.brain.tools import build_registry
        
        # Build tool registry
        tool_registry = build_registry(
            include_shell_tools=False,
        )
        
        # Get tool from registry
        tool = tool_registry.get_tool(request.tool_name)
        if not tool:
            raise HTTPException(status_code=404, detail=f"Tool {request.tool_name} not found")
        
        # Execute tool with parameters
        result = tool.execute(**(request.parameters or {}))
        
        return ToolResponse(
            tool_name=request.tool_name,
            status="success",
            result={"output": result},
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
