import threading
import time
import sqlite3
from datetime import datetime, timedelta
from math import log, sqrt, erf
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData

# ====================== FAST ESTIMATED DELTA FOR PRE-FILTER ======================
def norm_cdf(x: float) -> float:
    return (1.0 + erf(x / sqrt(2.0))) / 2.0

def est_delta(S: float, K: float, t: float, right: str, sigma: float = 0.18) -> float:
    if t <= 0.0001:
        return 1.0 if S > K else 0.0
    d1 = (log(S / K) + (0 + 0.5 * sigma**2) * t) / (sigma * sqrt(t))
    delta = norm_cdf(d1)
    return delta if right == "C" else delta - 1

class IBKRSpxOptionChain(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.spot = None
        self.all_options = []
        self.greeks_data = {}
        self.pending_snapshots = set()
        self.contract_details_done = False
        self.spot_done = False
        self.historical_done = False
        self.spot_req_id = None
        self.req_id = 1

    def error(self, *args):
        if len(args) >= 3:
            reqId, errorCode, errorString = args[:3]
            if errorCode in (2104, 2105, 2119, 2106, 2107, 2108):
                print(f"Info: {errorString}")
            else:
                print(f"Error {reqId} | Code {errorCode}: {errorString}")

    def contractDetails(self, reqId: int, contractDetails):
        c = contractDetails.contract
        if c.secType == "OPT" and c.symbol == "SPX":
            self.all_options.append(c)
            if len(self.all_options) % 1000 == 0:
                print(f"   Received {len(self.all_options)} option contracts...")

    def contractDetailsEnd(self, reqId: int):
        print(f"✅ Full SPX chain loaded: {len(self.all_options)} contracts")
        self.contract_details_done = True

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        if reqId in self.greeks_data:
            if tickType == 1: self.greeks_data[reqId]["bid"] = price
            if tickType == 2: self.greeks_data[reqId]["ask"] = price
            if tickType == 4: self.greeks_data[reqId]["last"] = price

    def tickOptionComputation(self, reqId: int, tickType: int, impliedVol: float, delta: float,
                              optPrice: float, pvDividend: float, gamma: float, vega: float,
                              theta: float, undPrice: float, modelPrice: float):
        if reqId in self.greeks_data:
            self.greeks_data[reqId].update({
                "iv": impliedVol, "delta": delta, "gamma": gamma,
                "vega": vega, "theta": theta, "undPrice": undPrice,
                "modelPrice": modelPrice
            })

    def tickSnapshotEnd(self, reqId: int):
        if reqId == self.spot_req_id:
            self.spot_done = True
        if reqId in self.pending_snapshots:
            self.pending_snapshots.remove(reqId)

    def historicalData(self, reqId: int, bar: BarData):
        if bar.close > 0:
            self.spot = bar.close

    def historicalDataEnd(self, reqId: int, startDateStr: str, endDateStr: str):
        self.historical_done = True

# ====================== MAIN ======================
app = IBKRSpxOptionChain()
app.connect("127.0.0.1", 4002, clientId=3)

api_thread = threading.Thread(target=app.run, daemon=True)
api_thread.start()
time.sleep(2)

# === 1. SPX Spot ===
spx = Contract()
spx.symbol = "SPX"
spx.secType = "IND"
spx.exchange = "CBOE"
spx.currency = "USD"

print("Getting SPX spot...")
app.spot_req_id = app.req_id
app.reqMktData(app.req_id, spx, "", True, False, [])
start = time.time()
while not app.spot_done and (time.time() - start < 15):
    time.sleep(0.5)
app.cancelMktData(app.req_id)
app.req_id += 1

if app.spot is None:
    print("Snapshot failed → using 1D historical close...")
    app.reqHistoricalData(app.req_id, spx, "", "1 D", "1 day", "TRADES", 1, 1, False, [])
    start = time.time()
    while not app.historical_done and (time.time() - start < 30):
        time.sleep(0.5)
    app.req_id += 1

print(f"Spot price: {app.spot:.2f}")

# === 2. Full SPX Option Chain ===
print("Loading full SPX option chain (30-90 seconds)...")
opt = Contract()
opt.symbol = "SPX"
opt.secType = "OPT"
opt.exchange = "CBOE"
opt.currency = "USD"
opt.multiplier = "100"

app.reqContractDetails(app.req_id, opt)
start = time.time()
while not app.contract_details_done and (time.time() - start < 180):
    time.sleep(0.5)
app.req_id += 1

# ====================== FILTER + REAL GREEKS ======================
today_str = datetime.now().strftime("%Y%m%d")
tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

exps = sorted({c.lastTradeDateOrContractMonth for c in app.all_options})
filtered_exps = [e for e in exps if e in (today_str, tomorrow_str)]
if not filtered_exps and exps:
    filtered_exps = exps[:2]

print(f"Using expirations: {filtered_exps}")

targets = [0.50, 0.35, 0.20, 0.15, 0.10, 0.09, 0.08]
candidates = []

for c in app.all_options:
    if c.lastTradeDateOrContractMonth not in filtered_exps:
        continue
    days_left = (datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d").date() - datetime.now().date()).days
    t = max(days_left + 0.25, 0.01) / 365.0
    delta_est = est_delta(app.spot, c.strike, t, c.right)
    for target in targets:
        if abs(abs(delta_est) - target) < 0.04:
            candidates.append(c)
            break

print(f"Found {len(candidates)} candidate contracts → requesting real Greeks...")

tradable_options = []
for contract in candidates:
    reqId = app.req_id
    app.greeks_data[reqId] = {"bid": None, "ask": None, "last": None,
                             "iv": None, "delta": None, "gamma": None,
                             "vega": None, "theta": None}
    app.pending_snapshots.add(reqId)

    app.reqMktData(reqId, contract, "100,101,104,106,107,221,223", True, False, [])
    app.req_id += 1

    start = time.time()
    while reqId in app.pending_snapshots and (time.time() - start < 8):
        time.sleep(0.5)

    g = app.greeks_data.get(reqId, {})
    tradable_options.append({
        "contract": contract,
        "expiry": contract.lastTradeDateOrContractMonth,
        "strike": contract.strike,
        "right": contract.right,
        "bid": g.get("bid"),
        "ask": g.get("ask"),
        "last": g.get("last"),
        "iv": g.get("iv"),
        "delta": g.get("delta"),
        "gamma": g.get("gamma"),
        "vega": g.get("vega"),
        "theta": g.get("theta")
    })

app.disconnect()

# ====================== SAVE TO SQLITE ======================
db_file = "ibkr_spx_daytrader.db"
conn = sqlite3.connect(db_file)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS spx_option_greeks (
    expiry TEXT,
    strike REAL,
    right TEXT,
    conId INTEGER,
    bid REAL,
    ask REAL,
    last REAL,
    iv REAL,
    delta REAL,
    gamma REAL,
    vega REAL,
    theta REAL,
    timestamp TEXT,
    PRIMARY KEY (expiry, strike, right)
)
""")

ts = datetime.now().isoformat()
for item in tradable_options:
    cur.execute("""
        INSERT OR REPLACE INTO spx_option_greeks 
        (expiry, strike, right, conId, bid, ask, last, iv, delta, gamma, vega, theta, timestamp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        item["expiry"],
        item["strike"],
        item["right"],
        item["contract"].conId,
        item.get("bid"),
        item.get("ask"),
        item.get("last"),
        item.get("iv"),
        item.get("delta"),
        item.get("gamma"),
        item.get("vega"),
        item.get("theta"),
        ts
    ))

conn.commit()
conn.close()

print(f"\n✅ Saved {len(tradable_options)} contracts with real Greeks to spx_option_greeks table")

# ====================== OUTPUT WITH SAFE FORMATTING ======================
print("\n" + "="*100)
print("✅ SPX OPTIONS (TODAY & TOMORROW) — REAL GREEKS + TRADABLE CONTRACTS")
print(f"Spot: {app.spot:.2f}")
print("="*100)

for item in sorted(tradable_options, key=lambda x: (x["expiry"], x["strike"])):
    c = item["contract"]
    delta_str = f"{item['delta']:+.3f}" if item['delta'] is not None else "N/A"
    gamma_str = f"{item['gamma']:.4f}" if item['gamma'] is not None else "N/A"
    vega_str  = f"{item['vega']:.4f}"  if item['vega'] is not None else "N/A"
    theta_str = f"{item['theta']:.3f}" if item['theta'] is not None else "N/A"
    iv_str    = f"{item['iv']*100:.1f}%" if item['iv'] is not None else "N/A"

    print(f"Expiry: {item['expiry']} | Strike: {item['strike']:7.0f} | {item['right']} | "
          f"conId: {c.conId} | Bid/Ask: {item['bid']:.2f}/{item['ask']:.2f}")
    print(f"   Real Greeks → Δ {delta_str}  Γ {gamma_str}  "
          f"Vega {vega_str}  Θ {theta_str}  IV {iv_str}")
    print("-" * 80)

print(f"\nTotal contracts with real Greeks: {len(tradable_options)}")
print("Each 'contract' object is ready for placeOrder / reqMktData etc.")