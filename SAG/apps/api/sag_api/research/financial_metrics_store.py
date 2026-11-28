"""Zero-Duplication Core PG Read-Only Catalog & PIT Financial Screener Bridge.

Architecture (Option 1 — Single Source of Truth):
- **Core PostgreSQL (`aiinvest`)** is queried strictly **read-only**
  (`default_transaction_read_only = on`) for normalized issuer profiles (`stocks` +
  `instrument_master`), market valuation multiples (`market_cap`, `pe`, `pb`, `ev_ebitda`,
  `fcf_yield`), Point-in-Time ratios (`financial_ratios`), and yearly financial statements
  (`financial_statements`).
- **SAG PostgreSQL (`aiinvest_sag`)** stores ZERO duplicate profile or metric-snapshot
  tables. Instead, it contributes active document scope (`documents`), strict PIT version
  windows (`document_versions`), and **smart on-demand overlay** from `VERIFIED` 2D PDF
  tables (`sag_extracted_tables`) ONLY for tickers missing from Core DB, lacking Core
  statements, or carrying a newer fiscal year in an uploaded filing.
- **Offline / No-Core-DB Fallback:** When `SAG_CORE_DATABASE_URL` is unset or Core PG is
  unreachable, catalog and screening degrade cleanly to SAG-local `KgNode` + `VERIFIED`
  PDF tables (never logging connection strings or credentials).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.research.calculator import (
    CalculatorError,
)
from sag_api.research.calculator import (
    cagr as calc_cagr,
)
from sag_api.research.calculator import (
    debt_to_equity as calc_debt_to_equity,
)
from sag_api.research.calculator import (
    margin as calc_margin,
)
from sag_api.research.calculator import (
    roa as calc_roa,
)
from sag_api.research.calculator import (
    roe as calc_roe,
)
from sag_api.research.calculator import (
    safe_div as calc_safe_div,
)
from sag_api.research.calculator import (
    yoy as calc_yoy,
)

log = logging.getLogger("sag_api.financial_metrics_store")

_MAX_VALID_TO_UTC = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

VALUATION_METRIC_COLS: frozenset[str] = frozenset(
    {
        "market_cap",
        "pe",
        "pb",
        "ev_ebitda",
        "fcf_yield",
    }
)

MONETARY_METRIC_COLS: frozenset[str] = frozenset(
    {
        "net_revenue",
        "gross_profit",
        "operating_profit",
        "net_income",
        "net_income_parent",
        "total_assets",
        "current_assets",
        "cash_and_st_investments",
        "inventory",
        "receivables",
        "total_liabilities",
        "current_liabilities",
        "short_term_debt",
        "long_term_debt",
        "total_debt",
        "equity",
        "cfo",
        "capex",
        "fcf",
    }
)

RATIO_METRIC_COLS: frozenset[str] = frozenset(
    {
        "gross_margin",
        "net_margin",
        "roe",
        "roa",
        "debt_to_equity",
        "liabilities_to_equity",
        "net_debt_to_equity",
        "current_ratio",
        "cash_to_assets",
        "cfo_to_net_income",
        "revenue_yoy",
        "net_income_yoy",
        "revenue_cagr_3y",
    }
)

ALLOWED_SCREEN_METRICS: frozenset[str] = (
    VALUATION_METRIC_COLS | MONETARY_METRIC_COLS | RATIO_METRIC_COLS
)

_ALL_SNAPSHOT_METRIC_FIELDS: tuple[str, ...] = tuple(sorted(ALLOWED_SCREEN_METRICS))

_STATEMENT_DERIVED_METRICS: frozenset[str] = MONETARY_METRIC_COLS | frozenset(
    {
        "debt_to_equity",
        "net_debt_to_equity",
        "cash_to_assets",
        "cfo_to_net_income",
        "revenue_cagr_3y",
    }
)

# ONLY percentage ratios may undergo `/ 100.0` scale normalization; NEVER divide multiples (`pe`, `pb`, `ev_ebitda`,
# `debt_equity`, `current_ratio`) or `market_cap`.
_CORE_PERCENTAGE_RATIO_COLS: tuple[str, ...] = (
    "gross_margin",
    "net_margin",
    "roe",
    "roa",
    "yoy_revenue_growth",
    "yoy_earnings_growth",
    "fcf_yield",
)

_CORE_POOLS: dict[tuple[str, int], Any] = {}

_PLAN_HDR_RE = re.compile(
    r"(?:kế\s*hoạch|mục\s*tiêu|chỉ\s*tiêu|plan|target|budget)",
    re.IGNORECASE | re.UNICODE,
)
_YEAR_IN_TEXT_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_PRIMARY_STMT_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|tình\s+hình\s+tài\s+chính|báo\s+cáo\s+kết\s+quả|"
    r"kết\s+quả\s+(?:hoạt\s+động\s+)?kinh\s+doanh|lưu\s+chuyển\s+tiền\s+tệ|"
    r"\bb0[1-3]\b|cđkt|kqkd|lctt|balance\s+sheet|income\s+statement|cash\s+flow)",
    re.IGNORECASE | re.UNICODE,
)
_SUB_NOTE_RE = re.compile(
    r"(?:thuyết\s+minh\s+(?:các\s+)?báo\s+cáo|thuyết\s+minh\s+bctc|"
    r"notes\s+to\s+(?:the\s+)?financial\s+statements)",
    re.IGNORECASE | re.UNICODE,
)

# (field_key, include_terms_ordered, exclude_terms)
_METRIC_ROW_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "net_revenue",
        (
            "doanh thu thuần về bán hàng và cung cấp dịch vụ",
            "doanh thu thuần",
            "thu nhập lãi thuần",
            "tổng doanh thu hoạt động",
            "is net revenue",
            "is net interest income",
            "net revenue",
            "net sales",
        ),
        ("giảm trừ", "tài chính", "khác", "công ty con", "nội bộ", "chưa thực hiện"),
    ),
    (
        "gross_profit",
        ("lợi nhuận gộp về bán hàng và cung cấp dịch vụ", "lợi nhuận gộp", "is gross profit", "gross profit"),
        ("biên", "tỷ suất", "%"),
    ),
    (
        "operating_profit",
        (
            "lợi nhuận thuần từ hoạt động kinh doanh",
            "lợi nhuận từ hoạt động kinh doanh",
            "is operating profit",
            "operating profit",
        ),
        ("khác", "trước thay đổi"),
    ),
    (
        "net_income_parent",
        (
            "lợi nhuận sau thuế của cổ đông của công ty mẹ",
            "lợi nhuận sau thuế của cổ đông công ty mẹ",
            "lợi nhuận sau thuế của công ty mẹ",
            "is profit after tax for shareholders of parent company",
            "profit attributable to owners of the parent",
        ),
        ("cơ bản trên cổ phiếu", "trên cổ phiếu", "không kiểm soát"),
    ),
    (
        "net_income",
        (
            "lợi nhuận sau thuế thu nhập doanh nghiệp",
            "lợi nhuận sau thuế tndn",
            "tổng lợi nhuận sau thuế",
            "lợi nhuận sau thuế",
            "is net profit after tax",
            "is net profit",
            "profit after tax",
            "net profit",
        ),
        ("chưa phân phối", "lũy kế", "không kiểm soát", "cơ bản trên cổ phiếu", "suy giảm", "trên cổ phiếu"),
    ),
    (
        "total_assets",
        ("tổng cộng tài sản", "tổng tài sản có", "tổng tài sản", "bs total assets", "total assets"),
        ("ngoài bảng", "dài hạn khác", "ngắn hạn khác", "thuê tài chính"),
    ),
    (
        "current_assets",
        (
            "a. tài sản ngắn hạn",
            "tài sản ngắn hạn",
            "bs short term assets",
            "bs current assets",
            "total current assets",
            "current assets",
        ),
        ("tài sản ngắn hạn khác", "phải thu", "hàng tồn kho", "tiền và", "đầu tư tài chính"),
    ),
    (
        "_cash_only",
        (
            "tiền và các khoản tương đương tiền",
            "tiền và tương đương tiền",
            "bs cash and cash equivalents",
            "bs cash",
            "cash and cash equivalents",
        ),
        ("đầu kỳ", "cuối kỳ", "ảnh hưởng của thay đổi", "lưu chuyển"),
    ),
    (
        "_st_investments",
        (
            "đầu tư tài chính ngắn hạn",
            "bs short term investments",
            "bs short term financial investments",
            "short-term financial investments",
            "đầu tư nắm giữ đến ngày đáo hạn",
        ),
        ("dài hạn", "dự phòng", "giảm giá", "chi tiết"),
    ),
    (
        "inventory",
        ("hàng tồn kho", "bs inventories", "inventories", "inventory"),
        ("dự phòng giảm giá", "dài hạn", "tăng, giảm", "tăng/giảm", "lưu chuyển"),
    ),
    (
        "receivables",
        (
            "các khoản phải thu ngắn hạn",
            "phải thu ngắn hạn",
            "bs short term receivables",
            "short-term receivables",
            "trade receivables",
        ),
        ("dài hạn", "khó đòi", "dự phòng", "công ty con", "bên liên quan", "tăng, giảm"),
    ),
    (
        "total_liabilities",
        (
            "tổng cộng nợ phải trả",
            "tổng nợ phải trả",
            "a. nợ phải trả",
            "c. nợ phải trả",
            "nợ phải trả",
            "bs total liabilities",
            "total liabilities",
        ),
        (
            "và vốn chủ",
            "nguồn vốn",
            "nợ ngắn hạn",
            "nợ dài hạn",
            "phải trả người bán",
            "người mua trả tiền",
            "khác",
            "dự phòng",
            "thuế thu nhập",
        ),
    ),
    (
        "current_liabilities",
        (
            "i. nợ ngắn hạn",
            "nợ ngắn hạn",
            "bs short term liabilities",
            "bs current liabilities",
            "total current liabilities",
            "current liabilities",
        ),
        ("khác", "phải trả", "dự phòng", "tài sản ngắn hạn", "vay và nợ"),
    ),
    (
        "short_term_debt",
        (
            "vay và nợ thuê tài chính ngắn hạn",
            "vay ngắn hạn",
            "bs short term debt",
            "bs short term borrowings",
            "short-term borrowings",
            "short-term debt",
        ),
        ("lãi vay", "chi phí lãi vay", "trả nợ gốc"),
    ),
    (
        "long_term_debt",
        (
            "vay và nợ thuê tài chính dài hạn",
            "vay dài hạn",
            "bs long term debt",
            "bs long term borrowings",
            "long-term borrowings",
            "long-term debt",
        ),
        ("lãi vay", "chi phí lãi vay", "trả nợ gốc"),
    ),
    (
        "total_debt",
        ("tổng nợ vay", "tổng vay và nợ thuê tài chính", "total debt"),
        ("dài hạn khác", "trả nợ gốc"),
    ),
    (
        "equity",
        (
            "d. vốn chủ sở hữu",
            "b. vốn chủ sở hữu",
            "i. vốn chủ sở hữu",
            "vốn chủ sở hữu",
            "vốn và các quỹ",
            "bs equity",
            "bs total equity",
            "total equity",
            "owners' equity",
        ),
        (
            "nợ phải trả và vốn chủ",
            "nguồn vốn",
            "vốn góp",
            "chưa phân phối",
            "thặng dư",
            "quỹ khen thưởng",
            "quỹ dự trữ",
            "quỹ đầu tư",
            "lợi ích cổ đông",
            "trên vốn chủ",
            "bằng vốn chủ",
        ),
    ),
    (
        "cfo",
        (
            "lưu chuyển tiền thuần từ hoạt động kinh doanh",
            "tiền thuần thu được từ hoạt động kinh doanh",
            "cf net cash flows from operating activities",
            "cf operating cash flow",
            "net cash flows from operating activities",
            "net cash from operating activities",
        ),
        ("trước thay đổi vốn lưu động", "đầu tư", "tài chính"),
    ),
    (
        "capex",
        (
            "tiền chi để mua sắm, xây dựng tscđ",
            "tiền chi mua sắm, xây dựng tscđ",
            "mua sắm tài sản cố định",
            "chi đầu tư tài sản cố định",
            "cf payments for fixed assets",
            "purchase of property, plant and equipment",
            "purchases of property, plant and equipment",
        ),
        ("thanh lý", "nhượng bán", "thu hồi"),
    ),
)

_NORMALIZED_STMT_SPECS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()


def strip_vn_accents(text: str) -> str:
    """Normalize Vietnamese/English strings by stripping diacritics and collapsing whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", str(text)).replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", s)
    ascii_chars = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", ascii_chars).strip().lower()


_NORMALIZED_STMT_SPECS = tuple(
    (
        field_key,
        tuple(strip_vn_accents(t) for t in inc_terms),
        tuple(strip_vn_accents(t) for t in exc_terms),
    )
    for field_key, inc_terms, exc_terms in _METRIC_ROW_SPECS
)


def _strip_boilerplate_company_name(name: str) -> str:
    try:
        from sag_api.research.kg_true_peer import strip_corporate_boilerplate

        return strip_corporate_boilerplate(name)
    except Exception:  # noqa: BLE001
        return (name or "").strip()


def _build_aliases(ticker: str, company_name: str | None) -> list[str]:
    clean_name = (company_name or "").strip()
    if not clean_name or clean_name.upper() == ticker.upper():
        return []
    aliases: list[str] = [clean_name]
    short_name = _strip_boilerplate_company_name(clean_name)
    if short_name and short_name.upper() != ticker.upper() and short_name not in aliases:
        aliases.append(short_name)
    return aliases


