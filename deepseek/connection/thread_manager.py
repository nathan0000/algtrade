# connection/thread_manager.py (fixed method calls)
import threading
import time
from datetime import datetime, time as dt_time
import pytz
import logging
from typing import List, Optional

# Use absolute imports
from connection.ibkr_client import IBKRClient
from ibapi.contract import Contract

class ThreadManager:
    """Manages background threads for the trading system"""
    
    def __init__(self, ibkr_client: IBKRClient):
        self.ibkr = ibkr_client
        self.logger = logging.getLogger(__name__)
        self.running = False
        self.threads: List[threading.Thread] = []
        self.ny_tz = pytz.timezone('America/New_York')
        
    def start_all(self):
        """Start all background threads"""
        self.running = True
        
        threads_config = [
            ("MarketData", self.market_data_loop),
            ("OrderMonitor", self.order_monitor_loop),
            ("PositionMonitor", self.position_monitor_loop),
            ("Heartbeat", self.heartbeat_loop)
        ]
        
        for name, target in threads_config:
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)
            self.logger.info(f"Started {name} thread")
    
    def stop_all(self):
        """Stop all threads gracefully"""
        self.running = False
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=2)
        self.logger.info("All threads stopped")
    
    def market_data_loop(self):
        """Thread for continuous market data requests"""
        spx_contract = self.create_spx_contract()
        vix_contract = self.create_vix_contract()
        
        while self.running:
            try:
                current_time = datetime.now(self.ny_tz).time()
                
                # Only request data during market hours + pre/post
                if dt_time(8, 0) <= current_time <= dt_time(17, 0):
                    # Request SPX tick data - FIXED: Added generic tick list
                    self.ibkr.reqMktData(1001, spx_contract, "", False, False, [])
                    
                    # Request VIX data
                    self.ibkr.reqMktData(1002, vix_contract, "", False, False, [])
                
                time.sleep(5)  # Rate limiting
                
            except Exception as e:
                self.logger.error(f"Market data thread error: {e}")
                time.sleep(10)
    
    def order_monitor_loop(self):
        """Thread for monitoring open orders"""
        while self.running:
            try:
                # Request open orders every 2 seconds
                self.ibkr.reqOpenOrders()
                time.sleep(2)
            except Exception as e:
                self.logger.error(f"Order monitor error: {e}")
                time.sleep(5)
    
    def position_monitor_loop(self):
        """Thread for monitoring positions"""
        while self.running:
            try:
                # Request account updates - FIXED: subscribe flag is boolean
                self.ibkr.reqAccountUpdates(True, "")
                time.sleep(5)
            except Exception as e:
                self.logger.error(f"Position monitor error: {e}")
                time.sleep(5)
    
    def heartbeat_loop(self):
        """Thread for connection heartbeat"""
        while self.running:
            try:
                # Check connection health
                if not self.ibkr.connected:
                    self.logger.warning("Connection lost, attempting reconnect...")
                    self.ibkr.reconnect()
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Heartbeat error: {e}")
                time.sleep(10)
    
    @staticmethod
    def create_spx_contract() -> Contract:
        """Create SPX index contract"""
        contract = Contract()
        contract.symbol = 'SPX'
        contract.secType = 'IND'
        contract.exchange = 'CBOE'
        contract.currency = 'USD'
        return contract
    
    @staticmethod
    def create_vix_contract() -> Contract:
        """Create VIX futures contract"""
        contract = Contract()
        contract.symbol = 'VIX'
        contract.secType = 'FUT'
        contract.exchange = 'CFE'
        contract.currency = 'USD'
        # Use front month
        contract.lastTradeDateOrContractMonth = datetime.now().strftime('%Y%m')
        return contract