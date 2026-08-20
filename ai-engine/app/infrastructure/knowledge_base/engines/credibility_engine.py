"""Credibility & Cross-Verification Engine — Layer 6 Evidence Graph

Phần mềm chịu trách nhiệm:
1. Đánh trọng số tin cậy nguồn tin (Source Weights).
2. Kiểm chứng chéo kế hoạch vs thực tế (Cross-Verification).
3. Định vị bằng chứng truy vết (Traceability).
"""

import re
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Big 4 auditor name patterns
_BIG_4_PATTERNS = [r"pwc", r"deloitte", r"ey", r"ernst", r"kpmg"]

class CredibilityEngine:
    def __init__(self):
        # Base source weights
        self.source_weights = {
            "financial_statement": 0.90,
            "agm_resolution": 0.90,
            "governance_report": 0.90,
            "annual_report": 0.85,
            "analyst_report": 0.70,
            "news": 0.50,
            "social_media": 0.20,
        }

    def calculate_document_credibility(self, doc: Dict[str, Any]) -> float:
        """Tính điểm tin cậy của tài liệu W_cred thuộc [0.0, 1.0]."""
        doc_type = doc.get("doc_type", "news")
        base_weight = self.source_weights.get(doc_type, 0.50)
        
        # Đặc quyền cho BCTC được kiểm toán bởi Big 4
        if doc_type == "financial_statement":
            title = (doc.get("title") or "").lower()
            content = (doc.get("article_content") or doc.get("article_pdf_text") or "").lower()
            
            is_audited = "kiểm toán" in title or "audited" in title or "kiểm toán" in content
            is_big4 = any(re.search(pat, title) or re.search(pat, content[:2000]) for pat in _BIG_4_PATTERNS)
            
            if is_audited and is_big4:
                return 1.00  # Tin cậy tối đa
            elif is_audited:
                return 0.95  # Kiểm toán thường
            else:
                return 0.90  # BCTC tự lập
                
        # CBTT chính thức từ HOSE/UBCKNN
        source = (doc.get("source") or "").lower()
        if source in ("hose", "ssc", "cafef_docs") and base_weight < 0.95:
            # Nâng trọng số cho nguồn công bố chính thống
            return max(base_weight, 0.95)
            
        return base_weight

    def cross_verify_consistency(self, symbol: str, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Thực hiện đối chiếu chéo giữa Kế hoạch (BCTN/Nghị quyết) và Số liệu thực tế (BCTC).
        
        Tính toán:
        - Consistency Index: Điểm nhất quán của ban lãnh đạo (0 - 100).
        - Flags: Danh sách các điểm mâu thuẫn phát hiện được.
        """
        consistency_score = 100.0
        flags = []
        evidences = []

        # Tách tài liệu theo nhóm để tiện đối chiếu
        guidances = []  # Kế hoạch (Layer 5)
        realities = []  # Thực tế (BCTC - Layer 3)
        capex_plans = [] # Kế hoạch đầu tư
        capex_realities = [] # Chi tiền thực tế

        for doc in documents:
            graph = doc.get("graph_nodes") or {}
            # Đảm bảo graph_nodes được parse thành dict nếu ở dạng JSON string
            if isinstance(graph, str):
                try:
                    graph = json.loads(graph)
                except Exception:
                    graph = {}

            # Thu thập Kỳ vọng (Expectations) từ Nghị quyết/BCTN
            for exp in graph.get("expectations", []):
                exp_type = exp.get("type", "")
                if exp_type == "GUIDANCE":
                    guidances.append(exp)
                elif exp_type == "CAPEX_PLAN":
                    capex_plans.append(exp)

            # Thu thập Sự kiện (Events) thực tế từ BCTC
            for evt in graph.get("events", []):
                evt_type = evt.get("type", "")
                if evt_type == "EARNINGS":
                    realities.append(evt)
                elif evt_type == "CAPEX":
                    # Support legacy check where capex plans were in events with "plan" in ID
                    evt_id = evt.get("id", "") or evt.get("node_id", "")
                    if "plan" in str(evt_id).lower() or "plan" in str(evt.get("description", "")).lower():
                        capex_plans.append(evt)
                    else:
                        capex_realities.append(evt)
                elif evt_type == "CAPEX_PLAN":
                    capex_plans.append(evt)

        # ── KỊCH BẢN 1: So sánh Kế hoạch Doanh thu/Lợi nhuận vs Thực tế ──
        for g in guidances:
            metric = (g.get("metric") or g.get("target_metric") or "").lower()
            target_val = g.get("target_value")
            
            if target_val and metric in ("revenue", "net_income", "profit"):
                # Tìm sự kiện thực tế khớp với chỉ tiêu
                for r in realities:
                    # Lấy giá trị thực tế tương ứng (giả định cấu trúc event chứa data hoặc description)
                    # Thử phân tích description để bốc số thực tế hoặc so khớp trong logic
                    desc = r.get("description", "").lower()
                    
                    # Mô phỏng đối chiếu số liệu
                    # Nếu thực tế chỉ đạt dưới 60% kế hoạch đặt ra trong Nghị quyết
                    # Trừ điểm nhất quán và đánh Flag
                    if "thực tế" in desc or "đạt" in desc:
                        # (Ví dụ giả lập đối chiếu số liệu)
                        pass

        # ── KỊCH BẢN 2: Dự án/CapEx trên BCTN vs Lưu chuyển tiền tệ (CFO/CFI) ──
        # Nếu doanh nghiệp công bố kế hoạch đầu tư lớn (CapEx Plan) nhưng dòng tiền đầu tư 
        # (CFI) trong BCTC thực tế gần như bằng 0 hoặc không giải ngân
        if capex_plans and not capex_realities:
            consistency_score -= 15.0
            flags.append({
                "type": "CAPEX_FREEZE",
                "severity": "WARNING",
                "message": f"Doanh nghiệp công bố kế hoạch đầu tư nhưng không ghi nhận dòng tiền CapEx thực tế trên BCTC."
            })

        # Giới hạn điểm nhất quán tối thiểu là 0
        consistency_score = max(0.0, consistency_score)

        return {
            "symbol": symbol.upper(),
            "consistency_index": consistency_score,
            "flags": flags,
            "verified_at": datetime.now(timezone.utc).isoformat()
        }

    def build_evidence_node(self, source_doc: Dict[str, Any], citation_detail: str) -> Dict[str, Any]:
        """Tạo nút bằng chứng (Layer 6) liên kết đến tài liệu gốc."""
        return {
            "source_id": source_doc.get("id"),
            "source_title": source_doc.get("title"),
            "url": source_doc.get("url"),
            "published_date": source_doc.get("published_date").isoformat() if isinstance(source_doc.get("published_date"), datetime) else source_doc.get("published_date"),
            "citation": citation_detail,
            "confidence_score": self.calculate_document_credibility(source_doc)
        }

credibility_engine = CredibilityEngine()
