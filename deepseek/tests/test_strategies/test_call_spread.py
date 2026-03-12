# tests/test_strategies/test_call_spread.py
import pytest
from strategies.call_spread import CallCreditSpreadStrategy

class TestCallCreditSpreadStrategy:
    """Test suite for Call Credit Spread Strategy"""
    
    def test_initialization(self, mock_ibkr_client, sample_config):
        """Test strategy initialization"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        assert strategy.name == "CallCreditSpread"
    
    def test_should_enter_optimal_conditions(self, mock_ibkr_client, sample_config,
                                             sample_market_state, sample_vix_state,
                                             sample_sentiment, sample_first_hour):
        """Test should_enter with optimal conditions"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Modify conditions for bearish bias
        sample_vix_state['regime'] = 'NORMAL'
        sample_vix_state['trend'] = 'RISING'
        sample_first_hour['market_type'] = 'trending_bearish'
        sample_market_state['current_price'] = 4990  # Below VWAP
        sample_market_state['vwap'] = 5005
        sample_sentiment['block_call_spreads'] = False
        sample_sentiment['gap_direction'] = 'UP'
        sample_sentiment['gap_size'] = 0.75
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        assert setup['strategy'] == 'call_credit_spread'
        assert setup['direction'] == 'bearish'
        assert setup['confidence'] >= 65
    
    def test_should_enter_blocked_by_sentiment(self, mock_ibkr_client, sample_config,
                                                sample_market_state, sample_vix_state,
                                                sample_sentiment, sample_first_hour):
        """Test should_enter when blocked by sentiment"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_sentiment['block_call_spreads'] = True
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_high_vix(self, mock_ibkr_client, sample_config,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        """Test should_enter with high VIX"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'HIGH'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_bullish_market(self, mock_ibkr_client, sample_config,
                                          sample_market_state, sample_vix_state,
                                          sample_sentiment, sample_first_hour):
        """Test should_enter with bullish market"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_first_hour['market_type'] = 'trending_bullish'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_above_vwap(self, mock_ibkr_client, sample_config,
                                      sample_market_state, sample_vix_state,
                                      sample_sentiment, sample_first_hour):
        """Test should_enter when price above VWAP"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_market_state['current_price'] = 5010
        sample_market_state['vwap'] = 5005
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_calculate_strikes(self, mock_ibkr_client, sample_config, sample_market_state):
        """Test strike calculation"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        params = {
            'delta_target': 0.20,
            'spread_width': 5
        }
        
        strikes, rights = strategy.calculate_strikes(5000, sample_market_state, params)
        
        assert len(strikes) == 2
        assert len(rights) == 2
        assert rights[0] == 'C'
        assert rights[1] == 'C'
        assert strikes[1] > strikes[0]  # Long strike higher than short
        assert abs(strikes[0] - strikes[1]) == 5  # 5-point spread
    
    def test_calculate_credit_target(self, mock_ibkr_client, sample_config):
        """Test credit target calculation"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        strikes = [5005, 5010]
        credit = strategy.calculate_credit_target(strikes, {})
        
        assert credit == 1.50  # 5 * 0.30
    
    def test_gap_up_confidence_boost(self, mock_ibkr_client, sample_config,
                                      sample_market_state, sample_vix_state,
                                      sample_sentiment, sample_first_hour):
        """Test confidence boost from gap up"""
        strategy = CallCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Setup conditions for bearish bias
        sample_vix_state['regime'] = 'NORMAL'
        sample_first_hour['market_type'] = 'trending_bearish'
        sample_market_state['current_price'] = 4990
        sample_market_state['vwap'] = 5005
        
        # With gap up
        sample_sentiment['gap_direction'] = 'UP'
        sample_sentiment['gap_size'] = 0.75
        
        setup_with_gap = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        # Without gap up
        sample_sentiment['gap_direction'] = 'NONE'
        
        setup_without_gap = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        # Confidence should be higher with gap
        assert setup_with_gap is not None
        assert setup_without_gap is not None
        assert setup_with_gap['confidence'] > setup_without_gap['confidence']