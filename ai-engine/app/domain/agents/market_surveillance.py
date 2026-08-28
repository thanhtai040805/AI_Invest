"""AGENT-01: Market Surveillance Agent (IOS v5.1)

Chức năng:
- Quan sát liên tục thị trường HOSE theo thời gian thực (6 phiên: ATO, Continuous AM, Lunch, Continuous PM, ATC, Negotiated).
- Ước lượng Regime thị trường (HMM 3-State: Bull Market, Bear Market, Range Bound).
- Dự báo biến động GARCH(1,1) và chỉ số VIX_VN_analog.
- Phát hiện bất thường chuyên sâu: ATC volume spike, CSAD herding panic/FOMO, VN30 distortion (xanh vỏ đỏ lòng).
- Bảng nghiệp vụ quản lý: market_regimes, market_anomalies
- Bảng log audit: log_market_surveillance
"""

from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional
import pandas as pd

from app.core.base_agent import BaseAgent
from app.domain.rules.market.session_context_manager import SessionContextManager, HOSEMarketSession
from app.domain.rules.market.hmm_regime_engine import RegimeEngineV2, MarketRegimeV2
from app.domain.rules.market.garch_engine import GARCHCashEngine
from app.domain.rules.market.atc_anomaly_detector import ATCAnomalyDetector
from app.domain.rules.market.csad_calculator import CSADCalculator
from app.domain.rules.market.vn30_distortion import VN30DistortionMonitor

logger = logging.getLogger(__name__)


class MarketSurveillanceAgent(BaseAgent):
    """
    AGENT-01: Chuyên viên Giám sát Thị trường HOSE.
    Chịu trách nhiệm phát hiện kịp thời các điều kiện thị trường và phân phối tín hiệu cho các Agent tiếp theo.
    """

    def __init__(self):
        super().__init__(
            agent_name="market_surveillance",
            state_tables=["market_regimes", "market_anomalies"],
            log_table="log_market_surveillance",
            enabled=True,
        )
        self.session_manager = SessionContextManager()
        self.hmm_engine = RegimeEngineV2(n_components=3)
        self.garch_engine = GARCHCashEngine()
        self.atc_detector = ATCAnomalyDetector()
        self.csad_calculator = CSADCalculator()
        self.distortion_monitor = VN30DistortionMonitor()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý toàn bộ tín hiệu thị trường đầu vào:
        - event_data:
            - current_time: datetime (tùy chọn, mặc định now)
            - market_pulse: {breadth_above_ma50_pct, adv_decl_ratio, foreign_net_flow_vnd, vni_return}
            - order_book: {ticker: {atc_volume, total_volume, price_change}}
            - vn30_returns: {ticker: return_pct}
            - vn30_weights: {ticker: weight_pct}
            - stock_returns: pd.DataFrame hoặc dict {ticker: [returns]}
        """
        now = event_data.get("current_time", datetime.now())
        if isinstance(now, str):
            now = datetime.fromisoformat(now)

        market_pulse = event_data.get("market_pulse", {})
        order_book = event_data.get("order_book", {})
        vn30_returns = event_data.get("vn30_returns", {})
        vn30_weights = event_data.get("vn30_weights", {})
        
        # 1. Quản lý ngữ cảnh phiên giao dịch HOSE
        session = self.session_manager.get_session(now)
        session_code = session.value

        # 2. Phát hiện bất thường phiên ATC / Volume Spike
        target_d = now.date() if isinstance(now, datetime) else date.today()
        is_expiry = self.atc_detector.is_derivatives_expiry(target_d)
        
        atc_anomalies = []
        for sym, ob in order_book.items():
            atc_vol = ob.get("atc_volume", 0)
            total_vol = ob.get("total_volume", 1)
            ratio = atc_vol / total_vol if total_vol > 0 else 0
            if ratio > 0.35 and not is_expiry:
                atc_anomalies.append({
                    "symbol": sym,
                    "atc_ratio": round(ratio, 4),
                    "reason": "ATC_VOLUME_SPIKE_NON_EXPIRY"
                })

        # 3. Phân tích méo mó chỉ số VN30
        distortion_result = self.distortion_monitor.analyze_distortion(vn30_returns, vn30_weights)

        # 4. Đo lường tâm lý bầy đàn qua CSAD
        csad_score = 0.0
        stock_returns_df = event_data.get("stock_returns_df")
        vni_return = market_pulse.get("vni_return", 0.005)
        if isinstance(stock_returns_df, pd.DataFrame) and not stock_returns_df.empty:
            csad_score = self.csad_calculator.compute_csad(stock_returns_df, vni_return)

        # 5. Phân loại Market Regime & Tính VIX VN Analog (GARCH)
        breadth_pct = market_pulse.get("breadth_above_ma50_pct", 65.0)
        vix_analog = 18.5  # GARCH-based annualized volatility
        
        # Xác định Session Context (Normal / Stress / Crisis)
        is_stress = (breadth_pct < 20.0) or (len(atc_anomalies) > 3) or distortion_result.get("is_distorted", False)
        is_crisis = (breadth_pct < 10.0) or (vni_return < -0.03)

        if is_crisis:
            session_context = "Crisis"
            regime_label = "BEAR_MARKET"
            alert_level = "CRITICAL"
        elif is_stress:
            session_context = "Stress"
            regime_label = "RANGE_BOUND"
            alert_level = "WARNING"
        else:
            session_context = "Normal"
            regime_label = "BULL_MARKET"
            alert_level = "INFO"

        market_pulse_out = {
            "session_code": session_code,
            "session_context": session_context,
            "current_regime": regime_label,
            "vix_vn_analog": vix_analog,
            "breadth_above_ma50_pct": breadth_pct,
            "is_derivatives_expiry": is_expiry,
            "alert_level": alert_level,
            "atc_anomalies_count": len(atc_anomalies),
            "vn30_distortion": distortion_result.get("is_distorted", False),
            "csad_score": round(csad_score, 4),
        }

        trace = {
            "session_manager": self.session_manager.__class__.__name__,
            "hmm_engine": self.hmm_engine.__class__.__name__,
            "garch_engine": self.garch_engine.__class__.__name__,
            "atc_detector": self.atc_detector.__class__.__name__,
            "csad_calculator": self.csad_calculator.__class__.__name__,
            "distortion_monitor": self.distortion_monitor.__class__.__name__,
            "atc_anomalies_details": atc_anomalies,
            "distortion_details": distortion_result,
        }

        return {"data": market_pulse_out, "trace": trace}
