import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.infrastructure.external_api.sector_heatmap import build_sector_heatmap
from app.infrastructure.external_api.dnse import redis_pub, stream_hub
from app.infrastructure.data_pipelines import ohlcv_backfill


def test_stock_master_sync_keeps_ssi_and_preserves_capital_when_dnse_omits_it(monkeypatch):
    assert ohlcv_backfill.is_real_stock("SSI")
    assert ohlcv_backfill.is_real_stock("SHB")
    assert not ohlcv_backfill.is_real_stock("FUEVFVND")
    assert not ohlcv_backfill.is_real_stock("CSSI2501")
    assert not ohlcv_backfill.is_real_stock("SSIAM")
    capitals = [None, 0, -1, "bad", float("nan"), float("inf"), 123456]
    instruments = [{"symbol": "SSI" if index == 0 else f"STOCK{index}", "marketCap": cap} for index, cap in enumerate(capitals)]
    statements = []
    cursor = SimpleNamespace(execute=lambda sql, params=None: statements.append((sql, params)), fetchall=lambda: [], close=lambda: None)
    connection = SimpleNamespace(cursor=lambda: cursor, commit=lambda: None, close=lambda: None)
    monkeypatch.setattr(ohlcv_backfill, "get_settings", lambda: SimpleNamespace(dnse_api_key="", dnse_api_secret="", dnse_base_url=""))
    monkeypatch.setattr(ohlcv_backfill, "DNSEClient", lambda **kwargs: object())
    monkeypatch.setattr(ohlcv_backfill, "get_all_stocks", lambda client, exchanges: instruments)
    monkeypatch.setattr(ohlcv_backfill, "get_db_conn", lambda: connection)

    assert ohlcv_backfill.sync_stocks() == len(instruments)
    upserts = [(sql, params) for sql, params in statements if "INSERT INTO stocks" in sql]
    assert [params[5] for _, params in upserts] == [None] * 6 + [123456]
    assert all("market_cap = COALESCE(EXCLUDED.market_cap, stocks.market_cap)" in sql for sql, _ in upserts)


def test_sector_counts_include_symbols_without_valid_prices_or_reference():
    sectors = build_sector_heatmap([
        {"sector": "BANKS", "price": 110, "ref": 100},
        {"sector": "BANKS", "price": 120},
        {"sector": "REAL_ESTATE", "price": 0},
        {"sector": "FINANCIAL_SERVICES", "price": 100, "ref": 100, "marketCap": 58e12},
    ])
    by_name = {sector["name"]: sector for sector in sectors}
    assert sum(sector["count"] for sector in sectors) == 4
    assert sectors[0]["name"] == "BANKS"
    assert by_name["BANKS"]["change"] == 10
    assert by_name["REAL_ESTATE"]["change"] is None
    assert by_name["FINANCIAL_SERVICES"]["marketCapCount"] == 1
    assert by_name["BANKS"]["marketCapCount"] == 0


def test_security_bands_survive_restart_and_update_an_existing_snapshot(monkeypatch):
    now = datetime.now(stream_hub.TZ_VN)
    security = {"symbol": "FPT", "ceiling": 69500, "floor": 56900, "prevClose": 63200, "lastUpdate": now.isoformat()}
    monkeypatch.setattr(redis_pub, "get_redis", lambda: SimpleNamespace(mget=lambda keys: [json.dumps(security)]))
    hub = stream_hub.DnseStreamHub()
    hub.set_market_universe([{"symbol": "FPT", "exchange": "HOSE", "close": 63.2}])
    hub._quotes["FPT"] = {"price": 63200, "ref": 62800, "ceiling": None, "floor": None, "source": "postgres-eod", "stale": True}
    hub._restore_security_definitions()
    snapshot = hub.get_market_board_snapshot()["stocks"][0]
    assert snapshot["ceiling"] == 69500
    assert snapshot["floor"] == 56900
    assert snapshot["ref"] == 62800
    assert snapshot["priceBandAsOf"] == security["lastUpdate"]
    assert hub._stock_metadata["FPT"]["refPrice"] == 63200
    hub._apply_security_definition({**security, "prevClose": 61000, "lastUpdate": (now - timedelta(days=1)).isoformat()})
    assert hub._stock_metadata["FPT"]["refPrice"] == 63200
    assert not hub._apply_security_definition({"symbol": "FPT", "ceiling": float("nan"), "floor": 1})

    writes = []
    monkeypatch.setattr(stream_hub, "set_cache", lambda key, value, ttl: writes.append((key, value, ttl)))
    monkeypatch.setattr(stream_hub, "publish_json", lambda *args: None)
    monkeypatch.setattr(hub, "_queue_market_flush", lambda: None)
    hub._on_sec_def(SimpleNamespace(symbol="FPT", ceilingPrice=69.5, floorPrice=56.9, basicPrice=63.2))
    assert {key for key, _, _ in writes} == {"stock:FPT:sec_def", "stock:FPT:quote"}
    assert all(ttl == 0 for _, _, ttl in writes)
    assert hub._quotes["FPT"]["ref"] == 62800
