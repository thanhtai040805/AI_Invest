"""CSS Scoring Engine — TASK-215

Tổng hợp điểm từ 6 nhóm Factor thành Composite Sentiment Score (CSS).
Áp dụng trọng số theo Market Regime.
Phân loại Conviction Level (A+, A, B, C, D).
"""

import logging
import os
import pandas as pd
from typing import List, Dict, Any, Optional
from enum import Enum
from app.domain.rules.market.hmm_classifier import MarketRegime

logger = logging.getLogger(__name__)

class ConvictionLevel(Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"

class CSSScoringEngine:
    def __init__(self):
        self.enable_ml_meta_labeling = os.getenv("ENABLE_ML_META_LABELING", "False").lower() in ("true", "1", "yes")
        # REGIME_WEIGHTS định nghĩa trong IOS/Blueprint
        self.regime_weights = {
            MarketRegime.BULL_TRENDING: {
                "f1_value": 0.15, "f2_quality": 0.15, "f3_momentum": 0.30, 
                "f4_earnings": 0.15, "f5_flow": 0.15, "f6_technical": 0.10
            },
            MarketRegime.BEAR_TRENDING: {
                "f1_value": 0.25, "f2_quality": 0.35, "f3_momentum": 0.05, 
                "f4_earnings": 0.10, "f5_flow": 0.15, "f6_technical": 0.10
            },
            MarketRegime.SIDEWAYS: {
                "f1_value": 0.10, "f2_quality": 0.20, "f3_momentum": 0.10, 
                "f4_earnings": 0.25, "f5_flow": 0.25, "f6_technical": 0.10
            },
            "DEFAULT": {
                "f1_value": 0.20, "f2_quality": 0.20, "f3_momentum": 0.20, 
                "f4_earnings": 0.20, "f5_flow": 0.10, "f6_technical": 0.10
            }
        }
        
        # Sector Override cho Bất động sản (Đầu cơ theo dòng tiền)
        self.real_estate_weights = {
            "f1_value": 0.10, "f2_quality": 0.05, "f3_momentum": 0.30, 
            "f4_earnings": 0.05, "f5_flow": 0.40, "f6_technical": 0.10
        }

    def calculate_css(self, factor_scores: pd.DataFrame, regime: MarketRegime) -> pd.DataFrame:
        """Tính CSS dựa trên trọng số regime."""
        weights = self.regime_weights.get(regime, self.regime_weights.get(MarketRegime.SIDEWAYS, self.regime_weights["DEFAULT"]))
        
        # Hàm áp dụng trọng số dựa trên sector
        def apply_weights(row):
            w = self.real_estate_weights if row.get("sector") == "Bất động sản" else weights
            
            # Đảm bảo các cột điểm tồn tại, nếu không coi là 50 (neutral)
            score = 0
            for f, weight in w.items():
                val = row.get(f, 50.0)
                if pd.isna(val):
                    val = 50.0
                score += val * weight
            return score

        # Tính Base CSS
        factor_scores['base_css'] = factor_scores.apply(apply_weights, axis=1)
        
        # Áp dụng Moat Multiplier (nếu có, mặc định là 1.0)
        if 'moat_multiplier' not in factor_scores.columns:
            factor_scores['moat_multiplier'] = 1.0
        
        factor_scores['css'] = factor_scores['base_css'] * factor_scores['moat_multiplier']
        
        # Xác định Conviction Level kèm theo Gatekeeper (Rule-based)
        factor_scores['conviction'] = factor_scores.apply(self._apply_gatekeeper, axis=1)
        
        # ==========================================
        # META-LABELING: ML Alpha Conviction Booster
        # ==========================================
        if 'ml_alpha_score' in factor_scores.columns:
            # Lưu lại hạng gốc
            factor_scores['conviction_pre_ml'] = factor_scores['conviction']
            
            # Chỉ áp dụng Booster/Veto nếu công tắc bật
            if self.enable_ml_meta_labeling:
                factor_scores['conviction'] = factor_scores.apply(self._apply_meta_labeling, axis=1)
        
        return factor_scores

    def _apply_gatekeeper(self, row: pd.Series) -> str:
        """Gatekeeper Rule: Check quality and audit/gil status before mapping to Conviction."""
        audit = row.get('audit_opinion', 'UNQUALIFIED')
        gil = row.get('gil_flag', 'PASS')
        
        if audit != 'UNQUALIFIED' or gil == 'CATASTROPHIC':
            return ConvictionLevel.E.value
            
        css = row['css']
        conviction = self._get_conviction_base(css)
        
        f2_percentile = row.get('f2_quality_percentile', 50)
        sector = row.get('sector', '')
        catalyst = row.get('catalyst', '')
        
        # Nếu F2 Quality < 20%
        if f2_percentile < 20:
            # Ngoại lệ: Cổ phiếu Chu kỳ có Ngòi nổ Vĩ mô (Đảo chiều)
            if sector == "Cyclical" and catalyst == "Macro_Price_Turnaround":
                pass # Bỏ qua rào cản
            else:
                # Ép hạ cấp Conviction
                if conviction in [ConvictionLevel.A_PLUS.value, ConvictionLevel.A.value, ConvictionLevel.B.value]:
                    conviction = ConvictionLevel.C.value
                    
        return conviction

    def _get_conviction_base(self, css: float) -> str:
        """Phân loại Conviction Level theo ngưỡng trong Spec."""
        if css >= 85: return ConvictionLevel.A_PLUS.value
        if css >= 75: return ConvictionLevel.A.value
        if css >= 60: return ConvictionLevel.B.value
        if css >= 45: return ConvictionLevel.C.value
        return ConvictionLevel.D.value

    def _apply_meta_labeling(self, row: pd.Series) -> str:
        """Dùng điểm ML để Veto (hạ bậc) hoặc Boost (Nâng bậc) Conviction."""
        conviction = row['conviction']
        ml_alpha = row.get('ml_alpha_score')
        
        if pd.isna(ml_alpha):
            return conviction
            
        # 1. VETO (Phủ quyết)
        if ml_alpha <= 0.35:
            if conviction in [ConvictionLevel.A_PLUS.value, ConvictionLevel.A.value, ConvictionLevel.B.value, ConvictionLevel.C.value]:
                return ConvictionLevel.D.value
        
        # 2. BOOSTER (Khuếch đại)
        elif ml_alpha >= 0.70 and conviction != ConvictionLevel.D.value and conviction != ConvictionLevel.E.value:
            if conviction == ConvictionLevel.A.value:
                return ConvictionLevel.A_PLUS.value
            elif conviction == ConvictionLevel.B.value:
                return ConvictionLevel.A.value
            elif conviction == ConvictionLevel.C.value:
                return ConvictionLevel.B.value
                
        return conviction

css_scoring_engine = CSSScoringEngine()
