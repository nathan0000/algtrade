from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import threading
import time

class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print("ERROR", reqId, errorCode, errorString)

    def nextValidId(self, orderId):
        print("CONNECTED", orderId)

app = TestApp()

app.connect("192.168.1.10", 4002, 1)

threading.Thread(target=app.run, daemon=True).start()

time.sleep(10)

print("Connected state:", app.isConnected())

app.disconnect()
