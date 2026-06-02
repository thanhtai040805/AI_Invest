"""Backtest execution tool: validates config.json + signal_engine.py and runs the built-in engine."""

from __future__ import annotations

import json
from pathlib import Path

from app.brain.agents.core.progress import emit_progress
from app.brain.agents.core.tools import BaseTool
from app.brain.tools.framework.runner import Runner
from app.brain.tools.path_utils import safe_run_dir


def run_backtest(run_dir: str) -> str:
    """Run backtest: validate config.json + signal_engine.py, invoke built-in engine.

    Args:
        run_dir: Path to the run directory.

    Returns:
        JSON-formatted execution result.
    """
    emit_progress("validate", message="validating run_dir and config")
    try:
        run_path = safe_run_dir(run_dir)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    config_path = run_path / "config.json"
    if not config_path.exists():
        return json.dumps({
            "status": "error",
            "error": "config.json not found",
            "hint": (
                "Create config.json first using write_file:\n"
                'write_file(path="config.json", content=\'{"source": "vietfin", '
                '"codes": ["FPT"], "start_date": "2020-01-01", "end_date": "2024-12-31"}\')\n'
                "Then create code/signal_engine.py (SignalEngine class with generate() method)."
            ),
        }, ensure_ascii=False)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "error": f"config.json parse error: {e}"}, ensure_ascii=False)

    if "source" not in config:
        return json.dumps({
            "status": "error",
            "error": "config.json missing 'source' field",
            "hint": "Add 'source' field: one of vietfin, dnse, auto",
        }, ensure_ascii=False)

    valid_sources = {"vietfin", "dnse", "auto"}
    if config["source"] not in valid_sources:
        return json.dumps({"status": "error", "error": f"source must be one of {valid_sources}, got: {config['source']}"}, ensure_ascii=False)

    signal_path = run_path / "code" / "signal_engine.py"
    if not signal_path.exists():
        return json.dumps({
            "status": "error",
            "error": "code/signal_engine.py not found",
            "hint": (
                "Create code/signal_engine.py first using write_file:\n"
                'write_file(path="code/signal_engine.py", content="""\n'
                "from signal_engine import SignalEngine\n\n"
                "class MySignal(SignalEngine):\n"
                "    def generate(self, df):\n"
                '        df[\"signal\"] = 0\n'
                "        return df\n"
                '""")\n'
                "See load_skill('strategy-generate') for the full SignalEngine contract."
            ),
        }, ensure_ascii=False)

    agent_root = Path(__file__).resolve().parents[2]
    entry_script = agent_root / "brain" / "tools" / "backtest" / "runner.py"

    source = config.get("source", "?")
    emit_progress(
        "simulate",
        message=f"running backtest engine (source={source})",
    )
    runner = Runner(timeout=300)

    result = runner.execute(
        entry_script,
        run_path,
        cwd=agent_root,
        cli_args=[str(run_path)],
    )

    emit_progress("finalize", message="collecting artifacts")
    artifacts_found = {name: str(path) for name, path in result.artifacts.items()}
    return json.dumps({
        "status": "ok" if result.success else "error",
        "exit_code": result.exit_code,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
        "artifacts": artifacts_found,
        "run_dir": run_dir,
    }, ensure_ascii=False)


class BacktestTool(BaseTool):
    """Backtest execution tool."""

    name = "backtest"
    description = "Run backtest: validate config.json + signal_engine.py, invoke built-in engine."
    parameters = {
        "type": "object",
        "properties": {
            "run_dir": {"type": "string", "description": "Path to the run directory"},
        },
        "required": ["run_dir"],
    }
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs) -> str:
        """Execute backtest."""
        return run_backtest(kwargs["run_dir"])