def _coerce_utc(val: Any, *, default: datetime | None = None) -> datetime | None:
    if val is None:
        return default
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, tzinfo=UTC)
    raw = str(val).strip()
    if not raw:
        return default
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        try:
            d = date.fromisoformat(raw[:10])
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        except ValueError:
            return default


def _coerce_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    raw = str(val).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _finite_float(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        fv = float(val)
        return fv if math.isfinite(fv) else None
    except (TypeError, ValueError):
        return None


def _safe_calc(fn: Any, *args: float) -> float | None:
    try:
        res = fn(*args)
        fv = _finite_float(res)
        return round(fv, 6) if fv is not None else None
    except (CalculatorError, ZeroDivisionError, ValueError, TypeError):
        return None


# ── Read-Only Core PG Connection & Protocol ─────────────────────────────────


def resolve_core_database_url() -> str:
    """Resolve `SAG_CORE_DATABASE_URL` from env/settings (never logs credentials)."""
    from sag_api.core.config import settings

    return (os.environ.get("SAG_CORE_DATABASE_URL") or settings.core_database_url or "").strip()


def _normalize_pg_dsn(url: str) -> str:
    """Convert SQLAlchemy asyncpg/psycopg URL into raw `postgresql://` DSN for asyncpg."""
    dsn = (url or "").strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix) :]
    return dsn


def _norm_ticker_param(tickers: Sequence[str] | None) -> list[str] | None:
    if not tickers:
        return None
    normed = sorted({str(t).strip().upper() for t in tickers if t and str(t).strip()})
    return normed or None


async def close_core_metrics_pools() -> None:
    """Close cached Core DB read-only connection pools gracefully (called on app shutdown)."""
    try:
        running_loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        running_loop_id = None

    items = list(_CORE_POOLS.items())
    for key, pool in items:
        if running_loop_id is not None and key[1] != running_loop_id:
            continue
        _CORE_POOLS.pop(key, None)
        try:
            await pool.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("Core PG pool close warning (%s)", type(exc).__name__)


