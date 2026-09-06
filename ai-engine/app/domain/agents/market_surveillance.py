"""AGENT-01: Market Surveillance Agent (IOS v5.1 Institutional Production Grade)

Chức năng:
- Quan sát liên tục thị trường HOSE theo thời gian thực (6 phiên: ATO, Continuous AM, Lunch, Continuous PM, ATC, Negotiated).
- Tự chủ nạp dữ liệu (Autonomous Data Hydration) trực tiếp từ CSDL Postgres (market_data_daily, technical_indicators).
- Ước lượng Regime thị trường (Sticky HMM 3-State: Bull Market, Bear Market, Range Bound).
- Dự báo biến động GJR-GARCH(1,1) và chỉ số VIX_VN_analog.
- Radar HOSE chuyên sâu cấp độ toàn thị trường (Market-level):
  + Sóng sàn bán tháo hàng loạt "Múa bên trăng" (Floor Lock <= -6.9%).
  + Hiện tượng méo mó chỉ số "Xanh vỏ đỏ lòng" (Index tăng nhưng Advance/Decline < 0.4).
  + Bất thường phiên ATC (ATC Volume Spike & Price Manipulation theo evaluate_atc_session).
  + Đo lường tâm lý bầy đàn qua CSAD Herding (Panic Selling vs Sector Rotation vs FOMO).
- Lưu trữ trạng thái nghiệp vụ: market_regimes, market_anomalies.
- Lưu vết kiểm toán: log_market_surveillance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from app.core.base_agent import BaseAgent
from app.domain.rules.market.session_context_manager import SessionContextManager, HOSEMarketSession
from app.domain.rules.market.hmm_regime_engine import RegimeEngineV2, MarketRegimeV2
from app.domain.rules.market.garch_engine import GARCHCashEngine
from app.domain.rules.market.atc_anomaly_detector import ATCAnomalyDetector
from app.domain.rules.market.csad_calculator import CSADCalculator
from app.domain.rules.market.vn30_distortion import VN30DistortionMonitor
from app.domain.rules.universe_manager import UniverseManager
from app.infrastructure.database.pg_pool import get_conn
from app.infrastructure.external_api.market_data_service import MarketDataService

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
        self.universe_manager = UniverseManager()
        self.market_data_service = MarketDataService()

    def _sync_hydrate_data(self, target_d: date) -> Dict[str, Any]:
        """Truy vấn dữ liệu thực tế từ Postgres để nạp đầy đủ tham số thị trường nếu thiếu."""
        hydrated: Dict[str, Any] = {}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 1. Xác định ngày có dữ liệu gần nhất <= target_d
                    cur.execute(
                        "SELECT MAX(date) FROM market_data_daily WHERE date <= %s",
                        (target_d,)
                    )
                    row_max = cur.fetchone()
                    eff_date = row_max[0] if (row_max and row_max[0]) else None
                    if not eff_date:
                        cur.execute("SELECT MAX(date) FROM market_data_daily")
                        row_fallback = cur.fetchone()
                        eff_date = row_fallback[0] if (row_fallback and row_fallback[0]) else target_d

                    hydrated["effective_date"] = eff_date

                    # 2. Độ rộng thị trường, Advance/Decline, Sóng Sàn (Floor Lock) & Sóng Trần (Ceiling)
                    cur.execute("""
                        SELECT 
                            COUNT(*) as total_stocks,
                            SUM(CASE WHEN close_adj > open_adj THEN 1 ELSE 0 END) as advancing,
                            SUM(CASE WHEN close_adj < open_adj THEN 1 ELSE 0 END) as declining,
                            SUM(CASE WHEN close_adj = open_adj THEN 1 ELSE 0 END) as unchanged,
                            SUM(CASE WHEN close_adj <= open_adj * 0.931 THEN 1 ELSE 0 END) as floor_count,
                            SUM(CASE WHEN close_adj >= open_adj * 1.069 THEN 1 ELSE 0 END) as ceiling_count,
                            COALESCE(SUM(foreign_net_vol), 0) as foreign_net_vol,
                            COALESCE(SUM(volume_total), 0) as total_market_volume
                        FROM market_data_daily 
                        WHERE date = %s AND ticker != 'VNINDEX'
                    """, (eff_date,))
                    row_breadth = cur.fetchone()
                    if row_breadth and row_breadth[0]:
                        total_s = int(row_breadth[0])
                        adv = int(row_breadth[1] or 0)
                        dec = int(row_breadth[2] or 0)
                        unc = int(row_breadth[3] or 0)
                        floor_cnt = int(row_breadth[4] or 0)
                        ceil_cnt = int(row_breadth[5] or 0)
                        foreign_net = float(row_breadth[6] or 0)
                        total_vol = float(row_breadth[7] or 0)
                        adv_decl = adv / max(dec, 1)
                    else:
                        total_s, adv, dec, unc, floor_cnt, ceil_cnt = 0, 0, 0, 0, 0, 0
                        foreign_net, total_vol, adv_decl = 0.0, 0.0, 1.0
                        hydrated["market_breadth_degraded"] = True
                        logger.warning("[MarketSurveillance] Thiếu dữ liệu độ rộng thị trường, đánh dấu trạng thái DEGRADED.")

                    hydrated["market_breadth_stats"] = {
                        "total_stocks": total_s,
                        "advancing": adv,
                        "declining": dec,
                        "unchanged": unc,
                        "floor_count": floor_cnt,
                        "ceiling_count": ceil_cnt,
                        "adv_decl_ratio": round(adv_decl, 3),
                        "foreign_net_vol": foreign_net,
                        "total_market_volume": total_vol,
                    }

                    # 3. % Cổ phiếu nằm trên MA50 (tính trực tiếp từ market_data_daily bằng Window Function)
                    start_ma50 = eff_date - timedelta(days=120)
                    cur.execute("""
                        WITH windowed AS (
                            SELECT ticker, date, close_adj,
                                   AVG(close_adj) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                                   COUNT(close_adj) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as cnt
                            FROM market_data_daily 
                            WHERE date <= %s AND date >= %s AND ticker != 'VNINDEX'
                        )
                        SELECT 
                            COUNT(*) as total,
                            SUM(CASE WHEN close_adj > ma50 THEN 1 ELSE 0 END) as above_ma50
                        FROM windowed
                        WHERE date = %s AND cnt >= 30
                    """, (eff_date, start_ma50, eff_date))
                    row_ti = cur.fetchone()
                    if row_ti and row_ti[0] and row_ti[0] > 0:
                        breadth_ma50_pct = float(row_ti[1] or 0) / float(row_ti[0]) * 100.0
                        hydrated["breadth_valid"] = True
                    elif total_s > 0:
                        breadth_ma50_pct = (adv / total_s) * 100.0
                        hydrated["breadth_valid"] = True
                    else:
                        breadth_ma50_pct = 50.0
                        hydrated["breadth_valid"] = False
                    hydrated["breadth_above_ma50_pct"] = round(breadth_ma50_pct, 2)

                    # 4. Lấy chuỗi lịch sử VNINDEX kèm vol_ma20, macro và foreign flow cho HMM & GARCH
                    cur.execute("""
                        WITH vni AS (
                            SELECT date, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_total as volume,
                                   AVG(volume_total) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol_ma20
                            FROM market_data_daily 
                            WHERE ticker = 'VNINDEX' AND date <= %s
                        ),
                        macro AS (
                            SELECT indicator_date as date, value as vninbr_interbank_rate
                            FROM macro_indicators
                            WHERE indicator_name = 'vninbr_interbank_rate'
                        ),
                        ff AS (
                            SELECT trade_date as date, sum(net_value) as net_foreign_value
                            FROM foreign_flow
                            GROUP BY trade_date
                        )
                        SELECT vni.date, vni.open, vni.high, vni.low, vni.close, vni.volume, vni.vol_ma20,
                               macro.vninbr_interbank_rate,
                               COALESCE(ff.net_foreign_value, 0) as net_foreign_value
                        FROM vni
                        LEFT JOIN macro ON vni.date = macro.date
                        LEFT JOIN ff ON vni.date = ff.date
                        ORDER BY vni.date ASC
                    """, (eff_date,))
                    vni_rows = cur.fetchall()
                    if vni_rows:
                        df_vni = pd.DataFrame(vni_rows, columns=[
                            "date", "open", "high", "low", "close", "volume", "vol_ma20",
                            "vninbr_interbank_rate", "net_foreign_value"
                        ])
                        df_vni["date"] = pd.to_datetime(df_vni["date"])
                        df_vni.set_index("date", inplace=True)
                        for col in ["open", "high", "low", "close", "volume", "vol_ma20", "vninbr_interbank_rate", "net_foreign_value"]:
                            if col in df_vni.columns:
                                df_vni[col] = pd.to_numeric(df_vni[col], errors="coerce")
                        hydrated["df_vni"] = df_vni
                        # VNI return ngày hiện tại
                        last_c = float(df_vni["close"].iloc[-1])
                        prev_c = float(df_vni["close"].iloc[-2]) if len(df_vni) >= 2 else last_c
                        vni_ret = (last_c - prev_c) / prev_c if prev_c > 0 else 0.0
                        hydrated["vni_return"] = vni_ret
                        hydrated["index_returns"] = df_vni["close"].pct_change().dropna()
                    else:
                        hydrated["df_vni"] = None
                        hydrated["vni_return"] = 0.0
                        hydrated["index_returns"] = pd.Series()

                    # 5. Dữ liệu ATC của các mã giao dịch mạnh
                    cur.execute("""
                        SELECT ticker, volume_continuous, volume_atc, volume_total, close_adj, open_adj, vwap, is_etf_rebalance_day
                        FROM market_data_daily
                        WHERE date = %s AND volume_total > 50000
                        ORDER BY volume_atc DESC LIMIT 50
                    """, (eff_date,))
                    atc_rows = cur.fetchall()
                    order_book_sim = {}
                    for r in atc_rows:
                        sym = r[0]
                        vol_cont = float(r[1] or 0)
                        vol_atc = float(r[2] or 0)
                        vol_tot = float(r[3] or (vol_cont + vol_atc))
                        c_p = float(r[4] or 0)
                        o_p = float(r[5] or c_p)
                        ret = (c_p - o_p) / o_p if o_p > 0 else 0.0
                        order_book_sim[sym] = {
                            "atc_volume": vol_atc,
                            "continuous_volume": vol_cont,
                            "total_volume": vol_tot,
                            "price_change": ret,
                            "is_etf_rebalance": bool(r[7]),
                        }
                    hydrated["order_book"] = order_book_sim

                    # 6. Dữ liệu rổ VN30 cho Distortion Monitor
                    vn30_list = self.universe_manager._get_vn30_list()
                    cur.execute("""
                        SELECT ticker, close_adj, open_adj, volume_total, market_cap
                        FROM market_data_daily
                        WHERE date = %s AND ticker = ANY(%s)
                    """, (eff_date, vn30_list))
                    vn30_rows = cur.fetchall()
                    vn30_returns = {}
                    vn30_weights = {}
                    caps = {}
                    for r in vn30_rows:
                        sym, c_p, o_p, vol, mcap = r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), r[4]
                        ret = (c_p - o_p) / o_p if o_p > 0 else 0.0
                        vn30_returns[sym] = ret
                        caps[sym] = float(mcap) if mcap else (c_p * (vol or 1000000))
                    tot_cap = sum(caps.values()) or 1.0
                    for sym, cap_v in caps.items():
                        vn30_weights[sym] = cap_v / tot_cap
                    hydrated["vn30_returns"] = vn30_returns
                    hydrated["vn30_weights"] = vn30_weights

                    # 7. Lấy chuỗi 60 phiên của top cổ phiếu thanh khoản để tính CSAD
                    start_csad = eff_date - timedelta(days=100)
                    cur.execute("""
                        SELECT ticker, date, close_adj
                        FROM market_data_daily
                        WHERE date >= %s AND date <= %s AND ticker IN (
                            SELECT ticker FROM market_data_daily 
                            WHERE date = %s AND volume_total * close_adj >= 15000000
                        )
                        ORDER BY date ASC
                    """, (start_csad, eff_date, eff_date))
                    csad_raw_rows = cur.fetchall()
                    if csad_raw_rows:
                        df_raw = pd.DataFrame(csad_raw_rows, columns=["ticker", "date", "close_adj"])
                        pivoted = df_raw.pivot(index="date", columns="ticker", values="close_adj").pct_change().dropna()
                        hydrated["stock_returns_df"] = pivoted
                    else:
                        hydrated["stock_returns_df"] = None

        except Exception as e:
            logger.warning(f"Lỗi nạp dữ liệu thị trường từ Postgres: {e}")
        
        return hydrated

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý toàn bộ tín hiệu thị trường đầu vào và phân phối bối cảnh.
        """
        raw_time = event_data.get("current_time") or event_data.get("date") or datetime.now()
        if isinstance(raw_time, str):
            try:
                now = datetime.fromisoformat(raw_time)
            except Exception:
                now = datetime.combine(date.fromisoformat(raw_time), datetime.min.time().replace(hour=14, minute=45))
        elif isinstance(raw_time, date) and not isinstance(raw_time, datetime):
            now = datetime.combine(raw_time, datetime.min.time().replace(hour=14, minute=45))
        else:
            now = raw_time

        target_d = now.date()

        # 1. Quản lý ngữ cảnh phiên giao dịch HOSE
        session = self.session_manager.get_session(now)
        session_code = session.value

        is_intraday_active = self.session_manager.is_order_matching_active(session)
        is_realtime_requested = bool(event_data.get("is_realtime", False))
        data_source_mode = "POSTGRESQL_EOD"

        # 1b. Cầu nối Dữ liệu Thời gian Thực qua DNSE API (chạy khi trong giờ giao dịch hoặc có yêu cầu realtime)
        dnse_live_data = None
        if is_intraday_active or is_realtime_requested:
            try:
                live_breadth = await self.market_data_service.get_breadth()
                live_indices = await self.market_data_service.get_indices()
                live_snap = await self.market_data_service.get_snapshot(exchange="HOSE")
                stocks = live_snap.get("stocks", [])
                if stocks and len(stocks) > 0:
                    dnse_live_data = {
                        "stocks": stocks,
                        "breadth": live_breadth,
                        "indices": live_indices,
                    }
                    data_source_mode = "DNSE_REALTIME_STREAM"
            except Exception as e_dnse:
                logger.debug(f"Không thể kết nối DNSE Real-time API, fallback sang CSDL: {e_dnse}")

        # 2. Tự chủ nạp dữ liệu từ Database nếu chưa có
        has_full_inputs = (
            "market_pulse" in event_data
            and "df_vni" in event_data
            and "vn30_returns" in event_data
        )

        if not has_full_inputs:
            hydrated = await asyncio.to_thread(self._sync_hydrate_data, target_d)
        else:
            hydrated = {}

        market_pulse = event_data.get("market_pulse") or {}
        order_book = event_data.get("order_book") or hydrated.get("order_book", {})
        vn30_returns = event_data.get("vn30_returns") or hydrated.get("vn30_returns", {})
        vn30_weights = event_data.get("vn30_weights") or hydrated.get("vn30_weights", {})
        stock_returns_df = event_data.get("stock_returns_df") or hydrated.get("stock_returns_df")
        df_vni = event_data.get("df_vni") if "df_vni" in event_data else hydrated.get("df_vni")
        index_returns = event_data.get("index_returns") if "index_returns" in event_data else hydrated.get("index_returns")

        # Thống kê độ rộng & các chỉ báo radar
        breadth_stats = hydrated.get("market_breadth_stats", {})
        vni_return = event_data.get("vni_return", market_pulse.get("vni_return", hydrated.get("vni_return", 0.0)))
        breadth_pct = market_pulse.get(
            "breadth_above_ma50_pct",
            hydrated.get("breadth_above_ma50_pct", 55.0)
        )
        adv_decl_ratio = breadth_stats.get("adv_decl_ratio", 1.0)
        floor_locked_count = breadth_stats.get("floor_count", 0)
        ceiling_count = breadth_stats.get("ceiling_count", 0)

        # Nếu có dữ liệu trực tiếp từ DNSE, cập nhật ngay lập tức thay vì chờ DB EOD
        if dnse_live_data:
            stocks = dnse_live_data["stocks"]
            adv = sum(1 for s in stocks if float(s.get("changePercent", 0.0) or 0.0) > 0)
            dec = sum(1 for s in stocks if float(s.get("changePercent", 0.0) or 0.0) < 0)
            unc = len(stocks) - adv - dec
            floor_cnt = sum(1 for s in stocks if float(s.get("changePercent", 0.0) or 0.0) <= -6.9 or (float(s.get("matchPrice", 0.0) or 0.0) <= float(s.get("floorPrice", 0.0) or 0.0) and float(s.get("floorPrice", 0.0) or 0.0) > 0))
            ceil_cnt = sum(1 for s in stocks if float(s.get("changePercent", 0.0) or 0.0) >= 6.9 or (float(s.get("matchPrice", 0.0) or 0.0) >= float(s.get("ceilingPrice", 0.0) or 0.0) and float(s.get("ceilingPrice", 0.0) or 0.0) > 0))
            tot_vol = sum(float(s.get("totalVolume", 0.0) or 0.0) for s in stocks)
            adv_decl = adv / max(dec, 1)
            breadth_stats = {
                "total_stocks": len(stocks),
                "advancing": adv,
                "declining": dec,
                "unchanged": unc,
                "floor_count": floor_cnt,
                "ceiling_count": ceil_cnt,
                "adv_decl_ratio": round(adv_decl, 3),
                "foreign_net_vol": 0.0,
                "total_market_volume": tot_vol,
                "source": "DNSE_REALTIME_STREAM",
            }
            floor_locked_count = floor_cnt
            ceiling_count = ceil_cnt
            adv_decl_ratio = round(adv_decl, 3)

            for idx in dnse_live_data["indices"].get("indices", []):
                sym_i = str(idx.get("symbol") or idx.get("indexId") or "").upper()
                if "VNINDEX" in sym_i or "VN-INDEX" in sym_i:
                    chg_pct = float(idx.get("changePercent", 0.0) or 0.0)
                    vni_return = chg_pct / 100.0 if abs(chg_pct) > 0.5 else chg_pct
                    break

        # 3. Phát hiện cổ phiếu bị Tạm ngừng / Đình chỉ giao dịch (Halt / Suspended)
        halted_tickers: List[str] = list(event_data.get("halted_tickers", []))
        for sym, ob in order_book.items():
            sym_clean = str(sym).upper().strip()
            ob_status = str(ob.get("status", "")).upper()
            if ob_status in ("HALT", "HALTED", "SUSPENDED"):
                if sym_clean not in halted_tickers:
                    halted_tickers.append(sym_clean)

        # Bổ sung cổ phiếu halt từ DNSE snapshot nếu có
        if dnse_live_data:
            for s in dnse_live_data["stocks"]:
                st = str(s.get("tradingStatus") or s.get("status") or "").upper().strip()
                if st in ("HALT", "HALTED", "SUSPENDED", "DELISTED"):
                    sym_c = str(s.get("symbol", "")).upper().strip()
                    if sym_c and sym_c not in halted_tickers:
                        halted_tickers.append(sym_c)

        if event_data.get("check_db_status", True):
            try:
                def _fetch_db_halted():
                    halted_db = []
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT symbol, trading_status FROM stocks WHERE trading_status IS NOT NULL"
                            )
                            for r in cur.fetchall():
                                if r and r[0]:
                                    st = str(r[1] or "").upper().strip()
                                    # Chú ý thực chiến HOSE: CONTROLLED vẫn được trade phiên chiều, chỉ SUSPENDED và HALTED là ngưng
                                    if st in ("HALT", "HALTED", "SUSPENDED", "DELISTED"):
                                        halted_db.append(str(r[0]).upper().strip())
                    return halted_db

                db_halts = await asyncio.to_thread(_fetch_db_halted)
                for s_db in db_halts:
                    if s_db not in halted_tickers:
                        halted_tickers.append(s_db)
            except Exception as e:
                logger.debug(f"Không thể query trading_status từ DB: {e}")

        # 4. HOSE Radar: Đánh giá bất thường phiên ATC qua ATCAnomalyDetector chuẩn
        is_expiry = self.atc_detector.is_derivatives_expiry(target_d)
        atc_anomalies: List[Dict[str, Any]] = []

        for sym, ob in order_book.items():
            atc_vol = ob.get("atc_volume", 0)
            cont_vol = ob.get("continuous_volume") or ob.get("total_volume", 1)
            p_change = ob.get("price_change", 0.0)
            is_etf = ob.get("is_etf_rebalance", False)

            eval_res = self.atc_detector.evaluate_atc_session(
                target_date=target_d,
                atc_volume=atc_vol,
                continuous_avg_volume=cont_vol,
                atc_price_change_pct=p_change,
                is_etf_rebalance=is_etf,
            )

            if eval_res["status"] in ("WARNING", "CRITICAL"):
                atc_anomalies.append({
                    "symbol": sym,
                    "status": eval_res["status"],
                    "volume_ratio": eval_res.get("volume_ratio"),
                    "reason": eval_res.get("reason"),
                })

        # 5. HOSE Radar: Phân tích méo mó chỉ số VN30 & Nhận diện "Xanh vỏ đỏ lòng"
        distortion_result = self.distortion_monitor.analyze_distortion(
            stock_returns=vn30_returns,
            stock_weights=vn30_weights,
            market_adv_decl_ratio=adv_decl_ratio,
            vni_return=vni_return,
        )

        # 6. HOSE Radar: Đo lường tâm lý bầy đàn qua CSAD Herding
        csad_analysis = {}
        csad_score = 0.0
        herding_status = "NORMAL"

        if isinstance(stock_returns_df, pd.DataFrame) and not stock_returns_df.empty:
            if isinstance(index_returns, pd.Series) and not index_returns.empty:
                common_dates = stock_returns_df.index.intersection(index_returns.index)
                if len(common_dates) >= 20:
                    csad_analysis = self.csad_calculator.analyze_herding(
                        daily_returns_df=stock_returns_df.loc[common_dates],
                        market_returns_series=index_returns.loc[common_dates],
                        window=min(60, len(common_dates)),
                    )
                    csad_score = csad_analysis.get("csad", 0.0)
                    herding_status = csad_analysis.get("herding_status", "NORMAL")
                else:
                    csad_score = self.csad_calculator.compute_csad(stock_returns_df, vni_return)
            else:
                csad_score = self.csad_calculator.compute_csad(stock_returns_df, vni_return)

        # 7. Phân loại Market Regime & Tính VIX VN Analog (GARCH & Sticky HMM)
        # 7.1 Dự báo biến động VIX VN Analog bằng GJR-GARCH(1,1)
        if index_returns is None or (isinstance(index_returns, pd.Series) and index_returns.empty):
            try:
                index_returns = self.garch_engine.get_index_returns(target_d)
            except Exception as e:
                logger.warning(f"Failed to fetch index returns for GARCH: {e}")
                index_returns = pd.Series()

        if isinstance(index_returns, pd.Series) and len(index_returns) >= 20:
            ann_vol = self.garch_engine.forecast_volatility(index_returns)
            vix_analog = round(float(ann_vol * 100.0), 2)
            garch_cash_target = round(float(self.garch_engine.calculate_cash_allocation(ann_vol) * 100.0), 2)
        else:
            vix_analog = 18.5
            garch_cash_target = 15.0

        # 7.2 Phân loại Regime bằng Sticky HMM (RegimeEngineV2)
        hmm_probs = {}
        if isinstance(df_vni, pd.DataFrame) and not df_vni.empty and len(df_vni) >= 30:
            try:
                hmm_probs = self.hmm_engine.infer_daily(df_vni)
                regime_label = max(hmm_probs.items(), key=lambda x: x[1])[0]
            except Exception as e:
                logger.warning(f"HMM infer_daily failed: {e}")
                regime_label = "BULL_MARKET"
        else:
            regime_label = event_data.get("regime_override", "BULL_MARKET")

        # 8. Phán quyết HOSE Session Context & Mức độ Cảnh báo (Alert Level)
        # Radar cấp độ thị trường HOSE:
        # - Crisis: Sóng bán sàn múa bên trăng (floor >= 20) | Breadth sụp đổ (<10% khi có dữ liệu) | Sập chỉ số (<-3%) | Hoảng loạn bầy đàn
        is_breadth_collapse = (breadth_pct < 10.0) if hydrated.get("breadth_valid", True) else False
        is_floor_wave_crisis = floor_locked_count >= 20
        is_herding_panic_crisis = (herding_status == "PANIC_SELLING_HERDING" and vni_return < -0.015)
        is_crisis = is_breadth_collapse or (vni_return < -0.03) or is_floor_wave_crisis or is_herding_panic_crisis

        # - Stress: Áp lực sàn xuất hiện (floor >= 8) | Xanh vỏ đỏ lòng | A/D ratio yếu (<0.4) | ATC bất thường | Breadth < 20%
        is_floor_wave_stress = floor_locked_count >= 8
        is_distortion_stress = distortion_result.get("is_distorted", False)
        is_breadth_stress = ((breadth_pct < 20.0) or (adv_decl_ratio < 0.40)) if hydrated.get("breadth_valid", True) else False
        is_atc_stress = len(atc_anomalies) >= 3
        is_stress = is_floor_wave_stress or is_distortion_stress or is_breadth_stress or is_atc_stress or (herding_status == "PANIC_SELLING_HERDING")

        anomalies_detected: List[Dict[str, str]] = []

        if is_crisis:
            session_context = "Crisis"
            regime_label = "BEAR_MARKET"
            alert_level = "CRITICAL"
        elif is_stress:
            session_context = "Stress"
            regime_label = "RANGE_BOUND" if regime_label == "BULL_MARKET" else regime_label
            alert_level = "WARNING"
        else:
            session_context = "Normal"
            alert_level = "INFO"

        # Ghi nhận các bất thường chi tiết để lưu trữ vào bảng market_anomalies
        if floor_locked_count >= 8:
            anomalies_detected.append({
                "type": "FLOOR_LOCK_WAVE",
                "severity": "CRITICAL" if floor_locked_count >= 20 else "WARNING",
                "description": f"Sóng bán sàn hàng loạt: {floor_locked_count} mã giảm sàn kịch biên độ (-7%).",
            })
        if distortion_result.get("is_distorted"):
            dist_type = distortion_result.get("distortion_type") or "VN30_DISTORTION"
            anomalies_detected.append({
                "type": dist_type,
                "severity": "WARNING",
                "description": distortion_result.get("reason", "VN30 Index Distortion"),
            })
        if herding_status == "PANIC_SELLING_HERDING":
            anomalies_detected.append({
                "type": "CSAD_PANIC_HERDING",
                "severity": "CRITICAL",
                "description": csad_analysis.get("reason", "Tâm lý bán tháo bầy đàn bao trùm toàn sàn."),
            })
        elif herding_status == "FOMO_EUPHORIA_HERDING":
            anomalies_detected.append({
                "type": "CSAD_FOMO_HERDING",
                "severity": "WARNING",
                "description": csad_analysis.get("reason", "Tâm lý mua đuổi hưng phấn bầy đàn cực độ."),
            })
        for atc_a in atc_anomalies:
            anomalies_detected.append({
                "type": "ATC_ANOMALY",
                "severity": atc_a["status"],
                "description": f"Mã {atc_a['symbol']}: {atc_a['reason']}",
            })

        market_pulse_out = {
            "session_code": session_code,
            "session_context": session_context,
            "current_regime": regime_label,
            "vix_vn_analog": vix_analog,
            "garch_cash_target_pct": garch_cash_target,
            "hmm_probabilities": hmm_probs,
            "breadth_above_ma50_pct": breadth_pct,
            "adv_decl_ratio": adv_decl_ratio,
            "floor_locked_count": floor_locked_count,
            "ceiling_count": ceiling_count,
            "is_derivatives_expiry": is_expiry,
            "alert_level": alert_level,
            "atc_anomalies_count": len(atc_anomalies),
            "vn30_distortion": distortion_result.get("is_distorted", False),
            "vn30_distortion_type": distortion_result.get("distortion_type"),
            "csad_score": round(csad_score, 4),
            "herding_status": herding_status,
            "halted_tickers": halted_tickers,
            "effective_date": str(hydrated.get("effective_date", target_d)),
        }

        # 9. Lưu trữ State Tables nghiệp vụ thực sự (market_regimes & market_anomalies)
        def _persist_state_tables():
            try:
                eff_d = hydrated.get("effective_date", target_d)
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        # 9.1 UPSERT vào market_regimes
                        cur.execute("""
                            INSERT INTO market_regimes (date, current_regime, vix_vn_analog, breadth_above_ma50_pct, hmm_posteriors, updated_at)
                            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (date) DO UPDATE SET
                                current_regime = EXCLUDED.current_regime,
                                vix_vn_analog = EXCLUDED.vix_vn_analog,
                                breadth_above_ma50_pct = EXCLUDED.breadth_above_ma50_pct,
                                hmm_posteriors = EXCLUDED.hmm_posteriors,
                                updated_at = CURRENT_TIMESTAMP
                        """, (
                            eff_d,
                            regime_label,
                            vix_analog,
                            breadth_pct,
                            json.dumps(hmm_probs),
                        ))

                        # 9.1b Đồng bộ sang bảng market_regime (singular) phục vụ MarketDataRepository & các downstream agent
                        cur.execute("""
                            INSERT INTO market_regime (
                                date, regime_label, breadth_ma50, breadth_ma200,
                                breadth_rsi_oversold, breadth_rsi_overbought,
                                market_volume_sma20_ratio, net_foreign_flow_bil, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (date) DO UPDATE SET
                                regime_label = EXCLUDED.regime_label,
                                breadth_ma50 = EXCLUDED.breadth_ma50,
                                breadth_ma200 = EXCLUDED.breadth_ma200,
                                market_volume_sma20_ratio = EXCLUDED.market_volume_sma20_ratio,
                                net_foreign_flow_bil = EXCLUDED.net_foreign_flow_bil
                        """, (
                            eff_d,
                            regime_label,
                            (breadth_pct / 100.0) if breadth_pct > 1.0 else breadth_pct,
                            0.50,
                            0.0,
                            0.0,
                            1.0,
                            0.0,
                        ))

                        # 9.2 INSERT vào market_anomalies nếu có phát hiện bất thường
                        for anom in anomalies_detected:
                            cur.execute("""
                                INSERT INTO market_anomalies (date, anomaly_type, severity, description, created_at)
                                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                            """, (
                                eff_d,
                                anom["type"],
                                anom["severity"],
                                anom["description"],
                            ))
                    conn.commit()
            except Exception as e_db:
                logger.warning(f"Lỗi ghi state_tables (market_regimes/market_anomalies): {e_db}")

        await asyncio.to_thread(_persist_state_tables)

        trace = {
            "session_manager": self.session_manager.__class__.__name__,
            "hmm_engine": self.hmm_engine.__class__.__name__,
            "garch_engine": self.garch_engine.__class__.__name__,
            "atc_detector": self.atc_detector.__class__.__name__,
            "csad_calculator": self.csad_calculator.__class__.__name__,
            "distortion_monitor": self.distortion_monitor.__class__.__name__,
            "anomalies_detected": anomalies_detected,
            "atc_anomalies_details": atc_anomalies,
            "distortion_details": distortion_result,
            "csad_details": csad_analysis,
            "data_source": data_source_mode,
        }

        return {"data": market_pulse_out, "trace": trace}
