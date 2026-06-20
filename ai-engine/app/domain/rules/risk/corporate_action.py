"""Corporate Action Adjustment Engine — IOS v5.1

Module điều chỉnh giá ngược (backward adjustment) khi có chia tách, cổ tức.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class ActionType(Enum):
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    DIVIDEND_CASH = "DIVIDEND_CASH"
    DIVIDEND_STOCK = "DIVIDEND_STOCK"
    RIGHTS = "RIGHTS"


@dataclass
class MarketDataRow:
    ticker: str
    date: date
    close_adj: float
    open_adj: float = 0.0
    high_adj: float = 0.0
    low_adj: float = 0.0
    vwap: float = 0.0
    close_unadj: float = 0.0
    adj_factor: float = 1.0


@dataclass
class CorporateActionRecord:
    ticker: str
    action_type: ActionType
    ex_date: date
    ratio: float = 0.0
    cash_amount: float = 0.0
    applied: bool = False
    adjustment_factor: Optional[float] = None


@dataclass
class AdjustmentResult:
    id: str
    ticker: str
    action_type: ActionType
    ex_date: date
    factor: float
    rows_affected: int
    success: bool
    error: Optional[str] = None


class AdjustmentReport:
    def __init__(self):
        self.results: List[AdjustmentResult] = []

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)


def _compute_split_factor(ratio: float) -> float:
    if ratio <= 0: return 1.0
    return 1.0 / ratio


def compute_adjustment_factor(ca: CorporateActionRecord) -> float:
    if ca.adjustment_factor is not None:
        return ca.adjustment_factor
        
    if ca.action_type == ActionType.SPLIT:
        return _compute_split_factor(ca.ratio)
    elif ca.action_type == ActionType.MERGE:
        return ca.ratio
    elif ca.action_type == ActionType.DIVIDEND_STOCK:
        return 1.0 / (1.0 + ca.ratio)
    elif ca.action_type == ActionType.RIGHTS:
        return 1.0 / (1.0 + ca.ratio)
    
    return 1.0


def adjust_prices_historical(
    data: List[MarketDataRow], 
    action: CorporateActionRecord, 
    ex_date_close_price: Optional[float] = None
) -> Tuple[List[MarketDataRow], float]:
    if action.applied:
        return data, 1.0
        
    factor = compute_adjustment_factor(action)
    
    if action.action_type == ActionType.DIVIDEND_CASH and ex_date_close_price:
        factor = (ex_date_close_price - action.cash_amount) / ex_date_close_price

    new_data = []
    for r in data:
        if r.date < action.ex_date:
            new_row = MarketDataRow(
                ticker=r.ticker,
                date=r.date,
                close_adj=r.close_adj * factor,
                open_adj=r.open_adj * factor,
                high_adj=r.high_adj * factor,
                low_adj=r.low_adj * factor,
                vwap=r.vwap * factor,
                close_unadj=r.close_unadj,
                adj_factor=r.adj_factor * factor
            )
            new_data.append(new_row)
        else:
            new_data.append(r)
            
    return new_data, factor


def apply_all_pending_adjustments(
    market_data_dict: Dict[str, List[MarketDataRow]], 
    pending_actions: List[CorporateActionRecord]
) -> Tuple[Dict[str, List[MarketDataRow]], AdjustmentReport]:
    report = AdjustmentReport()
    adjusted_data = market_data_dict.copy()
    
    # Sort actions by date descending to compound correctly
    pending_actions.sort(key=lambda x: x.ex_date, reverse=True)
    
    for i, ca in enumerate(pending_actions):
        ticker = ca.ticker
        if ticker not in adjusted_data:
            report.results.append(AdjustmentResult(str(i), ticker, ca.action_type, ca.ex_date, 1.0, 0, False, "Ticker not found"))
            continue
            
        # Get close price on ex_date for cash dividends
        ex_close = None
        if ca.action_type == ActionType.DIVIDEND_CASH:
            for r in adjusted_data[ticker]:
                if r.date == ca.ex_date:
                    ex_close = r.close_unadj
                    break
        
        rows, factor = adjust_prices_historical(adjusted_data[ticker], ca, ex_close)
        adjusted_data[ticker] = rows
        report.results.append(AdjustmentResult(str(i), ticker, ca.action_type, ca.ex_date, factor, len(rows), True))
        
    return adjusted_data, report
