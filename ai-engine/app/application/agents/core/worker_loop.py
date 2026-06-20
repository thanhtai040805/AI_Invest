"""WorkerAgentLoop — AgentLoop subclass with swarm worker features.

Extends AgentLoop with:
- Data tool call counting (for deliverable classification)
- Artifact management (summary.md, messages.json)
- Deliverable classification (report.md check, data evidence)
- SwarmEvent-compatible callback forwarding
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.application.agents.core.loop import AgentLoop
from app.application.agents.core.tools import ToolRegistry
from app.brain.providers.chat import ChatLLM

logger = logging.getLogger(__name__)

_GENERIC_TOOLS = {"bash", "read_file", "write_file", "load_skill", "edit_file"}


def _is_error_result(result: str) -> bool:
    text = (result or "").strip()
    if not text or not text.startswith("{"):
        return False
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        head = text[:160].lower()
        return '"status": "error"' in head or '"status":"error"' in head
    return isinstance(parsed, dict) and parsed.get("status") == "error"


class WorkerAgentLoop(AgentLoop):
    """AgentLoop extended for swarm worker task execution.

    Adds:
      - Data tool call counting for deliverable contract
      - Artifact writing (summary.md, messages.json)
      - report.md detection
      - Configurable worker event callback forwarding
    """

    def __init__(
        self,
        registry: ToolRegistry,
        llm: ChatLLM,
        max_iterations: int = 50,
        timeout_seconds: Optional[int] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        worker_callback: Optional[Callable[[str, str, str, Dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(
            registry=registry,
            llm=llm,
            max_iterations=max_iterations,
            timeout_seconds=timeout_seconds,
            event_callback=event_callback,
            worker_callback=worker_callback,
        )
        self.data_tool_calls = 0

    def _finalize_tool_result(
        self,
        tc: Any,
        result: str,
        elapsed_ms: int,
        context: Any,
        messages: list,
        trace: Any,
        react_trace: list,
        iteration: int,
    ) -> None:
        super()._finalize_tool_result(tc, result, elapsed_ms, context, messages, trace, react_trace, iteration)
        # Count non-generic tool calls for deliverable classification
        if tc.name not in _GENERIC_TOOLS and not _is_error_result(result):
            self.data_tool_calls += 1

    @staticmethod
    def report_written(artifact_dir: Path) -> bool:
        try:
            p = artifact_dir / "report.md"
            return p.is_file() and bool(p.read_text(encoding="utf-8").strip())
        except Exception:
            return False

    @staticmethod
    def is_data_agent(tools: List[str]) -> bool:
        return bool(set(tools or []) - _GENERIC_TOOLS)

    @staticmethod
    def classify_deliverable(
        summary: str,
        *,
        is_data_agent: bool,
        report_written: bool,
        data_tool_calls: int,
    ) -> Optional[str]:
        text = (summary or "").strip()
        if not text:
            return "empty deliverable"
        low = text.lower()

        markers = (
            "<\uff5ctool\u2581calls\u2581begin\uff5c>",
            "<tool_calls_begin>", "<tool_call_begin>", "<tool_sep>",
            "tool\u2581sep",
        )
        if any(m in low for m in markers):
            return "unparsed tool-call markup (provider did not parse tool calls)"

        fabrication = ("mock data", "without actual data", "fabricated data", "placeholder data")
        if any(m in low for m in fabrication):
            return "explicitly fabricated / mock data"

        if text.startswith("{") and '"status"' in text[:40] and (
            '"content"' in text[:300] or '"ok"' in text[:40]
        ):
            return "raw tool-result envelope, not analysis"

        if low.startswith(("# phase 1", "## phase 1", "phase 1 — plan", "phase 1 - plan", "# plan", "## plan")):
            plan_prefixes = ("# phase 1", "## phase 1", "### phase 1",
                            "phase 1 — plan", "phase 1 - plan", "phase 1: plan",
                            "# plan", "## plan", "### plan", "**plan**")
            if any(low.startswith(p) for p in plan_prefixes):
                tail = low.rsplit("phase 2", 1)[-1].strip() if "phase 2" in low else ""
                handoff_tails = ("execute", "execute.", "execute:", "skills.", "skills",
                                 "proceed?", "proceed.", "without writing files.",
                                 "let me adjust the approach", "stand by for final synthesis.")
                if len(text) < 600 or low.rstrip().endswith(handoff_tails) or (
                    "phase 2" in low and len(tail) < 80
                ):
                    return "plan-only stub (no executed analysis / conclusion)"

        if is_data_agent and not report_written and data_tool_calls == 0:
            return "data agent produced no tool calls and no report.md"

        return None

    def write_artifacts(self, artifact_dir: Path, messages: list, summary: str) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        summary_path = artifact_dir / "summary.md"
        try:
            summary_path.write_text(summary, encoding="utf-8")
        except Exception:
            logger.warning("Failed to write summary to %s", summary_path)

        msg_path = artifact_dir / "messages.json"
        try:
            msg_path.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("Failed to persist messages to %s", msg_path)

    @staticmethod
    def resolve_summary(artifact_dir: Path, fallback: str) -> str:
        report_path = artifact_dir / "report.md"
        try:
            if report_path.is_file():
                content = report_path.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except Exception:
            pass
        return fallback

    @staticmethod
    def collect_artifacts(artifact_dir: Path) -> list[str]:
        if not artifact_dir.exists():
            return []
        return [str(p) for p in artifact_dir.iterdir() if p.is_file()]
