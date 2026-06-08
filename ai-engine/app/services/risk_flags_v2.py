"""Risk Flags V2 — Batch computed flag engine.

Replaces the old per-symbol RAG-based risk_flags.py. Computes 10 flags from
structured data already in the DB (financial_statements, technical_indicators,
foreign_flow, insider_trades, news_events). No scraping, no RAG fallback.

Call from daily ETL (step_risk_flags) via refresh_incremental().
All functions are batch — single DB pass per tier.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

from app.services.pg_pool import DB_URL
from app.brain.dataflows.vendors.vn.sector_groups import (
    classify, FINANCIALS, REAL_ESTATE, OTHERS,
)

logger = logging.getLogger(__name__)
TZ_VN = timezone(timedelta(hours=7))

# ── Flag definitions (MSCI 2019 optimized per-sector) ─────────────────────

HARD_FLAGS = {"CANH_BAO_TC", "CHAM_BAO_TC", "DEBT_DANGER", "DEBT_DANGER_FIN", "CAR_DANGER"}
SOFT_FLAGS = {
    "FLOOR_TRAP", "SHARP_DROP", "KHOI_LUONG_BAT_THUONG",
    "FOREIGN_FLOW_ANOMALY", "INSIDER_SELLING_ANOMALY", "GOVERNANCE_SHOCK",
    "M_SCORE_FLAG", "F_SCORE_FLAG",
    "LIQUIDITY_DANGER", "VOLATILITY_DANGER", "EARNINGS_QUALITY",
}
ALL_FLAGS = HARD_FLAGS | SOFT_FLAGS

# Soft flags auto-deactivate after N days without re-triggering
SOFT_FLAG_TTL_DAYS = 60

# Special fiscal year symbols (Jul-Jun cycle)
# SBT, LSS: niên độ tài chính 01/07→30/06
SPECIAL_FY = {"SBT", "LSS"}

# Thresholds (original)
FLOOR_TRAP_PCT = -6.9      # -6.9% (VN HOSE limit is -7%, tolerance)
FLOOR_TRAP_MIN_DAYS = 2     # 2+ consecutive days
SHARP_DROP_PCT = -7.0       # -7% single day
VOLUME_SPIKE_RATIO = 3.0    # 3x 20d average
FOREIGN_SELL_DAYS = 5       # 5+ consecutive days
FOREIGN_SELL_THRESHOLD = -1e9  # -1B VND per day
INSIDER_NET_SELL_RATIO = 2.0   # net sell > 2x net buy
INSIDER_MIN_QTY = 100_000      # absolute quantity
M_SCORE_THRESHOLD = -2.22      # M > -2.22 = manipulation risk
F_SCORE_WEAK = 4               # F < 4 = weak (Piotroski 2000 standard)
F_SCORE_STRONG = 7

# Thresholds (new — MSCI 2019 industry-specific)
DEBT_DANGER_DE_THRESHOLD = 3.0      # D/E > 3 → DEBT_DANGER (OTHERS + RE)
DEBT_DANGER_FIN_NPL_THRESHOLD = 5.0  # NPL > 5% → DEBT_DANGER_FIN (FINANCIALS)
CAR_DANGER_THRESHOLD = 8.0           # CAR < 8% → CAR_DANGER (FINANCIALS)
AMIHUD_THRESHOLD = 0.01              # Amihud > 0.01 → LIQUIDITY_DANGER
VOLATILITY_DANGER_THRESHOLD = 0.5    # Vol 60d > 0.5 → VOLATILITY_DANGER
EARNINGS_QUALITY_ACCRUAL_THRESHOLD = 0.2  # Accrual/NI > 0.2 → EARNINGS_QUALITY


# ── Format helpers ────────────────────────────────────────────────────────

def _flag_tuple(symbol: str, flag_type: str, description: str, source: str) -> tuple:
    return (symbol, flag_type, date.today(), description, source, True)


def _deactivate_old(cur, calc_date: date) -> int:
    """Deactivate soft flags older than TTL that haven't been re-triggered."""
    cur.execute(
        """UPDATE risk_flags
           SET is_active = FALSE
           WHERE is_active = TRUE
             AND flag_type = ANY(%s)
             AND effective_date < %s""",
        (list(SOFT_FLAGS), calc_date - timedelta(days=SOFT_FLAG_TTL_DAYS)),
    )
    return cur.rowcount


# ── P0: Flags 1-5 ────────────────────────────────────────────────────────

