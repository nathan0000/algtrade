from ibapi.client import EClient
from ibapi.common import BarData, TagValueList, TickerId
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import pandas as pd
import logging
import threading
import queue
import time

tickers = ["AAPL", "TSLA", "MSFT"]

class OptionApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.data = {}
        self.df_data = {}
        self.underlyingPrice = {}
        self.atmCallOptions = {}
        self.atmPutOptions = {}
        self.data_queue_dict = {}
        self.open_list = list()
        self.high_list = list()
        self.low_list = list()
        self.close_list = list()
        self.volume_list = list()
        self.spy_price = None

    def tickPrice(self, reqId, tickType, price, attrib):
        super().tickPrice(reqId, tickType, price, attrib)
        self.lastPrice[reqId] = price

    def contractDetails(self, reqId, contractDetails):
        logging.Debug("reqID: {}, contract: {}".format(reqId, contractDetails))
        if reqId not in self.data:
            self.data[reqId] = [{"expiry": contractDetails.contract.lastTradeDataorContractMonth.expiry,
                                "strike": contractDetails.contract.strike,
                                "call/put": contractDetails.contract.right,
                                "symbol": contractDetails.contract.localSymbol}]
    
    def contractDetailsEnd(self, reqId):
        super().contractDetailsEnd(reqId)
        logging.Debug("ContractDetailsEnd. ReqId: {}".format(reqId))
        self.df_data[tickers[reqId]] = pd.DataFrame(self.data[reqId])

    def reqHistoricalData(self, reqId: TickerId, contract: Contract, endDateTime: str, durationStr: str, barSizeSetting: str, whatToShow: str, useRTH: int, formatDate: int, keepUpToDate: bool, chartOptions: TagValueList):
        super().reqHistoricalData(reqId, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate, keepUpToDate, chartOptions)
    
        if reqId not in self.data_queue_dict.keys():
            logging.Debug("adding queue for req {}".format(reqId))
            self.data_queue_dict[reqId] = queue.Queue()

        return reqId

    def historicalData(self, reqId: int, bar: BarData):
        self.data_queue_dict[reqId].put(bar)
    
    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
#        self.place_initial_orders()
        self.request_market_data()

    def request_market_data(self):
        # Define the contract for SPY
        contract = Contract()
        contract.symbol = "SPY"
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        # Request real-time market data for SPY
        self.reqMarketDataType(1) # Set market data type to delayed
        self.reqMktData(self.nextOrderId, contract, "", False, False, [])

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4: # 4 correspnds to last trade price
            self.spy_price = price
            print(f"Received SPY price update: {self.spy_price}")

    def place_initial_orders(self):
        # Define the contract for SPY options
        contract = Contract()
        contract.symbol = "SPY"
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        # Define the at-the-money and out-of-the-money strike prices
        atm_strike = self.spy_price  # Replace with the actual ATM strike price
        otm_strike = 0.007 * atm_strike  # 0.7% out-of-the-money
        
        # Create orders for shorting ATM call and put options
        short_call_order = self.create_option_order(contract, atm_strike, "CALL", "SELL")
        short_put_order = self.create_option_order(contract, atm_strike, "PUT", "SELL")
        
        # Create orders for buying back OTM call and put options
        buy_back_call_order = self.create_option_order(contract, otm_strike, "CALL", "BUY")
        buy_back_put_order = self.create_option_order(contract, otm_strike, "PUT", "BUY")
        
        # Attach order conditions to roll over long call or put option
        roll_over_call_order = self.create_option_order(contract, atm_strike, "CALL", "BUY")
        roll_over_put_order = self.create_option_order(contract, atm_strike, "PUT", "BUY")
        roll_over_call_order.conditions = [self.create_condition("Price", "SPY", "ASK", "<=", otm_strike)]
        roll_over_put_order.conditions = [self.create_condition("Price", "SPY", "BID", ">=", otm_strike)]
        
        # Place orders
        self.place_order(short_call_order)
        self.place_order(short_put_order)
        self.place_order(buy_back_call_order)
        self.place_order(buy_back_put_order)
        self.place_order(roll_over_call_order)
        self.place_order(roll_over_put_order)

    def create_option_order(self, contract, strike, right, action):
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = 1
        order.lmtPrice = 0.01  # Replace with your desired limit price
        order.transmit = False

        contract.strike = strike
        contract.right = right
        order.orderRef = f"{action}_{right}_{strike}"
        order.orderId = self.nextOrderId
        self.nextOrderId += 1
        return order

    def create_condition(self, type, conId, exchange, condition, value):
        condition = {
            "type": type,
            "conId": conId,
            "exchange": exchange,
            "condition": condition,
            "value": value
        }
        return condition

    def place_order(self, order):
        self.client.placeOrder(order.orderId, contract, order)

def slectedOption(local_symbol, sec_type="OPT", currency="USD", exchange="SMART"):
    contract = Contract()
    contract.symbol = local_symbol.split("  ")[0]
    contract.secType = sec_type
    contract.currency = currency
    contract.exchange = exchange
    contract.right = local_symbol.split("  ")[1][6]
    contract.lastTradeDateOrContractMonth = "20" + local_symbol.split("  ")[1][:6]
    contract.strike = float(local_symbol.split("  ")[1][7:])/1000
    return contract

def usOption(symbol, sec_type="OPT", currency="USD", exchange="SMART"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.currency = currency
    contract.exchange = exchange
    contract.lastTradeDateOrContractMonth = time.strftime("%Y%m")
    return contract

def main():
    app = OptionApp()
    app.connect("127.0.0.1", 7497, clientId = 1)

    def websocket_con():
        app.run()

    contract_event = threading.Event()

    con_thread = threading.Thread(target=websocket_con, daemon=True)
    con_thread.start()

    for ticker in tickers:
        contract_event.clear()
        app.reqContractDetails(tickers.index(ticker), usOption(ticker))
        contract_event.wait()

if __name__ == "__main__":
    main()
