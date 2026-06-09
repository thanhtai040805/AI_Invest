"""Alpha routes — stub (zoo removed)."""

from fastapi import APIRouter

router = APIRouter(tags=["Alpha"])


@router.get("/alpha/list")
async def alpha_list():
    return {"factors": []}
