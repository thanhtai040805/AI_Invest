from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.core.time_utils import VN_TZ, parse_vn_bound
from app.domain.repositories.portfolio_repository import PortfolioRepository, calculate_is_t25_locked


class _OrderStorage:
    def __init__(self, pending_status="PENDING_SHADOW"):
        self.pending_status = pending_status
        self.calls = []
        self.committed = False

    def begin(self):
        pass

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetch_all(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT user_id, symbol, side, quantity, status FROM orders" in sql:
            return [("acct", "FPT", "BUY", 100, self.pending_status)]
        if "SELECT cash_balance FROM users" in sql:
            return [(50_000_000,)]
        if "SELECT id, quantity, avg_price FROM positions" in sql:
            return []
        if "SELECT id, quantity, opened_at FROM positions" in sql:
            return [("pos-1", 100, datetime(2026, 11, 13, 2, 59, 20))]
        if "SELECT id, price, quantity, date FROM paper_trades" in sql:
            return []
        if "UPDATE orders SET status = 'EXPIRED'" in sql:
            return [("ord-1",)]
        raise AssertionError(sql)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def test_t25_unlocks_friday_utc_db_position_on_tuesday_afternoon():
    friday_utc_naive = datetime(2026, 11, 13, 2, 59, 20)  # 09:59:20 ICT Friday
    tuesday_morning_utc = datetime(2026, 11, 17, 3, 0, 0, tzinfo=timezone.utc)  # 10:00 ICT
    tuesday_afternoon_utc = datetime(2026, 11, 17, 6, 30, 0, tzinfo=timezone.utc)  # 13:30 ICT

    assert calculate_is_t25_locked(friday_utc_naive, tuesday_morning_utc) is True
    assert calculate_is_t25_locked(friday_utc_naive, tuesday_afternoon_utc) is False


def test_shadow_order_and_execution_write_naive_utc_and_aware_timestamptz(monkeypatch):
    storage = _OrderStorage()
    repo = PortfolioRepository(storage)
    monkeypatch.setattr(
        repo,
        "get_account_state",
        lambda **_kw: {
            "cash_balance": 47_490_000,
            "total_nav": 50_000_000,
            "peak_nav": 50_000_000,
            "drawdown_tier": "GREEN",
        },
    )

    repo.create_shadow_pending_order("FPT", 100, 25000, user_id="acct")
    shadow_sql, shadow_params = storage.calls[-1]
    assert "INSERT INTO orders" in shadow_sql
    assert shadow_params[7].tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - shadow_params[7]).total_seconds()) < 10

    storage.calls.clear()
    repo.execute_order_transaction("FPT", "BUY", 100, 25000, user_id="acct")
    pos_params = next(p for s, p in storage.calls if "INSERT INTO positions" in s)
    ord_params = next(p for s, p in storage.calls if "INSERT INTO orders" in s)
    exec_params = next(p for s, p in storage.calls if "INSERT INTO order_executions" in s)
    acct_params = next(p for s, p in storage.calls if "INSERT INTO portfolio_account" in s)
    paper_params = next(p for s, p in storage.calls if "INSERT INTO paper_trades" in s)

    assert pos_params[5].tzinfo is None
    assert ord_params[8].tzinfo is None
    assert paper_params[3].tzinfo is None
    assert exec_params[8].tzinfo is not None
    assert acct_params[5].tzinfo is not None


def test_replay_execution_writes_naive_utc_orders_and_positions(monkeypatch):
    class _ReplayStorage(_OrderStorage):
        def fetch_all(self, sql, params=None):
            if "SELECT id, quantity, avg_price, opened_at FROM positions" in sql:
                return []
            return super().fetch_all(sql, params)

    storage = _ReplayStorage()
    repo = PortfolioRepository(storage)
    monkeypatch.setattr(
        repo,
        "get_account_state",
        lambda **_kw: {
            "cash_balance": 47_490_000,
            "total_nav": 50_000_000,
            "peak_nav": 50_000_000,
            "drawdown_tier": "GREEN",
        },
    )
    executed_vn = datetime(2026, 8, 13, 9, 46, tzinfo=VN_TZ)
    repo.record_replay_execution(
        "HPG",
        "BUY",
        100,
        24900,
        user_id="ml",
        executed_at=executed_vn,
        mark_as_of=date(2026, 8, 12),
    )

    pos_params = next(p for s, p in storage.calls if "INSERT INTO positions" in s)
    ord_params = next(p for s, p in storage.calls if "INSERT INTO orders" in s)
    exec_params = next(p for s, p in storage.calls if "INSERT INTO order_executions" in s)
    assert pos_params[5] == datetime(2026, 8, 13, 2, 46)
    assert ord_params[8] == datetime(2026, 8, 13, 2, 46)
    assert exec_params[8].astimezone(timezone.utc) == datetime(2026, 8, 13, 2, 46, tzinfo=timezone.utc)


def test_expire_pending_shadow_orders_uses_utc_to_ict_cast():
    storage = _OrderStorage()
    repo = PortfolioRepository(storage)
    assert repo.expire_pending_shadow_orders() == 1
    sql = storage.calls[-1][0]
    assert "(created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Ho_Chi_Minh'" in sql


def test_parse_vn_bound_for_date_strings():
    assert parse_vn_bound("2026-11-16", is_end=False) == datetime(2026, 11, 16, 0, 0, 0, tzinfo=VN_TZ)
    assert parse_vn_bound("2026-11-16", is_end=True) == datetime(2026, 11, 16, 23, 59, 59, tzinfo=VN_TZ)
