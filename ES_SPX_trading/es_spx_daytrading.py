# ================================================
# ibkr_day_trader_vix_thresholds.py
# Advanced VIX Regime + TERM STRUCTURE THRESHOLDS
# ================================================

import threading
import time
import datetime
from zoneinfo import ZoneInfo
from collections import deque

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import TickerId
from ibapi.ticktype import TickTypeEnum, TickType

class DayTradingApp(EWrapper, EClient):
    def __init__(self, host="127.0.0.1", port=4002, client_id=123):
        EClient.__init__(self, self)
        self.host = host
        self.port = port
        self.client_id = client_id
        
        # === STORAGE & THRESHOLDS ===
        self.positions = {}
        self.account_pnl = {"unrealized": 0.0, "realized": 0.0}
        self.vix_price = None
        self.vx_price = None
        self.es_price = None
        self.es_last_prices = deque(maxlen=20)
        
        self.vix_regime = "NORMAL"
        self.term_level = "NEUTRAL"
        self.basis_spread = 0.0
        self.advanced_regime = "NORMAL_NEUTRAL"
        
        # TUNABLE THRESHOLDS (points) - adjust based on your backtests
        self.THRESH_STRONG_CONTANGO = 2.0
        self.THRESH_MILD_CONTANGO   = 0.5
        self.THRESH_MILD_BACK       = -0.5
        self.THRESH_STRONG_BACK     = -2.0
        
        self.next_order_id = 0
        self.is_flattened_today = False
        self.target_symbols = {"ES", "SPX"}
        
        # Contracts
        self.es_fut = self._create_es_contract()
        self.vx_fut = self._create_vx_contract()
        self.vix_ind = self._create_vix_contract()
        
        self.close_thread = None

    # ==================== CONTRACTS (unchanged) ====================
    def _create_es_contract(self):
        c = Contract()
        c.symbol = "ES"; c.secType = "FUT"; c.exchange = "CME"; c.currency = "USD"
        c.lastTradeDateOrContractMonth = self._get_front_es_expiry()
        return c

    def _create_vx_contract(self):
        c = Contract()
        c.symbol = "VIX"; c.secType = "FUT"; c.exchange = "CFE"; c.currency = "USD"
        c.lastTradeDateOrContractMonth = self._get_front_vx_expiry()
        return c

    def _create_vix_contract(self):
        c = Contract()
        c.symbol = "VIX"; c.secType = "IND"; c.exchange = "CBOE"; c.currency = "USD"
        return c

    def _get_front_es_expiry(self) -> str:
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
        y, m = now.year, now.month
        quarters = [3,6,9,12]
        for q in quarters:
            if m <= q: return f"{y}{q:02d}"
        return f"{y+1}03"

    def _get_front_vx_expiry(self) -> str:
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
        return f"{now.year}{now.month:02d}"

    def _get_today_0dte(self) -> str:
        return datetime.datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")

    def _create_spx_0dte_atm(self, strike: float, right: str):
        c = Contract()
        c.symbol = "SPX"; c.secType = "OPT"; c.exchange = "CBOE"; c.currency = "USD"
        c.lastTradeDateOrContractMonth = self._get_today_0dte()
        c.strike = round(strike / 5) * 5
        c.right = right.upper()
        return c

    # ==================== CALLBACKS ====================
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId

    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, attrib):
        if tickType != TickTypeEnum.LAST: return
        if reqId == 1000: self.vix_price = price
        elif reqId == 1001: self.vx_price = price
        elif reqId == 2000:
            self.es_price = price
            self.es_last_prices.append(price)
        
        if self.vix_price and self.vx_price:
            self._analyze_advanced_regime()
            print(f"VIX:{self.vix_price:.2f} | VX:{self.vx_price:.2f} | "
                  f"Basis:{self.basis_spread:+.2f} | Regime:{self.advanced_regime}")

    def position(self, account: str, contract: Contract, position: float, avgCost: float):
        if contract.symbol in self.target_symbols:
            self.positions[(contract.symbol, contract.secType)] = (position, contract)

    def updatePortfolio(self, contract: Contract, position: float, marketPrice: float,
                        marketValue: float, averageCost: float, unrealizedPNL: float,
                        realizedPNL: float, accountName: str):
        if contract.symbol in self.target_symbols:
            self.account_pnl["unrealized"] = unrealizedPNL
            self.account_pnl["realized"] += realizedPNL

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        print(f"Error {errorCode}: {errorString}")

    # ==================== ADVANCED REGIME WITH THRESHOLDS ====================
    def _analyze_advanced_regime(self):
        # VIX regime
        if self.vix_price < 15:
            self.vix_regime = "LOW"
        elif self.vix_price > 25:
            self.vix_regime = "HIGH"
        else:
            self.vix_regime = "NORMAL"
        
        # Term structure thresholds
        self.basis_spread = self.vx_price - self.vix_price
        if self.basis_spread > self.THRESH_STRONG_CONTANGO:
            self.term_level = "STRONG_CONTANGO"
        elif self.basis_spread > self.THRESH_MILD_CONTANGO:
            self.term_level = "MILD_CONTANGO"
        elif self.basis_spread < self.THRESH_STRONG_BACK:
            self.term_level = "STRONG_BACKWARDATION"
        elif self.basis_spread < self.THRESH_MILD_BACK:
            self.term_level = "MILD_BACKWARDATION"
        else:
            self.term_level = "NEUTRAL"
        
        self.advanced_regime = f"{self.vix_regime}_{self.term_level}"

    def get_regime_size_multiplier(self) -> float:
        if "HIGH" in self.advanced_regime and "STRONG_BACKWARDATION" in self.advanced_regime:
            return 0.75   # slightly smaller in extreme fear (protect against whipsaw)
        elif "LOW" in self.advanced_regime and "STRONG_CONTANGO" in self.advanced_regime:
            return 1.75   # larger in calm expansion
        elif "HIGH" in self.advanced_regime:
            return 0.5
        elif "LOW" in self.advanced_regime:
            return 1.5
        return 1.0

    # ==================== STRATEGY LOGIC (now threshold-aware) ====================
    def strategy_logic(self):
        if not self.es_price or len(self.es_last_prices) < 5: return
        
        multiplier = self.get_regime_size_multiplier()
        momentum = self.es_price > (sum(self.es_last_prices) / len(self.es_last_prices))
        
        print(f"Strategy | Regime:{self.advanced_regime} | ES:{self.es_price:.2f} | Momentum:{momentum}")

        # === STRONG CONTANGO + LOW VIX: aggressive trend-follow ===
        if "LOW" in self.advanced_regime and "STRONG_CONTANGO" in self.advanced_regime:
            self.place_market_order(self.es_fut, "BUY", int(2 * multiplier))

        # === STRONG BACKWARDATION + HIGH VIX: aggressive short-vol ===
        elif "HIGH" in self.advanced_regime and "STRONG_BACKWARDATION" in self.advanced_regime:
            atm = self.es_price
            self.place_spx_0dte_strangle("SELL", atm, qty=int(2 * multiplier))

        # === All other combos (original logic + mild adjustments) ===
        elif "LOW" in self.advanced_regime and "CONTANGO" in self.advanced_regime:
            if momentum:
                self.place_market_order(self.es_fut, "BUY", int(1 * multiplier))
        elif "HIGH" in self.advanced_regime and "BACKWARDATION" in self.advanced_regime:
            if not momentum:
                self.place_market_order(self.es_fut, "BUY", int(1 * multiplier))
            atm = self.es_price
            self.place_spx_0dte_strangle("SELL", atm, qty=int(1 * multiplier))
        elif "NORMAL" in self.advanced_regime:
            if momentum:
                self.place_market_order(self.es_fut, "BUY", 1)
            else:
                self.place_spx_0dte_strangle("SELL", self.es_price, qty=1)

    def place_market_order(self, contract: Contract, action: str, quantity: int):
        if self.next_order_id == 0: return
        order = Order()
        order.action = action
        order.orderType = "MKT"
        order.totalQuantity = quantity
        order.tif = "DAY"
        self.placeOrder(self.next_order_id, contract, order)
        self.next_order_id += 1

    def place_spx_0dte_strangle(self, action: str, atm: float, qty: int = 1):
        if self.next_order_id == 0: return
        call = self._create_spx_0dte_atm(atm + 10, "C")
        put  = self._create_spx_0dte_atm(atm - 10, "P")
        for opt in [call, put]:
            order = Order()
            order.action = action
            order.orderType = "MKT"
            order.totalQuantity = qty
            order.tif = "DAY"
            self.placeOrder(self.next_order_id, opt, order)
            self.next_order_id += 1
        print(f"0DTE SPX STRANGLE {action} ×{qty} | ATM~{atm:.0f}")

    # ==================== FLATTEN & START (unchanged) ====================
    def start_close_watcher(self):
        def watcher():
            while True:
                ny_time = datetime.datetime.now(ZoneInfo("America/New_York")).time()
                minutes_before_close = datetime.time(hour=15, minute=55)
                if ny_time >= minutes_before_close and not self.is_flattened_today:
                    print("=== 15:55 ET – FLATTENING ALL ES/SPX ===")
                    self.flatten_all_positions()
                    self.is_flattened_today = True
                time.sleep(30)
        self.close_thread = threading.Thread(target=watcher, daemon=True)
        self.close_thread.start()

    def flatten_all_positions(self):
        for (sym, stype), (pos, con) in list(self.positions.items()):
            if pos != 0:
                action = "SELL" if pos > 0 else "BUY"
                self.place_market_order(con, action, int(abs(pos)))

    def start(self):
        self.connect(self.host, self.port, self.client_id)
        threading.Thread(target=self.run, daemon=True).start()
        time.sleep(2)

        self.reqPositions()
        self.reqAccountUpdates(True, "")
        self.reqMktData(1000, self.vix_ind, "", False, False, [])   # VIX spot
        self.reqMktData(1001, self.vx_fut, "", False, False, [])    # VX front
        self.reqMktData(2000, self.es_fut, "", False, False, [])    # ES

        self.start_close_watcher()

        while True:
            self.strategy_logic()
            time.sleep(5)

    def stop(self):
        self.disconnect()

# ================================================
# TEST MODULE (updated)
# ================================================

class MockDayTradingApp(DayTradingApp):
    def __init__(self):
        super().__init__()
        self.mock_vix = 28.0
        self.mock_vx = 24.5   # strong backwardation example

    def test_thresholds(self):
        self.vix_price = self.mock_vix
        self.vx_price = self.mock_vx
        self._analyze_advanced_regime()
        print(f"Test: Basis={self.basis_spread:+.2f} → {self.advanced_regime}")
        assert "STRONG_BACKWARDATION" in self.advanced_regime
        print("=== VIX Term Structure Threshold Tests Passed ===")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        MockDayTradingApp().test_thresholds()
    else:
        app = DayTradingApp(port=4002)  # paper
        try:
            app.start()
        except KeyboardInterrupt:
            app.stop()