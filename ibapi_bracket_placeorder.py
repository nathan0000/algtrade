from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import ComboLeg
from ibapi.tag_value import TagValue
import time

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

    def nextValidId(self, orderId: int):
        # Order info
        mycontract = Contract()
        mycontract.symbol = "AAPL"
        mycontract.secType = "STK"
        mycontract.exchange = "SMART"
        mycontract.currency = "USD"

        self.reqContractDetails(orderId, mycontract)
    
    def contractDetails(self, reqId: int, contractDetails):
        print(contractDetails.contract)

        parent = Order()
        parent.orderId = reqId
        parent.orderType = "LMT"
        parent.lmtPrice = 140
        parent.action = "BUY"
        parent.totalQuantity = 10
        parent.transmit = False

        profit_taker = Order()
        profit_taker.orderId = parent.orderId + 1
        profit_taker.parentId = parent.orderId
        profit_taker.action = "SELL"
        profit_taker.orderType = "LMT"
        profit_taker.lmtPrice = 137
        profit_taker.totalQuantity = 10
        profit_taker.transmit = False

        stop_loss = Order()
        stop_loss.orderId = parent.orderId + 2
        stop_loss.parentId = parent.orderId
        stop_loss.orderType = "STP"
        stop_loss.auxPrice = 155
        stop_loss.action = "SELL"
        stop_loss.totalQuantity = 10
        stop_loss.transmit = True

        self.placeOrder(parent.orderId, contractDetails.contract, parent)
        self.placeOrder(profit_taker.orderId, contractDetails.contract, profit_taker)
        self.placeOrder(stop_loss.orderId, contractDetails.contract, stop_loss)

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        print(f"openOrder: {orderId}, contract: {contract}, order: {order}, Maintenance Margin: {orderState.maintMarginChange}")

    def orderStatus(self, orderId: OrderId, status: str, filled: float, remaining: float, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
        print(f"orderStatus. orderId: {orderId}, status:  {status}, filled: {filled}, remaining: {remaining}, avgFillPrice: {avgFillPrice}, permId: {permId}, parentId: {parentId}, lastFillPrice: {lastFillPrice}, clientId: {clientId}, whyHeld: {whyHeld}, mktCapPrice: {mktCapPrice}")

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        print(f"execDetails. reqId: {reqId}, contract: {contract}, execution:  {execution}")

app = TestApp()
app.connect("127.0.0.1", 7497, 333)
app.run()
time.sleep(2)
app.disconnect()