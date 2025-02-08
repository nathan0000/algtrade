import json, logging, logging.config
from logging.handlers import RotatingFileHandler
import yaml
import asyncio
from tastytrade import CertificationSession, Account

def loggerSetup():
  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  ch = RotatingFileHandler('tasty_api.log', maxBytes=10000000, backupCount=5, encoding='utf-8')
  ch.setLevel(logging.DEBUG)

  formatter = logging.Formatter('%(asctime)s | %(name)s | %(levelname)s | %(message)s')
  ch.setFormatter(formatter)

  logger.addHandler(ch)
  return logger

with open('config.yml', 'r') as c:
    config = yaml.safe_load(c)
    baseUrl = config['baseUrl']
    paper_account = config['paper_account']
    live_short_account = config['live_short_account']
    live_long_account = config['live_long_account']
  
  # Setup Logging
logger = loggerSetup()

session = CertificationSession('yaoliang_w@hotmail.com', 'Invest2freedom.', remember_me=True)
remember_token = session.remember_token

accounts = Account.get_accounts(session)
logger.debug(f'remember token: {remember_token}, accounts: {accounts}')