def compute_p0_flags(cur, symbols: list[str], calc_date: date) -> list[tuple]:
    """CANH_BAO_TC, CHAM_BAO_TC, FLOOR_TRAP, SHARP_DROP, KHOI_LUONG_BAT_THUONG."""
    flags: list[tuple] = []

    # ── 1+2: CANH_BAO_TC + CHAM_BAO_TC from financial_statements ─────
    # Use MAX across BS + IS for CHAM_BAO_TC (BS and IS together define a
    # complete quarterly report; CF-only updates from AlphaStock are not
    # sufficient to consider a report "filed").  Keep BS-specific query
    # separately for CANH_BAO_TC (equity check needs BS data).
    cur.execute(
        """SELECT fs.symbol, MAX(fs.period_end) AS max_pe
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type IN ('BS', 'IS')
           GROUP BY fs.symbol""",
        (symbols,),
    )
    latest_pe_map: dict[str, date] = dict(cur.fetchall())

    cur.execute(
        """SELECT DISTINCT ON (fs.symbol)
                  fs.symbol, fs.period_end, fs.data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
              AND fs.statement_type = 'BS'
           ORDER BY fs.symbol, fs.period_end DESC""",
        (symbols,),
    )
    bs_map: dict[str, tuple[date, dict]] = {}
    for sym, pe, raw_data in cur.fetchall():
        data = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if isinstance(raw_data, str) else {})
        bs_map[sym] = (pe, data)

    # Check each symbol for hard flags
    hard_symbols_done: set[str] = set()

    for sym in symbols:
        entry = bs_map.get(sym)
        if entry is None:
            # No financial data at all → CANH_BAO_TC
            flags.append(_flag_tuple(sym, "CANH_BAO_TC",
                                     "Không có dữ liệu báo cáo tài chính",
                                     "financial_statements_nodata"))
            hard_symbols_done.add(sym)
            continue

        period_end, data = entry

        # CHAM_BAO_TC: use MAX across ALL statement types to avoid false
        # positives when AlphaStock has partial data for the latest period.
        max_pe = latest_pe_map.get(sym)
        if max_pe:
            y, m = max_pe.year, max_pe.month
        else:
            y, m = period_end.year, period_end.month

        if m == 3:       # Q1 → next Q2 (June 30)
            next_pe = date(y, 6, 30)
            qlabel = f"Q2-{y}"
        elif m == 6:     # Q2 → next Q3 (Sep 30)
            next_pe = date(y, 9, 30)
            qlabel = f"Q3-{y}"
        elif m == 9:     # Q3 → next Q4 (Dec 31)
            next_pe = date(y, 12, 31)
            qlabel = f"Q4-{y}"
        elif m == 12:    # Q4 → next Q1 next year (Mar 31)
            next_pe = date(y + 1, 3, 31)
            qlabel = f"Q1-{y + 1}"
        else:
            next_pe = None

        if sym in SPECIAL_FY:
            # Niên độ đặc thù Jul-Jun, bỏ qua CHAM_BAO_TC theo lịch dương
            pass
        elif next_pe is not None and calc_date > next_pe + timedelta(days=19):
            days_over = (calc_date - next_pe).days
            flags.append(_flag_tuple(
                sym, "CHAM_BAO_TC",
                f"BCTC {qlabel} chậm {days_over - 20} ngày (kỳ gần nhất: {max_pe or period_end})",
                "financial_statements",
            ))
            hard_symbols_done.add(sym)

        # CANH_BAO_TC: negative equity, or accumulated losses (uses BS data)
        equity = _get_val(data, ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu", "_Owner's Equity", "OWNER'S EQUITY", "_Shareholders' equity"])
        if equity is not None and equity < 0:
            flags.append(_flag_tuple(
                sym, "CANH_BAO_TC",
                f"Vốn chủ sở hữu âm: {equity:,.0f} VND",
                "financial_statements",
            ))
            hard_symbols_done.add(sym)

    # ── 3-5: FLOOR_TRAP, SHARP_DROP, KHOI_LUONG_BAT_THUONG ──────────
    cur.execute(
        """SELECT symbol, calc_date, indicators
           FROM technical_indicators
           WHERE symbol = ANY(%s)
             AND calc_date >= %s
           ORDER BY symbol, calc_date DESC""",
        (symbols, calc_date - timedelta(days=10)),
    )
    tech_map: dict[str, list[tuple[date, dict]]] = {}
    for sym, cd, raw_ind in cur.fetchall():
        ind = raw_ind if isinstance(raw_ind, dict) else (json.loads(raw_ind) if isinstance(raw_ind, str) else {})
        tech_map.setdefault(sym, []).append((cd, ind))

    for sym in symbols:
        rows = tech_map.get(sym, [])
        if len(rows) < 2:
            continue

        # Extract momentum_1d values for last 5 days
        mom1d: list[float] = []
        latest_vol_ratio: Optional[float] = None
        for cd, ind in sorted(rows, key=lambda x: x[0], reverse=True)[:5]:
            mom = _get_val(ind, ["momentum_1d"])
            if mom is not None:
                mom1d.append(mom)
            if latest_vol_ratio is None:
                latest_vol_ratio = _get_val(ind, ["volume_ratio"])

        # FLOOR_TRAP: ≥2 consecutive days ≤ -6.9%
        floor_streak = 0
        for m in mom1d:
            if m <= FLOOR_TRAP_PCT:
                floor_streak += 1
            else:
                floor_streak = 0
        if floor_streak >= FLOOR_TRAP_MIN_DAYS:
            flags.append(_flag_tuple(
                sym, "FLOOR_TRAP",
                f"Sàn {floor_streak} phiên liên tiếp (mức giảm ≤ {abs(FLOOR_TRAP_PCT):.1f}%)",
                "technical_indicators",
            ))

        # SHARP_DROP: single day ≤ -7%
        for m in mom1d:
            if m <= SHARP_DROP_PCT:
                flags.append(_flag_tuple(
                    sym, "SHARP_DROP",
                    f"Giảm sâu {abs(m):.1f}% trong 1 phiên",
                    "technical_indicators",
                ))
                break

        # KHOI_LUONG_BAT_THUONG: volume_ratio ≥ 3.0
        if latest_vol_ratio is not None and latest_vol_ratio >= VOLUME_SPIKE_RATIO:
            flags.append(_flag_tuple(
                sym, "KHOI_LUONG_BAT_THUONG",
                f"Khối lượng gấp {latest_vol_ratio:.1f} lần trung bình 20 phiên",
                "technical_indicators",
            ))

    return flags


# ── P1: Flags 6-8 ────────────────────────────────────────────────────────

def compute_p1_flags(cur, symbols: list[str], calc_date: date) -> list[tuple]:
    """FOREIGN_FLOW_ANOMALY, INSIDER_SELLING_ANOMALY, GOVERNANCE_SHOCK."""
    flags: list[tuple] = []

    # ── 6: FOREIGN_FLOW_ANOMALY ───────────────────────────────────────
    cur.execute(
        """SELECT symbol, trade_date, net_value
           FROM foreign_flow
           WHERE symbol = ANY(%s)
             AND trade_date >= %s
           ORDER BY symbol, trade_date DESC""",
        (symbols, calc_date - timedelta(days=15)),
    )
    ff_map: dict[str, list[tuple[date, float]]] = {}
    for sym, td, nv in cur.fetchall():
        ff_map.setdefault(sym, []).append((td, nv if nv is not None else 0.0))

    for sym in symbols:
        rows = ff_map.get(sym, [])
        if len(rows) < FOREIGN_SELL_DAYS:
            continue
        sorted_rows = sorted(rows, key=lambda x: x[0], reverse=True)
        streak = 0
        for td, nv in sorted_rows:
            if nv < FOREIGN_SELL_THRESHOLD:
                streak += 1
            else:
                break
        if streak >= FOREIGN_SELL_DAYS:
            flags.append(_flag_tuple(
                sym, "FOREIGN_FLOW_ANOMALY",
                f"Khối ngoại bán ròng {streak} phiên liên tiếp",
                "foreign_flow",
            ))

    # ── 7: INSIDER_SELLING_ANOMALY ────────────────────────────────────
    cur.execute(
        """SELECT symbol, trade_type, SUM(quantity) as total_qty
           FROM insider_trades
           WHERE symbol = ANY(%s)
             AND trade_date >= %s
           GROUP BY symbol, trade_type""",
        (symbols, calc_date - timedelta(days=30)),
    )
    insider_map: dict[str, dict[str, int]] = {}
    for sym, ttype, qty in cur.fetchall():
        insider_map.setdefault(sym, {})[ttype] = (qty or 0)

    for sym in symbols:
        sd = insider_map.get(sym, {})
        buy_qty = sd.get("BUY", 0)
        sell_qty = sd.get("SELL", 0)
        net_sell = sell_qty - buy_qty
        if net_sell > INSIDER_MIN_QTY and (buy_qty == 0 or sell_qty / buy_qty > INSIDER_NET_SELL_RATIO):
            flags.append(_flag_tuple(
                sym, "INSIDER_SELLING_ANOMALY",
                f"Insider bán ròng {net_sell:,} CP trong 30 ngày (mua={buy_qty:,}, bán={sell_qty:,})",
                "insider_trades",
            ))

    # ── 8: GOVERNANCE_SHOCK ───────────────────────────────────────────
    governance_kw = [
        "từ nhiệm", "miễn nhiệm", "thay ceo", "thay chủ tịch",
        "thay đổi nhân sự", "thay đổi ban điều hành", "chủ tịch",
        "tổng giám đốc", "giám đốc điều hành", "hội đồng quản trị",
    ]
    # Build ILIKE clause safely
    like_clauses = " OR ".join(f"title ILIKE '%%{kw}%%'" for kw in governance_kw)
    cur.execute(
        f"""SELECT symbol, COUNT(*) as cnt
            FROM news_events
            WHERE symbol = ANY(%s)
              AND published_date >= %s
              AND ({like_clauses})
            GROUP BY symbol""",
        (symbols, calc_date - timedelta(days=30)),
    )
    for sym, cnt in cur.fetchall():
        flags.append(_flag_tuple(
            sym, "GOVERNANCE_SHOCK",
            f"{cnt} tin tức về thay đổi nhân sự/quản trị trong 30 ngày gần nhất",
            "news_events",
        ))

    return flags


# ── P2: Flags 9-10 (M-Score, F-Score) ────────────────────────────────────

def _get_val(data: dict, keywords: list[str], default: Optional[float] = None) -> Optional[float]:
    """Extract numeric value from flat dict by keyword matching."""
    for k, v in data.items():
        if any(kw in k for kw in keywords):
            if isinstance(v, (int, float)):
                return float(v)
    return default


def _is_bank(symbol: str) -> bool:
    """Detect if a symbol is a bank by its industry/sector from stocks table."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT industry FROM stocks WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
        if row and row[0]:
            bank_kw = ["ngân hàng", "bank", "tài chính ngân hàng", "financial services"]
            return any(kw in (row[0] or "").lower() for kw in bank_kw)
        return False
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()


def compute_p2_flags(cur, symbols: list[str], calc_date: date, bank_symbols: set[str]) -> list[tuple]:
    """M-Score (non-bank only) and F-Score (all stocks)."""
    flags: list[tuple] = []

    # Load 8 most recent quarters of income_statement + balance_sheet for all symbols
    cur.execute(
        """SELECT fs.symbol, fs.period_end, fs.statement_type, fs.data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
              AND fs.statement_type IN ('BS', 'IS', 'CF')
             AND fs.period_end >= %s
           ORDER BY fs.symbol, fs.period_end DESC""",
         (symbols, calc_date - timedelta(days=3000)),  # 8 years back (annual data)
    )
    stmt_map: dict[str, dict[str, list[tuple[date, dict]]]] = {}
    for sym, pe, st, raw_data in cur.fetchall():
        data = raw_data if isinstance(raw_data, dict) else (json.loads(raw_data) if isinstance(raw_data, str) else {})
        stmt_map.setdefault(sym, {}).setdefault(st, []).append((pe, data))

    for sym in symbols:
        bs_list = stmt_map.get(sym, {}).get("BS", [])
        inc_list = stmt_map.get(sym, {}).get("IS", [])
        cf_list = stmt_map.get(sym, {}).get("CF", [])

        if len(bs_list) < 1 or len(inc_list) < 1:
            continue

        # Sort by period_end ascending (oldest first)
        bs_list.sort(key=lambda x: x[0])
        inc_list.sort(key=lambda x: x[0])
        cf_list.sort(key=lambda x: x[0])

        latest_bs = bs_list[-1][1]
        latest_inc = inc_list[-1][1]
        latest_cf = cf_list[-1][1] if cf_list else {}

        ni = _get_val(latest_inc, ["Lợi nhuận sau thuế", "Lãi/(lỗ) thuần sau thuế", "_Net profit/(loss) after tax", "Net profit", "Lợi nhuận thuần"])
        ta = _get_val(latest_bs, ["TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản", "TỔNG TÀI SẢN", "_Total Assets", "TOTAL ASSETS", "_Total resource"])
        cfo = _get_val(latest_cf, ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "Lưu chuyển tiền tệ ròng", "Lưu chuyển thuần", "_Net cash from operating activities", "_Net cash flows from operating", "Net cash from operating"])

        # ── F-Score (all stocks) ────────────────────────────────────
        if ni is not None and ta is not None and ta != 0:
            fscore = 0
            roa_cur = ni / ta

            # 1. ROA > 0
            if roa_cur > 0:
                fscore += 1

            # 2. CFO > 0
            if cfo is not None and cfo > 0:
                fscore += 1

            # 3-4: Need previous year data
            if len(inc_list) >= 2 and len(bs_list) >= 2:
                prev_inc = inc_list[-2][1]
                prev_bs = bs_list[-2][1]

                ni_prev = _get_val(prev_inc, ["Lợi nhuận sau thuế", "Lãi/(lỗ) thuần sau thuế", "_Net profit/(loss) after tax", "Net profit", "Lợi nhuận thuần"])
                ta_prev = _get_val(prev_bs, ["TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản", "TỔNG TÀI SẢN", "_Total Assets", "TOTAL ASSETS", "_Total resource"])

                if ni_prev is not None and ta_prev is not None and ta_prev != 0:
                    roa_prev = ni_prev / ta_prev
                    # 3. ΔROA > 0
                    if roa_cur > roa_prev:
                        fscore += 1

                    # 4. CFO > ROA (accrual quality)
                    if cfo is not None and ta != 0 and (cfo / ta) > roa_cur:
                        fscore += 1

                # 5. ΔLeverage < 0
                liab_cur = _get_val(latest_bs, ["TỔNG NỢ PHẢI TRẢ", "Tổng nợ phải trả", "_TOTAL LIABILITIES", "_LIABILITIES", "TOTAL LIABILITIES"])
                liab_prev = _get_val(prev_bs, ["TỔNG NỢ PHẢI TRẢ", "Tổng nợ phải trả", "_TOTAL LIABILITIES", "_LIABILITIES", "TOTAL LIABILITIES"])
                equity_cur = _get_val(latest_bs, ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu", "_Owner's Equity", "OWNER'S EQUITY", "_Shareholders' equity"])
                equity_prev = _get_val(prev_bs, ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu", "_Owner's Equity", "OWNER'S EQUITY", "_Shareholders' equity"])
                if all(v is not None and v != 0 for v in [liab_cur, liab_prev, equity_cur, equity_prev]):
                    d_e_cur = liab_cur / equity_cur
                    d_e_prev = liab_prev / equity_prev
                    if d_e_cur < d_e_prev:
                        fscore += 1

            # 6. ΔCurrent Ratio > 0
            ca_cur = _get_val(latest_bs, ["Tài sản ngắn hạn", "Ngắn hạn", "_CURRENT ASSETS", "CURRENT ASSETS", "Tài sản lưu động"])
            cl_cur = _get_val(latest_bs, ["Nợ ngắn hạn", "_Current liabilities", "Current liabilities"])
            if len(bs_list) >= 2:
                prev_bs_2 = bs_list[-2][1]
                ca_prev = _get_val(prev_bs_2, ["Tài sản ngắn hạn", "Ngắn hạn", "_CURRENT ASSETS", "CURRENT ASSETS", "Tài sản lưu động"])
                cl_prev = _get_val(prev_bs_2, ["Nợ ngắn hạn", "_Current liabilities", "Current liabilities"])
                if all(v is not None and v != 0 for v in [ca_cur, cl_cur, ca_prev, cl_prev]):
                    cr_cur = ca_cur / cl_cur
                    cr_prev = ca_prev / cl_prev
                    if cr_cur > cr_prev:
                        fscore += 1

            # 7. ΔShares < 0 (no new shares issued)
            shares_cur = _get_val(latest_bs, ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu", "_Owner's Equity", "OWNER'S EQUITY", "_Shareholders' equity"])
            if shares_cur is not None and len(bs_list) >= 2:
                shares_prev = _get_val(bs_list[-2][1], ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu", "_Owner's Equity", "OWNER'S EQUITY", "_Shareholders' equity"])
                if shares_prev is not None and shares_cur <= shares_prev:
                    fscore += 1

            # 8. ΔGross Margin > 0
            rev_cur = _get_val(latest_inc, ["Doanh thu thuần", "Thu nhập lãi thuần", "_Net sales", "Net sales", "Doanh thu thuần về hoạt động kinh doanh"])
            cogs_cur = _get_val(latest_inc, ["Giá vốn hàng bán", "Chi phí hoạt động", "_Cost of sales", "Cost of sales"])
            if rev_cur is not None and cogs_cur is not None and rev_cur != 0 and len(inc_list) >= 2:
                rev_prev = _get_val(inc_list[-2][1], ["Doanh thu thuần", "Thu nhập lãi thuần", "_Net sales", "Net sales", "Doanh thu thuần về hoạt động kinh doanh"])
                cogs_prev = _get_val(inc_list[-2][1], ["Giá vốn hàng bán", "Chi phí hoạt động", "_Cost of sales", "Cost of sales"])
                if rev_prev is not None and cogs_prev is not None and rev_prev != 0:
                    gm_cur = (rev_cur - cogs_cur) / rev_cur
                    gm_prev = (rev_prev - cogs_prev) / rev_prev
                    if gm_cur > gm_prev:
                        fscore += 1

            # 9. ΔAsset Turnover > 0
            if rev_cur is not None and ta_prev is not None and ta_prev != 0 and rev_prev is not None:
                at_cur = rev_cur / ta
                at_prev = rev_prev / ta_prev
                if at_cur > at_prev:
                    fscore += 1

            # Store F-Score
            if fscore < F_SCORE_WEAK:
                flags.append(_flag_tuple(
                    sym, "F_SCORE_FLAG",
                    f"F-Score={fscore}/9 — cơ bản yếu, rủi ro cao",
                    "financial_statements",
                ))

        # ── M-Score (non-bank only) ──────────────────────────────────
        if sym not in bank_symbols and len(inc_list) >= 8 and len(bs_list) >= 8:
            try:
                cfo_val_ms = _get_val(cf_list[-1][1] if len(cf_list) >= 1 else {}, ["Lưu chuyển tiền thuần từ hoạt động kinh doanh", "Lưu chuyển tiền tệ ròng", "Lưu chuyển thuần", "_Net cash from operating activities", "_Net cash flows from operating", "Net cash from operating"])
                mscore = _compute_mscore(bs_list, inc_list, cfo_val_ms)
                if mscore is not None and mscore > M_SCORE_THRESHOLD:
                    flags.append(_flag_tuple(
                        sym, "M_SCORE_FLAG",
                        f"M-Score={mscore:.2f} > {-2.22} — nguy cơ thao túng lợi nhuận",
                        "financial_statements",
                    ))
            except Exception as e:
                logger.debug("M-Score failed for %s: %s", sym, e)

    return flags


def _compute_mscore(bs_list: list[tuple[date, dict]], inc_list: list[tuple[date, dict]], cfo_val: Optional[float] = None) -> Optional[float]:
    """Compute Beneish M-Score. Requires 2 years (8 quarters) of data.

    Returns M value, or None if insufficient data.
    M > −2.22 → earnings manipulation risk.
    """
    if len(inc_list) < 8 or len(bs_list) < 8:
        return None

    cur_inc = inc_list[-1][1]
    prev_inc = inc_list[-5][1]  # 4 quarters back (YoY)
    cur_bs = bs_list[-1][1]
    prev_bs = bs_list[-5][1]   # 4 quarters back
    prev_bs_2 = bs_list[-6][1] if len(bs_list) >= 6 else bs_list[-5][1]  # 5 quarters back (for Q adjustment)

    # Required items
    ni_cur = _get_val(cur_inc, ["Lợi nhuận sau thuế", "Lãi/(lỗ) thuần sau thuế", "_Net profit/(loss) after tax", "Net profit", "Lợi nhuận thuần"])
    ni_prev = _get_val(prev_inc, ["Lợi nhuận sau thuế", "Lãi/(lỗ) thuần sau thuế", "_Net profit/(loss) after tax", "Net profit", "Lợi nhuận thuần"])
    rev_cur = _get_val(cur_inc, ["Doanh thu thuần", "Thu nhập lãi thuần", "_Net sales", "Net sales", "Doanh thu thuần về hoạt động kinh doanh"])
    rev_prev = _get_val(prev_inc, ["Doanh thu thuần", "Thu nhập lãi thuần", "_Net sales", "Net sales", "Doanh thu thuần về hoạt động kinh doanh"])
    cogs_cur = _get_val(cur_inc, ["Giá vốn hàng bán", "Chi phí hoạt động", "_Cost of sales", "Cost of sales"])
    cogs_prev = _get_val(prev_inc, ["Giá vốn hàng bán", "Chi phí hoạt động", "_Cost of sales", "Cost of sales"])
    ta_cur = _get_val(cur_bs, ["TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản", "TỔNG TÀI SẢN", "_Total Assets", "TOTAL ASSETS", "_Total resource"])
    ta_prev = _get_val(prev_bs, ["TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản", "TỔNG TÀI SẢN", "_Total Assets", "TOTAL ASSETS", "_Total resource"])
    ca_cur = _get_val(cur_bs, ["Tài sản ngắn hạn", "Ngắn hạn", "_CURRENT ASSETS", "CURRENT ASSETS", "Tài sản lưu động"])
    ca_prev = _get_val(prev_bs, ["Tài sản ngắn hạn", "Ngắn hạn", "_CURRENT ASSETS", "CURRENT ASSETS", "Tài sản lưu động"])
    ppent_cur = _get_val(cur_bs, ["Tài sản cố định", "TSCĐ", "Nguyên giá TSCĐ", "_Fixed assets", "Fixed assets", "_Tangible fixed assets"])
    ppent_prev = _get_val(prev_bs, ["Tài sản cố định", "TSCĐ", "Nguyên giá TSCĐ", "_Fixed assets", "Fixed assets", "_Tangible fixed assets"])
    cl_cur = _get_val(cur_bs, ["Nợ ngắn hạn", "_Current liabilities", "Current liabilities"])
    cl_prev = _get_val(prev_bs, ["Nợ ngắn hạn", "_Current liabilities", "Current liabilities"])
    lt_debt_cur = _get_val(cur_bs, ["Vay và nợ thuê tài chính dài hạn", "Vay dài hạn", "Nợ dài hạn", "_Long term borrowings", "_Long-term borrowings"])
    lt_debt_prev = _get_val(prev_bs, ["Vay và nợ thuê tài chính dài hạn", "Vay dài hạn", "Nợ dài hạn", "_Long term borrowings", "_Long-term borrowings"])
    dep_cur = 0  # Depreciation not in IS; will attempt from CF in caller
    dep_prev = 0
    sga_cur = _get_val(cur_inc, ["Chi phí bán hàng", "Chi phí quản lý doanh nghiệp", "Chi phí bán hàng và quản lý", "_Selling expenses", "_General and Admin", "Selling expenses"])
    sga_prev = _get_val(prev_inc, ["Chi phí bán hàng", "Chi phí quản lý doanh nghiệp", "Chi phí bán hàng và quản lý", "_Selling expenses", "_General and Admin", "Selling expenses"])

    if any(v is None for v in [ni_cur, rev_cur, ta_cur, ta_prev, ca_cur, cl_cur]):
        return None

    # Defaults for optional items
    cogs_cur = cogs_cur or 0
    cogs_prev = cogs_prev or 0
    ca_prev = ca_prev or ca_cur
    ppent_cur = ppent_cur or 0
    ppent_prev = ppent_prev or ppent_cur
    cl_prev = cl_prev or cl_cur
    lt_debt_cur = lt_debt_cur or 0
    lt_debt_prev = lt_debt_prev or lt_debt_cur
    dep_cur = dep_cur or 0
    dep_prev = dep_prev or dep_cur
    sga_cur = sga_cur or 0
    sga_prev = sga_prev or sga_cur

    # DSRI: Days Sales in Receivables Index
    rec_cur = _get_val(cur_bs, ["Các khoản phải thu", "Phải thu", "_Accounts receivable", "_Trade accounts receivable", "Accounts receivable"]) or 0
    rec_prev = _get_val(prev_bs, ["Các khoản phải thu", "Phải thu", "_Accounts receivable", "_Trade accounts receivable", "Accounts receivable"]) or rec_cur
    dsri = (rec_cur / rev_cur) / (rec_prev / rev_prev) if rev_prev != 0 and rec_prev != 0 else 1.0

    # GMI: Gross Margin Index
    gm_cur = (rev_cur - cogs_cur) / rev_cur if rev_cur != 0 else 0
    gm_prev = (rev_prev - cogs_prev) / rev_prev if rev_prev != 0 else gm_cur
    gmi = gm_prev / gm_cur if gm_cur != 0 else 1.0

    # AQI: Asset Quality Index
    noncur_cur = ta_cur - ca_cur
    noncur_prev = ta_prev - ca_prev
    aq_cur = (noncur_cur - ppent_cur - (ta_cur - ca_cur - ppent_cur)) / ta_cur if ta_cur != 0 else 0
    aq_prev = (noncur_prev - ppent_prev - (ta_prev - ca_prev - ppent_prev)) / ta_prev if ta_prev != 0 else aq_cur
    aqi = aq_cur / aq_prev if aq_prev != 0 else 1.0

    # SGI: Sales Growth Index
    sgi = rev_cur / rev_prev if rev_prev != 0 else 1.0

    # DEPI: Depreciation Index
    dep_ratio_cur = dep_cur / (ppent_cur + dep_cur) if (ppent_cur + dep_cur) != 0 else 0
    dep_ratio_prev = dep_prev / (ppent_prev + dep_prev) if (ppent_prev + dep_prev) != 0 else dep_ratio_cur
    depi = dep_ratio_prev / dep_ratio_cur if dep_ratio_cur != 0 else 1.0

    # SGAI: Sales, General & Admin Index
    sga_ratio_cur = sga_cur / rev_cur if rev_cur != 0 else 0
    sga_ratio_prev = sga_prev / rev_prev if rev_prev != 0 else sga_ratio_cur
    sgai = sga_ratio_cur / sga_ratio_prev if sga_ratio_prev != 0 else 1.0

    # LVGI: Leverage Index
    liab_cur = _get_val(cur_bs, ["TỔNG NỢ PHẢI TRẢ", "Tổng nợ phải trả", "_TOTAL LIABILITIES", "_LIABILITIES", "TOTAL LIABILITIES"]) or 0
    liab_prev = _get_val(prev_bs, ["TỔNG NỢ PHẢI TRẢ", "Tổng nợ phải trả", "_TOTAL LIABILITIES", "_LIABILITIES", "TOTAL LIABILITIES"]) or liab_cur
    lvg_cur = liab_cur / ta_cur if ta_cur != 0 else 0
    lvg_prev = liab_prev / ta_prev if ta_prev != 0 else lvg_cur
    lvgi = lvg_cur / lvg_prev if lvg_prev != 0 else 1.0

    # TATA: Total Accruals to Total Assets
    cfo_cur = cfo_val or 0
    tata = (ni_cur - cfo_cur) / ta_cur if ta_cur != 0 else 0

    m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai - 0.327 * lvgi + 0.327 * tata)
    return m


# ── P3: New flags (MSCI 2019) ─────────────────────────────────────────────

def compute_p3_flags(cur, symbols: list[str], calc_date: date) -> list[tuple]:
    """DEBT_DANGER, LIQUIDITY_DANGER, VOLATILITY_DANGER, EARNINGS_QUALITY."""
    flags: list[tuple] = []

    # 1. Load sector classification
    cur.execute("SELECT symbol, industry FROM stocks WHERE symbol = ANY(%s)", (symbols,))
    sym_industry = dict(cur.fetchall())
    sym_group: dict[str, str] = {}
    for sym in symbols:
        sym_group[sym] = classify(sym_industry.get(sym), sym)

    # 2. DEBT_DANGER: D/E > 3 (OTHERS + REAL_ESTATE only)
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, debt_equity
           FROM financial_ratios
           WHERE symbol = ANY(%s)
           ORDER BY symbol, ratio_date DESC""",
        (symbols,),
    )
    for sym, de in cur.fetchall():
        if sym_group.get(sym) in (OTHERS, REAL_ESTATE) and de is not None and de > DEBT_DANGER_DE_THRESHOLD:
            flags.append(_flag_tuple(
                sym, "DEBT_DANGER",
                f"D/E={de:.2f} > {DEBT_DANGER_DE_THRESHOLD} — rủi ro nợ cao",
                "financial_ratios",
            ))

    # 3. LIQUIDITY_DANGER & VOLATILITY_DANGER from technical_indicators
    cur.execute(
        """SELECT symbol, calc_date, indicators
           FROM technical_indicators
           WHERE symbol = ANY(%s)
             AND calc_date >= %s
           ORDER BY symbol, calc_date DESC""",
        (symbols, calc_date - timedelta(days=3)),
    )
    tech_latest: dict[str, dict] = {}
    for sym, cd, raw in cur.fetchall():
        if sym not in tech_latest:
            ind = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
            tech_latest[sym] = ind

    for sym in symbols:
        tech = tech_latest.get(sym, {})
        vol60 = _get_val(tech, ["volatility_60d"])
        if vol60 is not None and vol60 > VOLATILITY_DANGER_THRESHOLD:
            flags.append(_flag_tuple(
                sym, "VOLATILITY_DANGER",
                f"Vol 60d={vol60:.2%} > {VOLATILITY_DANGER_THRESHOLD:.0%} — biến động cao",
                "technical_indicators",
            ))

    # 4. EARNINGS_QUALITY: Accrual ratio > 0.2 (OTHERS + REAL_ESTATE only)
    cur.execute(
        """SELECT DISTINCT ON (fs.symbol)
                  fs.symbol,
                  fs.data AS cf_data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type = 'CF'
             AND fs.frequency = 'quarterly'
           ORDER BY fs.symbol, fs.period_end DESC""",
        (symbols,),
    )
    cf_map: dict[str, dict] = {}
    for sym, raw in cur.fetchall():
        cf_map[sym] = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})

    cur.execute(
        """SELECT DISTINCT ON (fs.symbol)
                  fs.symbol,
                  fs.data AS inc_data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type = 'IS'
             AND fs.frequency = 'quarterly'
           ORDER BY fs.symbol, fs.period_end DESC""",
        (symbols,),
    )
    inc_map: dict[str, dict] = {}
    for sym, raw in cur.fetchall():
        inc_map[sym] = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})

    cur.execute(
        """SELECT DISTINCT ON (fs.symbol)
                  fs.symbol,
                  fs.data AS bs_data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type = 'BS'
             AND fs.frequency = 'quarterly'
           ORDER BY fs.symbol, fs.period_end DESC""",
        (symbols,),
    )
    bs_map: dict[str, dict] = {}
    for sym, raw in cur.fetchall():
        bs_map[sym] = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})

    for sym in symbols:
        group = sym_group.get(sym, OTHERS)
        if group not in (OTHERS, REAL_ESTATE):
            continue

        cf = cf_map.get(sym, {})
        inc = inc_map.get(sym, {})
        bs = bs_map.get(sym, {})

        cfo = _get_val(cf, ["lưu chuyển tiền thuần từ hoạt động kinh doanh", "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh"])
        ni = _get_val(inc, ["lợi nhuận sau thuế", "18_lợi_nhuận_sau_thuế"])
        ta = _get_val(bs, ["tổng cộng tài sản", "tổng_cộng_tài_sản"])

        if ni is not None and cfo is not None and ta is not None and ta > 0:
            accrual = (ni - cfo) / ta
            if accrual > EARNINGS_QUALITY_ACCRUAL_THRESHOLD:
                flags.append(_flag_tuple(
                    sym, "EARNINGS_QUALITY",
                    f"Accrual ratio={accrual:.2%} > {EARNINGS_QUALITY_ACCRUAL_THRESHOLD:.0%} — chất lượng lợi nhuận thấp",
                    "financial_statements",
                ))

    return flags


