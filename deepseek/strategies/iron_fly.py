# strategies/iron_fly.py
from typing import Dict, Any, Optional, Tuple, List

from strategies.base_strategy import BaseStrategy
from connection.ibkr_client import IBKRClient
from config import StrategyConfig, RiskConfig

class IronFlyStrategy(BaseStrategy):
    """Neutral iron fly strategy – ATM short put and call with wings"""

    def __init__(self, ibkr_client: IBKRClient,
                 strategy_config: StrategyConfig,
                 risk_config: RiskConfig):
        super().__init__("IronFly", ibkr_client, strategy_config, risk_config)

    def should_enter(self, market_state: Dict[str, Any],
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        # VIX filter – iron fly works best in low volatility
        if vix_state.get('regime', '') not in ['LOW', 'NORMAL']:
            self.logger.debug(f"VIX regime {vix_state.get('regime')} too high for iron fly")
            return None

        # First hour filter – need range-bound
        market_type = first_hour.get('market_type', '')
        if hasattr(market_type, 'value'):
            market_type = market_type.value
        if market_type != 'range_bound':
            self.logger.debug(f"Market type {market_type} not suitable for iron fly")
            return None

        # Confidence from first hour analysis
        if first_hour.get('confidence', 0) < 60:
            self.logger.debug(f"First hour confidence {first_hour.get('confidence')} too low")
            return None

        # Calculate confidence
        confidence = 60

        # Boost for very low VIX
        if vix_state.get('regime') == 'LOW':
            confidence += 15

        # Boost for strong range signals
        if first_hour.get('range_score', 0) > 60:
            confidence += 15

        if confidence < 70:
            self.logger.debug(f"Confidence {confidence} below threshold")
            return None

        # Calculate wing width based on ATR
        atr = market_state.get('atr', 10)
        wing_width = atr * self.strategy_config.iron_fly_atr_multiplier

        # Bound the width
        wing_width = max(self.strategy_config.iron_fly_min_width,
                        min(wing_width, self.strategy_config.iron_fly_max_width))

        # Round to nearest 5
        wing_width = round(wing_width / 5) * 5

        return {
            'strategy': 'iron_fly',
            'confidence': confidence,
            'direction': 'neutral',
            'wing_width': wing_width,
            'central_strike': round(market_state['current_price'] / 5) * 5
        }

    def calculate_strikes(self, current_price: float,
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:

        central = params.get('central_strike', round(current_price / 5) * 5)
        wing_width = params.get('wing_width', 30)

        # Iron fly legs:
        # 1. Short put at central
        # 2. Long put at central - wing_width
        # 3. Short call at central
        # 4. Long call at central + wing_width
        strikes = [
            float(central),                    # Short put
            float(central - wing_width),       # Long put
            float(central),                    # Short call
            float(central + wing_width)        # Long call
        ]
        rights = ['P', 'P', 'C', 'C']
        return strikes, rights

    def calculate_credit_target(self, strikes: List[float],
                                market_state: Dict[str, Any]) -> float:
        """
        Calculate target credit for iron fly
        Credit should be 20-33% of wing width
        """
        wing_width = abs(strikes[3] - strikes[2])  # Call wing width
        return wing_width * 0.25  # 25% of wing width