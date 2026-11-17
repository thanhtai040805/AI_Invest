"""Unit tests for corporate-action adjusted OHLCV synchronization and unadjusted price preservation."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from app.domain.pipeline.daily_etl import DailyETLPipeline
from app.infrastructure.data_pipelines import corporate_actions, ohlcv_backfill


class _DriftCursor:
    def __init__(self, mdd_rows, ohlcv_rows):
        self._mdd_rows = mdd_rows
        self._ohlcv_rows = ohlcv_rows
        self._last = []

    def execute(self, sql, params=None):
        if "FROM market_data_daily" in sql:
            self._last = list(self._mdd_rows)
        elif "FROM ohlcv" in sql:
            self._last = list(self._ohlcv_rows)
        else:
            self._last = []

    def fetchall(self):
        return self._last


class _RecordingCursor:
    def __init__(self, rowcount=0):
        self.many_calls = []
        self.exec_calls = []
        self.rowcount = rowcount

    def executemany(self, sql, rows):
        self.many_calls.append((sql, list(rows)))

    def execute(self, sql, params=None):
        self.exec_calls.append((sql, params))


def test_has_adjusted_price_drift_detects_shifted_historical_close():
    rows = [
        (date(2026, 11, 4), "DPR", 36.1, 36.6, 36.0, 36.44, 100000),
        (date(2026, 11, 17), "DPR", 35.4, 35.4, 34.85, 34.9, 120000),
    ]
    drifted_cur = _DriftCursor(
        mdd_rows=[(date(2026, 11, 4), 37.85), (date(2026, 11, 17), 35.1)],
        ohlcv_rows=[(date(2026, 11, 4), 37.85)],
    )
    assert ohlcv_backfill.has_adjusted_price_drift(drifted_cur, "DPR", rows, expected_date=date(2026, 11, 17)) is True

    clean_cur = _DriftCursor(
        mdd_rows=[(date(2026, 11, 4), 36.44), (date(2026, 11, 17), 35.1)],
        ohlcv_rows=[(date(2026, 11, 4), 36.44)],
    )
    assert ohlcv_backfill.has_adjusted_price_drift(clean_cur, "DPR", rows, expected_date=date(2026, 11, 17)) is False


def test_upsert_today_preserves_unadjusted_and_populates_adj_columns():
    cur = _RecordingCursor()
    rows = [
        (date(2026, 11, 4), "DPR", 36.1, 36.6, 36.0, 36.44, 100000),
        (date(2026, 11, 17), "DPR", 35.4, 35.4, 34.85, 34.9, 120000),
    ]

    saved = ohlcv_backfill.upsert_today(cur, rows, expected_date=date(2026, 11, 17), has_drift=True)
    assert saved == 2

    ohlcv_sql, ohlcv_rows = cur.many_calls[0]
    assert "adj_close" in ohlcv_sql
    assert ohlcv_rows[0][-1] == 36.44
    assert ohlcv_rows[1][-1] == 34.9

    mdd_sql, mdd_rows = cur.many_calls[1]
    assert "adj_factor" in mdd_sql
    assert "WHEN market_data_daily.close_unadj IS NOT NULL AND market_data_daily.close_unadj > 0" in mdd_sql
    # Historical pre-ex-date bar with drift must not seed an adjusted price into close_unadj
    assert mdd_rows[0][6] is None
    assert mdd_rows[1][6] == 34.9

    assert any("UPDATE ohlcv AS o" in sql and params == ("DPR", date(2026, 11, 4), date(2026, 11, 17)) for sql, params in cur.exec_calls)


def test_sync_adjusted_history_updates_only_adjusted_columns_never_close_unadj(monkeypatch):
    tz_vn = ohlcv_backfill.TZ_VN
    ts_old = int(datetime(2020, 6, 1, 0, 0, tzinfo=tz_vn).timestamp())
    ts_pre = int(datetime(2026, 11, 3, 0, 0, tzinfo=tz_vn).timestamp())
    ts_cur = int(datetime(2026, 11, 17, 0, 0, tzinfo=tz_vn).timestamp())

    monkeypatch.setattr(
        ohlcv_backfill,
        "fetch_full_history_ohlcv",
        lambda client, symbol, target_date=None: {
            "t": [ts_old, ts_pre, ts_cur],
            "o": [14.24, 35.91, 35.4],
            "h": [14.26, 35.96, 35.4],
            "l": [14.14, 35.52, 34.85],
            "c": [14.18, 35.62, 34.9],
            "v": [10000, 20000, 30000],
        },
    )

    recorded = []

    def fake_execute_values(cur, sql, argslist, page_size=1000):
        recorded.append((sql, list(argslist)))
        if "UPDATE market_data_daily" in sql:
            cur.rowcount = len(argslist)

    monkeypatch.setattr(ohlcv_backfill, "execute_values", fake_execute_values)
    cur = _RecordingCursor(rowcount=0)

    updated = ohlcv_backfill.sync_adjusted_history_for_symbol(
        client=object(),
        cur=cur,
        symbol="DPR",
        target_date=date(2026, 11, 17),
    )

    assert updated == 3
    assert len(recorded) == 2

    mdd_sql, mdd_vals = recorded[0]
    set_section = mdd_sql.split("SET", 1)[1].split("FROM", 1)[0]
    assert "open_adj" in set_section
    assert "high_adj" in set_section
    assert "low_adj" in set_section
    assert "close_adj" in set_section
    assert "adj_factor" in set_section
    assert "close_unadj =" not in set_section
    assert "close_unadj=" not in set_section
    assert [row[1] for row in mdd_vals] == [date(2020, 6, 1), date(2026, 11, 3), date(2026, 11, 17)]

    ohlcv_sql, ohlcv_vals = recorded[1]
    assert "UPDATE ohlcv AS o" in ohlcv_sql
    assert "adj_close = v.close_adj" in ohlcv_sql
    assert "adj_factor" in ohlcv_sql
    assert len(ohlcv_vals) == 3


def test_daily_etl_runs_corporate_actions_right_after_ohlcv_backfill(monkeypatch):
    pipeline = DailyETLPipeline()
    order = []

    async def ok_ohlcv():
        order.append("ohlcv_backfill")
        return {"target_rows": 400}

    async def ok_other(name):
        order.append(name)
        return {"status": "success"}

    monkeypatch.setattr("app.domain.pipeline.daily_etl.set_running", lambda *a, **kw: None)
    monkeypatch.setattr("app.domain.pipeline.daily_etl.set_completed", lambda *a, **kw: None)
    monkeypatch.setattr("app.domain.pipeline.daily_etl.set_failed", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "step_ohlcv_backfill", ok_ohlcv)
    monkeypatch.setattr(corporate_actions, "refresh_incremental", lambda partition_index=None: {"status": "success", "actions_upserted": 2})
    monkeypatch.setattr(
        ohlcv_backfill,
        "reconcile_adjusted_prices",
        lambda target_date=None, lookback_days=21: order.append("reconcile_adjusted_prices") or {"checked_symbols": 2, "updated_symbols": ["DPR"], "updated_rows": 3600},
    )

    for step_name in (
        "step_technical_indicators",
        "step_garch_volatility",
        "step_beta_alpha",
        "step_insider_trades",
        "step_foreign_flow",
        "step_financial_ratios",
        "step_corporate_documents",
        "step_factor_scores",
        "step_macro_indicators",
    ):
        monkeypatch.setattr(pipeline, step_name, lambda n=step_name: ok_other(n))

    result = asyncio.run(pipeline.run(trade_date=date(2026, 11, 17)))

    assert order[0] == "ohlcv_backfill"
    assert order[1] == "reconcile_adjusted_prices"
    assert order[2] == "step_technical_indicators"
    assert result["corporate_actions"]["adj_reconciliation"]["updated_symbols"] == ["DPR"]