# ── Batch upsert ──────────────────────────────────────────────────────────

def upsert_flags(cur, flags: list[tuple]) -> int:
    """Batch upsert into risk_flags table. Returns count."""
    if not flags:
        return 0
    execute_values(
        cur,
        """INSERT INTO risk_flags
           (symbol, flag_type, effective_date, description, source_url, is_active)
           VALUES %s
           ON CONFLICT (symbol, flag_type, effective_date)
           DO UPDATE SET
               description = EXCLUDED.description,
               source_url = EXCLUDED.source_url,
               is_active = TRUE""",
        flags,
        page_size=500,
    )
    return len(flags)


# ── Main entry point ──────────────────────────────────────────────────────

def refresh_all(calc_date: Optional[date] = None) -> dict:
    """Full refresh: compute all flags for all HOSE symbols (MSCI 2019 optimized)."""
    if calc_date is None:
        calc_date = datetime.now(TZ_VN).date()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT symbol, industry FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        rows = cur.fetchall()
        symbols = [r[0] for r in rows]
        sym_industry = dict(rows)
        logger.info("Risk flags v2: %d symbols for %s", len(symbols), calc_date)

        # Build sector group map
        sym_group: dict[str, str] = {}
        for sym in symbols:
            sym_group[sym] = classify(sym_industry.get(sym), sym)

        # Detect bank symbols
        bank_symbols = _detect_banks(cur, symbols)

        # DELETE old, then recompute every flag from scratch each day
        cur.execute("DELETE FROM risk_flags")
        logger.info("  Deleted all %d old risk flags", cur.rowcount)
        conn.commit()

        # P0 (original flags 1-5)
        p0 = compute_p0_flags(cur, symbols, calc_date)
        n0 = upsert_flags(cur, p0)
        conn.commit()
        logger.info("  P0: %d flags", len(p0))

        # P1 (original flags 6-8)
        p1 = compute_p1_flags(cur, symbols, calc_date)
        n1 = upsert_flags(cur, p1)
        conn.commit()
        logger.info("  P1: %d flags", len(p1))

        # P2 (F-Score for OTHERS only, M-Score for OTHERS+RE)
        p2 = compute_p2_flags(cur, symbols, calc_date, bank_symbols)
        p2_filtered = []
        for flag in p2:
            sym = flag[0]
            ftype = flag[1]
            group = sym_group.get(sym, OTHERS)
            if ftype == "F_SCORE_FLAG" and group != OTHERS:
                continue
            if ftype == "M_SCORE_FLAG" and group not in (OTHERS, REAL_ESTATE):
                continue
            p2_filtered.append(flag)
        n2 = upsert_flags(cur, p2_filtered)
        conn.commit()
        logger.info("  P2: %d flags (%d filtered)", len(p2), len(p2_filtered))

        # P3 (new MSCI 2019 flags)
        p3 = compute_p3_flags(cur, symbols, calc_date)
        n3 = upsert_flags(cur, p3)
        conn.commit()
        logger.info("  P3: %d flags", len(p3))

        total = n0 + n1 + n2 + n3
        logger.info("Risk flags done: %d total flags", total)
        return {"flags": total, "symbols": len(symbols), "calc_date": str(calc_date)}
    finally:
        cur.close()
        conn.close()


