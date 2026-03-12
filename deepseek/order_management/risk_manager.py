# order_management/risk_manager.py (fixed - remove import of OrderManager)
from datetime import datetime, date
import logging
from typing import Dict, Any, Optional, List

# Fix: Remove import of OrderManager to break circular dependency
from connection.ibkr_client import IBKRClient
from config import RiskConfig

class RiskManager:
    """Manages risk across all positions"""
    
    def __init__(self, config: RiskConfig, ibkr_client: IBKRClient):
        self.config = config
        self.ibkr = ibkr_client
        self.logger = logging.getLogger(__name__)
        
        # Daily tracking
        self.daily_trades: List[Dict[str, Any]] = []
        self.daily_pnl = 0.0
        self.current_date = date.today()
        
        # Position tracking
        self.positions: Dict[int, Dict[str, Any]] = {}
        
    def validate_new_trade(self, risk_amount: float, strategy: str) -> bool:
        """
        Validate if new trade is within risk limits
        """
        # Reset daily tracking if new day
        self._check_new_day()
        
        # Per-trade risk check
        max_risk_per_trade = self.ibkr.account_value * self.config.max_risk_per_trade_pct
        if risk_amount > max_risk_per_trade:
            self.logger.warning(f"Trade risk ${risk_amount:.2f} exceeds max ${max_risk_per_trade:.2f}")
            return False
        
        # Daily risk check
        daily_risk_used = sum([abs(t.get('risk', 0)) for t in self.daily_trades])
        max_daily_risk = self.ibkr.account_value * self.config.max_daily_risk_pct
        
        if daily_risk_used + risk_amount > max_daily_risk:
            self.logger.warning(f"Daily risk ${daily_risk_used + risk_amount:.2f} exceeds max ${max_daily_risk:.2f}")
            return False
        
        # Position count check
        if len(self.positions) >= self.config.max_positions:
            self.logger.warning(f"Max positions ({self.config.max_positions}) reached")
            return False
        
        return True
    
    def add_position(self, order_id: int, position_data: Dict[str, Any]):
        """Add new position to tracking"""
        self.positions[order_id] = position_data
        self.logger.info(f"Added position {order_id} to tracking")
    
    def update_position(self, order_id: int, current_value: float) -> Optional[str]:
        """Update position value and check exit conditions"""
        if order_id in self.positions:
            self.positions[order_id]['current_value'] = current_value
            
            # Check exit conditions
            exit_reason = self.check_exit_conditions(order_id)
            if exit_reason:
                self.logger.info(f"Position {order_id} exit signal: {exit_reason}")
                return exit_reason
        
        return None
    
    def check_exit_conditions(self, order_id: int) -> Optional[str]:
        """Check if position should be exited"""
        position = self.positions.get(order_id)
        if not position:
            return None
        
        # Time-based exit
        current_time = datetime.now().time()
        if current_time.hour >= 15 and current_time.minute >= 55:
            return "market_close"
        
        # Profit target (80% of max profit)
        if position.get('current_value', 0) <= position.get('credit_target', 0) * 0.2:
            return "profit_target"
        
        # Stop loss (2x credit received)
        if position.get('current_value', 0) >= position.get('credit_target', 0) * 2:
            return "stop_loss"
        
        return None
    
    def close_position(self, order_id: int, exit_reason: str, exit_price: float):
        """Close a position"""
        if order_id in self.positions:
            position = self.positions.pop(order_id)
            
            # Calculate P&L
            pnl = (position.get('credit_target', 0) - exit_price) * 100
            
            trade_record = {
                'order_id': order_id,
                'strategy': position.get('type'),
                'entry_time': position.get('entry_time'),
                'exit_time': datetime.now(),
                'entry_credit': position.get('credit_target'),
                'exit_price': exit_price,
                'pnl': pnl,
                'exit_reason': exit_reason,
                'risk': position.get('risk', 0)
            }
            
            self.daily_trades.append(trade_record)
            self.daily_pnl += pnl
            
            self.logger.info(f"Closed position {order_id}: {exit_reason}, P&L: ${pnl:.2f}")
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get daily trading statistics"""
        self._check_new_day()
        
        winning_trades = [t for t in self.daily_trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in self.daily_trades if t.get('pnl', 0) < 0]
        
        return {
            'date': self.current_date,
            'total_trades': len(self.daily_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(self.daily_trades) if self.daily_trades else 0,
            'total_pnl': self.daily_pnl,
            'avg_win': sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0,
            'avg_loss': sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0,
            'positions_open': len(self.positions)
        }
    
    def _check_new_day(self):
        """Check if day changed and reset daily tracking"""
        today = date.today()
        if today != self.current_date:
            self.daily_trades = []
            self.daily_pnl = 0.0
            self.current_date = today
            self.logger.info("Reset daily tracking for new day")