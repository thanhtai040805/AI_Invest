import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

from app.services.pg_pool import DB_URL
from app.risk_vn.composite_scorer import VNCompositeRiskScorer
from app.risk_vn.layers import (
    compute_quant_risk,
    compute_fundamental_risk,
    compute_market_structure_risk,
    compute_macro_vn_risk,
    compute_global_risk,
    compute_regulatory_risk,
    compute_behavioral_risk,
    fetch_cafef_news,
    map_news_to_symbols,
)
from app.dataflows.vendors.vn.sector_groups import classify

logger = logging.getLogger(__name__)
TZ_VN = timezone(timedelta(hours=7))

LAYER_COMPUTE_FN = {
    "layer1_quant": compute_quant_risk,
    "layer2_fundamental": compute_fundamental_risk,
    "layer3_market_vn": compute_market_structure_risk,
    "layer4_macro_vn": compute_macro_vn_risk,
    "layer5_global": compute_global_risk,
    "layer6_regulatory": compute_regulatory_risk,
    "layer7_behavioral": compute_behavioral_risk,
}


def run_assessment(calc_date: Optional[date] = None) -> dict:
    if calc_date is None:
        calc_date = datetime.now(TZ_VN).date()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    scorer = VNCompositeRiskScorer()

    try:
        symbols, sector_map = _load_symbols(cur)
        logger.info("CRS assessment for %s: %d symbols", calc_date, len(symbols))

        # 1. Fetch CafeF news (httpx)
        logger.info("  Fetching CafeF news...")
        articles = fetch_cafef_news(known_symbols=set(symbols))
        symbol_news = map_news_to_symbols(articles, set(symbols))
        logger.info("  CafeF news mapped to %d symbols", len(symbol_news))

        # 2. Load data for each layer
        logger.info("  Loading layer data...")
        ohlcv_data = _load_ohlcv(cur, symbols, calc_date)
        tech_data = _load_tech_data(cur, symbols, calc_date)
        fs_data = _load_financial_statements(cur, symbols)
        fr_data = _load_financial_ratios(cur, symbols)
        macro_data = _load_macro_indicators(cur, calc_date)
        news_events = _load_news_events(cur, symbols, calc_date)

        # 3. Compute each layer
        logger.info("  Computing 7 layers...")
        layer_results: dict[str, dict[str, dict]] = {}
        for layer_key, fn in LAYER_COMPUTE_FN.items():
            if layer_key == "layer1_quant":
                kwargs = {"symbols": symbols, "ohlcv_data": ohlcv_data, "tech_data": tech_data}
            elif layer_key == "layer2_fundamental":
                kwargs = {"symbols": symbols, "fs_data": fs_data, "fr_data": fr_data}
            elif layer_key == "layer3_market_vn":
                kwargs = {"symbols": symbols, "tech_data": tech_data, "symbol_news": symbol_news}
            elif layer_key == "layer4_macro_vn":
                kwargs = {"symbols": symbols, "sector_map": sector_map, "macro_data": macro_data}
            elif layer_key == "layer5_global":
                kwargs = {"symbols": symbols, "sector_map": sector_map, "macro_data": macro_data}
            elif layer_key == "layer6_regulatory":
                kwargs = {"symbols": symbols, "sector_map": sector_map, "symbol_news": symbol_news, "news_events": news_events}
            elif layer_key == "layer7_behavioral":
                kwargs = {"symbols": symbols, "tech_data": tech_data, "news_events": news_events, "calc_date": calc_date}
            else:
                kwargs = {"symbols": symbols}

            layer_results[layer_key] = fn(**kwargs)
            logger.info("    %s done", layer_key)

        # 4. Compute CRS per symbol
        logger.info("  Computing CRS scores...")
        assessments: list[tuple] = []
        for sym in symbols:
            ls = {
                lk: lr.get(sym, {"risk_score": 0, "flags": [], "detail": {}})
                for lk, lr in layer_results.items()
            }
            result = scorer.compute(sym, sector_map.get(sym, "OTHERS"), ls)

            assessments.append(_make_row(calc_date, sym, result, ls))

        # 5. Upsert into risk_assessments
        _upsert_assessments(cur, assessments)
        conn.commit()

        summary = {
            "status": "success",
            "calc_date": str(calc_date),
            "symbols": len(symbols),
            "news_articles": len(articles),
            "symbols_with_news": len(symbol_news),
            "assessments": len(assessments),
        }
        logger.info("CRS assessment done: %s", summary)
        return summary

    except Exception as e:
        logger.error("CRS assessment failed: %s", e)
        conn.rollback()
        return {"status": "failed", "error": str(e)}
    finally:
        cur.close()
        conn.close()


