"""Publish DNSE market events to Redis for the Node.js backend relay."""

import json
import time
import threading
from typing import Any, Optional, List

import redis

from app.config.settings import get_settings
_client: Optional[redis.Redis] = None
_publish_stats = {"published": 0, "failed": 0}
_stats_lock = threading.Lock()


def _channel(suffix: str) -> str:
    return f"{get_settings().redis_channel_prefix}:{suffix}"


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _client


def get_publish_stats() -> dict:
    with _stats_lock:
        return dict(_publish_stats)


def publish_json(suffix: str, payload: Any) -> None:
    try:
        get_redis().publish(_channel(suffix), json.dumps(payload, default=str))
        with _stats_lock:
            _publish_stats["published"] += 1
    except Exception as e:
        with _stats_lock:
            _publish_stats["failed"] += 1
        print(f"[DNSE Redis] publish {suffix} failed: {e}")


def publish_batch(items: list[tuple[str, Any]]) -> None:
    """Publish multiple events atomically via pipeline."""
    try:
        r = get_redis()
        pipe = r.pipeline()
        for suffix, payload in items:
            channel = _channel(suffix)
            pipe.publish(channel, json.dumps(payload, default=str))
        pipe.execute()
        with _stats_lock:
            _publish_stats["published"] += len(items)
    except Exception as e:
        with _stats_lock:
            _publish_stats["failed"] += len(items)
        print(f"[DNSE Redis] batch publish failed: {e}")


def set_cache(key: str, payload: Any, ttl: int = 5) -> None:
    try:
        get_redis().setex(key, ttl, json.dumps(payload, default=str))
    except Exception as e:
        print(f"[DNSE Redis] set cache {key} failed: {e}")


def set_hash(key: str, field: str, value: Any, ttl: int = 5) -> None:
    """Set a field in a Redis Hash."""
    try:
        r = get_redis()
        r.hset(key, field, json.dumps(value, default=str))
        if ttl > 0:
            r.expire(key, ttl)
    except Exception as e:
        print(f"[DNSE Redis] set hash {key}:{field} failed: {e}")


def get_hash(key: str, field: str) -> Optional[dict]:
    """Get a field from a Redis Hash."""
    try:
        r = get_redis()
        data = r.hget(key, field)
        if data:
            return json.loads(data)
    except Exception as e:
        print(f"[DNSE Redis] get hash {key}:{field} failed: {e}")
    return None


def push_to_list(key: str, value: Any, max_len: int = 100, ttl: int = 300) -> None:
    """Push value to Redis List (LPUSH) with optional trim to max_len."""
    try:
        r = get_redis()
        r.lpush(key, json.dumps(value, default=str))
        r.ltrim(key, 0, max_len - 1)
        if ttl > 0:
            r.expire(key, ttl)
    except Exception as e:
        print(f"[DNSE Redis] push list {key} failed: {e}")


def get_list_range(key: str, start: int = 0, end: int = -1) -> List[dict]:
    """Get range of values from Redis List."""
    try:
        r = get_redis()
        items = r.lrange(key, start, end)
        return [json.loads(item) for item in items]
    except Exception as e:
        print(f"[DNSE Redis] get list {key} failed: {e}")
        return []


def add_to_sorted_set(key: str, score: float, member: Any, ttl: int = 3600) -> None:
    """Add member to Redis Sorted Set (ZADD)."""
    try:
        r = get_redis()
        r.zadd(key, {json.dumps(member, default=str): score})
        if ttl > 0:
            r.expire(key, ttl)
    except Exception as e:
        print(f"[DNSE Redis] zadd {key} failed: {e}")


def get_sorted_set_range(
    key: str,
    min_score: float = "-inf",
    max_score: float = "+inf",
    with_scores: bool = False
) -> List[Any]:
    """Get range from Redis Sorted Set (ZRANGEBYSCORE)."""
    try:
        r = get_redis()
        if with_scores:
            return r.zrangebyscore(key, min_score, max_score, withscores=True)
        items = r.zrangebyscore(key, min_score, max_score)
        return [json.loads(item) for item in items]
    except Exception as e:
        print(f"[DNSE Redis] zrange {key} failed: {e}")
        return []


def remove_from_sorted_set(key: str, member: Any) -> None:
    """Remove member from Redis Sorted Set (ZREM)."""
    try:
        r = get_redis()
        r.zrem(key, json.dumps(member, default=str))
    except Exception as e:
        print(f"[DNSE Redis] zrem {key} failed: {e}")


def trim_sorted_set_by_score(key: str, max_score: float, min_score: float = "-inf") -> None:
    """Remove all members with score > max_score (older data)."""
    try:
        r = get_redis()
        r.zremrangebyscore(key, min_score, max_score)
    except Exception as e:
        print(f"[DNSE Redis] zremrangebyscore {key} failed: {e}")


def add_to_stream(key: str, payload: Any, max_len: int = 10000) -> None:
    """Add event to Redis Stream for durable replay."""
    try:
        r = get_redis()
        r.xadd(key, payload, maxlen=max_len, approximate=True)
    except Exception as e:
        print(f"[DNSE Redis] xadd {key} failed: {e}")
