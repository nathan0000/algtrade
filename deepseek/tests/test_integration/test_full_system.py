# tests/test_integration/test_full_system.py
import pytest
from unittest.mock import Mock, patch, MagicMock
import time
from datetime import datetime, time as dt_time
import pytz

from main import SPX0DTEAutoTrader
from config import AppConfig

class TestFullSystemIntegration:
    """Integration tests for the complete system"""
    
    @pytest.fixture
    def mock_all_components(self):
        """Mock all components for integration testing"""
        with patch('main.IBKRClient') as MockIBKR, \
             patch('main.ThreadManager') as MockThreadManager, \
             patch('main.MarketDataCollector') as MockMarketData, \
             patch('main.VIXAnalyzer') as MockVIX, \
             patch('main.FirstHourAnalyzer') as MockFirstHour, \
             patch('main.SentimentAnalyzer') as MockSentiment, \
             patch('main.RiskManager') as MockRisk, \
             patch('main.OrderManager') as MockOrder, \
             patch('main.PutCreditSpreadStrategy') as MockPutSpread, \
             patch('main.CallCreditSpreadStrategy') as MockCallSpread, \
             patch('main.IronFlyStrategy') as MockIronFly, \
             patch('main.IronCondorStrategy') as MockIronCondor:
            
            # Configure mocks
            mock_ibkr = MockIBKR.return_value
            mock_ibkr.connect_and_run.return_value = True
            mock_ibkr.account_value = 100000
            
            mock_market = MockMarketData.return_value
            mock_market.get_current_state.return_value = {
                'current_price': 5000,
                'vwap': 5005,
                'atr': 15
            }
            
            mock_vix = MockVIX.return_value
            mock_vix.get_regime.return_value = {
                'regime': 'NORMAL',
                'should_trade': True
            }
            mock_vix.should_trade.return_value = True
            
            mock_first_hour = MockFirstHour.return_value
            mock_first_hour.analyze.return_value = {
                'market_type': 'range_bound',
                'confidence': 75
            }
            
            mock_sentiment = MockSentiment.return_value
            mock_sentiment.get_sentiment_signals.return_value = {
                'block_put_spreads': False,
                'block_call_spreads': False
            }
            
            mock_risk = MockRisk.return_value
            mock_risk.validate_new_trade.return_value = True
            
            mock_order = MockOrder.return_value
            mock_order.place_credit_spread.return_value = 1001
            
            # Configure strategies
            mock_put = MockPutSpread.return_value
            mock_put.name = 'PutCreditSpread'
            mock_put.should_enter.return_value = {
                'strategy': 'put_credit_spread',
                'confidence': 80
            }
            
            mock_call = MockCallSpread.return_value
            mock_call.name = 'CallCreditSpread'
            mock_call.should_enter.return_value = None
            
            mock_fly = MockIronFly.return_value
            mock_fly.name = 'IronFly'
            mock_fly.should_enter.return_value = None
            
            mock_condor = MockIronCondor.return_value
            mock_condor.name = 'IronCondor'
            mock_condor.should_enter.return_value = None
            
            yield {
                'ibkr': mock_ibkr,
                'thread_manager': mock_thread_manager if 'mock_thread_manager' in locals() else Mock(),
                'market_data': mock_market,
                'vix': mock_vix,
                'first_hour': mock_first_hour,
                'sentiment': mock_sentiment,
                'risk': mock_risk,
                'order': mock_order,
                'strategies': [mock_put, mock_call, mock_fly, mock_condor]
            }
    
    def test_system_initialization(self, sample_config):
        """Test system initialization"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        assert trader.config == sample_config
        assert trader.is_running == False
        assert trader.trading_paused == False
    
    def test_connect_success(self, sample_config, mock_all_components):
        """Test successful connection"""
        trader = SPX0DTEAutoTrader(sample_config)
        trader.ibkr = mock_all_components['ibkr']
        trader.thread_manager = mock_all_components['thread_manager']
        
        result = trader.connect()
        
        assert result == True
        trader.ibkr.connect_and_run.assert_called_once()
        trader.thread_manager.start_all.assert_called_once()
    
    def test_connect_failure(self, sample_config, mock_all_components):
        """Test connection failure"""
        trader = SPX0DTEAutoTrader(sample_config)
        trader.ibkr = mock_all_components['ibkr']
        trader.ibkr.connect_and_run.return_value = False
        
        result = trader.connect()
        
        assert result == False
    
    def test_execute_trading_cycle(self, sample_config, mock_all_components, ny_tz):
        """Test executing a trading cycle"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.ibkr = mock_all_components['ibkr']
        trader.market_data = mock_all_components['market_data']
        trader.vix = mock_all_components['vix']
        trader.first_hour = mock_all_components['first_hour']
        trader.sentiment = mock_all_components['sentiment']
        trader.risk_manager = mock_all_components['risk']
        trader.order_manager = mock_all_components['order']
        trader.strategies = mock_all_components['strategies']
        
        # Mock current time
        with patch('main.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = dt_time(10, 30)  # 10:30 AM
            mock_datetime.now.return_value = mock_now
            mock_datetime.time = dt_time
            
            trader.execute_trading_cycle()
            
            # Verify data collection
            trader.market_data.get_current_state.assert_called()
            trader.vix.get_regime.assert_called()
            trader.vix.should_trade.assert_called()
            trader.first_hour.analyze.assert_called()
            trader.sentiment.get_sentiment_signals.assert_called()
            
            # Verify strategy evaluation
            for strategy in trader.strategies:
                strategy.should_enter.assert_called()
    
    def test_execute_strategy_trade(self, sample_config, mock_all_components):
        """Test executing a strategy trade"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.ibkr = mock_all_components['ibkr']
        trader.risk_manager = mock_all_components['risk']
        trader.order_manager = mock_all_components['order']
        
        # Mock strategy
        mock_strategy = Mock()
        mock_strategy.name = 'PutCreditSpread'
        mock_strategy.calculate_strikes.return_value = ([5000, 4995], ['P', 'P'])
        mock_strategy.calculate_credit_target.return_value = 1.50
        
        trader.strategies = [mock_strategy]
        
        setup = {
            'strategy': 'PutCreditSpread',
            'confidence': 80
        }
        
        market_state = {
            'current_price': 5000
        }
        
        trader.execute_strategy_trade(setup, market_state)
        
        # Verify trade execution
        mock_strategy.calculate_strikes.assert_called_once()
        mock_strategy.calculate_credit_target.assert_called_once()
        trader.risk_manager.validate_new_trade.assert_called_once()
        trader.order_manager.place_credit_spread.assert_called_once()
    
    def test_manage_positions(self, sample_config, mock_all_components):
        """Test position management"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.risk_manager = mock_all_components['risk']
        trader.order_manager = mock_all_components['order']
        
        # Add an active position
        mock_position = {
            'credit_target': 2.00,
            'current_value': 0.30
        }
        trader.order_manager.active_positions = {1001: mock_position}
        
        # Mock get_option_price
        trader.get_option_price = Mock(return_value=0.30)
        
        # Mock risk manager exit signal
        trader.risk_manager.update_position.return_value = "profit_target"
        
        # Mock close_position
        trader.close_position = Mock()
        
        trader.manage_positions()
        
        # Verify position management
        trader.get_option_price.assert_called_once_with(1001)
        trader.risk_manager.update_position.assert_called_once_with(1001, 0.30)
        trader.close_position.assert_called_once_with(1001, "profit_target", 0.30)
    
    def test_shutdown(self, sample_config, mock_all_components):
        """Test graceful shutdown"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.ibkr = mock_all_components['ibkr']
        trader.thread_manager = mock_all_components['thread_manager']
        trader.order_manager = mock_all_components['order']
        trader.risk_manager = mock_all_components['risk']
        
        # Add active positions and orders
        trader.order_manager.active_positions = {1001: {}}
        trader.order_manager.open_orders = {1002: {}}
        
        # Mock close_position
        trader.close_position = Mock()
        
        trader.shutdown()
        
        # Verify shutdown sequence
        assert trader.is_running == False
        trader.close_position.assert_called_once_with(1001, "system_shutdown", 0)
        trader.order_manager.cancel_order.assert_called_once_with(1002)
        trader.thread_manager.stop_all.assert_called_once()
        trader.ibkr.disconnect_safe.assert_called_once()
    
    def test_main_loop_execution(self, sample_config, mock_all_components, ny_tz):
        """Test main loop execution"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.ibkr = mock_all_components['ibkr']
        trader.thread_manager = mock_all_components['thread_manager']
        trader.market_data = mock_all_components['market_data']
        trader.vix = mock_all_components['vix']
        trader.first_hour = mock_all_components['first_hour']
        trader.sentiment = mock_all_components['sentiment']
        trader.risk_manager = mock_all_components['risk']
        trader.order_manager = mock_all_components['order']
        trader.strategies = mock_all_components['strategies']
        
        trader.is_running = True
        
        # Mock time to be within market hours for first iteration, then exit
        with patch('main.datetime') as mock_datetime, \
             patch('time.sleep', return_value=None) as mock_sleep:
            
            # First iteration: within market hours
            mock_now1 = Mock()
            mock_now1.time.return_value = dt_time(10, 30)
            
            # Second iteration: after market close (to break loop)
            mock_now2 = Mock()
            mock_now2.time.return_value = dt_time(16, 30)
            
            mock_datetime.now.side_effect = [mock_now1, mock_now2]
            mock_datetime.time = dt_time
            
            # Mock execute_trading_cycle and manage_positions
            trader.execute_trading_cycle = Mock()
            trader.manage_positions = Mock()
            trader.prepare_for_next_day = Mock()
            
            # Run main loop (will execute 2 iterations)
            trader.main_loop()
            
            # Verify first iteration executed
            trader.execute_trading_cycle.assert_called_once()
            trader.manage_positions.assert_called_once()
            
            # Verify second iteration triggered prepare_for_next_day
            trader.prepare_for_next_day.assert_called_once()
    
    def test_prepare_for_next_day(self, sample_config, mock_all_components):
        """Test preparation for next trading day"""
        trader = SPX0DTEAutoTrader(sample_config)
        
        # Assign mocks
        trader.first_hour = mock_all_components['first_hour']
        trader.market_data = mock_all_components['market_data']
        trader.order_manager = mock_all_components['order']
        
        # Add some data
        trader.first_hour.first_hour_data = [1, 2, 3]
        trader.market_data.daily_high = 5020
        trader.market_data.daily_low = 4980
        trader.market_data.daily_open = 5000
        trader.market_data.daily_volume = 1000000
        
        # Add an active position (should generate warning)
        trader.order_manager.active_positions = {1001: {}}
        
        with patch('logging.Logger.warning') as mock_warning:
            trader.prepare_for_next_day()
            
            # Verify reset
            assert len(trader.first_hour.first_hour_data) == 0
            assert trader.market_data.daily_high == 0
            assert trader.market_data.daily_low == float('inf')
            assert trader.market_data.daily_open == 0
            assert trader.market_data.daily_volume == 0
            
            # Verify warning for open positions
            mock_warning.assert_called_once()