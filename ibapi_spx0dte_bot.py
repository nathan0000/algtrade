import threading
import time
import datetime
import logging
import requests
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.common import TickerId

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1473812801096122430/nQBp2ua489B25Qa9ZU9jZRD7L1kqK0pDLbzxnIvv0ocRNPwuOfxGJv-9I0qltuKAb_SB"
IB_PORT = 7497        # 7497 for Paper, 7496 for Live
ACCOUNT_ID = "DU3232524"
STOP_LOSS_USD = -500.0
EXIT_TIME = datetime.time(15, 45)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DiscordNotifier:
    """Handles rich embed notifications via Discord Webhooks."""
    @staticmethod
    def send(title, message, color=0x3498db):
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": color,
                "footer": {"text": f"System Time: {datetime.datetime.now().strftime('%H:%M:%S')}"}
            }]
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Discord Error: {e}")

class RiskManager:
    """Monitors P&L and enforces global stop losses."""
    def __init__(self, app):
        self.app = app
        self.last_pnl_alert = 0

    def process_pnl(self, daily_pnl):
        # 1. Emergency Stop Loss
        if daily_pnl <= STOP_LOSS_USD and self.app.active_trades:
            logger.warning(f"STOP LOSS HIT: {daily_pnl}")
            self.app.executor.flatten_account("GLOBAL STOP LOSS BREACH")

        # 2. Threshold-based Reporting
        if abs(daily_pnl - self.last_pnl_alert) >= 50:
            self.last_pnl_alert = daily_pnl
            color = 0x2ecc71 if daily_pnl >= 0 else 0xe74c3c
            DiscordNotifier.send("💰 P&L Update", f"Total Daily P&L: ${daily_pnl:.2f}", color)

class ExecutionManager:
    """Handles order construction and position liquidation."""
    def __init__(self, app):
        self.app = app

    def flatten_account(self, reason):
        logger.info(f"Flattening Account: {reason}")
        self.app.reqGlobalCancel()
        
        # Request positions to ensure we have the latest
        self.app.reqPositions()
        time.sleep(1) 
        
        for conId, pos_data in self.app.positions.items():
            if pos_data['symbol'] in ['SPX', 'SPXW']:
                contract = pos_data['contract']
                qty = pos_data['qty']
                if qty == 0: continue

                order = Order()
                order.action = "SELL" if qty > 0 else "BUY"
                order.orderType = "MKT"
                order.totalQuantity = abs(qty)
                order.transmit = True
                
                self.app.placeOrder(self.app.next_order_id(), contract, order)
        
        self.app.active_trades = False
        DiscordNotifier.send("⚠️ LIQUIDATION TRIGGERED", f"Reason: {reason}", 0x000000)

    def place_iron_butterfly(self, atm_strike, width=20):
        """
        Constructs and places an Iron Butterfly (Top 0DTE R/R Strategy).
        Strategy: Sell ATM Put/Call, Buy OTM Put/Call (Wings).
        """
        logger.info(f"Executing Iron Butterfly at Strike {atm_strike}")
        today = datetime.datetime.now().strftime('%Y%m%d')
        
        # Define the 4 Option Contracts
        def make_option(strike, right):
            c = Contract()
            c.symbol = "SPX"
            c.secType = "OPT"
            c.exchange = "SMART"
            c.currency = "USD"
            c.lastTradeDateOrContractMonth = today
            c.strike = strike
            c.right = right
            c.multiplier = "100"
            c.tradingClass = "SPXW" # Standard for 0DTE
            return c

        leg_contracts = [
            make_option(atm_strike - width, "P"), # Buy Wing
            make_option(atm_strike, "P"),         # Sell Body
            make_option(atm_strike, "C"),         # Sell Body
            make_option(atm_strike + width, "C")  # Buy Wing
        ]

        # In native API, we must request contract details to get conIds for BAG orders
        # For brevity in this logic, we use individual orders or assume conIds are known.
        # Here we place individual market orders for the legs.
        actions = ["BUY", "SELL", "SELL", "BUY"]
        
        for i in range(4):
            order = Order()
            order.action = actions[i]
            order.orderType = "MKT"
            order.totalQuantity = 1
            order.transmit = (i == 3) # Transmit only on the last leg
            self.app.placeOrder(self.app.next_order_id(), leg_contracts[i], order)

        self.app.active_trades = True
        self.app.strategy_triggered = True
        DiscordNotifier.send("🚀 Strategy Executed", f"Iron Butterfly opened at {atm_strike} strike.")

