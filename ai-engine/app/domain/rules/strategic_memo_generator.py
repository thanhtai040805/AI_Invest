"""Strategic Memo Generator (Financial Quality + Market Risk)

Mô đun sinh "BÁO CÁO CẬP NHẬT CHIẾN LƯỢC" dành cho Giám đốc Đầu tư (Strategy CIO).
Persona: Giám đốc Đầu tư (CIO) lão luyện, đại diện dòng tiền lớn (Smart Money).
Nguyên tắc: Văn phong sắc bén, vạch trần lầm tưởng và neo vào Financial Quality, thị trường và định giá.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CIO_SYSTEM_PERSONA = (
    "Bạn là một Giám đốc Đầu tư (CIO) lão luyện, đại diện cho dòng tiền lớn (Smart Money) "
    "trên thị trường chứng khoán. Bạn có góc nhìn vô cùng thực tế, khắt khe, đi thẳng vào "
    "bản chất dòng tiền và các 'game' tài chính đằng sau doanh nghiệp, thay vì bị mờ mắt "
    "bởi các tin tức PR bề nổi.\n"
    "Quy tắc hành văn:\n"
    "- KHÔNG dùng ngôn ngữ êm tai, màu hồng hay các từ ngữ mỹ miều.\n"
    "- Mọi nhận định phải dựa trên logic phân tích dòng tiền, chi phí cơ hội và chu kỳ kinh tế.\n"
    "- Khai thác triệt để các số liệu, dẫn chứng thực tế từ tài liệu nguồn được đưa cho.\n"
)


class StrategicMemoGenerator:
    """
    Sinh Báo cáo Cập nhật Chiến lược CIO dựa trên sự kết hợp giữa:
    - Financial Quality và trích dẫn BCTC từ nguồn dữ liệu tài chính
    - Rủi ro tài chính, thị trường và định giá từ dữ liệu độc lập
    - Luận điểm mua từ Investment Thesis (Agent-04)
    - Phản biện Devil's Advocate & CTS từ Counter Thesis (Agent-05)
    """

    def __init__(self, llm_client: Optional[Any] = None):
        self.llm_client = llm_client

    def build_prompt(
        self,
        ticker: str,
        company_name: str,
        business_quality_data: Optional[Dict[str, Any]] = None,
        thesis_payload: Optional[Dict[str, Any]] = None,
        counter_payload: Optional[Dict[str, Any]] = None,
        financial_summary: Optional[Dict[str, Any]] = None,
        target_date: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Xây dựng prompt chuẩn hóa theo cấu trúc 4 phần của CIO."""
        t_date = target_date or datetime.now().strftime("%d/%m/%Y")
        clean_ticker = str(ticker).upper().strip()

        # Chuẩn bị dữ liệu tài chính độc lập
        quality_info = business_quality_data or {}
        quality_score = quality_info.get("business_quality_score", quality_info.get("f2_quality", "Chưa xác định"))
        quality_status = quality_info.get("business_quality_status", "FINANCIAL_QUALITY")
        evidence_quote = quality_info.get("evidence_quote") or quality_info.get("evidence_summary", {}).get("evidence_quote", "Không có trích dẫn")
        pillars = quality_info.get("pillars") or {}
        pillars_summary = []
        if isinstance(pillars, dict):
            for p_name, p_val in pillars.items():
                if isinstance(p_val, dict):
                    score = p_val.get("score")
                    verdict = p_val.get("verdict", "")
                    pillars_summary.append(f"- Trụ {p_name}: Score={score}, Trạng thái={verdict}")

        # Chuẩn bị dữ liệu từ Thesis và Counter-Thesis
        t_body = (thesis_payload or {}).get("thesis_body", {})
        c_verdict = (counter_payload or {}).get("verdict", "PROCEED")
        cts_score = (counter_payload or {}).get("cts_score", 0.0)
        holes = (counter_payload or {}).get("holes", [])
        constraints = (counter_payload or {}).get("execution_constraints", {})

        user_content = f"""
Nhiệm vụ: Dựa vào các dữ liệu được cung cấp dưới đây, hãy phân tích mã cổ phiếu {clean_ticker} và viết một Báo cáo Cập nhật Chiến lược với văn phong sắc bén, phũ phàng nhưng chính xác.

=== NGUỒN DỮ LIỆU ĐƯỢC XÁC THỰC TỪ HỆ THỐNG ===
Cổ phiếu: {clean_ticker} - {company_name}
Thời điểm phân tích: {t_date}

1. FINANCIAL QUALITY (TỪ DỮ LIỆU TÀI CHÍNH):
- Điểm Financial Quality: {quality_score}/100
- Bằng chứng thực tế trích dẫn từ BCTC: "{evidence_quote}"
- Chi tiết Financial Quality:
{chr(10).join(pillars_summary) if pillars_summary else "- Chưa có dữ liệu trụ cột chi tiết"}

2. RỦI RO TÀI CHÍNH VÀ THỊ TRƯỜNG:
- Tập trung vào chất lượng lợi nhuận, đòn bẩy, thanh khoản và chế độ thị trường từ dữ liệu được cung cấp.

3. LUẬN ĐIỂM ĐẦU TƯ TỪ THESIS AGENT:
{json.dumps(t_body, ensure_ascii=False, indent=2) if t_body else "Chưa có nội dung Thesis chi tiết"}

4. PHẢN BIỆN TỪ DEVIL'S ADVOCATE (COUNTER THESIS AGENT):
- Phán quyết phản biện: {c_verdict} (Điểm CTS: {cts_score:.1f}/100)
- Các lỗ hổng rủi ro phát hiện: {', '.join(holes) if holes else 'Không phát hiện rủi ro nghiêm trọng'}
- Ràng buộc thực thi: {json.dumps(constraints, ensure_ascii=False) if constraints else 'Không có ràng buộc'}

5. CHỈ SỐ TÀI CHÍNH CỐT LÕI:
{json.dumps(financial_summary or {}, ensure_ascii=False, indent=2)}

=== YÊU CẦU CẤU TRÚC BÁO CÁO (BẮT BUỘC) ===
Hãy trình bày đúng theo định dạng Markdown sau:

# BÁO CÁO CẬP NHẬT CHIẾN LƯỢC: {company_name} ({clean_ticker})
**Chủ đề:** [Một câu tóm tắt ngắn gọn, sắc bén, phản ánh đúng bản chất/tình trạng hiện tại của doanh nghiệp]  
**Thời điểm phân tích:** {t_date}

---

### 1. LOẠI BỎ "NHIỄU LỌC": [Tiêu đề phụ tùy chỉnh]
- Gạch bỏ ngay 1 đến 2 "ảo tưởng" hoặc lầm tưởng của đám đông (nhà đầu tư cá nhân) về cổ phiếu này.
- Phân tích và vạch trần những mảng kinh doanh phụ trợ, những tin đồn PR không tạo ra đột biến về Lợi nhuận cốt lõi (Core Earnings) nhưng lại hay được dùng để "lùa gà".
- Chốt lại bằng Quy tắc 80/20: Định giá của doanh nghiệp này thực chất chỉ bị chi phối tuyệt đối bởi 1-2 yếu tố cốt lõi nào?

### 2. NHÌN THẲNG VÀO NÚT THẮT / TỬ HUYỆT (THE CORE BOTTLENECK)
- Chỉ ra những điểm yếu mang tính cấu trúc, các rủi ro chìm đang bóp nghẹt doanh nghiệp hoặc kìm hãm định giá cổ phiếu.
- Đào sâu: Gánh nặng nợ vay, chi phí vốn (COF), biên lợi nhuận, điểm nghẽn pháp lý hoặc bẫy khấu hao. Giải thích rõ vì sao thị trường đang chiết khấu giá cổ phiếu này.

### 3. CHẤT XÚC TÁC ĐỊNH GIÁ / KHẢ NĂNG DUY TRÌ KẾT QUẢ (THE TRUE CATALYSTS)
- Nếu tử huyệt là rủi ro, thì đâu là "ánh sáng cuối đường hầm"? 
- Chỉ nêu kết luận khi có số liệu hoặc evidence trực tiếp; không suy diễn thành lợi thế độc quyền.
- Đâu là những sự kiện (Event-Driven) mang tính sống còn có thể kích hoạt dòng tiền FOMO và đẩy định giá (Re-rate) lên mạnh mẽ?

### 4. KẾT LUẬN ĐẦU TƯ (INVESTMENT THESIS)
- Định vị rõ ràng loại cổ phiếu (Cổ phiếu Chu kỳ / Phòng thủ ăn cổ tức / Tình huống đặc biệt Turnaround...).
- Khuyến nghị hành động thực tế cho Smart Money:
  + **Nhận diện cạm bẫy:** Tuyệt đối tránh hành động gì? (VD: Đừng lướt sóng, đừng mua đuổi khi ra tin PR).
  + **Điểm mua "Vàng" (Golden Entry):** Đâu là thời điểm kích hoạt gom hàng an toàn nhất?
  + **Chiến lược nắm giữ & Điều kiện chốt lời:** Nắm giữ chờ đợi điều kiện gì để hiện thực hóa lợi nhuận?
"""
        return [
            {"role": "system", "content": CIO_SYSTEM_PERSONA},
            {"role": "user", "content": user_content},
        ]

    async def generate_memo(
        self,
        ticker: str,
        company_name: str,
        business_quality_data: Optional[Dict[str, Any]] = None,
        thesis_payload: Optional[Dict[str, Any]] = None,
        counter_payload: Optional[Dict[str, Any]] = None,
        financial_summary: Optional[Dict[str, Any]] = None,
        target_date: Optional[str] = None,
    ) -> str:
        """Sinh Báo cáo Chiến lược hoàn chỉnh, có fallback nếu không có LLM."""
        clean_ticker = str(ticker).upper().strip()
        messages = self.build_prompt(
            ticker=clean_ticker,
            company_name=company_name,
            business_quality_data=business_quality_data,
            thesis_payload=thesis_payload,
            counter_payload=counter_payload,
            financial_summary=financial_summary,
            target_date=target_date,
        )

        if self.llm_client and getattr(self.llm_client, "is_configured", True):
            try:
                resp = await self.llm_client.chat(messages, temperature=0.3, max_tokens=2500)
                if resp and len(resp.strip()) > 200:
                    return resp.strip()
            except Exception as e:
                logger.warning(f"[StrategicMemoGenerator] Lỗi gọi LLM ({e}), chuyển sang fallback template.")

        # Fallback có cấu trúc nếu không có kết nối LLM
        return self._generate_fallback_memo(
            ticker=clean_ticker,
            company_name=company_name,
            business_quality_data=business_quality_data,
            thesis_payload=thesis_payload,
            counter_payload=counter_payload,
        )

    def _generate_fallback_memo(
        self,
        ticker: str,
        company_name: str,
        business_quality_data: Optional[Dict[str, Any]],
        thesis_payload: Optional[Dict[str, Any]],
        counter_payload: Optional[Dict[str, Any]],
    ) -> str:
        """Bản dự phòng chuẩn khi chưa cấu hình API Key."""
        quality_score = (business_quality_data or {}).get("business_quality_score", (business_quality_data or {}).get("f2_quality", "N/A"))
        quality_status = (business_quality_data or {}).get("business_quality_status", "FINANCIAL_QUALITY")
        quote = (business_quality_data or {}).get("evidence_quote", "Chưa có trích dẫn bằng chứng cụ thể.")
        verdict = (counter_payload or {}).get("verdict", "PROCEED")
        cts = (counter_payload or {}).get("cts_score", 0.0)
        today_str = datetime.now().strftime("%d/%m/%Y")

        return f"""# BÁO CÁO CẬP NHẬT CHIẾN LƯỢC: {company_name} ({ticker})
**Chủ đề:** Thẩm định thực chất dòng tiền và rủi ro cấu trúc tại {ticker}  
**Thời điểm phân tích:** {today_str}

---

### 1. LOẠI BỎ "NHIỄU LỌC"
- Gạt bỏ các tin đồn ngắn hạn về việc mở rộng mảng kinh doanh phụ trợ không đóng góp trọng yếu vào EBITDA.
- **Quy tắc 80/20:** Định giá của {ticker} bị chi phối 80% bởi chu kỳ cung-cầu cốt lõi và khả năng bảo toàn biên lợi nhuận gộp.

### 2. NHÌN THẲNG VÀO NÚT THẮT / TỬ HUYỆT (THE CORE BOTTLENECK)
- Áp lực từ chi phí vốn và điểm nghẽn tiến độ dự án là nguyên nhân thị trường đang chiết khấu định giá. Phản biện Devil's Advocate cảnh báo điểm CTS ở mức {cts:.1f}/100.

### 3. CHẤT XÚC TÁC ĐỊNH GIÁ / KHẢ NĂNG DUY TRÌ KẾT QUẢ (THE TRUE CATALYSTS)
- Điểm Financial Quality từ dữ liệu hiện có: **{quality_score}/100** ({quality_status}).
- Bằng chứng thực tế: "{quote}"
- Động lực mở khóa giá trị phải được chứng minh bằng kết quả tài chính, dòng tiền hoặc catalyst cụ thể; không mặc định là lợi thế độc quyền.

### 4. KẾT LUẬN ĐẦU TƯ (INVESTMENT THESIS)
- Phán quyết phản biện hiện tại: **{verdict}**.
- **Nhận diện cạm bẫy:** Tuyệt đối không mua đuổi trong các phiên hưng phấn đột biến thanh khoản.
- **Điểm mua Vàng:** Chỉ giải ngân khi định giá rơi vào vùng chiết khấu an toàn biên độ cao.
"""
