"""
Screener router — filter stocks by financial/technical criteria.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.domain.services.screener_service import screener_svc

router = APIRouter()


@router.get("/presets/builtin")
async def builtin_presets():
    return screener_svc.get_builtin_presets()


class ScreenerFilter(BaseModel):
    exchange: Optional[str] = None
    peMin: Optional[float] = None
    peMax: Optional[float] = None
    pbMin: Optional[float] = None
    pbMax: Optional[float] = None
    roeMin: Optional[float] = None
    roeMax: Optional[float] = None
    rsiMin: Optional[float] = None
    rsiMax: Optional[float] = None
    marketCapMin: Optional[float] = None
    marketCapMax: Optional[float] = None
    deMin: Optional[float] = None
    deMax: Optional[float] = None
    volumeMin: Optional[float] = None
    sort: Optional[str] = None
    sortDir: Optional[str] = "desc"
    limit: Optional[int] = 50
    offset: Optional[int] = 0


@router.post("/filter")
async def filter_stocks(filters: ScreenerFilter):
    """Screen stocks based on multi-criteria filters."""
    try:
        return await screener_svc.screen(filters.model_dump(exclude_none=True))
    except Exception as e:
        return {"error": str(e), "stocks": []}
