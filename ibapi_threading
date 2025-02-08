# %load ibkr-api/account_summary.py
from threading import Thread, Event
import time, yaml
import json, logging, logging.config
from logging.handlers import RotatingFileHandler
from typing import Any
from ibapi.wrapper import EWrapper
from ibapi.client import EClient
from ibapi.utils import iswrapper
from ibapi.common import *
from ibapi.scanner import ScannerSubscription
from ibapi.tag_value import TagValue
from ibapi.account_summary_tags import AccountSummaryTags
from ibapi.contract import Contract
import pandas as pd


class ibapp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.orderId = None
        self.count = 0
        self.combined_bar_df = pd.DataFrame(columns=['conid', 'date', 'open', 'high', 'low', 'close', 'volume'])
        self.bar_df = pd.DataFrame(columns=['conid', 'date', 'open', 'high', 'low', 'close', 'volume'])
        self.screen_df = pd.DataFrame(columns=['conid', 'symbol', 'rank'])
        self.done = Event()  # use threading.Event to signal between threads
        self.connection_ready = Event()  # to signal the connection has been established

    # override Ewrapper.error
    @iswrapper
    def error(
        self, reqId: TickerId, errorCode: int, errorString: str, contract: Any = None
    ):
        print("Error: ", reqId, " ", errorCode, " ", errorString)
        if errorCode == 502:  # not connected
            # set self.done (a threading.Event) to True
            self.done.set()

    # override Ewrapper.accountSummary - method for receiving account summary
    @iswrapper
    def accountSummary(
        self, reqId: int, account: str, tag: str, value: str, currency: str
    ):
        # just print the account information to screen
        print(
            "AccountSummary. ReqId:",
            reqId,
            "Account:",
            account,
            "Tag: ",
            tag,
            "Value:",
            value,
            "Currency:",
            currency,
        )

    # override Ewrapper.accountSummaryEnd - notifies when account summary information has been received
    @iswrapper
    def accountSummaryEnd(self, reqId: int):
        # print to screen
        print("AccountSummaryEnd. ReqId:", reqId)
        # set self.done (a threading.Event) to True
        self.done.set()

    # override Ewrapper.nextValidID - used to signal that the connection between application and TWS is complete
    # returns the next valid orderID (for any future transactions)
    # if we send messages before the connection has been established, they can be lost
    # so wait for this method to be called
    def nextValidId(self, orderId: int):
        print(f"Connection ready, next valid order ID: {orderId}")
        self.orderId = orderId
        self.connection_ready.set()  # signal that the connection is ready

    @iswrapper
    def scannerData(self, reqId, rank, details, distance, benchmark, projection, legsStr):
        # Print the symbols in the returned results
        print('{}: {} : {}'.format(rank, details.contract.symbol, details.contract.secType))
        self.count += 1
        self.screen_df.loc[len(self.screen_df)] = [details.contract.conId, details.contract.symbol, rank]
        
    @iswrapper
    def scannerDataEnd(self, reqId):
        # Print the number of results
        print('Number of results: {}'.format(self.count))
        self.cancelScannerSubscription(reqId)
        self.done.set()

    @iswrapper
    def historicalData(self, reqId, bar):
#        print(f"\nOpen: {bar.open}, High: {bar.high}, Low: {bar.low}, Close: {bar.close}")
        self.bar_df.loc[len(self.bar_df)] = [reqId, bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume]

    @iswrapper
    def historicalDataEnd(self, reqId, start, end):
        print(f"Historical Data Ended for {reqId}. Started at {start}, ending at {end}")
        self.commissionReport = pd.concat([self.combined_bar_df, self.bar_df], axis=0)
        self.cancelHistoricalData(reqId)
        self.done.set()
    
def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('trading_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

# define our event loop - this will run in its own thread
def run_loop(app):
    app.run()

def client_thread(addr, port, clientId):
    # instantiate an ibapp
    client = ibapp()

    # connect
    client.connect(addr, port, clientId)  # clientID identifies our application

    # start the application's event loop in a thread
    api_thread = Thread(target=run_loop, args=(client,), daemon=True)
    api_thread.start()

    # wait until the Ewrapper.nextValidId callback is triggered, indicating a successful connection
    client.connection_ready.wait()
    return client

def accsum(client):
    # request account summary
    print("Requesting account summary")
    client.reqAccountSummary(0, "All", AccountSummaryTags.AllTags)

    # wait for the account summary to finish (ie block until app.done - a threading.Event - becomes true)
    client.done.wait()
    print(f"order id: {client.orderId}")

def scanner(client):
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
  
    #request the scanner subscription
    client.reqScannerSubscription(700, sub, scan_options, filter_options)
    client.done.wait()
    
def historical_data(client):
    mycontract = Contract()
    for index, row in client.screen_df.iterrows():
        client.done.clear()
        mycontract.symbol = row['symbol']
        mycontract.secType = "STK"
        mycontract.exchange = "SMART"
        mycontract.currency = "USD"
        int_conid = int(row['conid'])

        client.reqHistoricalData(int_conid, mycontract, "20250205 16:00:00 US/Eastern", "3 D", "15 mins", "TRADES", 1, 1, False, [])
        client.done.wait()

def client_stop(client):
    # disconnect
    client.disconnect()

def main():
    logger = loggerSetup()

    with open('config.yml', 'r') as c:
        config = yaml.safe_load(c)
        baseUrl = config['baseUrl']
        paper_account = config['paper_account']
        live_short_account = config['live_short_account']
        live_long_account = config['live_long_account']
        addr = config["twsapi_addr"]
        port = config["twsapi_paperport"]
        clientId = config["twsapi_clientId"]

    client = client_thread(addr, port, clientId)
    accsum(client)
  
    client.done.clear()
    scanner(client)

    client.done.clear()
    historical_data(client)
    client.screen_df.to_csv('screen.csv', index=False)
    client.bar_df.to_csv('bar.csv', index=False)

    client_stop(client)

if __name__ == "__main__":
    main()