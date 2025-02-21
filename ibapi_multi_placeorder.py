from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.execution import *
import time
import threading

t = ['AAPL', 'AMD', 'CRM']

n = [10, 12, 10]


class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)

        # define variables
        self.done = False

    # note all these functions below handle the incoming server requests.
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)

    def nextValidId(self, orderId):
        super().nextValidId(orderId)
        self.nextValidOrderId = orderId
        print("NextValidId:", orderId)

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId,
                    whyHeld, mktCapPrice):
        print("OrderStatus. Id: ", orderId, ", Status: ", status, ", Filled: ", filled, ", Remaining: ", remaining,
              ", LastFillPrice: ", lastFillPrice)

    def openOrder(self, orderId, contract, order, orderState):
        super().openOrder(orderId, contract, order, orderState)

    def openOrderEnd(self):
        super().openOrderEnd()
        print("OpenOrderEnd")
        #logging.debug("Received %d openOrders", len(self.permId2ord))

    def execDetails(self, reqId, contract, execution):
        super().execDetails(reqId, contract, execution)
        print("ExecDetails. ReqId:", reqId, "Symbol:", contract.symbol, "SecType:", contract.secType, "Currency:",
              contract.currency, execution)

    def execDetailsEnd(self, reqId):
        super().execDetailsEnd(reqId)
        print("ExecDetailsEnd. ReqId:", reqId)


# define some functions to help make requests to IB servers (outgoing)
## define the contract, in this case US stock
def Stock(symbol, sec_type="STK", currency="USD", exchange="ISLAND"):
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.currency = currency
    contract.exchange = exchange
    return contract

## market order
def marketOrder(action, quantity):
    order = Order()
    order.action = action # buy or sell
    order.orderType = "MKT"
    order.totalQuantity = quantity
    order.tif = "DAY" # time in force day - cancel order if not filled - will cancel at the close
    #order.orderRef = order_ref
    order.optOutSmartRouting = False
    return order


## function to buy stocks
def BuyStocks(app):
    for i in range(0,len(t),1):
        print("This is i",i)
        print("stock is, ", t[i])
        print("qty is, ", n[i])
        app.reqIds(-1)
        time.sleep(.2)
        order_id = app.nextValidOrderId
        app.placeOrder(order_id, Stock(t[i]), marketOrder("BUY", n[i]))
        time.sleep(1)

    # disconnect when operation completed
    if i == len(t)-1:
        app.done = True
        print("Orders have been placed disconnect")
        app.disconnect()


def main(app):
    # buy function
    BuyStocks(app)


def websocket_con():
    app.run()

# setup the threads
app = TestApp()
app.connect(host='127.0.0.1', port=7497, clientId=9)  # port 4002 for ib gateway paper trading/7497 for TWS paper trading
con_thread = threading.Thread(target=websocket_con, args=())  # , daemon=True
con_thread.start()
time.sleep(5) # some lag added to ensure that streaming has started

# thread for main()
MainThread = threading.Thread(target = main, args =(app,))
MainThread.start()


#if __name__ == "__main__":
#    main(app)