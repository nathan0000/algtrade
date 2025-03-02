from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from threading import Timer

#position_ref = {}

class AccountApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.position_ref = {}

    def error(self, reqId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        pass
        print(reqId, errorCode, errorString, advancedOrderRejectJson)

    def nextValidId(self, orderId):
        self.start()

    def updatePortfolio(self, contract: Contract, position: float, marketPrice: float, marketValue: float,
                        averageCost: float, unrealizedPNL: float, realizedPNL: float, accountName: str):
        print("UpdatePortfolio.", "Symbol:", contract.symbol, "SecType:", contract.secType, "Exchange:", contract.exchange,
              "Position:", position, "MarketPrice:", marketPrice, "MarketValue:", marketValue, "AverageCost:", averageCost,
              "UnrealizedPNL:", unrealizedPNL, "RealizedPNL:", realizedPNL, "AccountName:", accountName)

    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        print("UpdateAccountValue. Key:", key, "Value:", val, "Currency:", currency, "AccountName:", accountName)

    def updateAccountTime(self, timeStamp: str):
        print("UpdateAccountTime. Time:", timeStamp)

    def accountDownloadEnd(self, accountName: str):
        print("AccountDownloadEnd. Account:", accountName)

    def position(self, account, contract, position, avgCost):
        self.position_ref[contract.symbol] = {account, contract, position, avgCost}

    def start(self):
        # Account number can be omitted when using reqAccountUpdates with single account structure
        self.reqAccountUpdates(True, "U5246790")
        self.reqAccountUpdates(True, "U7993843")
        self.reqPositions()

    def stop(self):
        self.reqAccountUpdates(False, "")
        print(f"Position: {self.position_ref}")
        self.done = True
        self.disconnect()

def main():
    app = AccountApp()
    app.connect("127.0.0.1", 7497, 227)

    Timer(5, app.stop).start()
    app.run()

if __name__ == "__main__":
    main()