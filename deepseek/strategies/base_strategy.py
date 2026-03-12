# strategies/base_strategy.py (updated)
from abc import ABC, abstractmethod
from datetime import datetime, time
import pytz
import logging
from typing import Dict, Any, Optional, Tuple, List

# Use relative imports
from connection.ibkr_client import IBKRClient
from config import StrategyConfig

class BaseStrategy(ABC):
    """Base class for all trading strategies"""
    
    def __init__(self, name: str, ibkr_client: IBKRClient, config: StrategyConfig):
        self.name = name
        self.ibkr = ibkr_client
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{name}")
        self.ny_tz = pytz.timezone('America/New_York')
        
        # State
        self.active_positions: List[Dict[str, Any]] = []
        self.daily_trades: List[Dict[str, Any]] = []
        self.last_signal_time: Optional[datetime] = None
    
    # ... rest of the methods remain the same
        
    @abstractmethod
    def should_enter(self, market_state: Dict[str, Any], 
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Determine if strategy should enter a trade
        Returns entry parameters or None
        """
        pass
    
    @abstractmethod
    def calculate_strikes(self, current_price: float, 
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:
        """
        Calculate strike prices for the strategy
        Returns (strikes, right) lists
        """
        pass
    
    @abstractmethod
    def calculate_credit_target(self, strikes: List[float], 
                                market_state: Dict[str, Any]) -> float:
        """
        Calculate target credit for the strategy
        """
        pass
    
    def validate_entry(self, entry_params: Dict[str, Any]) -> bool:
        """
        Validate entry parameters against risk rules
        """
        # Check time window
        current_time = datetime.now(self.ny_tz).time()
        
        # Avoid first 30 minutes
        if current_time < self.config.morning_start_time:
            self.logger.debug("Too early to trade")
            return False
        
        # Avoid last 15 minutes
        if current_time > self.config.final_hour_cutoff:
            self.logger.debug("Too late to enter new positions")
            return False
        
        return True
    
    def calculate_position_size(self, risk_amount: float) -> int:
        """
        Calculate number of contracts based on risk
        """
        max_risk_dollars = self.ibkr.account_value * self.config.max_risk_per_trade_pct
        if risk_amount <= 0:
            return 0
        
        contracts = int(max_risk_dollars // risk_amount)
        return min(contracts, 1)  # Max 1 contract for 0DTE
    
    def on_trade_exit(self, trade_data: Dict[str, Any]):
        """
        Called when a trade is exited
        """
        self.active_positions.remove(trade_data)
        self.daily_trades.append(trade_data)
        
    def get_exit_signal(self, position: Dict[str, Any], 
                        current_price: float) -> Optional[str]:
        """
        Check if position should be exited
        Returns exit reason or None
        """
        # Time-based exit
        current_time = datetime.now(self.ny_tz).time()
        if current_time >= self.config.market_close_time:
            return "market_close"
        
        # Profit target
        if position.get('current_value', 0) <= position['credit'] * 0.2:
            return "profit_target"
        
        # Stop loss
        if position.get('current_value', 0) >= position['credit'] * 2:
            return "stop_loss"
        
        return None