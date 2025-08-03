
# Imports for the Program
from ibapi.client import *
from ibapi.wrapper import *
from ibapi.utils import iswrapper
from ibapi.tag_value import *
import pandas as pd

import threading
import time

class StockScanner(EWrapper, EClient):
    ''' Serves as the Client and the Wrapper '''

    def __init__(self, addr, port, client_id):
        EClient. __init__(self, self)

        # Connect to TWS API
        self.data = pd.DataFrame(columns=['rank','conid','secid','localsymbol','symbol'])
        self.connect(addr, port, client_id)
        self.count = 0

        # Launch the client thread
        thread = threading.Thread(target=self.run)
        thread.start()
    
    def nextValidId(self, orderId):
        self.orderId = orderId
    
    @iswrapper
    def scannerParameters(self, xml: str):
        ''' Callback for reqScannerParameters '''
        super().scannerParameters(xml)
        #open('log/scanner.xml', 'w').write(xml)
        open('scanner.xml', 'w').write(xml)
        print("ScannerParameters received.")

    @iswrapper
    def scannerData(self, reqId, rank, contractDetails, distance, benchmark, projection, legsStr):
        print(f"scannerData. reqId: {reqId}, rank: {rank}, contractDetails: {contractDetails}, distance: {distance}, benchmark: {benchmark}, projection: {projection}, legsStr: {legsStr}.")
        if reqId not in self.data:
            self.data.loc[len(self.data)] = [rank,
                                 contractDetails.contract.conId,
                                 contractDetails.contract.secId,
                                contractDetails.contract.localSymbol,
                                contractDetails.contract.symbol]

    @iswrapper
    def scannerDataEnd(self, reqId):
        print("ScannerDataEnd!")
        print(self.data)
        self.cancelScannerSubscription(reqId)
        self.disconnect()

def reqScannerStock(client, scanner_code="TOP_PERC_GAIN"):
    sub = ScannerSubscription()
    sub.instrument = "STK"
    sub.locationCode = "STK.US.MAJOR"
    sub.scanCode = scanner_code

    scan_options = []
    filter_options = [
        TagValue("priceAbove", '50'),
        TagValue("marketCapAbove1e6", "200000"),
        TagValue("volumeAbove", "1M"),
#        TagValue("HVPercntl52wAbove", "60"),
#        TagValue("ivRank52wAbove", "55"),
#        TagValue("ivRank52wBelow", "15"),
#        TagValue("socialSentimentScoreAbove", "2"),
        TagValue("hasOptionsIs", "True"),
    ]

    client.reqScannerSubscription(client.orderId, sub, scan_options, filter_options)

def reqScannerStockOption(client, scanner_code="OPT_VOLUME_MOST_ACTIVE"):
    sub = ScannerSubscription()
    sub.instrument = "STK"
    sub.locationCode = "STK.US.MAJOR"
    sub.scanCode = scanner_code
#    sub.scanCode = "MOST_ACTIVE"

    scan_options = []
    filter_options = [
        TagValue("maxPeRatio", '160'),
        TagValue("marketCapAbove1e6", "20000"),
        TagValue("HVPercntl52wAbove", "60"),
#        TagValue("ivRank52wAbove", "55"),
#        TagValue("ivRank52wBelow", "15"),
        TagValue("socialSentimentScoreAbove", "2"),
        TagValue("hasOptionsIs", "True"),
        TagValue("volumeAbove", "10k"),
        TagValue("priceAbove", '30'),
        TagValue("priceBelow", "500")
    ]

    client.reqScannerSubscription(client.orderId, sub, scan_options, filter_options)

def reqScannerIndex(client):
    sub = ScannerSubscription()
    sub.instrument = "IND.US"
    sub.locationCode = "IND.US"
    sub.scanCode = "MOST_ACTIVE"

    scan_options = []
    filter_options = [
        TagValue("priceAbove", 2000),
#        TagValue("impvolatchangeperc", '5'),
#        TagValue("hasOptionsIs", "True"),
#        TagValue("priceBelow", "500")
    ]

    client.reqScannerSubscription(client.orderId, sub, scan_options, filter_options)

def reqScannerFutureIndex(client):
    sub = ScannerSubscription()
    sub.instrument = "FUT.US"
    sub.locationCode = "FUT.US"
    sub.scanCode = "MOST_ACTIVE"

    scan_options = []
    filter_options = [
        TagValue("priceAbove", 2000),
#        TagValue("hasOptionsIs", "True"),
#        TagValue("priceBelow", "500")
    ]

    client.reqScannerSubscription(client.orderId, sub, scan_options, filter_options)


def main():

    # Create the client and connect to TWS
    client = StockScanner('127.0.0.1', 7497, 7)
    time.sleep(3)

    # Request the scanner parameters
    client.reqScannerParameters()
    time.sleep(3)

    #reqScannerFutureIndex(client)
#    reqScannerStock(client)
    reqScannerStockOption(client, "TOP_OPT_IMP_VOLAT_GAIN")

    # Disconnect from TWS
    time.sleep(5)       
    client.disconnect()

if __name__ == '__main__':
    main()