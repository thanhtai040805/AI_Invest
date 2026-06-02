"""
VN Calendar - Vietnam market calendar and trading rules
Handles ATO/ATC, T+2 settlement, price limits, and trading hours
"""
import logging
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, time, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class TradingSession(str, Enum):
    """Trading sessions in Vietnam market"""
    MORNING = "morning"  # 9:00 - 11:30
    AFTERNOON = "afternoon"  # 13:00 - 15:00
    ATO = "ato"  # At The Open (9:00 - 9:15)
    ATC = "atc"  # At The Close (14:45 - 15:00)


class BoardType(str, Enum):
    """Board types in Vietnam market"""
    HOSE = "HOSE"  # Ho Chi Minh Stock Exchange
    HNX = "HNX"  # Hanoi Stock Exchange
    UPCOM = "UPCOM"  # Unlisted Public Companies Market


class VNCalendar:
    """
    Vietnam market calendar and trading rules
    Fetches real working dates from DNSE API (with caching) for holiday-aware checks.
    """
    
    # Trading hours
    MORNING_START = time(9, 0)
    MORNING_END = time(11, 30)
    AFTERNOON_START = time(13, 0)
    AFTERNOON_END = time(15, 0)
    
    # ATO/ATC times
    ATO_START = time(9, 0)
    ATO_END = time(9, 15)
    ATC_START = time(14, 45)
    ATC_END = time(15, 0)
    
    # Settlement rules
    SETTLEMENT_DAYS = 2  # T+2 settlement
    
    # Price limits by board (percentage)
    PRICE_LIMITS = {
        BoardType.HOSE: 7.0,  # ±7%
        BoardType.HNX: 10.0,  # ±10%
        BoardType.UPCOM: 15.0,  # ±15%
    }
    
    # Trading days (Monday to Friday)
    TRADING_DAYS = [0, 1, 2, 3, 4]  # Monday=0, Friday=4
    
    def __init__(self):
        """Initialize VN Calendar"""
        self._working_dates: set[str] = set()
        self._last_fetch_time: float = 0.0
        logger.info("VN Calendar initialized")
    
    def _fetch_working_dates(self) -> set[str]:
        """Fetch working dates from DNSE REST API with caching (1 hour)."""
        now = datetime.now().timestamp()
        if self._working_dates and (now - self._last_fetch_time) < 3600:
            return self._working_dates

        try:
            from app.config.settings import get_settings
            from app.services.dnse.api.client import DNSEClient

            settings = get_settings()
            if not settings.dnse_configured:
                return self._working_dates

            client = DNSEClient(
                api_key=settings.dnse_api_key,
                api_secret=settings.dnse_api_secret,
                base_url=settings.dnse_base_url,
            )
            status, body = client.get_working_dates()
            if status == 200 and body:
                import json
                data = json.loads(body) if isinstance(body, str) else body
                dates = data if isinstance(data, list) else data.get("workingDates", data.get("data", []))
                for entry in dates:
                    date_str = entry.get("date", "")[:10] if isinstance(entry, dict) else str(entry)[:10]
                    if date_str:
                        self._working_dates.add(date_str)
                self._last_fetch_time = now
                logger.info("VNCalendar: fetched %d working dates from DNSE", len(self._working_dates))
        except Exception as e:
            logger.warning("VNCalendar: DNSE working dates fetch failed: %s", e)

        return self._working_dates

    def is_trading_day(self, date: Optional[Union[str, datetime]] = None) -> bool:
        """
        Check if a date is a trading day
        
        Args:
            date: Date to check (default: today). Can be string "YYYY-MM-DD" or datetime.
            
        Returns:
            bool: True if trading day
        """
        if date is None:
            date = datetime.now()
        elif isinstance(date, str):
            date = datetime.strptime(date, "%Y-%m-%d")
        
        # Check if weekday
        if date.weekday() not in self.TRADING_DAYS:
            return False
        
        # Check against DNSE working dates (covers Vietnamese holidays)
        try:
            working = self._fetch_working_dates()
            date_str = date.strftime("%Y-%m-%d")
            if working and date_str not in working:
                return False
        except Exception:
            pass  # If DNSE fetch fails, fall through to weekday-only check
        
        return True
    
    def is_trading_time(self, dt: Optional[datetime] = None) -> bool:
        """
        Check if current time is during trading hours
        
        Args:
            dt: Datetime to check (default: now)
            
        Returns:
            bool: True if trading time
        """
        if dt is None:
            dt = datetime.now()
        
        # Check if trading day
        if not self.is_trading_day(dt):
            return False
        
        current_time = dt.time()
        
        # Check if within trading hours
        morning_active = self.MORNING_START <= current_time <= self.MORNING_END
        afternoon_active = self.AFTERNOON_START <= current_time <= self.AFTERNOON_END
        
        return morning_active or afternoon_active
    
    def get_current_session(self, dt: Optional[datetime] = None) -> Optional[TradingSession]:
        """
        Get current trading session
        
        Args:
            dt: Datetime to check (default: now)
            
        Returns:
            TradingSession or None if not in session
        """
        if dt is None:
            dt = datetime.now()
        
        if not self.is_trading_day(dt):
            return None
        
        current_time = dt.time()
        
        # Check ATO
        if self.ATO_START <= current_time <= self.ATO_END:
            return TradingSession.ATO
        
        # Check ATC
        if self.ATC_START <= current_time <= self.ATC_END:
            return TradingSession.ATC
        
        # Check morning session
        if self.MORNING_START < current_time <= self.MORNING_END:
            return TradingSession.MORNING
        
        # Check afternoon session
        if self.AFTERNOON_START <= current_time < self.ATC_START:
            return TradingSession.AFTERNOON
        
        return None
    
    def calculate_settlement_date(
        self,
        trade_date: Optional[datetime] = None,
        days: int = SETTLEMENT_DAYS
    ) -> datetime:
        """
        Calculate settlement date (T+N)
        
        Args:
            trade_date: Trade date (default: today)
            days: Settlement days (default: T+2)
            
        Returns:
            datetime: Settlement date
        """
        if trade_date is None:
            trade_date = datetime.now()
        
        settlement_date = trade_date
        added_days = 0
        
        while added_days < days:
            settlement_date += timedelta(days=1)
            if self.is_trading_day(settlement_date):
                added_days += 1
        
        return settlement_date
    
    def calculate_price_limit(
        self,
        reference_price: float,
        board: BoardType = BoardType.HOSE
    ) -> Dict[str, float]:
        """
        Calculate price limits (floor and ceiling)
        
        Args:
            reference_price: Reference price
            board: Board type
            
        Returns:
            Dict containing floor and ceiling prices
        """
        limit_percent = self.PRICE_LIMITS.get(board, 7.0)
        
        floor_price = reference_price * (1 - limit_percent / 100)
        ceiling_price = reference_price * (1 + limit_percent / 100)
        
        return {
            "reference_price": reference_price,
            "floor_price": round(floor_price, 2),
            "ceiling_price": round(ceiling_price, 2),
            "limit_percent": limit_percent,
            "board": board,
        }
    
    def get_trading_hours_summary(self) -> Dict[str, Any]:
        """
        Get summary of trading hours
        
        Returns:
            Dict containing trading hours information
        """
        return {
            "morning_session": {
                "start": self.MORNING_START.strftime("%H:%M"),
                "end": self.MORNING_END.strftime("%H:%M"),
                "ato": {
                    "start": self.ATO_START.strftime("%H:%M"),
                    "end": self.ATO_END.strftime("%H:%M"),
                },
            },
            "afternoon_session": {
                "start": self.AFTERNOON_START.strftime("%H:%M"),
                "end": self.AFTERNOON_END.strftime("%H:%M"),
                "atc": {
                    "start": self.ATC_START.strftime("%H:%M"),
                    "end": self.ATC_END.strftime("%H:%M"),
                },
            },
            "settlement": f"T+{self.SETTLEMENT_DAYS}",
            "trading_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        }
    
    def get_next_trading_day(self, date: Optional[datetime] = None) -> datetime:
        """
        Get next trading day
        
        Args:
            date: Reference date (default: today)
            
        Returns:
            datetime: Next trading day
        """
        if date is None:
            date = datetime.now()
        
        next_day = date + timedelta(days=1)
        
        while not self.is_trading_day(next_day):
            next_day += timedelta(days=1)
        
        return next_day
    
    def get_previous_trading_day(self, date: Optional[datetime] = None) -> datetime:
        """
        Get previous trading day
        
        Args:
            date: Reference date (default: today)
            
        Returns:
            datetime: Previous trading day
        """
        if date is None:
            date = datetime.now()
        
        prev_day = date - timedelta(days=1)
        
        while not self.is_trading_day(prev_day):
            prev_day -= timedelta(days=1)
        
        return prev_day
