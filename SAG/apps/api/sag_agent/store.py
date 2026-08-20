from __future__ import annotations

import asyncio
from collections import deque
from typing import Protocol, runtime_checkable

from sag_agent.types import AgentEvent, RunResult


@runtime_checkable
class RunStore(Protocol):
    """Persistence port. Implementations may use memory, SQL, Redis, or a remote API."""

    async def create(self, run_id: str) -> None: ...

    async def append(self, event: AgentEvent) -> None: ...

    async def finish(self, result: RunResult) -> None: ...

    async def events(self, run_id: str, *, after: int = 0) -> list[AgentEvent]: ...

    async def result(self, run_id: str) -> RunResult | None: ...


class MemoryRunStore:
    """Default store for embedded use and tests."""

    def __init__(self, *, max_runs: int | None = 1000) -> None:
        if max_runs is not None and max_runs < 1:
            raise ValueError("max_runs must be positive or None")
        self.max_runs = max_runs
        self._events: dict[str, list[AgentEvent]] = {}
        self._results: dict[str, RunResult] = {}
        self._finished: deque[str] = deque()
        self._lock = asyncio.Lock()

    async def create(self, run_id: str) -> None:
        async with self._lock:
            if run_id in self._events:
                raise ValueError(f"run already exists: {run_id}")
            self._events[run_id] = []

    async def append(self, event: AgentEvent) -> None:
        async with self._lock:
            events = self._events.setdefault(event.run_id, [])
            if events and event.sequence <= events[-1].sequence:
                raise ValueError("event sequence must increase monotonically")
            events.append(event)

    async def finish(self, result: RunResult) -> None:
        async with self._lock:
            self._results[result.run_id] = result
            self._finished.append(result.run_id)
            if self.max_runs is not None:
                while len(self._finished) > self.max_runs:
                    expired = self._finished.popleft()
                    self._events.pop(expired, None)
                    self._results.pop(expired, None)

    async def events(self, run_id: str, *, after: int = 0) -> list[AgentEvent]:
        async with self._lock:
            return [event for event in self._events.get(run_id, ()) if event.sequence > after]

    async def result(self, run_id: str) -> RunResult | None:
        async with self._lock:
            return self._results.get(run_id)
