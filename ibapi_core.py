from ibapi.client import EClient
from ibapi.common import HistogramData, TagValueList, TickerId
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.ticktype import TickTypeEnum
import time, queue
import pandas as pd
from dateutil import parser
import threading
import logging

host = "localhost"
port = 7497

global globalDict
globalDict = {}

global clientId
clientId = 1001

class CoreApp(EWrapper, EClient):
    def __init__(self, host, port, clientId):
        EWrapper.__init__(self)
        EClient.__init__(self, self) 

        self.data_queue_dict = {}
        self.datetime_list = list()
        self.open_list = list()
        self.high_list = list()
        self.low_list = list()
        self.close_list = list()
        self.volume_list = list()

        self.connect(host=host, port=port, clientId = clientId)

        thread = threading.Thread(target=self.run)
        thread.start()
        setattr(self, "_thread", thread)

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson = ""):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        if advancedOrderRejectJson:
            logging.debug(f"Error. Id: {reqId}, Code: {errorCode}, Msg: {errorString}, AdvancedOrderRejectJson: {advancedOrderRejectJson}")
        else:                  
            logging.debug(f"Error. Id: {reqId}, Code: {errorCode}, Msg: {errorString}")

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        logging.debug("Connection successful, Next valid Id: {}".format(orderId))
        self.nextValidOrderId = orderId

    def historicalData(self, reqId, bar):
        super().historicalData(reqId, bar)
        self.data_queue_dict[reqId].put(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        super().historicalDataEnd(reqId, start, end)
        logging.debug(f"Finished receiving current batch of historical data. Start: {start}. End: {end}")

        while not self.data_queue_dict[reqId].empty():
            bar_data = self.data_queue_dict[reqId].get()

            self.datetime_list.append(bar_data.date)
            self.open_list.append(bar_data.open)
            self.high_list.append(bar_data.high)
            self.low_list.append(bar_data.low)
            self.close_list.append(bar_data.close)
            self.volume_list.append(bar_data.volume)

    def req_Historical_Data(self, reqId: TickerId, contract: Contract, endDateTime: str, durationStr: str, barSizeSetting: str, whatToShow: str, useRTH: int, formatDate: int, keepUpToDate: bool, chartOptions: TagValueList):
        self.reqHistoricalData(
            reqId=reqId, 
            contract=contract, 
            endDateTime=endDateTime, 
            durationStr=durationStr, 
            barSizeSetting=barSizeSetting, 
            whatToShow=whatToShow, 
            useRTH=useRTH, 
            formatDate=formatDate, 
            keepUpToDate=keepUpToDate, 
            chartOptions=[])
    
        logging.debug(f"Requesting historical data for contract")

        if reqId not in self.data_queue_dict.keys():
            logging.debug(f"setting up queue for reqId {reqId}")
            self.data_queue_dict[reqId] = queue.Queue()
        
        return reqId
    
    def data_to_dataframe(self):

        data = {
            "open": self.open_list,
            "high": self.high_list,
            "low": self.low_list,
            "close": self.close_list,
            "volume": self.volume_list,
        }

        dataframe = pd.DataFrame(data, index=self.datetime_list)
        dataframe.sort_index(inplace=True)

        return dataframe

def main():
    app = CoreApp(host, port, clientId)
    print("getting historical data")

    mycontract = Contract()
    mycontract.symbol = "SPY"
    mycontract.secType = "STK"
    mycontract.exchange = "SMART"
    mycontract.currency = "USD"
    dt_range = pd.date_range(start="20231123-23:59:59", end="20231124-23:59:59", freq="24H")

    for dt in dt_range[::-1]:
        reqId = app.req_Historical_Data(
            reqId=1001,
            contract=mycontract,
            endDateTime=dt.strftime("%Y%m%d-%H:%M:%S"),
            barSizeSetting="1 min",
            durationStr="1 D",
            whatToShow="TRADES",
            useRTH=1,
            formatDate=1,
            keepUpToDate=-False,
            chartOptions="XYZ"
        )
    time.sleep(2)

    app.disconnect()
    df = app.data_to_dataframe()
    logging.debug(f"data received: {df}")
    df.to_csv("my_historical_data.csv", mode="w")

if __name__ == "__main__":
    main()

