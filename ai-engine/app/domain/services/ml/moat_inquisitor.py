import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class MoatInquisitor:
    """
    Kết nối với SAG Backend để truy vấn và chấm điểm Lợi thế cạnh tranh (Moat).
    """
    def __init__(self, api_base: str = "http://localhost:8000/api/v1"):
        self.api_base = api_base
        
    def _get_core_metric_for_sector(self, sector: str) -> str:
        """Xác định Core Metric (Chỉ số 80/20) dựa trên Sector."""
        sector = sector.lower()
        if "ngân hàng" in sector or "bank" in sector:
            return "Tỷ lệ CASA và Biên lãi thuần (NIM)"
        elif "bán lẻ" in sector or "retail" in sector or "fmcg" in sector:
            return "Hiệu ứng quy mô, Mạng lưới phân phối, Vòng quay tài sản (Asset Turnover)"
        elif "sản xuất" in sector or "vật liệu" in sector or "thép" in sector:
            return "Lợi thế chi phí thấp, Biên lãi gộp (GPM)"
        elif "bất động sản" in sector or "real estate" in sector:
            return "Quỹ đất sạch, Năng lực pháp lý, Tỷ lệ người mua trả tiền trước"
        else:
            return "Lợi thế cạnh tranh cốt lõi, Biên lợi nhuận, Thị phần"

    def get_moat_assessment(self, ticker: str, sector: str) -> Dict[str, Any]:
        """
        Gửi yêu cầu RAG tới SAG Backend để đánh giá Moat.
        Rubric: Awareness (40), Action (40), Intangible (20).
        Kill-Switch: Must have evidence_quote.
        """
        core_metric = self._get_core_metric_for_sector(sector)
        
        prompt = f"""Hãy đánh giá Lợi thế cạnh tranh (Economic Moat) của mã {ticker.upper()}.
Chỉ số cốt lõi (Core Metric) cần tìm kiếm: {core_metric}.
Chấm điểm theo Rubric 100 điểm:
- Awareness (40đ): Ban lãnh đạo có nhắc đích danh đến {core_metric} trong tài liệu không?
- Action (40đ): Doanh nghiệp có hành động (CapEx, Đầu tư, R&D) để bảo vệ/phát triển lợi thế này không?
- Intangible (20đ): Có lợi thế vô hình nào khác không (Vị trí, Giấy phép...)?

LƯU Ý QUAN TRỌNG: 
Bắt buộc phải trích dẫn nguyên văn (evidence_quote) một đoạn trong tài liệu để chứng minh. 
Nếu không tìm thấy bất kỳ bằng chứng nào, điểm Moat tự động bằng 0.

Trả về kết quả dưới định dạng JSON với các keys: "moat_score" (số), "evidence_quote" (chuỗi), "multiplier" (số).
Quy tắc Multiplier: 
- Nếu moat_score == 0 (không có bằng chứng) -> multiplier = 0.75
- Nếu moat_score <= 50 -> multiplier = 1.00
- Nếu moat_score > 50 -> multiplier = 1.20 (tối đa)
"""
        
        payload = {
            "query": prompt,
            "filter": {"ticker": ticker.upper()},
            "stream": False
        }
        
        default_response = {
            "moat_score": 0, 
            "evidence_quote": "", 
            "multiplier": 1.0,
            "status": "DEFAULT"
        }
        
        try:
            with httpx.Client(timeout=30.0) as client:
                res = client.post(f"{self.api_base}/generation", json=payload)
                if res.status_code == 200:
                    data = res.json()
                    # Trích xuất JSON từ text response nếu LLM trả về markdown
                    # Ở đây giả định SAG Backend trả về đúng định dạng JSON
                    moat_data = data.get("response", {})
                    if isinstance(moat_data, dict) and "multiplier" in moat_data:
                        return moat_data
                    
                    # Nếu response là text, cần parser cơ bản
                    text_resp = data.get("text", "")
                    import json, re
                    match = re.search(r'\{.*\}', text_resp, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group(0))
                        return parsed
                        
        except Exception as e:
            logger.error(f"Lỗi khi gọi SAG Backend cho {ticker}: {e}")
            
        return default_response

moat_inquisitor = MoatInquisitor()
