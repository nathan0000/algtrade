# tests/test_connection/test_thread_manager.py
import pytest
import time
from unittest.mock import Mock, patch
from connection.thread_manager import ThreadManager

class TestThreadManager:
    """Test suite for Thread Manager"""
    
    def test_initialization(self, mock_ibkr_client):
        """Test thread manager initialization"""
        tm = ThreadManager(mock_ibkr_client)
        
        assert tm.ibkr == mock_ibkr_client
        assert tm.running == False
        assert len(tm.threads) == 0
    
    def test_start_all(self, mock_ibkr_client):
        """Test starting all threads"""
        tm = ThreadManager(mock_ibkr_client)
        
        # Mock thread creation
        with patch('threading.Thread') as mock_thread:
            mock_thread_instance = Mock()
            mock_thread.return_value = mock_thread_instance
            
            tm.start_all()
            
            assert tm.running == True
            # Should create 4 threads (MarketData, OrderMonitor, PositionMonitor, Heartbeat)
            assert mock_thread.call_count == 4
            assert mock_thread_instance.start.call_count == 4
    
    def test_stop_all(self, mock_ibkr_client):
        """Test stopping all threads"""
        tm = ThreadManager(mock_ibkr_client)
        
        # Add mock threads
        mock_thread1 = Mock()
        mock_thread1.is_alive.return_value = True
        mock_thread2 = Mock()
        mock_thread2.is_alive.return_value = False
        
        tm.threads = [mock_thread1, mock_thread2]
        tm.running = True
        
        tm.stop_all()
        
        assert tm.running == False
        mock_thread1.join.assert_called_once_with(timeout=2)
        mock_thread2.join.assert_not_called()
    
    def test_market_data_loop(self, mock_ibkr_client):
        """Test market data loop"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        
        # Mock time to be within market hours
        with patch('connection.thread_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(10, 0)  # 10:00 AM
            mock_datetime.now.return_value = mock_now
            
            # Run one iteration
            tm.market_data_loop()
            
            # Should request market data
            assert mock_ibkr_client.reqMktData.call_count >= 2  # SPX and VIX
    
    def test_market_data_loop_outside_hours(self, mock_ibkr_client):
        """Test market data loop outside market hours"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        
        # Mock time to be outside market hours
        with patch('connection.thread_manager.datetime') as mock_datetime:
            mock_now = Mock()
            mock_now.time.return_value = time(20, 0)  # 8:00 PM
            mock_datetime.now.return_value = mock_now
            
            # Run one iteration
            tm.market_data_loop()
            
            # Should NOT request market data
            mock_ibkr_client.reqMktData.assert_not_called()
    
    def test_order_monitor_loop(self, mock_ibkr_client):
        """Test order monitor loop"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        
        # Run one iteration (with timeout to prevent infinite loop)
        with patch('time.sleep', return_value=None):
            tm.order_monitor_loop()
            
            # Should request open orders
            mock_ibkr_client.reqOpenOrders.assert_called_once()
    
    def test_position_monitor_loop(self, mock_ibkr_client):
        """Test position monitor loop"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        
        # Run one iteration
        with patch('time.sleep', return_value=None):
            tm.position_monitor_loop()
            
            # Should request account updates
            mock_ibkr_client.reqAccountUpdates.assert_called_once_with(True, "")
    
    def test_heartbeat_loop_connected(self, mock_ibkr_client):
        """Test heartbeat loop when connected"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        mock_ibkr_client.connected = True
        
        with patch('time.sleep', return_value=None):
            tm.heartbeat_loop()
            
            # Should NOT attempt reconnect
            mock_ibkr_client.reconnect.assert_not_called()
    
    def test_heartbeat_loop_disconnected(self, mock_ibkr_client):
        """Test heartbeat loop when disconnected"""
        tm = ThreadManager(mock_ibkr_client)
        tm.running = True
        mock_ibkr_client.connected = False
        
        with patch('time.sleep', return_value=None):
            tm.heartbeat_loop()
            
            # Should attempt reconnect
            mock_ibkr_client.reconnect.assert_called_once()
    
    def test_create_spx_contract(self, mock_ibkr_client):
        """Test SPX contract creation"""
        tm = ThreadManager(mock_ibkr_client)
        
        contract = tm.create_spx_contract()
        
        assert contract.symbol == 'SPX'
        assert contract.secType == 'IND'
        assert contract.exchange == 'CBOE'
        assert contract.currency == 'USD'
    
    def test_create_vix_contract(self, mock_ibkr_client):
        """Test VIX contract creation"""
        tm = ThreadManager(mock_ibkr_client)
        
        with patch('connection.thread_manager.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = '202503'
            
            contract = tm.create_vix_contract()
            
            assert contract.symbol == 'VIX'
            assert contract.secType == 'FUT'
            assert contract.exchange == 'CFE'
            assert contract.currency == 'USD'
            assert contract.lastTradeDateOrContractMonth == '202503'