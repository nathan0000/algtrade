from ibapi.client import *
from ibapi.wrapper import *
from ibapi.contract import Contract, ContractDetails
import time, threading, logging, logging.config
from logging.handlers import RotatingFileHandler
import yaml
from datetime import datetime

class TradeApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.tick_price = None

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson = ""):
        print("Error. Id:", reqId, "Code:", errorCode, "Msg:", errorString, "AdvancedOrderRejectJson:", advancedOrderRejectJson)
    
    def accountSummary(self, reqId: int, account: str, tag: str, value: str,currency: str):
        print("AccountSummary. ReqId:", reqId, "Account:", account,"Tag: ", tag, "Value:", value, "Currency:", currency)
    
    def accountSummaryEnd(self, reqId: int):
        print("AccountSummaryEnd. ReqId:", reqId)

    def position(self, account: str, contract: Contract, position: Decimal, avgCost: float):
        print("Position.", "Account:", account, "Contract:", contract, "Position:", position, "Avg cost:", avgCost)

    def positionEnd(self):
        print("PositionEnd")
    
    def tickPrice(self, reqId, tickType, price, attrib):
        self.index_price = price
        print('The current ask price is: ', price)

    def symbolSamples(self, reqId: int, contractDescriptions: ListOfContractDescription):
        print("Symbol Samples. Request Id: ", reqId)
        idx_symbol_conid = []
        for contractDescription in contractDescriptions:
            derivSecTypes = ""
            for derivSecType in contractDescription.derivativeSecTypes:
                derivSecTypes += " "
                derivSecTypes += derivSecType
                print("Contract: conId:%s, symbol:%s, secType:%s primExchange:%s, "
                    "currency:%s, derivativeSecTypes:%s, description:%s, issuerId:%s" % (
                    contractDescription.contract.conId,
                    contractDescription.contract.symbol,
                    contractDescription.contract.secType,
                    contractDescription.contract.primaryExchange,
                    contractDescription.contract.currency, derivSecTypes,
                    contractDescription.contract.description,
                    contractDescription.contract.issuerId))
            if (contractDescription.contract.secType == "IND" and derivSecTypes == "OPT"):
                idx_symbol_conid.append([contractDescription.contract.symbol, derivSecType])
        return idx_symbol_conid

    def securityDefinitionOptionParameter(self, reqId: int, exchange: str, underlyingConId: int, tradingClass: str, multiplier: str, expirations: SetOfString, strikes: SetOfFloat):
        print("SecurityDefinitionOptionParameter.", "ReqId:", reqId, "Exchange:", exchange, "Underlying conId:", underlyingConId, "TradingClass:", tradingClass, "Multiplier:", multiplier, "Expirations:", expirations, "Strikes:", strikes)

    def marketDataType(self, reqId: TickerId, marketDataType: int):
        print(f'marketDataType. ReqId: {reqId}, Type: {marketDataType}')

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        print(reqId, contractDetails)
        return super().contractDetails(reqId, contractDetails)
    
    def contractDetailsEnd(self, reqId: int):
        print("ContractDetailsEnd. ReqId:", reqId)

def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('trading_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

def idx0dte_strategy(symbol="SPX", listingExchange="CBOE", tradeDate=datetime.now()):
    zeroreqid = int((tradeDate - datetime(tradeDate.year, 1, 1)).days + 1)
    idxConid = app.reqMatchingSymbols(zeroreqid, symbol)
    print(f"index symbol: {symbol} and contract id: {idxConid}")
    
def run_loop():
    app.run()

with open('config.yml', 'r') as c:
    config = yaml.safe_load(c)
    baseUrl = config['baseUrl']
    paper_account = config['paper_account']
    live_short_account = config['live_short_account']
    live_long_account = config['live_long_account']
  
# Setup Logging
logger = loggerSetup()

app = TradeApp()
app.connect('127.0.0.1', 7497, 1210)

#Start the socket in a thread
api_thread = threading.Thread(target=run_loop, daemon=True)
api_thread.start()

time.sleep(1) #Sleep interval to allow time for connection to server

if app.isConnected:
    print(f"connected.")
    app.reqAccountSummary(9001, "All",  'NetLiquidation')
#    app.reqPositions()
#    app.reqMarketDataType(2)

    print(f'before strategy')
    idx0dte_strategy()
    #print(f"symbol search result: {idx_symbol_conid}")
    app.disconnect()
