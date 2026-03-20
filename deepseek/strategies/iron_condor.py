# strategies/iron_condor.py
from typing import Dict, Any, Optional, Tuple, List

from strategies.base_strategy import BaseStrategy
from connection.ibkr_client import IBKRClient
from config import StrategyConfig, RiskConfig

class IronCondorStrategy(BaseStrategy):
    """Neutral iron condor strategy – OTM put spread and OTM call spread"""

    def __init__(self, ibkr_client: IBKRClient,
                 strategy_config: StrategyConfig,
                 risk_config: RiskConfig):
        super().__init__("IronCondor", ibkr_client, strategy_config, risk_config)

    def should_enter(self, market_state: Dict[str, Any],
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        # VIX filter – iron condor works best in low volatility
        if vix_state.get('regime', '') not in ['LOW', 'NORMAL']:
            self.logger.debug(f"VIX regime {vix_state.get('regime')} too high for iron condor")
            return None

        # First hour filter – need range-bound
        market_type = first_hour.get('market_type', '')
        if hasattr(market_type, 'value'):
            market_type = market_type.value
        if market_type != 'range_bound':
            self.logger.debug(f"Market type {market_type} not suitable for iron condor")
            return None

        # Calculate confidence
        confidence = 60

        # Boost for strong range signals
        if first_hour.get('range_score', 0) > 60:
            confidence += 20

        # Boost for low VIX
        if vix_state.get('regime') == 'LOW':
            confidence += 10

        if confidence < 70:
            self.logger.debug(f"Confidence {confidence} below threshold")
            return None

        # Calculate put side and call side strikes
        current_price = market_state['current_price']
        atm_strike = round(current_price / 5) * 5

        # Distance for 15 delta (approximately 1-1.5% OTM)
        put_distance = current_price * 0.012  # 1.2% below
        call_distance = current_price * 0.012  # 1.2% above

        put_short = atm_strike - round(put_distance / 5) * 5
        call_short = atm_strike + round(call_distance / 5) * 5

        return {
            'strategy': 'iron_condor',
            'confidence': confidence,
            'direction': 'neutral',
            'put_short': put_short,
            'call_short': call_short,
            'spread_width': self.strategy_config.condor_spread_width
        }

    def calculate_strikes(self, current_price: float,
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:

        put_short = params.get('put_short', round(current_price / 5) * 5 - 50)
        call_short = params.get('call_short', round(current_price / 5) * 5 + 50)
        width = params.get('spread_width', self.strategy_config.condor_spread_width)

        # Iron condor legs:
        # 1. Short put at put_short
        # 2. Long put at put_short - width
        # 3. Short call at call_short
        # 4. Long call at call_short + width
        strikes = [
            float(put_short),                  # Short put
            float(put_short - width),          # Long put
            float(call_short),                 # Short call
            float(call_short + width)          # Long call
        ]
        rights = ['P', 'P', 'C', 'C']
        return strikes, rights

    def calculate_credit_target(self, strikes: List[float],
                                market_state: Dict[str, Any]) -> float:
        """
        Calculate target credit for iron condor
        Total credit should be 20-30% of the widest wing
        """
        put_width = abs(strikes[1] - strikes[0])
        call_width = abs(strikes[3] - strikes[2])
        max_width = max(put_width, call_width)
        return max_width * 0.25  # 25% of max width