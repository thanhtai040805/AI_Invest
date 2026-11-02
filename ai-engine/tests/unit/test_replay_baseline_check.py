from datetime import date, timedelta
import hashlib
import json

import pytest

from app.domain.repositories.replay_baseline_check import (
    BASELINE_ID, BASELINE_SNAPSHOT, ReplayBaselineCheck,
)


def fixture(tmp_path):
    days = [date(2026, 8, 12) + timedelta(days=n) for n in range(43)]
    days = [d for d in days if d.weekday() < 5 and str(d) not in ('2026-08-31', '2026-09-01', '2026-09-02')]
    factors = tmp_path / 'factors.json'
    factors.write_text('[]')
    reference = tmp_path / 'reference.json'
    reference.write_text(json.dumps(dict(
        baseline_id=BASELINE_ID, account_id='account', factor_snapshot={'sha256': BASELINE_SNAPSHOT},
        nav=[[str(d), '1000000000.00', '987615000.00'] for d in days],
        effective_factors={'path': factors.name, 'file_sha256': hashlib.sha256(factors.read_bytes()).hexdigest()},
    )))
    return reference, days


def check(tmp_path, **overrides):
    reference, days = fixture(tmp_path)
    kwargs = dict(reference=reference, days=days, snapshot_sha=BASELINE_SNAPSHOT,
                  account='account', capital=1e9, max_candidates=3, symbols=None, output=tmp_path / 'out')
    kwargs.update(overrides)
    return ReplayBaselineCheck(**kwargs), days


def test_gate_records_exact_match_and_stops_on_half_dong_difference(tmp_path):
    gate, days = check(tmp_path)
    gate.check(days[0], {'total_nav': '1000000000.00', 'cash_balance': '987615000.00'}, {})
    with pytest.raises(RuntimeError, match='stopped before next session'):
        gate.check(days[1], {'total_nav': '1000000000.50', 'cash_balance': '987615000.00'}, {})
    gate.close()
    rows = [json.loads(line) for line in gate.output.read_text().splitlines()]
    assert rows[0]['matches'] is True
    assert rows[1]['nav_delta'] == '0.50'


def test_gate_rejects_factor_difference_even_if_nav_matches(tmp_path):
    gate, days = check(tmp_path)
    with pytest.raises(RuntimeError):
        gate.check(days[0], {'total_nav': 1e9, 'cash_balance': 987615000}, {'factors': {'FPT': {}}})
    gate.close()


@pytest.mark.parametrize('override', [dict(account='other'), dict(capital=2e9), dict(max_candidates=4),
                                     dict(symbols=['HPG']), dict(snapshot_sha='other')])
def test_rejects_wrong_baseline_contract_before_run(tmp_path, override):
    with pytest.raises(ValueError):
        check(tmp_path, **override)


def test_gate_only_compares_first_28_sessions(tmp_path):
    gate, _ = check(tmp_path)
    gate.check(date(2026, 9, 24), {'total_nav': 0, 'cash_balance': 0}, {'factors': {}})
    gate.close()
    assert json.loads(gate.output.read_text())['mode'] == 'VN_PRODUCER'


def test_rejects_incomplete_calendar(tmp_path):
    _, days = fixture(tmp_path)
    with pytest.raises(ValueError, match='28 baseline sessions'):
        check(tmp_path, days=days[1:])
