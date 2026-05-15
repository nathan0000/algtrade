import json, logging, logging.config
from logging.handlers import RotatingFileHandler
import yaml
import asyncio
from tastytrade_sdk import Tastytrade

def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('tasty_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

def orderSetup(ticker='AAPL'):
  order_ticker = {
    "time-in-force": "Day",
    "order-type": "Market",
    "legs": [
      {
        "instrument-type": "Equity",
        "symbol": ticker,
        "quantity": 1,
        "action": "Buy to Open"
      }
    ]
  }
  return order_ticker

with open('config.yml', 'r') as c:
    config = yaml.safe_load(c)
    baseUrl = config['baseUrl']
    paper_account = config['paper_account']
    live_short_account = config['live_short_account']
    live_long_account = config['live_long_account']
  
  # Setup Logging
logger = loggerSetup()

tasty = Tastytrade('api.cert.tastyworks.com').login(login='yaoliang_w@hotmail.com', password='Invest2freedom.')

tasty.api.post('/sessions/validate')

# Get account information
#customer_account = tasty.api.get('/customers/me')
#print(customer_account)

equities = tasty.api.get(
    '/instruments/equities',
    params=[('symbol[]', 'SPY'), ('symbol[]', 'AAPL')]
)
print(equities)

#order = tasty.api.post(
#    'accounts/"D12345"/orders',
#    json=orderSetup('AAPL')
#)
#print(order)

# Subscribing to symbols across different instrument types
# Please note: The option symbols here are expired. You need to subscribe to an unexpired symbol to receive quote data
symbols = [
    'BTC/USD',
    'SPY',
    'QQQ'
]

"""
subscription = tasty.market_data.subscribe(
    symbols=symbols,
    on_quote=print,
    on_candle=print,
    on_greeks=print
)
"""

# start streaming
#subscription.open()