from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from sag_agent.runtime import Agent, AgentRuntime, RuntimeConfig
from sag_agent.types import AgentEvent, AgentMessage


async def agent_loop(
    agent: Agent,
    input: str | AgentMessage | Mapping[str, Any] | None = None,
    *,
    history: Sequence[AgentMessage | Mapping[str, Any]] = (),
    context: Any = None,
    config: RuntimeConfig | None = None,
) -> AsyncIterator[AgentEvent]:
    """Low-level one-run event stream.

    Use AgentRuntime directly when the caller needs cancellation, approvals, replay,
    multiple concurrent runs, or access to the final RunResult.
    """

    async with AgentRuntime(config) as runtime:
        handle = runtime.run(agent, input, history=history, context=context)
        async for event in handle:
            yield event
