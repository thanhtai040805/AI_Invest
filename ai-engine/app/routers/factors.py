"""Factors Router — VN factor computation and metadata (zoo removed)."""

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Factors"])


@router.get("/list")
async def list_factors():
    """List all registered factors (VN-core)."""
    from app.brain.quant.factors.vn_ic_tester import VN_FACTORS
    factors = [
        {"alpha_id": fid, "group": meta["group"], "direction": meta["direction"]}
        for fid, meta in VN_FACTORS.items()
    ]
    return {"factors": factors}


@router.get("/health")
async def factor_health():
    """VN factor registry health."""
    from app.brain.quant.factors.vn_ic_tester import VN_FACTORS
    return {"status": "ok", "n_vn_factors": len(VN_FACTORS)}
