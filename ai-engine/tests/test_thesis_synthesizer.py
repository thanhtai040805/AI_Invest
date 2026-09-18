"""Unit tests for ThesisSynthesizer (Agent-04 narrative enrichment with SAG MOAT)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.domain.rules.thesis_synthesizer import ThesisSynthesizer


def test_thesis_synthesizer_prompt_structure():
    synthesizer = ThesisSynthesizer()
    messages = synthesizer.build_prompt(
        ticker="HPG",
        sector="Materials",
        moat_data={
            "moat_score": 88.0,
            "evidence_quote": "Công nghệ luyện thép lò cao BOF khép kín tự chủ 80% điện nhiệt dư",
            "pillars": {
                "Cost Advantage": {"score": 95.0, "verdict": "STRONG"},
                "Efficient Scale": {"score": 85.0, "verdict": "STRONG"}
            }
        },
        catalyst_type="Earnings Expansion",
        price_target_info={"base_case": 35000.0, "bull_case": 40000.0},
        current_price=28000.0,
        timeline_months=3,
    )

    sys_content = messages[0]["content"]
    user_content = messages[1]["content"]

    assert "Head of Equity Strategy" in sys_content
    assert "HPG" in user_content
    assert "BOF khép kín tự chủ 80% điện" in user_content
    assert "Cost Advantage" in user_content
    assert "why_now" in user_content
    assert "why_this_stock" in user_content
    assert "investment_style" in user_content
    assert "pre_mortem" in user_content
    assert "invalidation_triggers" in user_content


def test_thesis_synthesizer_mock_synthesis():
    async def _run():
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value={
            "investment_style": "CYCLICAL_GROWTH",
            "why_now": "Dung Quất 2 giai đoạn 1 bắt đầu chạy thử nghiệm, đưa HPG bước vào chu kỳ siêu tăng trưởng sản lượng HRC.",
            "why_this_stock": "HPG sở hữu lợi thế chi phí sản xuất tuyệt đối (Moat 88/100) nhờ lò cao BOF khép kín và tự chủ điện.",
            "catalyst_description": "Vận hành thương mại Dung Quất 2 bổ sung 2.8 triệu tấn HRC/năm.",
            "pre_mortem": [
                "Kịch bản 1: Giá thép HRC Trung Quốc tiếp tục bán phá giá trước khi có thuế AD chính thức.",
                "Kịch bản 2: Giá quặng sắt thế giới tăng vọt bào mòn biên lợi nhuận gộp.",
                "Kịch bản 3: Tiến độ lò cao Dung Quất 2 bị trễ 1 quý do sự cố kỹ thuật."
            ],
            "invalidation_triggers": [
                "Biên EBITDA mảng thép HRC giảm xuống dưới 14%.",
                "Tiến độ Dung Quất 2 hoãn vận hành thương mại quá tháng 6/2025.",
                "Thị phần thép xây dựng nội địa giảm dưới 32%."
            ]
        })

        synthesizer = ThesisSynthesizer(llm_client=mock_llm)
        narrative = await synthesizer.synthesize_thesis_narrative(
            ticker="HPG",
            sector="Materials",
            moat_data={"moat_score": 88.0},
            catalyst_type="Earnings Expansion",
            price_target_info={"base_case": 35000.0},
            current_price=28000.0,
            timeline_months=3,
            financial_metrics={"pe": 12.5, "roe": 18.2, "gpm": 16.5},
        )

        assert narrative is not None
        assert narrative["investment_style"] == "CYCLICAL_GROWTH"
        assert "Dung Quất 2" in narrative["why_now"]
        assert "lò cao BOF" in narrative["why_this_stock"]
        assert len(narrative["pre_mortem"]) == 3
        assert len(narrative["invalidation_triggers"]) == 3

    asyncio.run(_run())


def test_thesis_synthesizer_fallback_when_none():
    async def _run():
        synthesizer = ThesisSynthesizer(llm_client=None)
        narrative = await synthesizer.synthesize_thesis_narrative(
            ticker="HPG",
            sector="Materials",
            moat_data={},
            catalyst_type="Earnings Expansion",
            price_target_info={"base_case": 35000.0},
            current_price=28000.0,
            timeline_months=3,
        )
        assert narrative is None

    asyncio.run(_run())
