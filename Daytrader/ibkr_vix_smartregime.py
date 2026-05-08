# ibkr_smart_regime.py
import sqlite3
import pandas as pd
import numpy as np
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import threading
import time

class VIXFetcher(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.vix = None
        self.done = False
        self.connected = False
        self.req_id = 1

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="", arg5=""):
        if errorCode in (2104, 2105, 2106, 2107, 2108, 2119, 2158):
            print(f"   [INFO] TWS: {errorString} (code {errorCode})")
        elif errorCode == 504:
            print("   [INFO] VIX snapshot unavailable → using historical")
        elif errorCode == 300 or errorCode == 162:
            print("   [INFO] No historical data yet for VIX (normal)")
        else:
            print(f"   [ERROR] {errorString} (code {errorCode})")

    def nextValidId(self, orderId: int):
        self.connected = True
        print("   [SUCCESS] TWS Connected successfully")

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        if tickType in (4, 9) and price > 0:
            self.vix = price
            print(f"   [VIX] Live snapshot received: {price:.2f}")

    def tickSnapshotEnd(self, reqId: int):
        self.done = True

    def historicalData(self, reqId: int, bar):
        if bar.close > 0:
            self.vix = bar.close
            print(f"   [VIX] Historical close received: {bar.close:.2f}")

    def historicalDataEnd(self, reqId: int, start, end):
        self.done = True

# ====================== MAIN ======================
print("=== Smart Regime Analysis (VIX + SPX Technical) ===")

vix_app = VIXFetcher()
print("Connecting to TWS...")
vix_app.connect("127.0.0.1", 4002, clientId=6)

threading.Thread(target=vix_app.run, daemon=True).start()
time.sleep(2)

if not vix_app.connected:
    print("   [FAIL] Could not connect to TWS. Check TWS is running and API is enabled.")
    vix_app.disconnect()
    exit()

print("   [OK] TWS connection established\n")

# ====================== FETCH VIX ======================
vix_contract = Contract()
vix_contract.symbol = "VIX"
vix_contract.secType = "IND"
vix_contract.exchange = "CBOE"
vix_contract.currency = "USD"

print("Fetching VIX...")

# Snapshot
vix_app.reqMktData(100, vix_contract, "", True, False, [])
start = time.time()
while not vix_app.done and (time.time() - start < 25):
    time.sleep(0.5)
vix_app.cancelMktData(100)
vix_app.done = False

# Strong historical fallback (tries both MIDPOINT and TRADES)
if vix_app.vix is None:
    print("   Snapshot failed → trying historical fallback...")
    vix_app.reqHistoricalData(101, vix_contract, "", "1 D", "1 day", "MIDPOINT", 1, 1, False, [])
    start = time.time()
    while not vix_app.done and (time.time() - start < 45):
        time.sleep(0.5)

    if vix_app.vix is None:
        vix_app.done = False
        vix_app.reqHistoricalData(102, vix_contract, "", "1 D", "1 day", "TRADES", 1, 1, False, [])
        while not vix_app.done and (time.time() - start < 45):
            time.sleep(0.5)

vix_app.disconnect()
vix_level = vix_app.vix or 20.0
print(f"\n✅ Final VIX: {vix_level:.2f}\n")

# ====================== SPX TECHNICAL + COMBINED RECOMMENDATION ======================
# (SPX part unchanged - same as before)
conn = sqlite3.connect("ibkr_spx_daytrader.db")
df = pd.read_sql("SELECT date, open, high, low, close FROM spx_5min ORDER BY date", conn)
conn.close()

df['date'] = df['date'].str.replace(r' US/.*$', '', regex=True)
df['date'] = pd.to_datetime(df['date'], format='%Y%m%d %H:%M:%S', errors='coerce')
df = df.dropna(subset=['date']).set_index('date')

today_date = df.index.date[-1]
today_df = df[df.index.date == today_date].copy()

today_df['ema20'] = today_df['close'].ewm(span=20, adjust=False).mean()

today_open = today_df['open'].iloc[0]
today_close = today_df['close'].iloc[-1]
daily_range_pct = (today_df['high'].max() - today_df['low'].min()) / today_open * 100
slope = np.polyfit(np.arange(len(today_df)), today_df['close'], 1)[0]
above_ema = today_close > today_df['ema20'].iloc[-1]

if slope > 0.08 and above_ema and daily_range_pct > 0.75:
    spx_regime = "BULLISH TRENDING"
    action = "BUY"
elif slope < -0.08 and not above_ema and daily_range_pct > 0.75:
    spx_regime = "BEARISH TRENDING"
    action = "BUY"
else:
    spx_regime = "RANGE-BOUND"
    action = "SELL"

# Combined logic (same as before)
if vix_level > 25:
    final_action = "BUY"
    rec = "HIGH VOL + " + spx_regime + " → Debit spreads / Long volatility"
elif vix_level < 15:
    final_action = "SELL"
    rec = "LOW VOL + " + spx_regime + " → Iron Condor / Credit spreads"
else:
    final_action = action
    rec = "NORMAL VOL + " + spx_regime + " → Vertical spreads"

print(f"🎯 TODAY'S SMART RECOMMENDATION: {rec}")
print(f"   → Use action='{final_action}' in ibkr_option_strategy.py")
print("\nScript complete. TWS connection status logged above.")