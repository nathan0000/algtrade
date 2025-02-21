from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import ComboLeg
from ibapi.tag_value import TagValue
import time, threading
import pandas as pd

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextValidOrderId = 1
        self.contractDetails_df = pd.DataFrame(columns=["reqId", "symbol", "secType",  "conId", "exchange", "currency"])          

    # note all these functions below handle the incoming server requests.
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=''):
        print("Error. Id:", reqId, errorCode, "Msg:", errorString, "AdvancedOrderRejectJson:", advancedOrderRejectJson)

    def nextValidId(self, orderId: int):
        self.nextValidOrderId = orderId
        print("NextValidId:", orderId)

    def contractDetails(self, reqId: int, contractDetails):
        self.contractDetails_df.loc[len(self.contractDetails_df)] = [reqId, contractDetails.contract.symbol, contractDetails.contract.secType, contractDetails.contract.conId, contractDetails.contract.exchange, contractDetails.contract.currency]  

    
    def contractDetailsEnd(self, reqId):
        print("End of contract details")
        print(self.contractDetails_df)

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        print(f"orderId: {orderId}, contract: {contract}, order: {order}, Maintenance Margin: {orderState.maintMarginChange}")

    def orderStatus(self, orderId: OrderId, status: str, filled: float, remaining: float, avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float, clientId: int, whyHeld: str, mktCapPrice: float):
        print(f"orderStatus. orderId: {orderId}, status:  {status}, filled: {filled}, remaining: {remaining}, avgFillPrice: {avgFillPrice}, permId: {permId}, parentId: {parentId}, lastFillPrice: {lastFillPrice}, clientId: {clientId}, whyHeld: {whyHeld}, mktCapPrice: {mktCapPrice}")

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        print(f"execDetails. reqId: {reqId}, contract: {contract}, execution:  {execution}")

def comboOrder(app):
           # Order info

        orderId = app.nextValidOrderId
        buyContract = Contract()
        buyContract.symbol = "AAPL"
        buyContract.secType = "STK"
        buyContract.exchange = "NASDAQ"
        buyContract.currency = "USD"
        app.reqContractDetails(orderId, buyContract)
        time.sleep(3)
        print(f'buying: {buyContract}')

        sellContract = Contract()
        sellContract.symbol = "TSLA"
        sellContract.secType = "STK"
        sellContract.exchange = "NASDAQ"
        sellContract.currency = "USD"
        app.reqContractDetails(orderId, sellContract)
        time.sleep(3)
        print(f'selling: {sellContract}')
        print(f'contractDetails_df: {app.contractDetails_df}')
        print(f'appl conId: {app.contractDetails_df.loc[app.contractDetails_df["symbol"] == "AAPL"]["conId"]}')
        print(f'tsla conId: {app.contractDetails_df.loc[app.contractDetails_df["symbol"] == "TSLA"]["conId"]}')

        mycontract = Contract()
        mycontract.symbol = "AAPL,TSLA"
        mycontract.secType = "BAG"
        mycontract.exchange = "SMART"
        mycontract.currency = "USD"

        leg1 = ComboLeg()
        leg1.conId = app.contractDetails_df.loc[app.contractDetails_df["symbol"] == "AAPL"].conId.item()
        leg1.ratio = 1
        leg1.action = "BUY"
    #    leg1.price = 230
        leg1.exchange = "SMART"
 
        leg2 = ComboLeg()
        leg2.conId = app.contractDetails_df.loc[app.contractDetails_df["symbol"] == "TSLA"].conId.item()
        leg2.ratio = 1
        leg2.action = "SELL"
    #    leg2.price = 350
        leg2.exchange = "SMART"
    
        mycontract.comboLegs = []
        mycontract.comboLegs.append(leg1)
        mycontract.comboLegs.append(leg2)

    #    print(mycontract)

        myorder = Order()
        myorder.orderId = orderId
        myorder.action = "BUY"
        myorder.orderType = "LMT"
        myorder.lmtPrice = -10
        myorder.totalQuantity = 10
        myorder.tif = "DAY"
        myorder.smartComboRoutingParams = []
        myorder.smartComboRoutingParams.append(TagValue('NonGuaranteed', '1'))

        app.placeOrder(orderId, mycontract, myorder)

        time.sleep(5)
        print("Order placed")

def websocket_con():
    app.run()

app = TestApp()
app.connect("127.0.0.1", 7497, 100)
con_thread = threading.Thread(target=websocket_con, args=())  # , daemon=True
con_thread.start()
time.sleep(5) # some lag added to ensure that streaming has started

# thread for main()
#MainThread = threading.Thread(target = comboOrder, args =(app,))
#MainThread.start()
comboOrder(app)

app.disconnect()