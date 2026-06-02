"""Vietnam stock analysis tool — fetches OHLCV + fundamentals via VietFin.

No API key needed. Falls back to text response if vietfin is not installed.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


def _analyze_vn_stock(symbol: str, days: int = 30) -> dict[str, Any]:
    """Fetch OHLCV + fundamentals for a Vietnam stock.

    Uses VietFin for price.historical (DNSE still works).
    Uses vnstock for company profile + fundamental ratios (VietFin TCBS APIs are dead).

    Args:
        symbol: Stock symbol (e.g. "VIX", "VCB", "VNM").
        days: Number of trailing days of OHLCV data.

    Returns:
        Dict with price data, fundamentals, and summary stats.
    """
    from vietfin import vf

    sym_lower = symbol.lower()
    sym_upper = symbol.upper()
    end = datetime.now()
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    result: dict[str, Any] = {
        "symbol": sym_upper,
        "days": days,
        "start_date": start_str,
        "end_date": end_str,
    }

    ohlcv = vf.equity.price.historical(
        symbol=sym_lower,
        start_date=start_str,
        end_date=end_str,
        interval="1d",
        provider="dnse",
    )
    df = ohlcv.to_df()
    if not df.empty:
        df_sorted = df.sort_values("date").reset_index()
        records = df_sorted.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        result["price_data"] = records

        closes = [r["close"] for r in result["price_data"] if "close" in r]
        volumes = [r["volume"] for r in result["price_data"] if "volume" in r]
        if closes:
            result["summary"] = {
                "first_close": closes[0],
                "last_close": closes[-1],
                "high": max(closes),
                "low": min(closes),
                "avg_volume": round(sum(volumes) / len(volumes)) if volumes else 0,
                "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes[0] else 0,
                "trading_days": len(closes),
            }

    _fetch_profile(result, sym_upper)
    _fetch_ratios(result, sym_upper)
    _fetch_financials(result, sym_upper)

    return result


def _fetch_profile(result: dict[str, Any], symbol: str) -> None:
    """Fetch company profile via vnstock."""
    try:
        from vnstock import Vnstock
        stock = Vnstock().stock(symbol=symbol, source="KBS")
        profile = stock.company.overview()
        if profile is not None and not profile.empty:
            row = profile.iloc[0].to_dict()
            # Get clean short name from new Company API if available
            short_name = ""
            try:
                from vnstock.api.company import Company as CompanyAPI
                ci = CompanyAPI(symbol=symbol, source="VCI").overview()
                if ci is not None and not ci.empty:
                    short_name = ci.iloc[0].get("organ_name", "")
            except Exception:
                pass
            result["profile"] = {
                "name": short_name or symbol,
                "legal_name": row.get("symbol", ""),
                "exchange": row.get("exchange", ""),
                "industry": row.get("company_type", ""),
                "employees": row.get("number_of_employees"),
                "website": str(row.get("website", "")),
            }
        else:
            result["profile"] = {"error": "profile data unavailable"}
    except Exception as exc:
        logger.warning("Profile fetch failed for %s: %s", symbol, exc)
        result["profile"] = {"error": "profile data unavailable"}


def _fetch_ratios(result: dict[str, Any], symbol: str) -> None:
    """Fetch fundamental ratios via vnstock."""
    try:
        from vnstock.api.financial import Finance
        f = Finance(symbol=symbol, source="KBS")
        ratios = f.ratio()
        if ratios is not None and not ratios.empty:
            period_cols = [c for c in ratios.columns if c not in ("item", "item_en", "item_id")]
            if period_cols:
                latest = period_cols[-1]
                raw = {}
                for _, row in ratios.iterrows():
                    item = str(row["item"]).strip()
                    val = row[latest]
                    if not isinstance(val, (int, float)):
                        continue
                    raw[item] = val

                RATIO_MAP: dict[str, str] = {
                    "P/E": "pe",
                    "P/B": "pb",
                    "EPS": "eps",
                    "Beta": "beta",
                    "ROE": "roe",
                    "ROA": "roa",
                    "Tỷ suất cổ tức": "dividend_yield",
                    "Giá trị sổ sách của cổ phiếu (BVPS)": "price_to_book",
                }
                mapped = {}
                for vn_name, eng_name in RATIO_MAP.items():
                    for k, v in raw.items():
                        if vn_name in k:
                            mapped[eng_name] = v
                            break
                if mapped:
                    result["ratios"] = mapped
                    return
        result["ratios"] = {"error": "fundamental ratios unavailable"}
    except Exception as exc:
        logger.warning("Ratios fetch failed for %s: %s", symbol, exc)
        result["ratios"] = {"error": "fundamental ratios unavailable"}


def _extract_fin_item(df, period_cols, keywords):
    """Extract latest value from a financial DataFrame by item name keywords."""
    if df is None or df.empty or not period_cols:
        return None
    latest = period_cols[-1]
    for _, row in df.iterrows():
        item = str(row["item"]).strip()
        if any(kw in item for kw in keywords):
            val = row[latest]
            if isinstance(val, (int, float)):
                return val
    return None


def _fetch_financials(result: dict[str, Any], symbol: str) -> None:
    """Fetch balance sheet, income statement, and cash flow via vnstock."""
    try:
        from vnstock.api.financial import Finance
        f = Finance(symbol=symbol, source="KBS")

        bs = f.balance_sheet()
        inc = f.income_statement()
        cf = f.cash_flow()

        bs_cols = [c for c in bs.columns if c not in ("item", "item_en", "item_id")] if bs is not None else []
        inc_cols = [c for c in inc.columns if c not in ("item", "item_en", "item_id")] if inc is not None else []
        cf_cols = [c for c in cf.columns if c not in ("item", "item_en", "item_id")] if cf is not None else []

        result["financials"] = {}

        if bs_cols:
            bs_latest = bs_cols[-1]
            assets = _extract_fin_item(bs, bs_cols, ["TỔNG CỘNG TÀI SẢN"])
            liab = _extract_fin_item(bs, bs_cols, ["TỔNG NỢ PHẢI TRẢ"])
            equity = assets - liab if assets is not None and liab is not None else None
            result["financials"]["balance_sheet"] = {
                "period": bs_latest,
                "total_assets": assets,
                "total_liabilities": liab,
                "total_equity": equity,
            }

        if inc_cols:
            inc_latest = inc_cols[-1]
            result["financials"]["income_statement"] = {
                "period": inc_latest,
                "revenue": (_extract_fin_item(inc, inc_cols, ["Doanh thu thuần"])
                            or _extract_fin_item(inc, inc_cols, ["Thu nhập lãi thuần"])),
                "gross_profit": (_extract_fin_item(inc, inc_cols, ["Lợi nhuận gộp"])
                                 or _extract_fin_item(inc, inc_cols, ["Lãi/lỗ thuần từ hoạt động dịch vụ"])),
                "net_profit": _extract_fin_item(inc, inc_cols, ["Lợi nhuận sau thuế"]),
            }

        if cf_cols:
            cf_latest = cf_cols[-1]
            result["financials"]["cash_flow"] = {
                "period": cf_latest,
                "operating": _extract_fin_item(cf, cf_cols, ["Lưu chuyển tiền thuần từ hoạt động kinh doanh"]),
                "investing": _extract_fin_item(cf, cf_cols, ["Lưu chuyển tiền thuần từ hoạt động đầu tư"]),
                "financing": _extract_fin_item(cf, cf_cols, ["Lưu chuyển tiền thuần từ hoạt động tài chính"]),
                "net_cf": _extract_fin_item(cf, cf_cols, ["Lưu chuyển tiền thuần trong kỳ"]),
            }

        if not result["financials"]:
            result.pop("financials")
    except Exception as exc:
        logger.warning("Financials fetch failed for %s: %s", symbol, exc)


def _check_vietfin() -> bool:
    try:
        import vietfin  # noqa: F401
        import vnstock  # noqa: F401
        return True
    except ImportError:
        return False


class VNStockAnalyzeTool(BaseTool):
    """Analyze a Vietnam stock: OHLCV (VietFin DNSE), profile + ratios + financials (vnstock)."""

    name = "vn_stock_analyze"
    description = (
        "Fetch OHLCV price history + company profile + fundamental ratios "
        "+ balance sheet + income statement + cash flow "
        "for a Vietnam stock (HOSE/HNX/UPCOM). No API key needed. "
        "Use this INSTEAD of web_search when the user asks about a VN stock."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock symbol (e.g. VIX, VCB, VNM, FPT, HPG)",
            },
            "days": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "Number of trailing days of data (default 30, max 365)",
                "default": 30,
            },
        },
        "required": ["symbol"],
    }
    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        return _check_vietfin()

    def execute(self, **kwargs: Any) -> str:
        raw_symbol = kwargs.get("symbol")
        if not raw_symbol:
            return json.dumps({"status": "error", "error": "symbol is required"}, ensure_ascii=False)
        symbol = str(raw_symbol).strip().upper()

        raw_days = kwargs.get("days")
        if raw_days is None:
            days = 30
        else:
            days = min(int(raw_days), 365)
        run_dir = kwargs.get("run_dir")

        try:
            data = _analyze_vn_stock(symbol, days)
            price_data = data.get("price_data", [])

            # Write OHLCV CSV to run_dir/artifacts/ when run_dir is available
            if run_dir and price_data:
                try:
                    artifacts_dir = Path(str(run_dir)) / "artifacts"
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    csv_path = artifacts_dir / f"ohlcv_{symbol.lower()}.csv"
                    ohlcv_cols = ["date", "open", "high", "low", "close", "volume"]
                    with csv_path.open("w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=ohlcv_cols, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(price_data)
                    logger.info("Wrote OHLCV CSV to %s (%d rows)", csv_path, len(price_data))
                except Exception as csv_err:
                    logger.warning("Failed to write OHLCV CSV for %s: %s", symbol, csv_err)

            return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)
        except ImportError:
            return json.dumps({
                "status": "error",
                "error": "Required packages not installed. Run: pip install vietfin vnstock",
            }, ensure_ascii=False)
        except Exception as exc:
            logger.warning("vn_stock_analyze failed for %s: %s", symbol, exc)
            return json.dumps({
                "status": "error",
                "error": f"Failed to analyze {symbol}: {exc}",
            }, ensure_ascii=False)
