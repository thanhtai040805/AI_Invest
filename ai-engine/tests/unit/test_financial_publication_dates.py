"""Reported availability must work identically before/after historical calendar cutoffs."""
from contextlib import nullcontext
from datetime import date, timedelta
import os

import psycopg2
import pytest

from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.services.factor_service import FactorService
from app.infrastructure.vendors.vn.factor_scores import _extract_fin_stmts, _extract_multi_stmts


@pytest.mark.parametrize('period,published', [
    (date(2026,3,31), date(2026,5,20)),
    (date(2026,6,30), date(2026,9,2)),
    (date(2026,6,30), date(2026,10,15)),
])
def test_report_cannot_be_read_before_its_valid_publication_date(monkeypatch, period, published):
    url = os.environ.get('FINANCIAL_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires a disposable LOCAL financial audit database')
    cfg = psycopg2.extensions.parse_dsn(url)
    assert cfg.get('host') in ('localhost', '127.0.0.1', '::1')
    assert cfg.get('dbname', '').startswith('aiinvest_backfill_audit_')
    conn = psycopg2.connect(url)
    class Storage:
        def fetch_all(self, query, params):
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO financial_ratios (symbol,ratio_date,frequency,pe,published_date) VALUES ('PUB_DATE_TEST',%s,'quarterly',10,%s)", (period,published))
            cur.execute("INSERT INTO financial_statements (symbol,period_end,statement_type,frequency,data,source,published_date) VALUES ('PUB_DATE_TEST',%s,'IS','quarterly','{}','test',%s)", (period,published))
            before = published-timedelta(days=1)
            repo = FinancialRepository(storage=Storage())
            assert repo.get_latest_ratios('PUB_DATE_TEST',as_of=before,allow_defaults=False) is None
            assert repo.get_latest_ratios('PUB_DATE_TEST',as_of=published,allow_defaults=False)['pe'] == 10
            assert repo.get_financial_statements('PUB_DATE_TEST','IS',as_of=before) == []
            assert repo.get_financial_statements('PUB_DATE_TEST','IS',as_of=published)[0]['period_end'] == str(period)
            assert not _extract_fin_stmts(cur,['PUB_DATE_TEST'],before)
            assert _extract_fin_stmts(cur,['PUB_DATE_TEST'],published)['PUB_DATE_TEST']['IS']['period_end'] == period
            assert not _extract_multi_stmts(cur,['PUB_DATE_TEST'],before)
            assert _extract_multi_stmts(cur,['PUB_DATE_TEST'],published)['PUB_DATE_TEST']['IS'][0]['period_end'] == period
            monkeypatch.setattr('app.domain.services.factor_service.get_conn',lambda:nullcontext(conn))
            service = FactorService(storage=Storage())
            assert service.compute_factors_for_ticker('PUB_DATE_TEST',before)['raw_metrics']['source']['ratio_date'] is None
            assert service.compute_factors_for_ticker('PUB_DATE_TEST',published)['raw_metrics']['source']['ratio_date'] == str(period)
    finally:
        conn.rollback()
        conn.close()
