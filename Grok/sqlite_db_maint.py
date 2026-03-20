import sqlite3

conn = sqlite3.connect("ibkr_spx_daytrader.db")
cur = conn.cursor()

cur.execute("DROP TABLE IF EXISTS spx_option_greeks")

cur.execute("""
CREATE TABLE spx_option_greeks (
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

conn.commit()
conn.close()
print("✅ Table spx_option_greeks recreated cleanly (13 columns)")