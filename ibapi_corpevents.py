from ibapi.client import *
from ibapi.wrapper import *

class CorpEventApp(EClient, EWrapper):
  def __init__(self):
    EClient.__init__(self, self)

  def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
    print(f"Error. Id: {reqId}, Code: {errorCode}, Msg: {errorString}")
    
  def nextValidId(self, orderId: OrderId):
    self.nextOrderId = orderId
    self.reqWshMetaData(self.nextOrderId)   

  def wshMetaData(self, reqId, dataJson):
    print(f"WshMetaData. ReqId: {reqId}, Data Json: {dataJson}")
    self.cancelWshMetaData(reqId)

    wshEvent = WshEventData()
    wshEvent.conId = 8314
    wshEvent.startDate = "20240701"
    wshEvent.endDate = "20250431"


  def wshEventData(self, reqId, dataJson):
    print(f"WshEventData. ReqId: {reqId} Data JSON: {dataJson}")
    self.cancelWshEventData(reqId)

app = CorpEventApp()
app.connect("127.0.0.1", 7497, 100)
app.run()
app.disconnect()