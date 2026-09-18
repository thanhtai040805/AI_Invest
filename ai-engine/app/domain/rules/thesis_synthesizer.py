"""Thesis Synthesizer (IOS v5.1)

Mô đun LLM Thesis Synthesizer cho Agent-04 (Investment Thesis).
Chức năng:
- Tiếp nhận Financial Quality và GIL từ SAG v2.
- Nạp các chỉ số tài chính cốt lõi (P/E, P/B, ROE, GPM, SUE) để tạo lập luận định chế sắc bén.
- Định vị phong cách đầu tư (Investment Style Taxonomy).
- Ép buộc các điều kiện hủy luận điểm (Invalidation Triggers) phải có NGƯỠNG ĐỊNH LƯỢNG cụ thể.
- Giữ nguyên các phép tính định giá Target Price và Stop-loss từ toán học định lượng (Zero Hallucination).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

THESIS_SYNTHESIZER_SYSTEM_PROMPT = (
    "Bạn là Giám đốc Chiến lược Cổ phiếu (Head of Equity Strategy) của một quỹ đầu tư định chế lớn.\n"
    "Nhiệm vụ: Xây dựng Luận điểm Đầu tư (Investment Thesis) sắc bén, thực dụng, đi thẳng vào dòng tiền thật "
    "và ngòi nổ thực tế, dựa trên Financial Quality, GIL và chỉ số tài chính từ BCTC.\n"
    "Quy tắc bắt buộc:\n"
    "1. KHÔNG viết văn PR màu hồng hay nhận định chung chung. Mọi lập luận phải gắn với dòng tiền tự do (FCF) và chất lượng lợi nhuận.\n"
    "2. Neo trực tiếp vào bằng chứng tài chính/kinh doanh được cung cấp; không suy diễn thành lợi thế độc quyền khi tài liệu không chứng minh.\n"
    "3. Điều kiện hủy luận điểm (Invalidation Triggers) BẮT BUỘC PHẢI CÓ CON SỐ ĐỊNH LƯỢNG cụ thể (ví dụ: GPM giảm > 200 bps, nợ vay tăng > 20%...).\n"
    "4. Trả về đúng định dạng JSON Schema yêu cầu, không kèm văn bản ngoài JSON."
)


class ThesisSynthesizer:
    """
    Bộ tổng hợp luận điểm định tính bằng LLM cho Agent-04.
    """

    def __init__(self, llm_client: Optional[Any] = None):
        self.llm_client = llm_client

    def build_prompt(
        self,
        ticker: str,
        sector: str,
        business_quality_data: Dict[str, Any],
        catalyst_type: str,
        price_target_info: Dict[str, Any],
        current_price: float,
        timeline_months: int,
        independent_signals: Optional[Dict[str, str]] = None,
        financial_metrics: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        clean_ticker = str(ticker).upper().strip()
        quality_score = business_quality_data.get("business_quality_score", business_quality_data.get("f2_quality", "Chưa có"))
        quality_status = business_quality_data.get("business_quality_status", "FINANCIAL_QUALITY")
        evidence_quote = (
            business_quality_data.get("evidence_quote")
            or business_quality_data.get("evidence_summary", {}).get("evidence_quote")
            or "Chưa có trích dẫn cụ thể"
        )
        pillars = business_quality_data.get("pillars") or {}
        pillars_lines = []
        if isinstance(pillars, dict):
            for k, v in pillars.items():
                if isinstance(v, dict):
                    pillars_lines.append(f"- Trụ {k}: Score={v.get('score')}, Verdict={v.get('verdict')}")

        base_case = price_target_info.get("base_case", current_price * 1.15)
        bull_case = price_target_info.get("bull_case", current_price * 1.25)
        upside_pct = round(((base_case / current_price) - 1.0) * 100.0, 1) if current_price > 0 else 15.0

        fin = financial_metrics or {}
        fin_lines = []
        if fin.get("pe"):
            fin_lines.append(f"- P/E hiện tại: {float(fin['pe']):.1f}x")
        if fin.get("pb"):
            fin_lines.append(f"- P/B hiện tại: {float(fin['pb']):.2f}x")
        if fin.get("roe"):
            fin_lines.append(f"- ROE: {float(fin['roe']):.1f}%")
        if fin.get("roic"):
            fin_lines.append(f"- ROIC: {float(fin['roic']):.1f}%")
        if fin.get("gpm"):
            fin_lines.append(f"- Biên lãi gộp (GPM): {float(fin['gpm']):.1f}%")
        if fin.get("earnings_growth"):
            fin_lines.append(f"- Tăng trưởng LN quý gần nhất (SUE): {float(fin['earnings_growth']):.1f}%")

        user_content = f"""
Cổ phiếu: {clean_ticker} (Ngành: {sector})
Giá hiện tại: {current_price:,.0f} VNĐ
Mục tiêu giá: {base_case:,.0f} VNĐ (+{upside_pct}% trong {timeline_months} tháng), Bull Case: {bull_case:,.0f} VNĐ.
Ngòi nổ xúc tác sơ bộ: {catalyst_type}

