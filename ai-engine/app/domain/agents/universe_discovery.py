"""AGENT-02: Universe Discovery Agent (IOS v5.1)

Chức năng:
- Quét toàn bộ Universe cổ phiếu trên sàn HOSE định kỳ hàng ngày và hàng quý.
- Phân nhóm Universe (Group A / Group B / Group C / Sandbox / Excluded) theo vốn hóa, thanh khoản và niêm yết.
- Chạy bộ lọc Lớp 0 (Hard Law): Beneish M-Score 8 biến phát hiện gian lận BCTC (ngưỡng loại M-Score > -1.78).
- Tích hợp phân hệ Graph Intelligence Layer (GIL) lọc bỏ rủi ro sở hữu chéo/rút ruột vốn.
- Bảng nghiệp vụ quản lý: universe_securities, beneish_results
- Bảng log audit: log_universe_discovery
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.universe_manager import UniverseManager, UniverseGroup, TradingStatus
from app.domain.rules.beneish import BeneishMScoreEngine
from app.adapters.sag_connector import sag_connector

logger = logging.getLogger(__name__)


class UniverseDiscoveryAgent(BaseAgent):
    """
    AGENT-02: Chuyên viên Khám phá & Sàng lọc Universe.
    Đảm bảo 100% cổ phiếu chuyển giao cho Research Agent đều vượt qua các Hard Filters bất biến.
    """

    def __init__(self):
        super().__init__(
            agent_name="universe_discovery",
            state_tables=["universe_securities", "beneish_results"],
            log_table="log_universe_discovery",
            enabled=True,
        )
        self.universe_manager = UniverseManager()
        self.beneish_engine = BeneishMScoreEngine()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Quét và phân loại Universe:
        - event_data:
            - tickers: Optional[List[str]] (danh sách mã cần quét, nếu không truyền sẽ lấy toàn bộ sàn HOSE)
            - target_date: date (ngày chốt BCTC/thanh khoản)
            - strategy_mode: str ("Quant" / "Fundamental")
            - session_context: str ("Normal" / "Stress" / "Crisis") từ Agent-01
            - current_regime: str ("BULL_MARKET" / "BEAR_MARKET" / "RANGE_BOUND") từ Agent-01
            - halted_tickers: List[str] (mã bị tạm ngừng giao dịch realtime) từ Agent-01
        """
        target_date: date = event_data.get("target_date", date.today())
        tickers: Optional[List[str]] = event_data.get("tickers")
        session_context: str = str(event_data.get("session_context", "Normal"))
        current_regime: str = str(event_data.get("current_regime", "BULL_MARKET"))
        halted_tickers_raw = event_data.get("halted_tickers", [])
        halted_tickers: set[str] = {str(t).upper().strip() for t in halted_tickers_raw if t}

        # 1. Nếu không truyền tickers, tự động lấy danh sách cổ phiếu HOSE từ CSDL
        if not tickers:
            from app.domain.repositories.universe_repository import UniverseRepository
            u_repo = UniverseRepository()
            stocks = u_repo.get_all_stocks(exchange="HOSE")
            tickers = [s["symbol"] for s in stocks if s.get("symbol")]

        if not tickers:
            logger.warning("[UniverseDiscoveryAgent] Không tìm thấy mã cổ phiếu nào để quét.")
            return {
                "data": {
                    "target_date": str(target_date),
                    "scanned_count": 0,
                    "eligible_count": 0,
                    "excluded_count": 0,
                    "discovery_list": [],
                    "exclusion_log": [],
                },
                "trace": {"reason": "EMPTY_UNIVERSE"},
            }

        # 2. Hard Law: Chế độ Khủng hoảng (Crisis Discovery Freeze)
        # Nếu Agent-01 báo thị trường đang sập / hoảng loạn, đóng van tìm kiếm mua mới lập tức
        if session_context == "Crisis":
            logger.warning("[UniverseDiscoveryAgent] Thị trường trong trạng thái CRISIS. Kích hoạt Discovery Freeze!")
            return {
                "data": {
                    "target_date": str(target_date),
                    "scanned_count": len(tickers),
                    "eligible_count": 0,
                    "excluded_count": len(tickers),
                    "discovery_list": [],
                    "exclusion_log": [{
                        "ticker": "ALL_MARKET",
                        "reason": "MARKET_CRISIS_DISCOVERY_FREEZE",
                        "detail": "Bối cảnh thị trường sụp đổ/hoảng loạn cực độ, đóng van đề xuất mua mới để bảo toàn vốn.",
                    }],
                },
                "trace": {
                    "session_context": session_context,
                    "current_regime": current_regime,
                    "action": "DISCOVERY_FREEZE",
                },
            }

        # 3. Lấy thông tin trading_status thực tế từ Database cho các mã
        db_trading_statuses: Dict[str, str] = {}
        try:
            from app.infrastructure.database.connection import get_raw_connection
            conn = get_raw_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, trading_status FROM stocks WHERE symbol = ANY(%s)",
                    (tickers,),
                )
                for row in cur.fetchall():
                    if row and row[0]:
                        db_trading_statuses[str(row[0]).upper().strip()] = str(row[1] or "NORMAL").upper().strip()
            conn.close()
        except Exception as e:
            logger.debug(f"[UniverseDiscoveryAgent] Không thể load trading_status từ DB: {e}")

        discovery_list: List[Dict[str, Any]] = []
        exclusion_log: List[Dict[str, Any]] = []
        beneish_summary: List[Dict[str, Any]] = []

        vn30_set = set(self.universe_manager._get_vn30_list())

        for ticker in tickers:
            symbol = str(ticker).upper().strip()
            if not symbol:
                continue

            # 4.1 Kiểm tra Trading Status Real-time (Websocket / Redis từ Agent-01)
            if symbol in halted_tickers:
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "TRADING_STATUS_HALTED_INTRADAY",
                    "detail": "Cổ phiếu bị tạm ngừng giao dịch hoặc đình chỉ trong phiên (Phát hiện từ Agent-01).",
                })
                continue

            # 4.2 Kiểm tra Trading Status Pháp lý từ Database
            db_status = db_trading_statuses.get(symbol, "NORMAL")
            if db_status not in ("NORMAL", ""):
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": f"TRADING_STATUS_{db_status}",
                    "detail": f"Trạng thái niêm yết vi phạm quy định Sở GDCK: {db_status}",
                })
                continue

            # 4.3 Kiểm tra cờ GIL (Graph Intelligence Layer) qua SAG FastMCP Adapter
            try:
                gil_data = await sag_connector.get_gil_relationships(symbol)
                gil_flag = gil_data.get("gil_flag", "PASS")
            except Exception as e:
                logger.warning(f"Không thể kết nối dịch vụ GIL cho {symbol}: {e}")
                gil_flag = "DATA_MISSING_GIL"

            if gil_flag == "CATASTROPHIC":
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "GIL_CATASTROPHIC_CROSS_HOLDING",
                    "detail": "Phát hiện chu trình sở hữu chéo hoặc rủi ro rút ruột vốn nghiêm trọng từ đồ thị GIL.",
                })
                continue

            # 4.4 Chạy bộ lọc Lớp 0: Beneish M-Score 8 biến
            beneish_overrides = event_data.get("beneish_overrides", {})
            if symbol in beneish_overrides:
                m_score = float(beneish_overrides[symbol])
                b_status = "FAIL" if m_score > -1.78 else "PASS"
                b_reason = "Evaluated via explicit simulation override"
            else:
                try:
                    beneish_res = self.beneish_engine.calculate_m_score(symbol, target_date)
                    m_score = beneish_res.get("m_score")
                    b_status = beneish_res.get("status", "PASS")
                    b_reason = beneish_res.get("reason", "")
                except Exception as e:
                    m_score = None
                    b_status = "DATA_MISSING"
                    b_reason = f"Lỗi hoặc thiếu BCTC: {e}"

            beneish_summary.append({
                "ticker": symbol,
                "m_score": m_score,
                "status": b_status,
                "reason": b_reason,
            })

            # Hard Law: Loại bỏ nếu M-Score > -1.78 hoặc Thiếu BCTC
            if b_status == "DATA_MISSING":
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "DATA_MISSING",
                    "m_score": None,
                    "detail": "Thiếu dữ liệu BCTC để tính Beneish M-Score.",
                })
                continue

            if b_status == "FAIL" or (m_score is not None and m_score > -1.78):
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "BENEISH_M_SCORE_MANIPULATION",
                    "m_score": m_score,
                    "detail": f"M-Score ({m_score}) vượt ngưỡng an toàn -1.78 (Dấu hiệu thao túng BCTC).",
                })
                continue

            # 4.5 Phân nhóm Universe động & Thích ứng Regime
            if symbol in vn30_set:
                u_group = UniverseGroup.A.value
            else:
                # Nếu thị trường Gấu (Bear Market), Hard Law cấm mở rộng sang các mã rủi ro/đầu cơ ngoài Group A
                if current_regime == "BEAR_MARKET":
                    exclusion_log.append({
                        "ticker": symbol,
                        "reason": "BEAR_REGIME_EXCLUSION",
                        "detail": "Chế độ Bear Market: Tự động loại bỏ cổ phiếu ngoài Group A để phòng thủ vốn.",
                    })
                    continue
                u_group = UniverseGroup.B.value

            discovery_list.append({
                "ticker": symbol,
                "universe_group": u_group,
                "trading_status": db_status if db_status else TradingStatus.NORMAL.value,
                "beneish_status": b_status,
                "m_score": m_score,
                "gil_flag": gil_flag,
                "provisional_conviction": "ELIGIBLE",
            })

        output_data = {
            "target_date": str(target_date),
            "scanned_count": len(tickers),
            "eligible_count": len(discovery_list),
            "excluded_count": len(exclusion_log),
            "discovery_list": discovery_list,
            "exclusion_log": exclusion_log,
        }

        trace = {
            "universe_manager": self.universe_manager.__class__.__name__,
            "beneish_engine": self.beneish_engine.__class__.__name__,
            "gil_source": "SAG Knowledge Graph (sag_connector)",
            "session_context": session_context,
            "current_regime": current_regime,
            "halted_tickers_count": len(halted_tickers),
            "beneish_details": beneish_summary,
        }

        return {"data": output_data, "trace": trace}
