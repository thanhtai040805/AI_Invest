"""Unit tests for the Agent-04 narrative contract."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.domain.rules.thesis_synthesizer import ThesisSynthesizer


def valid_narrative():
    return {
        "investment_style": "DEEP_VALUE",
        "why_now": "[ĐIỂM NGOẶT] Chưa đủ dữ liệu để định lượng; cần mức nền và kỳ BCTC. | [CƠ CHẾ] Chưa có trong dữ liệu về biến số quyết định. | [XÁC NHẬN TỪ THỊ TRƯỜNG] Chưa có trong dữ liệu.",
        "why_this_stock": "[PHONG CÁCH] [Suy luận] DEEP_VALUE gần nhất vì input chưa chứng minh phong cách khác. | [CHẤT LƯỢNG LỢI NHUẬN] Chưa có trong dữ liệu về chuyển đổi lợi nhuận thành tiền. | [CẤU TRÚC TÀI CHÍNH] Chưa có trong dữ liệu về nợ và khả năng trả lãi. | [ĐIỀU KIỆN ĐỂ MỤC TIÊU GIÁ ĐÚNG] Chưa đủ dữ liệu để định lượng; cần lợi nhuận/dòng tiền nền. | [ĐIỂM YẾU TÀI CHÍNH] Chưa có thêm dữ liệu cho phần này.",
        "catalyst_description": "[BẢN CHẤT] Chưa có trong dữ liệu về tên và quy mô sự kiện. | [TRẠNG THÁI & BẰNG CHỨNG] Chưa xác định được từ dữ liệu. | [LỘ TRÌNH] Chưa có trong dữ liệu về mốc công bố. | [CẦU NỐI SANG DÒNG TIỀN] Chưa đủ dữ liệu để định lượng; cần doanh thu, biên và vốn đầu tư. | [ĐIỂM YẾU] Chưa có trong dữ liệu về tiến độ và vốn làm mức nền.",
        "pre_mortem": [
            "RỦI RO ĐẦU VÀO/VĨ MÔ: giả định chi phí tăng; tác động lên biên chưa có số nền; mức thiệt hại chưa đủ dữ liệu; theo dõi BCTC. Liên kết: Trigger 1",
            "RỦI RO CẠNH TRANH/PHÁP LÝ/KHÁCH HÀNG: giả định mất khách hàng; tác động doanh thu chưa có dữ liệu; theo dõi công bố tiếp theo. Liên kết: Trigger 2",
            "RỦI RO THỰC THI/TIẾN ĐỘ NGÒI NỔ: giả định catalyst chậm; chưa có mốc nền để định lượng; theo dõi bằng chứng mới. Liên kết: Trigger 3",
        ],
        "invalidation_triggers": [
            "Trigger 1: Chưa đủ dữ liệu để định lượng; cần chỉ số kết quả kinh doanh và kỳ BCTC làm mức nền.",
            "Trigger 2: Chưa đủ dữ liệu để định lượng; cần mốc catalyst đã công bố làm căn cứ.",
            "Trigger 3: Chưa đủ dữ liệu để định lượng; cần CFO, nợ/vốn và số kỳ theo dõi.",
        ],
    }


def test_thesis_synthesizer_prompt_includes_sector_data_and_missing_fields():
    messages = ThesisSynthesizer().build_prompt(
        ticker="TCB",
        sector="BANKS",
        industry="Ngân hàng",
        business_quality_data={
            "business_quality_score": 88.0,
            "evidence_quote": "Thu nhập lãi thuần tăng theo dữ liệu đầu vào",
            "pillars": {"Cash Conversion": {"score": 70, "verdict": "WATCH"}},
        },
        catalyst_type="Earnings Expansion",
        price_target_info={"base_case": 35000, "bull_case": 40000},
        current_price=28000,
        timeline_months=3,
        financial_metrics={"pe": 12.5, "roe": 18.2, "_gpm_not_applicable": True, "_sources": {"pe": "Agent-03"}},
        financial_evidence={"formatted": "BCTC gần nhất: IS 2026-06-30; nguồn alphastock."},
        independent_signals={"F4": "PASS"},
    )
    system, user = (message["content"] for message in messages)
    assert "Chỉ dùng dữ kiện có trong user input" in system
    assert "Không áp dụng theo ngành tài chính" in user
    assert "BCTC gần nhất: IS 2026-06-30" in user
    assert "ngành con DB: Ngân hàng" in user
    assert "ROIC: Chưa có trong dữ liệu" in user
    assert "F4 là điểm factor, không phải tỷ lệ tăng trưởng" in user
    assert '"invalidation_triggers"' in user


def test_thesis_synthesizer_accepts_only_complete_bounded_narrative():
    async def run():
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=valid_narrative())
        synthesizer = ThesisSynthesizer(llm_client=mock_llm)
        result = await synthesizer.synthesize_thesis_narrative(
            ticker="HPG", sector="INDUSTRIAL_GOODS", business_quality_data={},
            catalyst_type="Undervaluation", price_target_info={"base_case": 35000},
            current_price=28000, timeline_months=3,
        )
        assert result == valid_narrative()
        assert mock_llm.complete_json.await_args.kwargs["max_tokens"] == 3000

    asyncio.run(run())


def test_thesis_synthesizer_rejects_wrong_structure_and_list_count():
    async def run():
        response = valid_narrative()
        response["pre_mortem"] = response["pre_mortem"][:2]
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value=response)
        result = await ThesisSynthesizer(llm_client=mock_llm).synthesize_thesis_narrative(
            ticker="HPG", sector="INDUSTRIAL_GOODS", business_quality_data={},
            catalyst_type="Undervaluation", price_target_info={"base_case": 35000},
            current_price=28000, timeline_months=3,
        )
        assert result is None

    asyncio.run(run())


def test_thesis_synthesizer_returns_none_without_llm():
    async def run():
        result = await ThesisSynthesizer().synthesize_thesis_narrative(
            ticker="HPG", sector="INDUSTRIAL_GOODS", business_quality_data={},
            catalyst_type="Undervaluation", price_target_info={"base_case": 35000},
            current_price=28000, timeline_months=3,
        )
        assert result is None

    asyncio.run(run())
