from datetime import date, datetime

from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.rules.thesis_engine import ThesisEngine
from experiments.replay_agent_pipeline import fill_pending


def test_published_bank_valuation_and_margin_of_safety():
    class Storage:
        def fetch_all(self, query, params):
            if "SELECT sector" in query:
                return [("BANKS",)]
            assert params[2:4] == (date(2026, 9, 23), date(2026, 9, 23))
            return [(7.5, 10, 1.3, 10)]

    repo = FinancialRepository(storage=Storage())
    repo.get_financial_statements = lambda *args, **kwargs: [{
        "period_end": "2026-06-30",
        "published_date": "2026-08-10",
        "data": {
            "thu_nhập_trên_mỗi_cổ_phần_của_4_quý_gần_nhất_eps": 4000,
            "giá_trị_sổ_sách_của_cổ_phiếu_bvps": 20000,
        },
    }]
    inputs = repo.get_peer_valuation_inputs("MBB", date(2026, 9, 23))
    assert inputs["pe_price"] == 30000
    assert inputs["pb_price"] == 26000

    research = {"ticker": "MBB", "sector": "Ngân hàng", "css": 70, "conviction": "B",
                "f2_quality": 95, "f4_earnings": 95, "f5_flow": 65,
                "business_quality_score": 95, "current_price": 20000}
    engine = ThesisEngine()
    passed, thesis, _ = engine.build_structured_thesis_output(
        "MBB", research, {"current_regime": "RANGE_BOUND"}, inputs)
    assert passed and thesis["thesis_body"]["price_target"]["base_case"] == 28000
    assert "PB" in thesis["thesis_body"]["price_target"]["valuation_method"]
    research["current_price"] = 25000
    passed, _, reason = engine.build_structured_thesis_output(
        "MBB", research, {"current_regime": "RANGE_BOUND"}, inputs)
    assert not passed and "15%" in reason


def test_pilot_fill_requires_real_bar_and_stays_within_five_percent(monkeypatch):
    class Repository:
        def __init__(self):
            self.executions = []

        def get_account_state(self, **kwargs):
            return {"total_nav": 1_000_000_000}

        def record_replay_execution(self, symbol, side, quantity, price, **kwargs):
            self.executions.append((symbol, side, quantity, price))
            return {"shares": quantity, "executed_price": price}

    from experiments import replay_agent_pipeline as replay
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Ho_Chi_Minh")
    mock_quotes = [
        {"time": datetime(2026, 9, 24, 9, 15, 1, tzinfo=tz), "bid": [],
         "offer": [{"price": 60.0, "qtty": 1000}]},
    ]
    monkeypatch.setattr(replay, "quote_history", lambda *_: mock_quotes)

    repo = Repository()
    order = {"symbol": "MBB", "side": "BUY", "quantity": 1000, "entry_type": "PILOT"}
    args = (date(2026, 9, 24), {"MBB": {"price": 60000, "source": "FORWARD_FILL"}},
            [order], repo, "account", date(2026, 9, 23))
    assert fill_pending(*args) == []
    assert repo.executions == []
    args = (args[0], {"MBB": {"price": 60000, "source": "DNSE"}}, *args[2:])
    assert fill_pending(*args) == []
    assert repo.executions == [("MBB", "BUY", 800, 60000)]
