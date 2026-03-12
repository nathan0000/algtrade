# tests/test_strategies/test_put_spread.py
import pytest
from strategies.put_spread import PutCreditSpreadStrategy

class TestPutCreditSpreadStrategy:
    """Test suite for Put Credit Spread Strategy"""
    
    def test_initialization(self, mock_ibkr_client, sample_config):
        """Test strategy initialization"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        assert strategy.name == "PutCreditSpread"
    
    def test_should_enter_optimal_conditions(self, mock_ibkr_client, sample_config,
                                             sample_market_state, sample_vix_state,
                                             sample_sentiment, sample_first_hour):
        """Test should_enter with optimal conditions"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Modify conditions for bullish bias
        sample_vix_state['regime'] = 'NORMAL'
        sample_vix_state['trend'] = 'FALLING'
        sample_first_hour['market_type'] = 'trending_bullish'
        sample_market_state['current_price'] = 5010  # Above VWAP
        sample_market_state['vwap'] = 5005
        sample_sentiment['block_put_spreads'] = False
        sample_sentiment['prefer_calls'] = True
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is not None
        assert setup['strategy'] == 'put_credit_spread'
        assert setup['direction'] == 'bullish'
        assert setup['confidence'] >= 65
    
    def test_should_enter_blocked_by_sentiment(self, mock_ibkr_client, sample_config,
                                                sample_market_state, sample_vix_state,
                                                sample_sentiment, sample_first_hour):
        """Test should_enter when blocked by sentiment"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_sentiment['block_put_spreads'] = True
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_high_vix(self, mock_ibkr_client, sample_config,
                                    sample_market_state, sample_vix_state,
                                    sample_sentiment, sample_first_hour):
        """Test should_enter with high VIX"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_vix_state['regime'] = 'HIGH'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_bearish_market(self, mock_ibkr_client, sample_config,
                                          sample_market_state, sample_vix_state,
                                          sample_sentiment, sample_first_hour):
        """Test should_enter with bearish market"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_first_hour['market_type'] = 'trending_bearish'
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_should_enter_below_vwap(self, mock_ibkr_client, sample_config,
                                      sample_market_state, sample_vix_state,
                                      sample_sentiment, sample_first_hour):
        """Test should_enter when price below VWAP"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        sample_market_state['current_price'] = 4990
        sample_market_state['vwap'] = 5005
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None
    
    def test_calculate_strikes(self, mock_ibkr_client, sample_config, sample_market_state):
        """Test strike calculation"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        params = {
            'delta_target': 0.20,
            'spread_width': 5
        }
        
        strikes, rights = strategy.calculate_strikes(5000, sample_market_state, params)
        
        assert len(strikes) == 2
        assert len(rights) == 2
        assert rights[0] == 'P'
        assert rights[1] == 'P'
        assert strikes[0] > strikes[1]  # Short strike higher than long
        assert abs(strikes[0] - strikes[1]) == 5  # 5-point spread
    
    def test_calculate_credit_target(self, mock_ibkr_client, sample_config):
        """Test credit target calculation"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        strikes = [5000, 4995]
        credit = strategy.calculate_credit_target(strikes, {})
        
        assert credit == 1.50  # 5 * 0.30
    
    def test_low_confidence_rejection(self, mock_ibkr_client, sample_config,
                                       sample_market_state, sample_vix_state,
                                       sample_sentiment, sample_first_hour):
        """Test rejection when confidence too low"""
        strategy = PutCreditSpreadStrategy(mock_ibkr_client, sample_config.strategy)
        
        # Create poor conditions to lower confidence
        sample_vix_state['regime'] = 'NORMAL'
        sample_first_hour['market_type'] = 'unclear'
        sample_market_state['distance_from_vwap'] = 0.3  # Far from VWAP
        
        setup = strategy.should_enter(
            sample_market_state, sample_vix_state,
            sample_sentiment, sample_first_hour
        )
        
        assert setup is None