class CoreMetricsReaderProtocol(Protocol):
    """Strict read-only interface to AIInvest Core PostgreSQL (`aiinvest`)."""

    async def fetch_issuer_profiles(
        self,
        tickers: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    async def fetch_pit_financial_ratios(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 7,
    ) -> list[dict[str, Any]]:
        ...

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 5,
        prefer_annual: bool = True,
        statement_types: Sequence[str] = ("BS", "IS", "CF"),
    ) -> list[dict[str, Any]]:
        ...

    async def fetch_pit_market_caps(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, float]:
        ...

    async def fetch_pit_market_and_macro_tape(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        ...


class AsyncPGCoreMetricsReader:
    """Pooled, strictly read-only (`default_transaction_read_only = on`) Core PG reader."""

    def __init__(
        self,
        core_database_url: str,
        *,
        pool_factory: Callable[..., Awaitable[Any]] | None = None,
        timeout: float = 5.0,
        command_timeout: float = 20.0,
    ) -> None:
        self._dsn = _normalize_pg_dsn(core_database_url)
        if not self._dsn:
            raise ValueError("SAG_CORE_DATABASE_URL is required for AsyncPGCoreMetricsReader")
        self._dsn_digest = hashlib.sha256(self._dsn.encode("utf-8")).hexdigest()[:16]
        self._pool_factory = pool_factory
        self._injected_pool: Any | None = None
        self._timeout = float(timeout)
        self._command_timeout = float(command_timeout)

    async def _get_pool(self) -> Any:
        if self._pool_factory is not None:
            if self._injected_pool is None:
                self._injected_pool = await self._pool_factory(
                    self._dsn,
                    min_size=0,
                    max_size=4,
                    timeout=self._timeout,
                    command_timeout=self._command_timeout,
                    server_settings={"default_transaction_read_only": "on"},
                )
            return self._injected_pool

        import asyncpg

        loop_id = id(asyncio.get_running_loop())
        pool_key = (self._dsn_digest, loop_id)
        pool = _CORE_POOLS.get(pool_key)
        if pool is None or getattr(pool, "_closed", False):
            pool = await asyncpg.create_pool(
                self._dsn,
                min_size=0,
                max_size=4,
                timeout=self._timeout,
                command_timeout=self._command_timeout,
                server_settings={"default_transaction_read_only": "on"},
            )
            _CORE_POOLS[pool_key] = pool
        return pool

    async def close(self) -> None:
        if self._injected_pool is not None:
            pool = self._injected_pool
            self._injected_pool = None
            await pool.close()

    async def fetch_issuer_profiles(
        self,
        tickers: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        norm_tks = _norm_ticker_param(tickers)
        pool = await self._get_pool()
        sql = """
            SELECT UPPER(TRIM(s.symbol)) AS ticker,
                   COALESCE(NULLIF(TRIM(im.name), ''), NULLIF(TRIM(s.name), ''), UPPER(TRIM(s.symbol))) AS company_name,
                   NULLIF(TRIM(s.sector), '') AS sector,
                   NULLIF(TRIM(s.industry), '') AS industry,
                   COALESCE(NULLIF(TRIM(s.exchange), ''), 'HOSE') AS exchange,
                   s.market_cap,
                   s.trading_status,
                   s.audit_opinion,
                   s.audit_year
            FROM stocks s
            LEFT JOIN instrument_master im ON UPPER(TRIM(im.symbol)) = UPPER(TRIM(s.symbol))
            WHERE ($1::text[] IS NULL OR UPPER(TRIM(s.symbol)) = ANY($1::text[]))
            ORDER BY ticker ASC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, norm_tks)
        return [dict(r) for r in rows]

    async def fetch_pit_financial_ratios(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 7,
    ) -> list[dict[str, Any]]:
        norm_tks = _norm_ticker_param(tickers)
        min_date = as_of_date - timedelta(days=365 * max(1, lookback_years) + 30)
        pool = await self._get_pool()
        sql = """
            SELECT UPPER(TRIM(symbol)) AS symbol,
                   ratio_date,
                   LOWER(TRIM(frequency)) AS frequency,
                   pe,
                   pb,
                   roe,
                   roa,
                   debt_equity,
                   current_ratio,
                   gross_margin,
                   net_margin,
                   fcf_yield,
                   ev_ebitda,
                   yoy_revenue_growth,
                   yoy_earnings_growth,
                   published_date,
                   CASE
                     WHEN published_date IS NOT NULL
                          AND published_date >= (ratio_date + INTERVAL '10 days')::date
                          AND published_date <= (ratio_date + INTERVAL '180 days')::date
                     THEN published_date
                     WHEN LOWER(TRIM(frequency)) = 'yearly'
                     THEN (ratio_date + INTERVAL '90 days')::date
                     ELSE (ratio_date + INTERVAL '45 days')::date
                   END AS effective_published_date
            FROM financial_ratios
            WHERE LOWER(TRIM(frequency)) IN ('quarterly', 'yearly')
              AND ratio_date <= $1
              AND ratio_date >= $2
              AND (
                CASE
                  WHEN published_date IS NOT NULL
                       AND published_date >= (ratio_date + INTERVAL '10 days')::date
                       AND published_date <= (ratio_date + INTERVAL '180 days')::date
                  THEN published_date
                  WHEN LOWER(TRIM(frequency)) = 'yearly'
                  THEN (ratio_date + INTERVAL '90 days')::date
                  ELSE (ratio_date + INTERVAL '45 days')::date
                END
              ) <= $1
              AND ($3::text[] IS NULL OR UPPER(TRIM(symbol)) = ANY($3::text[]))
            ORDER BY symbol ASC, ratio_date DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, as_of_date, min_date, norm_tks)
        return [dict(r) for r in rows]

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 5,
        prefer_annual: bool = True,
        statement_types: Sequence[str] = ("BS", "IS", "CF"),
    ) -> list[dict[str, Any]]:
        norm_tks = _norm_ticker_param(tickers)
        want_set = set(norm_tks) if norm_tks is not None else None
        sql_tks = None if (norm_tks is not None and len(norm_tks) >= 300) else norm_tks
        raw_sts = {str(s).strip() for s in statement_types if str(s).strip()}
        expanded_sts = sorted({s.upper() for s in raw_sts} | {s.lower() for s in raw_sts})
        min_date = as_of_date - timedelta(days=365 * max(1, lookback_years) + 30)
        freq_clause = (
            "frequency = 'yearly'"
            if prefer_annual
            else "frequency IN ('yearly', 'quarterly')"
        )
        pool = await self._get_pool()
        sql = f"""
            SELECT UPPER(TRIM(symbol)) AS symbol,
                   period_end,
                   UPPER(TRIM(statement_type)) AS statement_type,
                   LOWER(TRIM(frequency)) AS frequency,
                   data,
                   LOWER(TRIM(source)) AS source,
                   published_date,
                   CASE
                     WHEN published_date IS NOT NULL
                          AND published_date >= (period_end + INTERVAL '10 days')::date
                          AND published_date <= (period_end + INTERVAL '180 days')::date
                     THEN published_date
                     WHEN LOWER(TRIM(frequency)) IN ('yearly', 'annual')
                     THEN (period_end + INTERVAL '90 days')::date
                     ELSE (period_end + INTERVAL '45 days')::date
                   END AS effective_published_date
            FROM financial_statements
            WHERE {freq_clause}
              AND statement_type = ANY($4::text[])
              AND LOWER(COALESCE(source, '')) <> 'cafef'
              AND period_end <= $1
              AND period_end >= $2
              AND (
                CASE
                  WHEN published_date IS NOT NULL
                       AND published_date >= (period_end + INTERVAL '10 days')::date
                       AND published_date <= (period_end + INTERVAL '180 days')::date
                  THEN published_date
                  WHEN LOWER(TRIM(frequency)) IN ('yearly', 'annual')
                  THEN (period_end + INTERVAL '90 days')::date
                  ELSE (period_end + INTERVAL '45 days')::date
                END
              ) <= $1
              AND ($3::text[] IS NULL OR symbol = ANY($3::text[]))
            ORDER BY symbol ASC, period_end DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, as_of_date, min_date, sql_tks, expanded_sts)
        if want_set is not None and sql_tks is None:
            return [dict(r) for r in rows if str(r.get("symbol") or "").strip().upper() in want_set]
        return [dict(r) for r in rows]

    async def fetch_pit_market_caps(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, float]:
        """Compute strictly PIT-safe `market_cap` (in VND) as of `<= as_of_date` across all HOSE tickers.

        Core PG's `market_data_daily.market_cap` is unpopulated (0 positive rows) and `stocks.market_cap`
        only populates 1/411 tickers. We deterministically derive PIT `market_cap` in VND from:
        1. Latest `market_data_daily.close_unadj * 1000.0` (`date <= as_of_date`, 365d lookback for illiquid tail),
           preferring positive `market_data_daily.market_cap` when present.
        2. PIT-eligible `BS` shares (`bs_off_outstanding_shares` or `charter_capital / 10,000` par VND, including
           `vnstock_vci` `'Common shares'`), materialized via 1-row-per-ticker `DISTINCT ON (symbol)` before
           decompressing JSONB `data`.
        3. Optional treasury/in-transit refinement from `ratios.rt_value_outstanding_shares` STRICTLY guarded by
           `NOT EXISTS` post-`trade_date` share-count `corporate_actions` (`SPLIT`, `STOCK_DIVIDEND`, `RIGHTS`)
           so vendor post-split backfills never leak future share counts (`Gate G4 = 0%`).
        """
        norm_tks = _norm_ticker_param(tickers)
        want_set = set(norm_tks) if norm_tks is not None else None
        sql_tks = None if (norm_tks is not None and len(norm_tks) >= 300) else norm_tks
        min_trade_date = as_of_date - timedelta(days=366)
        min_bs_date = as_of_date - timedelta(days=760)
        num_re = r"^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$"
        pool = await self._get_pool()
        sql = f"""
            WITH latest_px AS MATERIALIZED (
                SELECT DISTINCT ON (ticker)
                       UPPER(TRIM(ticker)) AS ticker,
                       date AS trade_date,
                       close_unadj,
                       NULLIF(market_cap, 0) AS direct_mcap
                FROM market_data_daily
                WHERE date <= $1
                  AND date >= $2
                  AND ((close_unadj IS NOT NULL AND close_unadj > 0) OR (market_cap IS NOT NULL AND market_cap > 0))
                  AND ($3::text[] IS NULL OR ticker = ANY($3::text[]))
                ORDER BY ticker ASC, date DESC
            ),
            raw_bs AS MATERIALIZED (
                SELECT DISTINCT ON (symbol)
                       UPPER(TRIM(symbol)) AS ticker,
                       data
                FROM financial_statements
                WHERE statement_type IN ('BS', 'bs')
                  AND source IN ('alphastock', 'vnstock_vci')
                  AND period_end <= $1
                  AND period_end >= $4
                  AND ($3::text[] IS NULL OR symbol = ANY($3::text[]))
                  AND (
                    CASE
                      WHEN published_date IS NOT NULL
                           AND published_date > period_end
                           AND published_date <= (period_end + INTERVAL '180 days')::date
                      THEN published_date
                      WHEN LOWER(TRIM(frequency)) IN ('yearly', 'annual')
                      THEN (period_end + INTERVAL '90 days')::date
                      ELSE (period_end + INTERVAL '45 days')::date
                    END
                  ) <= $1
                ORDER BY symbol ASC, period_end DESC, CASE WHEN source = 'alphastock' THEN 0 ELSE 1 END ASC
            ),
            latest_bs AS (
                SELECT ticker,
                       CASE WHEN (data->>'bs_off_outstanding_shares') ~ '{num_re}'
                            THEN NULLIF((data->>'bs_off_outstanding_shares')::float8, 0) END AS bs_off_sh,
                       COALESCE(
                           CASE WHEN (data->>'bs_charter_capital') ~ '{num_re}'
                                THEN NULLIF((data->>'bs_charter_capital')::float8, 0) END,
                           CASE WHEN (data->>'1_vốn_góp_của_chủ_sở_hữu') ~ '{num_re}'
                                THEN NULLIF((data->>'1_vốn_góp_của_chủ_sở_hữu')::float8, 0) END,
                           CASE WHEN (data->>'a_vốn_điều_lệ') ~ '{num_re}'
                                THEN NULLIF((data->>'a_vốn_điều_lệ')::float8, 0) END,
                           CASE WHEN (data->>'1_1_vốn_góp_của_chủ_sở_hữu') ~ '{num_re}'
                                THEN NULLIF((data->>'1_1_vốn_góp_của_chủ_sở_hữu')::float8, 0) END,
                           CASE WHEN (data->>'Common shares') ~ '{num_re}'
                                THEN NULLIF((data->>'Common shares')::float8, 0) END,
                           CASE WHEN (data->>'common_shares') ~ '{num_re}'
                                THEN NULLIF((data->>'common_shares')::float8, 0) END,
                           CASE WHEN (data->>'Vốn góp') ~ '{num_re}'
                                THEN NULLIF((data->>'Vốn góp')::float8, 0) END,
                           CASE WHEN (data->>'vốn góp') ~ '{num_re}'
                                THEN NULLIF((data->>'vốn góp')::float8, 0) END
                       ) AS cc_vnd
                FROM raw_bs
            ),
            raw_rt AS MATERIALIZED (
                SELECT DISTINCT ON (symbol)
                       UPPER(TRIM(symbol)) AS ticker,
                       data
                FROM financial_statements
                WHERE statement_type IN ('ratios', 'RATIOS')
                  AND source = 'alphastock'
                  AND period_end <= $1
                  AND period_end >= $4
                  AND ($3::text[] IS NULL OR symbol = ANY($3::text[]))
                  AND (
                    CASE
                      WHEN published_date IS NOT NULL
                           AND published_date > period_end
                           AND published_date <= (period_end + INTERVAL '180 days')::date
                      THEN published_date
                      WHEN LOWER(TRIM(frequency)) IN ('yearly', 'annual')
                      THEN (period_end + INTERVAL '90 days')::date
                      ELSE (period_end + INTERVAL '45 days')::date
                    END
                  ) <= $1
                ORDER BY symbol ASC, period_end DESC
            ),
            latest_rt AS (
                SELECT ticker,
                       CASE WHEN (data->>'rt_value_outstanding_shares') ~ '{num_re}'
                            THEN NULLIF((data->>'rt_value_outstanding_shares')::float8, 0) END AS rt_sh
                FROM raw_rt
            )
            SELECT px.ticker,
                   COALESCE(
                       CASE WHEN px.direct_mcap > 0 THEN px.direct_mcap END,
                       px.close_unadj * 1000.0 * COALESCE(
                           CASE
                             WHEN rt.rt_sh > 0 AND NOT EXISTS (
                               SELECT 1
                               FROM corporate_actions ca
                               WHERE ca.symbol = px.ticker
                                 AND ca.action_date > px.trade_date
                                 AND ca.action_type IN ('SPLIT', 'STOCK_DIVIDEND', 'RIGHTS')
                             )
                             THEN rt.rt_sh
                           END,
                           CASE WHEN bs.bs_off_sh > 0 THEN bs.bs_off_sh END,
                           CASE WHEN bs.cc_vnd > 0 THEN bs.cc_vnd / 10000.0 END
                       )
                   ) AS market_cap
            FROM latest_px px
            LEFT JOIN latest_bs bs ON bs.ticker = px.ticker
            LEFT JOIN latest_rt rt ON rt.ticker = px.ticker
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, as_of_date, min_trade_date, sql_tks, min_bs_date)
        out: dict[str, float] = {}
        for r in rows:
            sym = str(r.get("ticker") or "").strip().upper()
            if want_set is not None and sym not in want_set:
                continue
            mc = _finite_float(r.get("market_cap"))
            if sym and mc is not None and mc > 0:
                out[sym] = mc
        return out

    async def fetch_pit_market_and_macro_tape(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch strictly PIT-gated (`<= as_of_date`) VN-Index, liquidity, breadth, foreign flow, regime & macro."""
        del tickers
        pool = await self._get_pool()
        min_px_date = as_of_date - timedelta(days=140)
        min_macro_date = as_of_date - timedelta(days=120)

        vni_sql = """
            SELECT date, close_unadj, close_adj, volume_total, adtv20_continuous
            FROM market_data_daily
            WHERE ticker = 'VNINDEX'
              AND date <= $1
              AND date >= $2
            ORDER BY date DESC
            LIMIT 70
        """
        breadth_and_sec_sql = """
            WITH recent_dates AS (
                SELECT DISTINCT date
                FROM market_data_daily
                WHERE date <= $1
                  AND date >= $2
                  AND ticker <> 'VNINDEX'
                ORDER BY date DESC
                LIMIT 22
            ),
            bounds AS (
                SELECT MAX(date) AS d0,
                       (SELECT date FROM recent_dates ORDER BY date DESC OFFSET 1 LIMIT 1) AS d1,
                       MIN(date) AS d21
                FROM recent_dates
            ),
            px AS (
                SELECT UPPER(TRIM(m.ticker)) AS ticker,
                       m.date,
                       COALESCE(NULLIF(m.close_adj, 0), NULLIF(m.close_unadj, 0)) AS px_val
                FROM market_data_daily m
                JOIN bounds b ON m.date IN (b.d0, b.d1, b.d21)
                WHERE m.ticker <> 'VNINDEX'
            ),
            piv AS (
                SELECT ticker,
                       MAX(CASE WHEN px.date = b.d0 THEN px.px_val END) AS p0,
                       MAX(CASE WHEN px.date = b.d1 THEN px.px_val END) AS p1,
                       MAX(CASE WHEN px.date = b.d21 THEN px.px_val END) AS p21,
                       MAX(b.d0) AS latest_d
                FROM px
                CROSS JOIN bounds b
                GROUP BY ticker
            )
            SELECT p.ticker,
                   NULLIF(TRIM(s.sector), '') AS sector,
                   p.p0,
                   p.p1,
                   p.p21,
                   p.latest_d
            FROM piv p
            LEFT JOIN stocks s ON UPPER(TRIM(s.symbol)) = p.ticker
            WHERE p.p0 IS NOT NULL
        """
        foreign_sql = """
            WITH f_dates AS (
                SELECT DISTINCT trade_date
                FROM foreign_flow
                WHERE trade_date <= $1
                  AND trade_date >= $2
                ORDER BY trade_date DESC
                LIMIT 20
            ),
            ranked_d AS (
                SELECT trade_date, ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
                FROM f_dates
            )
            SELECT COALESCE(NULLIF(TRIM(s.sector), ''), 'UNKNOWN') AS sector,
                   SUM(CASE WHEN rd.rn <= 5 THEN COALESCE(ff.net_value, 0) ELSE 0 END) AS net_5d,
                   SUM(COALESCE(ff.net_value, 0)) AS net_20d
            FROM foreign_flow ff
            JOIN ranked_d rd ON rd.trade_date = ff.trade_date
            LEFT JOIN stocks s ON UPPER(TRIM(s.symbol)) = UPPER(TRIM(ff.symbol))
            GROUP BY 1
        """
        regime_sql = """
            SELECT date, current_regime, vix_vn_analog, breadth_above_ma50_pct
            FROM market_regimes
            WHERE date <= $1
            ORDER BY date DESC
            LIMIT 1
        """
        macro_sql = """
            SELECT indicator_name, indicator_date, value, unit
            FROM macro_indicators
            WHERE indicator_date <= $1
              AND indicator_date >= $2
              AND indicator_name = ANY($3::text[])
              AND value IS NOT NULL
            ORDER BY indicator_name ASC, indicator_date DESC
        """
        macro_keys = [
            "usd_vnd_exchange",
            "vninbr_interbank_rate",
            "vnintr_interest_rate",
            "oil_price_brent",
            "usd_10y_yield",
            "gold_price_vnd",
        ]

        async with pool.acquire() as conn:
            vni_rows = await conn.fetch(vni_sql, as_of_date, min_px_date)
            bs_rows = await conn.fetch(breadth_and_sec_sql, as_of_date, min_px_date)
            ff_rows = await conn.fetch(foreign_sql, as_of_date, min_px_date)
            reg_row = await conn.fetchrow(regime_sql, as_of_date)
            mac_rows = await conn.fetch(macro_sql, as_of_date, min_macro_date, macro_keys)

        out: dict[str, Any] = {}
        if vni_rows:
            r0 = vni_rows[0]
            c0 = _finite_float(r0.get("close_unadj")) or _finite_float(r0.get("close_adj"))
            out["latest_trade_date"] = r0["date"]
            out["vnindex_close"] = c0
            if c0 and len(vni_rows) >= 2:
                c1 = _finite_float(vni_rows[1].get("close_unadj")) or _finite_float(vni_rows[1].get("close_adj"))
                if c1 and c1 > 0:
                    out["vnindex_ret_1d"] = (c0 - c1) / c1
            if c0 and len(vni_rows) >= 21:
                c21 = _finite_float(vni_rows[20].get("close_unadj")) or _finite_float(vni_rows[20].get("close_adj"))
                if c21 and c21 > 0:
                    out["vnindex_ret_1m"] = (c0 - c21) / c21
            if c0 and len(vni_rows) >= 60:
                idx_3m = min(len(vni_rows) - 1, 62)
                c63 = _finite_float(vni_rows[idx_3m].get("close_unadj")) or _finite_float(
                    vni_rows[idx_3m].get("close_adj")
                )
                if c63 and c63 > 0:
                    out["vnindex_ret_3m"] = (c0 - c63) / c63
            vol0 = _finite_float(r0.get("volume_total"))
            adtv20 = _finite_float(r0.get("adtv20_continuous"))
            if vol0 and adtv20 and adtv20 > 0:
                out["liquidity_vs_20d_ratio"] = vol0 / adtv20
            elif vol0 and len(vni_rows) >= 5:
                vols = [_finite_float(vr.get("volume_total")) for vr in vni_rows[:20]]
                valid_vols = [v for v in vols if v and v > 0]
                if valid_vols:
                    out["liquidity_vs_20d_ratio"] = vol0 / (sum(valid_vols) / len(valid_vols))

        adv = 0
        dec = 0
        sec_rets_map: dict[str, list[float]] = defaultdict(list)
        for r in bs_rows:
            if "latest_trade_date" not in out and r.get("latest_d") is not None:
                out["latest_trade_date"] = r["latest_d"]
            p0 = _finite_float(r.get("p0"))
            p1 = _finite_float(r.get("p1"))
            p21 = _finite_float(r.get("p21"))
            if p0 and p1 and p1 > 0:
                if p0 > p1 * 1.0001:
                    adv += 1
                elif p0 < p1 * 0.9999:
                    dec += 1
            sec = str(r.get("sector") or "").strip().upper()
            if sec and p0 and p21 and p21 > 0:
                sec_rets_map[sec].append((p0 - p21) / p21)
        if adv or dec:
            out["advancers"] = adv
            out["decliners"] = dec
        out["sector_ret_1m"] = {
            sc: sorted(vals)[len(vals) // 2] for sc, vals in sec_rets_map.items() if vals
        }

        f5_total = 0.0
        f20_total = 0.0
        f5_by_sec: dict[str, float] = {}
        for r in ff_rows:
            n5 = _finite_float(r.get("net_5d")) or 0.0
            n20 = _finite_float(r.get("net_20d")) or 0.0
            f5_total += n5
            f20_total += n20
            sec = str(r.get("sector") or "").strip().upper()
            if sec and sec != "UNKNOWN":
                f5_by_sec[sec] = n5
        if ff_rows:
            out["foreign_net_5d_vnd"] = f5_total
            out["foreign_net_20d_vnd"] = f20_total
            out["foreign_net_5d_by_sector"] = f5_by_sec

        if reg_row is not None:
            out["regime"] = {
                "date": reg_row["date"],
                "current_regime": str(reg_row["current_regime"] or ""),
                "vix_vn_analog": _finite_float(reg_row.get("vix_vn_analog")),
                "breadth_above_ma50_pct": _finite_float(reg_row.get("breadth_above_ma50_pct")),
            }

        mac_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in mac_rows:
            mac_grouped[str(r["indicator_name"])].append(dict(r))
        mac_out: dict[str, dict[str, Any]] = {}
        for mk, mlist in mac_grouped.items():
            latest_val = _finite_float(mlist[0].get("value"))
            latest_d = mlist[0].get("indicator_date")
            if latest_val is None or latest_d is None:
                continue
            ref_idx = min(len(mlist) - 1, 20)
            prev_val = _finite_float(mlist[ref_idx].get("value")) if len(mlist) >= 2 else None
            chg_1m = ((latest_val - prev_val) / abs(prev_val)) if (prev_val and abs(prev_val) > 1e-9) else None
            mac_out[mk] = {
                "latest": latest_val,
                "date": latest_d,
                "change_1m_pct": chg_1m,
                "unit": str(mlist[0].get("unit") or ""),
            }
        out["macro"] = mac_out
        return out


# ── Core PG Statement JSONB & Ratio Normalization Helpers ───────────────────


def _parse_jsonb_mapping(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, Mapping) else {}
        except ValueError:
            return {}
    return {}


def _match_dict_field(
    field_key: str,
    include_terms: tuple[str, ...],
    exclude_terms: tuple[str, ...],
    normalized_items: Sequence[tuple[str, float]],
) -> float | None:
    del field_key
    for term in include_terms:
        for norm_key, num_val in normalized_items:
            if term in norm_key and not any(ex in norm_key for ex in exclude_terms):
                return float(num_val)
    return None


def _enrich_snapshot_ratios(rec: dict[str, Any]) -> None:
    """Compute deterministic ratios from base-unit monetary fields in `rec` (zero mental math)."""
    rev = _finite_float(rec.get("net_revenue"))
    gp = _finite_float(rec.get("gross_profit"))
    ni = _finite_float(rec.get("net_income"))
    ni_parent = _finite_float(rec.get("net_income_parent"))
    eq = _finite_float(rec.get("equity"))
    assets = _finite_float(rec.get("total_assets"))
    ca = _finite_float(rec.get("current_assets"))
    cl = _finite_float(rec.get("current_liabilities"))
    cash_st = _finite_float(rec.get("cash_and_st_investments"))
    cfo = _finite_float(rec.get("cfo"))
    capex = _finite_float(rec.get("capex"))

    if rec.get("short_term_debt") is not None or rec.get("long_term_debt") is not None:
        st_d = _finite_float(rec.get("short_term_debt")) or 0.0
        lt_d = _finite_float(rec.get("long_term_debt")) or 0.0
        if rec.get("total_debt") is None:
            rec["total_debt"] = st_d + lt_d

    if rec.get("fcf") is None and cfo is not None and capex is not None:
        rec["fcf"] = cfo - abs(capex)

    if rec.get("gross_margin") is None and gp is not None and rev is not None and abs(rev) > 1e-9:
        rec["gross_margin"] = _safe_calc(calc_margin, gp, rev)

    if rec.get("net_margin") is None and ni is not None and rev is not None and abs(rev) > 1e-9:
        rec["net_margin"] = _safe_calc(calc_margin, ni, rev)

    is_annual = int(rec.get("fiscal_quarter") or 0) == 0
    roe_num = ni_parent if ni_parent is not None else ni
    if is_annual and rec.get("roe") is None and roe_num is not None and eq is not None and eq > 0:
        rec["roe"] = _safe_calc(calc_roe, roe_num, eq)

    if is_annual and rec.get("roa") is None and ni is not None and assets is not None and assets > 0:
        rec["roa"] = _safe_calc(calc_roa, ni, assets)

    debt = _finite_float(rec.get("total_debt"))
    if rec.get("debt_to_equity") is None and debt is not None and eq is not None and eq > 0:
        rec["debt_to_equity"] = _safe_calc(calc_debt_to_equity, debt, eq)

    liab = _finite_float(rec.get("total_liabilities"))
    if rec.get("liabilities_to_equity") is None and liab is not None and eq is not None and eq > 0:
        rec["liabilities_to_equity"] = _safe_calc(calc_debt_to_equity, liab, eq)

    if (
        rec.get("net_debt_to_equity") is None
        and debt is not None
        and cash_st is not None
        and eq is not None
        and eq > 0
    ):
        rec["net_debt_to_equity"] = _safe_calc(calc_safe_div, debt - cash_st, eq)

    if rec.get("current_ratio") is None and ca is not None and cl is not None and cl > 0:
        rec["current_ratio"] = _safe_calc(calc_safe_div, ca, cl)

    if rec.get("cash_to_assets") is None and cash_st is not None and assets is not None and assets > 0:
        rec["cash_to_assets"] = _safe_calc(calc_safe_div, cash_st, assets)

    if is_annual and rec.get("cfo_to_net_income") is None and cfo is not None and ni is not None and abs(ni) > 1e-9:
        rec["cfo_to_net_income"] = _safe_calc(calc_safe_div, cfo, ni)


def _effective_pit_pub_date(period_date: date, raw_pub: Any, *, default_lag_days: int) -> date:
    """Enforce realistic 10..180-day PIT publication lag; reject 0-day backfill dates."""
    pub_d = _coerce_date(raw_pub)
    if (
        pub_d is not None
        and (period_date + timedelta(days=10)) <= pub_d <= (period_date + timedelta(days=180))
    ):
        return pub_d
    return period_date + timedelta(days=default_lag_days)


_SOURCE_PRIORITY: dict[str, int] = {"alphastock": 0, "vnstock_vci": 1}


def _prev_quarter(yr: int, fq: int, steps: int = 1) -> tuple[int, int]:
    idx = yr * 4 + (fq - 1) - steps
    return idx // 4, (idx % 4) + 1


def _extract_core_statement_metrics(
    stmt_rows: Sequence[Mapping[str, Any]],
    *,
    as_of_date: date,
    include_quarterly: bool = False,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Extract PIT-filtered annual (or TTM-enriched quarterly) BS/IS/CF monetary & derived ratios."""
    grouped: dict[tuple[str, int, int], dict[str, list[tuple[str, float]]]] = defaultdict(dict)
    src_rank_by_slot: dict[tuple[str, int, int, str], int] = {}
    eff_pub_by_key: dict[tuple[str, int, int], date] = {}

    for row in stmt_rows:
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        st = str(row.get("statement_type") or "").strip().upper()
        freq = str(row.get("frequency") or "yearly").strip().lower()
        pe = _coerce_date(row.get("period_end"))
        if not sym or st not in {"BS", "IS", "CF"} or pe is None or pe > as_of_date:
            continue
        if freq != "yearly" and not (include_quarterly and freq == "quarterly"):
            continue

        raw_src = row.get("source")
        if raw_src is not None:
            src = str(raw_src).strip().lower()
            if src not in _SOURCE_PRIORITY:
                # Reject cafef (HDKD_* codes) and untagged rows when vendor source is tracked
                continue
            src_rank = _SOURCE_PRIORITY[src]
        else:
            src_rank = 0

        default_lag = 90 if freq == "yearly" else 45
        pub_d = _effective_pit_pub_date(
            pe,
            row.get("effective_published_date") or row.get("published_date"),
            default_lag_days=default_lag,
        )
        if pub_d > as_of_date:
            continue

        data_map = _parse_jsonb_mapping(row.get("data"))
        if not data_map:
            continue

        norm_items: list[tuple[str, float]] = []
        for raw_k, raw_v in data_map.items():
            fv = _finite_float(raw_v)
            if fv is None:
                continue
            nk = strip_vn_accents(str(raw_k).replace("_", " "))
            if nk:
                norm_items.append((nk, fv))
        if not norm_items:
            continue

        fq = 0 if freq == "yearly" else ((pe.month - 1) // 3 + 1)
        key = (sym, pe.year, fq)
        slot_key = (*key, st)
        prev_rank = src_rank_by_slot.get(slot_key)
        if prev_rank is not None and src_rank > prev_rank:
            continue
        src_rank_by_slot[slot_key] = src_rank
        grouped[key][st] = norm_items
        prev_pub = eff_pub_by_key.get(key)
        if prev_pub is None or pub_d > prev_pub:
            eff_pub_by_key[key] = pub_d

    per_period: dict[tuple[str, int, int], dict[str, Any]] = {}
    for (sym, yr, fq), st_map in grouped.items():
        all_items: list[tuple[str, float]] = []
        for st_name in ("IS", "BS", "CF"):
            all_items.extend(st_map.get(st_name, ()))
        if not all_items:
            continue

        rec: dict[str, Any] = {
            "ticker": sym,
            "fiscal_year": yr,
            "fiscal_quarter": fq,
            "currency": "VND",
            "effective_published_date": eff_pub_by_key[(sym, yr, fq)],
        }
        helper_vals: dict[str, float] = {}
        for field_key, inc_terms, exc_terms in _NORMALIZED_STMT_SPECS:
            existing = helper_vals.get(field_key) if field_key.startswith("_") else rec.get(field_key)
            if existing is not None:
                continue
            val = _match_dict_field(field_key, inc_terms, exc_terms, all_items)
            if val is None:
                continue
            cleaned = abs(val) if field_key == "capex" else val
            if field_key.startswith("_"):
                helper_vals[field_key] = cleaned
            else:
                rec[field_key] = cleaned

        if "_cash_only" in helper_vals or "_st_investments" in helper_vals:
            rec["cash_and_st_investments"] = (
                helper_vals.get("_cash_only", 0.0) + helper_vals.get("_st_investments", 0.0)
            )
        if rec.get("net_income") is None and rec.get("net_income_parent") is not None:
            rec["net_income"] = rec["net_income_parent"]

        if not any(rec.get(col) is not None for col in MONETARY_METRIC_COLS):
            continue

        _enrich_snapshot_ratios(rec)
        per_period[(sym, yr, fq)] = rec

    # Compute TTM ROE / ROA / margins across 4 consecutive quarters (never single-quarter ROE!)
    if include_quarterly:
        for (sym, yr, fq), rec in per_period.items():
            if fq == 0:
                continue
            trail = [per_period.get((sym, *_prev_quarter(yr, fq, step))) for step in range(4)]
            if all(q is not None and _finite_float(q.get("net_income")) is not None for q in trail):
                ni_ttm = sum(float(q["net_income"]) for q in trail if q is not None)
                ni_par_ttm = (
                    sum(float(q["net_income_parent"]) for q in trail if q is not None)
                    if all(q is not None and _finite_float(q.get("net_income_parent")) is not None for q in trail)
                    else ni_ttm
                )
                eq_end = _finite_float(rec.get("equity"))
                begin_rec = per_period.get((sym, yr - 1, fq))
                eq_beg = _finite_float(begin_rec.get("equity")) if begin_rec else None
                eq_avg = ((eq_end + eq_beg) / 2.0) if (eq_end and eq_beg and (eq_end + eq_beg) > 0) else eq_end
                if eq_avg is not None and eq_avg > 0:
                    rec["roe"] = _safe_calc(calc_roe, ni_par_ttm, eq_avg)
                as_end = _finite_float(rec.get("total_assets"))
                as_beg = _finite_float(begin_rec.get("total_assets")) if begin_rec else None
                as_avg = ((as_end + as_beg) / 2.0) if (as_end and as_beg and (as_end + as_beg) > 0) else as_end
                if as_avg is not None and as_avg > 0:
                    rec["roa"] = _safe_calc(calc_roa, ni_ttm, as_avg)
                if all(q is not None and _finite_float(q.get("net_revenue")) is not None for q in trail):
                    rev_ttm = sum(float(q["net_revenue"]) for q in trail if q is not None)
                    if abs(rev_ttm) > 1e-9:
                        rec["net_margin"] = _safe_calc(calc_margin, ni_ttm, rev_ttm)
                        if all(q is not None and _finite_float(q.get("gross_profit")) is not None for q in trail):
                            gp_ttm = sum(float(q["gross_profit"]) for q in trail if q is not None)
                            rec["gross_margin"] = _safe_calc(calc_margin, gp_ttm, rev_ttm)

    out: dict[tuple[str, int], dict[str, Any]] = {}
    for (sym, yr, fq), rec in sorted(per_period.items()):
        existing = out.get((sym, yr))
        if (
            existing is None
            or fq == 0
            or (int(existing.get("fiscal_quarter") or 0) != 0 and fq > int(existing["fiscal_quarter"]))
        ):
            out[(sym, yr)] = rec

    # Cross-year YoY & 3Y CAGR per ticker (strictly comparing annual->annual)
    by_tk: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for (sym, yr), rec in out.items():
        if int(rec.get("fiscal_quarter") or 0) == 0:
            by_tk[sym][yr] = rec

    for _sym, yr_map in by_tk.items():
        for yr, rec in sorted(yr_map.items()):
            prev = yr_map.get(yr - 1)
            if prev is not None:
                if (
                    rec.get("revenue_yoy") is None
                    and rec.get("net_revenue") is not None
                    and prev.get("net_revenue") is not None
                    and abs(prev["net_revenue"]) > 1e-9
                ):
                    rec["revenue_yoy"] = _safe_calc(calc_yoy, rec["net_revenue"], prev["net_revenue"])
                if (
                    rec.get("net_income_yoy") is None
                    and rec.get("net_income") is not None
                    and prev.get("net_income") is not None
                    and abs(prev["net_income"]) > 1e-9
                ):
                    rec["net_income_yoy"] = _safe_calc(calc_yoy, rec["net_income"], prev["net_income"])
            base_3y = yr_map.get(yr - 3)
            if (
                base_3y is not None
                and rec.get("revenue_cagr_3y") is None
                and rec.get("net_revenue") is not None
                and base_3y.get("net_revenue") is not None
                and rec["net_revenue"] > 0
                and base_3y["net_revenue"] > 0
            ):
                rec["revenue_cagr_3y"] = _safe_calc(
                    calc_cagr, rec["net_revenue"], base_3y["net_revenue"], 3.0
                )

    return out


def _scale_ratio_value_with_invariants(
    raw_val: float | None,
    *,
    col: str,
    row_is_percent: bool,
    stmt_ref: float | None = None,
    peer_upper_bound: float | None = None,
) -> float | None:
    """Calibrate a single Core PG ratio cell to decimal without global-median 100x corruption."""
    if raw_val is None:
        return None
    if stmt_ref is not None and math.isfinite(stmt_ref):
        err_as_dec = abs(raw_val - stmt_ref)
        err_as_pct = abs((raw_val / 100.0) - stmt_ref)
        return round((raw_val / 100.0) if err_as_pct < err_as_dec else raw_val, 6)

    av = abs(raw_val)
    if col in ("yoy_revenue_growth", "yoy_earnings_growth"):
        if av > 2.0 or (row_is_percent and av > 1.5):
            return round(raw_val / 100.0, 6)
        return round(raw_val, 6)

    if col == "roa" and peer_upper_bound is not None and peer_upper_bound > 0:
        if av > peer_upper_bound + 0.02:
            return round(raw_val / 100.0, 6)
        if row_is_percent and av > 1.0:
            return round(raw_val / 100.0, 6)
        return round(raw_val, 6)

    if col == "net_margin" and peer_upper_bound is not None and peer_upper_bound > 0:
        if av > peer_upper_bound * 1.25 and av > 1.0:
            return round(raw_val / 100.0, 6)
        if av > 1.2:
            return round(raw_val / 100.0, 6)
        return round(raw_val, 6)

    if av > 1.2:
        return round(raw_val / 100.0, 6)
    if row_is_percent and col in ("fcf_yield", "gross_margin", "net_margin", "roe") and av >= 1.0:
        return round(raw_val / 100.0, 6)
    return round(raw_val, 6)


def _normalize_core_ratio_rows(
    ratio_rows: Sequence[Mapping[str, Any]],
    *,
    stmt_metrics: Mapping[tuple[str, int], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize Core PG percentage columns per row using statement cross-checks & accounting invariants."""
    if not ratio_rows:
        return []

    normalized: list[dict[str, Any]] = []
    for row in ratio_rows:
        sym = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        r_date = _coerce_date(row.get("ratio_date"))
        if not sym or r_date is None:
            continue
        freq = str(row.get("frequency") or "quarterly").strip().lower()
        fq = 0 if freq == "yearly" else ((r_date.month - 1) // 3 + 1)

        default_lag = 90 if freq == "yearly" else 45
        eff_pub = _effective_pit_pub_date(
            r_date,
            row.get("effective_published_date") or row.get("published_date"),
            default_lag_days=default_lag,
        )

        s_ref = stmt_metrics.get((sym, r_date.year)) if stmt_metrics else None

        gm_raw = _finite_float(row.get("gross_margin"))
        nm_raw = _finite_float(row.get("net_margin"))
        roe_raw = _finite_float(row.get("roe"))
        roa_raw = _finite_float(row.get("roa"))
        rev_yoy_raw = _finite_float(row.get("yoy_revenue_growth"))
        ni_yoy_raw = _finite_float(row.get("yoy_earnings_growth"))
        fcf_raw = _finite_float(row.get("fcf_yield"))

        # Guard against upstream bank ETL copying ROA verbatim into ROE (e.g. MBB/TCB roe == roa == 0.0169)
        if roe_raw is not None and roa_raw is not None and abs(roe_raw) > 1e-6 and abs(roe_raw - roa_raw) <= 1e-9:
            roe_raw = None

        # Quarterly rows in Core `financial_ratios` store single-quarter unannualized NI/Equity;
        # when `stmt_metrics` is active, only TTM statement-derived ROE/ROA are valid for `fq != 0`.
        if fq != 0 and stmt_metrics is not None:
            roe_raw = None
            roa_raw = None

        row_is_percent = any(
            v is not None and abs(v) > 1.2 for v in (gm_raw, nm_raw, roe_raw)
        )

        gross_margin = _scale_ratio_value_with_invariants(
            gm_raw,
            col="gross_margin",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("gross_margin")) if s_ref else None,
        )
        net_margin = _scale_ratio_value_with_invariants(
            nm_raw,
            col="net_margin",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("net_margin")) if s_ref else None,
            peer_upper_bound=abs(gross_margin) if gross_margin is not None else None,
        )
        roe = _scale_ratio_value_with_invariants(
            roe_raw,
            col="roe",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("roe")) if s_ref else None,
        )
        roa = _scale_ratio_value_with_invariants(
            roa_raw,
            col="roa",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("roa")) if s_ref else None,
            peer_upper_bound=abs(roe) if roe is not None else None,
        )
        revenue_yoy = _scale_ratio_value_with_invariants(
            rev_yoy_raw,
            col="yoy_revenue_growth",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("revenue_yoy")) if s_ref else None,
        )
        net_income_yoy = _scale_ratio_value_with_invariants(
            ni_yoy_raw,
            col="yoy_earnings_growth",
            row_is_percent=row_is_percent,
            stmt_ref=_finite_float(s_ref.get("net_income_yoy")) if s_ref else None,
        )
        fcf_yield = _scale_ratio_value_with_invariants(
            fcf_raw,
            col="fcf_yield",
            row_is_percent=row_is_percent,
        )

        # Core PG `financial_ratios.debt_equity` stores Total Liabilities / Equity.
        # Map it to `liabilities_to_equity` so it never collides with interest-bearing `debt_to_equity`.
        liab_to_eq = _finite_float(row.get("debt_equity"))
        stmt_de = _finite_float(s_ref.get("debt_to_equity")) if s_ref else None

        raw_pe = _finite_float(row.get("pe"))
        # Guard against upstream yearly ETL writing fiscal year (e.g. 2025.0) into `pe`
        if raw_pe is not None and fq == 0 and 1990 <= r_date.year <= 2100 and raw_pe == float(r_date.year):
            raw_pe = None

        item: dict[str, Any] = {
            "ticker": sym,
            "fiscal_year": r_date.year,
            "fiscal_quarter": fq,
            "ratio_date": r_date,
            "effective_published_date": eff_pub,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
            "roe": roe,
            "roa": roa,
            "revenue_yoy": revenue_yoy,
            "net_income_yoy": net_income_yoy,
            "fcf_yield": fcf_yield,
            "pe": raw_pe,
            "pb": _finite_float(row.get("pb")),
            "ev_ebitda": _finite_float(row.get("ev_ebitda")),
            "debt_to_equity": stmt_de,
            "liabilities_to_equity": liab_to_eq,
            "current_ratio": _finite_float(row.get("current_ratio")),
        }
        normalized.append(item)
    return normalized


# ── Pure PDF VERIFIED Table Extraction (Authoritative / Fallback) ───────────


def _normalize_row(row: Any) -> tuple[str, list[float | None]] | None:
    if isinstance(row, Mapping):
        label = str(row.get("label") or "").strip()
        vals = row.get("values") or []
        return label, [
            _finite_float(v.get("numeric_value") if isinstance(v, Mapping) else v)
            for v in vals
        ]
    if isinstance(row, list) and len(row) >= 2:
        first = row[0]
        label = str(first.get("raw_text") if isinstance(first, Mapping) else first).strip()
        vals = [
            _finite_float(c.get("numeric_value") if isinstance(c, Mapping) else c)
            for c in row[1:]
        ]
        return label, vals
    return None


def _table_priority_score(table: Mapping[str, Any]) -> tuple[int, int]:
    """Rank headline audited statement tables above sub-notes and minor breakdowns."""
    title = str(table.get("table_title") or table.get("title") or "")
    sec_path = str(table.get("section_path") or "")
    slot = str(table.get("sub_doc_slot") or "").upper()
    blob = f"{title} {sec_path}"
    is_note = bool(_SUB_NOTE_RE.search(blob))
    is_stmt = bool(_PRIMARY_STMT_RE.search(blob))
    slot_rank = 3 if slot == "BCTC_AUDITED" else (2 if slot == "QUARTER_NOTES" else 1)
    stmt_score = (10 if is_stmt and not is_note else 0) - (10 if is_note else 0) + slot_rank
    page_from = int(table.get("page_from") or 9999)
    return (stmt_score, -page_from)


def _match_row_field(
    field_key: str,
    include_terms: tuple[str, ...],
    exclude_terms: tuple[str, ...],
    parsed_rows: Sequence[tuple[str, list[float | None]]],
    *,
    col_idx: int,
) -> tuple[str, float] | None:
    """Match highest-specificity VAS/IFRS label term for a numeric value column."""
    del field_key
    for term in include_terms:
        for label, nums in parsed_rows:
            low = label.lower()
            if term in low and not any(ex in low for ex in exclude_terms):
                if 0 <= col_idx < len(nums) and nums[col_idx] is not None:
                    return label, float(nums[col_idx])
    return None


def _infer_fiscal_year(table: Mapping[str, Any], headers: list[str]) -> int | None:
    fy = table.get("fiscal_year")
    if fy is not None:
        try:
            y = int(fy)
            if 1990 <= y <= 2100:
                return y
        except (TypeError, ValueError):
            pass
    for h in [*headers, str(table.get("table_title") or table.get("title") or "")]:
        ym = _YEAR_IN_TEXT_RE.search(str(h))
        if ym:
            return int(ym.group(1))
    return None


def _select_period_columns(
    headers: list[str],
    rows: list[Any],
    col_roles: list[str] | None,
) -> list[int]:
    """Return 0-based `values` indices of non-plan `period_amount` columns, sorted newest-year first."""
    from sag_api.research.table_engine import classify_table_columns

    roles = list(col_roles) if col_roles and len(col_roles) == len(headers) else []
    if not roles:
        roles, _ = classify_table_columns(headers, rows)

    period_indices = [
        c - 1
        for c in range(1, min(len(headers), len(roles)))
        if roles[c] == "period_amount" and not _PLAN_HDR_RE.search(headers[c].strip())
    ]
    if not period_indices:
        # Fallback: `other` numeric columns only when no explicit `period_amount` column header exists
        period_indices = [
            c - 1
            for c in range(1, min(len(headers), len(roles)))
            if roles[c] == "other" and not _PLAN_HDR_RE.search(headers[c].strip())
        ]

    # Sort columns with explicit 4-digit years descending (e.g., `2022 | 2023 | 2024` -> `[2024, 2023, 2022]`)
    year_by_idx: dict[int, int] = {}
    for idx in period_indices:
        hdr = headers[idx + 1] if (idx + 1) < len(headers) else ""
        ym = _YEAR_IN_TEXT_RE.search(str(hdr))
        if ym:
            year_by_idx[idx] = int(ym.group(1))
    if len(year_by_idx) >= 2:
        period_indices.sort(key=lambda idx: (0 if idx in year_by_idx else 1, -year_by_idx.get(idx, 0), idx))
    return period_indices


def extract_metrics_from_verified_tables(
    tables: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Extract normalized PIT financial metric snapshots from strictly `VERIFIED` 2D tables.

    Invariants:
    - Refuses `TABLE_UNVERIFIED` / `PARTIAL` tables.
    - Groups strictly by `(ticker, fiscal_year, fiscal_quarter, currency)` so `USD` and `VND`
      tables are NEVER summed or divided together.
    - Scales all monetary figures to base units (`value * unit_multiplier`) and derives
      deterministic ratios via `sag_api.research.calculator`.
    """
    grouped: dict[tuple[str, int, int, str], list[Mapping[str, Any]]] = defaultdict(list)

    for tbl in tables:
        if str(tbl.get("verification_status") or "").upper() != "VERIFIED":
            continue
        ticker = str(tbl.get("project_id") or tbl.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        raw_headers = tbl.get("headers") or tbl.get("headers_json") or []
        flat_headers = (
            [str(h or "") for h in raw_headers[0]]
            if (raw_headers and isinstance(raw_headers[0], list))
            else [str(h or "") for h in raw_headers]
        )
        if len(flat_headers) < 2:
            continue
        fy = _infer_fiscal_year(tbl, flat_headers)
        if fy is None:
            continue
        fq = int(tbl.get("fiscal_quarter") or 0)
        currency = str(tbl.get("currency") or "VND").strip().upper() or "VND"
        grouped[(ticker, fy, fq, currency)].append(tbl)

    snapshots: list[dict[str, Any]] = []
    for (ticker, fy, fq, currency), grp_tables in grouped.items():
        ordered_tables = sorted(grp_tables, key=_table_priority_score, reverse=True)
        rec: dict[str, Any] = {
            "tenant_id": tenant_id,
            "ticker": ticker,
            "fiscal_year": fy,
            "fiscal_quarter": fq,
            "period_end": date(fy, 12, 31) if fq == 0 else None,
            "source_kind": "VERIFIED_TABLE",
            "currency": currency,
            "provenance_json": {},
        }
        helper_vals: dict[str, float] = {}
        prior_rev_pairs: list[tuple[float, float]] = []
        prior_ni_pairs: list[tuple[float, float]] = []
        latest_vf: datetime | None = None
        headline_table_id: str | None = None
        headline_doc_ver_id: str | None = None
        headline_page_from: int | None = None
        contributing_ver_ids: list[str] = []

        for tbl in ordered_tables:
            raw_headers = tbl.get("headers") or tbl.get("headers_json") or []
            flat_headers = (
                [str(h or "") for h in raw_headers[0]]
                if (raw_headers and isinstance(raw_headers[0], list))
                else [str(h or "") for h in raw_headers]
            )
            raw_rows = list(tbl.get("rows") or tbl.get("rows_json") or [])
            if not raw_rows or len(flat_headers) < 2:
                continue

            period_indices = _select_period_columns(flat_headers, raw_rows, tbl.get("column_roles"))
            if not period_indices:
                continue
            curr_idx = period_indices[0]
            prior_idx = period_indices[1] if len(period_indices) >= 2 else None

            parsed_rows = [nr for nr in (_normalize_row(r) for r in raw_rows) if nr is not None]
            if not parsed_rows:
                continue

            mult = _finite_float(tbl.get("unit_multiplier")) or 1.0
            tbl_id = str(tbl.get("table_id") or "")
            ver_id = str(tbl.get("document_version_id") or "")
            if ver_id and ver_id not in contributing_ver_ids:
                contributing_ver_ids.append(ver_id)
            p_from = int(tbl.get("page_from") or 1)
            vf = _coerce_utc(tbl.get("valid_from"))
            if vf is not None and (latest_vf is None or vf > latest_vf):
                latest_vf = vf

            col_hdr_curr = flat_headers[curr_idx + 1].strip() if (curr_idx + 1) < len(flat_headers) else ""
            matched_any_on_table = False

            for field_key, inc_terms, exc_terms in _METRIC_ROW_SPECS:
                existing = helper_vals.get(field_key) if field_key.startswith("_") else rec.get(field_key)
                if existing is not None:
                    continue
                matched = _match_row_field(
                    field_key, inc_terms, exc_terms, parsed_rows, col_idx=curr_idx
                )
                if matched is None:
                    continue
                row_label, raw_val = matched
                base_val = abs(raw_val) * mult if field_key == "capex" else raw_val * mult
                if currency.upper() == "VND" and 0.0 < abs(base_val) < 1e6:
                    # Reject unscaled VND numbers (lost 1e6/1e9 multiplier on listed companies)
                    continue
                if field_key.startswith("_"):
                    helper_vals[field_key] = base_val
                else:
                    rec[field_key] = base_val
                rec["provenance_json"][field_key.lstrip("_")] = {
                    "table_id": tbl_id,
                    "document_version_id": ver_id or None,
                    "page_from": p_from,
                    "row_label": row_label,
                    "col_header": col_hdr_curr,
                    "raw_value": raw_val,
                    "unit_multiplier": mult,
                    "currency": currency,
                    "source_kind": "VERIFIED_TABLE",
                }
                matched_any_on_table = True

                if prior_idx is not None and field_key in ("net_revenue", "net_income"):
                    for p_lbl, p_nums in parsed_rows:
                        if p_lbl == row_label and 0 <= prior_idx < len(p_nums) and p_nums[prior_idx] is not None:
                            prior_base = float(p_nums[prior_idx]) * mult
                            if abs(prior_base) > 1e-9:
                                if field_key == "net_revenue":
                                    prior_rev_pairs.append((base_val, prior_base))
                                else:
                                    prior_ni_pairs.append((base_val, prior_base))
                            break

            if matched_any_on_table and headline_table_id is None:
                headline_table_id = tbl_id or None
                headline_doc_ver_id = ver_id or None
                headline_page_from = p_from

        if "_cash_only" in helper_vals or "_st_investments" in helper_vals:
            rec["cash_and_st_investments"] = (
                helper_vals.get("_cash_only", 0.0) + helper_vals.get("_st_investments", 0.0)
            )
            cash_prov = rec["provenance_json"].get("cash_only") or rec["provenance_json"].get("st_investments")
            if cash_prov:
                rec["provenance_json"]["cash_and_st_investments"] = cash_prov

        if rec.get("net_income") is None and rec.get("net_income_parent") is not None:
            rec["net_income"] = rec["net_income_parent"]

        if not any(rec.get(col) is not None for col in MONETARY_METRIC_COLS):
            continue

        if latest_vf is None:
            latest_vf = datetime(fy, 12, 31, tzinfo=UTC)

        if prior_rev_pairs and rec.get("revenue_yoy") is None:
            curr_v, prev_v = prior_rev_pairs[0]
            rec["revenue_yoy"] = _safe_calc(calc_yoy, curr_v, prev_v)
        if prior_ni_pairs and rec.get("net_income_yoy") is None:
            curr_v, prev_v = prior_ni_pairs[0]
            rec["net_income_yoy"] = _safe_calc(calc_yoy, curr_v, prev_v)

        _enrich_snapshot_ratios(rec)

        rec["valid_from"] = latest_vf
        rec["valid_to"] = _MAX_VALID_TO_UTC
        rec["source_table_id"] = headline_table_id
        rec["document_version_id"] = headline_doc_ver_id
        rec["page_from"] = headline_page_from
        rec["provenance_json"]["document_version_ids"] = contributing_ver_ids
        snapshots.append(rec)

    annual_by_tk_ccy: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for snap in snapshots:
        if snap["fiscal_quarter"] == 0:
            annual_by_tk_ccy[(snap["ticker"], snap["currency"])][snap["fiscal_year"]] = snap

    for _tk_ccy, by_yr in annual_by_tk_ccy.items():
        for yr, snap in sorted(by_yr.items()):
            prev_yr = by_yr.get(yr - 1)
            if prev_yr is not None:
                if (
                    snap.get("revenue_yoy") is None
                    and snap.get("net_revenue") is not None
                    and prev_yr.get("net_revenue") is not None
                    and abs(prev_yr["net_revenue"]) > 1e-9
                ):
                    snap["revenue_yoy"] = _safe_calc(calc_yoy, snap["net_revenue"], prev_yr["net_revenue"])
                if (
                    snap.get("net_income_yoy") is None
                    and snap.get("net_income") is not None
                    and prev_yr.get("net_income") is not None
                    and abs(prev_yr["net_income"]) > 1e-9
                ):
                    snap["net_income_yoy"] = _safe_calc(calc_yoy, snap["net_income"], prev_yr["net_income"])

            base_3y = by_yr.get(yr - 3)
            if (
                base_3y is not None
                and snap.get("revenue_cagr_3y") is None
                and snap.get("net_revenue") is not None
                and base_3y.get("net_revenue") is not None
                and snap["net_revenue"] > 0
                and base_3y["net_revenue"] > 0
            ):
                snap["revenue_cagr_3y"] = _safe_calc(
                    calc_cagr, snap["net_revenue"], base_3y["net_revenue"], 3.0
                )

    return snapshots


# ── Shared SAG Query Helpers (Active Tickers, PIT Years, KgNode Fallback) ────


async def _load_active_sag_tickers(
    session: AsyncSession,
    *,
    clean_tenant: str,
    allowed_project_ids: set[str],
    as_of_dt: datetime | None = None,
) -> set[str]:
    from sag_api.db.models import Document, DocumentVersion

    doc_filters: list[Any] = [Document.is_active.is_(True), Document.project_id.is_not(None)]
    if clean_tenant:
        doc_filters.append(Document.tenant_id == clean_tenant)
    if as_of_dt is not None:
        # Strict PIT & non-revoked enforcement on DocumentVersion
        stmt = (
            select(Document.project_id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(
                *doc_filters,
                DocumentVersion.valid_from <= as_of_dt,
                DocumentVersion.valid_to > as_of_dt,
                DocumentVersion.status != "REVOKED",
            )
            .distinct()
        )
    else:
        stmt = select(Document.project_id).where(*doc_filters).distinct()
    rows = (await session.execute(stmt)).all()
    out: set[str] = set()
    for (pid,) in rows:
        sym = str(pid or "").strip().upper()
        if sym and ("*" in allowed_project_ids or sym in allowed_project_ids):
            out.add(sym)
    return out


async def _load_sag_verified_max_years(
    session: AsyncSession,
    *,
    clean_tenant: str,
    as_of_dt: datetime,
    tickers: Sequence[str] | None,
) -> dict[str, int]:
    """Fetch `MAX(fiscal_year)` of PIT-valid `VERIFIED` tables per ticker (zero JSONB loaded)."""
    from sag_api.db.models import Document, DocumentVersion
    from sag_api.db.models.stock_research import SagExtractedTable

    if tickers is not None and not tickers:
        return {}

    filters: list[Any] = [
        SagExtractedTable.verification_status == "VERIFIED",
        SagExtractedTable.valid_from <= as_of_dt,
        SagExtractedTable.fiscal_year.is_not(None),
        DocumentVersion.valid_from <= as_of_dt,
        DocumentVersion.valid_to > as_of_dt,
        DocumentVersion.status != "REVOKED",
        Document.is_active.is_(True),
    ]
    if clean_tenant:
        filters.append(Document.tenant_id == clean_tenant)
    if tickers:
        filters.append(SagExtractedTable.project_id.in_(sorted(set(tickers))))

    stmt = (
        select(SagExtractedTable.project_id, func.max(SagExtractedTable.fiscal_year))
        .join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(*filters)
        .group_by(SagExtractedTable.project_id)
    )
    rows = (await session.execute(stmt)).all()
    out: dict[str, int] = {}
    for tk, max_fy in rows:
        sym = str(tk or "").strip().upper()
        if sym and max_fy is not None:
            out[sym] = int(max_fy)
    return out


async def _load_kg_fallback_profiles(
    session: AsyncSession,
    *,
    tickers: Sequence[str],
    as_of_date: date,
) -> dict[str, dict[str, Any]]:
    from sag_api.db.models.stock_research import KgNode

    if not tickers:
        return {}
    rows = (
        await session.execute(
            select(KgNode.ticker, KgNode.label, KgNode.metadata_json).where(
                KgNode.node_type == "COMPANY",
                KgNode.ticker.in_(sorted(set(tickers))),
                KgNode.effective_from <= as_of_date,
                KgNode.valid_to > as_of_date,
            )
        )
    ).all()
    out: dict[str, dict[str, Any]] = {}
    for tk, lbl, meta in rows:
        sym = str(tk or "").strip().upper()
        if not sym:
            continue
        cur = out.setdefault(
            sym,
            {
                "ticker": sym,
                "company_name": sym,
                "sector": None,
                "industry": None,
                "exchange": "HOSE",
                "market_cap": None,
            },
        )
        if lbl and cur["company_name"] == sym:
            cur["company_name"] = str(lbl).strip()
        if isinstance(meta, Mapping):
            if not cur["sector"] and meta.get("sector"):
                cur["sector"] = str(meta["sector"]).strip() or None
            if not cur["industry"] and meta.get("industry"):
                cur["industry"] = str(meta["industry"]).strip() or None
    return out


async def _load_pit_macro_factors(session: AsyncSession, *, as_of_date: date) -> list[str]:
    from sag_api.db.models.stock_research import MacroSensitivitySnapshot

    try:
        m_rows = (
            await session.execute(
                select(MacroSensitivitySnapshot.macro_factor)
                .where(
                    MacroSensitivitySnapshot.effective_from <= as_of_date,
                    MacroSensitivitySnapshot.valid_to > as_of_date,
                )
                .distinct()
            )
        ).all()
        return sorted({str(mf).strip() for (mf,) in m_rows if mf and str(mf).strip()})
    except Exception:  # noqa: BLE001
        return []


# ── Catalog & Deterministic Universe Screening API ───────────────────────────


async def load_org_catalog(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    as_of_date: date,
    core_reader: CoreMetricsReaderProtocol | None = None,
    include_core_universe: bool = False,
) -> dict[str, Any]:
    """Load live tenant- & ACL-scoped issuer catalog directly from Core PG + active SAG scope."""
    clean_tenant = (principal.tenant_id or principal.organization_id or "").strip()
    allowed = {a.strip().upper() for a in principal.allowed_project_ids if a and a.strip()}
    as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)

    sag_tickers = await _load_active_sag_tickers(
        session,
        clean_tenant=clean_tenant,
        allowed_project_ids=allowed,
        as_of_dt=as_of_dt,
    )
    sag_max_fy = await _load_sag_verified_max_years(
        session,
        clean_tenant=clean_tenant,
        as_of_dt=as_of_dt,
        tickers=sorted(sag_tickers) if sag_tickers else [],
    )

    reader = core_reader
    if reader is None and (dsn := resolve_core_database_url()):
        reader = AsyncPGCoreMetricsReader(dsn)

    catalog_source: Literal["CORE_DB_READONLY", "SAG_LOCAL_FALLBACK"] = "SAG_LOCAL_FALLBACK"
    core_db_warning: str | None = None
    profile_by_ticker: dict[str, dict[str, Any]] = {}

    if reader is not None and (include_core_universe or sag_tickers):
        try:
            query_tickers = None if include_core_universe else sorted(sag_tickers)
            raw_profiles = await reader.fetch_issuer_profiles(query_tickers)
            catalog_source = "CORE_DB_READONLY"
            for row in raw_profiles:
                sym = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
                if not sym or ("*" not in allowed and sym not in allowed):
                    continue
                profile_by_ticker[sym] = {
                    "ticker": sym,
                    "company_name": str(row.get("company_name") or sym).strip() or sym,
                    "sector": (str(row["sector"]).strip() or None) if row.get("sector") else None,
                    "industry": (str(row["industry"]).strip() or None) if row.get("industry") else None,
                    "exchange": str(row.get("exchange") or "HOSE").strip() or "HOSE",
                    "market_cap": _finite_float(row.get("market_cap")),
                }
        except Exception as exc:  # noqa: BLE001
            core_db_warning = type(exc).__name__
            log.warning(
                "Core DB read-only catalog query failed (%s); falling back to SAG local metadata",
                core_db_warning,
            )

    missing_sag_meta = sorted(tk for tk in sag_tickers if tk not in profile_by_ticker)
    if missing_sag_meta:
        kg_profiles = await _load_kg_fallback_profiles(
            session,
            tickers=missing_sag_meta,
            as_of_date=as_of_date,
        )
        for tk, prof in kg_profiles.items():
            profile_by_ticker[tk] = prof

    all_profile_tickers = (
        sorted(sag_tickers | set(profile_by_ticker.keys()))
        if include_core_universe
        else sorted(sag_tickers)
    )

    profiles: list[dict[str, Any]] = []
    sector_set: set[str] = set()
    industry_set: set[str] = set()
    for sym in all_profile_tickers:
        meta = profile_by_ticker.get(sym)
        comp_name = str(meta["company_name"]) if meta else sym
        sector = meta["sector"] if meta else None
        industry = meta["industry"] if meta else None
        exchange = str(meta["exchange"]) if meta else "HOSE"
        market_cap = meta["market_cap"] if meta else None
        if sector:
            sector_set.add(sector)
        if industry:
            industry_set.add(industry)
        profiles.append(
            {
                "ticker": sym,
                "company_name": comp_name,
                "sector": sector,
                "industry": industry,
                "exchange": exchange,
                "market_cap": market_cap,
                "aliases": _build_aliases(sym, comp_name),
                "latest_fiscal_year": sag_max_fy.get(sym),
                "has_sag_docs": sym in sag_tickers,
            }
        )

    macro_factors = await _load_pit_macro_factors(session, as_of_date=as_of_date)
    return {
        "tickers": sorted(sag_tickers),
        "all_catalog_tickers": all_profile_tickers,
        "profiles": profiles,
        "sectors": sorted(sector_set),
        "industries": sorted(industry_set),
        "macro_factors": macro_factors,
        "catalog_source": catalog_source,
        "core_db_warning": core_db_warning,
    }


def _profile_matches_sectors(
    profile: Mapping[str, Any] | None,
    target_sectors: Sequence[str],
) -> bool:
    if not target_sectors:
        return True
    if not profile:
        return False
    haystack_parts = [
        str(profile.get("sector") or ""),
        str(profile.get("industry") or ""),
        str(profile.get("company_name") or ""),
        " ".join(str(a) for a in (profile.get("aliases") or ())),
    ]
    haystack = strip_vn_accents(" ".join(haystack_parts))
    if not haystack:
        return False
    for raw_sec in target_sectors:
        norm_sec = strip_vn_accents(raw_sec)
        if not norm_sec:
            continue
        if norm_sec in haystack:
            return True
        words = [w for w in norm_sec.split() if len(w) >= 2]
        if words and all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", haystack) for w in words):
            return True
    return False


def _passes_filters(
    row: Mapping[str, Any],
    filters: Sequence[Mapping[str, Any]],
) -> bool:
    for f in filters:
        metric = str(f.get("metric") or "").strip()
        op = str(f.get("op") or "").strip().lower()
        target = _finite_float(f.get("value"))
        if metric not in ALLOWED_SCREEN_METRICS or target is None:
            continue
        val = _finite_float(row.get(metric))
        if val is None:
            return False
        if op in (">=", "gte") and not (val >= target):
            return False
        if op in (">", "gt") and not (val > target):
            return False
        if op in ("<=", "lte") and not (val <= target):
            return False
        if op in ("<", "lt") and not (val < target):
            return False
        if op in ("=", "==", "eq") and not (abs(val - target) <= 1e-9):
            return False
    return True


def _format_screen_row(
    rec: Mapping[str, Any],
    profile: Mapping[str, Any] | None,
    *,
    has_sag_docs: bool,
) -> dict[str, Any]:
    sym = str(rec.get("ticker") or "").upper()
    out: dict[str, Any] = {
        "ticker": sym,
        "company_name": str((profile or {}).get("company_name") or sym),
        "sector": (profile or {}).get("sector"),
        "industry": (profile or {}).get("industry"),
        "exchange": str((profile or {}).get("exchange") or "HOSE"),
        "has_sag_docs": has_sag_docs,
        "fiscal_year": int(rec.get("fiscal_year") or 0),
        "fiscal_quarter": int(rec.get("fiscal_quarter") or 0),
        "source_kind": str(rec.get("source_kind") or "CORE_DB_PIT"),
        "source_table_id": rec.get("source_table_id"),
        "document_version_id": rec.get("document_version_id"),
        "page_from": rec.get("page_from"),
        "currency": str(rec.get("currency") or "VND"),
    }
    vf = rec.get("valid_from")
    if isinstance(vf, datetime):
        out["valid_from"] = vf.isoformat()
    elif rec.get("effective_published_date") is not None:
        eff_d = _coerce_date(rec.get("effective_published_date"))
        out["valid_from"] = eff_d.isoformat() if eff_d else ""
    else:
        out["valid_from"] = ""

    for col in _ALL_SNAPSHOT_METRIC_FIELDS:
        val = _finite_float(rec.get(col))
        if val is not None:
            out[col] = val
    return out


async def _load_sag_verified_best_for_tickers(
    session: AsyncSession,
    *,
    clean_tenant: str,
    as_of_dt: datetime,
    tickers: Sequence[str],
    prefer_annual: bool,
) -> dict[str, dict[str, Any]]:
    from sag_api.db.models import Document, DocumentVersion
    from sag_api.db.models.stock_research import SagExtractedTable

    norm_tks = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not norm_tks:
        return {}

    filters: list[Any] = [
        SagExtractedTable.project_id.in_(norm_tks),
        SagExtractedTable.verification_status == "VERIFIED",
        SagExtractedTable.valid_from <= as_of_dt,
        DocumentVersion.valid_from <= as_of_dt,
        DocumentVersion.valid_to > as_of_dt,
        DocumentVersion.status != "REVOKED",
        Document.is_active.is_(True),
    ]
    if clean_tenant:
        filters.append(Document.tenant_id == clean_tenant)

    stmt = (
        select(SagExtractedTable, DocumentVersion.source_published_at)
        .join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(*filters)
        .order_by(SagExtractedTable.fiscal_year.desc(), SagExtractedTable.page_from.asc())
    )
    tbl_rows = (await session.execute(stmt)).all()
    if not tbl_rows:
        return {}

    table_dicts: list[dict[str, Any]] = []
    for tbl_obj, src_pub in tbl_rows:
        eff_vf = _coerce_utc(src_pub) or _coerce_utc(tbl_obj.valid_from) or as_of_dt
        table_dicts.append(
            {
                "table_id": str(tbl_obj.table_id),
                "project_id": str(tbl_obj.project_id),
                "document_version_id": str(tbl_obj.document_version_id),
                "sub_doc_slot": str(tbl_obj.sub_doc_slot or ""),
                "fiscal_year": tbl_obj.fiscal_year,
                "fiscal_quarter": int(tbl_obj.fiscal_quarter or 0),
                "table_title": str(tbl_obj.table_title or ""),
                "section_path": str(tbl_obj.section_path or ""),
                "page_from": int(tbl_obj.page_from or 1),
                "currency": str(tbl_obj.currency or "VND"),
                "unit_multiplier": float(tbl_obj.unit_multiplier or 1.0),
                "headers_json": tbl_obj.headers_json if isinstance(tbl_obj.headers_json, list) else [],
                "rows_json": tbl_obj.rows_json if isinstance(tbl_obj.rows_json, list) else [],
                "verification_status": str(tbl_obj.verification_status),
                "valid_from": eff_vf,
            }
        )

    snaps = extract_metrics_from_verified_tables(table_dicts, tenant_id=clean_tenant or "aiinvest-default")
    best_by_tk: dict[str, tuple[tuple[int, int, int, int, float], dict[str, Any]]] = {}
    for snap in snaps:
        sym = str(snap["ticker"]).upper()
        fq = int(snap.get("fiscal_quarter") or 0)
        vf = _coerce_utc(snap.get("valid_from")) or as_of_dt
        rank_key = (
            1 if (prefer_annual and fq == 0) else 0,
            int(snap["fiscal_year"]),
            fq,
            1 if str(snap.get("currency") or "").upper() == "VND" else 0,
            vf.timestamp(),
        )
        prev = best_by_tk.get(sym)
        if prev is None or rank_key > prev[0]:
            best_by_tk[sym] = (rank_key, snap)
    return {sym: item[1] for sym, item in best_by_tk.items()}


async def screen_universe_metrics(
    session: AsyncSession,
    *,
    tenant_id: str,
    as_of_date: date,
    allowed_project_ids: Iterable[str] = frozenset({"*"}),
    core_reader: CoreMetricsReaderProtocol | None = None,
    scope: Literal["sag_ingested", "core_universe"] = "sag_ingested",
    sectors: Sequence[str] | None = None,
    tickers: Sequence[str] | None = None,
    filters: Sequence[Mapping[str, Any]] | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    use_default_quality_screen: bool = False,
    prefer_annual: bool = True,
    limit: int = 15,
) -> tuple[list[dict[str, Any]], int, str]:
    """Deterministically screen 100% of PIT-valid issuers via Core PG read-only + PDF fallback.

    Returns `(ranked_rows[:limit], total_matching_count, criteria_description)`.
    """
    clean_tenant = tenant_id.strip()
    allowed = {a.strip().upper() for a in allowed_project_ids if a and a.strip()}
    as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)

    sag_tickers = await _load_active_sag_tickers(
        session,
        clean_tenant=clean_tenant,
        allowed_project_ids=allowed,
        as_of_dt=as_of_dt,
    )

    req_tickers: set[str] | None = None
    if tickers is not None:
        req_tickers = {
            t.strip().upper()
            for t in tickers
            if t and t.strip() and ("*" in allowed or t.strip().upper() in allowed)
        }

    if scope == "sag_ingested":
        cand_set = (sag_tickers & req_tickers) if req_tickers is not None else sag_tickers
        if not cand_set:
            return [], 0, "Không có mã chứng khoán nào đang hoạt động trong phạm vi tài liệu."
        candidate_tickers: list[str] | None = sorted(cand_set)
    else:
        if req_tickers is not None and not req_tickers:
            return [], 0, "Không có mã chứng khoán nào khớp phạm vi quyền truy cập."
        candidate_tickers = sorted(req_tickers) if req_tickers is not None else None

    active_filters = [f for f in (filters or ()) if str(f.get("metric") or "") in ALLOWED_SCREEN_METRICS]
    eff_sort = sort_by if (sort_by and sort_by in ALLOWED_SCREEN_METRICS) else None
    need_statements = bool(
        use_default_quality_screen
        or eff_sort is None
        or (eff_sort in _STATEMENT_DERIVED_METRICS)
        or any(str(f.get("metric") or "").strip() in _STATEMENT_DERIVED_METRICS for f in active_filters)
    )

    reader = core_reader
    if reader is None and (dsn := resolve_core_database_url()):
        reader = AsyncPGCoreMetricsReader(dsn)

    core_available = False
    core_profiles: dict[str, dict[str, Any]] = {}
    core_best_by_tk: dict[str, dict[str, Any]] = {}

    if reader is not None:
        try:
            coros: list[Awaitable[Any]] = [
                reader.fetch_issuer_profiles(candidate_tickers),
                reader.fetch_pit_financial_ratios(as_of_date, tickers=candidate_tickers),
                reader.fetch_pit_market_caps(as_of_date, tickers=candidate_tickers),
            ]
            if need_statements:
                coros.append(
                    reader.fetch_pit_financial_statements(
                        as_of_date,
                        tickers=candidate_tickers,
                        prefer_annual=prefer_annual,
                    )
                )
            fetched = await asyncio.gather(*coros)
            raw_profiles = fetched[0]
            raw_ratios = fetched[1]
            pit_market_caps: dict[str, float] = dict(fetched[2] or {})
            raw_stmts = fetched[3] if need_statements else []
            core_available = True
            allow_live_mcap_fallback = (date.today() - as_of_date).days <= 3

            for prow in raw_profiles:
                sym = str(prow.get("ticker") or prow.get("symbol") or "").strip().upper()
                if not sym or ("*" not in allowed and sym not in allowed):
                    continue
                if candidate_tickers is not None and sym not in candidate_tickers:
                    continue
                comp_name = str(prow.get("company_name") or sym).strip() or sym
                pit_mc = _finite_float(pit_market_caps.get(sym))
                if pit_mc is not None and pit_mc <= 0:
                    pit_mc = None
                raw_live_mc = _finite_float(prow.get("market_cap")) if allow_live_mcap_fallback else None
                live_mc = raw_live_mc if (raw_live_mc is not None and raw_live_mc > 0) else None
                core_profiles[sym] = {
                    "ticker": sym,
                    "company_name": comp_name,
                    "sector": (str(prow["sector"]).strip() or None) if prow.get("sector") else None,
                    "industry": (str(prow["industry"]).strip() or None) if prow.get("industry") else None,
                    "exchange": str(prow.get("exchange") or "HOSE").strip() or "HOSE",
                    "market_cap": pit_mc if pit_mc is not None else live_mc,
                    "aliases": _build_aliases(sym, comp_name),
                }

            stmt_metrics = (
                _extract_core_statement_metrics(
                    raw_stmts,
                    as_of_date=as_of_date,
                    include_quarterly=not prefer_annual,
                )
                if raw_stmts
                else {}
            )
            norm_ratios = _normalize_core_ratio_rows(raw_ratios, stmt_metrics=stmt_metrics)

            core_candidates: dict[str, tuple[tuple[int, int, int, int], dict[str, Any]]] = {}
            latest_valuation_by_tk: dict[str, tuple[tuple[date, date], dict[str, Any]]] = {}
            for r_item in norm_ratios:
                sym = r_item["ticker"]
                if "*" not in allowed and sym not in allowed:
                    continue
                if candidate_tickers is not None and sym not in candidate_tickers:
                    continue
                fy = int(r_item["fiscal_year"])
                fq = int(r_item["fiscal_quarter"])
                eff_pub = r_item["effective_published_date"]
                # Defense-in-depth PIT check after realistic publication-lag adjustment
                if r_item["ratio_date"] > as_of_date or eff_pub > as_of_date:
                    continue
                val_key = (r_item["ratio_date"], eff_pub)
                if any(r_item.get(vc) is not None for vc in VALUATION_METRIC_COLS):
                    prev_val = latest_valuation_by_tk.get(sym)
                    if prev_val is None or val_key > prev_val[0]:
                        latest_valuation_by_tk[sym] = (val_key, r_item)
                rank_key = (
                    1 if (prefer_annual and fq == 0) else 0,
                    fy,
                    fq,
                    eff_pub.toordinal(),
                )
                prev = core_candidates.get(sym)
                if prev is None or rank_key > prev[0]:
                    core_candidates[sym] = (rank_key, dict(r_item))

            # Also consider statement-only Core years when no financial_ratios row exists for a newer year
            for (sym, fy), s_rec in stmt_metrics.items():
                if "*" not in allowed and sym not in allowed:
                    continue
                if candidate_tickers is not None and sym not in candidate_tickers:
                    continue
                eff_pub = s_rec["effective_published_date"]
                sfq = int(s_rec.get("fiscal_quarter") or 0)
                rank_key = (1 if (prefer_annual and sfq == 0) else 0, fy, sfq, eff_pub.toordinal())
                prev = core_candidates.get(sym)
                if prev is None or rank_key > prev[0]:
                    merged = dict(s_rec)
                    core_candidates[sym] = (rank_key, merged)

            for sym, (_rk, rec) in core_candidates.items():
                rec["source_kind"] = "CORE_DB_PIT"
                rec["currency"] = "VND"
                s_rec = stmt_metrics.get((sym, int(rec["fiscal_year"])))
                if s_rec is not None:
                    for k, v in s_rec.items():
                        if k in MONETARY_METRIC_COLS and v is not None:
                            rec[k] = v
                        elif k in RATIO_METRIC_COLS and v is not None:
                            # Authoritative AST-calculated statement ratios override noisy ETL ratio cells
                            rec[k] = v
                    _enrich_snapshot_ratios(rec)
                latest_val_pair = latest_valuation_by_tk.get(sym)
                if latest_val_pair is not None:
                    v_item = latest_val_pair[1]
                    rec["_valuation_ratio_date"] = v_item.get("ratio_date")
                    for v_col in ("pe", "pb", "ev_ebitda", "fcf_yield"):
                        if rec.get(v_col) is None and v_item.get(v_col) is not None:
                            rec[v_col] = v_item[v_col]
                elif rec.get("ratio_date") is not None:
                    rec["_valuation_ratio_date"] = rec.get("ratio_date")
                prof = core_profiles.get(sym)
                if prof and prof.get("market_cap") is not None:
                    rec["market_cap"] = prof["market_cap"]
                    # Authoritative PIT annual FCF yield from statements + PIT market_cap (avoids ETL *4 bug)
                    fcf_v = _finite_float(rec.get("fcf"))
                    if fcf_v is not None and prof["market_cap"] > 0 and int(rec.get("fiscal_quarter") or 0) == 0:
                        rec["fcf_yield"] = _safe_calc(calc_safe_div, fcf_v, float(prof["market_cap"]))
                core_best_by_tk[sym] = rec
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Core DB read-only screener query failed (%s); falling back to SAG VERIFIED tables",
                type(exc).__name__,
            )

    # Lightweight PIT SAG coverage check (zero JSONB loaded) + smart PDF overlay
    sag_cand_list = (
        sorted(sag_tickers & set(candidate_tickers))
        if candidate_tickers is not None
        else sorted(sag_tickers)
    )
    sag_max_fy = await _load_sag_verified_max_years(
        session,
        clean_tenant=clean_tenant,
        as_of_dt=as_of_dt,
        tickers=sag_cand_list,
    )

    if not core_available:
        pdf_overlay_tickers = sorted(sag_max_fy.keys())
    else:
        pdf_overlay_tickers = sorted(
            sym
            for sym, pdf_fy in sag_max_fy.items()
            if (sym not in core_best_by_tk)
            or (pdf_fy > int(core_best_by_tk[sym].get("fiscal_year") or 0))
            or (
                need_statements
                and all(core_best_by_tk[sym].get(col) is None for col in MONETARY_METRIC_COLS)
            )
        )

    merged_by_tk: dict[str, dict[str, Any]] = dict(core_best_by_tk)
    if pdf_overlay_tickers:
        pdf_best = await _load_sag_verified_best_for_tickers(
            session,
            clean_tenant=clean_tenant,
            as_of_dt=as_of_dt,
            tickers=pdf_overlay_tickers,
            prefer_annual=prefer_annual,
        )
        for sym, pdf_rec in pdf_best.items():
            core_rec = merged_by_tk.get(sym)
            if core_rec is None:
                merged_by_tk[sym] = pdf_rec
                continue

            pdf_fy = int(pdf_rec.get("fiscal_year") or 0)
            core_fy = int(core_rec.get("fiscal_year") or 0)
            pdf_ccy = str(pdf_rec.get("currency") or "VND").strip().upper()
            core_ccy = str(core_rec.get("currency") or "VND").strip().upper()

            if pdf_fy >= core_fy:
                # Never mix USD PDF statements with VND Core rows
                if pdf_ccy != core_ccy:
                    if pdf_fy > core_fy:
                        merged_by_tk[sym] = pdf_rec
                    continue

                if pdf_fy > core_fy:
                    # Strictly isolate fresher PDF year: do NOT carry over stale prior-year fundamentals;
                    # only retain Core valuation multiples if their ratio_date matches `pdf_fy`.
                    combined = dict(pdf_rec)
                    val_date = _coerce_date(core_rec.get("_valuation_ratio_date") or core_rec.get("ratio_date"))
                    if val_date is not None and val_date.year == pdf_fy:
                        for v_col in ("pe", "pb", "ev_ebitda", "fcf_yield"):
                            if combined.get(v_col) is None and core_rec.get(v_col) is not None:
                                combined[v_col] = core_rec[v_col]
                        combined["valuation_as_of_date"] = val_date.isoformat()
                    if combined.get("market_cap") is None and core_rec.get("market_cap") is not None:
                        combined["market_cap"] = core_rec["market_cap"]
                else:
                    # Same fiscal year & currency: Core PG is authoritative; PDF only fills missing gaps
                    combined = dict(core_rec)
                    filled_any = False
                    for col in _ALL_SNAPSHOT_METRIC_FIELDS:
                        pv = pdf_rec.get(col)
                        if pv is not None and combined.get(col) is None:
                            combined[col] = pv
                            filled_any = True
                    if filled_any and combined.get("source_table_id") is None:
                        combined["source_table_id"] = pdf_rec.get("source_table_id")
                        combined["document_version_id"] = pdf_rec.get("document_version_id")
                        combined["page_from"] = pdf_rec.get("page_from")
                        combined["source_kind"] = "HYBRID_CORE_AND_VERIFIED_PDF"
                    merged_by_tk[sym] = combined
                    continue
                combined["source_kind"] = "HYBRID_CORE_AND_VERIFIED_PDF"
                merged_by_tk[sym] = combined

    # Fallback KG profiles only for SAG tickers missing from Core PG catalog
    missing_prof_tks = sorted(sym for sym in merged_by_tk if sym not in core_profiles)
    if missing_prof_tks:
        kg_profs = await _load_kg_fallback_profiles(
            session,
            tickers=missing_prof_tks,
            as_of_date=as_of_date,
        )
        for sym, kprof in kg_profs.items():
            comp_name = str(kprof.get("company_name") or sym)
            kprof["aliases"] = _build_aliases(sym, comp_name)
            core_profiles[sym] = kprof

    if not merged_by_tk:
        return [], 0, "Không có dữ liệu chỉ số tài chính PIT hợp lệ."

    # Scope + Sector filter across eligible issuers
    eligible_pairs: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for sym, rec in sorted(merged_by_tk.items()):
        if scope == "sag_ingested" and sym not in sag_tickers:
            continue
        prof = core_profiles.get(sym)
        if sectors and not _profile_matches_sectors(prof, sectors):
            continue
        eligible_pairs.append((rec, prof))

    total_eligible = len(eligible_pairs)
    if not eligible_pairs:
        return [], 0, "Không có doanh nghiệp nào khớp phạm vi ngành/mã tại thời điểm PIT."

    filtered_pairs = [
        (rec, prof)
        for rec, prof in eligible_pairs
        if not active_filters or _passes_filters(rec, active_filters)
    ]
    if not filtered_pairs:
        return [], total_eligible, f"0/{total_eligible} doanh nghiệp thỏa mãn điều kiện lọc định lượng."

    is_desc = str(sort_dir or "desc").lower() != "asc"
    if eff_sort and not use_default_quality_screen:
        def _sort_key(pair: tuple[dict[str, Any], dict[str, Any] | None]) -> tuple[int, float, str]:
            r = pair[0]
            v = _finite_float(r.get(eff_sort))
            if v is None:
                return (1, 0.0, str(r["ticker"]))
            return (0, -v if is_desc else v, str(r["ticker"]))

        ordered = sorted(filtered_pairs, key=_sort_key)
        out_rows = [
            _format_screen_row(r, p, has_sag_docs=str(r["ticker"]) in sag_tickers)
            for r, p in ordered[: max(1, limit)]
        ]
        dir_label = "giảm dần" if is_desc else "tăng dần"
        src_note = "Core PG Read-Only + PDF" if core_available else "SAG VERIFIED PDF"
        desc_text = (
            f"Sàng lọc PIT ({src_note}) trên {total_eligible} mã theo `{eff_sort}` ({dir_label})"
            + (f" với {len(active_filters)} bộ lọc" if active_filters else "")
        )
        return out_rows, len(filtered_pairs), desc_text

    # Transparent 4-factor percentile composite quality screen
    factor_defs: list[tuple[str, tuple[str, ...], bool]] = [
        ("gross_margin", ("gross_margin",), True),
        ("profitability", ("roe", "net_margin"), True),
        ("leverage", ("debt_to_equity", "liabilities_to_equity"), False),
        ("cash_growth", ("cfo_to_net_income", "revenue_yoy"), True),
    ]

    extracted_factors: dict[str, dict[str, float]] = {name: {} for name, _, _ in factor_defs}
    for r, _p in filtered_pairs:
        sym = str(r["ticker"]).upper()
        for f_name, col_opts, _higher in factor_defs:
            for col in col_opts:
                v = _finite_float(r.get(col))
                if v is not None:
                    extracted_factors[f_name][sym] = v
                    break

    pct_scores: dict[str, dict[str, float]] = defaultdict(dict)
    for f_name, _cols, higher_better in factor_defs:
        vals_map = extracted_factors[f_name]
        if not vals_map:
            continue
        sorted_syms = sorted(vals_map.keys(), key=lambda s: vals_map[s], reverse=not higher_better)
        n_vals = len(sorted_syms)
        idx = 0
        while idx < n_vals:
            j = idx
            val_i = vals_map[sorted_syms[idx]]
            while j + 1 < n_vals and abs(vals_map[sorted_syms[j + 1]] - val_i) <= 1e-12:
                j += 1
            avg_pos = (idx + j) / 2.0
            pct = 1.0 if n_vals == 1 else (avg_pos / float(n_vals - 1))
            for k in range(idx, j + 1):
                pct_scores[sorted_syms[k]][f_name] = pct
            idx = j + 1

    scored_items: list[tuple[tuple[float, int, float, str], dict[str, Any], dict[str, Any] | None, float]] = []
    for r, p in filtered_pairs:
        sym = str(r["ticker"]).upper()
        s_map = pct_scores.get(sym, {})
        avg_pct = (sum(s_map.values()) / float(len(s_map))) if s_map else 0.0
        tie_sort = _finite_float(r.get(eff_sort)) if eff_sort else None
        tie_rank = (-tie_sort if is_desc else tie_sort) if tie_sort is not None else float("inf")
        scored_items.append(
            ((-round(avg_pct, 6), -len(s_map), tie_rank, sym), r, p, round(avg_pct * 100.0, 2))
        )

    scored_items.sort(key=lambda item: item[0])
    out_rows = []
    for _key, r, p, q_score in scored_items[: max(1, limit)]:
        d = _format_screen_row(r, p, has_sag_docs=str(r["ticker"]) in sag_tickers)
        d["quality_score"] = q_score
        out_rows.append(d)

    src_note = "Core PG Read-Only + PDF" if core_available else "SAG VERIFIED PDF"
    desc_text = (
        f"Xếp hạng tổng hợp 4 trụ cột ({src_note}: gross_margin ↑, ROE/net_margin ↑, "
        f"debt_to_equity ↓, cfo_to_net_income/revenue_yoy ↑) trên {len(filtered_pairs)}/{total_eligible} mã PIT"
    )
    return out_rows, len(filtered_pairs), desc_text
