from datetime import date, timedelta
import os

import psycopg2
from psycopg2.extras import Json
import pytest

from app.infrastructure.data_pipelines.financial_statement_store import (
    upsert_financial_statement,
)


class Cursor:
    rowcount = 1

    def execute(self, query, params):
        self.query = query
        self.params = params


def run(
    *, allow_historical_updates=False, fill_only=False, period_end=date(2000, 3, 31)
):
    cur = Cursor()
    upsert_financial_statement(
        cur,
        "TEST",
        period_end,
        "IS",
        "quarterly",
        {"revenue": 2},
        "alphastock",
        None,
        allow_historical_updates=allow_historical_updates,
        fill_only=fill_only,
    )
    return cur


def test_backfill_can_replace_an_existing_historical_statement():
    cur = run(allow_historical_updates=True, period_end=date(2000, 3, 31))

    assert "AND TRUE" in cur.query
    assert len(cur.params) == 7


def test_normal_refresh_can_replace_only_recent_existing_statements():
    cur = run()

    assert "incoming.period_end >= %s::date" in cur.query
    assert "financial_statements.period_end >= %s::date" in cur.query
    assert cur.params[-2:] == (date.today() - timedelta(days=90),) * 2


def test_fill_only_fallback_keeps_sparse_alphastock_repair_available():
    cur = run(allow_historical_updates=False, fill_only=True)

    assert "financial_statements.source = 'alphastock'" in cur.query
    assert "jsonb_each(financial_statements.data)" in cur.query
    assert "metric.value <> 'null'::jsonb) < 8" in cur.query
    assert "incoming.period_end >= %s::date" in cur.query
    assert len(cur.params) == 9


def test_explicit_backfill_can_fill_a_sparse_historical_alphastock_row():
    cur = run(allow_historical_updates=True, fill_only=True)

    assert "financial_statements.source = 'alphastock'" in cur.query
    assert "AND TRUE" in cur.query
    assert len(cur.params) == 7


def test_explicit_backfill_refreshes_existing_fallback_rows():
    assert (
        "OR financial_statements.source = EXCLUDED.source"
        in run(
            allow_historical_updates=True,
            fill_only=True,
        ).query
    )
    assert (
        "OR financial_statements.source = EXCLUDED.source"
        not in run(fill_only=True).query
    )


@pytest.mark.parametrize(
    "source,field_count,replace",
    [
        ("alphastock", 8, False),
        ("alphastock", 3, True),
        ("cafef", 8, True),
    ],
)
def test_postgres_backfill_source_precedence(source, field_count, replace):
    url = os.environ.get("FINANCIAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set FINANCIAL_TEST_DATABASE_URL to run the PostgreSQL TEMP-table check"
        )
    conn = psycopg2.connect(url, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TEMP TABLE financial_statements (
                symbol text, period_end date, statement_type text, frequency text,
                data jsonb, source text, published_date date, fetched_at timestamp DEFAULT NOW(),
                PRIMARY KEY (symbol,period_end,statement_type,frequency)) ON COMMIT DROP""")
            old = {f"primary_{i}": i for i in range(field_count)}
            incoming = {f"alias_{i}": i for i in range(48)}
            period = date(2016, 6, 30)
            upsert_financial_statement(
                cur,
                "TEST",
                period,
                "IS",
                "quarterly",
                Json(old),
                source,
                None,
                allow_historical_updates=True,
            )
            upsert_financial_statement(
                cur,
                "TEST",
                period,
                "IS",
                "quarterly",
                Json(incoming),
                "cafef",
                None,
                fill_only=True,
                allow_historical_updates=True,
            )
            cur.execute(
                "SELECT data,source FROM financial_statements WHERE symbol='TEST'"
            )
            expected = (incoming, "cafef") if replace else (old, source)
            assert cur.fetchone() == expected
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("normalize_history", [False, True])
def test_postgres_explicit_backfill_repairs_retained_historical_aliases(
    normalize_history,
):
    from app.infrastructure.data_pipelines import financial_etl_cafef as cafef

    url = os.environ.get("FINANCIAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set FINANCIAL_TEST_DATABASE_URL to run the PostgreSQL TEMP-table check"
        )
    conn = psycopg2.connect(url, connect_timeout=5)
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TEMP TABLE financial_statements (
                symbol text, period_end date, statement_type text, frequency text,
                data jsonb, source text, published_date date, fetched_at timestamptz,
                PRIMARY KEY (symbol,period_end,statement_type,frequency)) ON COMMIT DROP""")
            ratios = "pe pb roe roa debt_equity current_ratio gross_margin net_margin fcf_yield ev_ebitda yoy_revenue_growth yoy_earnings_growth".split()
            cur.execute(
                "CREATE TEMP TABLE financial_ratios (symbol text, ratio_date date, frequency text,"
                + ",".join(f"{name} double precision" for name in ratios)
                + ",published_date date,updated_at timestamptz,PRIMARY KEY(symbol,ratio_date,frequency)) ON COMMIT DROP"
            )
            period, available = date(2000, 3, 31), date(2000, 5, 15)
            statements = {
                "BS": {"Tổng tài sản": 1000, "Vốn chủ sở hữu": 100},
                "IS": {
                    "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 10,
                    "lợi nhuận sau thuế": 8,
                    "18_loi_nhuan_sau_thue": 8,
                },
                "CF": {
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh trước những thay đổi về tài sản và vốn lưu động": 9,
                    "I. Lưu chuyển tiền thuần từ hoạt động kinh doanh": -42,
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh": 9,
                },
            }
            for kind, data in statements.items():
                upsert_financial_statement(
                    cur,
                    "TEST",
                    period,
                    kind,
                    "quarterly",
                    Json(data),
                    "cafef",
                    available,
                    allow_historical_updates=True,
                )
            cur.execute(
                "UPDATE financial_statements SET fetched_at='2000-05-15 00:00:00+00'"
            )
            if normalize_history:
                cafef._store_derived_ratios("TEST", cur, normalize_statements=True)
            else:
                cafef._store_derived_ratios("TEST", cur)
            cur.execute(
                "SELECT statement_type,data,published_date,fetched_at FROM financial_statements ORDER BY statement_type"
            )
            stored = {kind: data for kind, data, _, _ in cur.fetchall()}
            assert stored["IS"]["lợi nhuận sau thuế"] == (
                10 if normalize_history else 8
            )
            assert stored["CF"]["Lưu chuyển tiền thuần từ hoạt động kinh doanh"] == (
                -42 if normalize_history else 9
            )
            cur.execute(
                "SELECT count(*) FROM financial_statements WHERE published_date=%s AND fetched_at='2000-05-15 00:00:00+00'",
                (available,),
            )
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT roe FROM financial_ratios")
            assert cur.fetchone()[0] == pytest.approx(0.1)
    finally:
        conn.rollback()
        conn.close()
