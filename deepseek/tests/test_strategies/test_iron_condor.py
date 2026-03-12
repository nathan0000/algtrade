# tests/test_strategies/test_iron_condor.py
import pytest
from strategies.iron_condor import IronCondorStrategy

class TestIronCondorStrategy:
    """Test suite for Iron Condor Strategy"""
    
    def test_initialization(self, mock_ibkr_client, sample_config):
        """Test strategy initialization"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        assert strategy.name == "IronCondor"
    
    def test_should_enter_optimal_conditions(self, mock_ibkr_client, sample_config,
                                             sample_market_state, sample_vix_state,
                                             sample_sentiment, sample_first_hour):
        """Test should_enter with optimal conditions"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Modify conditions for iron condor
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        sample_first_hour['range_score'] = 75
        sample_market_state['current_price'] = 5000
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        assert setup['strategy'] == 'iron_condor'
        assert setup['direction'] == 'neutral'
        assert setup['confidence'] >= 70
        assert 'put_short' in setup
        assert 'call_short' in setup
        assert 'spread_width' in setup
    
    def test_should_enter_high_vix(self, mock_ibkr_client, sample_config,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        """Test should_enter with high VIX"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
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
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_first_hour['market_type'] = 'trending_bullish'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_calculate_strikes(self, mock_ibkr_client, sample_config, sample_market_state):
        """Test strike calculation for iron condor"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        params = {
            'put_short': 4950,
            'call_short': 5050,
            'spread_width': 5
        }
        
        strikes, rights = strategy.calculate_strikes(5000, sample_market_state, params)
        
        assert len(strikes) == 4
        assert len(rights) == 4
        assert rights == ['P', 'P', 'C', 'C']
        
        # Check strikes
        assert strikes[0] == 4950  # Short put
        assert strikes[1] == 4945  # Long put
        assert strikes[2] == 5050  # Short call
        assert strikes[3] == 5055  # Long call
    
    def test_calculate_credit_target(self, mock_ibkr_client, sample_config):
        """Test credit target calculation for iron condor"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        strikes = [4950, 4945, 5050, 5055]
        credit = strategy.calculate_credit_target(strikes, {})
        
        max_width = 5  # Both wings are 5 wide
        expected_credit = max_width * 0.25  # 25% of max width
        assert credit == expected_credit
    
    def test_strike_distance_calculation(self, mock_ibkr_client, sample_config,
                                          sample_market_state, sample_vix_state,
                                          sample_sentiment, sample_first_hour):
        """Test strike distance calculation"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['confidence'] = 80
        
        # Current price 5000
        sample_market_state['current_price'] = 5000
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        # Put strike should be below current price
        assert setup['put_short'] < 5000
        # Call strike should be above current price
        assert setup['call_short'] > 5000
        # Spread width should be 5
        assert setup['spread_width'] == 5
    
    def test_low_range_score_rejection(self, mock_ibkr_client, sample_config,
                                        sample_market_state, sample_vix_state,
                                        sample_sentiment, sample_first_hour):
        """Test rejection when range score too low"""
        strategy = IronCondorStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'LOW'
        sample_first_hour['market_type'] = 'range_bound'
        sample_first_hour['range_score'] = 40  # Too low
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None