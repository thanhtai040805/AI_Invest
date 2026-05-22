"""
Pydantic models for DNSE WebSocket payload validation.

All incoming DNSE data is validated before processing or Redis publish.
Invalid payloads are rejected with detailed error logging.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class ValidatedTrade(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    price: float = Field(..., ge=0)
    volume: int = Field(..., ge=0)
    change: float = Field(default=0)
    change_percent: float = Field(default=0, alias="changePercent")
    trading_value: float = Field(default=0, alias="tradingValue")
    open: float = Field(default=0)
    high: float = Field(default=0)
    low: float = Field(default=0)
    prev_close: float = Field(default=0, alias="prevClose")
    ceiling: float = Field(default=0)
    floor: float = Field(default=0)
    trend: str = Field(default="steady")
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")

    @field_validator("price")
    @classmethod
    def price_not_nan(cls, v: float) -> float:
        if v != v:
            raise ValueError("Price is NaN")
        return v

    @field_validator("volume")
    @classmethod
    def volume_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Volume cannot be negative")
        return v


class ValidatedOrderBook(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    bids: List[Dict[str, Any]] = Field(default_factory=list)
    asks: List[Dict[str, Any]] = Field(default_factory=list)
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")


class ValidatedMarketIndex(BaseModel):
    name: str = Field(..., min_length=1)
    value: float = Field(..., ge=0)
    change: float = Field(default=0)
    change_percent: float = Field(default=0, alias="changePercent")
    volume: int = Field(default=0)
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")


class ValidatedForeignTrading(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    buy_volume: int = Field(default=0, alias="buyVolume")
    sell_volume: int = Field(default=0, alias="sellVolume")
    net_volume: int = Field(default=0, alias="netVolume")
    buy_value: float = Field(default=0, alias="buyValue")
    sell_value: float = Field(default=0, alias="sellValue")
    net_value: float = Field(default=0, alias="netValue")
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")


class ValidatedOhlc(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    open: float = Field(..., ge=0)
    high: float = Field(..., ge=0)
    low: float = Field(..., ge=0)
    close: float = Field(..., ge=0)
    volume: int = Field(default=0, ge=0)
    resolution: str = Field(default="1")
    timestamp: Optional[int] = Field(default=None)
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")

    @field_validator("high")
    @classmethod
    def high_ge_low(cls, v: float, info) -> float:
        low = info.data.get("low", 0)
        if v < low:
            return low
        return v


class ValidatedExpectedPrice(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    expected_price: float = Field(default=0, alias="expectedPrice")
    matched_volume: int = Field(default=0, alias="matchedVolume")
    received_at: Optional[float] = Field(default=None, alias="receivedAt")
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")


class ValidatedSecurityDef(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    name: str = Field(default="")
    exchange: str = Field(default="")
    ceiling: float = Field(default=0)
    floor: float = Field(default=0)
    prev_close: float = Field(default=0, alias="prevClose")
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")


class ValidatedTradeExtra(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    price: float = Field(..., ge=0)
    volume: int = Field(..., ge=0)
    order_id: str = Field(default="", alias="orderId")
    match_type: str = Field(default="", alias="matchType")
    received_at: Optional[float] = Field(default=None, alias="receivedAt")
    last_update: Optional[str] = Field(default=None, alias="lastUpdate")

    @field_validator("price")
    @classmethod
    def price_not_nan(cls, v: float) -> float:
        if v != v:
            raise ValueError("Price is NaN")
        return v


def validate_payload(model_class: type[BaseModel], data: dict) -> Optional[BaseModel]:
    """Validate data against a Pydantic model. Returns None if invalid."""
    try:
        return model_class.model_validate(data)
    except Exception as e:
        symbol = data.get("symbol", "unknown")
        print(f"[Validation] {model_class.__name__} reject ({symbol}): {e}")
        return None
