import pytest

from app.domain.repositories.portfolio_repository import PortfolioRepository


class Storage:
    def __init__(self, pending_status="PENDING_SHADOW"):
        self.pending_status = pending_status
        self.executed = []
        self.rolled_back = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetch_all(self, sql, params=None):
        if "SELECT user_id, symbol, side, quantity, status FROM orders" in sql:
            return [("account", "FPT", "BUY", 100, self.pending_status)]
        if "SELECT id, user_id, symbol, side, price, quantity, order_type FROM orders" in sql:
            return [
                ("id1", "account", "FPT", "BUY", 25000, 100, "SHADOW_LIMIT"),
                ("id2", "ml-account", "HPG", "BUY", 30000, 200, "SHADOW_ML_LIMIT"),
            ]
        raise AssertionError(sql)

    def begin(self):
        pass

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        raise AssertionError("must not commit a duplicate fill")


def test_shadow_order_is_persisted_without_touching_cash_or_positions():
    storage = Storage()
    order_id = PortfolioRepository(storage).create_shadow_pending_order("fpt", 100, 25000, "account")

    sql, params = storage.executed[0]
    assert "INSERT INTO orders" in sql
    assert "PENDING_SHADOW" in sql
    assert params[1:6] == ("account", "FPT", "SHADOW_LIMIT", 25000, 100)
    assert order_id


def test_already_filled_shadow_order_cannot_debit_cash_twice():
    storage = Storage(pending_status="EXECUTED")
    repo = PortfolioRepository(storage)
    original_cash = repo._in_memory_account["cash_balance"]

    with pytest.raises(ValueError, match="no longer pending"):
        repo.execute_order_transaction(
            ticker="FPT", action="BUY", shares=100, executed_price=25000,
            user_id="account", pending_order_id="already-filled",
        )

    assert storage.rolled_back
    assert not any("cash_balance = cash_balance -" in sql for sql, _ in storage.executed)
    assert repo._in_memory_account["cash_balance"] == original_cash


def test_pending_scan_includes_both_shadow_channels():
    orders = PortfolioRepository(Storage()).get_pending_shadow_orders()

    assert [order["order_type"] for order in orders] == ["SHADOW_LIMIT", "SHADOW_ML_LIMIT"]
