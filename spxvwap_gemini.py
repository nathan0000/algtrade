import threading
import time
import datetime
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class HistoricalDataApp(EWrapper, EClient):
    """
    A standalone IBAPI client to request SPX historical data.
    Calculates a Price Average (TWAP) since SPX volume is typically 0.
    """
    def __init__(self):
        EClient.__init__(self, self)
        self.data_received = False
        self.history = []
        
        # Average Accumulators (Price-based since Vol is 0)
        self.cum_price = 0.0 
        self.bar_count = 0

    def nextValidId(self, orderId: int):
        """Called when the connection is established and the API is ready."""
        logger.info("Connected to IB Gateway. Requesting SPX data...")
        self.request_spx_history()

    def request_spx_history(self):
        """Defines the SPX contract and submits the historical data request."""
        spx = Contract()
        spx.symbol = "SPX"
        spx.secType = "IND"
        spx.exchange = "CBOE"
        spx.currency = "USD"

        # Request 1 Day of 1-minute bars
        self.reqHistoricalData(1001, spx, "", "1 D", "1 min", "TRADES", 1, 1, False, [])

    def historicalData(self, reqId, bar):
        """Called for every bar received. Calculates running average price."""
        try:
            high = float(bar.high)
            low = float(bar.low)
            close = float(bar.close)
            # Volume is logged but often 0 for Indices
            volume = float(bar.volume)
        except (TypeError, ValueError) as e:
            logger.error(f"Error casting bar data: {e}")
            return

        # Typical Price for the bar
        typical_price = (high + low + close) / 3.0
        
        # Accumulate for Time-Weighted Average (TWAP proxy)
        self.cum_price += typical_price
        self.bar_count += 1
        
        # Calculate current average
        current_avg = self.cum_price / self.bar_count if self.bar_count > 0 else 0.0
        
        data_point = {
            "time": bar.date,
            "close": close,
            "avg_price": current_avg,
            "vol": volume
        }
        self.history.append(data_point)
        
        # Print results - displaying 'AvgPrice' instead of VWAP since Vol is 0
        print(f"Time: {bar.date} | Close: {close:8.2f} | Vol: {volume:7.0f} | AvgPrice: {current_avg:8.2f}")

    def historicalDataEnd(self, reqId, start, end):
        """Called when all bars for the request have been received."""
        logger.info(f"Retrieval Complete. Total bars: {len(self.history)}")
        self.data_received = True

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        """
        Error handling with 6 parameters to match EWrapper specification.
        """
        if errorCode not in [2104, 2106, 2158]:
            logger.error(f"IBKR Error {errorCode}: {errorString} | Reject Info: {advancedOrderRejectJson}")

def main():
    app = HistoricalDataApp()
    
    # IB Gateway Paper Port is typically 4002 (TWS Paper is 7497)
    # IB Gateway Live Port is typically 4001 (TWS Live is 7496)
    GATEWAY_PORT = 4002 
    
    app.connect("127.0.0.1", GATEWAY_PORT, clientId=10)

    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    # Wait for the data retrieval
    start_wait = time.time()
    while not app.data_received and time.time() - start_wait < 30:
        time.sleep(1)

    if not app.data_received:
        logger.warning("Data retrieval timed out or failed. Ensure IB Gateway is open and API is enabled.")

    app.disconnect()
    logger.info("Disconnected.")

if __name__ == "__main__":
    main()