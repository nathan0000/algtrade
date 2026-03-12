# market/first_hour.py (updated)
from datetime import datetime, time
import pytz
import numpy as np
import logging
from typing import Dict, Any, List
from enum import Enum

class MarketType(Enum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGE_BOUND = "range_bound"
    UNCLEAR = "unclear"

class FirstHourAnalyzer:
    """Analyzes first hour of trading to determine market character"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.ny_tz = pytz.timezone('America/New_York')
        
        # First hour data
        self.first_hour_data: List[Dict[str, Any]] = []
        self.pre_market_high = 0.0
        self.pre_market_low = float('inf')
        self.yesterday_close = 0.0
        self.yesterday_high = 0.0
        self.yesterday_low = 0.0
        
        # Analysis results
        self.market_type = MarketType.UNCLEAR
        self.confidence = 0
    
    # ... rest of the methods remain the same
        
    def set_reference_levels(self, pre_high: float, pre_low: float, 
                             y_close: float, y_high: float, y_low: float):
        """Set reference levels for analysis"""
        self.pre_market_high = pre_high
        self.pre_market_low = pre_low
        self.yesterday_close = y_close
        self.yesterday_high = y_high
        self.yesterday_low = y_low
        
    def add_first_hour_bar(self, time: datetime, open: float, high: float, 
                          low: float, close: float, volume: int):
        """Add a bar from the first hour of trading"""
        self.first_hour_data.append({
            'time': time,
            'open': open,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })
        
    def analyze(self) -> Dict[str, Any]:
        """
        Analyze first hour data to determine market type
        Returns classification with confidence score
        """
        if len(self.first_hour_data) < 5:
            return {
                'market_type': MarketType.UNCLEAR,
                'confidence': 0,
                'reason': 'Insufficient data'
            }
        
        # Extract first hour range
        first_hour_high = max(d['high'] for d in self.first_hour_data)
        first_hour_low = min(d['low'] for d in self.first_hour_data)
        first_hour_close = self.first_hour_data[-1]['close']
        
        # Calculate indicators
        range_width = first_hour_high - first_hour_low
        pre_market_range = self.pre_market_high - self.pre_market_low
        
        # Trend indicators
        broke_pre_high = first_hour_high > self.pre_market_high
        broke_pre_low = first_hour_low < self.pre_market_low
        broke_y_high = first_hour_high > self.yesterday_high
        broke_y_low = first_hour_low < self.yesterday_low
        
        # Price position
        above_pre_high = first_hour_close > self.pre_market_high
        below_pre_low = first_hour_close < self.pre_market_low
        
        # Calculate slope (linear regression)
        closes = [d['close'] for d in self.first_hour_data]
        x = np.arange(len(closes))
        if len(closes) > 1:
            slope = np.polyfit(x, closes, 1)[0]
        else:
            slope = 0
        
        # Volume analysis
        volumes = [d['volume'] for d in self.first_hour_data]
        avg_volume = np.mean(volumes) if volumes else 0
        volume_trend = volumes[-1] > avg_volume * 1.2 if volumes else False
        
        # Classification logic
        trend_score = 0
        range_score = 0
        
        # Trend bullish indicators
        if broke_pre_high:
            trend_score += 20
        if broke_y_high:
            trend_score += 15
        if above_pre_high:
            trend_score += 15
        if slope > 0.1:
            trend_score += 25
        if first_hour_close > first_hour_low + range_width * 0.7:
            trend_score += 10
        if volume_trend and slope > 0:
            trend_score += 15
            
        # Trend bearish indicators
        if broke_pre_low:
            trend_score -= 20
        if broke_y_low:
            trend_score -= 15
        if below_pre_low:
            trend_score -= 15
        if slope < -0.1:
            trend_score -= 25
        if first_hour_close < first_hour_low + range_width * 0.3:
            trend_score -= 10
        if volume_trend and slope < 0:
            trend_score -= 15
            
        # Range-bound indicators
        if abs(slope) < 0.05:
            range_score += 25
        if not broke_pre_high and not broke_pre_low:
            range_score += 20
        if pre_market_range > 0 and range_width < pre_market_range * 0.8:
            range_score += 15
        if abs(first_hour_close - self.yesterday_close) < range_width * 0.2:
            range_score += 20
            
        # Determine market type
        if abs(trend_score) >= 40:
            if trend_score > 0:
                market_type = MarketType.TRENDING_BULLISH
                confidence = min(abs(trend_score), 100)
            else:
                market_type = MarketType.TRENDING_BEARISH
                confidence = min(abs(trend_score), 100)
        elif range_score >= 50:
            market_type = MarketType.RANGE_BOUND
            confidence = min(range_score, 100)
        else:
            market_type = MarketType.UNCLEAR
            confidence = 0
        
        self.market_type = market_type
        self.confidence = confidence
        
        return {
            'market_type': market_type,
            'confidence': confidence,
            'trend_score': trend_score,
            'range_score': range_score,
            'first_hour_range': (first_hour_low, first_hour_high),
            'broke_pre_high': broke_pre_high,
            'broke_pre_low': broke_pre_low,
            'broke_y_high': broke_y_high,
            'broke_y_low': broke_y_low,
            'slope': slope,
            'volume_trend': volume_trend
        }
    
    def get_recommended_strategies(self) -> List[str]:
        """Get recommended strategies based on market type"""
        if self.market_type == MarketType.TRENDING_BULLISH:
            return ["Put Credit Spread"]
        elif self.market_type == MarketType.TRENDING_BEARISH:
            return ["Call Credit Spread"]
        elif self.market_type == MarketType.RANGE_BOUND:
            return ["Iron Condor", "Iron Fly"]
        else:
            return ["Iron Condor"]  # Default to neutral