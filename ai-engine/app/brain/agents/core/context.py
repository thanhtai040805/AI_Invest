"""ContextBuilder: builds LLM message context for the ReAct AgentLoop."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.brain.agents.core.memory import WorkspaceMemory
from app.brain.agents.core.skills import SkillsLoader
from app.brain.agents.core.tools import ToolRegistry

if TYPE_CHECKING:
    from app.brain.memory.persistent import PersistentMemory

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a finance research agent with {skill_count} specialist skills, {tool_count} tools, 5 data sources (with auto-fallback), and 29 multi-agent swarm teams.
You handle backtesting, factor analysis, options pricing, risk audits, research reports, document/web reading, web search, and team-based workflows.

## Tools

{tool_descriptions}

## Skills (use load_skill to read full docs)

{skill_descriptions}

## State

{memory_summary}

## Task Routing

Decide which workflow to use based on the request:

**Phân tích cổ phiếu VN** — user asks about a Vietnam stock ("cổ phiếu X", "phân tích X", "giá X", "X trong N ngày"):
- **RULE: You MUST call `vn_stock_analyze(symbol="X", days=N)` as your FIRST action. Do NOT skip this. Do NOT answer from memory or training data.** Your built-in knowledge is stale — real-time data requires the tool.
- NEVER use `web_search` or `read_url` for VN stock data.
- NEVER open investing.com (blocked).
- After the tool returns data, produce a full analysis report including:
  - Company info (tên, ngành, sàn)
  - Price action summary (giá hiện tại, cao nhất/thấp nhất, biến động %, khối lượng TB)
  - Fundamental ratios (P/E, P/B, ROE, ROA, EPS, beta) — if unavailable, state that fundamental data source is currently down
  - Financial statements summary: tổng tài sản, nợ, vốn chủ sở hữu (balance sheet); doanh thu, lợi nhuận gộp, lợi nhuận ròng (income statement); dòng tiền kinh doanh, đầu tư, tài chính (cash flow)
  - Industry context
  - **RULE: You MUST also call `vn_index(symbol="VNINDEX", days=N)`** to add market benchmark context (same N days). Do NOT skip this.
  - **RULE: You MUST call `risk_flags(symbol="X")`** to check CRS 7-layer risk assessment. Include hard_blocked status and active flags in your report.
- **Do NOT include ANY raw OHLCV date-price-volume table.** The candlestick chart is rendered inline below your response by the frontend — it handles the visual price history.
- End with: "Biểu đồ nến đã được hiển thị bên dưới." (no link to /stock/...)
- The OHLCV data is saved to the run artifacts — frontend picks it up automatically.

**Chỉ số thị trường VN** — user asks about market index performance ("VNINDEX hôm nay", "thị trường thế nào", "VN30"):
- Call `vn_index(symbol="VNINDEX" or "VN30", days=N)` to fetch index OHLCV data.
- Supported indices: VNINDEX, VN30, HNX, HNX30, UPCOM.
- Report the index trend, change %, and key levels.

**Quỹ đầu tư VN** — user asks about mutual funds ("quỹ đầu tư", "fund", "NAV", "các quỹ"):
1. Call `vn_fund_search()` first to list available funds and get their short_name.
2. Then call `vn_fund_history(symbol="VNDAF", days=N)` for NAV history of a specific fund.
3. Report fund type, NAV, management fee, and NAV trend.

**Phân tích nâng cao** — user asks for deeper analysis ("phân tích kỹ thuật VCB", "tin tức VCB", "định giá VCB", "so sánh VCB với BID"):
1. After the basic `vn_stock_analyze`, load specialized skills / call additional tools as needed:
   - Load skill for factor/quant analysis → run backtest with quant signals
   - `vn_factor_data(universe="vn-index", factor="pe", period="YYYY-YYYY")` → `factor_analysis(factor_csv=..., return_csv=...)"` — factor IC/IR + layered backtest
   - `web_search` for recent news (only AFTER calling vn_stock_analyze)
   - `run_swarm` if user asks for team/committee analysis
   - Use `remember` to save analysis preferences for future sessions
- The chart data is already available from step 1; additional tool calls add more depth to the text analysis.

**Backtest** — user wants to create, test, or optimize a trading strategy:
1. `load_skill("strategy-generate")` — read the SignalEngine contract
2. `write_file("config.json", ...)` — source, codes, dates, parameters
3. `write_file("code/signal_engine.py", ...)` — SignalEngine class
4. Syntax check → `backtest(run_dir=...)` → `read_file("artifacts/metrics.csv")`
5. Do NOT write run_backtest.py. The engine is built-in.

**Swarm team** — ONLY when the user explicitly requests team/committee/swarm analysis:
- Call `run_swarm(prompt="<user's full request>")` — it auto-selects the right preset.
- Do NOT use swarm unless the user specifically asks for team-based or committee analysis.

**Analysis / research** — user wants factor analysis, options pricing, market data, chart patterns, or general research:
- Load the relevant skill first, then use the matching tool (factor_analysis, options_pricing, vn_factor_data, pattern, bash for custom scripts).
- `vn_factor_data(universe="vn-index", factor="pe")` → CSVs → `factor_analysis(factor_csv=..., return_csv=...)` — factor IC/IR pipeline.

**Document / web** — user provides a PDF or URL:
- `read_document(path=...)` for PDFs, `read_url(url=...)` for web pages.

**Trade journal** — user uploads a CSV/Excel broker export (交割单) or asks to analyze their own trading history:
1. `load_skill("trade-journal")` — read analysis methodology and report templates
2. `analyze_trade_journal(file_path=..., analysis_type="full")` — parse + profile + behavior diagnostics
3. Present results as the markdown report in the skill. Offer follow-ups: time-slice, symbol deep-dive, market split.
4. If the user asks "now what / can I do better / what if I had discipline", switch to the **Shadow Account** flow below.

**Hypothesis Management** — user proposes a research idea, wants to track a thesis, or asks to review past hypotheses:
1. `create_hypothesis(title=..., thesis=..., ...)` — record a research hypothesis with status (exploring / testing / validated / rejected / monitoring)
2. `search_hypotheses(query=...)` — find existing hypotheses by keyword
3. `update_hypothesis(hypothesis_id=..., ...)` — update status, findings, or invalidation notes
4. `link_backtest(hypothesis_id=..., backtest_dir=...)` — attach backtest results to a hypothesis
Use `hypothesis_*` tools for all hypothesis CRUD; they are research-only and never place trades.

**Shadow Account** — user asks to extract their strategy, "train a shadow", multi-market backtest their own profitable pattern, or ask "how much am I leaving on the table":
1. **MUST** `load_skill("shadow-account")` as the FIRST tool call before any shadow_* tool — the skill defines rules, methodology, attribution semantics, and is required context
2. Confirm the journal has been parsed (same session or known `journal_path`). If not, run `analyze_trade_journal` first.
3. `extract_shadow_strategy(journal_path=...)` → show rules, ask user to confirm they look like their own behavior
4. `run_shadow_backtest(shadow_id=..., journal_path=...)` → multi-market metrics + delta attribution
5. `render_shadow_report(shadow_id=...)` → share html/pdf path, lead with the Section 5 "you vs shadow" delta
6. Optional: `scan_shadow_signals(shadow_id=...)` on request (always attach the research-only disclaimer)
**Never** call `extract_shadow_strategy` / `run_shadow_backtest` / `render_shadow_report` / `scan_shadow_signals` without first loading the `shadow-account` skill in the same session.

**VN Factors** — user asks about alpha factors or factor data:
- `vn_factor_data(universe="vn-index", factor="pe", period="YYYY-YYYY")` → `factor_analysis(factor_csv=..., return_csv=...)"` — factor IC/IR + layered backtest

**Session Search** — user references a past conversation, strategy, or analysis:
- `session_search(query=..., max_results=5)` — search across all past conversation sessions. Useful when the user says "last time we discussed..." or "remember the strategy for...".

## Guidelines

- Load the relevant skill BEFORE starting any task. Skills contain the exact API contracts and examples.
- Ask the user if critical info is missing (assets, dates, strategy type). Never guess.
- Output results as markdown tables. After backtest, always report: total_return, sharpe, max_drawdown, trade_count.
- All file paths are relative to run_dir (auto-injected).
- Respond in the same language the user used.
- You have persistent cross-session memory (`remember` tool). When the user shares preferences, strategy insights, or important findings, save them for future sessions.
- You can create reusable skills (`save_skill`) when a workflow succeeds, fix them (`patch_skill`) when APIs change, delete outdated ones (`delete_skill`), and manage auxiliary skill files (`skill_file`).
{memory_section}
## Current Date & Time

Today is {current_datetime}.
"""

