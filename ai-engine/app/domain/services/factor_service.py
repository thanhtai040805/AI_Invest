"""Factor Service (IOS v5.1 Production Ready)

Tính toán động 6 nhóm nhân tố (F1 Value, F2 Quality, F3 Momentum, F4 Earnings, F5 Flow, F6 Technical)
từ dữ liệu BCTC và biến động thị trường thực tế trong CSDL PostgreSQL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from app.core.time_utils import today_vn
from app.infrastructure.database.pg_pool import get_conn
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class FactorService:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter()

    def compute_factors_for_ticker(
        self, ticker: str, target_date: Optional[date] = None
    ) -> Dict[str, float]:
        """Tính toán động 6 nhóm nhân tố (F1-F6) cho một mã cổ phiếu cụ thể."""
        sym = str(ticker).upper().strip()
        if target_date is None:
            target_date = today_vn()

        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Lấy tỷ số tài chính mới nhất tính đến target_date
                cur.execute(
                    """
                    SELECT pe, pb, ev_ebitda, roe, roa, debt_equity, current_ratio,
                           gross_margin, net_margin, yoy_revenue_growth, yoy_earnings_growth
                    FROM financial_ratios
                    WHERE symbol = %s AND frequency = 'quarterly' AND ratio_date <= %s
                      AND (
                        CASE
                            WHEN published_date IS NOT NULL
                                 AND (published_date - ratio_date) BETWEEN 10 AND 120
                                 AND published_date < '2026-09-01'
                            THEN published_date
                            ELSE ratio_date + (
                                CASE EXTRACT(QUARTER FROM ratio_date)
                                    WHEN 1 THEN 45
                                    WHEN 2 THEN 55
                                    WHEN 3 THEN 45
                                    ELSE 35
                                END
                            )::integer
                        END
                      ) <= %s
                    ORDER BY (
                        CASE
                            WHEN published_date IS NOT NULL
                                 AND (published_date - ratio_date) BETWEEN 10 AND 120
                                 AND published_date < '2026-09-01'
                            THEN published_date
                            ELSE ratio_date + (
                                CASE EXTRACT(QUARTER FROM ratio_date)
                                    WHEN 1 THEN 45
                                    WHEN 2 THEN 55
                                    WHEN 3 THEN 45
                                    ELSE 35
                                END
                            )::integer
                        END
                    ) DESC, ratio_date DESC
                    LIMIT 1
                    """,
                    (sym, target_date, target_date),
                )
                fin_row = cur.fetchone()

                # 2. Lấy 60 phiên giao dịch gần nhất từ market_data_daily
                cur.execute(
                    """
                    SELECT close, volume_total, foreign_net_vol, date
                    FROM market_data_daily_calculation
                    WHERE ticker = %s AND date <= %s
                    ORDER BY date DESC
                    LIMIT 60
                    """,
                    (sym, target_date),
                )
                mkt_rows = cur.fetchall()

        # Dữ liệu mặc định nếu thiếu
        pe = float(fin_row[0] or 15.0) if fin_row else 15.0
        pb = float(fin_row[1] or 2.0) if fin_row else 2.0
        ev_ebitda = float(fin_row[2] or 12.0) if fin_row else 12.0
        roe = float(fin_row[3] or 0.05) if fin_row else 0.05
        roa = float(fin_row[4] or 0.03) if fin_row else 0.03
        debt_equity = float(fin_row[5] or 0.8) if fin_row else 0.8
        net_margin = float(fin_row[8] or 0.10) if fin_row else 0.10
        yoy_rev = float(fin_row[9] or 0.0) if fin_row else 0.0
        yoy_earn = float(fin_row[10] or 0.0) if fin_row else 0.0

        p0 = float(mkt_rows[0][0]) if mkt_rows and mkt_rows[0][0] else 50.0
        p20 = float(mkt_rows[min(19, len(mkt_rows) - 1)][0]) if mkt_rows else p0
        p60 = float(mkt_rows[min(59, len(mkt_rows) - 1)][0]) if mkt_rows else p20

        vols = [float(r[1] or 0.0) for r in mkt_rows] if mkt_rows else [1000000.0]
        vol20 = sum(vols[:20]) / max(1, len(vols[:20]))
        vol60 = sum(vols[:60]) / max(1, len(vols[:60]))
        foreign_vol_20 = sum(float(r[2] or 0.0) for r in mkt_rows[:20]) if mkt_rows else 0.0

        # Tỷ suất sinh lời 1 tháng (20 phiên) và 3 tháng (60 phiên)
        ret_1m = (p0 - p20) / (p20 + 1e-6)
        ret_3m = (p0 - p60) / (p60 + 1e-6)

        # Trung bình động MA20 và MA50
        prices = [float(r[0] or 0.0) for r in mkt_rows] if mkt_rows else [p0]
        ma20 = sum(prices[:20]) / max(1, len(prices[:20]))
        ma50 = sum(prices[:50]) / max(1, len(prices[:50]))

        # --- 1. F1: Value (Thang 10 - 95, P/E và P/B hợp lý) ---
        # Điểm cao khi định giá rẻ (P/E < 15, P/B < 2.0)
        f1_value = max(10.0, min(95.0, 50.0 + (15.0 - pe) * 2.0 + (2.0 - pb) * 10.0))

        # --- 2. F2: Quality (Thang 15 - 95, Hiệu quả sinh lời và đòn bẩy an toàn) ---
        # Điểm cao khi ROE/ROA cao, biên ròng cao và nợ vay thấp
        f2_quality = max(
            15.0,
            min(95.0, 40.0 + (roe * 400.0) - (debt_equity - 0.8) * 15.0 + (net_margin * 100.0)),
        )

        # --- 3. F3: Momentum (Thang 10 - 95, Sức mạnh giá tương đối) ---
        # Điểm cao khi xu hướng tăng giá 1 tháng và 3 tháng tích cực
        f3_momentum = max(10.0, min(95.0, 50.0 + (ret_1m * 150.0) + (ret_3m * 100.0)))

        # --- 4. F4: Earnings (Thang 10 - 95, Tăng trưởng kết quả kinh doanh) ---
        # Điểm cao khi doanh thu và lợi nhuận tăng trưởng cùng kỳ
        f4_earnings = max(10.0, min(95.0, 50.0 + (yoy_rev * 50.0) + (yoy_earn * 50.0)))

        # --- 5. F5: Flow (Thang 15 - 95, Dòng tiền thông minh và khối ngoại) ---
        # Điểm cao khi thanh khoản bùng nổ so với 60 phiên hoặc khối ngoại gom ròng
        flow_trend = (vol20 / (vol60 + 1e-6)) - 1.0
        foreign_bonus = 5.0 if foreign_vol_20 > 0 else (-5.0 if foreign_vol_20 < 0 else 0.0)
        f5_flow = max(15.0, min(95.0, 50.0 + (flow_trend * 50.0) + foreign_bonus))

        # --- 6. F6: Technical (Thang 10 - 95, Vị thế giá so với các đường MA) ---
        # Điểm cao khi giá nằm trên MA20 và MA50
        tech_ma20 = (p0 / (ma20 + 1e-6)) - 1.0
        tech_ma50 = (p0 / (ma50 + 1e-6)) - 1.0
        f6_technical = max(10.0, min(95.0, 50.0 + (tech_ma20 * 200.0) + (tech_ma50 * 100.0)))

        raw_metrics = {
            "pe": pe,
            "pb": pb,
            "roe": roe,
            "de": debt_equity,
            "ret_1m": round(ret_1m, 4),
            "ret_3m": round(ret_3m, 4),
            "vol_20_ratio": round(vol20 / (vol60 + 1e-6), 2),
            "p_vs_ma20": round(tech_ma20, 4),
            "p_vs_ma50": round(tech_ma50, 4),
        }

        return {
            "f1_value": round(f1_value, 2),
            "f2_quality": round(f2_quality, 2),
            "f3_momentum": round(f3_momentum, 2),
            "f4_earnings": round(f4_earnings, 2),
            "f5_flow": round(f5_flow, 2),
            "f6_technical": round(f6_technical, 2),
            "raw_metrics": raw_metrics,
        }

    def compute_daily_factors(self, target_date: date):
        """Tính toán F1-F6 và lưu trữ vào bảng factor_scores cho toàn sàn."""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM stocks WHERE exchange = 'HOSE'")
                symbols = [r[0] for r in cur.fetchall()]

        from app.domain.repositories.intelligence_repository import IntelligenceRepository
        intel_repo = IntelligenceRepository()

        count = 0
        for sym in symbols:
            try:
                factors = self.compute_factors_for_ticker(sym, target_date)
                css = round(
                    0.2 * factors["f1_value"]
                    + 0.2 * factors["f2_quality"]
                    + 0.2 * factors["f3_momentum"]
                    + 0.2 * factors["f4_earnings"]
                    + 0.1 * factors["f5_flow"]
                    + 0.1 * factors["f6_technical"],
                    2,
                )
                conviction = "A" if css >= 70 else ("B" if css >= 60 else ("C" if css >= 50 else "D"))
                intel_repo.save_factor_score(
                    symbol=sym,
                    f1_value=factors["f1_value"],
                    f2_quality=factors["f2_quality"],
                    f3_momentum=factors["f3_momentum"],
                    f4_earnings=factors["f4_earnings"],
                    f5_flow=factors["f5_flow"],
                    f6_technical=factors["f6_technical"],
                    css=css,
                    conviction=conviction,
                    score_date=target_date,
                )
                count += 1
            except Exception as e:
                logger.debug(f"Không thể tính factors cho {sym}: {e}")

        logger.info(f"Hoàn thành tính toán Factor Scores cho {count}/{len(symbols)} mã vào {target_date}")
        return {"symbols_computed": count, "total_symbols": len(symbols), "date": str(target_date)}

    def compute_universe_leadership_metrics(
        self,
        tickers: List[str],
        as_of_date: Optional[date] = None,
        lookback_calendar_days: int = 120,
    ) -> Tuple[Dict[str, Dict[str, Any]], pd.DataFrame]:
        """Batch-compute point-in-time RS vs VNINDEX, Sector Leadership, Trend Template,
        VSA Swing/Bottom-Fishing signals, and Pairwise Return Correlation matrix from PostgreSQL."""
        clean_tickers = sorted({str(t).upper().strip() for t in tickers if t})
        if not clean_tickers:
            return {}, pd.DataFrame()

        ref_date = as_of_date or today_vn()
        start_date = ref_date - timedelta(days=lookback_calendar_days)
        query_tickers = list(set(clean_tickers) | {"VNINDEX"})

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT m.ticker, m.date, m.open, m.high, m.low, m.close,
                               COALESCE(m.volume_continuous, m.volume_total, 0) AS vol,
                               COALESCE(m.foreign_net_vol, 0) AS foreign_net_vol,
                               COALESCE(NULLIF(BTRIM(s.sector), ''), NULLIF(BTRIM(s.industry), ''), 'General') AS sector
                        FROM market_data_daily_calculation m
                        LEFT JOIN stocks s ON s.symbol = m.ticker
                        WHERE m.ticker = ANY(%s)
                          AND m.date BETWEEN %s AND %s
                          AND m.close IS NOT NULL AND m.close > 0
                        ORDER BY m.ticker, m.date ASC
                        """,
                        (query_tickers, start_date, ref_date),
                    )
                    rows = cur.fetchall()
        except Exception as exc:
            logger.warning("[FactorService] Batch leadership query failed as of %s: %s", ref_date, exc)
            return {}, pd.DataFrame()

        if not rows:
            return {}, pd.DataFrame()

        by_ticker: Dict[str, List[Tuple[Any, ...]]] = {}
        sector_by_ticker: Dict[str, str] = {}
        for row in rows:
            sym = str(row[0]).upper().strip()
            by_ticker.setdefault(sym, []).append(row)
            sector_by_ticker[sym] = str(row[8] or "General").strip()

        def _window_return(bars: List[Tuple[Any, ...]], steps: int) -> float:
            if len(bars) < 2:
                return 0.0
            p_now = float(bars[-1][5])
            p_prev = float(bars[max(0, len(bars) - 1 - steps)][5])
            return (p_now - p_prev) / p_prev if p_prev > 0 else 0.0

        vni_bars = by_ticker.get("VNINDEX", [])
        vni_ret_20d = _window_return(vni_bars, 20)
        vni_ret_60d = _window_return(vni_bars, 60)

        raw_metrics: Dict[str, Dict[str, Any]] = {}
        close_series_map: Dict[str, pd.Series] = {}

        for sym in clean_tickers:
            bars = by_ticker.get(sym, [])
            if len(bars) < 5:
                raw_metrics[sym] = {
                    "sector": sector_by_ticker.get(sym, "General"),
                    "ret_20d": 0.0,
                    "ret_60d": 0.0,
                    "rs_20d": 0.0,
                    "rs_60d": 0.0,
                    "rs_composite": 0.0,
                    "rs_score": 50.0,
                    "sector_rs_20d": 0.0,
                    "sector_leadership_score": 50.0,
                    "trend_template_score": 50.0,
                    "vsa_swing_score": 50.0,
                    "close_above_ma20": False,
                    "close_above_ma50": False,
                    "has_spring": False,
                    "has_no_supply": False,
                    "has_pocket_pivot": False,
                    "has_absorption": False,
                    "has_bearish_upthrust": False,
                    "absorption_vol_ratio": 1.0,
                    "up_vol_dominance": 0.5,
                    "rsi_14": 50.0,
                }
                continue

            dates = [b[1] for b in bars]
            opens = np.array([float(b[2] or b[5]) for b in bars], dtype=float)
            highs = np.array([float(b[3] or b[5]) for b in bars], dtype=float)
            lows = np.array([float(b[4] or b[5]) for b in bars], dtype=float)
            closes = np.array([float(b[5]) for b in bars], dtype=float)
            vols = np.array([float(b[6] or 0.0) for b in bars], dtype=float)
            foreign_nets = np.array([float(b[7] or 0.0) for b in bars], dtype=float)

            close_series_map[sym] = pd.Series(closes, index=dates)

            p0 = closes[-1]
            ret_20d = _window_return(bars, 20)
            ret_60d = _window_return(bars, 60)
            rs_20d = ret_20d - vni_ret_20d
            rs_60d = ret_60d - vni_ret_60d
            rs_composite = 0.60 * rs_20d + 0.40 * rs_60d

            ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else float(np.mean(closes))
            ma50 = float(np.mean(closes[-50:])) if len(closes) >= 50 else float(np.mean(closes))
            high_60d = float(np.max(highs[-60:])) if len(highs) >= 1 else p0
            dist_from_high = (p0 / high_60d) if high_60d > 0 else 0.0

            # Up-volume dominance over last 20 sessions
            n_win = min(20, len(closes) - 1)
            up_vol = 0.0
            tot_vol = 0.0
            for idx in range(len(closes) - n_win, len(closes)):
                v = vols[idx]
                tot_vol += v
                if closes[idx] >= closes[idx - 1]:
                    up_vol += v
            up_vol_dom = (up_vol / tot_vol) if tot_vol > 0 else 0.5

            # 14-session RSI
            rsi_14 = 50.0
            if len(closes) >= 15:
                deltas = np.diff(closes[-15:])
                gains = np.where(deltas > 0, deltas, 0.0)
                losses = np.where(deltas < 0, -deltas, 0.0)
                avg_gain = float(np.mean(gains))
                avg_loss = float(np.mean(losses))
                if avg_loss == 0:
                    rsi_14 = 100.0 if avg_gain > 0 else 50.0
                else:
                    rs_val = avg_gain / avg_loss
                    rsi_14 = 100.0 - (100.0 / (1.0 + rs_val))

            # Trend Template Score (0-100): genuine technical leadership structure
            close_above_ma20 = bool(p0 > ma20)
            close_above_ma50 = bool(p0 > ma50)
            ma20_above_ma50 = bool(ma20 > ma50)
            trend_pts = 0.0
            if close_above_ma20:
                trend_pts += 25.0
            if ma20_above_ma50:
                trend_pts += 20.0
            if close_above_ma50:
                trend_pts += 15.0
            # Proximity to 60d high: 0.75 -> 0 pts, >= 0.96 -> 25 pts
            high_prox_pts = max(0.0, min(25.0, ((dist_from_high - 0.75) / 0.21) * 25.0))
            up_vol_pts = max(0.0, min(15.0, ((up_vol_dom - 0.35) / 0.35) * 15.0))
            trend_template_score = round(max(10.0, min(98.0, trend_pts + high_prox_pts + up_vol_pts)), 2)

            # VSA Tactical Swing / Bottom-Fishing Signals over trailing 10 sessions
            vol_ma10 = float(np.mean(vols[-11:-1])) if len(vols) >= 11 else float(np.mean(vols))
            vol_ma20 = float(np.mean(vols[-21:-1])) if len(vols) >= 21 else float(np.mean(vols))
            has_spring = False
            has_no_supply = False
            has_pocket_pivot = False
            has_absorption = False
            has_bearish_upthrust = False
            max_absorption_ratio = 0.0

            scan_start = max(1, len(closes) - 10)
            for idx in range(scan_start, len(closes)):
                o, h, l, c, v = opens[idx], highs[idx], lows[idx], closes[idx], vols[idx]
                prev_c = closes[idx - 1]
                bar_range = h - l
                local_vol_ma10 = float(np.mean(vols[max(0, idx - 10):idx])) if idx > 0 else vol_ma10
                local_vol_ma20 = float(np.mean(vols[max(0, idx - 20):idx])) if idx > 0 else vol_ma20
                if bar_range > 0 and c > 0:
                    lower_wick = (min(o, c) - l) / bar_range
                    upper_wick = (h - max(o, c)) / bar_range
                    close_pos = (c - l) / bar_range
                    spread_pct = bar_range / c

                    if lower_wick >= 0.45 and close_pos >= 0.55 and local_vol_ma10 > 0 and v >= 0.9 * local_vol_ma10:
                        has_spring = True
                    if spread_pct <= 0.022 and local_vol_ma10 > 0 and v <= 0.72 * local_vol_ma10 and close_pos >= 0.45:
                        has_no_supply = True
                    if c > prev_c and local_vol_ma10 > 0 and v >= 1.40 * local_vol_ma10 and close_pos >= 0.55:
                        has_pocket_pivot = True
                    if local_vol_ma20 > 0:
                        ratio20 = v / local_vol_ma20
                        if ratio20 >= 1.50 and close_pos >= 0.50 and c <= ma20 * 1.03:
                            has_absorption = True
                            max_absorption_ratio = max(max_absorption_ratio, ratio20)
                    if idx == len(closes) - 1 and upper_wick >= 0.55 and local_vol_ma20 > 0 and v >= 1.8 * local_vol_ma20:
                        has_bearish_upthrust = True

            # Big-Picture Multi-Session Technical Structure (Trend, Support/Resistance, Money Flow)
            from app.domain.rules.technical_structure import TechnicalStructureAnalyzer
            ts_snap = TechnicalStructureAnalyzer.analyze_from_arrays(
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                volumes=vols,
                foreign_nets=foreign_nets,
                rs_20d=rs_20d,
            )

            # Only flag bearish upthrust on the BUY side if Big-Picture confirms genuine reversal
            # (meaningful daily spread >= 2.5%, weak close <= 35%, and at resistance / extended / distributing)
            if has_bearish_upthrust:
                last_rng = highs[-1] - lows[-1]
                last_spread = (last_rng / closes[-1]) if closes[-1] > 0 else 0.0
                last_cloc = ((closes[-1] - lows[-1]) / last_rng) if last_rng > 0 else 0.5
                at_res_or_ext = (
                    ts_snap.distance_to_resistance_pct <= 2.5
                    or ts_snap.extension_vs_ma20_pct >= 8.0
                    or ts_snap.stock_dist_days_10d >= 2
                )
                if not (last_spread >= 0.025 and last_cloc <= 0.35 and at_res_or_ext):
                    has_bearish_upthrust = False

            # VSA Swing score (0-100)
            vsa_pts = 42.0
            if has_spring:
                vsa_pts += 16.0
            if has_no_supply:
                vsa_pts += 12.0
            if has_pocket_pivot:
                vsa_pts += 14.0
            if has_absorption:
                vsa_pts += 10.0
            # Support zone proximity (near MA50 or constructive pullback without crash)
            ratio_ma50 = (p0 / ma50) if ma50 > 0 else 1.0
            if 0.92 <= ratio_ma50 <= 1.06:
                vsa_pts += 8.0
            elif ratio_ma50 < 0.85:
                vsa_pts -= 10.0
            # Constructive RSI pullback zone (not overbought chase, not free-fall)
            if 32.0 <= rsi_14 <= 58.0:
                vsa_pts += 6.0
            elif rsi_14 > 75.0:
                vsa_pts -= 8.0
            # Foreign net buying over last 5 sessions
            if float(np.sum(foreign_nets[-5:])) > 0:
                vsa_pts += 5.0
            if up_vol_dom >= 0.55:
                vsa_pts += 5.0
            if has_bearish_upthrust:
                vsa_pts -= 25.0

            vsa_swing_score = round(max(10.0, min(95.0, vsa_pts)), 2)

            raw_metrics[sym] = {
                "sector": sector_by_ticker.get(sym, "General"),
                "ret_20d": round(ret_20d, 4),
                "ret_60d": round(ret_60d, 4),
                "rs_20d": round(rs_20d, 4),
                "rs_60d": round(rs_60d, 4),
                "rs_composite": round(rs_composite, 4),
                "rs_score": 50.0,  # populated by cross-sectional rank below
                "sector_rs_20d": 0.0,
                "sector_leadership_score": 50.0,
                "trend_template_score": trend_template_score,
                "vsa_swing_score": vsa_swing_score,
                "big_picture_score": ts_snap.big_picture_score,
                "price_structure": ts_snap.price_structure,
                "money_flow_state": ts_snap.money_flow_state,
                "cmf_20d": ts_snap.cmf_20d,
                "up_down_vol_ratio_10d": ts_snap.up_down_vol_ratio_10d,
                "stock_dist_days_10d": ts_snap.stock_dist_days_10d,
                "distance_to_resistance_pct": ts_snap.distance_to_resistance_pct,
                "close_above_ma20": close_above_ma20,
                "close_above_ma50": close_above_ma50,
                "dist_from_high_60d": round(dist_from_high, 4),
                "has_spring": has_spring,
                "has_no_supply": has_no_supply,
                "has_pocket_pivot": has_pocket_pivot,
                "has_absorption": has_absorption,
                "has_bearish_upthrust": has_bearish_upthrust,
                "absorption_vol_ratio": round(max_absorption_ratio or (vols[-1] / vol_ma20 if vol_ma20 > 0 else 1.0), 2),
                "up_vol_dominance": round(up_vol_dom, 4),
                "rsi_14": round(rsi_14, 1),
            }

        # Cross-sectional RS percentile rank (0..100)
        if raw_metrics:
            rs_series = pd.Series({k: v["rs_composite"] for k, v in raw_metrics.items()})
            rs_ranks = rs_series.rank(pct=True) * 100.0
            for sym, rank_val in rs_ranks.items():
                raw_metrics[sym]["rs_score"] = round(float(rank_val), 2)

            # Sector leadership rank (median rs_20d per ICB sector)
            sector_buckets: Dict[str, List[float]] = {}
            for sym, m in raw_metrics.items():
                sector_buckets.setdefault(m["sector"], []).append(m["rs_20d"])
            sector_medians = {sec: float(np.median(vals)) for sec, vals in sector_buckets.items() if vals}
            if sector_medians:
                sec_series = pd.Series(sector_medians)
                sec_ranks = sec_series.rank(pct=True) * 100.0
                for sym, m in raw_metrics.items():
                    sec = m["sector"]
                    m["sector_rs_20d"] = round(sector_medians.get(sec, 0.0), 4)
                    m["sector_leadership_score"] = round(float(sec_ranks.get(sec, 50.0)), 2)

        # Build pairwise return correlation matrix (trailing 60 sessions)
        corr_matrix = pd.DataFrame()
        if len(close_series_map) >= 2:
            try:
                price_df = pd.DataFrame(close_series_map).sort_index().tail(61)
                ret_df = price_df.pct_change().dropna(how="all")
                if len(ret_df) >= 10:
                    corr_matrix = ret_df.corr().fillna(0.0)
            except Exception as exc:
                logger.debug("[FactorService] Correlation matrix computation skipped: %s", exc)

        return raw_metrics, corr_matrix
