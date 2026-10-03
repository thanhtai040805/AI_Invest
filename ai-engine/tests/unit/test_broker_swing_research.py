import pandas as pd
import pytest

from scripts import research_broker_swing as research


@pytest.fixture
def temporal_forecasts(monkeypatch, tmp_path):
    calendar = pd.bdate_range("2025-03-17", "2025-04-18")
    deadline = calendar[-8]
    rows = [
        (pd.Timestamp("2024-06-03"), "HIST", True, True, "ok"),
        (pd.Timestamp("2025-03-31"), "OLD", True, True, "censored_missing_session"),
        (pd.Timestamp("2025-04-01"), "NEW", True, True, "ok"),
        (deadline, "DEADLINE", True, True, "invalid_future_price"),
        (calendar[-7], "TOO_LATE", True, True, "ok"),
        (calendar[0], "NO_SETUP", True, False, "ok"),
        (calendar[0], "INELIGIBLE", False, True, "ok"),
    ]
    entries = pd.DataFrame(rows, columns=["date", "ticker", "universe_eligible", "expert_breakout", "label_status"])
    states = pd.DataFrame([
        {"entry_decision_date": pd.Timestamp("2025-03-31"), "ticker": "OLD",
         "date": pd.Timestamp("2025-04-02"), "holding_sessions": 2},
        {"entry_decision_date": pd.Timestamp("2025-04-01"), "ticker": "NEW",
         "date": pd.Timestamp("2025-04-03"), "holding_sessions": 2},
    ])
    metadata = {"expert_columns": {"breakout": "expert_breakout"}}
    fitted = []

    class Model:
        def __init__(self, n_jobs):
            self.n_jobs = n_jobs

        def fit(self, historical, historical_states, contract, prediction_start):
            assert historical["date"].lt(prediction_start).all()
            assert historical["date"].ge(prediction_start - pd.DateOffset(months=36)).all()
            assert historical_states["entry_decision_date"].lt(prediction_start).all()
            self.trained_through = (prediction_start - pd.Timedelta(days=1)).date().isoformat()
            self.metadata = {"prediction_start": prediction_start.date().isoformat(),
                             "trained_through": self.trained_through, "expert_samples": {}}
            fitted.append(self)
            return self

        def save(self, artifact):
            self.model_version = artifact.stem

        def forecast_entries(self, chunk, use_arbiter=True):
            assert chunk["date"].gt(pd.Timestamp(self.trained_through)).all()
            result = chunk[["date", "ticker", "label_status"]].rename(columns={"date": "decision_date"}).copy()
            result["model_version"] = self.model_version
            result["uses_arbiter"] = use_arbiter
            return result

        def forecast_exits(self, state_chunk):
            assert state_chunk["date"].gt(pd.Timestamp(self.trained_through)).all()
            result = state_chunk.rename(columns={"date": "state_date"}).copy()
            result["model_version"] = self.model_version
            return result

        def policy_outcomes(self, chunk, state_chunk):
            return chunk.loc[chunk["label_status"].eq("ok"), ["date", "ticker"]].assign(policy_net_return=.02)

    monkeypatch.setattr(research, "BrokerSwingModel", Model)
    output = research.forecasts_for(
        entries, states, metadata, calendar[0], calendar[-1], calendar, tmp_path, n_jobs=1,
    )
    return output, fitted, deadline


def test_exit_states_keep_original_entry_artifact_across_quarter_boundary(temporal_forecasts):
    (full, specialists, exits, _, artifacts), fitted, _ = temporal_forecasts

    assert [model.metadata["prediction_start"] for model in fitted] == ["2025-01-01", "2025-04-01"]
    assert full.loc[full["ticker"].eq("OLD"), "model_version"].iloc[0] == "model_2025Q1"
    assert exits.loc[exits["ticker"].eq("OLD"), "model_version"].iloc[0] == "model_2025Q1"
    assert exits.loc[exits["ticker"].eq("OLD"), "state_date"].iloc[0] == pd.Timestamp("2025-04-02")
    assert exits.loc[exits["ticker"].eq("NEW"), "model_version"].iloc[0] == "model_2025Q2"
    assert full["uses_arbiter"].all() and not specialists["uses_arbiter"].any()
    assert [item["quarter"] for item in artifacts] == ["2025Q1", "2025Q2"]


def test_causal_setup_forecasts_include_future_censored_and_invalid_paths(temporal_forecasts):
    (full, _, exits, outcomes, _), _, _ = temporal_forecasts

    assert set(full["ticker"]) == {"OLD", "NEW", "DEADLINE"}
    assert full.loc[full["ticker"].eq("OLD"), "label_status"].iloc[0] == "censored_missing_session"
    assert full.loc[full["ticker"].eq("DEADLINE"), "label_status"].iloc[0] == "invalid_future_price"
    assert set(exits["ticker"]) == {"OLD", "NEW"}
    assert outcomes["ticker"].tolist() == ["NEW"]


