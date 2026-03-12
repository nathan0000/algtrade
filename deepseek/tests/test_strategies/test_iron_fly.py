# tests/test_strategies/test_iron_fly.py
import pytest
from strategies.iron_fly import IronFlyStrategy

class TestIronFlyStrategy:
    """Test suite for Iron Fly Strategy"""
    
    def test_initialization(self, mock_ibkr_client, sample_config):
        """Test strategy initialization"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        assert strategy.name == "IronFly"
    
    def test_should_enter_optimal_conditions(self, mock_ibkr_client, sample_config,
                                             sample_market_state, sample_vix_state,
                                             sample_sentiment, sample_first_hour):
        """Test should_enter with optimal conditions"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Modify conditions for iron fly
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
    
    def test_should_enter_high_vix(self, mock_ibkr_client, sample_config,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        """Test should_enter with high VIX"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'ELEVATED'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_trending_market(self, mock_ibkr_client, sample_config,
                                           sample_market_state, sample_vix_state,
                                           sample_sentiment, sample_first_hour):
        """Test should_enter with trending market"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_first_hour['market_type'] = 'trending_bullish'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_low_first_hour_confidence(self, mock_ibkr_client, sample_config,
                                                     sample_market_state, sample_vix_state,
                                                     sample_sentiment, sample_first_hour):
        """Test should_enter with low first hour confidence"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 50  # Too low
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_calculate_strikes(self, mock_ibkr_client, sample_config, sample_market_state):
        """Test strike calculation for iron fly"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        params = {
            'central_strike': 5000,
            'wing_width': 30
        }
        
        strikes, rights = strategy.calculate_strikes(5000, sample_market_state, params)
        
        assert len(strikes) == 4
        assert len(rights) == 4
        assert rights == ['P', 'P', 'C', 'C']
        
        # Check strikes
        assert strikes[0] == 5000  # Short put
        assert strikes[1] == 4970  # Long put
        assert strikes[2] == 5000  # Short call
        assert strikes[3] == 5030  # Long call
    
    def test_calculate_credit_target(self, mock_ibkr_client, sample_config):
        """Test credit target calculation for iron fly"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        strikes = [5000, 4970, 5000, 5030]
        credit = strategy.calculate_credit_target(strikes, {})
        
        wing_width = 30
        expected_credit = wing_width * 0.25  # 25% of wing width
        assert credit == expected_credit
    
    def test_wing_width_calculation(self, mock_ibkr_client, sample_config,
                                     sample_market_state, sample_vix_state,
                                     sample_sentiment, sample_first_hour):
        """Test wing width calculation based on ATR"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Test with different ATR values
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        sample_first_hour['range_score'] = 75
        
        # ATR = 10
        sample_market_state['atr'] = 10
        sample_market_state['current_price'] = 5000
        
        setup1 = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        # ATR = 20
        sample_market_state['atr'] = 20
        
        setup2 = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup1 is not None
        assert setup2 is not None
        assert setup2['wing_width'] > setup1['wing_width']
    
    def test_wing_width_bounds(self, mock_ibkr_client, sample_config,
                                sample_market_state, sample_vix_state,
                                sample_sentiment, sample_first_hour):
        """Test wing width stays within bounds"""
        strategy = IronFlyStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        
        # Very low ATR
        sample_market_state['atr'] = 1
        sample_market_state['current_price'] = 5000
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        assert setup['wing_width'] >= strategy.config.iron_fly_min_width
        
        # Very high ATR
        sample_market_state['atr'] = 50
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        assert setup['wing_width'] <= strategy.config.iron_fly_max_width