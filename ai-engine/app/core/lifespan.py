"""FastAPI lifespan — start/stop DNSE WebSocket hub."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from app.services.news_ingestion import get_news_ingestion_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_stream_hub()
    news_service = get_news_ingestion_service()
    
    hub.start()
    news_service.start()
    yield
    hub.stop()
    news_service.stop()
    get_rest_client().close()
