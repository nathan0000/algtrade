import requests
import urllib3
import yaml
import pandas as pd
import json, logging, logging.config
import csv, json, time

def check_authStatus():
  url = f'{baseUrl}/iserver/auth/status'
  authStatus = requests.get(url=url, verify=False).json()
  print(f'auth status: {authStatus}')
  if authStatus['authenticated'] == True:
    return True
  elif authStatus['connected'] == True:
    return True
  else:
    return False


def keep_live():
  url = f'{baseUrl}/tickle'
  authStatus = check_authStatus()
  print(f'auth status: {authStatus}')
  while authStatus == True:
    keepalive_content = {}
    keepalive_request = requests.post(url=url, json=keepalive_content, verify=False).json()
    print(f'keep live: {keepalive_request}')
    starttime = time.time()
    time.sleep(280)
    elapsedTime = time.time() - starttime
    print(f"Elapsed Time = {elapsedTime}")
    authStatus = check_authStatus()
  if authStatus == False:
    return

if __name__ == "__main__":
  
  urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

  with open('config.yml', 'r') as c:
    config = yaml.safe_load(c)
    baseUrl = config['baseUrl']
    paper_account = config['paper_account']
    live_short_account = config['live_short_account']
    live_long_account = config['live_long_account']

  keep_live()