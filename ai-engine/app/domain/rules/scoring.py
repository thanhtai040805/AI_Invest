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

    def clamp_rl_weights(
        self,
        base_weights: Dict[str, float],
        rl_weights: Optional[Dict[str, float]],
        max_relative_deviation: float = 0.20,
    ) -> Dict[str, float]:
        """Allow Agent-10 RL to tune weights within +/- max_relative_deviation around base playbook weights."""
        if not rl_weights:
            return dict(base_weights)
        clamped: Dict[str, float] = {}
        for key, base_val in base_weights.items():
            raw = rl_weights.get(key)
            if raw is None:
                clamped[key] = float(base_val)
                continue
            low = float(base_val) * (1.0 - max_relative_deviation)
            high = float(base_val) * (1.0 + max_relative_deviation)
            clamped[key] = max(low, min(high, float(raw)))
        total = sum(clamped.values())
        if total <= 0:
            return dict(base_weights)
        return {k: round(v / total, 4) for k, v in clamped.items()}

    def calculate_playbook_score(
        self,
        factors: Dict[str, float],
        leadership: Optional[Dict[str, Any]] = None,
        playbook: str = "MARKET_LEADER",
        universe_group: str = "B",
    ) -> Dict[str, Any]:
        """Score a candidate specifically for its active InvestmentPlaybook (MARKET_LEADER vs TACTICAL_SWING)."""
        pb = str(playbook or "MARKET_LEADER").upper().strip()
        f1 = float(factors.get("f1_value", 50.0))
        f2 = float(factors.get("f2_quality", 50.0))
        f3 = float(factors.get("f3_momentum", 50.0))
        f4 = float(factors.get("f4_earnings", 50.0))
        f5 = float(factors.get("f5_flow", 50.0))
        f6 = float(factors.get("f6_technical", 50.0))

        lead = leadership or {}
        has_lead = bool(lead)
        rs_score = float(lead.get("rs_score", f3))
        rs_20d = float(lead.get("rs_20d", 0.0))
        sector_rank = float(lead.get("sector_leadership_score", 50.0))
        trend_score = float(lead.get("trend_template_score", f6))
        vsa_score = float(lead.get("vsa_swing_score", f6))
        big_picture_score = float(
            lead.get("big_picture_score", vsa_score if pb == "TACTICAL_SWING" else trend_score)
        )
        price_structure = str(lead.get("price_structure", "NEUTRAL"))
        money_flow_state = str(lead.get("money_flow_state", "NEUTRAL"))
        close_above_ma20 = bool(lead.get("close_above_ma20", True))
        has_upthrust = bool(lead.get("has_bearish_upthrust", False))

        group_upper = str(universe_group or "B").upper().strip()
        liq_bonus = 90.0 if group_upper == "A" else (65.0 if group_upper == "B" else 40.0)

        disqualifiers: List[str] = []

        if pb == "TACTICAL_SWING":
            # Tactical swing / rebound: Big-Picture structure + VSA absorption + quality/liquidity + value + RS
            quality_liq = 0.75 * f2 + 0.25 * liq_bonus
            score = (
                0.20 * vsa_score
                + 0.15 * big_picture_score
                + 0.25 * quality_liq
                + 0.20 * f1
                + 0.20 * rs_score
            )
            if has_upthrust:
                disqualifiers.append("VSA_BEARISH_UPTHRUST")
            if price_structure == "DISTRIBUTION_TOP" and money_flow_state == "DISTRIBUTING":
                disqualifiers.append("BIG_PICTURE_DISTRIBUTION_TOP")
            if f2 < 40.0:
                disqualifiers.append("TACTICAL_QUALITY_BELOW_40")
        else:
            # MARKET_LEADER (Bull): RS vs VNINDEX + Sector Leadership + Big-Picture Trend/Flow + Earnings + Quality + Value
            score = (
                0.20 * rs_score
                + 0.10 * sector_rank
                + 0.10 * trend_score
                + 0.10 * big_picture_score
                + 0.10 * f5
                + 0.15 * f4
                + 0.15 * f2
                + 0.10 * f1
            )
            if has_lead:
                if rs_20d <= 0.0:
                    disqualifiers.append("LEADER_RS20D_NONPOSITIVE")
                if not close_above_ma20:
                    disqualifiers.append("LEADER_BELOW_MA20")
                if has_upthrust:
                    disqualifiers.append("VSA_BEARISH_UPTHRUST")
                if price_structure in ("DISTRIBUTION_TOP", "DOWNTREND_BREAKDOWN") and money_flow_state == "DISTRIBUTING":
                    disqualifiers.append("BIG_PICTURE_DISTRIBUTING")
            if f2 < 45.0:
                disqualifiers.append("LEADER_QUALITY_BELOW_45")

        return {
            "playbook": pb,
            "playbook_score": round(max(0.0, min(100.0, score)), 2),
            "disqualifiers": disqualifiers,
            "components": {
                "rs_score": round(rs_score, 2),
                "rs_20d": round(rs_20d, 4),
                "sector_leadership_score": round(sector_rank, 2),
                "trend_template_score": round(trend_score, 2),
                "vsa_swing_score": round(vsa_score, 2),
                "big_picture_score": round(big_picture_score, 2),
                "price_structure": price_structure,
                "money_flow_state": money_flow_state,
            },
        }

    def calculate_css(
        self,
        factor_scores: pd.DataFrame,
        regime: Any = MarketRegime.SIDEWAYS,
        custom_weights: Optional[Dict[str, float]] = None
    ) -> pd.DataFrame:
        """Tính CSS dựa trên trọng số regime hoặc custom weights."""
        if custom_weights:
            weights = custom_weights
        else:
            reg_str = str(regime).upper()
            if "BULL" in reg_str:
                regime_key = MarketRegime.BULL_TRENDING
            elif "BEAR" in reg_str:
                regime_key = MarketRegime.BEAR_TRENDING
            else:
                regime_key = MarketRegime.SIDEWAYS
            weights = self.regime_weights.get(regime_key, self.regime_weights.get(MarketRegime.SIDEWAYS, self.regime_weights["DEFAULT"]))

        # Agent-10 may return unnormalised weights. Keep CSS on a stable 0-100
        # scale regardless of the policy source.
        total_weight = sum(float(value) for value in weights.values())
        if total_weight <= 0:
            weights = self.regime_weights["DEFAULT"]
        elif abs(total_weight - 1.0) > 1e-9:
            weights = {key: float(value) / total_weight for key, value in weights.items()}
        
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
        
        # CSS chỉ phản ánh sáu factor đã được định lượng.
        factor_scores['css'] = factor_scores['base_css']
        
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
        """Gatekeeper Rule: audited financial statements are required."""
        audit = row.get('audit_opinion', 'UNQUALIFIED')
        if audit != 'UNQUALIFIED':
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
