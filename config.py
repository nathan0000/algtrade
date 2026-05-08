# config.py
# Place this file in the SAME folder as ibkr_conid_helper.py
# You can override any value here. All values are optional.

IB_HOST = "127.0.0.1"          # Change if TWS/Gateway is on another machine
IB_PORT = 4002                 # 7497 = paper trading, 7496 = live (usually)
IB_CLIENT_ID = 999             # Any unique number (avoid 0-10)
IB_TIMEOUT = 15                # Seconds to wait for contract details / connection