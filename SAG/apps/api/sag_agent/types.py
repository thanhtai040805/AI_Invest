from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class RuntimeStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    TURN_STARTED = "turn.started"
    TURN_COMPLETED = "turn.completed"
    MESSAGE_STARTED = "message.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    TOOL_STARTED = "tool.started"
    TOOL_PROGRESS = "tool.progress"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"


class ToolExecutionMode(StrEnum):
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolDecisionAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class CancellationToken:
    """Cooperative cancellation token shared by model and tool adapters."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str | None = None

    def to_model_dict(self) -> dict[str, Any]:
        import json

        raw = self.raw_arguments or json.dumps(dict(self.arguments), ensure_ascii=False)
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": raw},
        }


@dataclass(frozen=True, slots=True)
class AgentMessage:
    role: str
    content: Any = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AgentMessage:
        calls: list[ToolCall] = []
        for raw in value.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments") or "{}"
            parsed: Mapping[str, Any] = {}
            parse_error = None
            if isinstance(arguments, str):
                try:
                    import json

                    candidate = json.loads(arguments)
                    if isinstance(candidate, dict):
                        parsed = candidate
                    else:
                        parse_error = "tool arguments must decode to an object"
                except (TypeError, ValueError) as exc:
                    parse_error = str(exc)
            elif isinstance(arguments, dict):
                parsed = arguments
            else:
                parse_error = "tool arguments must be an object or JSON string"
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=parsed,
                    raw_arguments=arguments if isinstance(arguments, str) else "",
                    parse_error=parse_error,
                )
            )
        return cls(
            role=str(value.get("role") or "user"),
            content=value.get("content", ""),
            tool_calls=tuple(calls),
            tool_call_id=value.get("tool_call_id"),
            name=value.get("name"),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_model_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            value["tool_calls"] = [call.to_model_dict() for call in self.tool_calls]
        if self.tool_call_id:
            value["tool_call_id"] = self.tool_call_id
        if self.name:
            value["name"] = self.name
        return value


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    label: str | None = None
    risk: ToolRisk = ToolRisk.READ_ONLY
    requires_approval: bool = False
    execution_mode: ToolExecutionMode | None = None
    timeout_seconds: float | None = None

    def to_model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[AgentMessage, ...]
    tools: tuple[Mapping[str, Any], ...] = ()
    tool_choice: str | Mapping[str, Any] | None = None
    run_id: str = ""
    turn: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: Usage) -> Usage:
        return Usage(
            requests=self.requests + other.requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass(frozen=True, slots=True)
class ModelChunk:
    text_delta: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None


@runtime_checkable
class ModelProvider(Protocol):
    def stream_turn(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelChunk]: ...


@dataclass(frozen=True, slots=True)
class ToolProgress:
    message: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    details: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    terminate: bool = False


@dataclass(frozen=True, slots=True)
class ToolDecision:
    action: ToolDecisionAction
    reason: str = ""

    @classmethod
    def allow(cls) -> ToolDecision:
        return cls(ToolDecisionAction.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> ToolDecision:
        return cls(ToolDecisionAction.DENY, reason)

    @classmethod
    def require_approval(cls, reason: str = "") -> ToolDecision:
        return cls(ToolDecisionAction.REQUIRE_APPROVAL, reason)


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: EventType
    run_id: str
    sequence: int
    timestamp: datetime
    turn: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type.value,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            "turn": self.turn,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class AgentError:
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    status: RunStatus
    output: str
    messages: tuple[AgentMessage, ...]
    usage: Usage = Usage()
    error: AgentError | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def normalize_messages(values: Sequence[AgentMessage | Mapping[str, Any]]) -> list[AgentMessage]:
    return [value if isinstance(value, AgentMessage) else AgentMessage.from_dict(value) for value in values]


def utc_now() -> datetime:
    return datetime.now(UTC)
