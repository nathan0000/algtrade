from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import threading
import time

class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        # Suppress benign notification codes
        if errorCode not in [2104, 2106, 2158]:
            print(f"Error {errorCode}: {errorString}")

    def contractDetails(self, reqId, contractDetails):
        print("\n=== VIX Contract Details Found ===")
        print(f"Symbol:       {contractDetails.contract.symbol}")
        print(f"SecType:      {contractDetails.contract.secType}")
        print(f"Exchange:     {contractDetails.contract.exchange}")
        print(f"Currency:     {contractDetails.contract.currency}")
        print(f"Contract ID:  **{contractDetails.contract.conId}**")
        print(f"Long Name:    {contractDetails.longName}")

    def contractDetailsEnd(self, reqId):
        print("\nFinished retrieving contract details. Disconnecting...")
        self.disconnect()

def run_loop(app):
    app.run()

def get_vix_conid():
    app = IBApp()
    
    # Connect to TWS/Gateway (7497 for paper, 7496 for live by default)
    app.connect("127.0.0.1", 4002, clientId=1)

    # Start the socket thread
    api_thread = threading.Thread(target=run_loop, args=(app,), daemon=True)
    api_thread.start()
    time.sleep(1)  # Allow time to establish connection

    # Define the VIX Index Contract
    vix_contract = Contract()
    vix_contract.symbol = "VIX"
    vix_contract.secType = "IND"
    vix_contract.exchange = "CBOE"
    vix_contract.currency = "USD"

    print("Querying IBKR for VIX contract details...")
    app.reqContractDetails(reqId=1, contract=vix_contract)

    # Keep the main script alive until disconnected by contractDetailsEnd
    while app.isConnected():
        time.sleep(1)

if __name__ == "__main__":
    get_vix_conid()