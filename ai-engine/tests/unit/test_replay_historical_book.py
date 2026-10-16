from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from experiments import replay_agent_pipeline as replay


def test_replay_fills_partial_quantity_only_from_timestamped_depth(monkeypatch):
    day = date(2026, 9, 24)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    snapshots = [
        {"time": datetime(2026, 9, 24, 9, 15, 1, tzinfo=tz), "bid": [],
         "offer": [{"price": 23.10, "qtty": 100}]},
        {"time": datetime(2026, 9, 24, 9, 15, 2, tzinfo=tz), "bid": [],
         "offer": [{"price": 23.15, "qtty": 200}]},
    ]
    monkeypatch.setattr(replay, "quote_history", lambda *_: snapshots)

    class Repository:
        def __init__(self):
            self.fills = []

        def record_replay_execution(self, symbol, side, shares, price, **kwargs):
            self.fills.append((symbol, side, shares, price, kwargs["executed_at"], kwargs.get("decision_context")))
            return {"shares": shares, "executed_price": price}

    repo = Repository()
    fill_log = []
    remaining = replay.fill_pending(
        day,
        {"PVT": {"price": 23_000, "source": "DNSE"}},
        [{"symbol": "PVT", "side": "BUY", "quantity": 300, "limit_price": 23_500,
          "decision_context": {"allocation": {"rationale": "approved"}}}],
        repo,
        "account",
        date(2026, 9, 23),
        fill_log,
    )

    assert remaining == []
    assert [fill[2] for fill in repo.fills] == [100, 200]
    assert [fill[3] for fill in repo.fills] == [23_100, 23_150]
    assert all(fill[4] == snapshots[i]["time"] for i, fill in enumerate(repo.fills))
    assert all(fill[5] == {"allocation": {"rationale": "approved"}} for fill in repo.fills)
    assert sum(fill["quantity"] for fill in fill_log) == 300


def test_replay_advances_quote_cursor_across_database_timezone(monkeypatch):
    day = date(2026, 9, 8)
    db_tz = timezone(timedelta(hours=-7))
    snapshots = [
        {"time": datetime(2026, 9, 7, 19, 15, 1, tzinfo=db_tz), "bid": [],
         "offer": [{"price": 23.10, "qtty": 100}]},
        {"time": datetime(2026, 9, 7, 19, 15, 2, tzinfo=db_tz), "bid": [],
         "offer": [{"price": 23.15, "qtty": 100}]},
    ]

    class Repository:
        def __init__(self):
            self.fills = []

        def record_replay_execution(self, symbol, side, shares, price, **kwargs):
            self.fills.append((shares, kwargs["executed_at"]))
            return {"shares": shares, "executed_price": price}

    repo = Repository()
    pending = [{
        "symbol": "PVT", "side": "BUY", "quantity": 200, "limit_price": 23_500,
        "not_before": datetime(2026, 9, 8, 9, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
    }]
    cache = {(day, "PVT"): snapshots}
    for snapshot in snapshots:
        pending = replay.fill_pending(
            day, {"PVT": {"price": 23_000, "source": "DNSE"}}, pending,
            repo, "account", date(2026, 9, 7),
            until=snapshot["time"] + timedelta(microseconds=1),
            symbols={"PVT"}, quote_cache=cache,
        )

    assert [fill[0] for fill in repo.fills] == [100, 100]
    assert [fill[1] for fill in repo.fills] == [snapshots[0]["time"], snapshots[1]["time"]]
    assert pending == []


def test_replay_drops_pending_order_from_previous_session(monkeypatch):
    day = date(2026, 9, 8)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    snapshots = [{
        "time": datetime(2026, 9, 8, 9, 15, 1, tzinfo=tz),
        "bid": [], "offer": [{"price": 22.55, "qtty": 1000}],
    }]
    monkeypatch.setattr(replay, "quote_history", lambda *_: snapshots)

    class Repository:
        def record_replay_execution(self, *_args, **_kwargs):
            raise AssertionError("an expired order must not fill")

    pending = replay.fill_pending(
        day, {"HVN": {"price": 22_550, "source": "DNSE"}},
        [{"symbol": "HVN", "side": "BUY", "quantity": 400, "limit_price": 22_550,
          "not_before": datetime(2026, 9, 7, 9, 45, tzinfo=tz)}],
        Repository(), "account", date(2026, 9, 7), until=snapshots[0]["time"] + timedelta(microseconds=1),
        symbols={"HVN"}, quote_cache={(day, "HVN"): snapshots},
    )

    assert pending == []


def test_replay_drops_unfilled_order_at_prod_expiry_time(monkeypatch):
    day = date(2026, 9, 8)
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    snapshot_time = datetime(2026, 9, 8, 14, 45, tzinfo=tz)
    snapshots = [{"time": snapshot_time, "bid": [], "offer": [{"price": 22.55, "qtty": 1000}]}]
    monkeypatch.setattr(replay, "quote_history", lambda *_: snapshots)

    class Repository:
        def record_replay_execution(self, *_args, **_kwargs):
            raise AssertionError("an order must not fill at or after 14:45")

    pending = replay.fill_pending(
        day, {"HVN": {"price": 22_550, "source": "DNSE"}},
        [{"symbol": "HVN", "side": "BUY", "quantity": 100, "limit_price": 22_550,
          "not_before": datetime(2026, 9, 8, 9, 45, tzinfo=tz)}],
        Repository(), "account", date(2026, 9, 7), until=snapshot_time + timedelta(microseconds=1),
        symbols={"HVN"}, quote_cache={(day, "HVN"): snapshots},
    )

    assert pending == []


def test_replay_excludes_lunch_and_auction_books_from_fills():
    book = replay.replay_book({
        "symbol": "PVT",
        "time": datetime(2026, 9, 24, 12, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        "bid": [{"price": 23.1, "qtty": 100}],
        "offer": [{"price": 23.15, "qtty": 100}],
    })
    assert book["marketState"] == "closed"


def test_replay_book_reads_dnse_quantity_field():
    book = replay.replay_book({
        "symbol": "PVT",
        "time": datetime(2026, 9, 24, 9, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        "bid": [{"price": 23.10, "quantity": 100}],
        "offer": [{"price": 23.15, "quantity": 200}],
    })

    assert replay.executable_shares(book, "BUY", 23_150) == 200
