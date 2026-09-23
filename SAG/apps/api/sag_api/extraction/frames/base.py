from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Type


@dataclass
class EvidenceReference:
    line_start: int
    line_end: int
    quote: str
    node_id: str | None = None


class BaseFinancialFrame(ABC):
    """Base class for all typed financial semantic frames."""

    frame_name: str = "base_frame"
    evidence: EvidenceReference

    @abstractmethod
    def to_relations(self) -> list[dict[str, Any]]:
        """Convert frame into graph relations compatible with SAG database schema."""
        return []

    @abstractmethod
    def to_facts(self) -> list[dict[str, Any]]:
        """Convert frame into facts compatible with SAG database schema."""
        return []

    @abstractmethod
    def to_observations(self) -> list[dict[str, Any]]:
        """Convert frame into narrative/forensic observations."""
        return []


class FrameRegistry:
    """Registry maintaining available semantic financial frames."""

    _frames: dict[str, Type[BaseFinancialFrame]] = {}

    @classmethod
    def register(cls, frame_cls: Type[BaseFinancialFrame]) -> Type[BaseFinancialFrame]:
        cls._frames[frame_cls.frame_name] = frame_cls
        return frame_cls

    @classmethod
    def get(cls, name: str) -> Type[BaseFinancialFrame] | None:
        return cls._frames.get(name)

    @classmethod
    def all_frames(cls) -> dict[str, Type[BaseFinancialFrame]]:
        return dict(cls._frames)
