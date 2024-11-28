from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.order_condition import OrderCondition, Create
import time, threading, logging
from ibapi.ticktype import TickTypeEnum
from ibapi.tag_value import *
import pandas as pd
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone

tickers = ["AAPL", "TSLA", "MSFT"]

global optContract, optPositon
optContract, optPosition = {}, {}

class ibContract(EClient,EWrapper):
    def __init__(self, ticker):
        EClient.__init__(self, self)
        self.tickerSymbol = ticker
        self.mycontract = Contract()
        self.mycontract.symbol = ticker
        self.mycontract.secType = "IND"
        self.mycontract.exchange = "CBOE"
        self.mycontract.currency = "USD"
        self.symbol_price = 0
    #    self.utcnow = datetime.now(tzinfo=timezone.utc)
        self.us_east = ZoneInfo("America/New_York")
        self.usnow = datetime.now().astimezone(self.us_east)
        self.usdatestr = self.usnow.strftime('%Y-%m-%d')
        print(f"us now: {self.usnow}, us date string: {self.usdatestr}")
        self.df_data = {}
    
    def nextValidId(self, orderId: int):
        print(f"next valid id: {orderId}")
        self.nextOrderId = orderId
#        self.getPosition()
#        threading.Timer(5, self.stop).start()
        self.reqMarketDataType(4)
        self.reqMktData(orderId, self.mycontract, "", False, False, [])

    def updatePortfolio(self, contract: Contract, position: Decimal, marketPrice: float, marketValue: float, averageCost: float, unrealizedPNL: float, realizedPNL: float, accountName: str):
        print("UpdatePortfolio.", "Symbol:", contract.symbol, "SecType:", contract.secType, "localSymbol:", contract.localSymbol,
              "Position:", position, "MarketPrice:", marketPrice, "MarketValue:", marketValue, "AverageCost:", averageCost,
              "UnrealizedPNL:", unrealizedPNL, "RealizedPNL:", realizedPNL, "AccountName:", accountName)
        global optPosition
        if contract.symbol in ["SPX", "XSP", "RUT"]: #only return index option positions
            optPosition[accountName] = [contract, position, marketPrice, averageCost, unrealizedPNL]
        for i in optPosition:
            contract = optPosition[i][0]
            position = decimalMaxString(optPosition[i][1])
            avgCost = floatMaxString(optPosition[i][3])
            print(f"Account: {i}; Contract: {contract}, Position: {position}; Average Cost: {avgCost}")
        
    def updateAccountTime(self, timeStamp: str):
        # Cancel account update if 10 minutes before market close
        if datetime.now().strftime("%H%M%S") > "155000":
            self.reqAccountUpdates(False, "")
            self.done = True
            self.disconnect()

    def getPosition(self):
        # Account number can be omitted when using reqAccountUpdates with single account structure
        self.reqAccountUpdates(True, "")

    def stop(self):
        self.reqAccountUpdates(False, "")
        self.done = True
        self.disconnect()   

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 68:
            self.symbol_price = price
            print(f"symbol: {self.tickerSymbol} price: {price}")
                    
            implied_move_percent = 10  # Example: 10% implied move
            implied_move = self.symbol_price * (implied_move_percent / 100)

            # Calculate strike prices for straddle and strangle
            straddle_strike = round(self.symbol_price/5, 0) * 5
            strangle_call_strike = round((self.symbol_price + implied_move)/10, 0)*10
            strangle_put_strike = round((self.symbol_price - implied_move)/10, 0)*10

            # Define contracts for straddle and strangle
            straddle_contract = Contract()
            straddle_contract.symbol = self.tickerSymbol
            straddle_contract.secType = 'OPT'
            straddle_contract.currency = 'USD'
            straddle_contract.exchange = 'CBOE'
            straddle_contract.strike = straddle_strike
            straddle_contract.lastTradeDateOrContractMonth = self.usnow.strftime('%Y%m%d')
    #        straddle_contract.lastTradeDateOrContractMonth = "20231227"
            print(f"contract details: {straddle_contract}")
            time.sleep(3)
            for reqId, right in enumerate(['C', 'P']):
                straddle_contract.right = right
                self.reqContractDetails(reqId, straddle_contract)

    def error(self, reqId, errorCode: int, errorString: str, advancedOrderRejectJson = ""):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        if advancedOrderRejectJson:
            print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString, "AdvancedOrderRejectJson:", advancedOrderRejectJson)
        else:
            print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString)    

    def contractDetails(self, reqId, contractDetails):
#        global optContract
        print("reqID: {}, contract: {}".format(reqId, contractDetails))
        if reqId not in optContract:
            optContract[reqId] = [{"contractId": contractDetails.contract.conId,
                                 "securityId": contractDetails.contract.secId,
                                "expiry": contractDetails.contract.lastTradeDateOrContractMonth,
                                "strike": contractDetails.contract.strike,
                                "call/put": contractDetails.contract.right,
                                "localSymbol": contractDetails.contract.localSymbol,
                                "symbol": contractDetails.contract.symbol}]

    def contractDetailsEnd(self, reqId: int):
