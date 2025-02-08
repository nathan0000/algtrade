from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.ticktype import TickTypeEnum
import threading
import time
from datetime import datetime, timedelta

class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextValidOrderId = None
        self.reqMarketDataType(3)
        self.spx_price = None

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
        self.request_market_data()
        self.start_strategy()

    def request_market_data(self):
        # Define the contract for SPY
        contract = Contract()
        contract.symbol = "SPX"
        contract.secType = "IND"
        contract.exchange = "CBOE"
        contract.currency = "USD"
     
        # Request real-time market data for SPY
        self.reqMarketDataType(3) # Set market data type to delayed
        self.reqMktData(self.nextOrderId, contract, "", False, False, [])

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson = ""):
        if advancedOrderRejectJson:
            print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString, "AdvancedOrderRejectJson:", advancedOrderRejectJson)
        else:
            print("Error:", reqId, errorCode, errorString)

    def historicalData(self, reqId, bar):
        # Handle historical data (not implemented in this example)
        pass

    def tickPrice(self, reqId, tickType, price, attrib):
        # Handle real-time price updates
        self.spx_price = price
        print(f"tickPrice, reqId: {reqId}, tickType: {TickTypeEnum.to_str(tickType)}, price: {price}, attrib: {attrib}")

    def start_strategy(self):
        # Start your strategy here
        threading.Thread(target=self.strategy).start()

    def strategy(self):
        while self.spx_price is None:
            print("SPX none price")
            time.sleep(1)

        print(self.spx_price)

        # Calculate 30 days implied move (you may need to replace this with your own calculation)
        implied_move_percent = 10  # Example: 10% implied move
        implied_move = self.spx_price * (implied_move_percent / 100)

        # Calculate strike prices for straddle and strangle
        straddle_strike = round(self.spx_price, 2)
        strangle_call_strike = round(self.spx_price + implied_move, 2)
        strangle_put_strike = round(self.spx_price - implied_move, 2)

        # Define contracts for straddle and strangle
        straddle_contract = Contract()
        straddle_contract.symbol = 'SPX'
        straddle_contract.secType = 'BAG'
        straddle_contract.exchange = 'CBOE'
        straddle_contract.lastTradeDateOrContractMonth = datetime.now().strftime('%Y%m%d')

        call_leg = ComboLeg()
        call_leg.conId = 0
        call_leg.ratio = 1
        call_leg.strike = straddle_strike
        call_leg.action = 'SELL'

        put_leg = ComboLeg()
        put_leg.conId = 0
        put_leg.ratio = 1
        put_leg.strike = straddle_strike
        put_leg.action = 'SELL'

        straddle_contract.comboLegs = [call_leg, put_leg]

        strangle_contract = Contract()
        strangle_contract.symbol = 'SPX'
        strangle_contract.secType = 'BAG'
        strangle_contract.lastTradeDateOrContractMonth = (datetime.now() + timedelta(days=30)).strftime('%Y%m%d')

        call_leg_strangle = ComboLeg()
        call_leg_strangle.conId = 0
        call_leg_strangle.ratio = 1
        call_leg_strangle.strike = strangle_call_strike
        call_leg_strangle.action = 'BUY'
        call_leg_strangle.exchange = 'CBOE'

        put_leg_strangle = ComboLeg()
        put_leg_strangle.conId = 0
        put_leg_strangle.ratio = 1
        put_leg_strangle.strike = strangle_put_strike
        put_leg_strangle.action = 'BUY'
        put_leg_strangle.exchange = 'CBOE'

        strangle_contract.comboLegs = [call_leg_strangle, put_leg_strangle]

        # Place orders for straddle and strangle
        straddle_order = Order()
        straddle_order.action = 'SELL'
        straddle_order.totalQuantity = 1
        straddle_order.orderType = 'MKT'

        strangle_order = Order()
        strangle_order.action = 'BUY'
        strangle_order.totalQuantity = 1
        strangle_order.orderType = 'MKT'

        self.placeOrder(self.nextValidOrderId, straddle_contract, straddle_order)
#        self.placeOrder(self.nextValidOrderId + 1, strangle_contract, strangle_order)

def run_loop():
    app.run()

# Connect to IBKR API
app = IBApp()
app.connect('127.0.0.1', 7497, clientId=1)  # Replace with your IBKR TWS/Gateway details

# Start the message loop in a separate thread
threading.Thread(target=run_loop).start()
