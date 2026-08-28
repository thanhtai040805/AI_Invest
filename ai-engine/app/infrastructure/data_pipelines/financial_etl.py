"""Financial Statements ETL — vnstock → financial_statements + financial_ratios tables.

Populates the two financial data tables that every other ETL step depends on but were
never wired. Run as part of daily ETL (step_financial_ratios). Idempotent.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
from psycopg2.extras import execute_values

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
RATE_LIMIT_DELAY = 4.0
BATCH_SIZE = 50

_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _period_to_date(period: str) -> Optional[date]:
    """Convert vnstock period label like '2024-Q1', '2025-Q4_1', or '2024' to end-of-period date."""
    if not period:
        return None
    m = re.match(r"(\d{4})-Q([1-4])(?:_\d+)?", period)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[q]
    m = re.match(r"(\d{4})", period)
    if m:
        return date(int(m.group(1)), 12, 31)
    return None


def _parse_dataframe(df, exclude_cols: set[str]) -> list[tuple[str, date, dict[str, Any]]]:
    """Parse a vnstock financial DataFrame into (period, period_end, data) tuples.

    Assumes df has columns: item, item_en, item_id, plus period columns.
    Returns one tuple per period column found.
    """
    if df is None or df.empty:
        return []
    period_cols = [c for c in df.columns if c not in exclude_cols]
    if not period_cols:
        return []

    rows: list[tuple[str, date, dict[str, Any]]] = []
    for period in period_cols:
        period_end = _period_to_date(period)
        if period_end is None:
            continue
        data: dict[str, Any] = {}
        for _, row in df.iterrows():
            item = str(row.get("item", "")).strip()
            val = row.get(period)
            if item and isinstance(val, (int, float)):
                data[item] = val
        if data:
            rows.append((period, period_end, data))
    return rows


from app.core.common import clean_nan as _clean_nan


def fetch_and_store_financials(symbol: str, cur) -> dict[str, Any]:
    """Fetch all financial statements for one symbol and upsert into DB."""
    from vnstock.api.financial import Finance

    f = Finance(symbol=symbol, source="VCI")
    exclude = {"item", "item_en", "item_id"}

    total = 0

    # Balance sheet
    try:
        bs = f.balance_sheet(freq="quarterly")
        bs_rows = _parse_dataframe(bs, exclude)
        for period, period_end, data in bs_rows:
            cur.execute(
                """INSERT INTO financial_statements
                   (symbol, period_end, statement_type, frequency, data, source)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, period_end, statement_type, frequency)
                   DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW()""",
                    (symbol, period_end, "BS", "quarterly", psycopg2.extras.Json(_clean_nan(data)), "vnstock"),
            )
            total += 1
    except Exception as e:
        cur.connection.rollback()
        logger.warning("BS fetch failed for %s: %s", symbol, e)

    # Income statement
    try:
        inc = f.income_statement(freq="quarterly")
        inc_rows = _parse_dataframe(inc, exclude)
        for period, period_end, data in inc_rows:
            cur.execute(
                """INSERT INTO financial_statements
                   (symbol, period_end, statement_type, frequency, data, source)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, period_end, statement_type, frequency)
                   DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW()""",
                    (symbol, period_end, "IS", "quarterly", psycopg2.extras.Json(_clean_nan(data)), "vnstock"),
            )
            total += 1
    except Exception as e:
        cur.connection.rollback()
        logger.warning("IS fetch failed for %s: %s", symbol, e)

    # Cash flow
    try:
        cf = f.cash_flow(freq="quarterly")
        cf_rows = _parse_dataframe(cf, exclude)
        for period, period_end, data in cf_rows:
            cur.execute(
                """INSERT INTO financial_statements
                   (symbol, period_end, statement_type, frequency, data, source)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, period_end, statement_type, frequency)
                   DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW()""",
                    (symbol, period_end, "CF", "quarterly", psycopg2.extras.Json(_clean_nan(data)), "vnstock"),
            )
            total += 1
    except Exception as e:
        cur.connection.rollback()
        logger.warning("CF fetch failed for %s: %s", symbol, e)

    # Ratios
    try:
        ratios = f.ratio(freq="quarterly")
        rat_rows = _parse_dataframe(ratios, exclude)
        for period, period_end, data in rat_rows:
            cur.execute(
                """INSERT INTO financial_statements
                   (symbol, period_end, statement_type, frequency, data, source)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, period_end, statement_type, frequency)
                   DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW()""",
                    (symbol, period_end, "ratios", "quarterly", psycopg2.extras.Json(_clean_nan(data)), "vnstock"),
            )
            total += 1
    except Exception as e:
        cur.connection.rollback()
        logger.warning("Ratios fetch failed for %s: %s", symbol, e)

    # Compute and store derived financial_ratios
    _store_derived_ratios(symbol, cur)

    return {"symbol": symbol, "rows": total}


def _extract_value(fs_rows: list, keywords: list[str], default: Optional[float] = None) -> Optional[float]:
    """Extract a numeric value from financial statement rows by keyword.
    Accepts either DB tuples (period_end, stmt_type, data) or bare dicts."""
    for row in fs_rows:
        if isinstance(row, (tuple, list)) and len(row) >= 3:
            data = row[2]  # (period_end, stmt_type, data)
        elif isinstance(row, (tuple, list)) and len(row) >= 1:
            data = row[0]
        else:
            data = row
        if isinstance(data, dict):
            for k, v in data.items():
                if any(kw in k for kw in keywords):
                    if isinstance(v, (int, float)):
                        return float(v)
        elif isinstance(data, str):
            import json
            try:
                d = json.loads(data)
                for k, v in d.items():
                    if any(kw in k for kw in keywords):
                        if isinstance(v, (int, float)):
                            return float(v)
            except (json.JSONDecodeError, TypeError):
                pass
    return default


def _store_derived_ratios(symbol: str, cur) -> None:
    """Compute and store financial_ratios for ALL available periods (backfill)."""
    cur.execute(
        """SELECT period_end, statement_type, data
           FROM financial_statements
           WHERE symbol = %s
           ORDER BY period_end DESC""",
        (symbol,),
    )
    stmt_rows = cur.fetchall()
    if not stmt_rows:
        return

    import json

    # Group data by period_end
    periods: dict[date, dict[str, dict]] = {}
    for pe, st, raw in stmt_rows:
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        periods.setdefault(pe, {})[st] = data

    sorted_periods = sorted(periods.keys(), reverse=True)

    for idx, pe in enumerate(sorted_periods):
        bs_data = periods[pe].get("BS", {})
        inc_data = periods[pe].get("IS", {})
        cf_data = periods[pe].get("CF", {})
        rat_data = periods[pe].get("ratios", {})

        if not bs_data or not inc_data:
            continue

        # Pull ratios directly from vnstock ratios data if available
        vn_pe = _extract_value([rat_data], ["P/E", "Chỉ số P/E"])
        vn_pb = _extract_value([rat_data], ["P/B", "Chỉ số P/B"])
        vn_roe = _extract_value([rat_data], ["ROE"])
        vn_roa = _extract_value([rat_data], ["ROA"])
        vn_gm = _extract_value([rat_data], ["lợi nhuận gộp", "Gross margin", "gộp biên"])
        vn_nm = _extract_value([rat_data], ["lợi nhuận ròng", "Net margin", "sinh lợi trên doanh thu"])

        # Compute derived values from raw statements
        assets = _extract_value([bs_data], ["TỔNG CỘNG TÀI SẢN", "Tổng cộng tài sản"])
        liab = _extract_value([bs_data], ["TỔNG NỢ PHẢI TRẢ", "Tổng nợ phải trả"])
        equity = _extract_value([bs_data], ["VỐN CHỦ SỞ HỮU", "Vốn chủ sở hữu"])
        if equity is None and assets is not None and liab is not None:
            equity = assets - liab

        revenue = _extract_value([inc_data], ["Doanh thu thuần", "Thu nhập lãi thuần"])
        cogs = _extract_value([inc_data], ["Giá vốn hàng bán"])
        ni = _extract_value([inc_data], ["Lợi nhuận sau thuế"])
        cfo = _extract_value([cf_data], ["Lưu chuyển tiền thuần từ hoạt động kinh doanh"])
        capex_raw = _extract_value([cf_data], ["Tiền chi để mua sắm, xây dựng TSCĐ"])
        capex = abs(capex_raw) if capex_raw is not None else None

        # Fallback computed margins (from raw IS) when vnstock ratios not available
        gross_margin_fb = None
        if revenue is not None and cogs is not None and revenue != 0:
            gross_margin_fb = (revenue - cogs) / revenue
        net_margin_fb = None
        if ni is not None and revenue is not None and revenue != 0:
            net_margin_fb = ni / revenue

        final_gm = vn_gm if vn_gm is not None else gross_margin_fb
        final_nm = vn_nm if vn_nm is not None else net_margin_fb

        fcf = None
        if cfo is not None and capex is not None:
            fcf = cfo - capex

        mcap = None
        if vn_pe is not None and ni is not None:
            mcap = vn_pe * ni

        debt_equity = _extract_value([rat_data], ["Nợ trên Vốn", "Debt/Equity", "D/E"])
        if debt_equity is None and liab is not None and equity is not None and equity != 0:
            debt_equity = liab / equity

        current_ratio = _extract_value([rat_data], ["thanh toán hiện hành", "Current ratio"])
        if current_ratio is None:
            ca = _extract_value([bs_data], ["Tài sản ngắn hạn", "Ngắn hạn"])
            cl = _extract_value([bs_data], ["Nợ ngắn hạn"])
            if ca is not None and cl is not None and cl != 0:
                current_ratio = ca / cl

        fcf_yield = None
        if fcf is not None and mcap is not None and mcap != 0:
            fcf_yield = fcf / mcap

        ev_ebitda = None
        if mcap is not None and liab is not None:
            cash = _extract_value([bs_data], ["Tiền và các khoản tương đương tiền"])
            ev = mcap + liab - (cash or 0)
            ebitda = _extract_value([inc_data], ["EBITDA"])
            if ebitda is not None and ebitda != 0:
                ev_ebitda = ev / ebitda

        # YoY growth: compare with same period 4 quarters back
        tgt = pe - timedelta(days=365)
        prev_inc = None
        for other_pe in sorted_periods:
            if other_pe <= tgt:
                prev_inc = periods[other_pe].get("IS")
                break
        yoy_rev = None
        yoy_ni = None
        if prev_inc:
            rev_prev = _extract_value([prev_inc], ["Doanh thu thuần", "Thu nhập lãi thuần"])
            ni_prev = _extract_value([prev_inc], ["Lợi nhuận sau thuế"])
            if revenue is not None and rev_prev is not None and rev_prev != 0:
                yoy_rev = (revenue - rev_prev) / abs(rev_prev)
            if ni is not None and ni_prev is not None and ni_prev != 0:
                yoy_ni = (ni - ni_prev) / abs(ni_prev)

        cur.execute(
            """INSERT INTO financial_ratios
               (symbol, ratio_date, pe, pb, roe, roa, debt_equity, current_ratio,
                gross_margin, net_margin, fcf_yield, ev_ebitda,
                yoy_revenue_growth, yoy_earnings_growth)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (symbol, ratio_date)
               DO UPDATE SET
                   pe = EXCLUDED.pe, pb = EXCLUDED.pb,
                   roe = EXCLUDED.roe, roa = EXCLUDED.roa,
                   debt_equity = EXCLUDED.debt_equity,
                   current_ratio = EXCLUDED.current_ratio,
                   gross_margin = EXCLUDED.gross_margin,
                   net_margin = EXCLUDED.net_margin,
                   fcf_yield = EXCLUDED.fcf_yield,
                   ev_ebitda = EXCLUDED.ev_ebitda,
                   yoy_revenue_growth = EXCLUDED.yoy_revenue_growth,
                   yoy_earnings_growth = EXCLUDED.yoy_earnings_growth,
                   updated_at = NOW()""",
               (symbol, pe, vn_pe, vn_pb, vn_roe, vn_roa, debt_equity, current_ratio,
                final_gm, final_nm, fcf_yield, ev_ebitda, yoy_rev, yoy_ni),
        )


def refresh_all() -> dict:
    """Full refresh: fetch financials for all HOSE symbols."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        logger.info("Financial ETL: %d symbols", len(symbols))

        total_rows = 0
        errors = 0
        for idx, sym in enumerate(symbols):
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)
                time.sleep(0.5)
            try:
                result = fetch_and_store_financials(sym, cur)
                total_rows += result["rows"]
                time.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                conn.rollback()
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
                time.sleep(RATE_LIMIT_DELAY * 2)
        conn.commit()

        logger.info("Financial ETL done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()


def refresh_incremental() -> dict:
    """Incremental: only fetch symbols missing recent financial data (< 30 days old)."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT fs.symbol
               FROM financial_statements fs
               WHERE fs.period_end >= %s
                 AND fs.statement_type = 'balance_sheet'""",
            (date.today() - timedelta(days=90),),
        )
        recent_symbols = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]
        stale_symbols = [s for s in all_symbols if s not in recent_symbols]
        logger.info("Financial ETL incremental: %d stale symbols", len(stale_symbols))

        if not stale_symbols:
            return {"rows": 0, "symbols": 0, "note": "all symbols up to date"}

        total_rows = 0
        errors = 0
        for idx, sym in enumerate(stale_symbols):
            try:
                result = fetch_and_store_financials(sym, cur)
                total_rows += result["rows"]
                time.sleep(RATE_LIMIT_DELAY)
            except Exception as e:
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
        conn.commit()

        logger.info("Financial ETL incremental done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(stale_symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()
