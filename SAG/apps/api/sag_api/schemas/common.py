from __future__ import annotations

from pydantic import BaseModel


class Ok(BaseModel):
    ok: bool = True
    detail: str = ""


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
