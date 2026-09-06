"""Unit & Integration Tests for Standalone Pure-ML Fund Autonomous Channel (IOS v5.1).

Kiểm thử toàn diện:
1. Account Isolation: Đảm bảo tài khoản riêng biệt, không dùng chung tiền hay vị thế với 12 Agent.
2. Pure-ML Prediction: Kiểm thử suy luận từ mô hình hybrid_stacking_ranker.pkl.
3. Autonomous Sizing & Orders: Phân bổ 20% NAV / vị thế và sinh lệnh chuẩn xác.
4. Realized Accuracy Tracking: Đối soát tỷ lệ sống sót thực tế và hit rate sau T+2.5.
"""

import asyncio
import os
import pytest
from datetime import date

from app.domain.services.ml.standalone_ml_channel import (
    StandaloneExecutionMode,
    StandaloneMLChannel,
    standalone_ml_channel,
)


def test_account_isolation():
    """Kiểm tra tính độc lập và cách ly tài khoản tuyệt đối của Standalone ML Fund."""
    channel = StandaloneMLChannel(
        account_id="test-standalone-isolated-account",
        initial_nav=500_000_000.0,
        position_weight=0.20,
    )
    assert channel.account_id == "test-standalone-isolated-account"
    assert channel.position_weight == 0.20

    state = channel.get_account_state()
    assert state is not None
    assert state.get("account_id") == "test-standalone-isolated-account"
    assert state.get("total_nav", 0) > 0


def test_standalone_prediction_universe():
    """Kiểm tra khả năng nạp dữ liệu và suy luận 3 nhánh qua hybrid_stacking_ranker."""
    preds_df = standalone_ml_channel.predict_universe(
        target_date=date(2026, 9, 4),
        candidate_tickers=["FPT", "HPG", "VNM"],
    )
    assert not preds_df.empty
    assert "surv_prob" in preds_df.columns
    assert "mom_pred" in preds_df.columns
    assert "pred_score" in preds_df.columns
    assert "rank_pred" in preds_df.columns

    # Xác suất sinh tồn phải nằm trong [0.0, 1.0]
    for p in preds_df["surv_prob"]:
        assert 0.0 <= p <= 1.0


def test_standalone_autonomous_cycle():
    """Kiểm tra chu trình tự hành tạo lệnh và sizing 20% NAV / vị thế."""
    async def _run():
        res = await standalone_ml_channel.run_autonomous_cycle(
            target_date="2026-09-04",
            candidate_tickers=["FPT", "HPG", "VNM"],
            execution_mode=StandaloneExecutionMode.SHADOW_RUNNER,
            max_candidates=2,
            nav=500_000_000.0,
        )
        assert res["status"] == "SUCCESS"
        assert res["account_id"] == standalone_ml_channel.account_id
        assert res["execution_mode"] == "SHADOW_RUNNER"

        orders = res["orders"]
        assert len(orders) > 0
        for o in orders:
            assert o["account_id"] == standalone_ml_channel.account_id
            assert o["shares"] > 0
            assert o["price"] > 0
            assert o["target_weight_pct"] == 0.20  # Chuẩn 20% NAV / vị thế
            assert o["action"] == "SHADOW_PAPER_TRADE_ONLY"
            assert "P(Surv)" in o["rationale"]

    asyncio.run(_run())


def test_standalone_forward_accuracy_evaluation():
    """Kiểm tra cơ chế đối soát độ chính xác thực tế (Realized Survival & Hit Rate)."""
    metrics = standalone_ml_channel.evaluate_forward_accuracy(lookback_days=60)
    assert metrics["status"] == "COMPLETED"
    assert "realized_survival_rate_pct" in metrics
    assert "directional_hit_rate_pct" in metrics
    assert "avg_realized_3d_return_pct" in metrics
