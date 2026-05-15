"""Publish DNSE market events to Redis for the Node.js backend relay."""

import json
from typing import Any, Optional

import redis

from app.config.settings import get_settings

_client: Optional[redis.Redis] = None


def _channel(suffix: str) -> str:
    return f"{get_settings().redis_channel_prefix}:{suffix}"


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def publish_json(suffix: str, payload: Any) -> None:
    try:
        get_redis().publish(_channel(suffix), json.dumps(payload, default=str))
    except Exception as e:
        print(f"[DNSE Redis] publish {suffix} failed: {e}")


def set_cache(key: str, payload: Any, ttl: int = 5) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(payload, default=str))
    except Exception as e:
        print(f"[DNSE Redis] set cache {key} failed: {e}")
