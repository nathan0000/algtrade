# market/sentiment.py (updated)
import numpy as np
from datetime import datetime
import logging
from typing import Dict, Any, List, Optional

class SentimentAnalyzer:
    """Analyzes market sentiment indicators"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Put/Call ratio
        self.put_call_ratio = 0.0
        self.put_call_history: List[Dict[str, Any]] = []
        
        # Gamma exposure (simplified)
        self.gex_value = 0.0
        self.gex_trend = "NEUTRAL"
        
        # Consecutive day streaks
        self.consecutive_up_days = 0
        self.consecutive_down_days = 0
        
        # Gap analysis
        self.gap_size = 0.0
        self.gap_direction = "NONE"
        
        # Inside day detection
        self.is_inside_day = False
    
    # ... rest of the methods remain the same
        
    def update_put_call_ratio(self, ratio: float):
        """Update put/call ratio"""
        self.put_call_ratio = ratio
        self.put_call_history.append({
            'time': datetime.now(),
            'ratio': ratio
        })
        
        # Keep last 20
        if len(self.put_call_history) > 20:
            self.put_call_history = self.put_call_history[-20:]
    
    def update_gamma_exposure(self, gex: float):
        """Update gamma exposure"""
        self.gex_value = gex
        
        if len(self.put_call_history) >= 2:
            if gex > self.gex_value:
                self.gex_trend = "RISING"
            elif gex < self.gex_value:
                self.gex_trend = "FALLING"
    
    def update_daily_streak(self, close_price: float, prev_close: float):
        """Update consecutive day streak"""
        if close_price > prev_close:
            self.consecutive_up_days += 1
            self.consecutive_down_days = 0
        elif close_price < prev_close:
            self.consecutive_down_days += 1
            self.consecutive_up_days = 0
    
    def update_gap_analysis(self, open_price: float, prev_close: float):
        """Analyze market gap"""
        self.gap_size = ((open_price - prev_close) / prev_close) * 100
        if abs(self.gap_size) > 0.2:  # 0.2% threshold
            if self.gap_size > 0:
                self.gap_direction = "UP"
            else:
                self.gap_direction = "DOWN"
        else:
            self.gap_direction = "NONE"
    
    def update_inside_day(self, today_high: float, today_low: float, 
                          yesterday_high: float, yesterday_low: float):
        """Detect inside day pattern"""
        self.is_inside_day = (today_high < yesterday_high and 
                              today_low > yesterday_low)
    
    def get_sentiment_signals(self) -> Dict[str, Any]:
        """Get sentiment-based trading signals"""
        
        signals = {
            'block_put_spreads': False,
            'block_call_spreads': False,
            'prefer_puts': False,
            'prefer_calls': False,
            'confidence_multiplier': 1.0
        }
        
        # Put/Call ratio signals
        if len(self.put_call_history) >= 5:
            avg_ratio = np.mean([d['ratio'] for d in self.put_call_history[-5:]])
            if self.put_call_ratio > 1.2:
                # Extreme fear - contrarian bullish
                signals['prefer_calls'] = True
                signals['confidence_multiplier'] = 1.2
            elif self.put_call_ratio < 0.6:
                # Extreme greed - contrarian bearish
                signals['prefer_puts'] = True
                signals['confidence_multiplier'] = 1.2
        
        # Consecutive day signals
        if self.consecutive_up_days >= 3:
            signals['block_call_spreads'] = True  # Avoid selling calls after rally
        if self.consecutive_down_days >= 3:
            signals['block_put_spreads'] = True  # Avoid selling puts after selloff
        
        # Gap signals
        if self.gap_direction == "UP" and self.gap_size > 0.5:
            # Big gap up - favor selling calls
            signals['prefer_puts'] = True
        elif self.gap_direction == "DOWN" and self.gap_size < -0.5:
            # Big gap down - favor selling puts
            signals['prefer_calls'] = True
        
        # Inside day signal
        if self.is_inside_day:
            # Inside days are safer for selling puts
            signals['prefer_calls'] = True
        
        # Gamma exposure signals
        if self.gex_value > 0:
            # Positive GEX - support, bullish bias
            signals['prefer_calls'] = True
        elif self.gex_value < 0:
            # Negative GEX - resistance, bearish bias
            signals['prefer_puts'] = True
        
        return signals