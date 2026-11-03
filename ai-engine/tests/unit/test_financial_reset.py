"""Reset and learning-order contracts against a disposable LOCAL PostgreSQL DB."""
import os
from datetime import date

import psycopg2
from psycopg2.extras import Json, execute_values
import pytest
from datetime import timedelta

from app.infrastructure.data_pipelines.financial_statement_store import stage_financial_reset, publish_financial_reset
from app.domain.repositories.replay_factor_snapshot import LEARNING_FACTOR_SQL
from app.domain.services.factor_service import FACTOR_SOURCE_VERSION
from app.infrastructure.data_pipelines.financial_etl_cafef import _store_derived_ratios
from app.infrastructure.vendors.vn.factor_scores import compute_factor_scores


@pytest.mark.parametrize('observations',[61,129,130])
def test_vn_momentum_uses_its_own_window_and_preserves_negative_earnings(conn,observations):
    asof=date(2026,8,11)
    days=[]
    day=asof
    while len(days)<observations:
        if day.weekday()<5:days.append(day)
        day-=timedelta(days=1)
    with conn.cursor() as cur:
        for table,columns in {
            'ohlcv_unadjusted':'symbol text,time timestamp,close double precision,volume double precision',
            'technical_indicators':'symbol text,calc_date date,indicators jsonb',
            'stocks':'symbol text,market_cap double precision,sector text,industry text,ceiling double precision,floor double precision',
            'foreign_flow':'symbol text,trade_date date,net_value double precision,net_volume bigint,room_remaining double precision,room_limit double precision,ownership_pct double precision',
            'insider_trades':'symbol text,trade_date date,trade_type text,quantity bigint',
            'macro_indicators':'indicator_name text,indicator_date date,value double precision',
        }.items():
            cur.execute(f'CREATE TEMP TABLE {table} ({columns}) ON COMMIT DROP')
        for symbol,pe in [('NEGTEST',-10),('POSTEST',10)]:
            cur.execute("INSERT INTO financial_ratios (symbol,ratio_date,frequency,pe,pb,roe,published_date) VALUES (%s,'2026-03-31','quarterly',%s,1,.1,'2026-05-20')",(symbol,pe))
            execute_values(cur,'INSERT INTO ohlcv_unadjusted (symbol,time,close,volume) VALUES %s',
                           [(symbol,day,20+(observations-i)/10,1000000) for i,day in enumerate(days)])
        rows={r[0]:r for r in compute_factor_scores(['NEGTEST','POSTEST'],asof,cur)}
        assert rows['NEGTEST'][5] is not None  # F3 needs 61 closes, not 130.
        assert rows['POSTEST'][5] is not None
        assert rows['NEGTEST'][12] is not None
        assert rows['NEGTEST'][12]<rows['POSTEST'][12]  # Signed E/P ranks loss below profit.


@pytest.fixture
def conn():
    url = os.environ.get('FINANCIAL_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires a disposable LOCAL financial reset test database')
    cfg = psycopg2.extensions.parse_dsn(url)
    if cfg.get('host') not in ('localhost', '127.0.0.1', '::1') or not cfg.get('dbname', '').startswith('aiinvest_backfill_audit_'):
        pytest.fail('Reset integration tests require a disposable LOCAL audit database')
    connection = psycopg2.connect(url)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def seed(cur, schema, symbol, value):
    for kind in ('BS', 'IS'):
        cur.execute(f'INSERT INTO {schema}.financial_statements (symbol,period_end,statement_type,frequency,data,source) VALUES (%s,%s,%s,%s,%s,%s)',
                    (symbol, date(2025,12,31), kind, 'quarterly', Json({'amount':value}), 'cafef'))
    cur.execute(f'INSERT INTO {schema}.financial_ratios (symbol,ratio_date,frequency,roe) VALUES (%s,%s,%s,%s)',
                (symbol,date(2025,12,31),'quarterly',value))


