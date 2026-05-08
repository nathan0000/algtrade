import time
import threading
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from contracts import get_es_contract
import config

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

    def tickPrice(self, reqId, tickType, price, attrib):
        # TickType 4 is Last Price, 1 is Bid, 2 is Ask
        if tickType in [1, 2, 4]:
            print(f"✅ Price Update | TickType {tickType}: {price}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        print(f"❌ IBKR Notice {errorCode}: {errorString}")

app = TestApp()
app.connect(config.IB_HOST, config.IB_PORT, clientId=99) # Use fresh ID

threading.Thread(target=app.run, daemon=True).start()
time.sleep(2)

print("📡 Requesting live top-of-book for ES...")
# Ask for Live Snapshot snapshot=False
app.reqMktData(reqId=1, contract=get_es_contract(), genericTickList="", snapshot=False, regulatorySnapshot=False, mktDataOptions=[])

time.sleep(10)
app.disconnect()