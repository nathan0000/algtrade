# order_management/order_manager.py (fixed imports)
from ibapi.contract import Contract
from ibapi.order import Order
import threading
import time
from datetime import datetime
import logging
from typing import Dict, Any, Optional, List, Tuple

# Fix: Use absolute imports
from connection.ibkr_client import IBKRClient
from order_management.risk_manager import RiskManager

class OrderManager:
    """Manages order placement and tracking"""
    
    def __init__(self, ibkr_client: IBKRClient, risk_manager: RiskManager):
        self.ibkr = ibkr_client
        self.risk_manager = risk_manager
        self.logger = logging.getLogger(__name__)
        
        # Order tracking
        self.open_orders: Dict[int, Dict[str, Any]] = {}
        self.filled_orders: Dict[int, Dict[str, Any]] = {}
        self.active_positions: Dict[int, Dict[str, Any]] = {}
        
        # Lock for thread safety
        self.order_lock = threading.Lock()
        
    def create_spxw_contract(self, expiry: str, strike: float, right: str) -> Contract:
        """Create SPXW options contract"""
        contract = Contract()
        contract.symbol = 'SPXW'
        contract.secType = 'OPT'
        contract.exchange = 'CBOE'
        contract.currency = 'USD'
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = strike
        contract.right = right
        contract.multiplier = '100'
        return contract
    
    def create_combo_contract(self, legs: List[Contract]) -> Contract:
        """Create combo contract for multi-leg orders"""
        combo = Contract()
        combo.symbol = 'SPXW'
        combo.secType = 'BAG'
        combo.exchange = 'CBOE'
        combo.currency = 'USD'
        
        # Add legs to combo - this is simplified, actual implementation would need proper combo legs
        return combo
    
    def place_credit_spread(self, short_strike: float, long_strike: float,
                            right: str, expiry: str, credit_target: float,
                            quantity: int = 1) -> Optional[int]:
        """
        Place a credit spread order
        """
        with self.order_lock:
            order_id = self.ibkr.next_order_id
            if order_id is not None:
                self.ibkr.next_order_id += 1
            else:
                return None
        
        # Create contracts
        short_contract = self.create_spxw_contract(expiry, short_strike, right)
        long_contract = self.create_spxw_contract(expiry, long_strike, right)
        
        # Create combo order
        order = Order()
        order.action = 'SELL'  # Selling the spread
        order.orderType = 'LMT'
        order.lmtPrice = credit_target
        order.totalQuantity = quantity
        order.orderId = order_id
        order.tif = 'DAY'
        order.transmit = True
        
        # Create combo contract
        combo_contract = self.create_combo_contract([short_contract, long_contract])
        
        # Place order
        self.ibkr.placeOrder(order_id, combo_contract, order)
        
        # Track order
        self.open_orders[order_id] = {
            'type': 'credit_spread',
            'strikes': (short_strike, long_strike),
            'right': right,
            'expiry': expiry,
            'credit_target': credit_target,
            'quantity': quantity,
            'entry_time': datetime.now(),
            'status': 'SUBMITTED'
        }
        
        self.logger.info(f"Placed credit spread order {order_id}: {right} {short_strike}/{long_strike} @ {credit_target}")
        return order_id
    
    def place_iron_fly(self, central_strike: float, wing_width: float,
                       expiry: str, credit_target: float,
                       quantity: int = 1) -> Optional[int]:
        """
        Place an iron fly order
        """
        with self.order_lock:
            order_id = self.ibkr.next_order_id
            if order_id is not None:
                self.ibkr.next_order_id += 1
            else:
                return None
        
        # Create contracts for all four legs
        short_put = self.create_spxw_contract(expiry, central_strike, 'P')
        long_put = self.create_spxw_contract(expiry, central_strike - wing_width, 'P')
        short_call = self.create_spxw_contract(expiry, central_strike, 'C')
        long_call = self.create_spxw_contract(expiry, central_strike + wing_width, 'C')
        
        # Create combo order
        order = Order()
        order.action = 'SELL'
        order.orderType = 'LMT'
        order.lmtPrice = credit_target
        order.totalQuantity = quantity
        order.orderId = order_id
        order.tif = 'DAY'
        order.transmit = True
        
        # Create combo contract with all legs
        combo_contract = self.create_combo_contract([short_put, long_put, short_call, long_call])
        
        # Place order
        self.ibkr.placeOrder(order_id, combo_contract, order)
        
        # Track order
        self.open_orders[order_id] = {
            'type': 'iron_fly',
            'central_strike': central_strike,
            'wing_width': wing_width,
            'expiry': expiry,
            'credit_target': credit_target,
            'quantity': quantity,
            'entry_time': datetime.now(),
            'status': 'SUBMITTED'
        }
        
        self.logger.info(f"Placed iron fly order {order_id}: {central_strike} ±{wing_width} @ {credit_target}")
        return order_id
    
    def place_iron_condor(self, put_short: float, put_long: float,
                          call_short: float, call_long: float,
                          expiry: str, credit_target: float,
                          quantity: int = 1) -> Optional[int]:
        """
        Place an iron condor order
        """
        with self.order_lock:
            order_id = self.ibkr.next_order_id
            if order_id is not None:
                self.ibkr.next_order_id += 1
            else:
                return None
        
        # Create contracts
        short_put = self.create_spxw_contract(expiry, put_short, 'P')
        long_put = self.create_spxw_contract(expiry, put_long, 'P')
        short_call = self.create_spxw_contract(expiry, call_short, 'C')
        long_call = self.create_spxw_contract(expiry, call_long, 'C')
        
        # Create combo order
        order = Order()
        order.action = 'SELL'
        order.orderType = 'LMT'
        order.lmtPrice = credit_target
        order.totalQuantity = quantity
        order.orderId = order_id
        order.tif = 'DAY'
        order.transmit = True
        
        # Create combo contract
        combo_contract = self.create_combo_contract([short_put, long_put, short_call, long_call])
        
        # Place order
        self.ibkr.placeOrder(order_id, combo_contract, order)
        
        # Track order
        self.open_orders[order_id] = {
            'type': 'iron_condor',
            'put_strikes': (put_short, put_long),
            'call_strikes': (call_short, call_long),
            'expiry': expiry,
            'credit_target': credit_target,
            'quantity': quantity,
            'entry_time': datetime.now(),
            'status': 'SUBMITTED'
        }
        
        self.logger.info(f"Placed iron condor order {order_id}: P {put_short}/{put_long} C {call_short}/{call_long} @ {credit_target}")
        return order_id
    
    def cancel_order(self, order_id: int):
        """Cancel an open order"""
        if order_id in self.open_orders:
            self.ibkr.cancelOrder(order_id)
            self.open_orders[order_id]['status'] = 'CANCELLED'
            self.logger.info(f"Cancelled order {order_id}")
    
    def handle_order_status(self, order_id: int, status: str, filled: float,
                            avg_fill_price: float):
        """Handle order status updates"""
        if order_id not in self.open_orders:
            return
        
        order = self.open_orders[order_id]
        order['status'] = status
        
        if status == 'Filled':
            order['fill_price'] = avg_fill_price
            order['fill_time'] = datetime.now()
            
            # Move to filled orders
            self.filled_orders[order_id] = order
            del self.open_orders[order_id]
            
            # Create position record
            self.active_positions[order_id] = {
                **order,
                'current_value': order['credit_target'],
                'entry_price': avg_fill_price,
                'max_profit': order['credit_target'],
                'max_loss': self.calculate_max_loss(order)
            }
            
            # Add to risk manager
            self.risk_manager.add_position(order_id, self.active_positions[order_id])
            
            self.logger.info(f"Order {order_id} filled at {avg_fill_price}")
    
    def calculate_max_loss(self, order: Dict[str, Any]) -> float:
        """Calculate maximum loss for position"""
        if order['type'] == 'credit_spread':
            spread_width = abs(order['strikes'][1] - order['strikes'][0])
            return (spread_width * 100) - order['credit_target']
        elif order['type'] == 'iron_fly':
            return (order['wing_width'] * 100) - order['credit_target']
        elif order['type'] == 'iron_condor':
            put_width = abs(order['put_strikes'][1] - order['put_strikes'][0])
            call_width = abs(order['call_strikes'][1] - order['call_strikes'][0])
            max_width = max(put_width, call_width)
            return (max_width * 100) - order['credit_target']
        return 0