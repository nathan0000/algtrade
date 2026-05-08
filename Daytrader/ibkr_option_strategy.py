from config import HOST, PORT, CLIENT_ID_STRATEGY, DB_FILE, DEFAULT_QUANTITY, TICK_BUFFER
import sqlite3
from ibapi.contract import Contract, ComboLeg          # ComboLeg imported correctly
from ibapi.order import Order
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
import threading
import time
from datetime import datetime

class IBKRStrategy:
    def __init__(self, host="127.0.0.1", port=4002, clientId=5):
        self.host = host
        self.port = port
        self.clientId = clientId
        self.app = None
        self.order_id_counter = int(time.time()) % 100000 + 10000

    def _connect(self):
        class Wrapper(EWrapper, EClient):
            def __init__(self):
                EClient.__init__(self, self)
                self.done = False
        self.app = Wrapper()
        self.app.connect(self.host, self.port, self.clientId)
        threading.Thread(target=self.app.run, daemon=True).start()
        time.sleep(2)

    def load_options(self, expiry=None):
        conn = sqlite3.connect("ibkr_spx_daytrader.db")
        query = "SELECT expiry, strike, right, conId, bid, ask, last, iv, delta, gamma, vega, theta FROM spx_option_greeks"
        if expiry:
            rows = conn.execute(query + " WHERE expiry = ?", (expiry,)).fetchall()
        else:
            rows = conn.execute(query).fetchall()
        conn.close()

        options = []
        for r in rows:
            c = Contract()
            c.symbol = "SPX"
            c.secType = "OPT"
            c.exchange = "CBOE"
            c.currency = "USD"
            c.multiplier = "100"
            c.strike = float(r[1])
            c.right = r[2]
            c.lastTradeDateOrContractMonth = r[0]
            c.conId = int(r[3])
            options.append({
                "contract": c,
                "expiry": r[0],
                "strike": float(r[1]),
                "right": r[2],
                "bid": r[4],
                "ask": r[5],
                "last": r[6],
                "iv": r[7],
                "delta": r[8],
                "gamma": r[9],
                "vega": r[10],
                "theta": r[11]
            })
        return options

    def print_available(self, options):
        print("\n=== AVAILABLE OPTIONS IN DB ===")
        for o in sorted(options, key=lambda x: (x["expiry"], x["strike"])):
            delta_str = f"{o['delta']:+.3f}" if o['delta'] is not None else "N/A"
            iv_str    = f"{o['iv']*100:.1f}%" if o['iv'] is not None else "N/A"
            bid_str   = f"{o['bid']:.2f}" if o['bid'] is not None else "N/A"
            ask_str   = f"{o['ask']:.2f}" if o['ask'] is not None else "N/A"
            print(f"{o['expiry']} | {o['strike']:7.0f} {o['right']} | "
                  f"Δ {delta_str} | IV {iv_str} | Bid/Ask {bid_str}/{ask_str}")
        print("================================\n")

    def snap_to_tick(self, price: float) -> float:
        price = max(price, 0.05)
        if price < 3.0:
            return round(round(price * 20) / 20, 2)
        else:
            return round(round(price * 10) / 10, 2)

    def build_vertical_spread(self, long_leg, short_leg, quantity=1, action="BUY"):
        long_price = long_leg.get("ask") or long_leg.get("last") or 0
        short_price = short_leg.get("bid") or short_leg.get("last") or 0
        raw_debit = long_price - short_price

        snapped = self.snap_to_tick(raw_debit)
        print(f"Calculated raw debit: {raw_debit:.2f} → Snapped to {snapped:.2f}")

        # === CORRECT BAG CONTRACT (ComboLegs attached to contract) ===
        bag = Contract()
        bag.symbol = "SPX"
        bag.secType = "BAG"               # ← Required for combos
        bag.exchange = "CBOE"
        bag.currency = "USD"

        leg1 = ComboLeg()
        leg1.conId = long_leg["contract"].conId
        leg1.ratio = 1
        leg1.action = "BUY"
        leg1.exchange = "CBOE"

        leg2 = ComboLeg()
        leg2.conId = short_leg["contract"].conId
        leg2.ratio = 1
        leg2.action = "SELL"
        leg2.exchange = "CBOE"

        bag.comboLegs = [leg1, leg2]
        bag.comboLegsDescrip = f"Vertical {long_leg['strike']}/{short_leg['strike']}"

        # Order
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = snapped
        order.transmit = True
        order.whatIf = False

        return bag, order                     # return both

    def place_order(self, bag_contract, order, whatIf=True):
        self._connect()
        reqId = self.order_id_counter
        self.order_id_counter += 2

        if whatIf:
            order.whatIf = True
            print("🔍 Previewing order (whatIf mode — safe)...")

        self.app.placeOrder(reqId, bag_contract, order)
        print(f"✅ Order sent (ReqId {reqId}) — check TWS!")
        time.sleep(4)
        self.app.disconnect()

# ====================== SMART EXAMPLE ======================
if __name__ == "__main__":
    strat = IBKRStrategy(clientId=5)

    options = strat.load_options()
    strat.print_available(options)

    if len(options) < 2:
        print("Not enough options in DB yet. Run ibkr_spx_option_chain.py first!")
        exit()

    calls = [o for o in options if o["right"] == "C"]
    calls_sorted = sorted(calls, key=lambda x: abs((x["delta"] or 0) - 0.5))

    long_leg = calls_sorted[0]
    short_candidates = [o for o in calls if o["strike"] > long_leg["strike"]]
    short_leg = min(short_candidates, key=lambda x: x["strike"]) if short_candidates else calls_sorted[1]

    print(f"\nBuilding Bull Call Spread → Long {long_leg['strike']}C / Short {short_leg['strike']}C")

    bag_contract, order = strat.build_vertical_spread(long_leg, short_leg, quantity=1, action="BUY")
    strat.place_order(bag_contract, order, whatIf=False)

    # When preview succeeds → change to False for live:
    # strat.place_order(bag_contract, order, whatIf=False)