def refresh_incremental() -> dict:
    """Incremental: same as full (idempotent via ON CONFLICT)."""
    return refresh_all()


def _detect_banks(cur, symbols: list[str]) -> set[str]:
    """Detect bank symbols from stocks table industry field."""
    cur.execute(
        "SELECT symbol FROM stocks WHERE industry ILIKE '%ngân hàng%' OR industry ILIKE '%bank%'"
    )
    return {r[0] for r in cur.fetchall()}


# ── Query interface (for agents) ──────────────────────────────────────────

def get_active_flags(symbol: str, cur=None) -> list[dict]:
    """Get all active risk flags for a symbol. Returns list of dicts."""
    own_conn = False
    if cur is None:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        own_conn = True
    try:
        cur.execute(
            """SELECT flag_type, effective_date, description, source_url
               FROM risk_flags
               WHERE symbol = %s AND is_active = TRUE
               ORDER BY
                   CASE WHEN flag_type IN ('CANH_BAO_TC','CHAM_BAO_TC') THEN 0 ELSE 1 END,
                   effective_date DESC""",
            (symbol,),
        )
        return [
            {"flag_type": r[0], "effective_date": str(r[1]), "description": r[2], "source": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        if own_conn:
            cur.close()
            conn.close()


def get_hard_blocked(symbol: str) -> bool:
    """Check if a symbol has active HARD flags that block BUY."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COUNT(*) FROM risk_flags
               WHERE symbol = %s AND flag_type = ANY(%s) AND is_active = TRUE""",
            (symbol, list(HARD_FLAGS)),
        )
        return cur.fetchone()[0] > 0
    finally:
        cur.close()
        conn.close()


def get_soft_flag_count(symbol: str) -> int:
    """Count active SOFT flags for a symbol."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT COUNT(*) FROM risk_flags
               WHERE symbol = %s AND flag_type = ANY(%s) AND is_active = TRUE""",
            (symbol, list(SOFT_FLAGS)),
        )
        return cur.fetchone()[0] or 0
    finally:
        cur.close()
        conn.close()
