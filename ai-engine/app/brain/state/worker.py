"""Swarm Worker: worker execution engine backed by WorkerAgentLoop.

Delegates the ReAct loop to WorkerAgentLoop (a subclass of AgentLoop with
swarm-specific features: deliverable classification, data-tool tracking,
artifact management). The worker remains responsible for:
- Tool registry construction (filtered per agent spec)
- System prompt building (role + skills + upstream context)
- Injected run_dir for artifact persistence
- SwarmEvent callbacks (worker_started, worker_text, tool_call, etc.)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.brain.agents.core.skills import SkillsLoader
from app.brain.providers.chat import ChatLLM
from app.brain.state.models import (
    SwarmAgentSpec,
    SwarmEvent,
    SwarmTask,
    WorkerResult,
)
from app.brain.tools import build_filtered_registry

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ITERATIONS = int(os.getenv("SWARM_WORKER_MAX_ITER", "50"))
_DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SWARM_WORKER_TIMEOUT", "300"))


def _emit(
    callback: Callable[[SwarmEvent], None] | None,
    event_type: str,
    agent_id: str,
    task_id: str,
    data: dict | None = None,
) -> None:
    """Emit a swarm event via callback if provided.

    Args:
        callback: Optional event callback function.
        event_type: Event type string.
        agent_id: Agent identifier.
        task_id: Task identifier.
        data: Additional event data.
    """
    if callback is None:
        return
    event = SwarmEvent(
        type=event_type,
        agent_id=agent_id,
        task_id=task_id,
        data=data or {},
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    try:
        callback(event)
    except Exception:
        logger.warning("Event callback failed for %s", event_type, exc_info=True)


def _filter_skill_descriptions(loader: SkillsLoader, skill_names: list[str]) -> str:
    """Return skill descriptions filtered to the given whitelist.

    Args:
        loader: SkillsLoader instance with all skills loaded.
        skill_names: Skill names to include. Empty list means include all.

    Returns:
        Formatted skill descriptions string.
    """
    if not skill_names:
        return loader.get_descriptions()
    lines: list[str] = []
    for skill in loader.skills:
        if skill.name in skill_names:
            lines.append(f"  - {skill.name}: {skill.description}")
    return "\n".join(lines) if lines else "(no matching skills)"


def build_worker_prompt(
    agent_spec: SwarmAgentSpec,
    upstream_summaries: dict[str, str],
    skill_descriptions: str,
    grounding_block: str = "",
) -> str:
    """Build the worker's system prompt with role, upstream context, and skills.

    Args:
        agent_spec: The agent's role specification.
        upstream_summaries: Mapping of context_key -> upstream task summary.
        skill_descriptions: Pre-filtered skill description text.
        grounding_block: Optional "Ground Truth" markdown produced by
            :func:`src.swarm.grounding.format_grounding_block`. Spliced in
            ahead of the Execution Rules section so the worker sees real
            recent prices before any tool decision. Empty string skips the
            section entirely.

    Returns:
        Complete system prompt string for the worker LLM.
    """
    upstream_block = ""
    if upstream_summaries:
        sections = []
        for key, summary in upstream_summaries.items():
            sections.append(f"### {key}\n{summary}")
        upstream_block = (
            "## Upstream Context (from previous agents)\n\n"
            + "\n\n".join(sections)
        )

    prompt_parts = [
        f"## Role\n\n{agent_spec.role}",
        agent_spec.system_prompt.replace("{upstream_context}", upstream_block),
    ]

    if skill_descriptions and skill_descriptions != "(no matching skills)":
        prompt_parts.append(
            f"## Available Skills (use load_skill to access full documentation)\n\n{skill_descriptions}"
        )

    if grounding_block:
        # Placed before Execution Rules so it's in scope when the worker
        # plans its first tool call. The block already contains an explicit
        # instruction to prefer these prices over training data.
        prompt_parts.append(grounding_block)

    # Universal anti-fabrication rule. The grounding_block carries a similar
    # instruction but only renders when user_vars supplies explicit symbols.
    # Free-form prompts ("look at A-share short-term sentiment") otherwise
    # leave the worker with no guardrail and it cheerfully cites training-data
    # prices and sector weights. This block applies the rule unconditionally
    # — including to aggregator / synthesis agents that have no data tools
    # and previously had no instruction against inventing numbers.
    prompt_parts.append(
        "## Data Citation Discipline (HARD RULE)\n\n"
        "Every specific number you cite in your output — prices, percentages, "
        "volumes, fund flows, market-cap rankings, sector weights, ETF codes, "
        "ticker recommendations — MUST be traceable to one of:\n"
        "  (a) a tool call result obtained in THIS run,\n"
        "  (b) the Ground Truth block above (if present),\n"
        "  (c) the Upstream Context above (if present and the upstream agent "
        "itself sourced it from (a) or (b)).\n\n"
        "You may NOT cite numbers from memory or training data. Markets have "
        "moved since your cutoff; any specific price/percentage you recall is "
        "wrong by default.\n\n"
        "If you cannot back a number with (a), (b), or (c), you have two "
        "choices:\n"
        "  - call a data tool to fetch it (preferred), or\n"
        "  - omit the number and qualify the statement (e.g. \"directional "
        "only — not verified against live data\").\n\n"
        "This rule applies equally to synthesis / aggregator / editor roles "
        "that lack data tools. If upstream did not provide a specific number, "
        "do NOT introduce one from training data — say the upstream omitted "
        "it and proceed without."
    )

    prompt_parts.append(
        "## Execution Rules\n\n"
        "You have a HARD LIMIT of 20 tool calls. After that you will be cut off. Work efficiently.\n\n"
        "**Phase 1 — Plan (0 tool calls):** Before calling any tool, state your plan in 3-5 bullet points.\n\n"
        "**Phase 2 — Execute (≤15 tool calls):**\n"
        "- `load_skill` first to get data access methods and analysis patterns.\n"
        "- Write ONE focused Python script via `write_file`, then run it with `bash python script.py`.\n"
        "- Do NOT write long Python code inside bash. Use write_file + bash.\n"
        "- Do NOT fetch data with curl/requests. Use the patterns from load_skill.\n"
        "- If a script fails, read the error, fix with `edit_file`, re-run. Max 2 retries per script.\n\n"
        "**Phase 3 — Summarize (MUST use write_file):**\n"
        "- You MUST call `write_file` with path `report.md` to save your final report as a markdown file.\n"
        "- This is REQUIRED, not optional. Your final response MUST include a write_file call for report.md.\n"
        "- The report must include specific numbers, dates, and actionable conclusions.\n"
        "- After writing report.md, output a brief 2-3 sentence summary in your text response.\n"
        "- Respond in the same language as the task prompt."
    )

    now = datetime.now()
    prompt_parts.append(
        f"## Current Date & Time\n\n"
        f"Today is {now.strftime('%A, %B %d, %Y %H:%M (local)')}."
    )

    return "\n\n".join(prompt_parts)


def run_worker(
    agent_spec: SwarmAgentSpec,
    task: SwarmTask,
    upstream_summaries: dict[str, str],
    user_vars: dict[str, str],
    run_dir: Path,
    event_callback: Callable[[SwarmEvent], None] | None = None,
    include_shell_tools: bool = False,
    grounding_block: str = "",
) -> WorkerResult:
    """Execute a single worker task using a lightweight ReAct loop.

    Steps:
      1. Build filtered ToolRegistry from agent_spec.tools
      2. Create ChatLLM with agent_spec.model_name
      3. Build system prompt with role + upstream summaries + filtered skills
      4. Resolve task.prompt_template with user_vars
      5. Run ReAct loop (for iteration in range(max_iterations))
      6. Write summary to artifacts/{agent_id}/summary.md
      7. Return WorkerResult

    Args:
        agent_spec: Agent role specification with tools/skills/model config.
        task: The task to execute, including prompt template.
        upstream_summaries: Summaries from upstream tasks keyed by input_from keys.
        user_vars: User-provided variables for template rendering.
        run_dir: Path to .swarm/runs/{run_id}/ directory.
        event_callback: Optional callback for swarm events.
        include_shell_tools: Whether this worker may register shell tools.
        grounding_block: Optional pre-rendered "Ground Truth" markdown that
            anchors the worker on real recent prices for symbols mentioned in
            ``user_vars``. Forwarded verbatim to :func:`build_worker_prompt`.

    Returns:
        WorkerResult with status, summary, artifacts, and iteration count.
    """
    agent_id = agent_spec.id
    task_id = task.id
    max_iterations = agent_spec.max_iterations or _DEFAULT_MAX_ITERATIONS
    timeout = agent_spec.timeout_seconds or _DEFAULT_TIMEOUT_SECONDS

    _emit(event_callback, "worker_started", agent_id, task_id)

    # 1. Build filtered tool registry
    # TODO(v1): Swarm stays local-tool-only. Do not thread MCP config into this
    # path until swarm-specific config propagation and execution constraints are
    # designed explicitly.
    registry = build_filtered_registry(agent_spec.tools, include_shell_tools=include_shell_tools)

    # 2. Create LLM
    llm = ChatLLM(model_name=agent_spec.model_name)

    # 3. Build system prompt with filtered skills
    skills_loader = SkillsLoader()
    skill_desc = _filter_skill_descriptions(skills_loader, agent_spec.skills)
    system_prompt = build_worker_prompt(
        agent_spec, upstream_summaries, skill_desc, grounding_block=grounding_block,
    )

    # 4. Resolve prompt template with user vars (missing vars → LLM infers)
    class _FallbackDict(dict):
        """Dict that hints LLM to infer missing template variables."""
        def __missing__(self, key: str) -> str:
            return f"(determine the appropriate {key} based on the objective)"

    template_vars = _FallbackDict(user_vars)

    try:
        user_prompt = task.prompt_template.format_map(_FallbackDict(template_vars))
    except (KeyError, ValueError) as exc:
        error_msg = f"Failed to render prompt template: {exc}"
        _emit(event_callback, "worker_failed", agent_id, task_id, {"error": error_msg})
        return WorkerResult(
            status="failed", summary="", iterations=0, error=error_msg,
            input_tokens=0, output_tokens=0,
        )

    # 5. Create WorkerAgentLoop and run
    from app.brain.agents.core.worker_loop import WorkerAgentLoop

    artifact_dir = run_dir / "artifacts" / agent_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Build swarm-compatible event bridge
    def _on_loop_event(event_type: str, data: Dict[str, Any]) -> None:
        mapping = {
            "text_delta": ("worker_text", lambda d: {"content": d.get("delta", ""), "iteration": d.get("iter", 0)}),
            "tool_call": ("tool_call", lambda d: {"tool": d.get("tool", ""), "iteration": d.get("iter", 0), "arguments": d.get("arguments", {})}),
            "tool_result": ("tool_result", lambda d: d),
            "tool_heartbeat": ("task_heartbeat", lambda d: {**d, "phase": "tool"}),
            "tool_progress": ("tool_progress", lambda d: d),
            "compact": ("compact", lambda d: d),
        }
        mapped = mapping.get(event_type)
        if mapped:
            swarm_type, data_fn = mapped
            _emit(event_callback, swarm_type, agent_id, task_id, data_fn(data))

    loop = WorkerAgentLoop(
        registry=registry,
        llm=llm,
        max_iterations=max_iterations,
        timeout_seconds=timeout,
        event_callback=_on_loop_event,
    )
    loop.memory.run_dir = str(artifact_dir)

    result = loop.run(
        user_message=user_prompt,
        history=[{"role": "system", "content": system_prompt}],
    )

    # Map result to WorkerResult
    summary = WorkerAgentLoop.resolve_summary(artifact_dir, result.get("content", ""))

    loop_status = result.get("status", "failed")
    reason_str = result.get("reason", "")
    error_msg = reason_str or ""

    if loop_status in ("cancelled", "failed"):
        worker_status = "failed"
    elif "timeout" in (reason_str or "").lower():
        worker_status = "timeout"
    else:
        worker_status = "completed"

    # Classify deliverable
    classify_reason = WorkerAgentLoop.classify_deliverable(
        summary,
        is_data_agent=WorkerAgentLoop.is_data_agent(agent_spec.tools),
        report_written=WorkerAgentLoop.report_written(artifact_dir),
        data_tool_calls=loop.data_tool_calls,
    )
    if classify_reason and worker_status == "completed":
        worker_status = "incomplete"
        error_msg = f"output contract not met: {classify_reason}"
        _emit(event_callback, "worker_incomplete", agent_id, task_id,
              {"iterations": result.get("iterations", 0), "reason": classify_reason})
    else:
        _emit(event_callback, f"worker_{worker_status}", agent_id, task_id,
              {"iterations": result.get("iterations", 0)})

    return WorkerResult(
        status=worker_status,
        summary=summary,
        artifact_paths=WorkerAgentLoop.collect_artifacts(artifact_dir),
        iterations=result.get("iterations", 0),
        error=error_msg,
        input_tokens=result.get("total_input_tokens", 0),
        output_tokens=result.get("total_output_tokens", 0),
    )