def test_reset_replaces_selected_history_preserving_other_symbols(conn):
    with conn.cursor() as cur:
        seed(cur,'public','RESET_TEST',1)
        seed(cur,'public','OUTSIDE_TEST',9)
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('RESET_TEST','2025-12-31',50),('OUTSIDE_TEST','2025-12-31',60)")
        cur.execute("INSERT INTO public.financial_ratios(symbol,ratio_date,frequency) VALUES ('RESET_TEST','2000-01-01','quarterly')")
        stage_financial_reset(cur)
        seed(cur,'pg_temp','RESET_TEST',2)
        assert publish_financial_reset(cur,['RESET_TEST']) == {'financial_statements':2,'financial_ratios':1,'factor_scores_invalidated':1}
        cur.execute("SELECT symbol,roe FROM public.financial_ratios WHERE symbol IN ('RESET_TEST','OUTSIDE_TEST') ORDER BY symbol")
        assert cur.fetchall() == [('OUTSIDE_TEST',9),('RESET_TEST',2)]
        cur.execute("SELECT symbol FROM public.factor_scores WHERE symbol IN ('RESET_TEST','OUTSIDE_TEST')")
        assert cur.fetchall()==[('OUTSIDE_TEST',)]


def test_incomplete_batch_cannot_replace_previous_data(conn):
    with conn.cursor() as cur:
        seed(cur,'public','RESET_TEST',1)
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('RESET_TEST','2025-12-31',50)")
        cur.execute('SAVEPOINT before_batch')
        stage_financial_reset(cur)
        with pytest.raises(ValueError,match='Incomplete'):
            publish_financial_reset(cur,['RESET_TEST'])
        cur.execute('ROLLBACK TO SAVEPOINT before_batch')
        cur.execute("SELECT roe FROM public.financial_ratios WHERE symbol='RESET_TEST'")
        assert cur.fetchall()==[(1,)]
        cur.execute("SELECT composite_score FROM public.factor_scores WHERE symbol='RESET_TEST'")
        assert cur.fetchall()==[(50,)]


def test_reset_rejects_loss_of_reported_eps_bvps_instead_of_patching_old_values(conn):
    with conn.cursor() as cur:
        seed(cur,'public','RESET_TEST',1)
        cur.execute("INSERT INTO public.financial_statements(symbol,period_end,statement_type,frequency,data,source) VALUES ('RESET_TEST','2025-12-31','ratios','quarterly',%s,'alphastock')",
                    (Json({'eps':0,'bvps':10000}),))
        stage_financial_reset(cur)
        seed(cur,'pg_temp','RESET_TEST',2)
        cur.execute("INSERT INTO pg_temp.financial_statements(symbol,period_end,statement_type,frequency,data,source) VALUES ('RESET_TEST','2025-12-31','ratios','quarterly',%s,'alphastock')",
                    (Json({'eps':None,'bvps':None,'pe':10}),))
        with pytest.raises(ValueError,match='erase 2 reported per-share inputs'):
            publish_financial_reset(cur,['RESET_TEST'])
        cur.execute("SELECT data FROM public.financial_statements WHERE symbol='RESET_TEST' AND statement_type='ratios'")
        assert cur.fetchone()[0]=={'eps':0,'bvps':10000}


def test_out_of_scope_stage_is_rejected(conn):
    with conn.cursor() as cur:
        stage_financial_reset(cur)
        seed(cur,'pg_temp','RESET_TEST',2)
        seed(cur,'pg_temp','OUTSIDE_TEST',3)
        with pytest.raises(ValueError,match='out-of-scope'):
            publish_financial_reset(cur,['RESET_TEST'])


