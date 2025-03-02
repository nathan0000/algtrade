from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.tag_value import TagValue
import threading, time
import pandas as pd
from trading_dates import *
from techAnalysis.taobjects import Indicators

class OptionOrderApp(EClient, EWrapper):
  def __init__(self):
    EClient.__init__(self, self)
    self.job_done = threading.Event() 
    self.optContractDetails = pd.DataFrame(columns=["conId", "symbol", "secType",  "expiration", "strike", "right", "exchange", "currency", "tradingClass"])
        
  def nextValidId(self, orderId: OrderId):
    self.nextOrderId = orderId
    print("I have nextValidId", self.nextOrderId)  

  def contractDetails(self, reqId, contractDetails):
    attrs = vars(contractDetails)
    print("\n".join(f"{name}: {value}" for name,value in attrs.items()))
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

  def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
    print(f"openOrder. orderId: {orderId}, contract: {contract}, order: {order}")

  def orderStatus(self, orderId: OrderId, status: str, filled: Decimal, remaining: Decimal, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
    print(f"orderId: {orderId}, status: {status}, filled: {filled}, remaining: {remaining}, avgFillPrice: {avgFillPrice}, permId: {permId}, parentId: {parentId}, lastFillPrice: {lastFillPrice}, clientId: {clientId}, whyHeld: {whyHeld}, mktCapPrice: {mktCapPrice}")

  def execDetails(self, reqId: int, contract: Contract, execution: Execution):
    print(f"reqId: {reqId}, contract: {contract}, execution: {execution}")

def optionContractDetails(client, closePrice: int, nextTradingDay, symbol="ES"):
  optContract = Contract()
  optContract.symbol = symbol
  optContract.secType = "FOP"
  optContract.currency = "USD"
  optContract.primaryExchange = "CME"
  optContract.exchange = "SMART"
  optContract.lastTradeDateOrContractMonth = nextTradingDay
  for strike in range(closePrice-50, closePrice+50, 5):
    optContract.strike = strike
    client.job_done.clear()
    client.reqContractDetails(client.nextOrderId, optContract)
    client.job_done.wait()
  print(f'optContractDetails: {client.optContractDetails}')
  client.optContractDetails.to_csv("optContractDetails.csv")

def optionStrategy(client, nextTradingDay, symbol="ES", strategy="ironCondor", quantity=1, sellAction="SELL", orderType="LMT", buyAction="BUY", price=0.0):
  
  shortPutContract = Contract()
  shortPutContract.symbol = symbol
  shortPutContract.secType = "FOP"
  shortPutContract.currency = "USD"
  shortPutContract.exchange = "CME"
  #shortPutContract.exchange = "SMART"
  shortPutContract.lastTradeDateOrContractMonth = nextTradingDay
  shortPutContract.strike = 5825
  shortPutContract.right = "P"
  client.reqContractDetails(client.nextOrderId, shortPutContract)
  time.sleep(1)

  shortCallContract = Contract()
  shortCallContract.symbol = symbol
  shortCallContract.secType = "FOP"
  shortCallContract.currency = "USD"
  shortCallContract.primaryExchange = "CME"
  shortCallContract.exchange = "SMART"
  shortCallContract.lastTradeDateOrContractMonth = nextTradingDay
  shortCallContract.strike = 5925
  shortCallContract.right = "C"
  client.reqContractDetails(client.nextOrderId, shortCallContract)
  time.sleep(1)

  longPutContract = Contract()
  longPutContract.symbol = symbol
  longPutContract.secType = "FOP"
  longPutContract.currency = "USD"
  longPutContract.primaryExchange = "CME"
  longPutContract.exchange = "SMART"
  longPutContract.lastTradeDateOrContractMonth = nextTradingDay
  longPutContract.strike = 5775
  longPutContract.right = "P"
  client.reqContractDetails(client.nextOrderId, longPutContract)
  time.sleep(1)

  longCallContract = Contract()
  longCallContract.symbol = symbol
  longCallContract.secType = "FOP"
  longCallContract.currency = "USD"
  longCallContract.primaryExchange = "CME"
  longCallContract.exchange = "SMART"
  longCallContract.lastTradeDateOrContractMonth = nextTradingDay
  longCallContract.strike = 5975
  longCallContract.right = "C"
  client.reqContractDetails(client.nextOrderId, longCallContract)
  time.sleep(1)
  print(f'optcontractDetails: {client.optContractDetails}')

  if strategy == "ironCondor":   
    optContract = Contract()
    optContract.symbol = symbol
    optContract.secType = "BAG"
    optContract.currency = "USD"
    optContract.primaryExchange = "CME"
    optContract.exchange = "SMART"

    leg1 = ComboLeg()
    leg1.conId = client.optContractDetails.loc[client.optContractDetails["strike"] == 5825]["conId"].item()
    leg1.ratio = 1
    leg1.action = "SELL"
    leg1.exchange = client.optContractDetails.loc[client.optContractDetails["strike"] == 5825]["exchange"].item()
    print(f'leg1 conId: {leg1.conId}')

    leg3 = ComboLeg()
    leg3.conId = client.optContractDetails.loc[client.optContractDetails["strike"] == 5975]["conId"].item()
    leg3.ratio = 1
    leg3.action = "SELL"
    leg3.exchange = client.optContractDetails.loc[client.optContractDetails["strike"] == 5975]["exchange"].item()
    print(f'leg3 conId: {leg3.conId}')

    leg2 = ComboLeg()
    leg2.conId = client.optContractDetails.loc[client.optContractDetails["strike"] == 5775]["conId"].item()
    leg2.ratio = 1
    leg2.action = "BUY"
    leg2.exchange = client.optContractDetails.loc[client.optContractDetails["strike"] == 5775]["exchange"].item()
    print(f'leg2 conId: {leg2.conId}')

    leg4 = ComboLeg()
    leg4.conId = client.optContractDetails.loc[client.optContractDetails["strike"] == 5975]["conId"].item()
    leg4.ratio = 1
    leg4.action = "BUY"
    leg4.exchange = client.optContractDetails.loc[client.optContractDetails["strike"] == 5975]["exchange"].item()
    print(f'leg4 conId: {leg4.conId}')

    optContract.comboLegs = []
    optContract.comboLegs.append(leg1)
    optContract.comboLegs.append(leg2)
    optContract.comboLegs.append(leg3)
    optContract.comboLegs.append(leg4)

  price = -2.8
  optOrder = Order()
  optOrder.orderId = client.nextOrderId
  optOrder.action = sellAction
  optOrder.totalQuantity = quantity
  optOrder.orderType = orderType
  optOrder.lmtPrice = price
  optOrder.tif = "DAY"
  optOrder.transmit = False
#  optOrder.smartComboRoutingParams = []
#  optOrder.smartComboRoutingParams.append(TagValue('NonGuaranteed', '1'))
  print(f'optOrder: {optOrder}')

  takeProfit = price * 0.5
  takeProfitOrder = Order()
  takeProfitOrder.orderId = client.nextOrderId + 1
  takeProfitOrder.parentId = optOrder.orderId
  takeProfitOrder.action = buyAction
  takeProfitOrder.totalQuantity = quantity
  takeProfitOrder.orderType = orderType
  takeProfitOrder.lmtPrice = takeProfit
  takeProfitOrder.tif = "GTC"
  takeProfitOrder.transmit = False
  print(f'takeProfitOrder: {takeProfitOrder}')  

  stopLoss = price * 1.3
  stopLossOrder = Order()
  stopLossOrder.orderId = client.nextOrderId + 2
  stopLossOrder.parentId = optOrder.orderId
  stopLossOrder.action = buyAction
  stopLossOrder.totalQuantity = quantity
  stopLossOrder.orderType = orderType
  stopLossOrder.lmtPrice = stopLoss
  stopLossOrder.tif = "GTC"
  stopLossOrder.transmit = True
  print(f'stopLossOrder: {stopLossOrder}')

  client.placeOrder(optOrder.orderId, optContract, optOrder)
  client.placeOrder(takeProfitOrder.orderId, optContract, takeProfitOrder)
  client.placeOrder(stopLossOrder.orderId, optContract, stopLossOrder)

def websocket_con():
    app.run()
    
app = OptionOrderApp()      
app.connect("127.0.0.1", 7497, clientId=1)

con_thread = threading.Thread(target=websocket_con, daemon=True)
con_thread.start()

time.sleep(1) 

#optionContractDetails(app)

#time.sleep(5)

today, todaytime, nextTradingDay = getDate()
print(f'Today: {today}, Next Business Day: {nextTradingDay}')

optionStrategy(app, nextTradingDay)

time.sleep(5)

app.disconnect()