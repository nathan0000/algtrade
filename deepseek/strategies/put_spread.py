# strategies/put_spread.py (fixed imports)
from typing import Dict, Any, Optional, Tuple, List

# Fix: Import from strategies.base_strategy, not base_strategy directly
from strategies.base_strategy import BaseStrategy
from connection.ibkr_client import IBKRClient
from config import StrategyConfig

class PutCreditSpreadStrategy(BaseStrategy):
    """Bullish put credit spread strategy"""
    
    def __init__(self, ibkr_client: IBKRClient, config: StrategyConfig):
        super().__init__("PutCreditSpread", ibkr_client, config)
        
    def should_enter(self, market_state: Dict[str, Any], 
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        
        # Check if blocked by sentiment
        if sentiment.get('block_put_spreads', False):
            self.logger.debug("Put spreads blocked by sentiment")
            return None
        
        # VIX filter
        if vix_state.get('regime', '') not in ['NORMAL', 'LOW']:
            self.logger.debug(f"VIX regime {vix_state.get('regime')} not suitable for put spreads")
            return None
        
        # First hour filter - need bullish or unclear
        market_type = first_hour.get('market_type', '')
        if hasattr(market_type, 'value'):
            market_type = market_type.value
        if market_type not in ['trending_bullish', 'unclear']:
            self.logger.debug(f"Market type {market_type} not suitable for put spreads")
            return None
        
        # Price action filter
        if market_state.get('current_price', 0) < market_state.get('vwap', 0):
            self.logger.debug("Price below VWAP, not suitable for bullish put spreads")
            return None
        
        # Check if we prefer calls based on sentiment
        if sentiment.get('prefer_calls', False):
            confidence_mult = sentiment.get('confidence_multiplier', 1.0)
        else:
            confidence_mult = 1.0
        
        # Calculate confidence score
        confidence = 50
        
        # Boost based on conditions
        if market_state.get('distance_from_vwap', 0) > -0.2:  # Near or above VWAP
            confidence += 15
        
        if first_hour.get('market_type') == 'trending_bullish':
            confidence += 25
            
        if vix_state.get('trend') == 'FALLING':
            confidence += 10
            
        confidence = min(int(confidence * confidence_mult), 100)
        
        if confidence < 65:
            self.logger.debug(f"Confidence {confidence} below threshold")
            return None
        
        return {
            'strategy': 'put_credit_spread',
            'confidence': confidence,
            'direction': 'bullish',
            'delta_target': self.config.directional_delta_target,
            'spread_width': self.config.directional_spread_width
        }
    
    def calculate_strikes(self, current_price: float, 
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:
        
        # Round to nearest 5-point strike
        atm_strike = round(current_price / 5) * 5
        
        # Calculate short strike (OTM put)
        # For 20 delta, approximately 0.5-1.0% OTM
        distance = current_price * 0.007  # 0.7% OTM
        short_strike = atm_strike - round(distance / 5) * 5
        
        # Long strike (5 points lower for protection)
        long_strike = short_strike - params.get('spread_width', 5)
        
        strikes = [float(short_strike), float(long_strike)]
        rights = ['P', 'P']  # Both puts
        
        return strikes, rights
    
    def calculate_credit_target(self, strikes: List[float], 
                                market_state: Dict[str, Any]) -> float:
        """
        Calculate target credit (simplified - in production, use market data)
        Credit should be 20-33% of spread width
        """
        spread_width = abs(strikes[1] - strikes[0])
        return spread_width * 0.30  # 30% of width