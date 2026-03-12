# tests/test_order_management/test_order_manager.py
import pytest
from unittest.mock import Mock, patch
from order_management.order_manager import OrderManager

class TestOrderManager:
    """Test suite for Order Manager"""
    
    def test_initialization(self, mock_ibkr_client, mock_risk_manager):
        """Test order manager initialization"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        assert om.ibkr == mock_ibkr_client
        assert om.risk_manager == mock_risk_manager
        assert len(om.open_orders) == 0
        assert len(om.filled_orders) == 0
        assert len(om.active_positions) == 0
    
    def test_create_spxw_contract(self, mock_ibkr_client, mock_risk_manager):
        """Test SPXW options contract creation"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        contract = om.create_spxw_contract('20250308', 5000, 'C')
        
        assert contract.symbol == 'SPXW'
        assert contract.secType == 'OPT'
        assert contract.exchange == 'CBOE'
        assert contract.currency == 'USD'
        assert contract.lastTradeDateOrContractMonth == '20250308'
        assert contract.strike == 5000
        assert contract.right == 'C'
        assert contract.multiplier == '100'
    
    def test_place_credit_spread(self, mock_ibkr_client, mock_risk_manager):
        """Test placing credit spread order"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        mock_ibkr_client.next_order_id = 1000
        
        order_id = om.place_credit_spread(
            short_strike=5000,
            long_strike=4995,
            right='P',
            expiry='20250308',
            credit_target=1.50,
            quantity=1
        )
        
        assert order_id == 1000
        assert 1000 in om.open_orders
        assert om.open_orders[1000]['type'] == 'credit_spread'
        assert om.open_orders[1000]['strikes'] == (5000, 4995)
        assert om.open_orders[1000]['right'] == 'P'
        assert om.open_orders[1000]['credit_target'] == 1.50
        
        mock_ibkr_client.placeOrder.assert_called_once()
    
    def test_place_iron_fly(self, mock_ibkr_client, mock_risk_manager):
        """Test placing iron fly order"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        mock_ibkr_client.next_order_id = 1001
        
        order_id = om.place_iron_fly(
            central_strike=5000,
            wing_width=30,
            expiry='20250308',
            credit_target=7.50,
            quantity=1
        )
        
        assert order_id == 1001
        assert 1001 in om.open_orders
        assert om.open_orders[1001]['type'] == 'iron_fly'
        assert om.open_orders[1001]['central_strike'] == 5000
        assert om.open_orders[1001]['wing_width'] == 30
        assert om.open_orders[1001]['credit_target'] == 7.50
    
    def test_place_iron_condor(self, mock_ibkr_client, mock_risk_manager):
        """Test placing iron condor order"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        mock_ibkr_client.next_order_id = 1002
        
        order_id = om.place_iron_condor(
            put_short=4950,
            put_long=4945,
            call_short=5050,
            call_long=5055,
            expiry='20250308',
            credit_target=2.50,
            quantity=1
        )
        
        assert order_id == 1002
        assert 1002 in om.open_orders
        assert om.open_orders[1002]['type'] == 'iron_condor'
        assert om.open_orders[1002]['put_strikes'] == (4950, 4945)
        assert om.open_orders[1002]['call_strikes'] == (5050, 5055)
        assert om.open_orders[1002]['credit_target'] == 2.50
    
    def test_cancel_order(self, mock_ibkr_client, mock_risk_manager):
        """Test canceling an order"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        # Add an order
        om.open_orders[1000] = {'status': 'SUBMITTED'}
        
        om.cancel_order(1000)
        
        mock_ibkr_client.cancelOrder.assert_called_once_with(1000)
        assert om.open_orders[1000]['status'] == 'CANCELLED'
    
    def test_handle_order_status_filled(self, mock_ibkr_client, mock_risk_manager):
        """Test handling filled order status"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        # Add an order
        om.open_orders[1000] = {
            'type': 'credit_spread',
            'strikes': (5000, 4995),
            'right': 'P',
            'credit_target': 1.50,
            'status': 'SUBMITTED'
        }
        
        om.handle_order_status(1000, 'Filled', 1, 1.50)
        
        assert 1000 not in om.open_orders
        assert 1000 in om.filled_orders
        assert 1000 in om.active_positions
        assert om.active_positions[1000]['fill_price'] == 1.50
        assert om.active_positions[1000]['current_value'] == 1.50
    
    def test_calculate_max_loss_credit_spread(self, mock_ibkr_client, mock_risk_manager):
        """Test max loss calculation for credit spread"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        order = {
            'type': 'credit_spread',
            'strikes': (5000, 4995),
            'credit_target': 1.50
        }
        
        max_loss = om.calculate_max_loss(order)
        
        spread_width = 5
        expected_loss = (spread_width * 100) - 1.50
        assert max_loss == expected_loss
    
    def test_calculate_max_loss_iron_fly(self, mock_ibkr_client, mock_risk_manager):
        """Test max loss calculation for iron fly"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        order = {
            'type': 'iron_fly',
            'wing_width': 30,
            'credit_target': 7.50
        }
        
        max_loss = om.calculate_max_loss(order)
        
        expected_loss = (30 * 100) - 7.50
        assert max_loss == expected_loss
    
    def test_calculate_max_loss_iron_condor(self, mock_ibkr_client, mock_risk_manager):
        """Test max loss calculation for iron condor"""
        om = OrderManager(mock_ibkr_client, mock_risk_manager)
        
        order = {
            'type': 'iron_condor',
            'put_strikes': (4950, 4945),
            'call_strikes': (5050, 5055),
            'credit_target': 2.50
        }
        
        max_loss = om.calculate_max_loss(order)
        
        max_width = 5  # Both wings are 5 wide
        expected_loss = (max_width * 100) - 2.50
        assert max_loss == expected_loss