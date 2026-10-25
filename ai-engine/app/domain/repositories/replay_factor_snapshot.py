"""Run-scoped factor inputs; replay outputs must never become source scores."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.financial_fields import financial_number


LEARNING_FACTOR_SQL = """
    SELECT symbol, value_score, quality_score, momentum_1m,
           earnings_yield_score, foreign_flow_score, composite_score
    FROM factor_scores
    WHERE score_date <= %s
    ORDER BY score_date DESC LIMIT 100
"""
_active: ContextVar[ReplayFactorSnapshot | None] = ContextVar("replay_factor_snapshot", default=None)


def current_factor_snapshot() -> ReplayFactorSnapshot | None:
    return _active.get()


class ReplayFactorSnapshot:
    def __init__(self, data: dict):
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        self.sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self._data = json.loads(encoded)
        self.version = self._data.get("version")
        if self.version not in (1, 2):
            raise ValueError("Unsupported replay factor snapshot version")
        self._dates = set(self._data["as_of_dates"])
        if not self._dates or self._dates != set(self._data["learning_rows"]):
            raise ValueError("Replay factor snapshot has incomplete date coverage")
        self._rows = {}
        for row in self._data["factor_rows"]:
            if len(row) != 11 or row[1] not in self._dates:
                raise ValueError("Invalid replay factor row")
            key = (row[0], row[1])
            if key in self._rows:
                raise ValueError("Duplicate replay factor symbol/date")
            if self.version == 2 and (not isinstance(row[10], dict) or not row[10].get("source", {}).get("version")):
                raise ValueError("Rebuilt factor snapshot requires source provenance")
            self._rows[key] = row
        if any(len(row) != 7 for rows in self._data["learning_rows"].values() for row in rows):
            raise ValueError("Invalid replay learning factor row")

    def _day(self, as_of: date) -> str:
        day = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
        if day not in self._dates:
            raise ValueError(f"Date {day} is outside the replay factor snapshot")
        return day

    def factor_rows(self, symbol: str, as_of: date) -> list:
        row = self._rows.get((symbol, self._day(as_of)))
        return [deepcopy(row)] if row is not None else []

    def learning_rows(self, as_of: date) -> list:
        return deepcopy(self._data["learning_rows"][self._day(as_of)])

    def require_dates(self, dates: list[date]) -> None:
        for day in dates:
            self._day(day)

    def require_current_source(self) -> None:
        from app.infrastructure.vendors.vn.factor_scores import FACTOR_SOURCE_VERSION
        if self.version == 2 and self._data.get("source_version") != FACTOR_SOURCE_VERSION:
            raise ValueError("Rebuilt factor snapshot source version differs from the current producer")
        if self.version == 2 and self._data.get("source_code_sha256") != self._source_hashes():
            raise ValueError("Rebuilt factor snapshot source code hash differs from the current producer")

    @staticmethod
    def _source_hashes() -> dict:
        root = Path(__file__).resolve().parents[2]
        return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in (
            "infrastructure/vendors/vn/factor_scores.py", "infrastructure/vendors/vn/sector_groups.py",
            "domain/services/financial_fields.py", "domain/repositories/replay_factor_snapshot.py",
        )}

    @contextmanager
    def activate(self):
        token = _active.set(self)
        try:
            yield self
        finally:
            _active.reset(token)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"sha256": self.sha256, "data": self._data}, stream, ensure_ascii=False, allow_nan=False)

    @classmethod
    def load(cls, path: Path) -> ReplayFactorSnapshot:
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = cls(payload["data"])
        if snapshot.sha256 != payload["sha256"]:
            raise ValueError("Replay factor snapshot hash mismatch")
        return snapshot

    @classmethod
    def capture(cls, dates: list[date]) -> ReplayFactorSnapshot:
        dates = sorted(set(dates))
        if not dates:
            raise ValueError("Replay factor snapshot requires as-of dates")
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("""
                SELECT symbol, score_date, value_score, quality_score, momentum_3m,
                       earnings_yield_score, foreign_flow_score, volatility_score,
                       composite_score, percentile, factor_details
                FROM factor_scores WHERE score_date = ANY(%s)
                ORDER BY symbol, score_date
            """, (dates,))
            factor_rows = [
                [str(row[0]), row[1].isoformat(),
                 *(financial_number(value) for value in row[2:10]),
                 row[10] if isinstance(row[10], dict) else {}]
                for row in cur.fetchall()
            ]
            learning_rows = {}
            for day in dates:
                cur.execute(LEARNING_FACTOR_SQL, (day,))
                learning_rows[day.isoformat()] = [
                    [row[0], *(financial_number(value) for value in row[1:])]
                    for row in cur.fetchall()
                ]
        return cls(dict(version=1, source_policy="PROD_EXACT_DATE_CACHE_WITH_GENERIC_FALLBACK",
                        as_of_dates=[day.isoformat() for day in dates],
                        factor_rows=factor_rows, learning_rows=learning_rows))
