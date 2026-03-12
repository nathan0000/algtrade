import threading
import time
import datetime
import logging
import requests
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData, TickerId

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = "YOUR_DISCORD_WEBHOOK_HERE"
IB_PORT = 4002        # 7497 for Paper, 7496 for Live
ACCOUNT_ID = "DU3232524"
STOP_LOSS_USD = -500.0
EXIT_TIME = datetime.time(15, 45)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TWAPManager:
    """Calculates Time-Weighted Average Price based on 1-minute bars."""
    def __init__(self):
        self.sum_typical_price = 0.0
        self.bar_count = 0
        self.last_twap = 0.0

    def add_bar(self, bar: BarData):
        # Typical Price = (Open + High + Low + Close) / 4
        typical_price = (bar.open + bar.high + bar.low + bar.close) / 4
        self.sum_typical_price += typical_price
        self.bar_count += 1
        self.last_twap = self.sum_typical_price / self.bar_count
        logger.info(f"TWAP Updated: {self.last_twap:.2f} (Intervals: {self.bar_count})")

    def get_twap(self):
        return self.last_twap

class PnLApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.twap_manager = TWAPManager()
        self.spx_price = 0.0
        self.vix_price = 0.0
        self.active_trades = False
        self.strategy_triggered = False
        self.done = False

    def nextValidId(self, orderId: int):
        self.start_monitoring()

    def start_monitoring(self):
        # 1. Request SPX 1-minute Bars (Historical + Live)
        spx = Contract()
        spx.symbol = "SPX"
        spx.secType = "IND"
        spx.exchange = "CBOE"
        spx.currency = "USD"
        
        # Requests 1-minute bars from the start of the current day
        # keepUpToDate=True ensures we get 'historicalDataUpdate' calls every minute
        self.reqHistoricalData(1, spx, "", "1 D", "1 min", "TRADES", 0, 1, True, [])

        # 2. Request VIX Snapshot/Streaming (for filtering)
        vix = Contract()
        vix.symbol = "VIX"
        vix.secType = "IND"
        vix.exchange = "CBOE"
        vix.currency = "USD"
        self.reqMktData(2, vix, "", False, False, [])

    def historicalData(self, reqId: int, bar: BarData):
        """Processes historical bars to initialize TWAP."""
        self.twap_manager.add_bar(bar)
        self.spx_price = bar.close # Set current price to last bar close

    def historicalDataUpdate(self, reqId: int, bar: BarData):
        """Processes the ongoing 1-minute bar updates."""
        self.twap_manager.add_bar(bar)
        self.spx_price = bar.close

    def tickPrice(self, reqId: TickerId, tickType: int, price: float, attrib):
        # Use tickPrice only for VIX or fast SPX price display
        if reqId == 2 and tickType == 4: # Last Price for VIX
            self.vix_price = price

    def error(self, reqId, errorCode, errorString):
        if errorCode not in [2104, 2106, 2158]:
            logger.error(f"IBKR Error {errorCode}: {errorString}")

# ... [Keep previous TWAPManager and PnLApp setup] ...

    def place_vertical_spread(self, side="BULL"):
        """Constructs and places a 0DTE vertical credit spread."""
        logger.info(f"Constructing {side} Credit Spread...")
        
        # 1. Determine strikes (rounding to nearest 5)
        base_strike = round(self.spx_price / 5) * 5
        if side == "BULL":
            short_strike = base_strike - 10
            long_strike = short_strike - 5
            right = "P"
        else:
            short_strike = base_strike + 10
            long_strike = short_strike + 5
            right = "C"

        # 2. Create the Combo (Bag) Contract
        contract = Contract()
        contract.symbol = "SPX"
        contract.secType = "BAG"
        contract.currency = "USD"
        contract.exchange = "SMART"

        # Leg 1: The Short (Sell)
        leg1 = ComboLeg()
        leg1.conId = self.get_opt_conid(short_strike, right)
        leg1.ratio = 1
        leg1.action = "SELL"
        leg1.exchange = "SMART"

        # Leg 2: The Long (Buy - Hedge)
        leg2 = ComboLeg()
        leg2.conId = self.get_opt_conid(long_strike, right)
        leg2.ratio = 1
        leg2.action = "BUY"
        leg2.exchange = "SMART"

        contract.comboLegs = [leg1, leg2]

        # 3. Place the Order
        order = Order()
        order.action = "BUY" # Buying the 'spread' (even if it's a credit spread)
        order.orderType = "MKT" # Use LMT for live, MKT for testing
        order.totalQuantity = 1
        order.transmit = True

        self.placeOrder(self.next_id, contract, order)
        self.next_id += 1
        self.active_trades = True
        self.strategy_triggered = True

    def get_opt_conid(self, strike, right):
        """Helper to fetch conId for specific SPXW options."""
        # Note: In a production environment, you'd use reqContractDetails 
        # to fetch the exact conId for today's expiration.
        return 0 # Placeholder: Replace with actual ID fetching logic
    
def api_loop(app):
    app.run()

if __name__ == "__main__":
    app = PnLApp()
    app.connect("127.0.0.1", IB_PORT, clientId=3)

    api_thread = threading.Thread(target=api_loop, args=(app,), daemon=True)
    api_thread.start()
    
    time.sleep(3)
    logger.info("🤖 Bot Online: Monitoring SPX 0DTE with 1-Min TWAP")

    try:
        while not app.done:
            now = datetime.datetime.now().time()
            current_twap = app.twap_manager.get_twap()
            
            # Monitoring Display
            print(f"SPX: {app.spx_price:.2f} | TWAP: {current_twap:.2f} | VIX: {app.vix_price:.2f}", end='\r')
            
            # --- STRATEGY LOGIC ---
            # Entry: 10:30 AM, SPX > TWAP, VIX < 20
            if not app.active_trades and not app.strategy_triggered:
                if now > datetime.time(10, 30) and 0 < app.vix_price < 20.0:
                    if app.spx_price > current_twap:
                        logger.info("Strategy Signal: SPX above TWAP. Executing...")
                        # Insert your Order Placement logic here
                        app.active_trades = True
                        app.strategy_triggered = True

            # Exit: Standard Time
            if now >= EXIT_TIME and app.active_trades:
                logger.info("Market Closing: Flattening positions.")
                app.active_trades = False
            
            time.sleep(1)
    except KeyboardInterrupt:
        app.disconnect()