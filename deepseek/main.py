# main.py (updated with better error handling)
import sys
import os
import time
import logging
from datetime import datetime, time as dt_time
import pytz
from typing import Optional

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import configuration
from config import AppConfig

# Import connection modules
from connection.ibkr_client import IBKRClient
from connection.thread_manager import ThreadManager

# Import market modules
from market.data_collector import MarketDataCollector
from market.vix_analyzer import VIXAnalyzer
from market.first_hour import FirstHourAnalyzer
from market.sentiment import SentimentAnalyzer

# Import strategy modules
from strategies.put_spread import PutCreditSpreadStrategy
from strategies.call_spread import CallCreditSpreadStrategy
from strategies.iron_fly import IronFlyStrategy
from strategies.iron_condor import IronCondorStrategy

# Import order management modules
from order_management.order_manager import OrderManager
from order_management.risk_manager import RiskManager

# Import utilities
from utils.logger import setup_logging

class SPX0DTEAutoTrader:
    """Main application for automated SPX 0DTE trading"""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.ny_tz = pytz.timezone('America/New_York')
        
        # Initialize components
        self.ibkr = IBKRClient(config.ibkr)
        self.thread_manager = ThreadManager(self.ibkr)
        self.market_data = MarketDataCollector(self.ibkr)
        self.vix = VIXAnalyzer(self.ibkr)
        self.first_hour = FirstHourAnalyzer()
        self.sentiment = SentimentAnalyzer()
        
        # Initialize risk manager
        self.risk_manager = RiskManager(config.risk, self.ibkr)
        
        # Initialize order manager
        self.order_manager = OrderManager(self.ibkr, self.risk_manager)
        
        # Initialize strategies
        self.strategies = [
            PutCreditSpreadStrategy(self.ibkr, config.strategy),
            CallCreditSpreadStrategy(self.ibkr, config.strategy),
            IronFlyStrategy(self.ibkr, config.strategy),
            IronCondorStrategy(self.ibkr, config.strategy)
        ]
        
        # Trading state
        self.is_running = False
        self.trading_paused = False
        self.pause_reason = ""
        
    def start(self):
        """Start the trading system"""
        self.logger.info("Starting SPX 0DTE AutoTrader")
        
        try:
            # Connect to IBKR
            if not self.ibkr.connect_and_run():
                self.logger.error("Failed to connect to IBKR")
                return False
            
            # Wait a moment for connection to stabilize
            time.sleep(2)
            
            # Start background threads
            self.thread_manager.start_all()
            
            # Request historical data
            self.request_historical_data()
            
            # Start main trading loop
            self.is_running = True
            self.main_loop()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting trader: {e}", exc_info=True)
            return False
    
    def request_historical_data(self):
        """Request historical data for analysis"""
        try:
            # Request SPX historical data
            self.market_data.request_historical_data(self.config.data_lookback_days)
            
            # Request VIX historical data
            self.request_vix_data()
        except Exception as e:
            self.logger.error(f"Error requesting historical data: {e}")
    
    def request_vix_data(self):
        """Request VIX historical data"""
        try:
            contract = self.thread_manager.create_vix_contract()
            end_date = datetime.now().strftime('%Y%m%d %H:%M:%S')
            
            req_id = self.ibkr.req_id_counter
            self.ibkr.req_id_counter += 1
            
            self.ibkr.reqHistoricalData(
                reqId=req_id,
                contract=contract,
                endDateTime=end_date,
                durationStr=f'{self.config.data_lookback_days} D',
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=1,
                formatDate=1,
                keepUpToDate=False,
                chartOptions=[]
            )
        except Exception as e:
            self.logger.error(f"Error requesting VIX data: {e}")
    
    def main_loop(self):
        """Main trading loop"""
        self.logger.info("Entering main trading loop")
        
        loop_count = 0
        while self.is_running:
            try:
                current_time = datetime.now(self.ny_tz).time()
                
                # Check if market is open
                if dt_time(9, 30) <= current_time <= dt_time(16, 0):
                    
                    # Check if trading is paused
                    if self.trading_paused:
                        if loop_count % 12 == 0:  # Log every minute
                            self.logger.debug(f"Trading paused: {self.pause_reason}")
                        time.sleep(5)
                        loop_count += 1
                        continue
                    
                    # Execute trading cycle (every 30 seconds)
                    if loop_count % 6 == 0:
                        self.execute_trading_cycle()
                    
                    # Manage existing positions (every 10 seconds)
                    if loop_count % 2 == 0:
                        self.manage_positions()
                    
                elif current_time > dt_time(16, 0) and current_time < dt_time(17, 0):
                    # Market closed - prepare for next day
                    if loop_count % 12 == 0:  # Log every minute
                        self.prepare_for_next_day()
                    time.sleep(5)
                
                time.sleep(5)  # Main loop sleep
                loop_count += 1
                
                # Reset loop count to prevent overflow
                if loop_count > 1000:
                    loop_count = 0
                
            except KeyboardInterrupt:
                self.logger.info("Received interrupt signal")
                self.shutdown()
                break
            except Exception as e:
                self.logger.error(f"Main loop error: {e}", exc_info=True)
                time.sleep(10)
    
    def execute_trading_cycle(self):
        """Execute one trading cycle"""
        try:
            # Get current market state
            market_state = self.market_data.get_current_state()
            
            # Get VIX state
            vix_state = self.vix.get_regime()
            
            # Check if we should trade based on VIX
            if not self.vix.should_trade():
                self.trading_paused = True
                self.pause_reason = "VIX too high"
                return
            
            # Get sentiment signals
            sentiment_signals = self.sentiment.get_sentiment_signals()
            
            # Get first hour analysis (if available)
            first_hour_analysis = self.first_hour.analyze()
            
            # Evaluate each strategy
            best_setup = None
            best_confidence = 0
            
            for strategy in self.strategies:
                setup = strategy.should_enter(
                    market_state, vix_state, 
                    sentiment_signals, first_hour_analysis
                )
                
                if setup and setup.get('confidence', 0) > best_confidence:
                    best_setup = setup
                    best_confidence = setup['confidence']
            
            # Execute best setup
            if best_setup:
                self.execute_strategy_trade(best_setup, market_state)
                
        except Exception as e:
            self.logger.error(f"Error in trading cycle: {e}")
    
    def execute_strategy_trade(self, setup: dict, market_state: dict):
        """Execute a trade based on strategy setup"""
        try:
            # Get current price
            current_price = market_state.get('current_price', 0)
            if current_price == 0:
                self.logger.warning("Cannot execute trade: current price is 0")
                return
            
            # Find the strategy instance
            strategy = None
            for s in self.strategies:
                if s.name.lower().replace(' ', '') == setup.get('strategy', '').lower().replace(' ', ''):
                    strategy = s
                    break
            
            if not strategy:
                self.logger.error(f"Strategy {setup.get('strategy')} not found")
                return
            
            # Calculate strikes
            strikes, rights = strategy.calculate_strikes(current_price, market_state, setup)
            
            # Calculate credit target
            credit_target = strategy.calculate_credit_target(strikes, market_state)
            
            # Calculate risk
            spread_width = abs(strikes[1] - strikes[0])
            risk_amount = (spread_width * 100) - credit_target
            
            # Validate risk
            if not self.risk_manager.validate_new_trade(risk_amount, setup.get('strategy', 'unknown')):
                self.logger.warning(f"Trade rejected by risk manager")
                return
            
            # Get today's expiry
            expiry = datetime.now().strftime('%Y%m%d')
            
            # Place order based on strategy type
            order_id = None
            
            strategy_type = setup.get('strategy', '')
            
            if strategy_type == 'put_credit_spread':
                order_id = self.order_manager.place_credit_spread(
                    strikes[0], strikes[1], 'P', expiry, credit_target
                )
            elif strategy_type == 'call_credit_spread':
                order_id = self.order_manager.place_credit_spread(
                    strikes[0], strikes[1], 'C', expiry, credit_target
                )
            elif strategy_type == 'iron_fly':
                order_id = self.order_manager.place_iron_fly(
                    setup.get('central_strike', current_price), 
                    setup.get('wing_width', 30), 
                    expiry, credit_target
                )
            elif strategy_type == 'iron_condor':
                order_id = self.order_manager.place_iron_condor(
                    strikes[0], strikes[1], strikes[2], strikes[3],
                    expiry, credit_target
                )
            
            if order_id:
                self.logger.info(f"Executed {strategy_type} trade with confidence {setup.get('confidence', 0)}%")
                
        except Exception as e:
            self.logger.error(f"Error executing trade: {e}")
    
    def manage_positions(self):
        """Manage existing positions"""
        try:
            for order_id, position in list(self.order_manager.active_positions.items()):
                # Get current option price (simplified - in production, request market data)
                current_price = self.get_option_price(order_id)
                
                # Update position in risk manager
                exit_signal = self.risk_manager.update_position(order_id, current_price)
                
                if exit_signal:
                    # Close position
                    self.close_position(order_id, exit_signal, current_price)
        except Exception as e:
            self.logger.error(f"Error managing positions: {e}")
    
    def get_option_price(self, order_id: int) -> float:
        """Get current price for option position"""
        # Simplified - in production, you would request market data
        # This is a placeholder
        return 0.0
    
    def close_position(self, order_id: int, reason: str, current_price: float):
        """Close a position"""
        try:
            if order_id in self.order_manager.active_positions:
                position = self.order_manager.active_positions[order_id]
                
                # Create closing order (simplified)
                self.logger.info(f"Closing position {order_id}: {reason}")
                
                # Record in risk manager
                self.risk_manager.close_position(order_id, reason, current_price)
                
                # Remove from active positions
                del self.order_manager.active_positions[order_id]
        except Exception as e:
            self.logger.error(f"Error closing position {order_id}: {e}")
    
    def prepare_for_next_day(self):
        """Prepare for next trading day"""
        try:
            self.logger.info("Preparing for next trading day")
            
            # Reset first hour data
            self.first_hour.first_hour_data = []
            
            # Reset market data
            self.market_data.daily_high = 0
            self.market_data.daily_low = float('inf')
            self.market_data.daily_open = 0
            self.market_data.daily_volume = 0
            
            # Check if any positions still open (should be closed by now)
            if self.order_manager.active_positions:
                self.logger.warning(f"Still have {len(self.order_manager.active_positions)} positions open after market close")
        except Exception as e:
            self.logger.error(f"Error preparing for next day: {e}")
    
    def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("Shutting down...")
        self.is_running = False
        
        try:
            # Close all positions
            for order_id in list(self.order_manager.active_positions.keys()):
                self.close_position(order_id, "system_shutdown", 0)
            
            # Cancel all open orders
            for order_id in list(self.order_manager.open_orders.keys()):
                self.order_manager.cancel_order(order_id)
            
            # Stop threads
            self.thread_manager.stop_all()
            
            # Disconnect from IBKR
            self.ibkr.disconnect_safe()
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
        
        self.logger.info("Shutdown complete")
    
    def connect(self):
        """Connect to IBKR (alias for backward compatibility)"""
        return self.ibkr.connect_and_run()


