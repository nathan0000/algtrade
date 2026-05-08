import sqlite3
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.execution import ExecutionFilter
from ibapi.contract import Contract
from ibapi.execution import Execution
from ibapi.commission_and_fees_report import CommissionAndFeesReport
class IBKRTradeLogger(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.exec_details = []          # list of (contract, execution)
        self.commission_reports = {}    # execId -> CommissionReport
        self.req_id = 1
        self.done = False

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):        
        self.exec_details.append((contract, execution))
        print(f"Exec: {execution.time} | {contract.symbol} | {contract.strike} | {contract.right} | {contract.lastTradeDateOrContractMonth} | {execution.side} {execution.shares} @ {execution.price}")

    def commissionReport(self, commissionReport: CommissionAndFeesReport):
        self.commission_reports[commissionReport.execId] = commissionReport
        print(f"Commission: {commissionReport.execId} | {commissionReport.commission} {commissionReport.currency}")

    def execDetailsEnd(self, reqId: int):
        print(f"✅ All executions received (reqId {reqId})")
        self.done = True

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        print(f"Error {errorCode}: {errorString}")

# ====================== MAIN ======================
app = IBKRTradeLogger()

# Connect to TWS (use 7497 for live TWS, 7496 for paper; Gateway was 4001/4002)
app.connect("127.0.0.1", 7497, clientId=1)   # ← CHANGE PORT IF NEEDED

api_thread = threading.Thread(target=app.run, daemon=True)
api_thread.start()

time.sleep(2)

# 7 days ago filter (server still respects the Trade Log setting)
seven_days_ago = datetime.now() - timedelta(days=7)
time_filter = seven_days_ago.strftime("%Y%m%d %H:%M:%S")

exec_filter = ExecutionFilter()
exec_filter.time = time_filter

print(f"Requesting executions since: {time_filter} (make sure TWS Trade Log = 7 days)")

app.reqExecutions(app.req_id, exec_filter)

# Wait for data
timeout = 60
start = time.time()
while not app.done and (time.time() - start < timeout):
    time.sleep(0.5)

app.disconnect()

# ====================== SAVE TO SQLITE (no duplicates) ======================
db_file = "/Users/nathanwang/ibkr_trade_log.db"

sqlite3.register_adapter(Decimal, float)
conn = sqlite3.connect(db_file)
cur = conn.cursor()

# Create table (execId is UNIQUE primary key)
cur.execute("""
CREATE TABLE IF NOT EXISTS trades (
    execId TEXT PRIMARY KEY,
    time TEXT,
    symbol TEXT,
    secType TEXT,
    exchange TEXT,
    currency TEXT,
    optionStrike REAL,
    optionRight TEXT,
    optionExpiry TEXT,
    side TEXT,
    shares REAL,
    price REAL,
    avgPrice REAL,
    cumQty REAL,
    commission REAL,
    comm_currency TEXT,
    realizedPNL REAL,
    orderId INTEGER,
    permId INTEGER,
    clientId INTEGER
)
""")

inserted = 0
for contract, execution in app.exec_details:
    exec_id = execution.execId
    comm = app.commission_reports.get(exec_id)

    try:
        cur.execute("""
            INSERT OR IGNORE INTO trades 
            (execId, time, symbol, secType, exchange, currency, optionStrike, optionRight, optionExpiry, side, shares, price, 
             avgPrice, cumQty, commission, comm_currency, realizedPNL, 
             orderId, permId, clientId)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            exec_id,
            execution.time,
            contract.symbol,
            contract.secType,
            contract.exchange,
            contract.currency,
            contract.strike if hasattr(contract, 'strike') else None,
            contract.right if hasattr(contract, 'right') else None,
            contract.lastTradeDateOrContractMonth if hasattr(contract, 'lastTradeDateOrContractMonth') else None,
            execution.side,
            execution.shares,
            execution.price,
            execution.avgPrice,
            execution.cumQty,
            comm.commission if comm else 0.0,
            comm.currency if comm else "",
            comm.realizedPNL if comm else 0.0,
            execution.orderId,
            execution.permId,
            execution.clientId
        ))
        if cur.rowcount > 0:
            inserted += 1
    except Exception as e:
        print(f"DB error for {exec_id}: {e}")

conn.commit()
conn.close()

print(f"✅ SQLite database updated: {db_file}")
print(f"   New trades added this run: {inserted}")
print(f"   Total executions processed: {len(app.exec_details)}")