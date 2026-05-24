"""
Session Router — delegates to the shared SessionService singleton.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.brain.lifespan import get_session_service
from app.brain.state.models import Session

router = APIRouter(tags=["Session"])


class SessionCreateRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="User ID")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Session metadata")


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def _svc(request: Request):
    svc = request.app.state.session_service
    if svc is None:
        raise HTTPException(status_code=503, detail="SessionService not initialized")
    return svc


@router.post("/create", response_model=SessionCreateResponse)
async def create_session(body: SessionCreateRequest, request: Request):
    svc = _svc(request)
    config = body.metadata or {}
    session = svc.create_session(title="", config=config)
    return SessionCreateResponse(
        session_id=session.session_id,
        status=session.status.value,
        user_id=body.user_id,
        metadata=config,
    )


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    svc = _svc(request)
    session = svc.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.model_dump()


@router.get("/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request):
    svc = _svc(request)
    session = svc.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = svc.get_messages(session_id)
    return {
        "session_id": session_id,
        "messages": [m.model_dump() for m in msgs],
    }


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    svc = _svc(request)
    ok = svc.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


@router.get("/", response_model=List[Dict[str, Any]])
async def list_sessions(request: Request):
    svc = _svc(request)
    sessions = svc.list_sessions(limit=50)
    return [s.model_dump() for s in sessions]
