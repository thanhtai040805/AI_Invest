"""Automated test suite verifying mathematical integrity and formulas across AI-Engine."""

import numpy as np
import pandas as pd
import pytest
from datetime import date

from app.infrastructure.vendors.vn.technical_indicators import compute_full_indicators
from app.backtest.metrics import compute_sortino, compute_deflated_sharpe, compute_sharpe
from app.domain.services.ml.feature_forge import FeatureForge
from app.domain.rules.beneish import BeneishMScoreEngine
from app.domain.rules.risk.tail_risk_engine import TailRiskEngine
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.rules.stop_loss import StopLossEngine


class TestADXDirectionalMovement:
    def test_sharp_downtrend_increases_minus_di(self):
        """On a steep downtrend, minus_di must be strictly greater than plus_di."""
        n = 50
        dates = pd.date_range("2025-01-01", periods=n)
        # Price drops continuously: High and Low both decreasing every day
        lows = [100.0 - i * 1.5 for i in range(n)]
        highs = [l + 1.0 for l in lows]
        closes = [l + 0.3 for l in lows]
        volumes = [1000000] * n

        df = pd.DataFrame({
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }, index=dates)

        res = compute_full_indicators(df)
        assert "plus_di" in res and "minus_di" in res and "adx_14" in res
        
        last_plus = res["plus_di"].iloc[-1]
        last_minus = res["minus_di"].iloc[-1]
        last_adx = res["adx_14"].iloc[-1]

        # In a severe downtrend, minus_di must dominate plus_di
        assert last_minus > last_plus
        assert last_minus > 50.0
        assert last_plus < 10.0
        assert last_adx > 30.0  # Strong trend detected


class TestSortinoDownsideDeviation:
    def test_sortino_exact_formula(self):
        """Sortino must use RMS of negative deviations over ALL samples."""
        returns = pd.Series([0.02, -0.01, 0.03, -0.02, 0.01, -0.015, 0.04])
        mar = 0.0
        
        # Manual computation
        downside_diff = np.minimum(0.0, returns - mar)
        expected_dev = np.sqrt(np.mean(downside_diff ** 2))
        expected_sortino = (returns.mean() - mar) / expected_dev * np.sqrt(252)

        actual_sortino = compute_sortino(returns, target_return=mar)
        assert pytest.approx(actual_sortino, rel=1e-5) == expected_sortino


class TestDeflatedSharpeRatio:
    def test_dsr_penalizes_multiple_trials(self):
        """As n_trials increases (multiple testing bias), DSR probability must decrease."""
        sharpe = 0.12
        n_samples = 252
        
        dsr_10 = compute_deflated_sharpe(sharpe, n_samples, n_trials=10)
        dsr_100 = compute_deflated_sharpe(sharpe, n_samples, n_trials=100)
        dsr_1000 = compute_deflated_sharpe(sharpe, n_samples, n_trials=1000)

        assert 0.0 <= dsr_1000 <= dsr_100 <= dsr_10 <= 1.0
        assert dsr_10 > dsr_1000


class TestMomentumSharpeDimension:
    def test_rolling_sharpe_dimension_consistency(self):
        """Rolling Sharpe ratios across windows must have consistent annualized dimensions."""
        forge = FeatureForge()
        n = 150
        dates = pd.date_range("2025-01-01", periods=n)
        # Constant daily growth of 0.1% with slight noise
        np.random.seed(42)
        rets = 0.001 + np.random.normal(0, 0.005, n)
        prices = 100.0 * np.cumprod(1 + rets)
        df = pd.DataFrame({"close": prices, "volume": [100000]*n}, index=dates)

        res = forge._compute_price_momentum(df)
        
        # Sharpe 5d and Sharpe 60d must be on the same annualized scale (~0.5 - 3.0), not 20x apart
        sh_5d = res["sharpe_5d"].dropna().mean()
        sh_60d = res["sharpe_60d"].dropna().mean()
        
        assert abs(sh_5d) < 10.0
        assert abs(sh_60d) < 10.0
        # Check that ratio is within reasonable factor (not orders of magnitude distorted)
        assert 0.1 < abs(sh_5d / (sh_60d + 1e-6)) < 10.0


