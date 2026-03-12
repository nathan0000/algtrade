# tests/test_connection/test_ibkr_client.py
import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from connection.ibkr_client import IBKRClient

class TestIBKRClient:
    """Test suite for IBKR Native Client"""
    
    def test_initialization(self, sample_config):
        """Test client initialization"""
        client = IBKRClient(sample_config.ibkr)
        assert client.connected == False
        assert client.next_order_id == None
        assert client.accounts == []
        assert client.account_value == 0.0
        assert client.req_id_counter >= 1000
    
    def test_error_handling_non_critical(self, sample_config, caplog):
        """Test handling of non-critical errors"""
        client = IBKRClient(sample_config.ibkr)
        
        # Non-critical error (2104 - Market data farm connection is OK)
        client.error(1, 2104, "Market data farm connection is OK")
        
        # Should log at DEBUG level
        assert "Market data farm connection is OK" in caplog.text
    
    def test_error_handling_critical(self, sample_config, caplog):
        """Test handling of critical errors"""
        client = IBKRClient(sample_config.ibkr)
        
        # Mock the handle_critical_error method
        client.handle_critical_error = Mock()
        
        # Critical error (502 - Cannot connect to TWS)
        client.error(1, 502, "Cannot connect to TWS")
        
        # Should log at ERROR level
        assert "Cannot connect to TWS" in caplog.text
        client.handle_critical_error.assert_called_once_with(502)
    
    def test_next_valid_id(self, sample_config):
        """Test receiving next valid order ID"""
        client = IBKRClient(sample_config.ibkr)
        
        client.nextValidId(1001)
        
        assert client.next_order_id == 1001
        assert client.connected == True
    
    def test_account_summary(self, sample_config):
        """Test receiving account summary"""
        client = IBKRClient(sample_config.ibkr)
        
        client.accountSummary(9001, 'DU123456', 'NetLiquidation', '100000.00', 'USD')
        
        assert client.account_value == 100000.0
    
    def test_managed_accounts(self, sample_config):
        """Test receiving managed accounts"""
        client = IBKRClient(sample_config.ibkr)
        
        client.managedAccounts('DU123456,DU789012')
        
        assert 'DU123456' in client.accounts
        assert 'DU789012' in client.accounts
        assert len(client.accounts) == 2
    
    def test_contract_details(self, sample_config):
        """Test receiving contract details"""
        client = IBKRClient(sample_config.ibkr)
        
        # Mock contract details
        mock_details = Mock()
        mock_details.contract.symbol = 'SPX'
        
        client.contractDetails(1001, mock_details)
        client.contractDetails(1001, mock_details)
        
        assert 1001 in client.option_chains
        assert len(client.option_chains[1001]) == 2
    
    def test_historical_data(self, sample_config):
        """Test receiving historical data"""
        client = IBKRClient(sample_config.ibkr)
        
        # Mock bar data
        mock_bar = Mock()
        mock_bar.date = '20250101 12:00:00'
        mock_bar.open = 5000.0
        mock_bar.high = 5010.0
        mock_bar.low = 4990.0
        mock_bar.close = 5005.0
        mock_bar.volume = 1000000
        
        client.historicalData(2001, mock_bar)
        client.historicalData(2001, mock_bar)
        
        assert 2001 in client.historical_data
        assert len(client.historical_data[2001]) == 2
        assert client.historical_data[2001][0]['open'] == 5000.0
    
    def test_historical_data_end(self, sample_config):
        """Test historical data completion"""
        client = IBKRClient(sample_config.ibkr)
        
        # Add some data first
        mock_bar = Mock()
        mock_bar.date = '20250101 12:00:00'
        mock_bar.open = 5000.0
        mock_bar.high = 5010.0
        mock_bar.low = 4990.0
        mock_bar.close = 5005.0
        mock_bar.volume = 1000000
        
        client.historicalData(2001, mock_bar)
        
        # End historical data
        client.historicalDataEnd(2001, '20250101', '20250102')
        
        # Data should be in queue and removed from storage
        assert client.data_queue.qsize() > 0
        assert 2001 not in client.historical_data
    
    def test_tick_price(self, sample_config):
        """Test receiving tick price"""
        client = IBKRClient(sample_config.ibkr)
        
        client.tickPrice(3001, 4, 5005.50, None)  # 4 = Last price
        
        assert 3001 in client.realtime_prices
        assert client.realtime_prices[3001][4] == 5005.50
    
    def test_order_status(self, sample_config):
        """Test receiving order status"""
        client = IBKRClient(sample_config.ibkr)
        
        client.orderStatus(1001, 'Filled', 1, 0, 5.50, 12345, 0, 5.50, 1, '', None)
        
        assert client.order_queue.qsize() > 0
        status_data = client.order_queue.get()
        assert status_data['type'] == 'order_status'
        assert status_data['orderId'] == 1001
        assert status_data['status'] == 'Filled'
    
    def test_reconnect(self, sample_config):
        """Test reconnection logic"""
        client = IBKRClient(sample_config.ibkr)
        
        # Mock methods
        client.disconnect = Mock()
        client.connect_and_run = Mock(return_value=True)
        
        client.reconnect()
        
        client.disconnect.assert_called_once()
        client.connect_and_run.assert_called_once()
    
    @patch('connection.ibkr_client.threading.Thread')
    def test_connect_and_run(self, mock_thread, sample_config):
        """Test connect and run method"""
        client = IBKRClient(sample_config.ibkr)
        client.connect = Mock()
        client.run = Mock()
        
        # Mock thread
        mock_thread_instance = Mock()
        mock_thread.return_value = mock_thread_instance
        
        result = client.connect_and_run()
        
        assert result == True
        client.connect.assert_called_once_with(
            sample_config.ibkr.host, 
            sample_config.ibkr.port, 
            clientId=sample_config.ibkr.client_id
        )
        mock_thread.assert_called_once()
        mock_thread_instance.start.assert_called_once()