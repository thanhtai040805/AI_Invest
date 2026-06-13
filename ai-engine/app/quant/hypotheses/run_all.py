#!/usr/bin/env python3
"""Run all 3 VN hypotheses and report results."""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from app.quant.hypotheses.test_base import HypothesisResult
from app.quant.hypotheses.test_tet import run_tet_hypothesis
from app.quant.hypotheses.test_foreign_flow import run_foreign_flow_hypothesis
from app.quant.hypotheses.test_insider import run_insider_hypothesis
from app.quant.hypotheses.registry import HypothesisRegistry

logger = logging.getLogger(__name__)

SIGNAL_DEFINITIONS = {
    "H001": "days_to_tet <= 15 AND days_to_tet > 0 → long top 30 liquid stocks",
    "H002": "foreign_net_buy_3d_streak >= 3 AND foreign_net_value_3d > 30e9 → long 5d",
    "H003": "insider_buy_pct_outstanding > 0.5 AND role IN (CEO/CFO/Chairman) → long 20d",
}

DATA_SOURCES = {
    "H001": ["ohlcv (HOSE prices)", "VNCalendar (Tet dates 2020-2026)"],
    "H002": ["foreign_flow table (226 symbols, 2023-2026)", "ohlcv (exit prices)"],
    "H003": ["insider_trades table (29k+ trades, 2005-2026)", "ohlcv (exit prices)"],
}

SKILLS = {
    "H001": ["vn-trading-rules", "vn-macro-calendar"],
    "H002": ["vn-trading-rules", "vn-sector-analysis"],
    "H003": ["vn-trading-rules"],
}

HYPOTHESIS_META = {
    "H001": {
        "universe": "HOSE_LIQUID_TOP30",
        "signal_definition": SIGNAL_DEFINITIONS["H001"],
        "expected_holding": "10 trading days",
    },
    "H002": {
        "universe": "ALL_FOREIGN_FLOW_SYMBOLS",
        "signal_definition": SIGNAL_DEFINITIONS["H002"],
        "expected_holding": "5 trading days",
    },
    "H003": {
        "universe": "ALL_LISTED_WITH_INSIDER_TRADES",
        "signal_definition": SIGNAL_DEFINITIONS["H003"],
        "expected_holding": "20 trading days",
    },
}


def _classify_sharpe(sharpe: float) -> str:
    if sharpe > 1.0:
        return "validated"
    if sharpe > 0.5:
        return "promising"
    return "rejected" if sharpe < 0 else "exploring"


def _result_to_report(result: HypothesisResult) -> str:
    lines = [
        f"## {result.hypothesis_id}: {result.title}",
        f"**Thesis:** {result.thesis}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Return | {result.total_return_pct:+.2f}% |",
        f"| Annualized Return | {result.annualized_return_pct:+.2f}% |",
        f"| Sharpe Ratio | {result.sharpe_ratio:.3f} |",
        f"| Max Drawdown | {result.max_drawdown_pct:.2f}% |",
        f"| Win Rate | {result.win_rate:.1%} |",
        f"| Total Trades | {result.total_trades} |",
        f"| Avg Win | {result.avg_win_pct:+.2f}% |",
        f"| Avg Loss | {result.avg_loss_pct:+.2f}% |",
        f"| Profit Factor | {result.profit_factor:.3f} |",
        f"| Avg Holding | {result.avg_holding_days:.0f} days |",
        f"| Calmar Ratio | {result.calmar_ratio:.3f} |",
        f"| vs VNINDEX | {result.vs_benchmark_return_pct:+.2f}% |",
        "",
    ]
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    registry = HypothesisRegistry()
    results_dir = Path.home() / ".vibe-trading" / "reports"
    results_dir.mkdir(parents=True, exist_ok=True)

    hyp_id = None
    runner = None
    result = None
    last_hyp_id = None

    testers = [
        ("H001", run_tet_hypothesis),
        ("H002", run_foreign_flow_hypothesis),
        ("H003", run_insider_hypothesis),
    ]

    report_sections = ["# VN Hypothesis Test Results\n", f"**Run date:** {date.today().isoformat()}\n", "---\n"]

    for hyp_id, runner_fn in testers:
        last_hyp_id = hyp_id
        logger.info("=" * 60)
        logger.info("Running %s...", hyp_id)
        logger.info("=" * 60)
        try:
            runner = runner_fn
            result = runner()
        except Exception as e:
            logger.error("Failed to run %s: %s", hyp_id, e, exc_info=True)
            report_sections.append(f"## {hyp_id}: **FAILED**\nError: {e}\n\n---\n")
            continue

        report_sections.append(_result_to_report(result))
        report_sections.append("---\n")

        status = _classify_sharpe(result.sharpe_ratio)

        try:
            existing = registry.search(query=hyp_id, status=None, limit=1)
            if existing:
                h = existing[0]
                registry.update(
                    h.hypothesis_id,
                    status=status,
                    invalidation_notes=(
                        None if status == "validated" else
                        f"Sharpe={result.sharpe_ratio:.3f}, WinRate={result.win_rate:.1%}, "
                        f"Return={result.total_return_pct:+.2f}%"
                    ),
                )
                registry.link_backtest(
                    h.hypothesis_id,
                    backtest_run_dir=str(results_dir / f"{hyp_id}_run"),
                    metrics={
                        "sharpe": result.sharpe_ratio,
                        "total_return_pct": result.total_return_pct,
                        "win_rate": result.win_rate,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "total_trades": result.total_trades,
                    },
                    notes=f"Automated hypothesis test on {date.today().isoformat()}",
                )
            else:
                meta = HYPOTHESIS_META.get(hyp_id, {})
                h = registry.create(
                    title=result.title,
                    thesis=result.thesis,
                    status=status,
                    universe=meta.get("universe"),
                    signal_definition=meta.get("signal_definition"),
                    data_sources=DATA_SOURCES.get(hyp_id, []),
                    skills=SKILLS.get(hyp_id, []),
                    invalidation_notes=(
                        None if status == "validated" else
                        f"Sharpe={result.sharpe_ratio:.3f}, WinRate={result.win_rate:.1%}"
                    ),
                )
                registry.link_backtest(
                    h.hypothesis_id,
                    backtest_run_dir=str(results_dir / f"{hyp_id}_run"),
                    metrics={
                        "sharpe": result.sharpe_ratio,
                        "total_return_pct": result.total_return_pct,
                        "win_rate": result.win_rate,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "total_trades": result.total_trades,
                    },
                    notes=f"Automated hypothesis test on {date.today().isoformat()}",
                )
        except Exception as e:
            logger.warning("Failed to update registry for %s: %s", hyp_id, e)

    report = "\n".join(report_sections)
    report_path = results_dir / f"vn_hypothesis_results_{date.today().isoformat()}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n\nReport saved to: {report_path}")
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(report)


if __name__ == "__main__":
    main()
