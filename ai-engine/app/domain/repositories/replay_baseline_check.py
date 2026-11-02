"""Daily acceptance for the explicitly chosen historical baseline."""
from datetime import date, timedelta
from decimal import Decimal
import json
import hashlib
from pathlib import Path


BASELINE_ID = "cache_readonly_replay_28_20261025_NAV_1005857685"
BASELINE_SNAPSHOT = "730aa18a67bf4169ffcc1ff89cdfffe09a5be9680957f30758cc84d3ec6de581"


class ReplayBaselineCheck:
    def __init__(self, reference: Path, days: list[date], snapshot_sha: str, account: str,
                 capital: float, max_candidates: int, symbols: list | None, output: Path):
        data = json.loads(reference.read_text(encoding="utf-8"))
        if (data.get("baseline_id") != BASELINE_ID or snapshot_sha != BASELINE_SNAPSHOT
                or data.get("factor_snapshot", {}).get("sha256") != BASELINE_SNAPSHOT):
            raise ValueError("Expected the approved original baseline and factor snapshot")
        rows = data["nav"]
        if (len(rows) != 28 or [str(day) for day in days[:28]] != [row[0] for row in rows]
                or rows[0][0] != "2026-08-12" or rows[-1][0] != "2026-09-23"):
            raise ValueError("Replay must start with all 28 baseline sessions from 2026-08-12")
        if account != data["account_id"] or capital != 1_000_000_000 or max_candidates != 3 or symbols:
            raise ValueError("Account/capital/candidate limit/universe differs from the approved baseline")
        self.expected = {row[0]: row[1:] for row in rows}
        factor_file = reference.parent / data["effective_factors"]["path"]
        if hashlib.sha256(factor_file.read_bytes()).hexdigest() != data["effective_factors"]["file_sha256"]:
            raise ValueError("Baseline effective factor file hash mismatch")
        self.factors: dict[str, dict] = {}
        for row in json.loads(factor_file.read_text(encoding="utf-8")):
            self.factors.setdefault(row["as_of"], {})[row["symbol"]] = row
        output.mkdir(parents=True, exist_ok=True)
        self.output = output / "daily_baseline_comparison.jsonl"
        # Refuse accidental reuse of an earlier run's evidence.
        self.stream = self.output.open("x", encoding="utf-8")

    def check(self, day: date, closing: dict, diagnostics: dict) -> None:
        expected = self.expected.get(str(day))
        if expected is None:
            self.stream.write(json.dumps({"date": str(day), "mode": "VN_PRODUCER",
                                          "diagnostics": diagnostics}, default=str, ensure_ascii=False) + "\n")
            self.stream.flush()
            return
        nav, cash = Decimal(str(closing["total_nav"])), Decimal(str(closing["cash_balance"]))
        record = {"date": str(day), "nav": str(nav), "cash": str(cash),
                  "nav_delta": str(nav - Decimal(expected[0])),
                  "cash_delta": str(cash - Decimal(expected[1])), "diagnostics": diagnostics}
        expected_factors = self.factors.get(str(day - timedelta(days=1)), {})
        actual_factors = diagnostics.get("factors", {})
        record["factor_differences"] = [symbol for symbol in sorted(set(expected_factors) | set(actual_factors))
                                        if expected_factors.get(symbol) != actual_factors.get(symbol)]
        record["matches"] = (nav == Decimal(expected[0]) and cash == Decimal(expected[1])
                             and not record["factor_differences"])
        self.stream.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self.stream.flush()
        if not record["matches"]:
            raise RuntimeError(f"Baseline mismatch on {day}; stopped before next session. Evidence: {self.output}")

    def close(self) -> None:
        self.stream.close()