def main():
    """Main entry point"""
    # Setup logging
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Load configuration
    config = AppConfig()
    
    # Override with command line arguments if needed
    if len(sys.argv) > 1:
        if sys.argv[1] == '--live':
            config.ibkr.port = 7496  # Live trading port
            config.paper_trading = False
            logger.warning("LIVE TRADING MODE - USE WITH CAUTION")
        elif sys.argv[1] == '--paper':
            config.ibkr.port = 7497  # Paper trading port
            config.paper_trading = True
            logger.info("Paper trading mode")
    
    # Create and run trader
    trader = SPX0DTEAutoTrader(config)
    
    try:
        # Just initialize and test connection without starting full trading
        logger.info("Testing connection to IBKR...")
        if trader.ibkr.connect_and_run():
            logger.info("✅ Successfully connected to IBKR")
            logger.info(f"Account value: ${trader.ibkr.account_value:,.2f}")
            
            # Disconnect after test
            trader.ibkr.disconnect_safe()
            logger.info("Disconnected from IBKR")
            
            # Ask user if they want to start trading
            response = input("\nDo you want to start the full trading system? (y/n): ")
            if response.lower() == 'y':
                logger.info("Starting full trading system...")
                if trader.start():
                    logger.info("Trading system running. Press Ctrl+C to stop.")
                    # Keep main thread alive
                    while trader.is_running:
                        time.sleep(1)
                else:
                    logger.error("Failed to start trading system")
                    sys.exit(1)
            else:
                logger.info("Exiting without starting trading")
        else:
            logger.error("Failed to connect to IBKR")
            logger.info("\nTroubleshooting tips:")
            logger.info("1. Make sure TWS/IB Gateway is running")
            logger.info("2. Check that port 7497 is correct for paper trading")
            logger.info("3. Ensure API connections are enabled in TWS/IB Gateway")
            logger.info("4. Check that the client ID is not already in use")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
        trader.shutdown()
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        trader.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()