"""FastAPI lifespan — start/stop DNSE WebSocket hub."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_stream_hub()
    hub.start()
    yield
    hub.stop()
    get_rest_client().close()
