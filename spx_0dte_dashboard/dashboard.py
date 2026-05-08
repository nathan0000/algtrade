import time
import pandas as pd
import threading
from datetime import datetime
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from contracts import get_es_contract, get_vx_contract
from term_structure import get_vix_term_structure
from config import logger, IB_HOST, IB_PORT, IB_CLIENT_ID

class MarketBriefApp(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        self.data_store, self.contract_resolved = {}, threading.Event()
        self.resolved_contract = None

    def contractDetails(self, reqId, contractDetails):
        self.resolved_contract = contractDetails.contract

    def contractDetailsEnd(self, reqId):
        self.contract_resolved.set()

    def historicalData(self, reqId, bar):
        if reqId not in self.data_store: self.data_store[reqId] = []
        self.data_store[reqId].append({'close': bar.close})

    def historicalDataEnd(self, reqId, start, end):
        self.data_store[reqId] = pd.DataFrame(self.data_store[reqId])

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        if errorCode not in [2104, 2106, 2107, 2158]:
            logger.warning(f"IBKR API {errorCode}: {errorString}")

def request_market_data(app):
    # Resolve ES
    app.contract_resolved.clear()
    app.reqContractDetails(501, get_es_contract())
    app.contract_resolved.wait(timeout=5)
    final_es = app.resolved_contract if app.resolved_contract else get_es_contract()

    # Resolve VX Dynamic
    app.contract_resolved.clear()
    app.resolved_contract = None
    search_vx = get_vx_contract(0, resolved_conid=None) # Start with basic search
    app.reqContractDetails(502, search_vx)
    app.contract_resolved.wait(timeout=5)
    
    resolved_id = app.resolved_contract.conId if app.resolved_contract else None
    final_vx = get_vx_contract(0, resolved_conid=resolved_id)

    # Request History
    app.reqHistoricalData(101, final_es, "", "14400 S", "5 mins", "TRADES", 0, 1, False, [])
    app.reqHistoricalData(102, final_vx, "", "1 D", "1 day", "MIDPOINT", 0, 1, False, [])
    
    time.sleep(10)
    return 101 in app.data_store and 102 in app.data_store

def generate_dashboard(es_df, vx_df, ts_df):
    es_px, twap = es_df['close'].iloc[-1], es_df['close'].mean()
    vx1 = ts_df['price'].iloc[0] if not ts_df.empty else vx_df['close'].iloc[-1]
    vx2 = ts_df['price'].iloc[1] if len(ts_df) > 1 else vx1
    spread = vx2 - vx1

    print("\n" + "="*45)
    print(f" 🗓️ 0DTE BRIEF | {datetime.now().strftime('%H:%M')} SYD")
    print("="*45)
    print(f"ES: {es_px:.2f} | TWAP: {twap:.2f}")
    print(f"VX1: {vx1:.2f} | VX2: {vx2:.2f} | Spread: {spread:+.2f}")
    print(f"State: {'CONTANGO' if spread > 0 else 'BACKWARDATION'}")
    print("="*45)

if __name__ == "__main__":
    ts_df = get_vix_term_structure()
    app = MarketBriefApp()
    app.connect(IB_HOST, IB_PORT, IB_CLIENT_ID)
    threading.Thread(target=app.run, daemon=True).start()
    time.sleep(1)

    if request_market_data(app):
        generate_dashboard(app.data_store[101], app.data_store[102], ts_df)
    app.disconnect()