CHỈ SỐ TÀI CHÍNH CỐT LÕI:
{chr(10).join(fin_lines) if fin_lines else "- P/E, P/B và các tỷ số đang được cập nhật"}

DỮ LIỆU FINANCIAL QUALITY TỪ HỆ THỐNG:
- Điểm Financial Quality: {quality_score}/100
- Trạng thái: {quality_status}
- Evidence được cung cấp: "{evidence_quote}"
- Các chiều đánh giá:
{chr(10).join(pillars_lines) if pillars_lines else "- Chi tiết trụ cột đang cập nhật"}

TÍN HIỆU ĐỘC LẬP ĐÃ XÁC NHẬN:
{json.dumps(independent_signals or {}, ensure_ascii=False, indent=2)}

YÊU CẦU ĐẦU RA (JSON FORMAT):
Hãy tổng hợp luận điểm đầu tư chuyên sâu theo đúng cấu trúc JSON sau:
{{
    "investment_style": "CYCLICAL_GROWTH | DEEP_VALUE | SPECIAL_SITUATION_TURNAROUND | QUALITY_COMPOUNDER",
    "why_now": "Bước ngoặt điểm rơi lợi nhuận (Inflection Point) trong 1-2 quý tới là gì? Tại sao dòng tiền lớn gom hàng lúc này?",
    "why_this_stock": "Điểm đáng chú ý của doanh nghiệp dựa trên Financial Quality và evidence được cung cấp.",
    "catalyst_description": "Mô tả chi tiết ngòi nổ cốt lõi (tên dự án, công suất, mốc thời gian thương mại hóa, câu chuyện mở room/tái cấu trúc)",
    "pre_mortem": [
        "Kịch bản sai lầm 1 (Rủi ro chi phí nguyên vật liệu đầu vào / chu kỳ vĩ mô)",
        "Kịch bản sai lầm 2 (Rủi ro cạnh tranh / pháp lý / khách hàng)",
        "Kịch bản sai lầm 3 (Rủi ro thực thi / chậm tiến độ ngòi nổ)"
    ],
    "invalidation_triggers": [
        "Trigger 1: [Ngưỡng định lượng cụ thể trong BCTC quý tiếp theo, ví dụ GPM sụt giảm > 200 bps]",
        "Trigger 2: [Sự kiện gãy ngòi nổ catalyst, ví dụ tiến độ dự án hoãn quá thời hạn cụ thể]",
        "Trigger 3: [Tín hiệu vi phạm dòng tiền hoặc nợ vay, ví dụ Nợ ròng/EBITDA vượt ngưỡng cụ thể]"
    ]
}}
"""
        return [
            {"role": "system", "content": THESIS_SYNTHESIZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    async def synthesize_thesis_narrative(
        self,
        ticker: str,
        sector: str,
        business_quality_data: Dict[str, Any],
        catalyst_type: str,
        price_target_info: Dict[str, Any],
        current_price: float,
        timeline_months: int,
        independent_signals: Optional[Dict[str, str]] = None,
        financial_metrics: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Gọi LLM để làm giàu luận điểm đầu tư định tính."""
        if not self.llm_client:
            return None

        clean_ticker = str(ticker).upper().strip()
        messages = self.build_prompt(
            ticker=clean_ticker,
            sector=sector,
            business_quality_data=business_quality_data,
            catalyst_type=catalyst_type,
            price_target_info=price_target_info,
            current_price=current_price,
            timeline_months=timeline_months,
            independent_signals=independent_signals,
            financial_metrics=financial_metrics,
        )

        try:
            if hasattr(self.llm_client, "complete_json"):
                data = await self.llm_client.complete_json(messages)
            else:
                resp = await self.llm_client.chat(messages)
                from app.infrastructure.llm.client import clean_and_parse_json
                data = clean_and_parse_json(resp)

            # Kiểm tra tính toàn vẹn của các trường
            style = str(data.get("investment_style") or "QUALITY_COMPOUNDER").strip()
            why_now = str(data.get("why_now") or "").strip()
            why_this_stock = str(data.get("why_this_stock") or "").strip()
            cat_desc = str(data.get("catalyst_description") or "").strip()
            pre_mortem = [str(x).strip() for x in data.get("pre_mortem", []) if str(x).strip()]
            invalidation = [str(x).strip() for x in data.get("invalidation_triggers", []) if str(x).strip()]

            if why_now and why_this_stock and len(pre_mortem) >= 2 and len(invalidation) >= 2:
                return {
                    "investment_style": style,
                    "why_now": why_now,
                    "why_this_stock": why_this_stock,
                    "catalyst_description": cat_desc,
                    "pre_mortem": pre_mortem,
                    "invalidation_triggers": invalidation,
                }
            logger.warning(f"[ThesisSynthesizer] LLM trả về thiếu trường cho {clean_ticker}.")
            return None
        except Exception as e:
            logger.warning(f"[ThesisSynthesizer] Lỗi gọi LLM cho {clean_ticker} ({e}), dùng baseline template.")
            return None
