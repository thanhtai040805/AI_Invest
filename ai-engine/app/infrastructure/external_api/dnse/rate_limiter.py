"""
Token Bucket Rate Limiter for Redis publish.

Prevents Redis flood from high-frequency DNSE WebSocket ticks.
Uses per-symbol token buckets with configurable refill rates.
"""

import time
import threading
from typing import Dict, Optional


class TokenBucket:
    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class RateLimitedPublisher:
    def __init__(
        self,
        high_freq_rate: float = 10.0,
        high_freq_capacity: float = 20.0,
        low_freq_rate: float = 2.0,
        low_freq_capacity: float = 5.0,
    ) -> None:
        self._high_freq_rate = high_freq_rate
        self._high_freq_capacity = high_freq_capacity
        self._low_freq_rate = low_freq_rate
        self._low_freq_capacity = low_freq_capacity
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

        self._dropped_count = 0
        self._published_count = 0
        self._lock_stats = threading.Lock()

    def _get_bucket(self, channel: str) -> TokenBucket:
        if channel not in self._buckets:
            high_freq_channels = {"trade:", "orderbook:", "quote:", "ohlc:"}
            is_high_freq = any(channel.startswith(p) for p in high_freq_channels)

            with self._lock:
                if channel not in self._buckets:
                    if is_high_freq:
                        self._buckets[channel] = TokenBucket(
                            self._high_freq_rate, self._high_freq_capacity
                        )
                    else:
                        self._buckets[channel] = TokenBucket(
                            self._low_freq_rate, self._low_freq_capacity
                        )
        return self._buckets[channel]

    def should_publish(self, channel: str) -> bool:
        bucket = self._get_bucket(channel)
        allowed = bucket.consume()
        with self._lock_stats:
            if allowed:
                self._published_count += 1
            else:
                self._dropped_count += 1
        return allowed

    @property
    def stats(self) -> dict:
        with self._lock_stats:
            return {
                "published": self._published_count,
                "dropped": self._dropped_count,
                "drop_rate": (
                    self._dropped_count / (self._published_count + self._dropped_count)
                    if (self._published_count + self._dropped_count) > 0
                    else 0.0
                ),
                "active_buckets": len(self._buckets),
            }

    def reset_stats(self) -> None:
        with self._lock_stats:
            self._dropped_count = 0
            self._published_count = 0