def _load_symbols(cur) -> tuple[list[str], dict[str, str]]:
    cur.execute("SELECT symbol, industry FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
    rows = cur.fetchall()
    symbols = [r[0] for r in rows]
    sector_map = {}
    for sym, ind in rows:
        sector = classify(ind, sym)
        if sector in ("BANKS", "FINANCIAL_SERVICES"):
            mapped = "BANKS" if sector == "BANKS" else "FINANCIAL_SERVICES"
        elif sector in ("REAL_ESTATE", "CONSTRUCTION"):
            mapped = sector
        elif sector == "CONSTRUCTION_MATERIALS":
            mapped = "CONSTRUCTION"
        elif sector == "BASIC_RESOURCES":
            mapped = "BASIC_RESOURCES"
        elif sector == "OIL_GAS":
            mapped = "OIL_GAS"
        elif sector == "FOOD_BEVERAGE":
            mapped = "FOOD_BEVERAGE"
        elif sector == "TECHNOLOGY":
            mapped = "TECHNOLOGY"
        else:
            mapped = "OTHERS"
        sector_map[sym] = mapped
    return symbols, sector_map


def _load_ohlcv(cur, symbols: list[str], calc_date: date) -> dict[str, pd.DataFrame]:
    start = calc_date - timedelta(days=120)
    cur.execute(
        """SELECT symbol, time, adj_close, volume
           FROM ohlcv
           WHERE symbol = ANY(%s) AND time >= %s
           ORDER BY symbol, time ASC""",
        (symbols, start),
    )
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "time", "adj_close", "volume"])
    result = {}
    for sym in symbols:
        sub = df[df["symbol"] == sym][["time", "adj_close", "volume"]].sort_values("time")
        if len(sub) >= 20:
            result[sym] = sub
    return result


def _load_tech_data(cur, symbols: list[str], calc_date: date) -> dict[str, dict]:
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, indicators
           FROM technical_indicators
           WHERE symbol = ANY(%s) AND calc_date <= %s
           ORDER BY symbol, calc_date DESC""",
        (symbols, calc_date),
    )
    result: dict[str, dict] = {}
    for sym, raw in cur.fetchall():
        d = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        result[sym] = d
    return result


def _load_financial_statements(cur, symbols: list[str]) -> dict[str, dict]:
    """Load latest BS + IS + CF per symbol."""
    result: dict[str, dict] = {}
    for stmt_type in ("BS", "IS", "CF"):
        cur.execute(
            """SELECT DISTINCT ON (symbol) symbol, data
               FROM financial_statements
               WHERE symbol = ANY(%s) AND statement_type = %s
               ORDER BY symbol, period_end DESC""",
            (symbols, stmt_type),
        )
        for sym, raw in cur.fetchall():
            d = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
            result.setdefault(sym, {}).update(d)
    return result


def _load_financial_ratios(cur, symbols: list[str]) -> dict[str, dict]:
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, debt_equity
           FROM financial_ratios
           WHERE symbol = ANY(%s)
           ORDER BY symbol, ratio_date DESC""",
        (symbols,),
    )
    result: dict[str, dict] = {}
    for row in cur.fetchall():
        result[row[0]] = {"debt_equity": row[1]}
    return result


