from fastapi import APIRouter

from sag_api.api.v2 import financial

api_router = APIRouter(prefix="/api/v2")
api_router.include_router(financial.router)

__all__ = ["api_router"]
