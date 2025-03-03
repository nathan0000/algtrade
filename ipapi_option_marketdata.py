from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import Contract
import threading
from trading_dates import *
import time
import pandas as pd

class TradeApp(EWrapper, EClient): 
    def __init__(self): 
        EClient.__init__(self, self)
        self.closePrice: int = -1
        self.lastPrice: int = -1
        self.highPrice: int = -1
        self.lowPrice: int = -1
        self.openPrice: int = -1
        self.tradingVolume: int = -1
        self.job_done = threading.Event() 
        self.optContractDetails = pd.DataFrame(columns=["conId", "symbol", "secType",  "expiration", "strike", "right", "exchange", "currency", "tradingClass"])
        self.optTraded = pd.DataFrame(columns=["conId", "openPrice", "closePrice",  "lowPrice", "highPrice", "lastPrice", "tradingVolume"])
        self.optPriceGreeks = pd.DataFrame(columns=["conId", "tickType", "impliedVol", "delta", "optPrice", "pvDividend", "gamma", "vega", "theta", "undPrice"])  

    def nextValidId(self, orderId):
        self.orderId = orderId
    
    def nextId(self):
        self.orderId += 1
        return self.orderId

    def contractDetails(self, reqId, contractDetails):
        attrs = vars(contractDetails)
#        print("\n".join(f"{name}: {value}" for name,value in attrs.items()))
        print(contractDetails.contract)
        if contractDetails.contract.secType == "FOP":
            self.optContractDetails.loc[len(self.optContractDetails)] = [contractDetails.contract.conId,
                                                                        contractDetails.contract.symbol,
                                                                        contractDetails.contract.secType,
                                                                        contractDetails.contract.lastTradeDateOrContractMonth,
                                                                        contractDetails.contract.strike,
                                                                        contractDetails.contract.right,
                                                                        contractDetails.contract.exchange,
                                                                        contractDetails.contract.currency,
                                                                        contractDetails.contract.tradingClass]
    
    def contractDetailsEnd(self, reqId):
        self.job_done.set()
        print("End of contract details")

    def tickGeneric(self, reqId: TickerId, tickType: TickType, value: float):
        print("TickGeneric. TickerId:", reqId, "TickType:", tickType, "Value:", floatMaxString(value))

    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, attrib: TickAttrib):
        print("TickPrice. TickerId:", reqId, tickType, "TickerPrice:", price, attrib)
        if tickType == 9:
            self.closePrice = price
        if tickType == 4:
            self.lastPrice = price
        if tickType == 14:
            self.openPrice = price
        if tickType == 6:
            self.highPrice = price
        if tickType == 7:
            self.lowPrice = price
        if tickType == 8:
            self.tradingVolume = price
        self.optTraded.loc[len(self.optTraded)] = [int(reqId), self.openPrice, self.closePrice, self.lowPrice, self.highPrice, self.lastPrice, self.tradingVolume]
        
    def tickSize(self, reqId: TickerId, tickType: TickType, size: Decimal):
        print("TickSize. TickerId:", reqId, "TickType:", tickType, "Size: ", decimalMaxString(size))

    def tickString(self, reqId: TickerId, tickType: TickType, value: str):
        #print("TickString. TickerId:", reqId, "Type:", tickType, "Value:", value)
        pass

    def tickReqParams(self, tickerId:int, minTick:float, bboExchange:str, snapshotPermissions:int):
        print("TickReqParams. TickerId:", tickerId, "MinTick:", floatMaxString(minTick), "BboExchange:", bboExchange, "SnapshotPermissions:", intMaxString(snapshotPermissions))
        pass

    def tickOptionComputation(self, reqId: TickerId, tickType: TickType, tickAttrib: int, impliedVol: float, delta: float, optPrice: float, pvDividend: float, gamma: float, vega: float, theta: float, undPrice: float):
        print("TickOptionComputation. TickerId:", reqId, "TickType:", tickType, "TickAttrib:", intMaxString(tickAttrib), "ImpliedVolatility:", floatMaxString(impliedVol), "Delta:", floatMaxString(delta), "OptionPrice:", floatMaxString(optPrice), "pvDividend:", floatMaxString(pvDividend), "Gamma: ", floatMaxString(gamma), "Vega:", floatMaxString(vega), "Theta:", floatMaxString(theta), "UnderlyingPrice:", floatMaxString(undPrice))
        self.optPriceGreeks.loc[len(self.optPriceGreeks)] = [int(reqId), tickType, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice]  

def websocket_con():
    app.run()
    
app = TradeApp()      
app.connect("127.0.0.1", 7497, clientId=1)

con_thread = threading.Thread(target=websocket_con, daemon=True)
con_thread.start()

time.sleep(1) 

today, todaytime, nextTradingDay = getDate()
print(f"Today: {today}, Next Business Day: {nextTradingDay}")

contract = Contract()
tickerSymbol = "ES"
tickerSecType = "FUT"
tickerExchange = "CME"
tickerCurrency = "USD"
contract.symbol = tickerSymbol
contract.secType = tickerSecType
contract.exchange = tickerExchange
contract.currency = tickerCurrency
contract.lastTradeDateOrContractMonth = 202503

while (app.lastPrice == -1) and (app.closePrice == -1):
    app.reqMarketDataType(3)
    app.reqMktData(app.nextId(), contract, "", True, False, [])
    time.sleep(2)

optContract = Contract()
optContract.symbol = "ES"
optContract.secType = "FOP"
optContract.exchange = "CME"
optContract.currency = tickerCurrency
optContract.lastTradeDateOrContractMonth = nextTradingDay
#optContract.right = "C"
#optContract.multiplier = 50
print(f'last price: {app.lastPrice}, close price: {app.closePrice}')

if app.lastPrice != -1:
    last_price = app.lastPrice
else:
    last_price = app.closePrice

last_price = int(last_price - last_price % 5)
strikes = [x for x in range(last_price - 120, last_price + 120, 5)]
print(f'strikes: {strikes}')

for strike in strikes:
    app.job_done.clear()
    optContract.strike = strike
    app.reqContractDetails(app.nextId(), optContract)
    app.job_done.wait()
print(app.optContractDetails)

app.job_done.clear()
mycontract = Contract()
for index, row in app.optContractDetails.iterrows():
#    if row['secType'] == "FOP":
    mycontract.symbol = row['symbol']
    mycontract.secType = row['secType']
    mycontract.lastTradeDateOrContractMonth = row['expiration']
    mycontract.strike = row['strike']
    mycontract.right = row['right']
    mycontract.exchange = row['exchange']
    mycontract.currency = row['currency']
    mycontract.tradingClass = row['tradingClass']
    int_conid = int(row['conId'])

    app.reqMarketDataType(3)
    app.reqMktData(int_conid, mycontract, "", True, False, [])
    print(app.optPriceGreeks)
    time.sleep(6)

optMarketData = app.optContractDetails.merge(app.optPriceGreeks, on="conId", suffixes=('_contract', '_traded'))
#optMarketData = optMarketData.merge(app.optPriceGreeks, on="conId", suffixes=('_traded', '_greeks'))
filename = f"optMarketData_{todaytime}.csv"
optMarketData.to_csv(filename, index=False)
app.optTraded.to_csv(f"optTraded_{todaytime}.csv", index=False)
app.optContractDetails.to_csv(f"optContractDetails_{todaytime}.csv", index=False)
time.sleep(3)
app.disconnect()