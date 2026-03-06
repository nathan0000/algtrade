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
IB_PORT = 4002        # 7497 for Paper, 7496 for Live
ACCOUNT_ID = "DU3232524"
STOP_LOSS_USD = -500.0
EXIT_TIME = datetime.time(15, 45)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- MANUAL CLASS DEFINITION FOR OrderCancel ---
# Since some IBAPI versions lack this, we define it here to prevent ImportErrors.
class OrderCancel:
    def __init__(self, manualOrderIndicator=1, extOperator="", manualOrderCancelTime=""):
        self.manualOrderIndicator = manualOrderIndicator # 1 for manual, 0 for automated
        self.extOperator = extOperator
        self.manualOrderCancelTime = manualOrderCancelTime # YYYYMMDD-HH:mm:ss

class VWAPManager:
    """Calculates VWAP using either real-time ticks or historical fallback."""
    def __init__(self, symbol):
        self.symbol = symbol
        self.cum_volume = 0.0
        self.cum_pv = 0.0
        self.last_price = 0.0
        self.current_vwap = 0.0
        self.historical_vwap = 0.0

    def update_price(self, price):
        self.last_price = price

    def update_size(self, size):
        if self.last_price > 0 and size > 0:
            f_size = float(size)
            self.cum_pv += (self.last_price * f_size)
            self.cum_volume += f_size
            self.current_vwap = self.cum_pv / self.cum_volume

    def get_effective_vwap(self):
        """Returns real-time VWAP if available, otherwise historical baseline."""
        return self.current_vwap if self.current_vwap > 0 else self.historical_vwap

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
        if daily_pnl <= STOP_LOSS_USD and self.app.active_trades:
            logger.warning(f"STOP LOSS HIT: {daily_pnl}")
            self.app.executor.flatten_account("GLOBAL STOP LOSS BREACH")

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
        
        # We define the OrderCancel object manually based on CME Rule 576 requirements
        oc = OrderCancel()
        oc.manualOrderIndicator = 0 # This bot is automated
        oc.extOperator = "BOT_V1"
        
        try:
            # Most modern IBAPI versions require this argument
            self.app.reqGlobalCancel(oc)
        except TypeError:
            # Fallback for very old versions that take no arguments
            try:
                self.app.reqGlobalCancel()
            except Exception as inner_e:
                logger.error(f"Critical error during reqGlobalCancel: {inner_e}")
        
        self.app.reqPositions()
        time.sleep(1.5) 
        
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
                logger.info(f"Liquidation order sent for {pos_data['symbol']} {qty} units.")
        
        self.app.active_trades = False
        DiscordNotifier.send("⚠️ LIQUIDATION TRIGGERED", f"Reason: {reason}", 0x000000)

    def place_iron_butterfly(self, atm_strike, width=20):
        logger.info(f"Executing Iron Butterfly at Strike {atm_strike}")
        today = datetime.datetime.now().strftime('%Y%m%d')
        
        def make_option(strike, right):
            c = Contract()
            c.symbol = "SPX"; c.secType = "OPT"; c.exchange = "SMART"; c.currency = "USD"
            c.lastTradeDateOrContractMonth = today; c.strike = strike; c.right = right
            c.multiplier = "100"; c.tradingClass = "SPXW"
            return c

        leg_contracts = [
            make_option(atm_strike - width, "P"),
            make_option(atm_strike, "P"),
            make_option(atm_strike, "C"),
            make_option(atm_strike + width, "C")
        ]

        actions = ["BUY", "SELL", "SELL", "BUY"]
        for i in range(4):
            order = Order()
            order.action = actions[i]; order.orderType = "MKT"; order.totalQuantity = 1
            order.transmit = (i == 3)
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
        self.spx_vwap_manager = VWAPManager("SPX")
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

    def nextValidId(self, orderId: int):
        self.next_id = orderId
        logger.info("Connected. Subscribing to data...")
        self.reqPnL(9001, ACCOUNT_ID, "")
        
        vix = Contract()
        vix.symbol = "VIX"; vix.secType = "IND"; vix.exchange = "CBOE"; vix.currency = "USD"
        self.reqMktData(1001, vix, "", False, False, [])

        spx = Contract()
        spx.symbol = "SPX"; spx.secType = "IND"; spx.exchange = "CBOE"; spx.currency = "USD"
        self.reqMktData(1002, spx, "", False, False, [])
        
        self.fetch_historical_vwap(spx)
        self.reqPositions()

    def fetch_historical_vwap(self, contract):
        logger.info("Calculating historical VWAP from previous day...")
        self.reqHistoricalData(2001, contract, "", "1 D", "1 min", "TRADES", 1, 1, False, [])

    def historicalData(self, reqId, bar):
        if reqId == 2001:
            vol = float(bar.volume)
            self.spx_vwap_manager.cum_pv += (bar.close * vol)
            self.spx_vwap_manager.cum_volume += vol

    def historicalDataEnd(self, reqId, start, end):
        if reqId == 2001:
            if self.spx_vwap_manager.cum_volume > 0:
                self.spx_vwap_manager.historical_vwap = self.spx_vwap_manager.cum_pv / self.spx_vwap_manager.cum_volume
                logger.info(f"Historical VWAP Baseline set: {self.spx_vwap_manager.historical_vwap:.2f}")

    def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
        self.risk_manager.process_pnl(daily_pnl=dailyPnL)

    def tickPrice(self, reqId, tickType, price, attrib):
        if price <= 0: return
        if reqId == 1001 and tickType == 4:
            self.vix_price = price
        elif reqId == 1002:
            if tickType in [1, 2, 4]:
                self.spx_price = price
                self.spx_vwap_manager.update_price(price)

    def tickSize(self, reqId, tickType, size):
        if reqId == 1002 and tickType == 8:
            self.spx_vwap_manager.update_size(float(size))

    def position(self, account, contract, position, avgCost):
        if account == ACCOUNT_ID:
            self.positions[contract.conId] = {'contract': contract, 'symbol': contract.symbol, 'qty': position}
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
    DiscordNotifier.send("🤖 Bot Online", f"Monitoring SPX 0DTE (Historical VWAP Enabled)")

    try:
        while not app.done:
            now = datetime.datetime.now().time()
            effective_vwap = app.spx_vwap_manager.get_effective_vwap()
            
            print(f"SPX: {app.spx_price:.2f} | VWAP: {effective_vwap:.2f} | VIX: {app.vix_price:.2f}", end='\r')
            
            if now >= EXIT_TIME and app.active_trades:
                app.executor.flatten_account(f"Standard Exit Time ({EXIT_TIME})")
            
            if not app.active_trades and not app.strategy_triggered:
                market_ready = now > datetime.time(10, 30)
                if market_ready and 0 < app.vix_price < 20.0 and app.spx_price > 0 and effective_vwap > 0:
                    if abs(app.spx_price - effective_vwap) < 3.0: 
                        atm_strike = round(app.spx_price / 5) * 5
                        app.executor.place_iron_butterfly(atm_strike)

            time.sleep(5) 
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        app.disconnect()