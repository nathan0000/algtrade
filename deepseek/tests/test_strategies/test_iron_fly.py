# tests/test_strategies/test_iron_fly.py
import pytest
from datetime import datetime
from unittest.mock import Mock

from strategies.iron_fly import IronFlyStrategy
from config import StrategyConfig, RiskConfig


class TestIronFlyStrategy:
    """Test suite for Iron Fly Strategy"""

    def test_initialization(self, mock_ibkr_client):
        """Test strategy initialization"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)
        assert strategy.name == "IronFly"

    def test_should_enter_optimal_conditions(self, mock_ibkr_client,
                                             sample_market_state, sample_vix_state,
                                             sample_sentiment, sample_first_hour):
        """Test should_enter with optimal conditions"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        sample_first_hour['range_score'] = 75
        sample_market_state['atr'] = 12

        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        assert setup is not None
        assert setup['strategy'] == 'iron_fly'
        assert setup['direction'] == 'neutral'
        assert setup['confidence'] >= 70
        assert 'wing_width' in setup
        assert 'central_strike' in setup

    def test_should_enter_high_vix(self, mock_ibkr_client,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        """Test should_enter with high VIX"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_vix_state['regime'] = 'ELEVATED'

        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        assert setup is None

    def test_should_enter_trending_market(self, mock_ibkr_client,
                                           sample_market_state, sample_vix_state,
                                           sample_sentiment, sample_first_hour):
        """Test should_enter with trending market"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_first_hour['market_type'] = 'trending_bullish'

        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        assert setup is None

    def test_should_enter_low_first_hour_confidence(self, mock_ibkr_client,
                                                     sample_market_state, sample_vix_state,
                                                     sample_sentiment, sample_first_hour):
        """Test should_enter with low first hour confidence"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 50  # Too low

        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        assert setup is None

    def test_calculate_strikes(self, mock_ibkr_client, sample_market_state):
        """Test strike calculation for iron fly"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        params = {
            'central_strike': 5000,
            'wing_width': 30
        }

        strikes, rights = strategy.calculate_strikes(5000, sample_market_state, params)

        assert len(strikes) == 4
        assert len(rights) == 4
        assert rights == ['P', 'P', 'C', 'C']
        assert strikes[0] == 5000
        assert strikes[1] == 4970
        assert strikes[2] == 5000
        assert strikes[3] == 5030

    def test_calculate_credit_target(self, mock_ibkr_client):
        """Test credit target calculation for iron fly"""
        strategy_config = StrategyConfig()
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        strikes = [5000, 4970, 5000, 5030]
        credit = strategy.calculate_credit_target(strikes, {})
        assert credit == 7.5  # 30 * 0.25

    def test_wing_width_calculation(self, mock_ibkr_client,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        strategy_config = StrategyConfig(
            iron_fly_atr_multiplier=6.0,
            iron_fly_min_width=15,
            iron_fly_max_width=50
        )
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        sample_first_hour['range_score'] = 75

        # ATR = 5 → width = 30
        sample_market_state['atr'] = 5
        sample_market_state['current_price'] = 5000
        setup1 = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        # ATR = 8 → width = 48, rounded to 50
        sample_market_state['atr'] = 8
        setup2 = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )

        assert setup1 is not None
        assert setup2 is not None
        assert setup2['wing_width'] > setup1['wing_width']
        assert setup1['wing_width'] == 30
        assert setup2['wing_width'] == 50   # was 48, now corrected

    def test_wing_width_bounds(self, mock_ibkr_client,
                                sample_market_state, sample_vix_state,
                                sample_sentiment, sample_first_hour):
        """Test wing width stays within bounds"""
        strategy_config = StrategyConfig(
            iron_fly_atr_multiplier=6.0,
            iron_fly_min_width=15,
            iron_fly_max_width=50
        )
        risk_config = RiskConfig()
        strategy = IronFlyStrategy(mock_ibkr_client, strategy_config, risk_config)

        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80

        # Very low ATR → should hit min width
        sample_market_state['atr'] = 1
        sample_market_state['current_price'] = 5000
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        assert setup is not None
        assert setup['wing_width'] == 15  # min

        # Very high ATR → should hit max width
        sample_market_state['atr'] = 50
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        assert setup is not None
        assert setup['wing_width'] == 50  # max