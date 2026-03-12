# tests/conftest.py (updated with proper path handling)
import pytest
import sys
import os
from datetime import datetime, time, timedelta
import pytz
from unittest.mock import Mock, MagicMock, patch
import logging

# Add project root to path - IMPORTANT for tests
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Now we can import from the project
from config import AppConfig, IBKRConfig, RiskConfig, StrategyConfig

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)

# ... rest of the fixtures remain the same

# ==================== Fixtures ====================

@pytest.fixture
def ny_tz():
    """New York timezone fixture"""
    return pytz.timezone('America/New_York')

@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    config = AppConfig()
    config.ibkr.host = '127.0.0.1'
    config.ibkr.port = 4002
    config.ibkr.client_id = 9999  # Use different ID for tests
    config.paper_trading = True
    return config

@pytest.fixture
def mock_ibkr_client():
    """Mock IBKR client for testing"""
    mock = MagicMock()
    mock.connected = True
    mock.next_order_id = 1000
    mock.account_value = 100000.0
    mock.accounts = ['DU123456']
    
    # Mock methods
    mock.connect_and_run.return_value = True
    mock.disconnect_safe.return_value = None
    mock.reqHistoricalData.return_value = 1001
    mock.placeOrder.return_value = None
    
    return mock

@pytest.fixture
def sample_market_state():
    """Sample market state for testing"""
    return {
        'current_price': 5000.00,
        'daily_open': 4980.00,
        'daily_high': 5020.00,
        'daily_low': 4980.00,
        'vwap': 5005.00,
        'atr': 15.5,
        'distance_from_vwap': -0.1,  # -0.1% from VWAP
        'range_width': 40.0,
        'range_position': 0.5  # Middle of range
    }

@pytest.fixture
def sample_vix_state():
    """Sample VIX state for testing"""
    return {
        'level': 16.5,
        'regime': 'NORMAL',
        'trend': 'FALLING',
        'ma20': 17.0,
        'ma50': 17.5,
        'percentile': 45.0,
        'implication': 'Balanced conditions',
        'preferred_strategies': ['All strategies viable'],
        'block_long': False,
        'block_short': False
    }

@pytest.fixture
def sample_sentiment():
    """Sample sentiment signals for testing"""
    return {
        'block_put_spreads': False,
        'block_call_spreads': False,
        'prefer_puts': False,
        'prefer_calls': True,
        'confidence_multiplier': 1.0,
        'gap_direction': 'NONE',
        'gap_size': 0.0
    }

@pytest.fixture
def sample_first_hour():
    """Sample first hour analysis for testing"""
    return {
        'market_type': 'range_bound',
        'confidence': 75,
        'trend_score': 20,
        'range_score': 70,
        'first_hour_range': (4985.0, 5015.0),
        'broke_pre_high': False,
        'broke_pre_low': False,
        'broke_y_high': False,
        'broke_y_low': False,
        'slope': 0.02,
        'volume_trend': False
    }

@pytest.fixture
def mock_order_manager():
    """Mock order manager for testing"""
    mock = MagicMock()
    mock.place_credit_spread.return_value = 1001
    mock.place_iron_fly.return_value = 1002
    mock.place_iron_condor.return_value = 1003
    mock.cancel_order.return_value = None
    return mock

@pytest.fixture
def mock_risk_manager():
    """Mock risk manager for testing"""
    mock = MagicMock()
    mock.validate_new_trade.return_value = True
    mock.add_position.return_value = None
    mock.update_position.return_value = None
    mock.get_daily_stats.return_value = {
        'total_trades': 5,
        'winning_trades': 3,
        'losing_trades': 2,
        'win_rate': 0.6,
        'total_pnl': 250.0
    }
    return mock