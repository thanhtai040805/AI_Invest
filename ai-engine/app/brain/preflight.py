"""
Preflight connectivity checks for all AI model providers + data sources.

Tests each provider with a real API call, measures latency,
and reports which models are OK, slow, or failing.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table

# Ensure project root is on sys.path for direct execution
_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

SLOW_THRESHOLD = 5.0
TEST_PROMPT = "Giới thiệu về bạn trong 1-2 câu."


@dataclass(frozen=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    status: str  # "ready", "error", "not_configured", "skipped", "slow"
    message: str
    impact: str  # what breaks if this fails
    critical: bool = False
    latency: Optional[float] = None


# ── AI Model Checks ──────────────────────────────────────────────────────────


def _check_nvidia() -> CheckResult:
    from app.config.settings import get_settings
    s = get_settings()
    api_key = s.llm_nvidia_key
    model = s.llm_nvidia_model
    if not api_key:
        return CheckResult("NVIDIA", "not_configured", "NVDIA key not set", "document reader unavailable", critical=True)
    try:
        from openai import OpenAI
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
        start = time.time()
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": TEST_PROMPT}], max_tokens=100)
        lat = time.time() - start
        msg = f"{model} — {lat:.1f}s"
        status = "ready" if lat < SLOW_THRESHOLD else "slow"
        return CheckResult("NVIDIA", status, msg, "", latency=lat)
    except Exception as e:
        err = str(e)
        if "api key" in err.lower() or "auth" in err.lower() or "unauthorized" in err.lower():
            return CheckResult("NVIDIA", "error", f"AUTH: {err[:100]}", "document reader unavailable", critical=True)
        if "rate limit" in err.lower() or "429" in err or "quota" in err.lower():
            return CheckResult("NVIDIA", "error", f"RATE LIMIT: {err[:100]}", "document reader unavailable", critical=True)
        return CheckResult("NVIDIA", "error", f"{type(e).__name__}: {err[:100]}", "document reader unavailable", critical=True)


def _check_groq0() -> CheckResult:
    from app.config.settings import get_settings
    s = get_settings()
    api_key = s.llm_groq_key0
    model = s.llm_groq_model0
    if not api_key:
        return CheckResult("Groq-0", "not_configured", "GROQ_API_KEY0 not set", "realtime signals unavailable", critical=True)
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        start = time.time()
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": TEST_PROMPT}], max_tokens=100)
        lat = time.time() - start
        msg = f"{model} — {lat:.1f}s"
        status = "ready" if lat < SLOW_THRESHOLD else "slow"
        return CheckResult("Groq-0", status, msg, "", latency=lat)
    except Exception as e:
        err = str(e)
        if "api key" in err.lower() or "auth" in err.lower():
            return CheckResult("Groq-0", "error", f"AUTH: {err[:100]}", "realtime signals unavailable", critical=True)
        if "rate limit" in err.lower() or "429" in err:
            return CheckResult("Groq-0", "error", f"RATE LIMIT: {err[:100]}", "realtime signals unavailable", critical=True)
        return CheckResult("Groq-0", "error", f"{type(e).__name__}: {err[:100]}", "realtime signals unavailable", critical=True)


def _check_groq1() -> CheckResult:
    from app.config.settings import get_settings
    s = get_settings()
    api_key = s.llm_groq_key1
    model = s.llm_groq_model1
    if not api_key:
        return CheckResult("Groq-1", "not_configured", "GROQ_API_KEY1 not set", "structured output unavailable", critical=True)
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        start = time.time()
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": TEST_PROMPT}], max_tokens=100)
        lat = time.time() - start
        msg = f"{model} — {lat:.1f}s"
        status = "ready" if lat < SLOW_THRESHOLD else "slow"
        return CheckResult("Groq-1", status, msg, "", latency=lat)
    except Exception as e:
        err = str(e)
        if "api key" in err.lower() or "auth" in err.lower():
            return CheckResult("Groq-1", "error", f"AUTH: {err[:100]}", "structured output unavailable", critical=True)
        if "rate limit" in err.lower() or "429" in err:
            return CheckResult("Groq-1", "error", f"RATE LIMIT: {err[:100]}", "structured output unavailable", critical=True)
        return CheckResult("Groq-1", "error", f"{type(e).__name__}: {err[:100]}", "structured output unavailable", critical=True)


# ── Data-Source Checks ───────────────────────────────────────────────────────





def _check_yfinance() -> CheckResult:
    try:
        import yfinance
    except ImportError:
        return CheckResult("yfinance", "skipped", "package not installed", "US/HK equity backtest unavailable")
    try:
        import yfinance as yf
        ticker = yf.Ticker("AAPL")
        info = ticker.fast_info
        return CheckResult("yfinance", "ready", "reachable", "")
    except Exception as exc:
        return CheckResult("yfinance", "error", f"{type(exc).__name__}: {str(exc)[:100]}", "US/HK equity backtest unavailable")


def _check_tushare() -> CheckResult:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token == "your-tushare-token":
        return CheckResult("Tushare", "not_configured", "TUSHARE_TOKEN not set (optional)", "A-share data unavailable")
    try:
        import tushare
    except ImportError:
        return CheckResult("Tushare", "skipped", "package not installed", "A-share data unavailable")
    return CheckResult("Tushare", "ready", "token configured", "")


def _check_akshare() -> CheckResult:
    if find_spec("akshare") is None:
        return CheckResult("akshare", "skipped", "package not installed", "A-share/forex fallback unavailable")
    return CheckResult("akshare", "ready", "installed", "")


def _check_vietfin() -> CheckResult:
    if find_spec("vietfin") is None:
        return CheckResult("VietFin", "skipped", "package not installed", "VN equity backtest unavailable")
    return CheckResult("VietFin", "ready", "installed", "")





# -- Status icons and colors --------------------------------------------------

_STATUS_DISPLAY = {
    "ready": ("[green]OK[/green]", "green"),
    "slow": ("[yellow]SLOW[/yellow]", "yellow"),
    "error": ("[red]FAIL[/red]", "red"),
    "not_configured": ("[yellow]N/A[/yellow]", "yellow"),
    "skipped": ("[dim]SKIP[/dim]", "dim"),
}


def _run_model_checks() -> List[CheckResult]:
    results: List[CheckResult] = []
    async def _run():
        nonlocal results
        loop = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(None, _check_nvidia),
            loop.run_in_executor(None, _check_groq0),
            loop.run_in_executor(None, _check_groq1),
        ]
        results = await asyncio.gather(*tasks)
    asyncio.run(_run())
    return results


def run_preflight(console: Optional[Console] = None) -> List[CheckResult]:
    """Run all preflight checks and print results.

    Args:
        console: Rich console for output. Creates one if not provided.

    Returns:
        List of check results.
    """
    if console is None:
        console = Console()

    # AI model checks (real API calls, runs in thread pool)
    model_results = _run_model_checks()

    # Data-source checks (sync)
    data_checks: List[CheckResult] = [
        _check_yfinance(),
        _check_tushare(),
        _check_akshare(),
        _check_vietfin(),
    ]

    all_results: List[CheckResult] = model_results + data_checks

    # Build display table
    table = Table(show_header=False, show_edge=False, padding=(0, 1), expand=False)
    table.add_column(width=6)   # icon
    table.add_column(width=20)  # name
    table.add_column(width=16)  # latency/model
    table.add_column()          # message

    for r in all_results:
        icon, color = _STATUS_DISPLAY.get(r.status, ("[dim]?[/dim]", "dim"))
        latency_str = f"[{color}]{r.latency:.1f}s[/{color}]" if r.latency is not None else ""
        detail = r.message
        if r.status in ("error", "not_configured") and r.impact:
            detail = f"{detail}  ({r.impact})"
        table.add_row(icon, f"[{color}]{r.name}[/{color}]", latency_str, f"[{color}]{detail}[/{color}]")

    console.print()
    console.print("[bold]Preflight Check — AI Models & Data Sources[/bold]")
    console.print(table)

    summary_ok = sum(1 for r in all_results if r.status in ("ready", "skipped"))
    summary_slow = sum(1 for r in all_results if r.status == "slow")
    summary_fail = sum(1 for r in all_results if r.status in ("error", "not_configured"))
    has_critical = any(r.critical and r.status not in ("ready", "skipped") for r in all_results)

    console.print()
    console.print(f"  [green]OK[/green]: {summary_ok}  [yellow]SLOW[/yellow]: {summary_slow}  [red]FAIL[/red]: {summary_fail}   ([bold]{len(all_results)}[/bold] total)")
    if has_critical:
        console.print("[bold red]  ✗ Some critical checks failed — core AI features may not work.[/bold red]")
    else:
        console.print("[dim]  ✓ All critical checks passed.[/dim]")
    console.print()

    # Show slow models detail
    slow_ones = [r for r in model_results if r.status == "slow"]
    if slow_ones:
        console.print("[yellow]── Slow models (>5s) ──[/yellow]")
        for r in slow_ones:
            console.print(f"  {r.name}: {r.latency:.1f}s  —  có thể cần timeout cao hơn hoặc check network")
        console.print()

    return all_results


if __name__ == "__main__":
    run_preflight()
