import requests
import urllib3
import yaml
import pandas as pd
import json, logging, logging.config
from logging.handlers import RotatingFileHandler
import csv, json, time

# Ignore insecure error messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
baseUrl = 'https://localhost:5050/v1/api'
paper_account="DU3232524"

def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('trading_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

def accountInfo(account='DU3232524'):
  url = f'{baseUrl}/iserver/accounts'
  
  account_request = requests.get(url=url, verify=False)
  logger.debug('account request status: {}'.format(account_request.status_code))

  logger.info('account info: {}'.format(account_request.text))

  return json.dumps(account_request.text)

def positionInfo(account=paper_account):
  url = f'{baseUrl}/portfolio/{account}/positions/0'

  pos_req = requests.get(url=url, verify=False)
  logger.debug(f'position request status: {pos_req.status_code}')
  logger.debug(f'position info: {pos_req}')

  if pos_req.status_code == '200':
    pos_json = json.dumps(pos_req.json(), indent=2)
    return pos_json
  else:
    return pos_req.status_code

def orderInfo():
  url = f'{baseUrl}/iserver/account/orders'

  search_order = requests.get(url=url, verify=False)
  logger.debug('order search status: {}'.format(search_order.status_code))
  logger.info('order info: {}'.format(search_order.text))

  return json.dumps(search_order.text)

def tradesInfo():
  url = f'{baseUrl}/iserver/account/trades?days=4'

  search_trades = requests.get(url=url, verify=False)
  logger.debug('trades request status: {}'.format(search_trades.status_code))
  logger.info('trades info: {}'.format(search_trades.text))

  return json.dumps(search_trades.text)

def optionIndex(ticker='SPX'):
  pass

def optionSamurai(input="samurai.csv"):
  ds_samurai = pd.read_csv(input, delimiter=',')
  ds_samurai_work = ds_samurai[["Include symbols",  "Options"]]
#  ds_samurai_work["Strike"] = ds_samurai_work["Strike"].apply(lambda x: x.split('/'))
#  ds_samurai_work["Options"] = ds_samurai_work["Options"].apply(lambda x: x.split('/'))
#  ds_samurai_work.explode("Options")
  print(ds_samurai_work.dtypes)
'''
      underConid,months = secdefSearch(symbol, exchange)
      month = months[0]
      itmStrikes = secdefStrikes(underConid,month)
      contractDict = {}
      for strike in itmStrikes:
        contractDict[strike] = secdefInfo(underConid,month,strike)
      writeResult(contractDict)

  return
'''

def orderCombo(account="DU3232524"):
  us_spread_conid = "28812380"
  order_data = {
    "orders": [
      {
        "conidex": "{us_spread_conid};;;{'734452809'}/{1},{'740950685'}/{-1},{'740950728'}/{1},{'740950849'}/{-1}",
        "orderType": "LMT",                # Limit order type
        "quantity": 1,                # Total quantity of contracts (e.g., 1 for one Iron Condor)
        "price": 2.50,                  # Limit price for the entire order
        "tif": "GTC",              # Order validity (Good Till Canceled)
        "outsideRTH": False,               # Whether to allow orders outside regular trading hours
        "listingExchange": "SMART",
        "side": "BUY",
      }
  ]
  }

  return order_data

def orderSimple(account="DU3232524"):
  
  order_data = {
    "orders": [
      {
        "conid": 734452939,
        "orderType": "LMT",                # Limit order type
        "price": 1.50,                  # Limit price for the entire order
        "side": "BUY",
        "tif": "DAY",              # Order validity (Good Till Canceled)
        "quantity": 1              # Total quantity of contracts (e.g., 1 for one Iron Condor)
      }
    ]
  }

  return order_data


def orderPlace(account='DU3232524', orderDetails=''):
  url = f'{baseUrl}/iserver/account/{account}/orders'

  logger.debug(f'order detail data: {orderDetails}')
  json_content = orderDetails
  
  order_request = requests.post(url=url, json=json_content, verify=False)
  logger.debug(f'order submit status: {order_request.status_code}')
  
  # handling order response

  return order_request.json()

def orderReply(replyId="", confirmed=True):
  if replyId is None:
    return

  url = f'{baseUrl}/iserver/reply/{replyId}'

  json_data = {"confirmed":confirmed}

  reply_req = requests.post(url=url, verify=False, json=json_data)

  return json.dumps(reply_req.json(), indent=2)

def secdefSearch(symbol, listingExchange):

  url = f'{baseUrl}/iserver/secdef/search?symbol={symbol}'

  search_request = requests.get(url=url, verify=False)
  logger.debug('search request code: {}'.format(search_request.status_code))
  logger.debug(f'sec def search response: {search_request.json()}')

  for contract in search_request.json():
    if contract["description"] == listingExchange:
      underConid = contract["conid"]

      for secType in contract["sections"]:
         if secType["secType"] == "OPT":
            months = secType["months"].split(';')

  return underConid,months

def idxdefPrice(idxConid):
  url = f'{baseUrl}/iserver/marketdata/snapshot?conids={idxConid}&fields=31,84,86,7295,7296'
  requests.get(url=url, verify=False)
  snapshot = requests.get(url=url, verify=False)
  logger.debug('index marketdata status code: {}'.format(snapshot.status_code))
  logger.debug('index market price: {}'.format(snapshot.json()[0]))
  return snapshot.json()[0]["31"]

def secdefStrikes(underConid,month):

  snapshot = float(snapshotData(underConid))
#  snapshot = float(idxdefPrice(underConid))
  logger.debug(f'market snapshot price: {snapshot}')

  itmStrikes = []

  url = f'{baseUrl}/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}'

  strike_request = requests.get(url=url, verify=False)
  logger.debug('strike_request status code: {}'.format(strike_request.status_code))
  logger.debug(f'strike request response: {strike_request.json()}')

  strikes = strike_request.json()["put"]
  for strike in strikes:
    if strike>snapshot-5 and strike<snapshot+5:
      itmStrikes.append(strike)
  return itmStrikes

def secdefInfo(conid, month, strike):

  url = f'{baseUrl}/iserver/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT'

  info_request = requests.get(url=url, verify=False)
  logger.debug('info_request status code: {}'.format(info_request.status_code))

  contracts = []

  for contract in info_request.json():
    # add option ask/bid price&size
    optionPrice = snapshotOption(contract["conid"])
    optionPrice_string = json.dumps(optionPrice)

    contractDetails = {"conid": contract["conid"], 
                       "symbol": contract["symbol"],
                       "right": contract["right"],
                       "strike": contract["strike"],
                       "prices": optionPrice_string,
                       "maturityDate": contract["maturityDate"]
                      }
    contracts.append(contractDetails)
  logger.debug(f"contract details: {contracts}")
  return contracts

def regulatorySnapshotData(underConid):
  url = f'{baseUrl}/md/regsnapshot?conid={underConid}'
  requests.get(url=url, verify=False)
  snapshot = requests.get(url=url, verify=False)
  logger.debug('regulatory_snapshot_request status code: {}'.format(snapshot.status_code))
  logger.debug('regulatory snapshotData price: {}'.format(snapshot.json()))
  if snapshot.json()[0]["31"] is None:
    logger.debug(f'snapshot price is None')
  else:
    price = snapshot.json()[0]["31"]
  return price

def snapshotData(underConid):
  url = f'{baseUrl}/iserver/marketdata/snapshot?conids={underConid}&fields=31'
  requests.get(url=url, verify=False)
  url = f'{baseUrl}/iserver/marketdata/snapshot?conids={underConid}'
  snapshot = requests.get(url=url, verify=False)
  logger.debug('snapshot_request status code: {}'.format(snapshot.status_code))
  logger.debug('snapshotData price: {}'.format(snapshot.json()))
  if "31" not in snapshot.json()[0]:
    logger.debug(f'snapshot price is None')
    price = 0
  else:
    price = snapshot.json()[0]["31"]
  return price

def snapshotOption(optionConid):
  url = f'{baseUrl}/iserver/marketdata/snapshot?conids={optionConid}&fields=31,84,86,88,85'
  optionPrices = {}
  requests.get(url=url, verify=False)
  url = f'{baseUrl}/iserver/marketdata/snapshot?conids={optionConid}'
  optionSnapshot = requests.get(url=url, verify=False)
  logger.debug('options snapshot_request status code: {}'.format(optionSnapshot.status_code))
  logger.debug('option snapshot data: {}'.format(optionSnapshot.json()))
  last_price, ask_price, bid_price, ask_size, bid_size = 0, 0, 0, 0, 0
  if "31" not in optionSnapshot.json()[0]:
    logger.debug(f'option last price is NOne')
    last_price = 0
  else:
    last_price = optionSnapshot.json()[0]["31"]
  if "84" not in optionSnapshot.json()[0]:
    logger.debug(f'option bid price is NOne')
    ask_price = 0
  else:
    ask_price = optionSnapshot.json()[0]["84"]
  if "86" not in optionSnapshot.json()[0]:
    logger.debug(f'option ask price is NOne')
  else:
    bid_price = optionSnapshot.json()[0]["86"]
  if "88" not in optionSnapshot.json()[0]:
    logger.debug(f'option bid size NOne')
    ask_size = 0
  else:
    ask_size = optionSnapshot.json()[0]["88"]
  if "85" not in optionSnapshot.json()[0]:
    logger.debug(f'option ask size is NOne')
    biz_size = 0
  else:
    bid_size = optionSnapshot.json()[0]["85"]

  optionPrices = {"lastPrice": last_price, "bidPrice": bid_price,"bidSize": bid_size, \
                      "ask": ask_price,"askSize": ask_size}
  logger.debug(f"contract: {optionConid} option prices: {optionPrices}")
  return optionPrices

def writeResult(contractDict):
  headers = ["conid", "symbol", "strike", "optionPrices", "maturityDate"]
  filePath = "./MayContracts.csv"
  contract_csv_file = open(filePath, 'w', newline='')
  contract_writer = csv.DictWriter(f=contract_csv_file, fieldnames=headers)
  contract_writer.writeheader()
  for strikeGroup in contractDict:
    for contractDetails in contractDict[strikeGroup]:
      contract_writer.writerow(contractDetails)
  contract_csv_file.close()
  print("Job's done.")

def writeJsonResult(contractDict):
  filePath = "./MayContracts.json"
  contractJson = json.dumps(contractDict, indent=2)
  with open(filePath, 'w', newline='') as contract_json_file:
    contract_json_file.write(contractJson)
  print("Json file written.")

if __name__ == "__main__":
  
  with open('config.yml', 'r') as c:
    config = yaml.safe_load(c)
    baseUrl = config['baseUrl']
    paper_account = config['paper_account']
    live_short_account = config['live_short_account']
    live_long_account = config['live_long_account']
  
  # Setup Logging
  logger = loggerSetup()

  # get position info
#  positions = positionInfo(paper_account)
#  print(f'postions: {positions}')

  # parse option samurai
  #optionSamurai("./samurai.csv")

  # return account info
  #account_info = accountInfo()
  #logger.debug('account info: {}'.format(account_info))

  # search order info
  #order_info = orderInfo()
  #print(f'order info: {order_info}')

  # search trades info
  #trades_info = tradesInfo()
  #print(f'trades info: {trades_info}')

  #prepare and submit order
  #order_data = orderSimple()
  #logger.debug(f'order data: {json.dumps(order_data)}')
  #order_response = orderPlace(paper_account, order_data)
  #print(f'order response : {order_response}')
  #print(f'messageIds: {order_response[0]["messageIds"]}')
  #handle order reply message
  #if order_response[0]["messageIds"][0] == "o163":
  #  replyId = order_response[0]["id"]
  #  order_reply = orderReply(replyId, True)
  #  logger.debug(f'order confirmed: {order_reply}')

  # I'm looking for the U.S. Apple Incorporated company listed on NASDAQ
  underConid,months = secdefSearch("TSLA", "NASDAQ")
  logger.debug(f'security def search: {underConid, months}')
  # I only want the front month. 
  # Users could always grab all months, or pull out a specific value, but sending the 0 value always gives me the first available contract.
  month = months[0]

  # We'll be calling our Strikes endpoint to pull in the money strike prices rather than all strikes.
  itmStrikes = secdefStrikes(underConid,month)
  print(f'item strike: {itmStrikes}')

  # We can then pass those strikes to the /info endpoint, and retrieve all the contract details we need.
  contractDict = {}
  for strike in itmStrikes:
    contractDict[strike] = secdefInfo(underConid,month,strike)

  writeJsonResult(contractDict)
