from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.ticktype import TickTypeEnum

class IBapi(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self) 

    def nextValidId(self, orderId: int):

        mycontract = Contract()
        mycontract.symbol = "SPX"
        mycontract.secType = "IND"
        mycontract.exchange = "CBOE"
        mycontract.currency = "USD"

        self.reqMarketDataType(4)
        self.reqMktData(orderId, mycontract, "", 0, 0, [])

    def tickPrice(self, reqId, tickType, price, attrib):
        print(f"tickPrice, reqId: {reqId}, tickType: {TickTypeEnum.to_str(tickType)}, price: {price}, attrib: {attrib}")

    def tickSize(self, reqId, tickType, size):
        print(f"tickSize, reqId: {reqId}, tickType: {TickTypeEnum.to_str(tickType)}, size: {size}")

app = IBapi()
app.connect('127.0.0.1', 7497, 1000)
app.run()


#Uncomment this section if unable to connect
#and to prevent errors on a reconnect
import time
time.sleep(2)
app.disconnect()
