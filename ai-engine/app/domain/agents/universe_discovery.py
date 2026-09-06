"""AGENT-02: Universe Discovery Agent (IOS v5.1 Production Ready)

Chức năng:
- Quét toàn bộ Universe cổ phiếu trên sàn HOSE định kỳ hàng ngày và hàng quý.
- Áp dụng các Hard Filters bất biến:
  1. Trạng thái giao dịch (Trading Status: NORMAL, không bị Halt/Suspended).
  2. Báo cáo kiểm toán (Audit Opinion: UNQUALIFIED).
  3. Thanh khoản bắt buộc (ADTV20 >= 15 tỷ VND).
  4. Thời gian niêm yết (Listing Age >= 12 tháng cho chiến lược Quant).
  5. Rủi ro sở hữu chéo / rút ruột vốn Graph Intelligence Layer (GIL Flag != CATASTROPHIC).
  6. Bộ lọc Lớp 0 (Forensic Accounting): Beneish M-Score 8 biến (M-Score <= -1.78, miễn trừ tài chính).
- Phân nhóm Universe động (Group A / Group B / Group C / Sandbox / Excluded) theo vốn hóa, thanh khoản và regime.
- Bảng nghiệp vụ quản lý: universe_securities, beneish_results
- Bảng log audit: log_universe_discovery
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.universe_manager import UniverseManager, UniverseGroup, TradingStatus
from app.domain.rules.beneish import BeneishMScoreEngine
from app.domain.repositories.universe_repository import UniverseRepository
from app.adapters.sag_connector import sag_connector
from app.infrastructure.database.pg_pool import get_conn
from app.domain.rules.market.session_context_manager import SessionContextManager
from app.infrastructure.external_api.market_data_service import MarketDataService

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
        self.repo = UniverseRepository()
        self.session_manager = SessionContextManager()
        self.market_data_service = MarketDataService()

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
            - beneish_overrides: Optional[Dict[str, float]] (ghi đè mô phỏng nếu có)
            - refresh_gil: Optional[bool] (có truy vấn trực tiếp SAG realtime không)
        """
        target_date: date = event_data.get("target_date", date.today())
        tickers: Optional[List[str]] = event_data.get("tickers")
        strategy_mode: str = str(event_data.get("strategy_mode", "Quant"))
        session_context: str = str(event_data.get("session_context", "Normal"))
        current_regime: str = str(event_data.get("current_regime", "BULL_MARKET"))
        halted_tickers_raw = event_data.get("halted_tickers", [])
        halted_tickers: set[str] = {str(t).upper().strip() for t in halted_tickers_raw if t}
        beneish_overrides: Dict[str, float] = event_data.get("beneish_overrides", {})
        refresh_gil: bool = bool(event_data.get("refresh_gil", False))

        # Cầu nối Real-time DNSE: Quét trạng thái giao dịch trực tiếp nếu đang trong giờ giao dịch hoặc có yêu cầu realtime
        session = self.session_manager.get_session(datetime.now())
        if self.session_manager.is_order_matching_active(session) or event_data.get("is_realtime"):
            try:
                snap = await self.market_data_service.get_snapshot(exchange="HOSE")
                for s in snap.get("stocks", []):
                    st = str(s.get("tradingStatus") or s.get("status") or "").upper().strip()
                    if st in ("HALT", "HALTED", "SUSPENDED", "DELISTED"):
                        sym_h = str(s.get("symbol", "")).upper().strip()
                        if sym_h:
                            halted_tickers.add(sym_h)
            except Exception as e_live:
                logger.debug(f"[UniverseDiscoveryAgent] Không thể tải trạng thái realtime từ DNSE: {e_live}")

        # 1. Nếu không truyền tickers, tự động lấy danh sách cổ phiếu HOSE từ CSDL
        if not tickers:
            stocks = self.repo.get_all_stocks(exchange="HOSE")
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

        # 3. Nạp nhanh metadata toàn bộ Universe (O(1) Batch Query)
        stocks_metadata: Dict[str, Dict[str, Any]] = {}
        liquidity_map: Dict[str, Dict[str, Any]] = {}
        listing_map: Dict[str, Dict[str, Any]] = {}

        def _fetch_db_metadata():
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 3.1 Metadata từ bảng stocks
                    cur.execute(
                        """
                        SELECT symbol, trading_status, audit_opinion, gil_flag, market_cap,
                               COALESCE(industry, sector, '') as industry
                        FROM stocks
                        WHERE symbol = ANY(%s)
                        """,
                        (tickers,),
                    )
                    s_meta = {
                        str(r[0]).upper().strip(): {
                            "trading_status": str(r[1] or "NORMAL").upper().strip(),
                            "audit_opinion": str(r[2] or "UNQUALIFIED").upper().strip(),
                            "gil_flag": str(r[3] or "PASS").upper().strip(),
                            "market_cap": float(r[4] or 0.0),
                            "industry": str(r[5] or "").strip(),
                        }
                        for r in cur.fetchall()
                    }

                    # 3.2 Thanh khoản rolling 20 ngày gần nhất
                    cur.execute(
                        """
                        WITH recent_days AS (
                            SELECT DISTINCT date 
                            FROM market_data_daily 
                            WHERE date <= %s
                            ORDER BY date DESC 
                            LIMIT 20
                        )
                        SELECT ticker, 
                               AVG(close_adj * volume_total * 1000) as adtv20_vnd,
                               COUNT(*) as trade_days
                        FROM market_data_daily
                        WHERE date IN (SELECT date FROM recent_days) AND ticker = ANY(%s)
                        GROUP BY ticker
                        """,
                        (target_date, tickers),
                    )
                    l_meta = {
                        str(r[0]).upper().strip(): {
                            "adtv20": float(r[1] or 0.0),
                            "trade_days": int(r[2] or 0),
                        }
                        for r in cur.fetchall()
                    }

                    # 3.3 Ngày niêm yết lịch sử
                    cur.execute(
                        """
                        SELECT ticker, MIN(date), COUNT(*)
                        FROM market_data_daily
                        WHERE ticker = ANY(%s) AND date <= %s
                        GROUP BY ticker
                        """,
                        (tickers, target_date),
                    )
                    list_meta = {
                        str(r[0]).upper().strip(): {
                            "first_date": r[1],
                            "total_days": int(r[2] or 0),
                        }
                        for r in cur.fetchall()
                    }

                    return s_meta, l_meta, list_meta

        stocks_metadata, liquidity_map, listing_map = await asyncio.to_thread(_fetch_db_metadata)

        vn30_set = set(self.universe_manager._get_vn30_list())
        discovery_list: List[Dict[str, Any]] = []
        exclusion_log: List[Dict[str, Any]] = []
        beneish_summary: List[Dict[str, Any]] = []
        state_securities_to_save: List[Dict[str, Any]] = []

        for ticker in tickers:
            symbol = str(ticker).upper().strip()
            if not symbol:
                continue

            # 4.0 Kiểm tra tồn tại dữ liệu gốc (Data Availability)
            if symbol not in stocks_metadata:
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "DATA_MISSING",
                    "detail": f"Mã cổ phiếu {symbol} không tồn tại trong CSDL hoặc thiếu hoàn toàn dữ liệu BCTC.",
                })
                continue

            # 4.1 Kiểm tra Trading Status Real-time (Phát hiện từ Agent-01)
            if symbol in halted_tickers:
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "TRADING_STATUS_HALTED_INTRADAY",
                    "detail": "Cổ phiếu bị tạm ngừng giao dịch hoặc đình chỉ trong phiên (Phát hiện từ Agent-01).",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": "HALTED",
                    "beneish_status": "UNKNOWN",
                    "gil_flag": "UNKNOWN",
                })
                continue

            # 4.2 Kiểm tra Trading Status Pháp lý từ Database
            meta = stocks_metadata.get(symbol, {})
            db_status = meta.get("trading_status", "NORMAL")
            if db_status not in ("NORMAL", ""):
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": f"TRADING_STATUS_{db_status}",
                    "detail": f"Trạng thái niêm yết vi phạm quy định Sở GDCK: {db_status}",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": "UNKNOWN",
                    "gil_flag": "UNKNOWN",
                })
                continue

            # 4.3 Kiểm tra Báo cáo kiểm toán (Audit Opinion)
            audit_opinion = meta.get("audit_opinion", "UNQUALIFIED")
            if audit_opinion != "UNQUALIFIED":
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": f"AUDIT_OPINION_{audit_opinion}",
                    "detail": f"Ý kiến kiểm toán không đạt chuẩn UNQUALIFIED: {audit_opinion}",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": "UNKNOWN",
                    "gil_flag": "UNKNOWN",
                })
                continue

            # 4.4 Kiểm tra Thanh khoản Hard Filter (ADTV20 >= 15 tỷ VND)
            # Ngoại lệ: Nhóm VN30 luôn được giữ lại vì tính đại diện rổ chỉ số
            liq_data = liquidity_map.get(symbol, {})
            adtv20 = liq_data.get("adtv20", 0.0)
            if adtv20 < 15_000_000_000 and symbol not in vn30_set:
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "ADTV20_INSUFFICIENT",
                    "detail": f"Thanh khoản trung bình 20 phiên ({adtv20:,.0f} VND) < 15 tỷ VND.",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": "UNKNOWN",
                    "gil_flag": "UNKNOWN",
                })
                continue

            # 4.5 Kiểm tra Thời gian niêm yết (Listing Age >= 12 tháng cho chiến lược Quant)
            list_data = listing_map.get(symbol, {})
            first_trade = list_data.get("first_date")
            total_days = list_data.get("total_days", 0)
            listed_months = 0.0
            if first_trade and target_date:
                listed_months = (target_date - first_trade).days / 30.0
            elif total_days >= 250:
                listed_months = 12.0

            if strategy_mode == "Quant" and listed_months < 12.0 and symbol not in vn30_set:
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "QUANT_LISTING_AGE_SHORT",
                    "detail": f"Thời gian niêm yết ({listed_months:.1f} tháng) < 12 tháng quy định cho chiến lược Quant.",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": "UNKNOWN",
                    "gil_flag": "UNKNOWN",
                })
                continue

            # 4.6 Kiểm tra cờ GIL (Graph Intelligence Layer)
            gil_flag = meta.get("gil_flag", "PASS")
            if refresh_gil:
                try:
                    gil_data = await sag_connector.get_gil_relationships(symbol)
                    gil_flag = gil_data.get("gil_flag", gil_flag)
                except Exception as e:
                    logger.debug(f"Không thể refresh GIL từ SAG cho {symbol}: {e}")

            if gil_flag == "CATASTROPHIC":
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "GIL_CATASTROPHIC_CROSS_HOLDING",
                    "detail": "Phát hiện chu trình sở hữu chéo hoặc rủi ro rút ruột vốn nghiêm trọng từ đồ thị GIL.",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": "UNKNOWN",
                    "gil_flag": gil_flag,
                })
                continue

            # 4.7 Chạy bộ lọc Lớp 0: Beneish M-Score 8 biến
            is_exempt = False
            if symbol in beneish_overrides:
                m_score = float(beneish_overrides[symbol])
                b_status = "FAIL" if m_score > -1.78 else "PASS"
                b_reason = "Evaluated via explicit simulation override"
                is_exempt = False
            else:
                try:
                    beneish_res = await asyncio.to_thread(
                        self.beneish_engine.calculate_m_score, symbol, target_date
                    )
                    m_score = beneish_res.get("m_score")
                    b_status = beneish_res.get("status", "PASS")
                    b_reason = beneish_res.get("reason", "")
                    is_exempt = beneish_res.get("is_exempt", False)
                except Exception as e:
                    m_score = None
                    b_status = "DATA_MISSING"
                    b_reason = f"Lỗi hoặc thiếu BCTC: {e}"
                    is_exempt = False

            beneish_summary.append({
                "ticker": symbol,
                "m_score": m_score,
                "status": b_status,
                "is_exempt": is_exempt,
                "reason": b_reason,
            })

            # Hard Law: Loại bỏ nếu thiếu BCTC
            if b_status in ("DATA_MISSING", "PENDING") or (m_score is None and not is_exempt):
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "DATA_MISSING",
                    "m_score": None,
                    "detail": f"Thiếu dữ liệu BCTC để tính Beneish M-Score ({b_reason}).",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": b_status,
                    "gil_flag": gil_flag,
                })
                continue

            # Hard Law: Loại bỏ nếu M-Score > -1.78 (không áp dụng cho nhóm được miễn trừ)
            if b_status == "FAIL" or (m_score is not None and m_score > -1.78 and not is_exempt):
                exclusion_log.append({
                    "ticker": symbol,
                    "reason": "BENEISH_M_SCORE_MANIPULATION",
                    "m_score": m_score,
                    "detail": f"M-Score ({m_score}) vượt ngưỡng an toàn -1.78 (Dấu hiệu thao túng BCTC).",
                })
                state_securities_to_save.append({
                    "ticker": symbol,
                    "universe_group": UniverseGroup.EXCLUDED.value,
                    "trading_status": db_status,
                    "beneish_status": b_status,
                    "gil_flag": gil_flag,
                })
                continue

            # 4.8 Phân nhóm Universe động & Thích ứng Regime
            mcap = meta.get("market_cap", 0.0)
            if symbol in vn30_set or (adtv20 >= 50_000_000_000 and mcap >= 10_000_000_000_000):
                u_group = UniverseGroup.A.value
            else:
                # Nếu thị trường Gấu (Bear Market), Hard Law cấm mở rộng sang các mã rủi ro/đầu cơ ngoài Group A
                if current_regime == "BEAR_MARKET":
                    exclusion_log.append({
                        "ticker": symbol,
                        "reason": "BEAR_REGIME_EXCLUSION",
                        "detail": "Chế độ Bear Market: Tự động loại bỏ cổ phiếu ngoài Group A để phòng thủ vốn.",
                    })
                    state_securities_to_save.append({
                        "ticker": symbol,
                        "universe_group": UniverseGroup.EXCLUDED.value,
                        "trading_status": db_status,
                        "beneish_status": b_status,
                        "gil_flag": gil_flag,
                    })
                    continue

                if adtv20 < 20_000_000_000:
                    u_group = UniverseGroup.C.value
                else:
                    u_group = UniverseGroup.B.value

            discovery_list.append({
                "ticker": symbol,
                "universe_group": u_group,
                "trading_status": db_status if db_status else TradingStatus.NORMAL.value,
                "beneish_status": b_status,
                "m_score": m_score,
                "gil_flag": gil_flag,
                "adtv20": adtv20,
                "provisional_conviction": "ELIGIBLE",
            })
            state_securities_to_save.append({
                "ticker": symbol,
                "universe_group": u_group,
                "trading_status": db_status,
                "beneish_status": b_status,
                "gil_flag": gil_flag,
            })

        # 5. Lưu đồng bộ vào State Tables (universe_securities) và Master Table (stocks)
        def _persist_state_tables():
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for item in state_securities_to_save:
                        cur.execute(
                            """
                            INSERT INTO universe_securities (
                                ticker, universe_group, trading_status, beneish_status, gil_flag, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (ticker) DO UPDATE SET
                                universe_group = EXCLUDED.universe_group,
                                trading_status = EXCLUDED.trading_status,
                                beneish_status = EXCLUDED.beneish_status,
                                gil_flag = EXCLUDED.gil_flag,
                                updated_at = NOW();
                            UPDATE stocks
                            SET universe_group = %s, group_updated_at = NOW()
                            WHERE symbol = %s;
                            """,
                            (
                                item["ticker"][:16],
                                item["universe_group"][:16],
                                item["trading_status"][:16],
                                item["beneish_status"][:16],
                                item["gil_flag"][:16],
                                item["universe_group"][:16],
                                item["ticker"][:16],
                            ),
                        )

        try:
            await asyncio.to_thread(_persist_state_tables)
        except Exception as e:
            logger.warning(f"[UniverseDiscoveryAgent] Lỗi khi lưu state_securities vào CSDL: {e}")

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
            "gil_source": "Database gil_flag (Fast O(1) Local Lookup)",
            "session_context": session_context,
            "current_regime": current_regime,
            "halted_tickers_count": len(halted_tickers),
            "beneish_details": beneish_summary,
        }

        return {"data": output_data, "trace": trace}
