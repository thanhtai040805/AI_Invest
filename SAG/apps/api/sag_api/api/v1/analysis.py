from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.db.models import User
from sag_api.schemas.document_v2 import GILAssessmentOut, MoatAssessmentOut
from sag_api.services.analysis_v2_service import assess_gil_by_ticker, assess_moat_by_ticker

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/by-ticker/{ticker}/moat", response_model=MoatAssessmentOut)
async def get_moat_by_ticker(
    ticker: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await assess_moat_by_ticker(session, ticker)


@router.get("/by-ticker/{ticker}/gil", response_model=GILAssessmentOut)
async def get_gil_by_ticker(
    ticker: str,
    equity_vnd: float | None = Query(default=None, ge=0.0),
    equity_provenance: str | None = Query(default=None),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await assess_gil_by_ticker(
        session,
        ticker,
        equity_override_vnd=equity_vnd,
        equity_provenance=equity_provenance,
    )
