import json
from datetime import date

from app.infrastructure.data_pipelines import latest_quote_cache, ohlcv_backfill


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        assert "close_unadj" in sql
        assert len(params) == 1

    def fetchall(self):
        return [("FPT", date(2026, 9, 30), 63.2, 62.8, 3000, "FPT Corp", 69.5, 56.9)]


class Connection:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return Cursor()

    def close(self):
        self.closed = True


class Pipeline:
    def __init__(self):
        self.values = {}
        self.executed = False

    def set(self, key, value):
        self.values[key] = value

    def execute(self):
        self.executed = True


class Redis:
    def __init__(self):
        self.pipe = Pipeline()

    def pipeline(self, transaction=False):
        assert transaction is False
        return self.pipe


def test_eod_refresh_persists_unadjusted_prices_without_expiry(monkeypatch):
    connection = Connection()
    redis = Redis()
    monkeypatch.setattr(ohlcv_backfill, "get_db_conn", lambda: connection)
    monkeypatch.setattr(latest_quote_cache, "get_redis", lambda: redis)

    assert latest_quote_cache.refresh_latest_quote_cache() == 1
    quote = json.loads(redis.pipe.values["stock:FPT:quote"])
    assert quote["price"] == 63200
    assert quote["ref"] == 62800
    assert quote["source"] == "postgres-eod"
    assert quote["stale"] is True
    assert redis.pipe.executed is True
    assert connection.closed is True
