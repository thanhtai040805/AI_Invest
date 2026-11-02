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
      AND factor_details->'source'->>'as_of' = score_date::text
      AND factor_details->'source'->>'producer' IN ('FACTOR_SERVICE', 'VN_FACTOR_SCORES')
      AND factor_details->'source'->'cache_complete' = 'true'::jsonb
    ORDER BY score_date DESC, symbol ASC LIMIT 100
"""
_active: ContextVar[ReplayFactorSnapshot | None] = ContextVar("replay_factor_snapshot", default=None)


def current_factor_snapshot() -> ReplayFactorSnapshot | None:
    return _active.get()


def factor_cache_row_is_usable(row) -> bool:
    """Accept a dated producer result, never an untraceable decision cache."""
    details = row[10] if len(row) == 11 and isinstance(row[10], dict) else {}
    source = details.get("source", {})
    if not isinstance(source, dict):
        return False
    usable = (
        source.get("producer") in ("FACTOR_SERVICE", "VN_FACTOR_SCORES")
        and source.get("as_of") == str(row[1])
        and source.get("cache_complete") is True
        and all((value := financial_number(raw)) is not None and 0 <= value <= 100
                for raw in row[2:8])
    )
    if not usable:
        return False
    if source["producer"] == "FACTOR_SERVICE":
        keys = ("f1_value", "f2_quality", "f3_momentum", "f4_earnings", "f5_flow", "f6_technical")
        return all(financial_number(details.get(key)) == financial_number(value)
                   for key, value in zip(keys, row[2:8]))
    return True


class ReplayFactorSnapshot:
    def __init__(self, data: dict):
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        self.sha256 = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self._data = json.loads(encoded)
        self.historical_reproduction = False
        self.historical_dates: set[str] | None = None
        self.factor_failures: list[dict] = []
        self.factor_observations: dict[str, dict] = {}
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

    def allows_historical_defaults(self, as_of: date) -> bool:
        return self.historical_reproduction and (
            self.historical_dates is None or str(as_of) in self.historical_dates
        )

    def extend_with_vn_producer(self, dates: list[date]) -> ReplayFactorSnapshot:
        """Keep archived dates intact; compute later dates without publishing source rows."""
        from app.infrastructure.vendors.vn.factor_scores import compute_factor_scores, select_scoring_symbols
        payload = deepcopy(self._data)
        archived_dates = set(self._dates)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            for day in sorted(set(dates)):
                if str(day) in archived_dates:
                    continue
                symbols = select_scoring_symbols(cur, day)
                cur.execute("SELECT symbol FROM stocks WHERE symbol = ANY(%s) AND (market_cap IS NULL OR market_cap <= 0) ORDER BY symbol", (symbols,))
                missing_market_cap = [row[0] for row in cur.fetchall()]
                if missing_market_cap:
                    raise ValueError(f"VN factor inputs missing at {day}: stocks.market_cap={missing_market_cap}")
                rows = compute_factor_scores(symbols, day, cur)
                absent = sorted(set(symbols) - {row[0] for row in rows})
                if not symbols or absent:
                    raise ValueError(f"VN factor coverage missing at {day}: {absent or 'empty universe'}")
                factors = []
                invalid = []
                for row in rows:
                    details = json.loads(row[20]) if isinstance(row[20], str) else row[20]
                    factor = [row[0], str(day), *(financial_number(row[i]) for i in (2, 3, 5, 12, 14, 8, 10, 11)), details]
                    missing = [key for key in ("pe", "pb", "roe")
                               if financial_number(details.get("financial_inputs", {}).get(key)) is None]
                    if missing or not factor_cache_row_is_usable(factor):
                        invalid.append({"symbol": row[0], "missing_financial_inputs": missing,
                                        "missing_factors": [f"F{i + 1}" for i, value in enumerate(factor[2:8])
                                                            if value is None or not 0 <= value <= 100]})
                    factors.append(factor)
                if invalid:
                    raise ValueError(f"Incomplete VN factors at {day}: {json.dumps(invalid)}")
                payload["factor_rows"].extend(factors)
                # Same learning columns/order as LEARNING_FACTOR_SQL, from this day's producer.
                payload["learning_rows"][str(day)] = [
                    [row[0], *(financial_number(row[i]) for i in (2, 3, 4, 12, 14, 10))]
                    for row in sorted(rows, key=lambda item: item[0])[:100]
                ]
                payload["as_of_dates"].append(str(day))
        combined = ReplayFactorSnapshot(payload)
        combined.historical_reproduction = self.historical_reproduction
        combined.historical_dates = archived_dates
        return combined

    def require_current_source(self) -> None:
        from app.infrastructure.vendors.vn.factor_scores import FACTOR_SOURCE_VERSION
        if self.version == 2 and self._data.get("source_version") != FACTOR_SOURCE_VERSION:
            raise ValueError("Rebuilt factor snapshot source version differs from the current producer")
        if self.version == 2 and self._data.get("source_code_sha256") != self._source_hashes():
            raise ValueError("Rebuilt factor snapshot source code hash differs from the current producer")

    def require_current_cache(self) -> None:
        """Reject stale file inputs before Replay mutates account state."""
        if any(not factor_cache_row_is_usable(row) for row in self._rows.values()):
            raise ValueError("Factor snapshot lacks valid producer/as-of inputs; rebuild the cache and capture again")
        current = self.capture([date.fromisoformat(day) for day in sorted(self._dates)])
        if self._rows != current._rows or self._data["learning_rows"] != current._data["learning_rows"]:
            raise ValueError("Factor snapshot differs from the current database cache; capture again after backfill")

    def require_historical_inputs(self, manifest_path: Path) -> None:
        """Explicit LOCAL archive reproduction; never certify legacy cache as current."""
        self.historical_reproduction = False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("purpose") != "HISTORICAL_BASELINE_REPRODUCTION"
                or manifest.get("factor_snapshot_sha256") != self.sha256):
            raise ValueError("Historical input manifest purpose/snapshot mismatch")
        tables = ("financial_statements", "financial_ratios", "factor_scores")
        if set(manifest.get("tables", {})) != set(tables):
            raise ValueError("Historical input manifest requires all three source tables")
        with get_conn() as conn, conn.cursor() as cur:
            config = conn.get_dsn_parameters()
            expected_host = manifest.get("target_host")
            allowed_hosts = (expected_host,) if expected_host else ("localhost", "127.0.0.1", "::1")
            if (config.get("host") not in allowed_hosts
                    or config.get("dbname") != manifest.get("target_database")):
                raise ValueError("Historical reproduction requires the manifest's exact target host/database")
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SET LOCAL TIME ZONE 'Asia/Ho_Chi_Minh'")
            for table in tables:
                cur.execute(f"SELECT md5(to_jsonb(t)::text) FROM public.{table} t ORDER BY 1")
                rows = cur.fetchall()
                actual = {"rows": len(rows), "sha256": hashlib.sha256(
                    "\n".join(row[0] for row in rows).encode()).hexdigest()}
                if actual != manifest["tables"][table]:
                    raise ValueError(f"Historical source table differs from the archive: {table}")
        self.historical_reproduction = True

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
            cached = cur.fetchall()
            factor_rows = [
                [str(row[0]), row[1].isoformat(),
                 *(financial_number(value) for value in row[2:10]),
                 row[10] if isinstance(row[10], dict) else {}]
                for row in cached if factor_cache_row_is_usable(row)
            ]
            learning_rows = {}
            for day in dates:
                cur.execute(LEARNING_FACTOR_SQL, (day,))
                learning_rows[day.isoformat()] = [
                    [row[0], *(financial_number(value) for value in row[1:])]
                    for row in cur.fetchall()
                ]
        return cls(dict(version=1, source_policy="PROD_EXACT_DATE_CACHE_WITH_GENERIC_FALLBACK",
                        rejected_factor_rows=len(cached) - len(factor_rows),
                        as_of_dates=[day.isoformat() for day in dates],
                        factor_rows=factor_rows, learning_rows=learning_rows))
