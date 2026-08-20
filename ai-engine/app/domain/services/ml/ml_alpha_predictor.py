"""ML Alpha Predictor — trains XGBoost/Random Forest models on alpha factor zoo
to predict forward returns for Vietnamese equities.

Pipeline:
  1. Fetch OHLCV for universe
  2. Compute N alpha factors (from factor zoo)
  3. Engineer features (impute, winsorize, z-score)
  4. Train model (XGBoost or Random Forest)
  5. Predict forward returns
  6. Return predictions + feature importance
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "models"
MODEL_DIR = Path(os.getenv("ML_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Feature extraction from factor zoo
# ---------------------------------------------------------------------------

_SELECTED_ALPHAS = [
    # momentum
    "alpha_001", "alpha_003", "alpha_006",
    "carhart_mom",
    # reversal
    "alpha_004", "alpha_007", "alpha_019",
    # volume
    "alpha_014", "alpha_021", "alpha_054",
    # volatility
    "alpha_026", "alpha_051",
    # quality
    "alpha_040",
    # value
    "alpha_043",
    # liquidity
    "alpha_048",
    # microstructure
    "alpha_005", "alpha_016",
    # GTJA
    "alpha_001", "alpha_004", "alpha_006", "alpha_008", "alpha_013",
    # Qlib
    "beta5", "correlation10", "std20", "roc20", "rsv_kd",
]


_HMM_POSTERIORS_CACHE = {}


def _fetch_hmm_posteriors_panel(start_date: date, end_date: date) -> pd.DataFrame:
    """Fetch VNI daily and market_regime data, compute HMM posteriors for all dates in range."""
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL
    from app.domain.rules.market.hmm_classifier import hmm_classifier, MarketRegime

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # VNI data is needed for rolling averages (ma50, vol_ma20), so fetch from start_date - 120 days
    query_start = start_date - timedelta(days=120)

    cur.execute("""
        WITH vni AS (
            SELECT date, close_adj, volume_total,
                   AVG(close_adj) OVER(ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                   AVG(volume_total) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol_ma20
            FROM market_data_daily
            WHERE ticker = 'VNINDEX' AND date >= %s AND date <= %s
        ),
        br AS (
            SELECT date, breadth_ma50 FROM market_regime WHERE date >= %s AND date <= %s
        )
        SELECT vni.date, vni.close_adj, vni.ma50, vni.volume_total, vni.vol_ma20,
               COALESCE(br.breadth_ma50, 50.0) as breadth
        FROM vni
        LEFT JOIN br ON vni.date = br.date
        ORDER BY vni.date
    """, (query_start, end_date, query_start, end_date))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    records = []
    for dt, close_adj, ma50, vol, vol_ma20, breadth in rows:
        if dt < start_date:
            continue
        vni_vs_ma50 = (close_adj / ma50 - 1) if ma50 else 0.0
        vol_trend = (vol / vol_ma20 - 1) if vol_ma20 else 0.0

        # Calculate posterior probabilities
        posterior = hmm_classifier.calculate_posterior(vni_vs_ma50, breadth, vol_trend)

        records.append({
            "date": pd.to_datetime(dt),
            "hmm_prob_bull_trending": posterior.get(MarketRegime.BULL_TRENDING, 0.25),
            "hmm_prob_bull_choppy": posterior.get(MarketRegime.BULL_CHOPPY, 0.25),
            "hmm_prob_bear_trending": posterior.get(MarketRegime.BEAR_TRENDING, 0.25),
            "hmm_prob_bear_bounce": posterior.get(MarketRegime.BEAR_BOUNCE, 0.25),
        })

    if not records:
        return pd.DataFrame(columns=[
            "hmm_prob_bull_trending", "hmm_prob_bull_choppy",
            "hmm_prob_bear_trending", "hmm_prob_bear_bounce"
        ])

    df_hmm = pd.DataFrame(records).set_index("date")
    return df_hmm


def _get_cached_hmm_posteriors(start_date: date, end_date: date) -> pd.DataFrame:
    cache_key = (start_date, end_date)
    if cache_key not in _HMM_POSTERIORS_CACHE:
        _HMM_POSTERIORS_CACHE[cache_key] = _fetch_hmm_posteriors_panel(start_date, end_date)
    return _HMM_POSTERIORS_CACHE[cache_key]


def _bulk_prefetch_data(symbols: List[str], start_dt: datetime, end_dt: datetime) -> Dict[str, Dict[str, Any]]:
    """Prefetch all required data for multiple symbols in bulk to avoid N+1 queries."""
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL
    
    symbols_tuple = tuple(symbols)
    if len(symbols) == 1:
        symbols_tuple = (symbols[0],)
        
    prefetch = {sym: {
        "market_cap": None,
        "ohlcv": [],
        "ratios": [],
        "flow": [],
        "insider": [],
        "statements": []
    } for sym in symbols}
    
    if not symbols:
        return prefetch
        
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # 1. Fetch static market_cap
        cur.execute("SELECT symbol, market_cap FROM stocks WHERE symbol IN %s", (symbols_tuple,))
        for sym, mc in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["market_cap"] = float(mc) if mc is not None else None
                
        # 2. Fetch daily OHLCV
        cur.execute("""
            SELECT symbol, time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol IN %s AND time >= %s::timestamptz AND time <= %s::timestamptz
            ORDER BY time
        """, (symbols_tuple, start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")))
        for sym, t, o, h, l, c, v in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["ohlcv"].append((t, o, h, l, c, v))
                
        # 3. Fetch financial ratios
        cur.execute("""
            SELECT symbol, ratio_date, pb, ev_ebitda
            FROM financial_ratios
            WHERE symbol IN %s AND ratio_date <= %s
            ORDER BY ratio_date
        """, (symbols_tuple, end_dt.date()))
        for sym, rd, pb, ev in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["ratios"].append((rd, pb, ev))
                
        # 4. Fetch foreign flow
        cur.execute("""
            SELECT symbol, trade_date, net_value
            FROM foreign_flow
            WHERE symbol IN %s AND trade_date >= %s AND trade_date <= %s
            ORDER BY trade_date
        """, (symbols_tuple, start_dt.date(), end_dt.date()))
        for sym, td, nv in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["flow"].append((td, nv))
                
        # 5. Fetch insider trades
        cur.execute("""
            SELECT symbol, trade_date,
                   SUM(CASE WHEN trade_type IN ('Mua','Đăng ký mua','đăng ký mua') THEN quantity ELSE 0 END) -
                   SUM(CASE WHEN trade_type IN ('Bán','Đăng ký bán','đăng ký bán') THEN quantity ELSE 0 END) as net_qty
            FROM insider_trades
            WHERE symbol IN %s AND trade_date >= %s AND trade_date <= %s
            GROUP BY symbol, trade_date
            ORDER BY trade_date
        """, (symbols_tuple, (start_dt - timedelta(days=30)).date(), end_dt.date()))
        for sym, td, nq in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["insider"].append((td, nq))
                
        # 6. Fetch financial statements
        cur.execute("""
            SELECT symbol, statement_type, period_end, data, published_date
            FROM financial_statements
            WHERE symbol IN %s AND frequency = 'quarterly' AND statement_type IN ('BS', 'IS', 'CF')
            ORDER BY period_end
        """, (symbols_tuple,))
        for sym, stype, pe, raw, pub in cur.fetchall():
            if sym in prefetch:
                prefetch[sym]["statements"].append((stype, pe, raw, pub))
                
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Bulk prefetch failed: {e}")
        
    return prefetch


def _fetch_factor_panel(
    symbol: str,
    prefetch_data: Optional[Dict[str, Any]] = None,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV and compute selected alpha factors as feature panel.

    Returns:
        DataFrame with columns = alpha_id, values = factor scores.
        Index = date.
    """
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL

    if end_dt is None:
        end_dt = datetime.now(TZ_VN)
    if start_dt is None:
        start_dt = end_dt - timedelta(days=365 + 90)

    ohlcv_rows = []
    market_cap = None
    ratios_rows = []
    flow_rows = []
    insider_rows = []
    fs_rows = []

    if prefetch_data is not None:
        market_cap = prefetch_data.get("market_cap")
        ohlcv_rows = prefetch_data.get("ohlcv", [])
        ratios_rows = prefetch_data.get("ratios", [])
        flow_rows = prefetch_data.get("flow", [])
        insider_rows = prefetch_data.get("insider", [])
        fs_rows = prefetch_data.get("statements", [])
    else:
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            
            # 1. Fetch static market_cap
            cur.execute("SELECT market_cap FROM stocks WHERE symbol = %s", (symbol,))
            row = cur.fetchone()
            market_cap = float(row[0]) if row and row[0] is not None else None
            
            # 2. Fetch daily OHLCV
            cur.execute("""
                SELECT time, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND time >= %s::timestamptz AND time <= %s::timestamptz
                ORDER BY time
            """, (symbol, start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")))
            ohlcv_rows = cur.fetchall()
            
            # 3. Fetch financial ratios
            cur.execute("""
                SELECT ratio_date, pb, ev_ebitda
                FROM financial_ratios
                WHERE symbol = %s AND ratio_date <= %s
                ORDER BY ratio_date
            """, (symbol, end_dt.date()))
            ratios_rows = cur.fetchall()
            
            # 4. Fetch foreign flow
            cur.execute("""
                SELECT trade_date, net_value
                FROM foreign_flow
                WHERE symbol = %s AND trade_date >= %s AND trade_date <= %s
                ORDER BY trade_date
            """, (symbol, start_dt.date(), end_dt.date()))
            flow_rows = cur.fetchall()
            
            # 5. Fetch insider trades
            cur.execute("""
                SELECT trade_date,
                       SUM(CASE WHEN trade_type IN ('Mua','Đăng ký mua','đăng ký mua') THEN quantity ELSE 0 END) -
                       SUM(CASE WHEN trade_type IN ('Bán','Đăng ký bán','đăng ký bán') THEN quantity ELSE 0 END) as net_qty
                FROM insider_trades
                WHERE symbol = %s AND trade_date >= %s AND trade_date <= %s
                GROUP BY trade_date
                ORDER BY trade_date
            """, (symbol, (start_dt - timedelta(days=30)).date(), end_dt.date()))
            insider_rows = cur.fetchall()

            # 6. Fetch financial statements
            cur.execute("""
                SELECT statement_type, period_end, data, published_date
                FROM financial_statements
                WHERE symbol = %s AND frequency = 'quarterly' AND statement_type IN ('BS', 'IS', 'CF')
                ORDER BY period_end
            """, (symbol,))
            fs_rows = cur.fetchall()
            
            cur.close(); conn.close()
        except Exception as e:
            logger.warning(f"Failed to fetch data from DB for {symbol}: {e}")

    # Fallback to market_data_svc if DB is empty
    if not ohlcv_rows:
        from app.infrastructure.external_api.market_data_service import market_data_svc
        import asyncio
        try:
            ohlcv = asyncio.run(
                market_data_svc.get_ohlcv(
                    symbol, interval="1D",
                    start=start_dt.strftime("%Y-%m-%d"),
                    end=end_dt.strftime("%Y-%m-%d"),
                )
            )
            bars = ohlcv.get("data", [])
            ohlcv_rows = [
                (b["time"], b["open"], b["high"], b["low"], b["close"], b["volume"])
                for b in bars
            ]
        except Exception as ex:
            logger.error(f"Fallback fetch failed for {symbol}: {ex}")

    if len(ohlcv_rows) < 30:
        return None

    df = pd.DataFrame(ohlcv_rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"])
    if df["time"].dt.tz is not None:
        df["time"] = df["time"].dt.tz_localize(None)
    df = df.set_index("time")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_index()

    # Estimate daily historical market cap to avoid Look-Ahead Bias
    shares_outstanding = 1.0
    if market_cap and market_cap > 0 and not df.empty:
        latest_close = df["close"].iloc[-1]
        if latest_close > 0:
            shares_outstanding = market_cap / latest_close
    market_cap_historical = df["close"] * shares_outstanding

    features: Dict[str, pd.Series] = {}

    # Compute momentum features
    close = df["close"]
    volume = df["volume"]

    # Simple alphas (from factor zoo formulas)
    for period in [5, 10, 20, 60]:
        features[f"ret_{period}d"] = close.pct_change(period)
        features[f"vol_{period}d"] = close.pct_change().rolling(period).std()
        features[f"volume_ma_{period}d"] = volume.rolling(period).mean()
        features[f"volume_ratio_{period}d"] = volume / volume.rolling(period).mean()

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    features["rsi_14"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    features["macd"] = ema12 - ema26
    features["macd_signal"] = features["macd"].ewm(span=9).mean()

    # Bollinger Bands
    bb_sma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    features["bb_position"] = (close - bb_sma) / (bb_std * 2 + 1e-10)
    features["bb_width"] = bb_std / bb_sma

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features["atr_14"] = tr.rolling(14).mean()

    # Price / SMA ratio
    for period in [10, 20, 50]:
        features[f"price_sma_{period}"] = close / close.rolling(period).mean()

    # Volume price trend
    features["vpt"] = (volume * close.pct_change()).cumsum()

    # 3. Process financial ratios
    if ratios_rows:
        df_ratios = pd.DataFrame(ratios_rows, columns=["date", "pb", "ev_ebitda"])
        df_ratios["date"] = pd.to_datetime(df_ratios["date"])
        if df_ratios["date"].dt.tz is not None:
            df_ratios["date"] = df_ratios["date"].dt.tz_localize(None)
        df_ratios = df_ratios.set_index("date")
        df_ratios["EVEBITDA_INV"] = df_ratios["ev_ebitda"].apply(lambda x: 1.0 / x if x and x > 0 else np.nan)
        df_ratios["HML_REAL"] = df_ratios["pb"].apply(lambda x: 1.0 / x if x and x > 0 else np.nan)
        df_ratios = df_ratios.reindex(df.index).ffill()
        features["EVEBITDA_INV"] = df_ratios["EVEBITDA_INV"]
        features["HML_REAL"] = df_ratios["HML_REAL"]
    else:
        features["EVEBITDA_INV"] = pd.Series(np.nan, index=df.index)
        features["HML_REAL"] = pd.Series(np.nan, index=df.index)

    # 4. Process foreign flow
    if flow_rows:
        df_flow = pd.DataFrame(flow_rows, columns=["date", "net_value"])
        df_flow["date"] = pd.to_datetime(df_flow["date"])
        if df_flow["date"].dt.tz is not None:
            df_flow["date"] = df_flow["date"].dt.tz_localize(None)
        df_flow = df_flow.set_index("date")
        df_flow["net_value_5d"] = df_flow["net_value"].rolling(5).sum()
        df_flow = df_flow.reindex(df.index)
        df_flow["net_value_5d"] = df_flow["net_value_5d"].fillna(0.0)
        if market_cap and market_cap > 0:
            features["FOREIGN_NET_5D"] = df_flow["net_value_5d"] / market_cap_historical
        else:
            features["FOREIGN_NET_5D"] = pd.Series(0.0, index=df.index)
    else:
        features["FOREIGN_NET_5D"] = pd.Series(0.0, index=df.index)

    # 5. Process SIZE
    if market_cap and market_cap > 0:
        features["SIZE"] = pd.Series(np.log(market_cap_historical), index=df.index)
    else:
        features["SIZE"] = pd.Series(np.nan, index=df.index)

    # 6. Process Insider Trades
    if insider_rows:
        df_insider = pd.DataFrame(insider_rows, columns=["date", "net_qty"])
        df_insider["date"] = pd.to_datetime(df_insider["date"])
        df_insider = df_insider.set_index("date")
        insider_series = df_insider["net_qty"].reindex(df.index, fill_value=0.0)
        rolling_net_qty = insider_series.rolling(window=30, min_periods=1).sum()
        if market_cap and market_cap > 0:
            features["INSIDER_NET_30D"] = (rolling_net_qty * df["close"] * 1000) / market_cap_historical
        else:
            features["INSIDER_NET_30D"] = pd.Series(0.0, index=df.index)
    else:
        features["INSIDER_NET_30D"] = pd.Series(0.0, index=df.index)

    # 7. Process Financial Statements (Piotroski 9-point)
    import re

    def clean_key(k: str) -> str:
        if not isinstance(k, str):
            return ""
        k = k.lower().strip()
        # Safe suffix regex: matches e.g. _270_100_200, _xi_xii, (xi-xii), _xiii_xiv at the end of key
        k = re.sub(r'[\s_]*[\(\[\{]?(?:\d+|[ivxldcm]+)(?:[\s_\-\+=\*/]+(?:\d+|[ivxldcm]+))*[\)\]\}]?$', '', k)
        # Safe prefix regex: matches e.g. 1., 1_1_, I., a., a_ without stripping word beginnings like 'thu' or 'doanh'
        k = re.sub(r'^(?:(?:\d+(?:\.\d+)*|[ivxldcm]+|[a-z])[\.\-_\s]+)+', '', k)
        k = k.replace("_", " ").strip()
        k = re.sub(r'\s+', ' ', k)
        return k

    METRICS_MAPPING = {
        "total_assets": ["tổng cộng tài sản", "tổng tài sản", "tổng cộng tài sản có"],
        "total_liabilities": ["tổng nợ phải trả", "nợ phải trả", "tổng cộng nợ phải trả", "cộng nợ phải trả"],
        "current_assets": ["tài sản ngắn hạn"],
        "current_liabilities": ["nợ ngắn hạn"],
        "revenue": [
            "doanh thu thuần về bán hàng và cung cấp dịch vụ",
            "doanh thu thuần",
            "doanh thu bán hàng",
            "thu nhập lãi thuần",
            "doanh thu phí bảo hiểm thuần",
            "cộng doanh thu hoạt động",
            "doanh thu hoạt động",
            "doanh thu nghiệp vụ"
        ],
        "net_income": [
            "lợi nhuận sau thuế thu nhập doanh nghiệp",
            "lợi nhuận kế toán sau thuế tndn",
            "lợi nhuận sau thuế",
            "lợi nhuận kế toán sau thuế",
            "lợi nhuận sau thuế của cổ đông của công ty mẹ",
            "lợi nhuận sau thuế của cổ đông của ngân hàng mẹ"
        ],
        "cost_of_goods_sold": ["giá vốn hàng bán", "giá vốn", "chi phí hoạt động tự doanh", "chi phí hoạt động"],
        "cfo": [
            "lưu chuyển tiền thuần từ hoạt động kinh doanh",
            "lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh",
            "tiền thuần từ hoạt động kinh doanh",
            "lưu chuyển tiền thuần từ hoạt động kinh doanh chứng khoán"
        ]
    }

    STANDARD_CODE_MAP = {
        "total_assets": "270",
        "total_liabilities": "300",
        "current_assets": "100",
        "current_liabilities": "310",
        "revenue": "10",
        "cost_of_goods_sold": "11",
        "net_income": "60",
        "cfo": "20"
    }

    def pick_key_val(data_dict, targets, metric_name=None):
        if not isinstance(data_dict, dict):
            return None
        # 1. Try standard accounting code matching first if applicable
        if metric_name and metric_name in STANDARD_CODE_MAP:
            code = STANDARD_CODE_MAP[metric_name]
            for k, v in data_dict.items():
                if v is not None:
                    k_lower = k.lower()
                    if f"({code}" in k_lower or f"[{code}" in k_lower or f"{{{code}" in k_lower or k_lower.endswith(f"_{code}"):
                        if isinstance(v, (int, float)):
                            return float(v)
                        try:
                            return float(str(v).replace(",", ""))
                        except:
                            pass
        # 2. Fall back to exact clean key match
        cleaned_map = {}
        for k, v in data_dict.items():
            if v is not None:
                cleaned_map[clean_key(k)] = v
        for t in targets:
            if t in cleaned_map:
                val = cleaned_map[t]
                if isinstance(val, (int, float)):
                    return float(val)
                try:
                    return float(str(val).replace(",", ""))
                except:
                    pass
        return None

    periods = {}
    for row in fs_rows:
        if len(row) == 5:
            stype, pe, raw, pub = row[1], row[2], row[3], row[4]
        elif len(row) == 4:
            stype, pe, raw, pub = row[0], row[1], row[2], row[3]
        else:
            stype, pe, raw = row[0], row[1], row[2]
            pub = None

        pe_date = pd.to_datetime(pe).date()
        pub_date = pd.to_datetime(pub).date() if pub is not None else None
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        
        if pe_date not in periods:
            periods[pe_date] = {"published_dates": []}
            
        parsed = periods[pe_date].get(stype, {})
        if stype == 'BS':
            parsed['total_assets'] = pick_key_val(data, METRICS_MAPPING['total_assets'], 'total_assets')
            parsed['total_liabilities'] = pick_key_val(data, METRICS_MAPPING['total_liabilities'], 'total_liabilities')
            parsed['current_assets'] = pick_key_val(data, METRICS_MAPPING['current_assets'], 'current_assets')
            parsed['current_liabilities'] = pick_key_val(data, METRICS_MAPPING['current_liabilities'], 'current_liabilities')
        elif stype == 'IS':
            parsed['revenue'] = pick_key_val(data, METRICS_MAPPING['revenue'], 'revenue')
            parsed['net_income'] = pick_key_val(data, METRICS_MAPPING['net_income'], 'net_income')
            parsed['cost_of_goods_sold'] = pick_key_val(data, METRICS_MAPPING['cost_of_goods_sold'], 'cost_of_goods_sold')
        elif stype == 'CF':
            parsed['cfo'] = pick_key_val(data, METRICS_MAPPING['cfo'], 'cfo')
            
        periods[pe_date][stype] = parsed
        if pub_date:
            periods[pe_date]["published_dates"].append(pub_date)

    sorted_periods = sorted(periods.keys())
    quarterly_records = []
    for idx_p, pe in enumerate(sorted_periods):
        bs = periods[pe].get("BS", {})
        inc = periods[pe].get("IS", {})
        cf = periods[pe].get("CF", {})
        
        rec = {
            "period_end": pe,
            "net_income": inc.get("net_income"),
            "revenue": inc.get("revenue"),
            "cost_of_goods_sold": inc.get("cost_of_goods_sold"),
            "total_assets": bs.get("total_assets"),
            "total_liabilities": bs.get("total_liabilities"),
            "current_assets": bs.get("current_assets"),
            "current_liabilities": bs.get("current_liabilities"),
            "cfo": cf.get("cfo"),
        }
        
        if idx_p >= 4:
            prev_pe = sorted_periods[idx_p - 4]
            prev_bs = periods[prev_pe].get("BS", {})
            prev_inc = periods[prev_pe].get("IS", {})
            rec.update({
                "prev_4q_net_income": prev_inc.get("net_income"),
                "prev_4q_revenue": prev_inc.get("revenue"),
                "prev_4q_cost_of_goods_sold": prev_inc.get("cost_of_goods_sold"),
                "prev_4q_total_assets": prev_bs.get("total_assets"),
                "prev_4q_total_liabilities": prev_bs.get("total_liabilities"),
                "prev_4q_current_assets": prev_bs.get("current_assets"),
                "prev_4q_current_liabilities": prev_bs.get("current_liabilities"),
            })
        else:
            rec.update({
                "prev_4q_net_income": None, "prev_4q_revenue": None, "prev_4q_cost_of_goods_sold": None,
                "prev_4q_total_assets": None, "prev_4q_total_liabilities": None, "prev_4q_current_assets": None,
                "prev_4q_current_liabilities": None,
            })
        quarterly_records.append(rec)

    pf_records = []
    for rec in quarterly_records:
        pe = rec["period_end"]
        has_history = rec["prev_4q_total_assets"] is not None
        
        ni = rec["net_income"]
        ta = rec["total_assets"]
        cfo = rec["cfo"]
        
        pf = 0
        applicable_points = 0
        
        # 1. ROA > 0
        roa = None
        if ni is not None and ta is not None and ta > 0:
            roa = ni / ta
            applicable_points += 1
            if roa > 0: pf += 1
        
        # 2. CFO > 0
        if cfo is not None:
            applicable_points += 1
            if cfo > 0: pf += 1
            
        # 3. ΔROA > 0
        prev_ni = rec["prev_4q_net_income"]
        prev_ta = rec["prev_4q_total_assets"]
        if ni is not None and ta is not None and ta > 0:
            if prev_ni is not None and prev_ta is not None and prev_ta > 0:
                prev_roa = prev_ni / prev_ta
                applicable_points += 1
                if roa > prev_roa: pf += 1
                
        # 4. Accrual
        if cfo is not None and ni is not None:
            applicable_points += 1
            if cfo > ni: pf += 1
            
        # 5. ΔLeverage
        tl = rec["total_liabilities"]
        cl = rec["current_liabilities"]
        prev_tl = rec["prev_4q_total_liabilities"]
        prev_cl = rec["prev_4q_current_liabilities"]
        
        # For leverage, long term debt = total_liabilities - current_liabilities
        # If current_liabilities is None (like in banks), treat it as 0
        if tl is not None and ta is not None and ta > 0:
            cl_val = cl if cl is not None else 0.0
            lt = tl - cl_val
            lev = lt / ta
            if prev_tl is not None and prev_ta is not None and prev_ta > 0:
                prev_cl_val = prev_cl if prev_cl is not None else 0.0
                prev_lt = prev_tl - prev_cl_val
                prev_lev = prev_lt / prev_ta
                applicable_points += 1
                if lev < prev_lev: pf += 1
                
        # 6. ΔLiquidity
        ca = rec["current_assets"]
        prev_ca = rec["prev_4q_current_assets"]
        if ca is not None and cl is not None and cl > 0:
            cr = ca / cl
            if prev_ca is not None and prev_cl is not None and prev_cl > 0:
                prev_cr = prev_ca / prev_cl
                applicable_points += 1
                if cr > prev_cr: pf += 1
                
        # 7. No new shares
        if ta is not None and tl is not None:
            eq = ta - tl
            if prev_ta is not None and prev_tl is not None:
                prev_eq = prev_ta - prev_tl
                applicable_points += 1
                if eq <= prev_eq * 1.02: pf += 1
                
        # 8. ΔMargin
        rev = rec["revenue"]
        cogs = rec["cost_of_goods_sold"]
        prev_rev = rec["prev_4q_revenue"]
        prev_cogs = rec["prev_4q_cost_of_goods_sold"]
        if rev is not None and cogs is not None and rev > 0:
            gm = (rev - cogs) / rev
            if prev_rev is not None and prev_cogs is not None and prev_rev > 0:
                prev_gm = (prev_rev - prev_cogs) / prev_rev
                applicable_points += 1
                if gm > prev_gm: pf += 1
                
        # 9. ΔTurnover
        if rev is not None and ta is not None and ta > 0:
            at = rev / ta
            if prev_rev is not None and prev_ta is not None and prev_ta > 0:
                prev_at = prev_rev / prev_ta
                applicable_points += 1
                if at > prev_at: pf += 1
                
        if applicable_points > 0:
            score = float(pf) * (9.0 / applicable_points)
        else:
            score = 4.5
            
        if not has_history:
            basic_pf = 0
            roe_norm = ni / ta if ni is not None and ta is not None and ta > 0 else 0.0
            if roe_norm > 0: basic_pf += 1
            if market_cap and market_cap > 0: basic_pf += 1
            score = float(basic_pf * 4.5)
            
        pub_dates = periods[pe].get("published_dates", [])
        if pub_dates:
            release_date = max(pub_dates)
        else:
            freq = "yearly" if pe.month == 12 and pe.day == 31 else "quarterly"
            release_date = pe + timedelta(days=90 if freq == "yearly" else 45)
        pf_records.append((release_date, score))

    if pf_records:
        df_pf = pd.DataFrame(pf_records, columns=["date", "PIOTROSKI_F"])
        df_pf["date"] = pd.to_datetime(df_pf["date"])
        df_pf = df_pf.set_index("date")
        features["PIOTROSKI_F"] = df_pf["PIOTROSKI_F"].reindex(df.index).ffill().fillna(4.5)
    else:
        features["PIOTROSKI_F"] = pd.Series(4.5, index=df.index)

    # Fetch and merge HMM posteriors
    df_hmm = _get_cached_hmm_posteriors(start_dt.date(), end_dt.date())
    df_hmm_reindexed = df_hmm.reindex(df.index).ffill().fillna(0.25)

    features["hmm_prob_bull_trending"] = df_hmm_reindexed["hmm_prob_bull_trending"]
    features["hmm_prob_bull_choppy"] = df_hmm_reindexed["hmm_prob_bull_choppy"]
    features["hmm_prob_bear_trending"] = df_hmm_reindexed["hmm_prob_bear_trending"]
    features["hmm_prob_bear_bounce"] = df_hmm_reindexed["hmm_prob_bear_bounce"]

    # Target: forward 5-day return
    features["target"] = close.pct_change(5).shift(-5)

    result = pd.DataFrame(features)
    result = result.replace([np.inf, -np.inf], np.nan)
    # Keep only the last 365 days using timezone-naive cutoff
    cutoff_dt = pd.Timestamp((end_dt - timedelta(days=365)).date())
    result = result.loc[result.index >= cutoff_dt]
    return result


def _orthogonalize_volatility_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Compute VOL_20D_ORTHO cross-sectionally for each date in the panel DataFrame."""
    if df.empty:
        return df

    if "vol_20d" not in df.columns or "vol_60d" not in df.columns:
        df["VOL_20D_ORTHO"] = np.nan
        return df

    from scipy import stats as scipy_stats

    def _orth_group(group):
        v20 = group["vol_20d"]
        v60 = group["vol_60d"]
        mask = v20.notna() & v60.notna()
        if mask.sum() >= 10:
            try:
                slope, intercept, _, _, _ = scipy_stats.linregress(v60[mask], v20[mask])
                group["VOL_20D_ORTHO"] = v20 - (intercept + slope * v60)
            except Exception:
                group["VOL_20D_ORTHO"] = np.nan
        else:
            group["VOL_20D_ORTHO"] = np.nan
        return group

    # Apply grouping and regression on dates
    df = df.groupby(level=0, group_keys=False).apply(_orth_group)
    return df


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def _prepare_data(panel: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Prepare feature matrix X and target vector y.

    - Drops rows with NaN target
    - Imputes feature NaN with median
    - Returns (X, y)
    """
    df = panel.dropna(subset=["target"])
    if df.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    y = df.pop("target")
    X = df.copy()

    # Impute remaining NaN
    for col in X.columns:
        if X[col].isna().any():
            med = X[col].median()
            if pd.isna(med):
                med = 0.0
            X[col] = X[col].fillna(med)

    return X, y


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def _get_feature_cols(X: pd.DataFrame) -> List[str]:
    """Get sorted feature column names (excludes target-like cols)."""
    return sorted([c for c in X.columns if c != "target"])


def train_panel_model(
    symbols: List[str],
    model_type: str = "xgboost",
    force_retrain: bool = False,
) -> Dict[str, Any]:
    """Fetch data, engineer cross-sectional features, train panel model, save to disk.

    Args:
        symbols: List of ticker symbols to form the panel.
        model_type: 'xgboost' or 'random_forest'.
        force_retrain: If True, retrain even if cached model exists.

    Returns:
        Dict with model info + training metrics.
    """
    model_path = MODEL_DIR / f"panel_{model_type}.pkl"
    feature_path = MODEL_DIR / f"panel_{model_type}_features.json"

    if model_path.exists() and not force_retrain:
        try:
            with open(feature_path) as f:
                feature_cols = json.load(f)
            return {
                "model_type": model_type,
                "status": "cached",
                "model_path": str(model_path),
                "feature_count": len(feature_cols),
            }
        except Exception:
            pass

    # Bulk prefetch data for all symbols to avoid N+1 query overhead (End-to-End optimization)
    end_dt = datetime.now(TZ_VN)
    start_dt = end_dt - timedelta(days=455)
    prefetch = _bulk_prefetch_data(symbols, start_dt, end_dt)

    panels = []
    for sym in symbols:
        p = _fetch_factor_panel(sym, prefetch_data=prefetch.get(sym), start_dt=start_dt, end_dt=end_dt)
        if p is not None and not p.empty:
            p['symbol'] = sym
            panels.append(p)

    if not panels:
        return {"error": "Insufficient data for all symbols"}

    df = pd.concat(panels)
    df = _orthogonalize_volatility_panel(df)
    df = df.dropna(subset=["target"])
    
    # De-mean target return cross-sectionally to produce Cross-sectional Excess Return
    df["target"] = df["target"] - df.groupby(level=0)["target"].transform("mean")
    
    if df.empty or len(df) < 100:
        return {"error": f"Too few samples in panel: {len(df)}"}

    feature_cols = _get_feature_cols(df.drop(columns=["symbol"]))
    
    # Cross-sectional rank normalization (excluding HMM columns)
    hmm_cols = {"hmm_prob_bull_trending", "hmm_prob_bull_choppy", "hmm_prob_bear_trending", "hmm_prob_bear_bounce"}
    for col in feature_cols:
        if col not in hmm_cols:
            df[col] = df.groupby(level=0)[col].rank(pct=True)
    df[feature_cols] = df[feature_cols].fillna(0.5)

    X = df[feature_cols]
    y = df["target"]

    # Time-based train/test split with Embargo
    X = X.sort_index()
    y = y.sort_index()
    unique_dates = X.index.unique().sort_values()
    train_dates_count = max(int(len(unique_dates) * 0.8), 30)
    embargo_size = 5
    
    if train_dates_count + embargo_size >= len(unique_dates):
        return {"error": f"Too few dates for embargo split: {len(unique_dates)}"}
        
    train_end_date = unique_dates[train_dates_count - 1]
    test_start_date = unique_dates[train_dates_count + embargo_size]
    
    X_train = X.loc[:train_end_date]
    y_train = y.loc[:train_end_date]
    X_test = X.loc[test_start_date:]
    y_test = y.loc[test_start_date:]

    if model_type == "random_forest":
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(
            n_estimators=200, max_depth=8, random_state=42, n_jobs=-1
        )
    else:
        from xgboost import XGBRegressor
        model = XGBRegressor(
            n_estimators=200, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, n_jobs=-1,
        )

    model.fit(X_train, y_train)

    # Evaluate
    train_pred = model.predict(X_train)
    test_pred = model.predict(X_test)

    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    train_mae = float(mean_absolute_error(y_train, train_pred))
    test_mae = float(mean_absolute_error(y_test, test_pred))
    train_rmse = float(np.sqrt(mean_squared_error(y_train, train_pred)))
    test_rmse = float(np.sqrt(mean_squared_error(y_test, test_pred)))
    train_r2 = float(r2_score(y_train, train_pred))
    test_r2 = float(r2_score(y_test, test_pred))

    if hasattr(model, "feature_importances_"):
        importance = list(zip(feature_cols, model.feature_importances_))
        importance.sort(key=lambda x: x[1], reverse=True)
        top_features = [{"name": name, "importance": round(imp, 4)} for name, imp in importance[:20]]
    else:
        top_features = []

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(feature_path, "w") as f:
        json.dump(feature_cols, f)

    return {
        "model_type": model_type,
        "status": "trained",
        "model_path": str(model_path),
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "feature_count": len(feature_cols),
        "metrics": {
            "train_mae": round(train_mae, 6),
            "test_mae": round(test_mae, 6),
            "train_rmse": round(train_rmse, 6),
            "test_rmse": round(test_rmse, 6),
            "train_r2": round(train_r2, 4),
            "test_r2": round(test_r2, 4),
        },
        "top_features": top_features,
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_panel(
    symbols: List[str],
    model_type: str = "xgboost",
) -> Dict[str, Any]:
    """Generate cross-sectional predictions for a list of symbols."""
    model_path = MODEL_DIR / f"panel_{model_type}.pkl"
    
    if not model_path.exists():
        return {"error": "Panel model not trained yet."}

    try:
        with open(model_path, "rb") as f:
            model = pickle.load(f)
    except Exception as e:
        return {"error": f"Failed to load model: {e}"}

    # Union symbols with Active Universe (Group A + B + Sandbox) from DB to prevent Dataset Shift (Bluechip Bias)
    # Fail loudly if DB query fails or returns too few symbols to maintain visibility on errors
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT symbol FROM stocks WHERE universe_group IN ('A', 'B', 'SANDBOX')")
    ref_symbols = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
        
    if len(ref_symbols) < 10:
        raise ValueError(f"Active universe size is too small ({len(ref_symbols)} symbols). Minimum 10 symbols required.")
    
    all_symbols = list(set(symbols) | set(ref_symbols))

    # Optimize inference lookback to 120 days and bulk prefetch data to eliminate N+1 queries
    end_dt = datetime.now(TZ_VN)
    start_dt = end_dt - timedelta(days=120)
    prefetch = _bulk_prefetch_data(all_symbols, start_dt, end_dt)

    panels = []
    for sym in all_symbols:
        p = _fetch_factor_panel(sym, prefetch_data=prefetch.get(sym), start_dt=start_dt, end_dt=end_dt)
        if p is not None and not p.empty:
            p['symbol'] = sym
            panels.append(p)

    if not panels:
        return {"error": "No data available for prediction"}

    df = pd.concat(panels)
    df = _orthogonalize_volatility_panel(df)
    
    feature_cols = _get_feature_cols(df.drop(columns=["symbol", "target"], errors="ignore"))
    
    # Cross-sectional rank normalization (excluding HMM columns)
    hmm_cols = {"hmm_prob_bull_trending", "hmm_prob_bull_choppy", "hmm_prob_bear_trending", "hmm_prob_bear_bounce"}
    for col in feature_cols:
        if col not in hmm_cols:
            df[col] = df.groupby(level=0)[col].rank(pct=True)
    df[feature_cols] = df[feature_cols].fillna(0.5)

    # We only care about the latest date for each symbol
    latest_date = df.index.max()
    latest_df = df.loc[[latest_date]].copy()
    
    # Filter latest_df to contain ONLY the requested input symbols
    latest_df = latest_df[latest_df["symbol"].isin(symbols)]
    
    if latest_df.empty:
        return {"error": "No valid features for latest date"}

    X = latest_df[feature_cols]
    preds = model.predict(X)
    latest_df["prediction"] = preds

    results = []
    for _, row in latest_df.iterrows():
        pred = float(row["prediction"])
        direction = "BUY" if pred > 0.015 else ("SELL" if pred < -0.015 else "HOLD")
        confidence = min(abs(pred) * 10, 1.0)
        
        results.append({
            "symbol": row["symbol"],
            "predictionDate": str(latest_date.date()),
            "predicted5dReturn": round(pred * 100, 2),
            "direction": direction,
            "confidence": round(confidence, 2),
        })

    return {
        "model": model_type,
        "predictionDate": str(latest_date.date()),
        "predictions": results
    }
