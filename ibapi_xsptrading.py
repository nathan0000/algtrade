from ibapi.client import *
from ibapi.common import Decimal
from ibapi.contract import Contract
from ibapi.utils import Decimal
from ibapi.wrapper import *
from ibapi.tag_value import *
from ibapi.contract import *
from ibapi.ticktype import TickTypeEnum

import threading
from time import sleep
from datetime import *

port = 7497

global globalDict, clientId
globalDict = {}
clientId = 100001

class ibThreading(EClient, EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

    def error(self, reqId, errorCode: int, errorString: str, advancedOrderRejectJson=""):
        pass
        print(reqId, errorCode, errorString, advancedOrderRejectJson)

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
        print(f"next Oder Id: {orderId}")
        self.start()

    def managedAccount(self, accountsList: str):
        print(f"Account list: {accountsList}")

    def updatePortfolio(self, contract: Contract, position: Decimal, marketPrice: float, marketValue: float, averageCost: float, unrealizedPNL: float, realizedPNL: float, accountName: str):
        print("UpdatePortfolio.", "Symbol:", contract.symbol, "SecType:", contract.secType, "Exchange:", contract.exchange,
              "Position:", position, "MarketPrice:", marketPrice, "MarketValue:", marketValue, "AverageCost:", averageCost,
              "UnrealizedPNL:", unrealizedPNL, "RealizedPNL:", realizedPNL, "AccountName:", accountName)
        global globalDict
        if contract.symbol in ["SPX", "XSP", "RUT"]: #only return index option positions
            globalDict[accountName] = [contract, position, marketPrice, averageCost, unrealizedPNL]
        for i in globalDict:
            contract = globalDict[i][0]
            position = decimalMaxString(globalDict[i][1])
            avgCost = floatMaxString(globalDict[i][3])
            print(f"Account: {i}; Symbol: {contract.symbol}; Position: {position}; Average Cost: {avgCost}")
        
    def updateAccountTime(self, timeStamp: str):
        # Cancel account update if 10 minutes before market close
        if datetime.now().strftime("%H%M%S") > "155000":
            self.reqAccountUpdates(False, "")
            self.done = True
            self.disconnect()

        # Request market data for all of our scanner values.
    #    for i, (account, contract) in enumerate(globalDict.items()):
    #        self.reqMarketDataType(4)
    #        x = threading.Thread(target=self.reqMktData(reqId=i,contract=contract,genericTickList="",snapshot=True,regulatorySnapshot=False,mktDataOptions=[]))
    #        x.start()

    # Returned market data
    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, attrib: TickAttrib):
        global globalDict
        for ind,value in enumerate(globalDict[reqId]):
            if TickTypeEnum.to_str(tickType) == value:
                globalDict[reqId][ind] = price

    def tickSnapshotEnd(self, reqId: int):
        if reqId == 49:
            # After my last request, disconnect from socket.
            self.disconnect()

    # Returned Hisotircal Data
    def historicalData(self, reqId: int, bar: BarData):
        global globalDict
        barDate = bar.date.split()[0]
        requestedDate = starttrading.dateCleanUp()
        
        # Save Todays Bar
        if barDate == requestedDate:
            globalDict[reqId][4] = bar
        # Save the prior bar
        else:
            globalDict[reqId][5] = bar

    # End of All Hisotrical Data
    def historicalDataEnd(self, reqId: int, start: str, end: str):
        global clientId
        if reqId  == 4:
            clientId = self.nextOrderId
            self.disconnect()

    # Show order placed
    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        print(orderId, contract, order, orderState)

    def start(self):
        # Account number can be omitted when using reqAccountUpdates with single account structure
        self.reqAccountUpdates(True, "")

    def stop(self):
        self.reqAccountUpdates(False, "")
        self.done = True
        self.disconnect()
        
def run_loop(app_obj: ibThreading):
    print("Run_Loop")
    app_obj.run()

