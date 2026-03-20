from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import threading
import time
import math

class SPXChainLoader(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)

        self.underlying_price = None
        self.req_id = 1

        self.expirations = []
        self.strikes = []

        self.target_expiry = None
        self.selected_strikes = []

        self.resolved_contracts = []

    # --------------------------
    # CONNECTION
    # --------------------------
    def nextValidId(self, orderId):
        print("Connected")

        self.reqMarketDataType(3)  # delayed ok for off-hours

        self.request_spx_price()

    # --------------------------
    # STEP 1: GET SPX PRICE
    # --------------------------
    def request_spx_price(self):
        contract = Contract()
        contract.symbol = "SPX"
        contract.secType = "IND"
        contract.exchange = "CBOE"
        contract.currency = "USD"

        self.reqMktData(self.req_id, contract, "", False, False, [])
        self.req_id += 1

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4 and self.underlying_price is None:  # LAST
            self.underlying_price = price
            print("SPX Price:", price)

            self.request_option_chain()

    # --------------------------
    # STEP 2: LOAD CHAIN
    # --------------------------
    def request_option_chain(self):
        self.reqSecDefOptParams(
            self.req_id,
            "SPX",
            "",
            "IND",
            0
        )
        self.req_id += 1

    def securityDefinitionOptionParameter(self, reqId, exchange,
                                          underlyingConId, tradingClass,
                                          multiplier, expirations, strikes):

        if tradingClass == "SPXW":  # only weekly/daily
            self.expirations = sorted(expirations)
            self.strikes = sorted(strikes)

            print("Loaded expiries:", len(self.expirations))
            print("Loaded strikes:", len(self.strikes))

            self.select_expiry_and_strikes()

    # --------------------------
    # STEP 3: FILTER TARGET
    # --------------------------
    def select_expiry_and_strikes(self):
        target = "20260319"

        if target not in self.expirations:
            raise Exception(f"Expiry {target} not found")

        self.target_expiry = target

        spot = self.underlying_price

        # nearest strikes
        nearest = sorted(self.strikes, key=lambda x: abs(x - spot))

        # pick ATM +/- N
        N = 5
        atm = nearest[0]

        selected = []
        for s in self.strikes:
            if abs(s - atm) <= N * 5:  # 5-point spacing assumption
                selected.append(s)

        self.selected_strikes = sorted(selected)

        print("Selected strikes:", self.selected_strikes)

        self.resolve_contracts()

    # --------------------------
    # STEP 4: RESOLVE CONTRACTS
    # --------------------------
    def resolve_contracts(self):
        for strike in self.selected_strikes:
            for right in ["C", "P"]:

                c = Contract()
                c.symbol = "SPX"
                c.secType = "OPT"
                c.exchange = "CBOE"
                c.currency = "USD"

                c.lastTradeDateOrContractMonth = self.target_expiry
                c.strike = float(strike)
                c.right = right
                c.tradingClass = "SPXW"
                c.multiplier = "100"

                self.reqContractDetails(self.req_id, c)
                self.req_id += 1

    def contractDetails(self, reqId, details):
        c = details.contract

        self.resolved_contracts.append(c)

        print(f"Resolved: {c.lastTradeDateOrContractMonth} "
              f"{c.strike} {c.right} conId={c.conId}")

    def contractDetailsEnd(self, reqId):
        pass

    # --------------------------
    # ERROR HANDLING
    # --------------------------
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        print(f"ERROR {errorCode}: {errorString}")


# --------------------------
# RUN
# --------------------------
def run():
    app = SPXChainLoader()
    app.connect("127.0.0.1", 4002, clientId=1)

    thread = threading.Thread(target=app.run)
    thread.start()

    time.sleep(30)

    print("\nFinal contracts loaded:", len(app.resolved_contracts))

    app.disconnect()


if __name__ == "__main__":
    run()