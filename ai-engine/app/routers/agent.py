"""
Agent Router — wired to SessionService + AgentLoop for chat + backtest runs.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.brain.lifespan import get_session_service
from app.brain.state.service import SessionService

router = APIRouter(tags=["Agent"])


# ── Request / Response models ──


class AgentRunRequest(BaseModel):
    input: str = Field(..., description="User message")
    stream: bool = False
    session_id: Optional[str] = Field(None, description="Existing session ID (omit to create new)")


class AgentRunResponse(BaseModel):
    session_id: str
    status: str
    message_id: Optional[str] = None
    attempt_id: Optional[str] = None


class CancelResponse(BaseModel):
    status: str
    cancelled: bool


class UploadResponse(BaseModel):
    filename: str
    file_path: str
    size: int


class FileUploadResponse(BaseModel):
    filename: str
    file_path: str
    size: int


# ── Dependency ──


def _svc(request: Request) -> SessionService:
    svc: SessionService = request.app.state.session_service
    if svc is None:
        raise HTTPException(status_code=503, detail="SessionService not initialized")
    return svc


# ── Endpoints ──


@router.post("/run", response_model=AgentRunResponse)
async def agent_run(body: AgentRunRequest, request: Request):
    """Create or reuse a session, send a message, and trigger agent execution."""
    svc = _svc(request)
    try:
        if body.session_id:
            session = svc.get_session(body.session_id)
            if not session:
                raise HTTPException(status_code=404, detail=f"Session {body.session_id} not found")
            session_id = body.session_id
        else:
            session = svc.create_session(title=body.input[:60])
            session_id = session.session_id

        result = await svc.send_message(
            session_id=session_id,
            content=body.input,
            role="user",
            include_shell_tools=False,
        )
        return AgentRunResponse(
            session_id=session_id,
            status="streaming",
            message_id=result.get("message_id"),
            attempt_id=result.get("attempt_id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/run/{session_id}/stream")
async def agent_stream(session_id: str, request: Request):
    """SSE stream for a session's agent execution events."""
    svc = _svc(request)
    session = svc.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    last_event_id: Optional[str] = request.headers.get("last-event-id")

    async def event_generator():
        async for event in svc.event_bus.subscribe(session_id, last_event_id=last_event_id):
            yield event.to_sse()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/cancel/{session_id}", response_model=CancelResponse)
async def agent_cancel(session_id: str, request: Request):
    """Cancel the currently running agent loop for a session."""
    svc = _svc(request)
    cancelled = svc.cancel_current(session_id)
    return CancelResponse(status="ok" if cancelled else "no_active_loop", cancelled=cancelled)


@router.get("/sessions/{session_id}/messages")
async def agent_session_messages(session_id: str, request: Request):
    """Return message history for a session."""
    svc = _svc(request)
    session = svc.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = svc.get_messages(session_id)
    # Serialize to dict for JSON response
    return [m.model_dump() for m in messages]


@router.post("/upload", response_model=UploadResponse)
async def agent_upload(request: Request, file: UploadFile = File(...)):
    """Upload a file (PDF, CSV, etc.) for the agent to use."""
    svc = _svc(request)
    upload_dir = svc.runs_dir / ".uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex[:12]}_{file.filename or 'file'}"
    dest = upload_dir / safe_name

    content = await file.read()
    dest.write_bytes(content)

    return UploadResponse(
        filename=file.filename or safe_name,
        file_path=str(dest),
        size=len(content),
    )


@router.get("/sessions", response_model=List[Dict[str, Any]])
async def list_sessions(request: Request):
    """Return all active sessions."""
    svc = _svc(request)
    sessions = svc.list_sessions(limit=50)
    result = []
    for s in sessions:
        d = s.model_dump()
        d["message_count"] = len(svc.get_messages(s.session_id, limit=1))
        result.append(d)
    return result
