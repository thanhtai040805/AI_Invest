import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.domain.services.ml import standalone_ml_channel as ml
from app.domain.repositories.market_data_repository import MarketDataRepository


class Cursor:
    def __init__(self, cash=100_000_000, held=(), pending=(), duplicate=False, fail_order=False):
        self.cash, self.held, self.pending = cash, held, pending
        self.duplicate, self.fail_order = duplicate, fail_order
        self.calls, self.result = [], []

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if 'SELECT cash_balance' in sql: self.result = [(self.cash,)]
        elif 'SELECT id FROM users' in sql: self.result = [('ml-test',)]
        elif "side = 'SELL'" in sql and 'SELECT id FROM orders' in sql: self.result = []
        elif 'SELECT symbol FROM positions' in sql: self.result = [(s,) for s in self.held]
        elif 'SELECT symbol, quantity, price FROM orders' in sql: self.result = list(self.pending)
        elif 'INSERT INTO standalone_ml_predictions' in sql: self.result = [] if self.duplicate else [(1,)]
        elif 'INSERT INTO orders' in sql and self.fail_order: raise RuntimeError('order write failed')
    def fetchone(self): return self.result[0] if self.result else None
    def fetchall(self): return self.result


def setup_channel(monkeypatch, cursor):
    outcome = {'committed': False, 'rolled_back': False}
    @contextmanager
    def connection():
        try:
            yield SimpleNamespace(cursor=lambda: cursor)
            outcome['committed'] = True
        except Exception:
            outcome['rolled_back'] = True
            raise
    monkeypatch.setattr(ml, 'get_conn', connection)
    monkeypatch.setattr(MarketDataRepository, 'get_realtime_or_latest_price', lambda *a, **kw: 25_000)
    today = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    channel = ml.StandaloneMLChannel(account_id='ml-test')
    monkeypatch.setattr(channel, '_ensure_storage_and_account', lambda: None)
    monkeypatch.setattr(channel.portfolio_repo, 'get_account_state', lambda **kw: {'total_nav': 100_000_000, 'cash_balance': cursor.cash})
    predictions = pd.DataFrame([{'ticker': symbol, 'close': 25, 'feature_date': today-timedelta(days=1), 'rank_pred': 1, 'mom_pred': .01, 'surv_prob': .8, 'pred_score': score} for symbol, score in [('HPG', 2), ('FPT', 1)]])
    monkeypatch.setattr(channel, 'predict_universe', lambda **kw: predictions)
    return channel, today, outcome


def snapshots(cursor):
    return [params for sql, params in cursor.calls if 'INSERT INTO standalone_ml_predictions' in sql]


def test_candidate_filter_never_bypasses_holdings_cash_or_lot_budget(monkeypatch):
    cursor = Cursor(cash=2_500_000, held=('HPG',))
    channel, today, _ = setup_channel(monkeypatch, cursor)
    result = asyncio.run(channel.run_autonomous_cycle(today, candidate_tickers=['HPG', 'FPT']))
    assert result['orders'] == []
    assert [p[13] for p in snapshots(cursor)] == ['ALREADY_HELD', 'INSUFFICIENT_BUDGET']
    assert [p[8] for p in snapshots(cursor)] == [0, 0]


def test_all_signals_are_saved_and_pending_budget_includes_fees(monkeypatch):
    cursor = Cursor(cash=5_020_000, pending=(('OTHER', 100, 25_000),))
    channel, today, _ = setup_channel(monkeypatch, cursor)
    result = asyncio.run(channel.run_autonomous_cycle(today, max_candidates=1))
    assert len(result['orders']) == 1
    assert result['orders'][0]['shares'] == 100
    assert len(snapshots(cursor)) == 2
    assert snapshots(cursor)[1][13] == 'OUTSIDE_TOP_K'


def test_duplicate_day_does_not_requeue_or_overwrite_accuracy(monkeypatch):
    cursor = Cursor(duplicate=True)
    channel, today, _ = setup_channel(monkeypatch, cursor)
    assert asyncio.run(channel.run_autonomous_cycle(today))['orders'] == []
    assert not any('INSERT INTO orders' in sql for sql, _ in cursor.calls)
    assert all('DO NOTHING' in sql and 'DO UPDATE' not in sql for sql, _ in cursor.calls if 'INSERT INTO standalone_ml_predictions' in sql)


def test_order_failure_rolls_back_signal_and_order_together(monkeypatch):
    cursor = Cursor(fail_order=True)
    channel, today, outcome = setup_channel(monkeypatch, cursor)
    with pytest.raises(RuntimeError, match='order write failed'):
        asyncio.run(channel.run_autonomous_cycle(today))
    assert outcome == {'committed': False, 'rolled_back': True}


def test_historical_run_cannot_enter_live_queue(monkeypatch):
    cursor = Cursor()
    channel, today, _ = setup_channel(monkeypatch, cursor)
    with pytest.raises(ValueError, match='today-only'):
        asyncio.run(channel.run_autonomous_cycle(today-timedelta(days=1)))
    assert cursor.calls == []


def test_replay_requires_model_cutoff_and_uses_only_historical_prices(monkeypatch):
    cursor = Cursor()
    channel, today, _ = setup_channel(monkeypatch, cursor)
    day = today-timedelta(days=1)
    with pytest.raises(ValueError, match='trained strictly before'):
        asyncio.run(channel.run_autonomous_cycle(day, execution_mode='REPLAY', replay_prices={'HPG': 20_000}))
    channel.model = SimpleNamespace(trained_through=(day-timedelta(days=1)).isoformat(), model_version='frozen-model')
    channel.predict_universe = lambda **kw: pd.DataFrame([{'ticker':'HPG','feature_date':day-timedelta(days=1),'close':20,'rank_pred':1,'mom_pred':.01,'surv_prob':.8,'pred_score':2}])
    result = asyncio.run(channel.run_autonomous_cycle(day, execution_mode='REPLAY', replay_prices={'HPG': 20_000}))
    assert result['orders'][0]['price'] == 20_000
    order = next(params for sql, params in cursor.calls if 'INSERT INTO orders' in sql)
    assert order[3] == 'REPLAY_ML_LIMIT'
    assert order[6] == 'PENDING_REPLAY'
    assert order[7].date() == day


@pytest.mark.parametrize('stale,expected', [(False,1),(True,0)])
def test_ml_protection_queues_only_its_own_sell_with_fresh_depth(monkeypatch,stale,expected):
    import sys
    cursor=Cursor()
    channel,today,_=setup_channel(monkeypatch,cursor)
    monkeypatch.setenv('STANDALONE_ML_MODE','SHADOW_RUNNER')
    now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    monkeypatch.setattr(channel,'get_account_state',lambda:{'total_nav':100_000_000})
    monkeypatch.setattr(channel,'get_open_positions',lambda:[{'ticker':'HPG','shares':1000,'average_price':35000,'opened_at':now-timedelta(days=10)}])
    async def book(ticker):
        return {'marketState':'continuous_morning','lastUpdate':(now-timedelta(seconds=20 if stale else 1)).isoformat(),'bids':[{'price':24.9,'volume':2000}]}
    monkeypatch.setitem(sys.modules,'app.infrastructure.external_api.market_data_service',SimpleNamespace(market_data_svc=SimpleNamespace(get_order_book=book)))
    result=asyncio.run(channel.monitor_positions())
    assert result['queued']==expected
    inserts=[(sql,params) for sql,params in cursor.calls if 'INSERT INTO orders' in sql]
    assert len(inserts)==expected
    if expected:
        assert "'SELL'" in inserts[0][0]
        assert inserts[0][1][1]=='ml-test'