class starttrading():
    # Normalize Date Values
    def dateCleanUp():
        badDate = date.today().__str__().split('-')
        goodDate = badDate[0] + badDate[1] + badDate[2]
        return goodDate
    
    # Retrieve account portofolios
    def getPosition():
        global clientId
        app = ibThreading()
        app.connect("127.0.0.1", port, clientId)
    #
        app.reqAccountUpdates(True, "")
      
        app.run()
        return
    
    # create all historical data requests
    def buildHistorical():
        global globalDict
        app = ibThreading()
        app.connect("127.0.0.1", port, clientId)
        sleep(3)
        for i in range(0,5):
            # threading.Thread(target=app.reqHistoricalData(i, globalDict[i][0], "", "2 D", "1 day", "TRADES", 1, 1, 0, [])).start()
            app.reqHistoricalData(i, globalDict[i][0], "", "2 D", "1 day", "TRADES", 1, 1, 0, [])
        app.run()
        
    # Create values for change values
    def calcChange():
        global globalDict
        for i in range(0,5):
            yesterday = globalDict[i][5]
            today = globalDict[i][4]
            now = globalDict[i][3]
            globalDict[i][6] = float((now / today.open)*100)
            globalDict[i][7] = float((now / yesterday.open)*100)
            globalDict[i][8] = float(now - today.open)
            globalDict[i][9] = float(now - yesterday.open)
        return
    
    # Place an order for the best buys
    def bestBuys():
        bestVal = globalDict[0]
        bestPerc = globalDict[0]
        # global globalDict
        for i in range(0,5):
            
            if globalDict[i][8] > bestVal[8]:
                bestVal = globalDict[i]
            if globalDict[i][6] > bestPerc[6]:
                print(globalDict[i][6], ">", bestPerc[6])
                bestPerc = globalDict[i]
        print(f"The largest increase by integer: {bestVal[0].symbol} by {bestVal[8]:.4f} ")
        print(f"The largest increase by percentage: {bestPerc[0].symbol} by {bestPerc[6]:.4f}")
        buyIt = input("\nWould you like to buy these? Y/N: ")
        if buyIt == "Y" or buyIt == "y":
            starttrading.buyBest([bestVal, bestPerc])
        else:
            return
        
    # Buy the best percentage and value contracts from the bestBuys()
    def buyBest(steals):
        global clientId
        app = ibThreading()
        app.connect("127.0.0.1", port, clientId)
        sleep(3)
        for i in (0, 1):
            order = Order()
            clientId+=1
            order.orderId = clientId
            order.action = "BUY"
            order.orderType = "MKT"
            order.totalQuantity = 100
            order.tif = "GTC"
            app.placeOrder(order.orderId, steals[i][0], order)
        threading.Timer(10, app.stop).start()
        app.run()
        
    # Print Scanner Results
    def printPosition():
        for i in globalDict:
            contract = globalDict[i][0]
            position = decimalMaxString(globalDict[i][1])
            avgCost = floatMaxString(globalDict[i][2])
            print(f"Account: {i}; Symbol: {contract.symbol}; Position: {position}; Average Cost: {avgCost}")
        return
    
    # print top change
    def printTopDif():
        global globalDict
        print("\nThe top 5 Orders, Compared to this morning's opening:")
        for i in range(0,5):
            symbol = globalDict[i][0].symbol
            yesterday = globalDict[i][5]
            today = globalDict[i][4]
            now = globalDict[i][3]
            
            print(f"Symbol: {symbol}; Current Price: {now} ")
            print(f"Today's bar: Open: {today.open}, High: {today.high}, Low: {today.low}, Close: {today.close};")
            print(f"{yesterday.date}'s bar: Open: {yesterday.open}, High: {yesterday.high}, Low: {yesterday.low}, Close: {yesterday.close}; ")
            print(f"Change from this morning: {globalDict[i][8]:.4f} OR {globalDict[i][6]:.4f}%.")
            print(f"Change from last trade day: {globalDict[i][9]:.4f} OR {globalDict[i][7]:.4f}%. \n")
        return

def main():
    app = ibThreading()
    app.connect("127.0.0.1", 7497, 0)

    threading.Timer(5, app.stop).start()
    app.run()

if __name__ == "__main__":
    main()