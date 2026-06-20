"""
Memory Router - Agent memory management
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import uuid

router = APIRouter(tags=["Memory"])


class MemoryEntry(BaseModel):
    """Memory entry."""
    
    key: str = Field(..., description="Memory key")
    value: Any = Field(..., description="Memory value")
    timestamp: Optional[str] = Field(None, description="Timestamp")


class MemoryResponse(BaseModel):
    """Memory response."""
    
    status: str
    entry: Optional[MemoryEntry] = None
    error: Optional[str] = None


# In-memory memory storage (in production, use database)
memory_store: Dict[str, Dict[str, Any]] = {}


@router.post("/store")
async def store_memory(entry: MemoryEntry):
    """
    Store a memory entry.
    
    Args:
        entry: Memory entry to store
        
    Returns:
        Storage result
    """
    try:
        from app.brain.memory.persistent import PersistentMemory
        
        memory_store[entry.key] = {
            "value": entry.value,
            "timestamp": entry.timestamp or "2026-05-23T00:00:00Z",
        }
        
        return MemoryResponse(
            status="stored",
            entry=entry,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{key}")
async def get_memory(key: str):
    """Get a memory entry."""
    if key not in memory_store:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory_store[key]


@router.delete("/{key}")
async def delete_memory(key: str):
    """Delete a memory entry."""
    if key not in memory_store:
        raise HTTPException(status_code=404, detail="Memory not found")
    del memory_store[key]
    return {"status": "deleted"}


@router.get("/")
async def list_memory():
    """List all memory entries."""
    return {"memory": list(memory_store.keys())}
