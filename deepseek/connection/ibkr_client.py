# connection/ibkr_client.py (fixed method calls)
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ContractDetails
from ibapi.order import Order
from ibapi.common import BarData, TickerId
from ibapi.utils import iswrapper
from ibapi.account_summary_tags import AccountSummaryTags
import threading
import queue
import time
from datetime import datetime
import logging
from typing import Optional, Dict, Any, List, Tuple

# Use absolute import for config
from config import IBKRConfig

class IBKRClient(EWrapper, EClient):
    """Native IBKR API client with callback handling"""
    
    def __init__(self, config: IBKRConfig):
        EClient.__init__(self, self)
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Communication queues
        self.data_queue = queue.Queue()
        self.error_queue = queue.Queue()
        self.order_queue = queue.Queue()
        
        # Connection state
        self.connected = False
        self.next_order_id = None
        self.accounts = []
        self.account_value = 0.0
        
        # Request tracking
        self.req_id_counter = 1000
        self.request_callbacks = {}
        
        # Data storage
        self.historical_data: Dict[int, List[Dict[str, Any]]] = {}
        self.realtime_prices: Dict[int, Dict[int, float]] = {}
        self.option_chains: Dict[int, List[ContractDetails]] = {}
        
        # Threading
        self.api_thread = None
        self.is_running = False
        
    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        """Handle errors from TWS/IB Gateway"""
        error_msg = f"Error ID: {reqId}, Code: {errorCode}, Message: {errorString}"
        
        # Non-critical errors (common during normal operation)
        non_critical = [2104, 2106, 2107, 2108, 2158]
        
        if errorCode in non_critical:
            self.logger.debug(error_msg)
        else:
            self.logger.error(error_msg)
            self.error_queue.put({
                'reqId': reqId,
                'code': errorCode,
                'message': errorString,
                'time': datetime.now()
            })
            
            # Critical connection errors
            if errorCode in [502, 504, 509, 2105]:
                self.handle_critical_error(errorCode)
    
    @iswrapper
    def nextValidId(self, orderId: int):
        """Receives next valid order ID"""
        self.next_order_id = orderId
        self.logger.info(f"Next valid order ID: {orderId}")
        self.connected = True
        
    @iswrapper
    def managedAccounts(self, accountsList: str):
        """Receive list of managed accounts"""
        self.accounts = accountsList.split(',')
        self.logger.info(f"Managed accounts: {self.accounts}")
        
    @iswrapper
    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str):
        """Receive account summary data"""
        if tag == 'NetLiquidation':
            try:
                self.account_value = float(value)
                self.logger.info(f"Account {account} Net Liquidation: ${value:,.2f}")
            except ValueError:
                self.logger.warning(f"Could not parse account value: {value}")
            
    @iswrapper
    def accountSummaryEnd(self, reqId: int):
        """Account summary complete"""
        self.data_queue.put({
            'type': 'account_summary',
            'reqId': reqId,
            'account_value': self.account_value
        })
    
    @iswrapper
    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        """Receive contract details"""
        if reqId not in self.option_chains:
            self.option_chains[reqId] = []
        self.option_chains[reqId].append(contractDetails)
    
    @iswrapper
    def contractDetailsEnd(self, reqId: int):
        """Contract details complete"""
        self.data_queue.put({
            'type': 'contract_details',
            'reqId': reqId,
            'data': self.option_chains.get(reqId, [])
        })
    
    @iswrapper
    def historicalData(self, reqId: int, bar: BarData):
        """Receive historical data bar"""
        if reqId not in self.historical_data:
            self.historical_data[reqId] = []
        self.historical_data[reqId].append({
            'date': bar.date,
            'open': float(bar.open),
            'high': float(bar.high),
            'low': float(bar.low),
            'close': float(bar.close),
            'volume': int(bar.volume) if bar.volume else 0
        })
    
    @iswrapper
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Historical data complete"""
        self.data_queue.put({
            'type': 'historical_data',
            'reqId': reqId,
            'data': self.historical_data.get(reqId, []),
            'start': start,
            'end': end
        })
        if reqId in self.historical_data:
            del self.historical_data[reqId]
    
    @iswrapper
    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        """Receive real-time tick price"""
        if reqId not in self.realtime_prices:
            self.realtime_prices[reqId] = {}
        self.realtime_prices[reqId][tickType] = float(price)
        
    @iswrapper
    def tickSize(self, reqId: TickerId, tickType: int, size: int):
        """Receive real-time tick size"""
        if reqId not in self.realtime_prices:
            self.realtime_prices[reqId] = {}
        self.realtime_prices[reqId][tickType] = int(size)
    
    @iswrapper
    def orderStatus(self, orderId: int, status: str, filled: float, remaining: float,
                    avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float,
                    clientId: int, whyHeld: str, mktCapPrice: float):
        """Receive order status updates"""
        self.order_queue.put({
            'type': 'order_status',
            'orderId': orderId,
            'status': status,
            'filled': float(filled),
            'remaining': float(remaining),
            'avgFillPrice': float(avgFillPrice),
            'lastFillPrice': float(lastFillPrice),
            'time': datetime.now()
        })
    
    @iswrapper
    def openOrder(self, orderId: int, contract: Contract, order: Order, orderState):
        """Receive open order information"""
        self.order_queue.put({
            'type': 'open_order',
            'orderId': orderId,
            'contract': contract,
            'order': order,
            'state': orderState
        })
    
    @iswrapper
    def connectionClosed(self):
        """Handle connection closure"""
        self.logger.warning("Connection to TWS/IB Gateway closed")
        self.connected = False
        
    def connect_and_run(self) -> bool:
        """Connect and start the API thread"""
        self.logger.info(f"Connecting to TWS/IB Gateway at {self.config.host}:{self.config.port}")
        
        # EClient connect method
        self.connect(self.config.host, self.config.port, clientId=self.config.client_id)
        
        # Start the API thread
        self.api_thread = threading.Thread(target=self.run, daemon=True)
        self.api_thread.start()
        
        # Wait for connection
        timeout = 10
        start_time = time.time()
        while not self.connected and time.time() - start_time < timeout:
            time.sleep(0.1)
            
        if self.connected:
            self.logger.info("Successfully connected to TWS/IB Gateway")
            
            # Request account information - FIXED: Added 'All' for tags parameter
            self.reqAccountSummary(9001, "All", "NetLiquidation,TotalCashValue,BuyingPower")
            
            return True
        else:
            self.logger.error("Failed to connect to TWS/IB Gateway")
            return False
    
    def handle_critical_error(self, error_code: int):
        """Handle critical connection errors"""
        self.logger.critical(f"Critical error {error_code}, attempting reconnect...")
        threading.Timer(10.0, self.reconnect).start()
    
    def reconnect(self):
        """Attempt to reconnect"""
        try:
            self.disconnect()
            time.sleep(2)
            self.connect_and_run()
        except Exception as e:
            self.logger.error(f"Reconnection failed: {e}")
    
    def disconnect_safe(self):
        """Safely disconnect"""
        self.is_running = False
        self.disconnect()
        if self.api_thread and self.api_thread.is_alive():
            self.api_thread.join(timeout=5)