def test_rollback_restores_financials_and_invalidated_factors_together(conn):
    with conn.cursor() as cur:
        seed(cur,'public','RESET_TEST',1)
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('RESET_TEST','2025-12-31',50)")
        cur.execute('SAVEPOINT before_reset')
        stage_financial_reset(cur)
        seed(cur,'pg_temp','RESET_TEST',2)
        publish_financial_reset(cur,['RESET_TEST'])
        cur.execute('ROLLBACK TO SAVEPOINT before_reset')
        cur.execute("SELECT roe FROM public.financial_ratios WHERE symbol='RESET_TEST'")
        assert cur.fetchall()==[(1,)]
        cur.execute("SELECT composite_score FROM public.factor_scores WHERE symbol='RESET_TEST'")
        assert cur.fetchall()==[(50,)]


def test_learning_selection_is_independent_of_physical_insertion_order(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TEMP TABLE factor_scores(symbol text,score_date date,value_score float,
            quality_score float,momentum_3m float,earnings_yield_score float,
            foreign_flow_score float,volatility_score float,composite_score float,factor_details jsonb) ON COMMIT DROP''')
        details=Json({'source':{'producer':'FACTOR_SERVICE','version':FACTOR_SOURCE_VERSION,'as_of':'2026-08-11','cache_complete':True}})
        rows=[(f'SYM{i:03}',date(2026,8,11),i,i,i,i,i,17.,i,details) for i in range(150)]
        execute_values(cur,'INSERT INTO pg_temp.factor_scores VALUES %s',rows)
        cur.execute(LEARNING_FACTOR_SQL,(date(2026,8,11),));first=cur.fetchall()
        cur.execute('TRUNCATE pg_temp.factor_scores')
        execute_values(cur,'INSERT INTO pg_temp.factor_scores VALUES %s',list(reversed(rows)))
        cur.execute(LEARNING_FACTOR_SQL,(date(2026,8,11),));second=cur.fetchall()
        assert first==second
        assert [r[0] for r in first]==[f'SYM{i:03}' for i in range(100)]
        assert first[0][3] == 0 and first[0][6] == 17.  # F3 and F6 are independent.


def test_learning_excludes_undated_or_mismatched_cache(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TEMP TABLE factor_scores(symbol text,score_date date,value_score float,
            quality_score float,momentum_3m float,earnings_yield_score float,
            foreign_flow_score float,volatility_score float,composite_score float,factor_details jsonb) ON COMMIT DROP''')
        for symbol,details in [('OLD',{}),('SHIFTED',{'source':{'producer':'FACTOR_SERVICE','as_of':'2026-08-10','cache_complete':True}}),
                               ('STRING_STATUS',{'source':{'producer':'FACTOR_SERVICE','as_of':'2026-08-11','cache_complete':'true'}}),
                               ('VN',{'source':{'producer':'VN_FACTOR_SCORES','version':FACTOR_SOURCE_VERSION,'as_of':'2026-08-11','cache_complete':True}}),
                               ('GOOD',{'source':{'producer':'FACTOR_SERVICE','version':FACTOR_SOURCE_VERSION,'as_of':'2026-08-11','cache_complete':True}})]:
            cur.execute('INSERT INTO pg_temp.factor_scores VALUES (%s,%s,50,50,50,50,50,50,50,%s)',(symbol,date(2026,8,11),Json(details)))
        cur.execute(LEARNING_FACTOR_SQL,(date(2026,8,11),))
        assert [row[0] for row in cur.fetchall()]==['GOOD']
        cur.execute(LEARNING_FACTOR_SQL,(date(2026,8,12),))
        assert cur.fetchall()==[]  # Never carry yesterday's cache into a missing day.


