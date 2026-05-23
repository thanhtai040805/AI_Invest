"""FastAPI lifespan — start/stop DNSE WebSocket hub."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from app.services.news_ingestion import get_news_ingestion_service
from app.database.models import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo SQLite database (tạo bảng nếu chưa tồn tại)
    init_db()

    hub = get_stream_hub()
    news_service = get_news_ingestion_service()
    
    hub.start()
    news_service.start()
    yield
    hub.stop()
    news_service.stop()
    get_rest_client().close()
