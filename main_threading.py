# %load ibkr-api/account_summary.py
from threading import Thread, Event
import time, yaml
import json, logging, logging.config
from logging.handlers import RotatingFileHandler
from typing import Any
import pandas as pd
from ipapi_accounts import AccountApp



orderId = None
count = 0
combined_bar_df = pd.DataFrame(columns=['conid', 'date', 'open', 'high', 'low', 'close', 'volume'])
bar_df = pd.DataFrame(columns=['conid', 'date', 'open', 'high', 'low', 'close', 'volume'])
screen_df = pd.DataFrame(columns=['conid', 'symbol', 'rank'])
done = Event()  # use threading.Event to signal between threads
connection_ready = Event()  # to signal the connection has been established
    
def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('trading_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

# define our event loop - this will run in its own thread

def accsum(client):
    # request account summary
    print("Requesting account summary")
    
    print(f"main threading account position: {client.position_ref}")


def main():
    logger = loggerSetup()

    with open('config.yml', 'r') as c:
        config = yaml.safe_load(c)
        baseUrl = config['baseUrl']
        paper_account = config['paper_account']
        live_short_account = config['live_short_account']
        live_long_account = config['live_long_account']
        addr = config["twsapi_addr"]
        port = config["twsapi_paperport"]
        clientId = config["twsapi_clientId"]

    account = AccountApp()
    accsum(account)

if __name__ == "__main__":
    main()