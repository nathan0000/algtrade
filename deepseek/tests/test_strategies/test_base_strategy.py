# tests/test_strategies/test_base_strategy.py
import pytest
from datetime import datetime, time
from unittest.mock import Mock, patch
import pytz

from strategies.base_strategy import BaseStrategy

class TestBaseStrategy:
    """Test suite for Base Strategy"""

    class ConcreteStrategy(BaseStrategy):
        """Concrete implementation for testing"""
        def should_enter(self, market_state, vix_state, sentiment, first_hour):
            return {'test': 'setup'}

        def calculate_strikes(self, current_price, market_state, params):
            return [5000, 4995], ['P', 'P']

        def calculate_credit_target(self, strikes, market_state):
            return 1.50

    def test_initialization(self, mock_ibkr_client, sample_config):
        """Test strategy initialization"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        assert strategy.name == "TestStrategy"
        assert strategy.ibkr == mock_ibkr_client
        assert len(strategy.active_positions) == 0
        assert len(strategy.daily_trades) == 0

    def test_validate_entry_valid_time(self, mock_ibkr_client, sample_config, ny_tz):
        """Test entry validation with valid time"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        # Mock current time (10:30 AM, within allowed window)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(10, 30)
            mock_datetime.now.return_value = mock_now

            result = strategy.validate_entry({})

            assert result == True

    def test_validate_entry_too_early(self, mock_ibkr_client, sample_config, ny_tz):
        """Test entry validation - too early"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        # Mock current time (9:15 AM)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(9, 15)
            mock_datetime.now.return_value = mock_now

            result = strategy.validate_entry({})

            assert result == False

    def test_validate_entry_too_late(self, mock_ibkr_client, sample_config, ny_tz):
        """Test entry validation - too late"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        # Mock current time (3:50 PM, after entry_cutoff 3:45)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(15, 50)
            mock_datetime.now.return_value = mock_now

            result = strategy.validate_entry({})

            assert result == False

    def test_calculate_position_size(self, mock_ibkr_client):
        """Test position size calculation"""
        from config import StrategyConfig, RiskConfig
        strategy_config = StrategyConfig()
        risk_config = RiskConfig(max_risk_per_trade_pct=0.2)  # 0.2% risk per trade
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            strategy_config,
            risk_config
        )

        mock_ibkr_client.account_value = 100000
        # Risk amount $500, should allow 1 contract
        size = strategy.calculate_position_size(500)
        assert size == 1

        # Risk amount $2500, still capped at 1 contract
        size = strategy.calculate_position_size(2500)
        assert size == 1

    def test_get_exit_signal_profit_target(self, mock_ibkr_client, sample_config):
        """Test exit signal - profit target"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        position = {
            'credit': 2.00,
            'current_value': 0.30  # 85% profit
        }

        # Mock current time (within trading hours)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(14, 30)
            mock_datetime.now.return_value = mock_now

            signal = strategy.get_exit_signal(position, 5000)
            assert signal == "profit_target"

    def test_get_exit_signal_stop_loss(self, mock_ibkr_client, sample_config):
        """Test exit signal - stop loss"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        position = {
            'credit': 2.00,
            'current_value': 4.50  # > 2x credit
        }

        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(14, 30)
            mock_datetime.now.return_value = mock_now

            signal = strategy.get_exit_signal(position, 5000)
            assert signal == "stop_loss"

    def test_get_exit_signal_market_close(self, mock_ibkr_client, sample_config, ny_tz):
        """Test exit signal - market close"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        position = {
            'credit': 2.00,
            'current_value': 1.00
        }

        # Mock current time near close (3:55 PM)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(16, 5)
            mock_datetime.now.return_value = mock_now

            signal = strategy.get_exit_signal(position, 5000)
            assert signal == "market_close"

    def test_get_exit_signal_no_signal(self, mock_ibkr_client, sample_config, ny_tz):
        """Test exit signal - no signal"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        position = {
            'credit': 2.00,
            'current_value': 1.00  # 50% profit, not at target
        }

        # Mock current time (2:30 PM)
        with patch('strategies.base_strategy.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(14, 30)
            mock_datetime.now.return_value = mock_now

            signal = strategy.get_exit_signal(position, 5000)
            assert signal is None

    def test_on_trade_exit(self, mock_ibkr_client, sample_config):
        """Test trade exit handling"""
        strategy = self.ConcreteStrategy(
            "TestStrategy",
            mock_ibkr_client,
            sample_config.strategy,
            sample_config.risk
        )

        # Add an active position
        trade_data = {'order_id': 1001, 'credit': 2.00}
        strategy.active_positions.append(trade_data)

        # Exit the trade
        strategy.on_trade_exit(trade_data)

        assert len(strategy.active_positions) == 0
        assert len(strategy.daily_trades) == 1
        assert strategy.daily_trades[0] == trade_data