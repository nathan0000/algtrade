import threading
import queue
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(threadName)s: %(message)s')

class IBGatewayApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        # Event queues for decoupled components
        self.market_data_queue = queue.Queue()
        self.contract_details_queue = queue.Queue()
        self.order_queue = queue.Queue()
        self.contract_lookup_queue = queue.Queue()
        
    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "", argv: str = ""):
        # Avoid flooding logs with standard notification codes
        if errorCode in [2104, 2106, 2158]: 
            return
        logging.error(f"IB Error [{errorCode}] for ReqId [{reqId}]: {errorString}")

    def contractDetails(self, reqId: int, contractDetails):
        # Extract the unique contract ID from the payload
        self.contract_lookup_queue.put((reqId, contractDetails.contract.conId))

    def contractDetailsEnd(self, reqId: int):
        self.contract_lookup_queue.put((reqId, "END"))

    # --- Market Data Callbacks ---
    def tickPrice(self, reqId, tickType, price, attrib):
        self.market_data_queue.put(('tickPrice', reqId, tickType, price, attrib))

    def tickSize(self, reqId, tickType, size):
        self.market_data_queue.put(('tickSize', reqId, tickType, size))

    def tickOptionComputation(self, reqId, tickType, tickAttrib, impliedVol, delta, optPrice, pvDividend, gamma, vega, theta, undPrice):
        self.market_data_queue.put(('tickOption', reqId, tickType, impliedVol, delta, optPrice, gamma, vega, theta, undPrice))

    def historicalData(self, reqId, bar):
        self.market_data_queue.put(('historicalBar', reqId, bar))

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        self.market_data_queue.put(('historicalEnd', reqId))

    # --- Contract Discovery Callbacks ---
    def securityDefinitionOptionParameter(self, reqId, exchange, underlyingConId, tradingClass, multiplier, expirations, strikes):
        self.contract_details_queue.put((reqId, expirations, strikes))

    def securityDefinitionOptionParameterEnd(self, reqId):
        self.contract_details_queue.put((reqId, "END", None))


class IBGatewayManager:
    def __init__(self, host="192.168.1.116", port=4002, clientId=1):
        self.app = IBGatewayApp()
        self.host = host
        self.port = port
        self.clientId = clientId
        self._thread = None

    def connect(self):
        self.app.connect(self.host, self.port, self.clientId)
        self._thread = threading.Thread(target=self.app.run, name="IB_Socket_Thread", daemon=True)
        self._thread.start()
        logging.info("Connected to IB Gateway and socket thread started.")

    def disconnect(self):
        self.app.disconnect()
        logging.info("Disconnected from IB Gateway.")