def test_entry_deadline_uses_period_calendar_and_not_future_ticker_outcome(temporal_forecasts):
    (full, _, _, _, _), _, deadline = temporal_forecasts

    assert full["decision_date"].max() == deadline
    assert "DEADLINE" in full["ticker"].tolist()
    assert "TOO_LATE" not in full["ticker"].tolist()


def test_zero_event_period_reports_full_cash_nav_without_fitting(monkeypatch, tmp_path):
    calendar = pd.bdate_range("2025-04-01", periods=20)
    entries = pd.DataFrame({"date": calendar, "ticker": "HPG", "universe_eligible": True,
                            "expert_breakout": False, "label_status": "ok"})
    states = pd.DataFrame(columns=["date", "ticker", "entry_decision_date", "holding_sessions"])
    bars = pd.DataFrame([
        {"date": day, "ticker": ticker, "open": 100.0, "close": 100.0, "volume_continuous": 10_000}
        for day in calendar for ticker in ("VNINDEX", "HPG")
    ])

    def forbidden_fit(*args, **kwargs):
        pytest.fail("A period with no setup events does not require model fitting")

    monkeypatch.setattr(research, "BrokerSwingModel", forbidden_fit)
    output = tmp_path / "empty_period"
    result = research.evaluate_period(
        entries, states, {"expert_columns": {"breakout": "expert_breakout"}},
        bars, calendar, calendar[0], calendar[-1], output, n_jobs=1,
    )

    for name in ("full", "static_exit_same_entry", "specialists_without_arbiter", "friction_100bps"):
        assert result[name]["total_return"] == 0.0
        assert result[name]["closed_trades"] == 0
        assert result[name]["coverage"]["simulated_sessions"] == 20
        assert result[name]["realized_pnl_vnd_at_20_sessions"] == 0.0
        assert result[name]["sessions_to_first_positive_realized_pnl"] is None
    nav = pd.read_csv(output / "full_nav.csv", parse_dates=["date"])
    assert nav["date"].tolist() == list(calendar)
    assert nav["total_nav"].eq(1_000_000_000).all()
    assert result["diagnostics"]["forecast_rows"] == 0
    assert (output / "artifacts.json").read_text(encoding="utf-8") == "[]"


def test_source_quarantines_bad_quotes_and_keeps_price_free_index_calendar(tmp_path):
    source = pd.DataFrame([
        {"date": "2025-01-02", "ticker": "VNINDEX", "open": 100, "high": 101, "low": 99, "close": 100},
        {"date": "2025-01-02", "ticker": "HPG", "open": 100, "high": 95, "low": 90, "close": 100},
        {"date": "2025-01-03", "ticker": "HPG", "open": 100, "high": 101, "low": 99, "close": 100},
    ])
    path = tmp_path / "bars.csv"
    source.to_csv(path, index=False)

    with pytest.raises(ValueError, match="missing index dates"):
        research.prepare_source(path)
    bars, quality = research.prepare_source(path, allow_missing_index_sessions=True)

    bad = bars.loc[bars["ticker"].eq("HPG") & bars["date"].eq("2025-01-02")]
    placeholder = bars.loc[bars["ticker"].eq("VNINDEX") & bars["date"].eq("2025-01-03")]
    assert len(bad) == len(placeholder) == 1
    assert bad[["open", "high", "low", "close"]].isna().all(axis=None)
    assert placeholder[["open", "high", "low", "close"]].isna().all(axis=None)
    assert quality["invalid_raw_ohlc_rows"] == 1
    assert quality["missing_index_session_dates"] == [pd.Timestamp("2025-01-03")]


def _development_records():
    return [
        {"full": {"gross_profit_vnd": 500.0, "gross_loss_vnd": 250.0,
                  "closed_trades": 50, "total_return": .05,
                  "max_drawdown": .05, "open_positions": []},
         "friction_100bps": {"total_return": .01, "open_positions": []}}
        for _ in range(3)
    ]


def test_development_gate_accepts_one_profitable_resolved_architecture():
    result = research.development_gate(_development_records())
    assert result["passed"]
    assert result["candidate_count"] == 1
    assert result["closed_trades"] == 150
    assert result["profit_factor"] == 2.0


@pytest.mark.parametrize("failure", ["annual_loss", "stress_loss", "full_inventory", "stress_inventory"])
def test_development_gate_rejects_loss_or_unresolved_inventory(failure):
    records = _development_records()
    if failure == "annual_loss":
        records[1]["full"]["total_return"] = -.001
    elif failure == "stress_loss":
        records[1]["friction_100bps"]["total_return"] = -.001
    elif failure == "full_inventory":
        records[1]["full"]["open_positions"] = [{"ticker": "HPG"}]
    else:
        records[1]["friction_100bps"]["open_positions"] = [{"ticker": "HPG"}]

    assert not research.development_gate(records)["passed"]