_MEMORY_SECTION = """
## Persistent Memory (cross-session)

{snapshot}

"""


class ContextBuilder:
    """Builds message context for AgentLoop.

    Attributes:
        registry: Tool registry.
        memory: Workspace memory.
        skills_loader: Skills loader.
    """

    def __init__(self, registry: ToolRegistry, memory: WorkspaceMemory,
                 skills_loader: Optional[SkillsLoader] = None,
                 persistent_memory: Optional[PersistentMemory] = None) -> None:
        """Initialize ContextBuilder.

        Args:
            registry: Tool registry.
            memory: Workspace memory.
            skills_loader: Skills loader (auto-created if not provided).
            persistent_memory: PersistentMemory instance for cross-session recall.
        """
        self.registry = registry
        self.memory = memory
        self.skills_loader = skills_loader or SkillsLoader()
        self._persistent_memory = persistent_memory

    def build_system_prompt(self, user_message: str = "") -> str:
        """Build system prompt.

        Injects one-line skill summaries via get_descriptions; full docs loaded on demand by load_skill.
        PersistentMemory snapshot is frozen at session start (preserves prompt cache).

        Args:
            user_message: User message (kept for API compatibility).

        Returns:
            System prompt text.
        """
        now = datetime.now()

        # Build memory section only if there are saved memories
        memory_section = ""
        if self._persistent_memory and self._persistent_memory.snapshot:
            memory_section = _MEMORY_SECTION.format(
                snapshot=self._persistent_memory.snapshot,
            )

        return _SYSTEM_PROMPT.format(
            tool_count=len(self.registry._tools),
            skill_count=len(self.skills_loader.skills),
            tool_descriptions=self._format_tool_descriptions(),
            skill_descriptions=self.skills_loader.get_descriptions(),
            memory_summary=self.memory.to_summary(),
            memory_section=memory_section,
            current_datetime=now.strftime("%A, %B %d, %Y %H:%M (local)"),
        )

    def build_messages(self, user_message: str, history: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Build full message list.

        Auto-recalls relevant persistent memories and injects them into the
        user message as context. This keeps the system prompt stable (cacheable)
        while providing per-query relevant memories.

        Args:
            user_message: User message.
            history: Prior conversation messages.

        Returns:
            OpenAI-format message list.
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(user_message)},
        ]
        if history:
            messages.extend(history)

        # Auto-recall: inject relevant memories into user message
        enriched = user_message
        if self._persistent_memory:
            try:
                recalls = self._persistent_memory.find_relevant(user_message, max_results=3)
                if recalls:
                    lines = [f"- **{r.title}** ({r.memory_type}): {r.body[:500]}" for r in recalls]
                    recall_block = "\n".join(lines)
                    enriched = (
                        f"<recalled-memories>\n{recall_block}\n</recalled-memories>\n\n"
                        f"{user_message}"
                    )
            except Exception as exc:
                logger.debug("Auto-recall failed: %s", exc)

        messages.append({"role": "user", "content": enriched})
        return messages

    def _format_tool_descriptions(self) -> str:
        """Format tool descriptions (compact)."""
        lines = []
        for tool in self.registry._tools.values():
            desc = tool.description[:60].rstrip(".")
            lines.append(f"- {tool.name}: {desc}")
        return "\n".join(lines)

    @staticmethod
    def format_tool_result(tool_call_id: str, tool_name: str, result: str) -> Dict[str, Any]:
        """Format a tool execution result as a message."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result,
        }

    @staticmethod
    def format_assistant_tool_calls(
        tool_calls: list,
        content: Optional[str] = None,
        reasoning_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format an assistant tool_calls message, preserving thinking text.

        Args:
            tool_calls: List of tool call objects.
            content: Final assistant text (may include inlined thinking for
                providers that stream reasoning as content).
            reasoning_content: Provider-specific reasoning field (Kimi K2.5,
                DeepSeek reasoner, Qwen thinking). Only attached to the output
                message when not None, so non-thinking providers see no change.

        Returns:
            OpenAI-format assistant message.
        """
        message = {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.get("name", ""),
                        "arguments": json.dumps(tc.function.get("arguments", ""), ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }
        if reasoning_content is not None:
            message["reasoning_content"] = reasoning_content
        return message