#        logging.Debug("ContractDetailsEnd. ReqId: {}".format(reqId))
        if len(optContract) >= 2:
            for reqId in range(len(optContract)):
                print(f"reqId: {reqId}, contract fields: {optContract[reqId]}")
            self.place_Order()
        
    def place_Order(self):
        straddlecontract = Contract()
        straddlecontract.symbol = optContract[0][0]["symbol"]
        straddlecontract.secType = "BAG"
        straddlecontract.exchange = "SMART"
        straddlecontract.currency = "USD"
        straddlecontract.comboLegs = []

        midPrice_tastytrade = -1.5
        for reqId in range(2):
            leg = ComboLeg()
            leg.conId = optContract[reqId][0]["contractId"]
            leg.ratio = 1
            leg.action = "SELL"
            leg.exchange = "SMART"
            straddlecontract.comboLegs.append(leg)

        print(f"straddle contract to be placed: {straddlecontract}")

        # place market order and bracket tp of 25%
        straddleorder = Order()
        straddleorder.orderId = self.nextOrderId
        straddleorder.action = "BUY"
        straddleorder.orderType = "LMT"
        straddleorder.lmtPrice = midPrice_tastytrade
        straddleorder.totalQuantity = 1
        straddleorder.tif = "GTC"
        within2hrsclose = datetime.fromisoformat(self.usdatestr + "T14:00:00-06:00")
        self.addTimeCondition(straddleorder, within2hrsclose, True)
#        straddleorder.smartComboRoutingParams = []
#        straddleorder.smartComboRoutingParams.append(TagValue('NonGuaranteed', '1'))
        straddleorder.transmit = False
        print(f"straddle order: {straddleorder}")

        takeprofit = Order()
        takeprofit.orderId = straddleorder.orderId + 1
        takeprofit.parentId = straddleorder.orderId
        takeprofit.action = "SELL"
        takeprofit.orderType = "LMT"
        takeprofit.lmtPrice = round(straddleorder.lmtPrice * (1 - 0.25), 0)
        takeprofit.totalQuantity = 1
        takeprofit.transmit = True
        print(f"takeprofit order: {takeprofit}")

        self.placeOrder(straddleorder.orderId, straddlecontract, straddleorder)
        self.placeOrder(takeprofit.orderId, straddlecontract, takeprofit)

    def addTimeCondition(self, order: Order, end_datetime, orth=True):
        time_condition = Create(OrderCondition.Time)
        time_condition.time = end_datetime.astimezone(timezone.utc).strftime('%Y%m%d-%H:%M:%S')
        time_condition.isMore = False
        time_condition.isConjunctionConnection = "AND"
        order.conditions.append(time_condition)
        order.conditionsIgnoreRth = orth
        return order

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        print(f"openOrder: {orderId}, contract: {contract}, Order: {order}, Maintenance Margin: {orderState.maintMarginChange}")

    def orderStatus(self, orderId: OrderId, status: str, filled: float, remaining: float, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
        print(f"order Status. orderId: {orderId}, status: {status}, filled: {filled}, remaining: {remaining}, avgFillPrice: {avgFillPrice}, permId: {permId}, parentId: {parentId}, lastFillPrice: {lastFillPrice}, clientId: {clientId}, whyHeld: {whyHeld}, mktCapPrice: {mktCapPrice}")

    def execDetails(self, reqId:int, contract: Contract, execution: Execution):
        print(f"execDetails. reqId: {reqId}, contract: {contract}, execution: {execution}")

"""
    def strategy(self):
        while self.symbol_price is None:
            print(f"symbol price is None")
            time.sleep(1)
        print(f"symbol price: {self.symbol_price}")

                # Calculate 30 days implied move (you may need to replace this with your own calculation)
        implied_move_percent = 10  # Example: 10% implied move
        implied_move = self.symbol_price * (implied_move_percent / 100)

        # Calculate strike prices for straddle and strangle
        straddle_strike = round(self.symbol_price, 2)
        strangle_call_strike = round(self.symbol_price + implied_move, 2)
        strangle_put_strike = round(self.symbol_price - implied_move, 2)

        # Define contracts for straddle and strangle
        straddle_contract = Contract()
        straddle_contract.symbol = self.tickerSymbol
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
        self.reqContractDetails(self.orderId, straddle_contract)

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

#        self.reqContractDetails(self.orderId, strangle_contract)
"""
def usOption(symbol, sec_type="OPT", currency="USD", exchange="SMART"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.currency = currency
    contract.exchange = exchange
    contract.lastTradeDateOrContractMonth = time.now().strftime("%Y%m%d")
    return contract

def usStock(symbol, sec_type="STK", currency="USD", exchange="SMART"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.currency = currency
    contract.exchange = exchange
    contract.lastTradeDateOrContractMonth = time.now().strftime("%Y%m%d")
    return contract

def main():
    app = ibContract("SPX")
    app.connect("127.0.0.1", 7497, clientId = 1)
    app.run()
    
    contract_event = threading.Event()
"""
    for ticker in tickers:
#        contract_event.clear()
        print(f"ticker: {ticker}")
        app.reqContractDetails(tickers.index(ticker), usStock(ticker))
        print(f"end calling request contract details")
#        contract_event.wait()

    app.sleep(1)
    app.disconnect()
"""
    
if __name__ == "__main__":
    main()