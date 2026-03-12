# strategies/call_spread.py (updated)
from typing import Dict, Any, Optional, Tuple, List

# Use relative imports
from strategies.base_strategy import BaseStrategy
from connection.ibkr_client import IBKRClient
from config import StrategyConfig

class CallCreditSpreadStrategy(BaseStrategy):
    """Bearish call credit spread strategy"""
    
    def __init__(self, ibkr_client: IBKRClient, config: StrategyConfig):
        super().__init__("CallCreditSpread", ibkr_client, config)
    
    # ... rest of the methods remain the same
        
    def should_enter(self, market_state: Dict[str, Any], 
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        
        # Check if blocked by sentiment
        if sentiment.get('block_call_spreads', False):
            self.logger.debug("Call spreads blocked by sentiment")
            return None
        
        # VIX filter
        if vix_state['regime'] not in ['NORMAL', 'LOW']:
            self.logger.debug(f"VIX regime {vix_state['regime']} not suitable for call spreads")
            return None
        
        # First hour filter - need bearish or unclear
        if first_hour['market_type'].value not in ['trending_bearish', 'unclear']:
            self.logger.debug(f"Market type {first_hour['market_type'].value} not suitable for call spreads")
            return None
        
        # Price action filter
        if market_state['current_price'] > market_state['vwap']:
            self.logger.debug("Price above VWAP, not suitable for bearish call spreads")
            return None
        
        # Gap up favors selling calls
        if sentiment.get('gap_direction') == 'UP' and sentiment.get('gap_size', 0) > 0.5:
            confidence_mult = 1.2
        else:
            confidence_mult = 1.0
        
        # Calculate confidence score
        confidence = 50
        
        # Boost based on conditions
        if market_state['distance_from_vwap'] < 0.2:  # Near or below VWAP
            confidence += 15
        
        if first_hour['market_type'].value == 'trending_bearish':
            confidence += 25
            
        if vix_state['trend'] == 'RISING':
            confidence += 10
            
        confidence = min(int(confidence * confidence_mult), 100)
        
        if confidence < 65:
            self.logger.debug(f"Confidence {confidence} below threshold")
            return None
        
        return {
            'strategy': 'call_credit_spread',
            'confidence': confidence,
            'direction': 'bearish',
            'delta_target': self.config.directional_delta_target,
            'spread_width': self.config.directional_spread_width
        }
    
    def calculate_strikes(self, current_price: float, 
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:
        
        # Round to nearest 5-point strike
        atm_strike = round(current_price / 5) * 5
        
        # Calculate short strike (OTM call)
        distance = current_price * 0.007  # 0.7% OTM
        short_strike = atm_strike + round(distance / 5) * 5
        
        # Long strike (5 points higher for protection)
        long_strike = short_strike + params['spread_width']
        
        strikes = [short_strike, long_strike]
        rights = ['C', 'C']  # Both calls
        
        return strikes, rights
    
    def calculate_credit_target(self, strikes: List[float], 
                                market_state: Dict[str, Any]) -> float:
        """
        Calculate target credit
        """
        spread_width = abs(strikes[1] - strikes[0])
        return spread_width * 0.30  # 30% of width