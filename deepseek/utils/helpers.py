# utils/helpers.py (new file)
"""
Helper utility functions
"""
import math
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

def round_to_strike(price: float, increment: float = 5.0) -> float:
    """Round price to nearest valid strike"""
    return round(price / increment) * increment

def calculate_position_risk(entry_price: float, stop_price: float, quantity: int) -> float:
    """Calculate position risk in dollars"""
    return abs(entry_price - stop_price) * 100 * quantity

def time_until_expiry(expiry_date: datetime) -> timedelta:
    """Calculate time until option expiry"""
    return expiry_date - datetime.now()

def is_market_hours(dt: Optional[datetime] = None) -> bool:
    """Check if given time is within market hours"""
    if dt is None:
        dt = datetime.now()
    
    # Market hours 9:30 AM - 4:00 PM ET
    market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = dt.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= dt <= market_close

def calculate_atr_from_high_low(highs: List[float], lows: List[float], 
                                closes: List[float], period: int = 14) -> float:
    """Calculate ATR from price lists"""
    if len(highs) < period + 1:
        return 0.0
    
    true_ranges = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)
    
    return sum(true_ranges[-period:]) / period