class PnLApp(EWrapper, EClient):
    """Core IBAPI Client handling all network events and data state."""
    def __init__(self):
        EClient.__init__(self, self)
        self.risk_manager = RiskManager(self)
        self.executor = ExecutionManager(self)
        self.next_id = 1
        self.positions = {}
        self.active_trades = False
        self.strategy_triggered = False
        self.vix_price = 0.0
        self.spx_price = 0.0
        self.done = False

    def next_order_id(self):
        oid = self.next_id
        self.next_id += 1
        return oid

    # --- API Callbacks ---
    def nextValidId(self, orderId: int):
        self.next_id = orderId
        logger.info("Connected to IBKR. Initializing subscriptions...")
        self.reqPnL(9001, ACCOUNT_ID, "")
        
        # VIX Subscription
        vix = Contract()
        vix.symbol = "VIX"
        vix.secType = "IND"
        vix.exchange = "CBOE"
        vix.currency = "USD"
        self.reqMktData(1001, vix, "", False, False, [])

        # SPX Subscription for ATM detection
        spx = Contract()
        spx.symbol = "SPX"
        spx.secType = "IND"
        spx.exchange = "CBOE"
        spx.currency = "USD"
        self.reqMktData(1002, spx, "", False, False, [])
        
        self.reqPositions()

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        self.risk_manager.process_pnl(daily_pnl=dailyPnL)

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4: # Last Price
            if reqId == 1001:
                self.vix_price = price
            elif reqId == 1002:
                self.spx_price = price

    def position(self, account, contract, position, avgCost):
        if account == ACCOUNT_ID:
            self.positions[contract.conId] = {
                'contract': contract,
                'symbol': contract.symbol,
                'qty': position
            }
            if position != 0: self.active_trades = True

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        if errorCode not in [2104, 2106, 2158]:
            logger.error(f"IBKR Error {errorCode}: {errorString}")

def api_loop(app):
    app.run()

if __name__ == "__main__":
    app = PnLApp()
    app.connect("127.0.0.1", IB_PORT, clientId=3)

    api_thread = threading.Thread(target=api_loop, args=(app,), daemon=True)
    api_thread.start()
    
    time.sleep(2)
    DiscordNotifier.send("🤖 Bot Online", f"Monitoring SPX 0DTE for {ACCOUNT_ID}")

    try:
        while not app.done:
            now = datetime.datetime.now().time()
            
            # 1. Time-Based Safety Exit
            if now >= EXIT_TIME and app.active_trades:
                app.executor.flatten_account(f"Standard Exit Time ({EXIT_TIME})")
            
            # 2. Iron Butterfly Strategy Entry
            # Criteria: Low VIX (Stable), Market Open > 1 hour, No active trades
            if not app.active_trades and not app.strategy_triggered:
                market_open_passed = datetime.datetime.now().time() > datetime.time(10, 30)
                
                if market_open_passed and 0 < app.vix_price < 20.0 and app.spx_price > 0:
                    # Round SPX price to nearest 5 for strike selection
                    atm_strike = round(app.spx_price / 5) * 5
                    app.executor.place_iron_butterfly(atm_strike)

            time.sleep(10) 
            
    except KeyboardInterrupt:
        logger.info("Keyboard Interrupt detected.")
    finally:
        app.disconnect()
        logger.info("Disconnected from IBKR.")