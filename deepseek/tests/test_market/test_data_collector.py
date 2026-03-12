# tests/test_market/test_data_collector.py
import pytest
from datetime import datetime, timedelta
import pytz
from market.data_collector import MarketDataCollector

class TestMarketDataCollector:
    """Test suite for Market Data Collector"""
    
    def test_initialization(self, mock_ibkr_client):
        """Test data collector initialization"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        assert dc.ibkr == mock_ibkr_client
        assert len(dc.price_history) == 0
        assert dc.daily_high == 0
        assert dc.daily_low == float('inf')
        assert dc.daily_open == 0
        assert dc.vwap == 0
    
    def test_update_tick(self, mock_ibkr_client, ny_tz):
        """Test updating with tick data"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        with patch('market.data_collector.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 3, 8, 10, 30, tzinfo=ny_tz)
            
            dc.update_tick(5000.50, 1000)
            
            assert len(dc.price_history) == 1
            assert dc.daily_high == 5000.50
            assert dc.daily_low == 5000.50
            assert dc.daily_open == 5000.50
            assert dc.daily_volume == 1000
    
    def test_update_tick_multiple(self, mock_ibkr_client, ny_tz):
        """Test multiple tick updates"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        with patch('market.data_collector.datetime') as mock_datetime:
            mock_datetime.now.return_value = datetime(2025, 3, 8, 10, 30, tzinfo=ny_tz)
            
            dc.update_tick(5000.50, 1000)
            dc.update_tick(5010.75, 1500)
            dc.update_tick(4995.25, 800)
            
            assert len(dc.price_history) == 3
            assert dc.daily_high == 5010.75
            assert dc.daily_low == 4995.25
            assert dc.daily_open == 5000.50
            assert dc.daily_volume == 3300
    
    def test_calculate_vwap(self, mock_ibkr_client):
        """Test VWAP calculation"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        # Add some price/volume data
        dc.vwap_data = [
            {'price': 5000, 'volume': 1000, 'vwap': 5000},
            {'price': 5010, 'volume': 1500, 'vwap': 5006},
            {'price': 5005, 'volume': 1200, 'vwap': 5005.5}
        ]
        
        # Calculate VWAP with new tick
        dc.calculate_vwap(5008, 800)
        
        # VWAP should be updated
        assert dc.vwap > 0
        assert len(dc.vwap_data) == 4
    
    def test_calculate_atr(self, mock_ibkr_client):
        """Test ATR calculation"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        # Add price history
        prices = [5000, 5010, 5020, 5015, 5005, 4995, 5000, 5010, 5020, 5030, 
                  5025, 5015, 5005, 4995, 4985, 4990, 5000]
        
        for i, price in enumerate(prices):
            dc.price_history.append({
                'time': datetime.now(),
                'price': price,
                'volume': 1000
            })
        
        atr = dc.calculate_atr(period=14)
        
        assert atr > 0
        assert dc.atr == atr
    
    def test_calculate_atr_insufficient_data(self, mock_ibkr_client):
        """Test ATR with insufficient data"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        # Add only a few prices
        for price in [5000, 5010]:
            dc.price_history.append({
                'time': datetime.now(),
                'price': price,
                'volume': 1000
            })
        
        atr = dc.calculate_atr(period=14)
        
        assert atr == 0  # Should return 0 when insufficient data
    
    def test_get_current_state(self, mock_ibkr_client):
        """Test getting current market state"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        # Setup some data
        dc.price_history.append({'time': datetime.now(), 'price': 5005, 'volume': 1000})
        dc.daily_open = 4980
        dc.daily_high = 5020
        dc.daily_low = 4980
        dc.vwap = 5000
        dc.atr = 15
        
        state = dc.get_current_state()
        
        assert state['current_price'] == 5005
        assert state['daily_open'] == 4980
        assert state['daily_high'] == 5020
        assert state['daily_low'] == 4980
        assert state['vwap'] == 5000
        assert state['atr'] == 15
        assert 'distance_from_vwap' in state
        assert 'range_width' in state
        assert 'range_position' in state
    
    def test_request_historical_data(self, mock_ibkr_client):
        """Test historical data request"""
        dc = MarketDataCollector(mock_ibkr_client)
        mock_ibkr_client.req_id_counter = 1000
        
        req_id = dc.request_historical_data(days=20)
        
        assert req_id == 1000
        mock_ibkr_client.reqHistoricalData.assert_called_once()
    
    def test_create_spx_contract(self, mock_ibkr_client):
        """Test SPX contract creation"""
        dc = MarketDataCollector(mock_ibkr_client)
        
        contract = dc.create_spx_contract()
        
        assert contract.symbol == 'SPX'
        assert contract.secType == 'IND'
        assert contract.exchange == 'CBOE'
        assert contract.currency == 'USD'