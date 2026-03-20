import sqlite3
import threading
import time
from decimal import Decimal
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData

class IBKRSPXHist(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.bars = []
        self.req_id = 1
        self.done = False

    def historicalData(self, reqId: int, bar: BarData):
        self.bars.append(bar)
        if len(self.bars) % 500 == 0:
            print(f"   Received {len(self.bars)} bars so far...")

    def historicalDataEnd(self, reqId: int, startDateStr: str, endDateStr: str):
        print(f"✅ All {len(self.bars)} bars received (reqId {reqId})")
        print(f"   Period: {startDateStr} → {endDateStr}")
        self.done = True

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = "", arg5: str = ""):
        print(f"Error {errorCode}: {errorString}")

# ====================== MAIN ======================
app = IBKRSPXHist()

app.connect("127.0.0.1", 4002, clientId=2)   # different clientId from trade logger

api_thread = threading.Thread(target=app.run, daemon=True)
api_thread.start()

time.sleep(2)

contract = Contract()
contract.symbol = "SPX"
contract.secType = "IND"
contract.exchange = "CBOE"
contract.currency = "USD"

print("Requesting SPX 5-min bars for past 3 months... (works 24/7)")

app.reqHistoricalData(
    reqId=app.req_id,
    contract=contract,
    endDateTime="",
    durationStr="3 M",
    barSizeSetting="5 mins",
    whatToShow="TRADES",
    useRTH=1,
    formatDate=1,
    keepUpToDate=False,
    chartOptions=[]
)

timeout = 120
start = time.time()
while not app.done and (time.time() - start < timeout):
    time.sleep(0.5)

app.disconnect()

# ====================== SAVE TO SQLITE (fixed WAP) ======================
db_file = "ibkr_spx_daytrader.db"

sqlite3.register_adapter(Decimal, float)
conn = sqlite3.connect(db_file)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS spx_5min (
    date TEXT PRIMARY KEY,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    wap REAL,
    barCount INTEGER
)
""")

inserted = 0
for bar in app.bars:
    try:
        # Safe for ANY ibapi version (new = wap, old = average)
        wap_value = getattr(bar, 'wap', getattr(bar, 'average', 0.0))
        bar_count = getattr(bar, 'barCount', -1)

        cur.execute("""
            INSERT OR IGNORE INTO spx_5min 
            (date, open, high, low, close, volume, wap, barCount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            bar.date,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            wap_value,
            bar_count
        ))
        if cur.rowcount > 0:
            inserted += 1
    except Exception as e:
        print(f"DB error for {bar.date}: {e}")

conn.commit()


print(f"✅ SPX 5-min data saved to {db_file}")
print(f"   New bars added this run: {inserted}")
print(f"   Total bars in DB: {cur.execute('SELECT COUNT(*) FROM spx_5min').fetchone()[0]}")

conn.close()