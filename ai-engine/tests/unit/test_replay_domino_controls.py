"""Counterfactual checks from retained replay evidence, with no DB or LLM calls."""
import json
from pathlib import Path

from app.domain.rules.learning.causal_learning_engines import ProbabilityCalibrationEngine
from app.domain.rules.thesis_engine import ThesisEngine


FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/cache_readonly_domino_20261025.json').read_text(encoding='utf-8'))


def test_two_missing_ctg_losses_explain_sept7_calibration_delta():
    returns = FIXTURE['local_closed_return_pct_sep07']
    assert len(returns) == 29 and sum(value > 0 for value in returns) == 17
    engine = ProbabilityCalibrationEngine()
    for values, expected in (
        (returns, FIXTURE['expected_local_A']),
        (returns + FIXTURE['missing_prod_ctg_loss_return_pct'], FIXTURE['expected_with_ctg_losses_A']),
    ):
        trades = [{'pnl': value, 'conviction': 0.8} for value in values]
        actual = engine.calibrate(trades, regime='RANGE_BOUND')['A']
        assert {key: actual[key] for key in expected} == expected


def test_hag_f3_alone_flips_signal_gate_with_css_held_constant():
    case = FIXTURE['hag_signal_control']
    fixed = case['held_constant']
    engine = ThesisEngine()
    before = engine.evaluate_independent_signals(fixed['factors'], fixed['css'], fixed['quality'], fixed['regime'])
    after = engine.evaluate_independent_signals(
        {**fixed['factors'], 'f3_momentum': case['new']}, fixed['css'], fixed['quality'], fixed['regime'],
    )
    assert before[0] is False and before[2] == 2
    assert after[0] is True and after[2] == 3
