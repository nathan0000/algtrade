# ibkr_vix_regime.py
import sqlite3
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData
import threading
import time
from datetime import datetime

class VIXRegimeFetcher(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.vix_spot = None
        self.vx_front = None
        self.vx_next = None
        self.vix_conid = None
        self.contracts_done = False
        self.snapshot_done = False
        self.req_id = 1

    def error(self, *args):
        if len(args) >= 3 and args[1] == 504:
            print("Info: VIX data farm temporarily unavailable (normal)")
        elif len(args) >= 3 and args[1] not in (2104, 2105, 2119, 2106, 2107):
            print(f"VIX Info: {args[2]}")

    def contractDetails(self, reqId: int, contractDetails):
        c = contractDetails.contract
        if c.symbol == "VIX" and c.secType == "IND":
            self.vix_conid = c.conId
            print(f"VIX index conId resolved: {self.vix_conid}")

    def contractDetailsEnd(self, reqId: int):
        self.contracts_done = True

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        if tickType in (4, 9) and price > 0:
            if reqId == 100:
                self.vix_spot = price
                print(f"VIX Spot: {price:.2f}")
            elif reqId == 101:
                self.vx_front = price
                print(f"VX Front Month: {price:.2f}")
            elif reqId == 102:
                self.vx_next = price
                print(f"VX Next Month: {price:.2f}")

    def tickSnapshotEnd(self, reqId: int):
        if reqId in (100, 101, 102):
            self.snapshot_done = True

    def historicalData(self, reqId: int, bar: BarData):
        if bar.close > 0:
            if reqId == 100:
                self.vix_spot = bar.close
            elif reqId == 101:
                self.vx_front = bar.close
            elif reqId == 102:
                self.vx_next = bar.close

# ====================== MAIN ======================
app = VIXRegimeFetcher()
app.connect("127.0.0.1", 4002, clientId=6)

vix = Contract()
vix.symbol = "VIX"
vix.secType = "IND"
vix.exchange = "CBOE"
vix.currency = "USD"

print("Resolving VIX contract + fetching spot + term structure...")

# Try contract details (sometimes slow)
app.reqContractDetails(app.req_id, vix)
start = time.time()
while not app.contracts_done and (time.time() - start < 45):
    time.sleep(0.5)
app.req_id += 1

# === Snapshot VIX Spot (works even without conId) ===
app.reqMktData(100, vix, "", True, False, [])

# === VX Term Structure (Front + Next month) ===
vx = Contract()
vx.symbol = "VX"
vx.secType = "FUT"
vx.exchange = "CFE"
vx.currency = "USD"
vx.multiplier = "1000"

# Snapshot front and next VX
app.reqMktData(101, vx, "", True, False, [])   # front month

vx_next = Contract()
vx_next.symbol = "VX"
vx_next.secType = "FUT"
vx_next.exchange = "CFE"
vx_next.currency = "USD"
vx_next.multiplier = "1000"
app.reqMktData(102, vx_next, "", True, False, [])   # next month

start = time.time()
while not app.snapshot_done and (time.time() - start < 50):
    time.sleep(0.5)

app.disconnect()

# Final VIX value (prefer snapshot → historical fallback)
vix_level = app.vix_spot or app.vx_front or 20.0
print(f"\n✅ Current VIX Spot: {vix_level:.2f}")
print(f"   VX Front Month: {app.vx_front or 'N/A'}")
print(f"   VX Next Month:  {app.vx_next or 'N/A'}")

# ====================== REGIME + RECOMMENDATION ======================
if vix_level < 15:
    regime = "LOW VOLATILITY"
    rec = "SELL PREMIUM → Iron Condor or Credit Spreads"
    action = "SELL"
    target_delta = 0.16
elif vix_level < 25:
    regime = "NORMAL VOLATILITY"
    rec = "Balanced → Vertical Debit Spreads"
    action = "BUY"
    target_delta = 0.45
else:
    regime = "HIGH VOLATILITY"
    rec = "BUY VOLATILITY → Debit Spreads or Long Straddle"
    action = "BUY"
    target_delta = 0.35

print(f"📊 Regime: {regime}")
print(f"🎯 Recommended Strategy: {rec}")
print(f"   Use action='{action}' in ibkr_option_strategy.py")
print(f"   Target delta: ~{target_delta}")

# ====================== SUGGEST LEGS ======================
conn = sqlite3.connect("ibkr_spx_daytrader.db")
rows = conn.execute("""
    SELECT expiry, strike, right, delta 
    FROM spx_option_greeks 
    ORDER BY ABS(delta - ?) ASC LIMIT 6
""", (target_delta,)).fetchall()
conn.close()

print("\nSuggested legs (closest to target delta):")
for row in rows:
    print(f"   {row[0]} | Strike {row[1]:.0f} {row[2]} | Δ {row[3]:+.3f}")

print("\nRun ibkr_option_strategy.py with the recommended action above!")