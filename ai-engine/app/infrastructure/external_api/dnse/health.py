"""
Health monitoring for DNSE Stream Hub.

Tracks message flow per channel, detects silent connections,
and provides detailed health status for production monitoring.
"""

import time
import threading
from typing import Dict, Optional
from collections import defaultdict


class ChannelHealthTracker:
    def __init__(self, stale_threshold: float = 30.0) -> None:
        self._stale_threshold = stale_threshold
        self._channels: Dict[str, float] = {}
        self._totals: Dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._start_time = time.time()

    def record_message(self, channel: str) -> None:
        with self._lock:
            self._channels[channel] = time.time()
            self._totals[channel] += 1

    def get_status(self) -> Dict[str, dict]:
        now = time.time()
        with self._lock:
            result = {}
            for channel, last_at in self._channels.items():
                age = now - last_at
                result[channel] = {
                    "last_message_seconds_ago": round(age, 1),
                    "total_messages": self._totals[channel],
                    "healthy": age < self._stale_threshold,
                }
            return result

    @property
    def is_receiving_data(self) -> bool:
        now = time.time()
        with self._lock:
            for last_at in self._channels.values():
                if now - last_at < self._stale_threshold:
                    return True
            return False

    @property
    def uptime_seconds(self) -> float:
        return round(time.time() - self._start_time, 1)

    @property
    def total_messages(self) -> int:
        with self._lock:
            return sum(self._totals.values())

    @property
    def active_channels(self) -> int:
        now = time.time()
        with self._lock:
            return sum(
                1 for last_at in self._channels.values()
                if now - last_at < self._stale_threshold
            )
