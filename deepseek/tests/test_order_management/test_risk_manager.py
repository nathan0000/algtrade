# tests/test_order_management/test_risk_manager.py
import pytest
from datetime import datetime, date, timedelta
from order_management.risk_manager import RiskManager

class TestRiskManager:
    """Test suite for Risk Manager"""
    
    def test_initialization(self, sample_config, mock_ibkr_client):
        """Test risk manager initialization"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        assert rm.config == sample_config.risk
        assert rm.ibkr == mock_ibkr_client
        assert len(rm.daily_trades) == 0
        assert rm.daily_pnl == 0.0
        assert rm.current_date == date.today()
        assert len(rm.positions) == 0
    
    def test_validate_new_trade_within_limits(self, sample_config, mock_ibkr_client):
        """Test trade validation within limits"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        mock_ibkr_client.account_value = 100000
        
        # Risk amount $1500 (1.5% of $100k) - within 2% limit
        result = rm.validate_new_trade(1500, 'test_strategy')
        
        assert result == True
    
    def test_validate_new_trade_exceeds_per_trade_limit(self, sample_config, mock_ibkr_client):
        """Test trade validation exceeding per-trade limit"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        mock_ibkr_client.account_value = 100000
        
        # Risk amount $2500 (2.5% of $100k) - exceeds 2% limit
        result = rm.validate_new_trade(2500, 'test_strategy')
        
        assert result == False
    
    def test_validate_new_trade_exceeds_daily_limit(self, sample_config, mock_ibkr_client):
        """Test trade validation exceeding daily limit"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        mock_ibkr_client.account_value = 100000
        
        # Add existing trades with total risk $5000 (5% of $100k)
        rm.daily_trades = [{'risk': 2500}, {'risk': 2500}]
        
        # New trade risk $1500 would make total $6500 (6.5%) - exceeds 6% limit
        result = rm.validate_new_trade(1500, 'test_strategy')
        
        assert result == False
    
    def test_validate_new_trade_exceeds_position_limit(self, sample_config, mock_ibkr_client):
        """Test trade validation exceeding position limit"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        mock_ibkr_client.account_value = 100000
        
        # Add max positions
        rm.positions = {i: {} for i in range(sample_config.risk.max_positions)}
        
        result = rm.validate_new_trade(1000, 'test_strategy')
        
        assert result == False
    
    def test_add_position(self, sample_config, mock_ibkr_client):
        """Test adding a position"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position_data = {
            'order_id': 1001,
            'type': 'credit_spread',
            'credit_target': 1.50
        }
        
        rm.add_position(1001, position_data)
        
        assert 1001 in rm.positions
        assert rm.positions[1001] == position_data
    
    def test_check_exit_conditions_profit_target(self, sample_config, mock_ibkr_client):
        """Test exit condition - profit target"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position = {
            'credit_target': 2.00,
            'current_value': 0.30  # 85% profit
        }
        
        rm.positions[1001] = position
        
        # Mock time to be within trading hours
        with patch('order_management.risk_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value.hour = 14
            mock_now.time.return_value.minute = 30
            mock_datetime.now.return_value = mock_now
            
            exit_reason = rm.check_exit_conditions(1001)
            
            assert exit_reason == "profit_target"
    
    def test_check_exit_conditions_stop_loss(self, sample_config, mock_ibkr_client):
        """Test exit condition - stop loss"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position = {
            'credit_target': 2.00,
            'current_value': 4.50  # > 2x credit
        }
        
        rm.positions[1001] = position
        
        # Mock time to be within trading hours
        with patch('order_management.risk_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value.hour = 14
            mock_now.time.return_value.minute = 30
            mock_datetime.now.return_value = mock_now
            
            exit_reason = rm.check_exit_conditions(1001)
            
            assert exit_reason == "stop_loss"
    
    def test_check_exit_conditions_market_close(self, sample_config, mock_ibkr_client):
        """Test exit condition - market close"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position = {
            'credit_target': 2.00,
            'current_value': 1.00
        }
        
        rm.positions[1001] = position
        
        # Mock time near close
        with patch('order_management.risk_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value.hour = 15
            mock_now.time.return_value.minute = 56
            mock_datetime.now.return_value = mock_now
            
            exit_reason = rm.check_exit_conditions(1001)
            
            assert exit_reason == "market_close"
    
    def test_check_exit_conditions_no_signal(self, sample_config, mock_ibkr_client):
        """Test exit condition - no signal"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position = {
            'credit_target': 2.00,
            'current_value': 1.00
        }
        
        rm.positions[1001] = position
        
        # Mock time in middle of day
        with patch('order_management.risk_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value.hour = 13
            mock_now.time.return_value.minute = 30
            mock_datetime.now.return_value = mock_now
            
            exit_reason = rm.check_exit_conditions(1001)
            
            assert exit_reason is None
    
    def test_close_position(self, sample_config, mock_ibkr_client):
        """Test closing a position"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        position = {
            'order_id': 1001,
            'type': 'credit_spread',
            'credit_target': 2.00,
            'entry_time': datetime.now()
        }
        
        rm.positions[1001] = position
        
        rm.close_position(1001, "profit_target", 0.40)
        
        assert 1001 not in rm.positions
        assert len(rm.daily_trades) == 1
        assert rm.daily_trades[0]['order_id'] == 1001
        assert rm.daily_trades[0]['exit_reason'] == "profit_target"
        assert rm.daily_trades[0]['pnl'] == (2.00 - 0.40) * 100  # $160 profit
    
    def test_get_daily_stats(self, sample_config, mock_ibkr_client):
        """Test getting daily statistics"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        # Add some trades
        rm.daily_trades = [
            {'pnl': 150},
            {'pnl': 200},
            {'pnl': -50},
            {'pnl': -25},
            {'pnl': 75}
        ]
        rm.daily_pnl = sum(t['pnl'] for t in rm.daily_trades)
        
        stats = rm.get_daily_stats()
        
        assert stats['total_trades'] == 5
        assert stats['winning_trades'] == 3
        assert stats['losing_trades'] == 2
        assert stats['win_rate'] == 0.6
        assert stats['total_pnl'] == 350
        assert stats['avg_win'] == 141.67  # (150+200+75)/3
        assert stats['avg_loss'] == -37.5  # (-50-25)/2
    
    def test_new_day_reset(self, sample_config, mock_ibkr_client):
        """Test reset for new day"""
        rm = RiskManager(sample_config.risk, mock_ibkr_client)
        
        # Add some trades from yesterday
        yesterday = date.today() - timedelta(days=1)
        rm.current_date = yesterday
        rm.daily_trades = [{'pnl': 100}]
        rm.daily_pnl = 100
        
        # Trigger new day check
        rm._check_new_day()
        
        assert rm.current_date == date.today()
        assert len(rm.daily_trades) == 0
        assert rm.daily_pnl == 0.0