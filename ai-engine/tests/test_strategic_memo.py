"""Unit tests for Strategic Memo Generator and Strategy CIO Integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.domain.rules.strategic_memo_generator import StrategicMemoGenerator


def test_strategic_memo_prompt_building():
    generator = StrategicMemoGenerator()
    prompt_messages = generator.build_prompt(
        ticker="HPG",
        company_name="Công ty Cổ phần Tập đoàn Hòa Phát",
        moat_data={
            "moat_score": 85.0,
            "evidence_quote": "Chu trình lò cao BOF khép kín và cảng nước sâu 200.000 tấn",
            "pillars": {
                "Cost Advantage": {"score": 90.0, "verdict": "STRONG"},
                "Efficient Scale": {"score": 80.0, "verdict": "STRONG"}
            }
        },
        gil_data={
            "gil_flag": "PASS",
            "ocr_score": 10.0,
            "cycles_detected": 0
        },
        thesis_payload={
            "thesis_body": {
                "catalyst": {"primary_type": "Earnings Expansion", "description": "Dung Quất 2 chạy thử"}
            }
        },
        counter_payload={
            "verdict": "PROCEED",
            "cts_score": 22.5,
            "holes": ["Rủi ro giá quặng sắt biến động"]
        }
    )

    system_msg = prompt_messages[0]["content"]
    user_msg = prompt_messages[1]["content"]

    # Kiểm tra Persona và các yêu cầu cốt lõi
    assert "Giám đốc Đầu tư (CIO)" in system_msg
    assert "Smart Money" in system_msg
    assert "BÁO CÁO CẬP NHẬT CHIẾN LƯỢC" in user_msg
    assert "1. LOẠI BỎ \"NHIỄU LỌC\"" in user_msg
    assert "2. NHÌN THẲNG VÀO NÚT THẮT / TỬ HUYỆT" in user_msg
    assert "3. CHẤT XÚC TÁC ĐỊNH GIÁ / LỢI THẾ CẠNH TRANH ĐỘC QUYỀN" in user_msg
    assert "4. KẾT LUẬN ĐẦU TƯ" in user_msg
    # Kiểm tra đã nạp dữ liệu MOAT và BCTC từ SAG
    assert "Chu trình lò cao BOF khép kín" in user_msg
    assert "Cost Advantage" in user_msg


def test_strategic_memo_generation_with_mock_llm():
    async def _run():
        mock_llm = MagicMock()
        mock_llm.is_configured = True
        expected_memo = """# BÁO CÁO CẬP NHẬT CHIẾN LƯỢC: HPG
**Chủ đề:** Khẳng định vị thế độc tôn ngành thép nhờ lợi thế chi phí quy mô
### 1. LOẠI BỎ NHIỄU LỌC
- Gạt bỏ các tin đồn nông nghiệp, tập trung 80% vào mảng HRC.
### 2. NHÌN THẲNG VÀO NÚT THẮT
- Áp lực từ thép giá rẻ Trung Quốc.
### 3. TRUE MOAT
- Moat 85 điểm từ lò cao BOF.
### 4. KẾT LUẬN ĐẦU TƯ
- Cổ phiếu chu kỳ tăng trưởng. Điểm mua gom khi P/B về sát 1.2x.
"""
        mock_llm.chat = AsyncMock(return_value=expected_memo)

        generator = StrategicMemoGenerator(llm_client=mock_llm)
        memo = await generator.generate_memo(
            ticker="HPG",
            company_name="Tập đoàn Hòa Phát",
            moat_data={"moat_score": 85.0}
        )

        assert "BÁO CÁO CẬP NHẬT CHIẾN LƯỢC" in memo
        assert "LOẠI BỎ NHIỄU LỌC" in memo
        assert "TRUE MOAT" in memo

    asyncio.run(_run())


def test_strategic_memo_fallback():
    async def _run():
        # Khi không có LLM, phải fallback an toàn
        generator = StrategicMemoGenerator(llm_client=None)
        memo = await generator.generate_memo(
            ticker="HPG",
            company_name="Tập đoàn Hòa Phát",
            moat_data={"moat_score": 85.0, "evidence_quote": "Lò cao BOF"},
            gil_data={"gil_flag": "PASS"}
        )

        assert "BÁO CÁO CẬP NHẬT CHIẾN LƯỢC" in memo
        assert "LOẠI BỎ \"NHIỄU LỌC\"" in memo
        assert "NÚT THẮT / TỬ HUYỆT" in memo
        assert "CHẤT XÚC TÁC ĐỊNH GIÁ / LỢI THẾ CẠNH TRANH ĐỘC QUYỀN" in memo
        assert "KẾT LUẬN ĐẦU TƯ" in memo

    asyncio.run(_run())
