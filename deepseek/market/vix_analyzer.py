# market/vix_analyzer.py (updated)
import numpy as np
from datetime import datetime
import logging
from typing import Dict, Any, List

# Use relative imports
from connection.ibkr_client import IBKRClient

class VIXAnalyzer:
    """Analyzes VIX for volatility regime classification"""
    
    def __init__(self, ibkr_client: IBKRClient):
        self.ibkr = ibkr_client
        self.logger = logging.getLogger(__name__)
        
        self.current_vix = 0.0
        self.vix_history: List[Dict[str, Any]] = []
        self.vix_ma20 = 0.0
        self.vix_ma50 = 0.0
        self.vix_percentile = 0.0
    
    # ... rest of the methods remain the same
        
    def update_vix(self, vix_price: float):
        """Update with new VIX price"""
        self.current_vix = vix_price
        self.vix_history.append({
            'time': datetime.now(),
            'value': vix_price
        })
        
        # Keep last 100 values
        if len(self.vix_history) > 100:
            self.vix_history = self.vix_history[-100:]
            
        self.calculate_indicators()
        
    def calculate_indicators(self):
        """Calculate VIX indicators"""
        if len(self.vix_history) >= 20:
            values = [d['value'] for d in self.vix_history[-20:]]
            self.vix_ma20 = np.mean(values)
            
        if len(self.vix_history) >= 50:
            values = [d['value'] for d in self.vix_history[-50:]]
            self.vix_ma50 = np.mean(values)
            
        # Calculate percentile
        if len(self.vix_history) >= 20:
            current = self.current_vix
            values = [d['value'] for d in self.vix_history]
            count_less = sum(1 for v in values if v < current)
            self.vix_percentile = (count_less / len(values)) * 100
    
    def get_regime(self) -> Dict[str, Any]:
        """
        Determine volatility regime based on VIX
        Returns regime classification and implications
        """
        if self.current_vix < 15:
            regime = "LOW"
            implication = "Complacency, range-bound tendencies"
            preferred_strategies = ["Iron Condor", "Iron Fly"]
        elif self.current_vix < 20:
            regime = "NORMAL"
            implication = "Balanced conditions"
            preferred_strategies = ["All strategies viable"]
        elif self.current_vix < 25:
            regime = "ELEVATED"
            implication = "Caution, trending potential"
            preferred_strategies = ["Directional spreads (wider strikes)"]
        else:
            regime = "HIGH"
            implication = "Panic/fear regime"
            preferred_strategies = ["Avoid premium selling", "Consider long options"]
        
        # Trend direction
        if self.vix_ma20 > 0:
            if self.current_vix > self.vix_ma20:
                vix_trend = "RISING"
            else:
                vix_trend = "FALLING"
        else:
            vix_trend = "UNKNOWN"
        
        return {
            'level': self.current_vix,
            'regime': regime,
            'trend': vix_trend,
            'ma20': self.vix_ma20,
            'ma50': self.vix_ma50,
            'percentile': self.vix_percentile,
            'implication': implication,
            'preferred_strategies': preferred_strategies,
            'block_long': self.current_vix > 25 or (vix_trend == "RISING" and self.current_vix > 20),
            'block_short': self.current_vix > 25 or (vix_trend == "FALLING" and self.current_vix < 15)
        }
    
    def should_trade(self) -> bool:
        """Determine if trading is advisable based on VIX"""
        # Avoid trading in extreme VIX conditions
        if self.current_vix > 30:
            self.logger.warning(f"VIX too high ({self.current_vix:.1f}) - avoiding trades")
            return False
        
        return True