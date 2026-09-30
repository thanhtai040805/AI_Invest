from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.repositories.portfolio_repository import execution_amounts


def test_cash_receipt_keeps_full_vwap_not_display_price():
    gross, fee, tax, delta = execution_amounts(300, 24900 + 1 / 3, 'BUY')
    assert gross == 7470100
    assert fee == 10000
    assert tax == 0
    assert delta == -7480100


def test_daily_nav_is_persisted_with_close_mark(monkeypatch):
    storage = Storage()
    repository = PortfolioRepository(storage)
    state = {'cash_balance': 100, 'total_nav': 500, 'peak_nav': 500, 'drawdown_tier': 'GREEN'}
    monkeypatch.setattr(repository, 'get_account_state', lambda **kw: state)
    repository.record_replay_mark(user_id='ml', mark_as_of=date(2026, 8, 12))
    assert any('INSERT INTO portfolio_nav_history' in sql and params == ('ml', date(2026, 8, 12), 500, 100) for sql, params in storage.calls)
    assert storage.committed


class Storage:
    def __init__(self, status='PENDING_REPLAY'):
        self.status=status
        self.calls=[]
        self.committed=False
        self.rolled_back=False
    def begin(self): pass
    def execute(self, sql, params=None): self.calls.append((sql,params))
    def fetch_all(self, sql, params=None):
        if 'SELECT user_id, symbol, side, quantity, status, price' in sql:
            return [('ml','HPG','BUY',100,self.status,25000)]
        if 'SELECT cash_balance FROM users' in sql: return [(10_000_000,)]
        if 'SELECT id, quantity, avg_price, opened_at' in sql: return []
        raise AssertionError(sql)
    def commit(self): self.committed=True
    def rollback(self): self.rolled_back=True


def execute(repository, price=24900):
    return repository.record_replay_execution('HPG','BUY',100,price,user_id='ml',
        executed_at=datetime(2026,8,13,9,46,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')),
        mark_as_of=date(2026,8,12),pending_order_id='pending-ml')


def test_ml_replay_fill_updates_original_order_and_all_account_tables(monkeypatch):
    storage=Storage()
    repository=PortfolioRepository(storage)
    monkeypatch.setattr(repository,'get_account_state',lambda **kw:{'cash_balance':7_500_000,'total_nav':10_000_000,'peak_nav':10_000_000,'drawdown_tier':'GREEN'})
    result=execute(repository)
    assert result['order_id']=='pending-ml'
    assert storage.committed
    assert not any('INSERT INTO orders' in sql for sql,_ in storage.calls)
    assert any("status = 'FILLED_REPLAY'" in sql for sql,_ in storage.calls)
    assert all(any(table in sql for sql,_ in storage.calls) for table in ['UPDATE users','INSERT INTO positions','INSERT INTO order_executions','INSERT INTO paper_trades','INSERT INTO portfolio_account'])
    debit=next(params[0] for sql,params in storage.calls if 'UPDATE users SET cash_balance' in sql)
    assert debit==-(24900*100+10000)


def test_replay_cannot_fill_an_order_twice():
    storage=Storage('FILLED_REPLAY')
    with pytest.raises(ValueError,match='no longer pending'):
        execute(PortfolioRepository(storage))
    assert storage.rolled_back
    assert storage.calls==[]


def test_replay_cannot_fill_above_limit():
    storage=Storage()
    with pytest.raises(ValueError,match='exceeds limit'):
        execute(PortfolioRepository(storage),price=25050)
    assert storage.rolled_back
    assert storage.calls==[]


def test_multi_replay_without_pending_order_creates_learning_lot(monkeypatch):
    storage = Storage()
    repository = PortfolioRepository(storage)
    monkeypatch.setattr(repository, 'get_account_state', lambda **kw: {
        'cash_balance': 7_500_000, 'total_nav': 10_000_000,
        'peak_nav': 10_000_000, 'drawdown_tier': 'GREEN'})
    repository.record_replay_execution('HPG', 'BUY', 100, 24900, user_id='multi',
        executed_at=datetime(2026, 8, 13, 10, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')),
        mark_as_of=date(2026, 8, 12))
    assert storage.committed
    assert any('INSERT INTO orders' in sql for sql, _ in storage.calls)
    assert any('INSERT INTO paper_trades' in sql and params[-1] == 'multi'
               for sql, params in storage.calls)


def test_multi_partial_sale_closes_only_filled_quantity_and_keeps_entry_date(monkeypatch):
    entry_date = datetime(2026, 8, 10, 10)
    class SellStorage(Storage):
        def fetch_all(self, sql, params=None):
            if 'SELECT id, quantity, avg_price, opened_at' in sql:
                return [('position', 100, 20000, entry_date)]
            if 'SELECT id, price, quantity, date FROM paper_trades' in sql:
                return [('lot', 20000, 100, entry_date)]
            return super().fetch_all(sql, params)
    storage = SellStorage()
    repository = PortfolioRepository(storage)
    monkeypatch.setattr(repository, 'get_account_state', lambda **kw: {
        'cash_balance': 10_000_000, 'total_nav': 11_000_000,
        'peak_nav': 11_000_000, 'drawdown_tier': 'GREEN'})
    repository.record_replay_execution('HPG', 'SELL', 40, 25000, user_id='multi',
        executed_at=datetime(2026, 8, 14, 14, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')),
        mark_as_of=date(2026, 8, 13))
    assert storage.committed
    assert any('quantity=quantity-%s' in sql and params == (40, 'lot') for sql, params in storage.calls)
    closed = next(params for sql, params in storage.calls if 'INSERT INTO paper_trades' in sql)
    assert closed[2] == entry_date
    assert closed[3] == 40
    assert closed[5] == 25
