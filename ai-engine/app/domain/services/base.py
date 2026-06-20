"""Factor Engine Base — Core Logic for F1/F2/F3 Groups.

Provides standard normalization (Percentile Rank) and Universe filtering.
"""

import logging
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class FactorEngineBase:
    def __init__(self):
        pass

    def normalize_percentile(self, series: pd.Series, invert: bool = False) -> pd.Series:
        """Chuẩn hóa giá trị thành percentile rank 0-100.
        
        Args:
            series: Dữ liệu thô.
            invert: Nếu True, giá trị nhỏ hơn sẽ có rank cao hơn (VD: P/E thấp -> score cao).
        """
        if series.empty:
            return series
            
        ranks = series.rank(pct=True) * 100
        if invert:
            ranks = 100 - ranks
            
        return ranks

    def filter_universe(self, df: pd.DataFrame, universe_groups: List[str]) -> pd.DataFrame:
        """Lọc dữ liệu theo Universe Groups được phép (thường là A, B, SANDBOX)."""
        if "universe_group" not in df.columns:
            return df
            
        return df[df["universe_group"].isin(universe_groups)]

    def handle_nulls(self, df: pd.DataFrame, factor_cols: List[str]) -> pd.DataFrame:
        """Xử lý giá trị Null: Không interpolate theo mandate, giữ nguyên Null."""
        # AC: Null khi thiếu data, không interpolate
        return df
