import asyncio

from app.domain.agents.position_monitoring import PositionMonitoringAgent


class StorageStub:
    def execute(self, *args, **kwargs):
        pass


class PeakRepositoryStub:
    account_id = "account-a"

    def __init__(self):
        self.storage = StorageStub()
        self.peaks = {}

    def get_account_state(self):
        return {"total_nav": 1_000_000_000.0}

    def update_position_peak_price(self, ticker, observed_peak, user_id=None):
        key = (user_id or self.account_id, ticker)
        self.peaks[key] = max(self.peaks.get(key, 0.0), observed_peak)
        return self.peaks[key]


def run_monitor(agent, price, user_id="account-a"):
    return asyncio.run(agent.process({
        "current_time": "2026-10-13T10:00:00+07:00",
        "nav": 1_000_000_000.0,
        "auto_dispatch": False,
        "positions": [{
            "ticker": "PVP",
            "user_id": user_id,
            "quantity": 1_400,
            "available_shares": 1_400,
            "entry_price": 19_825.0,
            "current_price": price,
        }],
    }))


def test_trailing_peak_survives_agent_restart_and_uses_current_price():
    repository = PeakRepositoryStub()
    first_agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)

    peak_result = run_monitor(first_agent, 25_550.0)
    assert peak_result["data"]["stop_loss_triggered"] is False

    restarted_agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)
    after_restart = run_monitor(restarted_agent, 22_700.0)

    assert after_restart["data"]["stop_loss_triggered"] is True
    order = after_restart["data"]["stop_loss_orders"][0]
    assert order["rule_level"] == "TRAILING_STOP"
    assert order["price"] == 22_700.0
    assert "đỉnh 25,550" in order["reason"]


def test_trailing_peak_is_scoped_to_the_position_account():
    repository = PeakRepositoryStub()
    agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)

    run_monitor(agent, 25_550.0, user_id="account-a")
    other_account = run_monitor(agent, 22_700.0, user_id="account-b")

    assert other_account["data"]["stop_loss_triggered"] is False