def test_vn_composite_cannot_overwrite_generic_css(conn):
    from app.infrastructure.vendors.vn.composite_pipeline import load_factor_details, write_composite_scores
    with conn.cursor() as cur:
        day=date(2025,12,31)
        cur.execute('CREATE TEMP TABLE factor_scores(symbol text,score_date date,composite_score float,factor_details jsonb,updated_at timestamptz) ON COMMIT DROP')
        for symbol,producer in [('GENERIC_COMPOSITE_TEST','FACTOR_SERVICE'),('VN_COMPOSITE_TEST','VN_FACTOR_SCORES')]:
            cur.execute('INSERT INTO pg_temp.factor_scores(symbol,score_date,composite_score,factor_details) VALUES (%s,%s,77,%s)',
                        (symbol,day,Json({'source':{'producer':producer},'css':77})))
        assert set(load_factor_details(day,cur=cur))=={'VN_COMPOSITE_TEST'}
        assert load_factor_details(day,['GENERIC_COMPOSITE_TEST'],cur)=={}
        write_composite_scores(day,{symbol:{'composite':.5} for symbol in ['GENERIC_COMPOSITE_TEST','VN_COMPOSITE_TEST']},[],cur)
        cur.execute("SELECT symbol,composite_score,factor_details->>'css' FROM pg_temp.factor_scores ORDER BY symbol")
        assert cur.fetchall()==[('GENERIC_COMPOSITE_TEST',77.,'77'),('VN_COMPOSITE_TEST',.5,'77')]


def seed_ratio_inputs(cur, schema='public'):
    for kind,data in [('BS',{'total_assets':2000,'equity':1000}),('IS',{'net_income':100,'net_revenue':500})]:
        cur.execute(f'INSERT INTO {schema}.financial_statements(symbol,period_end,statement_type,frequency,data,source) VALUES (%s,%s,%s,%s,%s,%s)',
                    ('CACHE_TEST',date(2025,12,31),kind,'quarterly',Json(data),'cafef'))


@pytest.mark.parametrize('backfill',[False,True])
def test_ratio_backfill_replaces_legacy_publication_metadata(conn,backfill):
    with conn.cursor() as cur:
        seed_ratio_inputs(cur)
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("UPDATE public.financial_ratios SET published_date='2026-10-23' WHERE symbol='CACHE_TEST'")
        _store_derived_ratios('CACHE_TEST',cur,normalize_statements=backfill)
        cur.execute("SELECT published_date FROM public.financial_ratios WHERE symbol='CACHE_TEST'")
        assert cur.fetchone()[0]==(None if backfill else date(2026,10,23))


def test_ratio_changes_invalidate_affected_dates_and_vn_peer_ranks(conn):
    with conn.cursor() as cur:
        seed_ratio_inputs(cur)
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('CACHE_TEST','2025-09-30',50),('CACHE_TEST','2025-12-31',50),('OUTSIDE_TEST','2025-12-31',50)")
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score,factor_details) VALUES ('PEER_TEST','2025-12-31',50,%s)",(Json({'source':{'producer':'VN_FACTOR_SCORES'}}),))
        cur.execute("UPDATE public.financial_statements SET data=%s WHERE symbol='CACHE_TEST' AND statement_type='IS'",(Json({'net_income':200,'net_revenue':500}),))
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("SELECT symbol,score_date FROM public.factor_scores WHERE symbol IN ('CACHE_TEST','OUTSIDE_TEST','PEER_TEST') ORDER BY symbol,score_date")
        assert cur.fetchall()==[('CACHE_TEST',date(2025,9,30)),('OUTSIDE_TEST',date(2025,12,31))]


def test_unchanged_ratio_recalculation_preserves_cache(conn):
    with conn.cursor() as cur:
        seed_ratio_inputs(cur)
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('CACHE_TEST','2025-12-31',50)")
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("SELECT composite_score FROM public.factor_scores WHERE symbol='CACHE_TEST'")
        assert cur.fetchall()==[(50,)]


def test_staged_ratio_recalculation_does_not_invalidate_public_cache_before_publish(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.factor_scores(symbol,score_date,composite_score) VALUES ('CACHE_TEST','2025-12-31',50)")
        stage_financial_reset(cur)
        seed_ratio_inputs(cur,'pg_temp')
        _store_derived_ratios('CACHE_TEST',cur)
        cur.execute("SELECT composite_score FROM public.factor_scores WHERE symbol='CACHE_TEST'")
        assert cur.fetchall()==[(50,)]
