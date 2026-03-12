# config.py (updated with dataclass imports)
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, Any, Optional

@dataclass
class IBKRConfig:
    """IBKR connection configuration"""
    host: str = "127.0.0.1"
    port: int = 4002  # 7497 for paper, 7496 for live
    client_id: int = 1
    account: str = ""  # Leave empty for first account

@dataclass
class RiskConfig:
    """Risk management configuration"""
    max_risk_per_trade_pct: float = 0.02  # 2% of account per trade
    max_daily_risk_pct: float = 0.06      # 6% daily max loss
    max_positions: int = 3                 # Maximum concurrent positions
    profit_target_pct: float = 0.80        # Close at 80% profit
    stop_loss_multiple: float = 2.0        # Stop at 2x credit received
    min_credit_ratio: float = 0.20         # Minimum credit as % of spread width

@dataclass
class StrategyConfig:
    """Strategy-specific configuration"""
    # Put/Call spreads
    directional_delta_target: float = 0.20  # 20 delta for short strikes
    directional_spread_width: int = 5       # 5 points wide
    
    # Iron Fly
    iron_fly_atr_multiplier: float = 6.0    # Wing width = ATR * multiplier
    iron_fly_min_width: int = 15            # Minimum wing width
    iron_fly_max_width: int = 50            # Maximum wing width
    
    # Iron Condor
    condor_delta_target: float = 0.15       # 15 delta for short strikes
    condor_spread_width: int = 5            # 5 points wide per side
    
    # Market filters
    vix_threshold_low: float = 15.0
    vix_threshold_normal: float = 20.0
    vix_threshold_high: float = 25.0
    
    # Time windows (ET) - stored as time objects for comparison
    morning_start: time = field(default_factory=lambda: time(9, 30))
    morning_end: time = field(default_factory=lambda: time(11, 30))
    midday_start: time = field(default_factory=lambda: time(11, 30))
    midday_end: time = field(default_factory=lambda: time(14, 0))
    afternoon_start: time = field(default_factory=lambda: time(14, 0))
    afternoon_end: time = field(default_factory=lambda: time(15, 30))
    final_hour_start: time = field(default_factory=lambda: time(15, 30))
    market_close: time = field(default_factory=lambda: time(16, 0))
    
    # Computed properties for convenience
    @property
    def morning_start_time(self) -> time:
        return self.morning_start
    
    @property
    def final_hour_cutoff(self) -> time:
        return time(15, 45)  # 3:45 PM cutoff for entries

@dataclass
class AppConfig:
    """Main application configuration"""
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    log_level: str = "INFO"
    paper_trading: bool = True
    data_lookback_days: int = 20