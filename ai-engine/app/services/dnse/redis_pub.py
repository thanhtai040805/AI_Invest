"""Publish DNSE market events to Redis for the Node.js backend relay."""

import json
from typing import Any, Optional, List

import redis

from app.config import REDIS_URL, REDIS_CHANNEL_PREFIX

_client: Optional[redis.Redis] = None


def _channel(suffix: str) -> str:
    return f"{REDIS_CHANNEL_PREFIX}:{suffix}"


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
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