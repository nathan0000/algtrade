# strategies/base_strategy.py
from abc import ABC, abstractmethod
from datetime import datetime, time
import pytz
import logging
from typing import Dict, Any, Optional, Tuple, List

from connection.ibkr_client import IBKRClient
from config import StrategyConfig, RiskConfig

class BaseStrategy(ABC):
    """Base class for all trading strategies"""

    def __init__(self, name: str, ibkr_client: IBKRClient,
                 strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.name = name
        self.ibkr = ibkr_client
        self.strategy_config = strategy_config
        self.risk_config = risk_config
        self.logger = logging.getLogger(f"{__name__}.{name}")
        self.ny_tz = pytz.timezone('America/New_York')

        # State
        self.active_positions: List[Dict[str, Any]] = []
        self.daily_trades: List[Dict[str, Any]] = []
        self.last_signal_time: Optional[datetime] = None

    @abstractmethod
    def should_enter(self, market_state: Dict[str, Any],
                     vix_state: Dict[str, Any],
                     sentiment: Dict[str, Any],
                     first_hour: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def calculate_strikes(self, current_price: float,
                          market_state: Dict[str, Any],
                          params: Dict[str, Any]) -> Tuple[List[float], List[str]]:
        pass

    @abstractmethod
    def calculate_credit_target(self, strikes: List[float],
                                market_state: Dict[str, Any]) -> float:
        pass

    def validate_entry(self, entry_params: Dict[str, Any]) -> bool:
        """Validate entry parameters against risk rules"""
        current_time = datetime.now(self.ny_tz).time()

        # Avoid first 30 minutes
        if current_time < self.strategy_config.morning_start:
            self.logger.debug("Too early to trade")
            return False

        # Avoid last 15 minutes (use entry_cutoff property)
        if current_time > self.strategy_config.entry_cutoff:
            self.logger.debug("Too late to enter new positions")
            return False

        return True

    def calculate_position_size(self, risk_amount: float) -> int:
        """Calculate number of contracts based on risk"""
        max_risk_dollars = self.ibkr.account_value * self.risk_config.max_risk_per_trade_pct
        if risk_amount <= 0:
            return 0
        contracts = int(max_risk_dollars // risk_amount) if risk_amount > 0 else 0
        return min(contracts, 1)  # Max 1 contract for 0DTE

    def on_trade_exit(self, trade_data: Dict[str, Any]):
        if trade_data in self.active_positions:
            self.active_positions.remove(trade_data)
        self.daily_trades.append(trade_data)

    def get_exit_signal(self, position: Dict[str, Any],
                        current_price: float) -> Optional[str]:
        """Check if position should be exited. Returns exit reason or None."""
        current_time = datetime.now(self.ny_tz).time()
        if current_time >= self.strategy_config.market_close:
            return "market_close"

        if position.get('current_value', 0) <= position.get('credit', 0) * 0.2:
            return "profit_target"

        if position.get('current_value', 0) >= position.get('credit', 0) * 2:
            return "stop_loss"

        return None