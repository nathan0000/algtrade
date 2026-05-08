import time
import pandas as pd
import threading
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from config import logger, IB_HOST, IB_PORT, IB_CLIENT_ID
from contracts import get_vx_contract

class VixTermStructureApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.contracts, self.prices = [], {}
        self.details_resolved, self.prices_resolved = threading.Event(), threading.Event()
        self.active_req_ids = set()

    def contractDetails(self, reqId, contractDetails):
        self.contracts.append(contractDetails.contract)

    def contractDetailsEnd(self, reqId):
        self.details_resolved.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType in [4, 9, 68, 71, 75] and price > 0: # Includes delayed price types
            self.prices[reqId] = price
            self.active_req_ids.discard(reqId)
            if not self.active_req_ids: self.prices_resolved.set()

def get_vix_term_structure():
    app = VixTermStructureApp()
    app.connect(IB_HOST, IB_PORT, IB_CLIENT_ID + 2)
    threading.Thread(target=app.run, daemon=True).start()
    time.sleep(1)

    search = Contract()
    search.symbol, search.secType, search.exchange, search.currency = "VX", "FUT", "CFE", "USD"
    
    app.reqContractDetails(201, search)
    app.details_resolved.wait(timeout=10)

    # Use resolved contracts or fallbacks
    targets = sorted(app.contracts, key=lambda c: getattr(c, 'lastTradeDateOrContractMonth', ''))[:2]
    if not targets:
        logger.warning("Term structure search failed; injecting hardcoded fallbacks.")
        targets = [get_vx_contract(0), get_vx_contract(1)]

    app.reqMarketDataType(3) # Allow delayed data
    for i, contract in enumerate(targets):
        req_id = 300 + i
        app.active_req_ids.add(req_id)
        app.reqMktData(req_id, contract, "", True, False, [])

    app.prices_resolved.wait(timeout=10)
    app.disconnect()
    
    return pd.DataFrame([{'expiry': getattr(c, 'lastTradeDateOrContractMonth', 'Fallback'), 
                          'price': app.prices.get(300+i)} for i, c in enumerate(targets)]).dropna()