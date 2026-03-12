# market/data_collector.py (updated with proper imports)
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import logging
from typing import Dict, Any, Optional, List
from collections import deque
from ibapi.contract import Contract

# Use relative imports
from connection.ibkr_client import IBKRClient
from config import AppConfig

logging.basicConfig(level=logging.DEBUG, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

class MarketDataCollector:
    """Collects and processes real-time market data"""
    
    def __init__(self, ibkr_client: IBKRClient):
        self.ibkr = ibkr_client
        self.logger = logging.getLogger(__name__)
        self.ny_tz = pytz.timezone('America/New_York')
        
        # Data storage
        self.price_history = deque(maxlen=100)
        self.volume_history = deque(maxlen=100)
        self.vwap_data = []
        self.daily_high = 0
        self.daily_low = float('inf')
        self.daily_open = 0
        self.daily_volume = 0
        
        # Technical indicators
        self.vwap = 0
        self.atr = 0
        self.support_levels = []
        self.resistance_levels = []
    
    # ... rest of the methods remain the same
        
    def update_tick(self, price: float, volume: int):
        """Update with new tick data"""
        self.price_history.append({
            'time': datetime.now(self.ny_tz),
            'price': price,
            'volume': volume
        })
        
        # Update daily stats
        self.daily_high = max(self.daily_high, price)
        self.daily_low = min(self.daily_low, price)
        if self.daily_open == 0:
            self.daily_open = price
            
        self.daily_volume += volume
        
        # Update VWAP
        self.calculate_vwap(price, volume)
        
    def calculate_vwap(self, price: float, volume: int):
        """Calculate Volume-Weighted Average Price"""
        if len(self.vwap_data) == 0:
            self.vwap = price
        else:
            # VWAP = Σ(Price * Volume) / Σ(Volume)
            total_value = sum(d['price'] * d['volume'] for d in self.vwap_data[-20:])
            total_volume = sum(d['volume'] for d in self.vwap_data[-20:])
            if total_volume > 0:
                self.vwap = total_value / total_volume
        
        self.vwap_data.append({
            'price': price,
            'volume': volume,
            'vwap': self.vwap
        })
    
    def calculate_atr(self, period: int = 14):
        """Calculate Average True Range"""
        if len(self.price_history) < period + 1:
            return 0
            
        prices = list(self.price_history)
        true_ranges = []
        
        for i in range(1, len(prices)):
            high = max(prices[i]['price'], prices[i-1]['price'])
            low = min(prices[i]['price'], prices[i-1]['price'])
            prev_close = prices[i-1]['price']
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)
        
        if len(true_ranges) >= period:
            self.atr = np.mean(true_ranges[-period:])
        else:
            self.atr = np.mean(true_ranges)
            
        return self.atr
    
    def get_current_state(self) -> Dict[str, Any]:
        """Get current market state"""
        current_price = self.price_history[-1]['price'] if self.price_history else 0
        
        return {
            'current_price': current_price,
            'daily_open': self.daily_open,
            'daily_high': self.daily_high,
            'daily_low': self.daily_low,
            'vwap': self.vwap,
            'atr': self.atr,
            'distance_from_vwap': ((current_price - self.vwap) / self.vwap * 100) if self.vwap else 0,
            'range_width': self.daily_high - self.daily_low,
            'range_position': ((current_price - self.daily_low) / 
                              (self.daily_high - self.daily_low)) if self.daily_high > self.daily_low else 0.5
        }
    
    def request_historical_data(self, days: int = 20):
        """Request historical SPX data"""
        contract = self.create_spx_contract()
        end_date = datetime.now().strftime('%Y%m%d %H:%M:%S')
        
        req_id = self.ibkr.req_id_counter
        self.ibkr.req_id_counter += 1
        
        self.ibkr.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_date,
            durationStr=f'{days} D',
            barSizeSetting='1 hour',
            whatToShow='TRADES',
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )
        
        return req_id
    
    @staticmethod
    def create_spx_contract():
        """Create SPX index contract"""
        contract = Contract()
        contract.symbol = 'SPX'
        contract.secType = 'IND'
        contract.exchange = 'CBOE'
        contract.currency = 'USD'
        return contract