from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.ticktype import TickType
import threading
from threading import Event
import time
import pandas as pd
import numpy as np
import statistics

# Custom IBKR Client Class that handles responses
class RateVixApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.last_price = None
        self.bar_df = pd.DataFrame(columns=['reqId', 'date', 'open', 'high', 'low', 'close', 'volume'])
        self.done = Event()  # use threading.Event to signal between threads
        self.connection_ready = Event()  # to signal the connection has been established

    def nextValidId(self, orderId):
        """Handle the next valid order ID."""
        self.orderId = orderId
        print(f"Next Valid Order ID: {orderId}")

    def contractDetails(self, reqId, contractDetails):
        print(f"Contract Details. Ticker Id: {reqId}, Contract: {contractDetails.contract}")
    
    def tickPrice(self, reqId, tickType, price, attrib):
        """Handle real-time market data ticks (e.g., price updates)."""
        print(f"Tick Price. Ticker Id: {reqId}, Price: {price}, Tick Type: {tickType}")
        if tickType == 4:  # LAST corresponds to the last traded price
            self.last_price = price
        elif tickType == 9:  # CLOSE corresponds to the closing price
            self.last_price = price
        print(f"Last Price: {price}")

#    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Handle errors."""
#        print(f"Error: {errorCode}, {errorString}")
#        if errorCode == 502:  # not connected
#            # set self.done (a threading.Event) to True
#            self.done.set()

    def historicalData(self, reqId, bar):
#        print(f"\nOpen: {bar.open}, High: {bar.high}, Low: {bar.low}, Close: {bar.close}")
        self.bar_df.loc[len(self.bar_df)] = [reqId, bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume]

    def historicalDataEnd(self, reqId, start, end):
        print(f"Historical Data Ended for {reqId}. Started at {start}, ending at {end}")
        self.cancelHistoricalData(reqId)
        self.done.set()

# Function to create and return a contract for U.S. Treasury Futures
def create_treasury_contract():
    contract = Contract()
    contract.symbol = "10Y"  # Symbol for 10-Year Treasury Bond Futures
    contract.secType = "FUT"  # Security type: Futures
    contract.exchange = "CBOT"  # Exchange: ECM futures
    contract.currency = "USD"  # Currency: USD
    contract.lastTradeDateOrContractMonth = 20250331  # Expiration month for the contract (example: June 2023)
    return contract
    # Create an IBKR client instance

def GetRateVix(symbol="10Y"):

    app = RateVixApp()      
    app.connect("127.0.0.1", 7497, clientId=1)

    con_thread = threading.Thread(target=app.run, daemon=True)
    con_thread.start()

    time.sleep(1) 

    # Create the contract for the 10-Year Treasury Futures (ZN)
    tnxContract = create_treasury_contract()
    app.reqContractDetails(1, tnxContract)

    # Request real-time market data (for the contract)
    app.reqMarketDataType(2)
    app.reqMktData(1, tnxContract, "", True, False, [])

    time.sleep(5)  # Wait for the response

    mycontract = Contract()
    mycontract.symbol = symbol
    mycontract.secType = "STK"
    mycontract.exchange = "SMART"
    mycontract.currency = "USD"

    app.reqHistoricalData(1, mycontract, "", "1 Y", "1 day", "TRADES", 0, 1, False, [])
    app.done.wait()
    app.bar_df['price relative'], app.bar_df['Daily Returns'] = "",""
    for i in range(1, len(app.bar_df.close)):
        app.bar_df['price relative'][i] = app.bar_df['close'][i] / app.bar_df['close'][i-1]
    for i in range(1, len(app.bar_df.close)):
        app.bar_df['Daily Returns'][i] = np.log(app.bar_df['close'][i]/app.bar_df['close'][i-1])
    #app.bar_df['Daily Returns'] = app.bar_df['close'].pct_change()

    # Calculate standard deviation of daily returns (Historical Volatility)
    dailyVolatility = statistics.stdev(app.bar_df['Daily Returns'][1:])
    historical_volatility = dailyVolatility * np.sqrt(252)  # Annualize volatility


    app.disconnect()  # Disconnect the client
    return app.last_price, historical_volatility

def main():
    rate, hisvol = GetRateVix("AAPL")
    print(f"10-Year Treasury Price: {rate}")
    print(f"30-Day Historical Volatility: {hisvol*100:.2f}%")

if __name__ == "__main__":
    main()
