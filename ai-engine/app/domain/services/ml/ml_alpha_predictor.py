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


def _fetch_factor_panel(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV and compute selected alpha factors as feature panel.

    Returns:
        DataFrame with columns = alpha_id, values = factor scores.
        Index = date.
    """
    import psycopg2
    from app.infrastructure.database.pg_pool import DB_URL

    end_dt = datetime.now(TZ_VN)
    start_dt = end_dt - timedelta(days=365 + 90)

    ohlcv_rows = []
    market_cap = None
    ratios_rows = []
    flow_rows = []
    insider_rows = []
    fs_rows = []

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
            SELECT statement_type, period_end, data
            FROM financial_statements
            WHERE symbol = %s AND frequency = 'quarterly' AND statement_type IN ('BS', 'IS', 'CF')
            ORDER BY period_end
        """, (symbol,))
        fs_rows = cur.fetchall()
        
        cur.close; conn.close()
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
            features["FOREIGN_NET_5D"] = df_flow["net_value_5d"] / market_cap
        else:
            features["FOREIGN_NET_5D"] = pd.Series(0.0, index=df.index)
    else:
        features["FOREIGN_NET_5D"] = pd.Series(0.0, index=df.index)

    # 5. Process SIZE
    if market_cap and market_cap > 0:
        features["SIZE"] = pd.Series(np.log(market_cap), index=df.index)
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
            features["INSIDER_NET_30D"] = (rolling_net_qty * df["close"] * 1000) / market_cap
        else:
            features["INSIDER_NET_30D"] = pd.Series(0.0, index=df.index)
    else:
        features["INSIDER_NET_30D"] = pd.Series(0.0, index=df.index)

    # 7. Process Financial Statements (Piotroski 9-point)
    BS_KEYS = {
        "total_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN", "tài_sản", "TÀI SẢN", "a_tài_sản", "A. TÀI SẢN"),
        "total_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ", "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ", "nợ_phải_trả", "Nợ phải trả"),
        "current_assets": ("a_tài_sản_ngắn_hạn", "A. TÀI SẢN NGắn HẠN", "tài_sản_ngắn_hạn", "Tài sản ngắn hạn", "i_tài_sản_ngắn_hạn", "I. Tài sản ngắn hạn"),
        "current_liabilities": ("i_nợ_ngắn_hạn", "I. Nợ ngắn hạn", "nợ_ngắn_hạn", "Nợ ngắn hạn"),
    }
    IS_KEYS = {
        "revenue": ("3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ", "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ", "doanh_thu_thuần", "Doanh thu thuần"),
        "net_income": ("18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp", "18. Lợi nhuận sau thuế thu nhập doanh nghiệp", "lợi_nhuận_sau_thuế", "Lợi nhuận sau thuế"),
        "cost_of_goods_sold": ("giá_vốn_hàng_bán", "giá vốn hàng bán", "4_giá_vốn_hàng_bán", "4. Giá vốn hàng bán"),
    }
    CF_KEYS = {
        "cfo": ("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh", "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "lưu_chuyển_tiền_tệ_ròng_từ_các_hoạt_động_sản_xuất_kinh_doanh", "Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh", "tiền_thuần_từ_hđkd", "Tiền thuần từ HĐKD"),
    }
    
    def pick_key_val(data_dict, keys):
        if not isinstance(data_dict, dict):
            return None
        for key in keys:
            if key in data_dict:
                v = data_dict[key]
                if isinstance(v, (int, float)):
                    return float(v)
                try:
                    fv = float(str(v).replace(",", ""))
                    return fv
                except:
                    pass
        for k, v in data_dict.items():
            if isinstance(k, str) and any(kw.lower() in k.lower() for kw in keys):
                if isinstance(v, (int, float)):
                    return float(v)
                try:
                    fv = float(str(v).replace(",", ""))
                    return fv
                except:
                    pass
        return None

    periods = {}
    for stype, pe, raw in fs_rows:
        pe_date = pd.to_datetime(pe).date()
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        if pe_date not in periods:
            periods[pe_date] = {}
        parsed = {}
        km = BS_KEYS if stype == 'BS' else (IS_KEYS if stype == 'IS' else CF_KEYS)
        for out_key, candidates in km.items():
            parsed[out_key] = pick_key_val(data, candidates)
        periods[pe_date][stype] = parsed

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
        pf = 0
        has_history = rec["prev_4q_total_assets"] is not None
        
        ni = rec["net_income"]
        ta = rec["total_assets"]
        cfo = rec["cfo"]
        
        # 1. ROA > 0
        roa = 0.0
        if ni is not None and ta is not None and ta > 0:
            roa = ni / ta
            if roa > 0: pf += 1
        # 2. CFO > 0
        if cfo is not None and cfo > 0: pf += 1
        # 3. ΔROA > 0
        prev_ni = rec["prev_4q_net_income"]
        prev_ta = rec["prev_4q_total_assets"]
        if ni is not None and ta is not None and ta > 0:
            if prev_ni is not None and prev_ta is not None and prev_ta > 0:
                prev_roa = prev_ni / prev_ta
                if roa > prev_roa: pf += 1
        # 4. Accrual
        if cfo is not None and ni is not None and cfo > ni: pf += 1
        
        # 5. ΔLeverage
        tl = rec["total_liabilities"]
        cl = rec["current_liabilities"]
        if tl is not None and cl is not None and ta is not None and ta > 0:
            lt = tl - cl
            lev = lt / ta
            prev_tl = rec["prev_4q_total_liabilities"]
            prev_cl = rec["prev_4q_current_liabilities"]
            if prev_tl is not None and prev_cl is not None and prev_ta is not None and prev_ta > 0:
                prev_lt = prev_tl - prev_cl
                prev_lev = prev_lt / prev_ta
                if lev < prev_lev: pf += 1
                
        # 6. ΔLiquidity
        ca = rec["current_assets"]
        if ca is not None and cl is not None and cl > 0:
            cr = ca / cl
            prev_ca = rec["prev_4q_current_assets"]
            prev_cl = rec["prev_4q_current_liabilities"]
            if prev_ca is not None and prev_cl is not None and prev_cl > 0:
                prev_cr = prev_ca / prev_cl
                if cr > prev_cr: pf += 1
                
        # 7. No new shares
        if ta is not None and tl is not None:
            eq = ta - tl
            prev_tl = rec["prev_4q_total_liabilities"]
            if prev_ta is not None and prev_tl is not None:
                prev_eq = prev_ta - prev_tl
                if eq <= prev_eq * 1.02: pf += 1
                
        # 8. ΔMargin
        rev = rec["revenue"]
        cogs = rec["cost_of_goods_sold"]
        if rev is not None and cogs is not None and rev > 0:
            gm = (rev - cogs) / rev
            prev_rev = rec["prev_4q_revenue"]
            prev_cogs = rec["prev_4q_cost_of_goods_sold"]
            if prev_rev is not None and prev_cogs is not None and prev_rev > 0:
                prev_gm = (prev_rev - prev_cogs) / prev_rev
                if gm > prev_gm: pf += 1
                
        # 9. ΔTurnover
        if rev is not None and ta is not None and ta > 0:
            at = rev / ta
            prev_rev = rec["prev_4q_revenue"]
            if prev_rev is not None and prev_ta is not None and prev_ta > 0:
                prev_at = prev_rev / prev_ta
                if at > prev_at: pf += 1
                
        score = float(pf)
        if not has_history:
            basic_pf = 0
            roe_norm = ni / ta if ni is not None and ta is not None and ta > 0 else 0.0
            if roe_norm > 0: basic_pf += 1
            if market_cap and market_cap > 0: basic_pf += 1
            score = float(basic_pf * 4.5)
            
        release_date = pe + timedelta(days=30 if pe.month == 12 else 20)
        pf_records.append((release_date, score))

    if pf_records:
        df_pf = pd.DataFrame(pf_records, columns=["date", "PIOTROSKI_F"])
        df_pf["date"] = pd.to_datetime(df_pf["date"])
        df_pf = df_pf.set_index("date")
        features["PIOTROSKI_F"] = df_pf["PIOTROSKI_F"].reindex(df.index).ffill().fillna(4.5)
    else:
        features["PIOTROSKI_F"] = pd.Series(4.5, index=df.index)

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

    panels = []
    for sym in symbols:
        p = _fetch_factor_panel(sym)
        if p is not None and not p.empty:
            p['symbol'] = sym
            panels.append(p)

    if not panels:
        return {"error": "Insufficient data for all symbols"}

    df = pd.concat(panels)
    df = _orthogonalize_volatility_panel(df)
    df = df.dropna(subset=["target"])
    
    if df.empty or len(df) < 100:
        return {"error": f"Too few samples in panel: {len(df)}"}

    feature_cols = _get_feature_cols(df.drop(columns=["symbol"]))
    
    # Cross-sectional rank normalization
    for col in feature_cols:
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

    panels = []
    for sym in symbols:
        p = _fetch_factor_panel(sym)
        if p is not None and not p.empty:
            p['symbol'] = sym
            panels.append(p)

    if not panels:
        return {"error": "No data available for prediction"}

    df = pd.concat(panels)
    df = _orthogonalize_volatility_panel(df)
    
    feature_cols = _get_feature_cols(df.drop(columns=["symbol", "target"], errors="ignore"))
    
    # Cross-sectional rank normalization
    for col in feature_cols:
        df[col] = df.groupby(level=0)[col].rank(pct=True)
    df[feature_cols] = df[feature_cols].fillna(0.5)

    # We only care about the latest date for each symbol
    latest_date = df.index.max()
    latest_df = df.loc[[latest_date]].copy()
    
    if latest_df.empty:
        return {"error": "No valid features for latest date"}

    X = latest_df[feature_cols]
    preds = model.predict(X)
    latest_df["prediction"] = preds

    results = []
    for _, row in latest_df.iterrows():
        pred = float(row["prediction"])
        direction = "BUY" if pred > 0.005 else ("SELL" if pred < -0.005 else "HOLD")
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