class TestDualEngineBeneishMScore:
    def test_financial_sector_exemption(self):
        """Banks and securities must be exempt."""
        engine = BeneishMScoreEngine()
        res_bank = engine.calculate_m_score("VCB")
        assert res_bank["is_exempt"] is True
        assert res_bank["status"] == "PASS"
        assert res_bank["model_used"] == "exempt"

        res_sec = engine.calculate_m_score("SSI")
        assert res_sec["is_exempt"] is True
        assert res_sec["status"] == "PASS"

    def test_adaptive_5var_and_8var_models(self):
        """Non-financial stocks calculate using 100% real data without fake zeros."""
        engine = BeneishMScoreEngine()
        
        # HPG (Steel)
        res_hpg = engine.calculate_m_score("HPG")
        assert res_hpg["is_exempt"] is False
        assert res_hpg["model_used"] in ("8_variable", "5_variable")
        assert res_hpg["m_score"] is not None
        assert res_hpg["status"] in ("PASS", "FAIL")
        assert res_hpg["variables"]["dsri"] > 0
        assert res_hpg["variables"]["gmi"] > 0
        assert res_hpg["variables"]["sgi"] > 0

        # TLG (has full unbundled depreciation statement)
        res_tlg = engine.calculate_m_score("TLG")
        assert res_tlg["is_exempt"] is False
        assert res_tlg["m_score"] is not None
        assert res_tlg["model_used"] in ("8_variable", "5_variable")


class TestTailRiskMultiplier:
    def test_student_t_expected_shortfall_multiplier(self):
        """ES tail multiplier must be ~3.37 (greater than VaR quantile 2.571)."""
        engine = TailRiskEngine()
        es = engine.calculate_egarch_student_t_es([], recent_daily_volatility=0.014)
        # Expected: 0.014 * 3.37 = 0.04718 ~ 0.0472
        assert es == pytest.approx(0.014 * 3.37, abs=1e-3)
        assert es > 0.014 * 2.571  # Strictly larger than VaR quantile

    def test_constant_large_losses_have_nonzero_expected_shortfall(self):
        engine = TailRiskEngine()
        assert engine.calculate_egarch_student_t_es([-0.07] * 20) > 0.07


class TestValuationOvervaluationIntegrity:
    def test_overvalued_stock_retains_intrinsic_target(self):
        """Thesis Engine must not artificially inflate target price when base_case < current_price."""
        engine = ThesisEngine()
        # When comp prices suggest 20,000 but market is at 50,000
        val = engine.calculate_adaptive_target_price(
            timeline_months=6,
            current_price=50000.0,
            pe_comp_price=20000.0,
            ev_ebitda_comp_price=20000.0,
            dcf_price=20000.0,
            regime_label="SIDEWAY",
            sector="GENERAL",
        )
        assert val["is_overvalued"] is True
        assert val["base_case"] < 50000.0  # Kept genuine intrinsic valuation
        assert val["base_case"] != 50000.0 * 1.12  # Not artificially pumped


class TestTimeStopDeadCapital:
    def test_slightly_negative_position_triggers_time_stop(self):
        """Positions with pnl = -1.5% held past 50% timeline must trigger Time Stop."""
        engine = StopLossEngine()
        order = engine.check_position(
            ticker="STAG",
            quantity=1000,
            entry_price=100000.0,
            current_price=98500.0,  # -1.5% loss
            nav=1_000_000_000.0,
            available_shares=1000,
            market_data={
                "days_held": 40,
                "expected_timeline_days": 60,   # 40 > 30 (50% of timeline)
                "peak_price": 100000.0,
            }
        )
        assert order is not None
        assert order.rule_level == "TIME_STOP"
        assert order.suggested_action == "REDUCE_50_PCT"
