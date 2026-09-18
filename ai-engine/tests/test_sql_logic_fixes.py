"""Unit tests validating SQL, T+2.5 settlement, fee/tax accounting, and logic fixes."""

from datetime import datetime, time, timedelta
import pytest

from app.domain.repositories.portfolio_repository import calculate_is_t25_locked, PortfolioRepository
from app.domain.rules.execution.eae import ExecutionAdaptationEngine, ExecutionStrategy
from app.domain.rules.portfolio.liquidity_engine import LiquidityEngine
from app.domain.rules.portfolio.dynamic_allocation_engine import DynamicAllocationEngine
from app.domain.rules.risk.confidence_scorer import ConfidenceScorer


def test_t25_settlement_vietnamese_business_days():
    """Kiểm tra chính xác quy chế thanh toán T+2.5 của thị trường chứng khoán Việt Nam:
    - Mua thứ 6: Thứ 7, CN nghỉ.
    - Thứ 2: T+1 -> Chưa về (Locked).
    - Thứ 3 sáng (< 11:30): T+2 sáng -> Chưa về (Locked).
    - Thứ 3 chiều (>= 11:30): T+2 chiều -> Đã về (Unlocked).
    - Thứ 4: T+3 -> Đã về (Unlocked).
    """
    # Thứ Sáu ngày 2026-08-21 10:00:00
    friday_buy = datetime(2026, 8, 21, 10, 0, 0)

    # Thứ Bảy & Chủ Nhật (sau > 48h nhưng là cuối tuần)
    sunday_noon = datetime(2026, 8, 23, 12, 0, 0)
    assert calculate_is_t25_locked(friday_buy, sunday_noon) is True, "Chủ Nhật phải bị locked"

    # Thứ Hai 09:30 sáng (T+1)
    monday_morning = datetime(2026, 8, 24, 9, 30, 0)
    assert calculate_is_t25_locked(friday_buy, monday_morning) is True, "Thứ Hai (T+1) phải bị locked"

    # Thứ Hai 14:30 chiều (T+1)
    monday_afternoon = datetime(2026, 8, 24, 14, 30, 0)
    assert calculate_is_t25_locked(friday_buy, monday_afternoon) is True, "Thứ Hai chiều (T+1) phải bị locked"

    # Thứ Ba 10:00 sáng (T+2 sáng)
    tuesday_morning = datetime(2026, 8, 25, 10, 0, 0)
    assert calculate_is_t25_locked(friday_buy, tuesday_morning) is True, "Thứ Ba trước 11:30 (T+2 sáng) phải bị locked"

    # Thứ Ba 13:00 chiều (T+2 chiều - chứng khoán về lúc 11:30)
    tuesday_afternoon = datetime(2026, 8, 25, 13, 0, 0)
    assert calculate_is_t25_locked(friday_buy, tuesday_afternoon) is False, "Thứ Ba sau 11:30 (T+2 chiều) phải unlocked"

    # Thứ Tư (T+3)
    wednesday = datetime(2026, 8, 26, 9, 30, 0)
    assert calculate_is_t25_locked(friday_buy, wednesday) is False, "Thứ Tư (T+3) phải unlocked"


def test_liquidity_engine_negative_rounding():
    """Kiểm tra không bị lỗi Python negative integer division gây bán khống (Naked Short).
    Khi muốn bán 250 cổ phiếu, phải làm tròn thành -200, KHÔNG ĐƯỢC thành -300.
    """
    engine = LiquidityEngine()
    # Danh mục có 250 cổ phiếu, muốn giảm tỷ trọng về 0 (target = 0.0)
    res = engine.evaluate_liquidity(
        ticker="FPT",
        portfolio_target=0.0,
        price=100_000,
        total_nav=100_000_000,
        current_shares=250,
        adtv20=1_000_000,
    )
    # Lượng incremental phải là -200 (không thể vượt quá 250 cổ phiếu đang có do làm tròn xuống)
    assert res.incremental_shares == -200, f"Expected -200 shares, got {res.incremental_shares}"
    assert res.executable_shares >= 0, "Executable shares không được âm"


def test_eae_passive_limit_direction_and_slicing():
    """Kiểm tra lệnh Passive Limit:
    - Khi BUY: giá đặt phải <= giá thị trường (đón giá rẻ).
    - Khi SELL: giá đặt phải >= giá thị trường (đón giá cao).
    - Khối lượng lớn (1,200,000 cổ phiếu) phải phân bổ hết 100%, không bị cắt xén ở 500k.
    """
    eae = ExecutionAdaptationEngine()

    # 1. Test BUY Passive Limit (atc_concentration > 0.30 triggers PASSIVE_LIMIT in NORMAL)
    plan_buy = eae.create_execution_plan(
        ticker="HPG",
        direction="BUY",
        total_quantity=50_000,
        decision_price=28_000,
        max_price=30_000,
        adtv20=5_000_000,
        market_state={"atc_concentration": 0.35, "market_regime": "NORMAL"},
    )
    assert plan_buy.strategy == "PASSIVE_LIMIT"
    for s in plan_buy.slices:
        assert s.limit_price <= 28_000, f"Passive BUY limit ({s.limit_price}) không được vượt quá decision price (28000)"

    # 2. Test SELL Passive Limit
    plan_sell = eae.create_execution_plan(
        ticker="HPG",
        direction="SELL",
        total_quantity=50_000,
        decision_price=28_000,
        max_price=25_000,
        adtv20=5_000_000,
        market_state={"atc_concentration": 0.35, "market_regime": "NORMAL"},
    )
    assert plan_sell.strategy == "PASSIVE_LIMIT"
    for s in plan_sell.slices:
        assert s.limit_price >= 28_000, f"Passive SELL limit ({s.limit_price}) không được thấp hơn decision price (28000)"

    # 3. Test Lệnh lớn vượt 500k trần HOSE: 1,200,000 cổ
    plan_large = eae.create_execution_plan(
        ticker="HPG",
        direction="BUY",
        total_quantity=1_200_000,
        decision_price=28_000,
        max_price=30_000,
        adtv20=10_000_000,
    )
    total_sliced = sum(s.quantity for s in plan_large.slices)
    assert total_sliced == 1_200_000, f"Expected 1,200,000 shares sliced, got {total_sliced}"
    for s in plan_large.slices:
        assert s.quantity <= 500_000, f"Mỗi slice không được vượt trần 500k HOSE: {s.quantity}"


def test_dynamic_allocation_symbol_key_handling():
    """Kiểm tra Dynamic Allocation Engine không bị KeyError khi vị thế dùng 'symbol' thay vì 'ticker'."""
    engine = DynamicAllocationEngine()
    existing_positions = [
        {"symbol": "HPG", "market_value": 50_000_000},
        {"symbol": "FPT", "market_value": 30_000_000},
    ]
    # Sẽ không ném ngoại lệ KeyError: 'ticker'
    res = engine.evaluate_allocation(
        portfolio_target=0.10,
        ticker="HPG",
        regime_str="BULL",
        total_nav=1_000_000_000,
        cash_balance=800_000_000,
        existing_positions=existing_positions,
        drawdown_tier="GREEN",
    )
    assert res.adjusted_target >= 0.0


def test_confidence_scorer_clean_score():
    """Kiểm tra ConfidenceScorer tính điểm thuần factor tin cậy mà không phụ thuộc vào bảng risk_assessments."""
    scorer = ConfidenceScorer()
    res = scorer.score(factor_percentile=85.0, technical_aligned=True)
    assert res["decision"] == "BUY"
    assert res["confidence"] > 0.9
    assert res["hard_flags"] == []
    assert res["soft_flags"] == []