def _load_macro_indicators(cur, calc_date: date) -> dict[str, float]:
    start = calc_date - timedelta(days=365)
    cur.execute(
        """SELECT indicator_name, value, indicator_date
           FROM macro_indicators
           WHERE indicator_date >= %s
           ORDER BY indicator_name, indicator_date DESC""",
        (start,),
    )
    result: dict[str, float] = {}
    seen: set = set()
    for name, val, _ in cur.fetchall():
        if val is not None and name not in seen:
            try:
                result[name] = float(val)
            except (ValueError, TypeError):
                pass
            seen.add(name)
    return result


def _load_news_events(cur, symbols: list[str], calc_date: date) -> dict[str, list[dict]]:
    start = calc_date - timedelta(days=30)
    cur.execute(
        """SELECT symbol, title, sentiment_score, published_date
           FROM news_events
           WHERE symbol = ANY(%s) AND published_date >= %s
           ORDER BY published_date DESC""",
        (symbols, start),
    )
    result: dict[str, list[dict]] = {}
    for sym, title, sentiment_score, pub_date in cur.fetchall():
        result.setdefault(sym, []).append({
            "title": title,
            "sentiment": float(sentiment_score) if sentiment_score is not None else 0.0,
            "published_date": str(pub_date) if pub_date else "",
        })
    return result


def _make_row(calc_date: date, symbol: str, crs_result: dict, layer_scores: dict[str, dict]) -> tuple:
    return (
        symbol,
        calc_date,
        crs_result.get("sector", ""),
        crs_result.get("crs_score"),
        crs_result.get("risk_level"),
        crs_result.get("hard_blocked", False),
        crs_result.get("soft_blocked", False),
        crs_result.get("recommendation"),
        layer_scores.get("layer1_quant", {}).get("risk_score"),
        layer_scores.get("layer2_fundamental", {}).get("risk_score"),
        layer_scores.get("layer3_market_vn", {}).get("risk_score"),
        layer_scores.get("layer4_macro_vn", {}).get("risk_score"),
        layer_scores.get("layer5_global", {}).get("risk_score"),
        layer_scores.get("layer6_regulatory", {}).get("risk_score"),
        layer_scores.get("layer7_behavioral", {}).get("risk_score"),
        crs_result.get("hard_flags", []),
        crs_result.get("soft_flags", []),
        crs_result.get("all_flags", []),
        json.dumps({
            "layer_details": {
                lk: ls.get("detail", {})
                for lk, ls in layer_scores.items()
            },
            "crs_result": {
                k: v for k, v in crs_result.items()
                if k not in ("hard_flags", "soft_flags", "all_flags", "layer_scores")
            },
        }),
    )


def _upsert_assessments(cur, rows: list[tuple]):
    if not rows:
        return
    execute_values(
        cur,
        """INSERT INTO risk_assessments
           (symbol, assessment_date, sector, crs_score, risk_level,
            hard_blocked, soft_blocked, recommendation,
            score_quant, score_fundamental, score_market_vn,
            score_macro_vn, score_global, score_regulatory, score_behavioral,
            hard_flags, soft_flags, all_flags, detail)
           VALUES %s
           ON CONFLICT (symbol, assessment_date)
           DO UPDATE SET
               crs_score = EXCLUDED.crs_score,
               risk_level = EXCLUDED.risk_level,
               hard_blocked = EXCLUDED.hard_blocked,
               soft_blocked = EXCLUDED.soft_blocked,
               recommendation = EXCLUDED.recommendation,
               score_quant = EXCLUDED.score_quant,
               score_fundamental = EXCLUDED.score_fundamental,
               score_market_vn = EXCLUDED.score_market_vn,
               score_macro_vn = EXCLUDED.score_macro_vn,
               score_global = EXCLUDED.score_global,
               score_regulatory = EXCLUDED.score_regulatory,
               score_behavioral = EXCLUDED.score_behavioral,
               hard_flags = EXCLUDED.hard_flags,
               soft_flags = EXCLUDED.soft_flags,
               all_flags = EXCLUDED.all_flags,
               detail = EXCLUDED.detail""",
        rows,
        page_size=500,
    )
