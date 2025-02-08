from ibapi.client import *
from ibapi.wrapper import *
from ibapi.tag_value import *
import pandas as pd

port = 7497

class TestApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.data = pd.DataFrame(columns=['rank','conid','secid','localsymbol','symbol'])
        self.nextOrderId = None

    def nextValidId(self, orderId: int):
        sub = ScannerSubscription()
        sub.instrument = "STK"
        sub.locationCode = "STK.US.MAJOR"
        sub.scanCode = "MOST_ACTIVE"

        scan_options = []
        filter_options = [
            TagValue("priceAbove", '30'),
            TagValue("hasOptionsIs", "True"),
            TagValue("priceBelow", "500")
        ]

        self.reqScannerSubscription(orderId, sub, scan_options, filter_options)

    def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
        print(f"scannerData. reqId: {reqId}, rank: {rank}, contractDetails: {contractDetails}, distance: {distance}, benchmark: {benchmark}, projection: {projection}, legsStr: {legsStr}.")
        if reqId not in self.data:
            self.data.loc[len(self.data)] = [rank,
                                 contractDetails.contract.conId,
                                 contractDetails.contract.secId,
                                contractDetails.contract.localSymbol,
                                contractDetails.contract.symbol]

    def scannerDataEnd(self, reqId):
        print("ScannerDataEnd!")
        print(self.data)
        self.cancelScannerSubscription(reqId)
        self.disconnect()


app = TestApp()
app.connect("127.0.0.1", port, 9001)
app.run()