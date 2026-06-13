"""Point-in-Time (PIT) Fundamentals Access Layer.

Mọi truy cập dữ liệu tài chính phải qua module này để đảm bảo
không có look-ahead bias. Bắt buộc dùng published_date.
"""
import logging
from datetime import date, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Conservative lag estimates for VN market (trading days)
# VN doanh nghiệp công bố BCTC quý chậm 20-45 ngày, báo cáo năm chậm 90 ngày
DEFAULT_LAG_QUARTERLY_DAYS = 45
DEFAULT_LAG_ANNUAL_DAYS = 90


def estimate_published_date(period_end: date, frequency: str = "quarterly") -> date:
    """Ước tính published_date nếu không có dữ liệu thật.

    Args:
        period_end: Ngày kết thúc kỳ báo cáo
        frequency: "quarterly" hoặc "yearly"

    Returns:
        published_date ước tính = period_end + lag
    """
    if frequency == "yearly":
        return period_end + timedelta(days=DEFAULT_LAG_ANNUAL_DAYS)
    return period_end + timedelta(days=DEFAULT_LAG_QUARTERLY_DAYS)


def get_fundamentals_at_date(
    symbol: str,
    as_of_date: date,
    conn,
) -> dict[str, Any]:
    """Lấy fundamental ratios PIT-safe.

    BẮT BUỘC: Chỉ trả về dữ liệu có published_date <= as_of_date.
    CẤM TUYỆT: Dùng period_end/ratio_date làm proxy cho published_date.

    Args:
        symbol: Mã cổ phiếu
        as_of_date: Ngày hiện tại trong quá khứ (backtest date)
        conn: DB connection

    Returns:
        Dict chứa các chỉ số tài chính mới nhất tại as_of_date,
        hoặc dict rỗng nếu không có dữ liệu.
    """
    query = """
        SELECT pe, pb, roe, roa, debt_equity, current_ratio,
               gross_margin, net_margin, fcf_yield, ev_ebitda,
               yoy_revenue_growth, yoy_earnings_growth,
               ratio_date, published_date
        FROM financial_ratios
        WHERE symbol = %s
          AND published_date <= %s
        ORDER BY published_date DESC
        LIMIT 1
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (symbol, as_of_date))
        row = cur.fetchone()
        cur.close()
        if row:
            columns = [
                "pe", "pb", "roe", "roa", "debt_equity", "current_ratio",
                "gross_margin", "net_margin", "fcf_yield", "ev_ebitda",
                "yoy_revenue_growth", "yoy_earnings_growth",
                "ratio_date", "published_date",
            ]
            result = dict(zip(columns, row))
            result["_pit_as_of"] = as_of_date.isoformat()
            return result
    except Exception as e:
        logger.warning("PIT query failed for %s at %s: %s", symbol, as_of_date, e)

    return {}


def get_financial_statements_pit(
    symbol: str,
    as_of_date: date,
    conn,
    statement_type: str = "IS",
) -> list[dict[str, Any]]:
    """Lấy báo cáo tài chính PIT-safe.

    Chỉ trả về các báo cáo đã được công bố (published_date <= as_of_date).
    """
    query = """
        SELECT period_end, statement_type, frequency, data,
               published_date, fetched_at
        FROM financial_statements
        WHERE symbol = %s
          AND statement_type = %s
          AND published_date <= %s
        ORDER BY period_end DESC
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (symbol, statement_type, as_of_date))
        rows = cur.fetchall()
        cur.close()
        columns = [
            "period_end", "statement_type", "frequency",
            "data", "published_date", "fetched_at",
        ]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.warning(
            "PIT stmt query failed for %s/%s at %s: %s",
            symbol, statement_type, as_of_date, e,
        )
        return []


def get_pit_factor_panel(
    symbols: list[str],
    as_of_date: date,
    conn,
) -> dict[str, dict[str, float]]:
    """Lấy factor scores PIT-safe cho nhiều mã.

    Returns:
        {symbol: {factor_name: value, ...}}
    """
    if not symbols:
        return {}

    placeholders = ",".join("%s" for _ in symbols)
    query = f"""
        SELECT DISTINCT ON (fs.symbol)
            fs.symbol,
            fs.score_date,
            fs.factor_details
        FROM factor_scores fs
        WHERE fs.symbol IN ({placeholders})
          AND fs.score_date <= %s
        ORDER BY fs.symbol, fs.score_date DESC
    """
    try:
        cur = conn.cursor()
        params = list(symbols) + [as_of_date]
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        result = {}
        for row in rows:
            symbol = row[0]
            details = row[2] or {}
            result[symbol] = details
        return result
    except Exception as e:
        logger.warning("PIT factor panel query failed at %s: %s", as_of_date, e)
        return {}


def add_published_date_to_ratios_table(cur) -> None:
    """Migration: add published_date column to financial_ratios table.

    Chạy một lần khi migrate DB. Nếu published_date NULL, set mặc định
    = ratio_date + 45 ngày.
    """
    cur.execute("""
        ALTER TABLE financial_ratios
        ADD COLUMN IF NOT EXISTS published_date DATE
    """)
    cur.execute("""
        UPDATE financial_ratios
        SET published_date = ratio_date + INTERVAL '45 days'
        WHERE published_date IS NULL
    """)


def add_published_date_to_statements_table(cur) -> None:
    """Migration: add published_date column to financial_statements table."""
    cur.execute("""
        ALTER TABLE financial_statements
        ADD COLUMN IF NOT EXISTS published_date DATE
    """)
    cur.execute("""
        UPDATE financial_statements
        SET published_date =
            CASE
                WHEN frequency = 'yearly' THEN period_end + INTERVAL '90 days'
                ELSE period_end + INTERVAL '45 days'
            END
        WHERE published_date IS NULL
    """)
