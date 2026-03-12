"""
SPX 0DTE Automated Trading Bot — IBKR Native API
=================================================
Implements all 3 strategies from the Rules Engine:
  1. Iron Condor       (range days, VIX 13–20)
  2. Broken Wing Butterfly (trend days, VIX 14–22)
  3. Vertical Scalp    (momentum, VIX any, confirmed breakout)

Requirements:
  pip install ibapi pandas pytz schedule

IBKR TWS/Gateway must be running with API enabled.
Paper trade recommended until live validated.
"""

import sys
import re
import time
import threading
import logging
import math
from datetime import datetime, timedelta
from typing import Optional
import pytz
import pandas as pd

# ─── IBKR Native API imports ────────────────────────────────────────────────
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import TickerId, BarData
from ibapi.ticktype import TickTypeEnum

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("spx_0dte.log"),
    ],
)
log = logging.getLogger("SPX0DTE")

ET = pytz.timezone("America/New_York")


# ════════════════════════════════════════════════════════════════════════════
# ❶  CONFIGURATION  — edit before running
# ════════════════════════════════════════════════════════════════════════════
class Config:
    # ── Connection
    HOST: str       = "127.0.0.1"
    PORT: int       = 4002          # 7497 = TWS paper | 7496 = TWS live | 4002 = Gateway paper
    CLIENT_ID: int  = 1

    # ── Account / sizing
    ACCOUNT: str    = "DU000000"    # Replace with your paper account ID
    MAX_ACCOUNT_RISK_PCT: float  = 0.025    # 2.5% daily max loss
    TRADE_RISK_PCT_IC: float     = 0.010    # 1.0% per Iron Condor
    TRADE_RISK_PCT_BWB: float    = 0.0075   # 0.75% per BWB
    TRADE_RISK_PCT_VS: float     = 0.010    # 1.0% per Vertical Scalp

    # ── VIX regimes
    VIX_MIN_IC: float   = 13.0
    VIX_MAX_IC: float   = 20.0
    VIX_MIN_BWB: float  = 14.0
    VIX_MAX_BWB: float  = 22.0
    VIX_SPIKE_THRESHOLD: float = 2.0       # pts in 30 min → close all

    # ── IVR minimums
    IVR_MIN_IC: float   = 30.0
    IVR_MIN_BWB: float  = 40.0

    # ── Timing (ET)
    SETUP_START      = (10, 0)    # 10:00am
    PRIME_START      = (10, 30)   # 10:30am — earliest entry
    IC_ENTRY_END     = (11, 30)   # no new ICs after
    BWB_ENTRY_END    = (11, 30)   # no new BWBs after
    VS_ENTRY_END     = (12, 30)   # no new verticals after
    HARD_CLOSE_TIME  = (14, 0)    # 2:00pm — close ALL

    # ── Strategy parameters
    IC_WING_WIDTH_LOW:  int = 5    # VIX 13–16
    IC_WING_WIDTH_HIGH: int = 10   # VIX 16–20
    IC_DELTA_TARGET: float  = 0.18 # ~18 delta for short strikes
    IC_MIN_CREDIT: float    = 1.50 # per 5-wide spread

    BWB_BODY_WIDTH: int   = 10     # ATM to short strike
    BWB_SKIP_WIDTH: int   = 15     # ATM to long wing
    BWB_MAX_DEBIT: float  = 0.75   # max acceptable debit

    VS_SPREAD_WIDTH: int  = 5
    VS_MAX_DEBIT_PCT: float = 0.40  # max 40% of spread width
    VS_PROFIT_TARGET_PCT: float = 0.80  # 80% of max value
    VS_STOP_PCT: float  = 0.50     # stop at 50% of debit

    # ── Kill switch conditions
    MAX_CONSECUTIVE_LOSSES: int = 2


# ════════════════════════════════════════════════════════════════════════════
# ❷  IBKR WRAPPER — handles all callbacks from TWS
# ════════════════════════════════════════════════════════════════════════════
class IBKRWrapper(EWrapper):
    def __init__(self):
        super().__init__()
        self.next_order_id: int = 0
        self.account_value: float = 100_000.0
        self.spx_price: float = 0.0
        self.vix_price: float = 0.0
        self.option_chain: dict = {}          # strike → {call_bid, call_ask, put_bid, put_ask}
        self.open_orders: dict = {}           # orderId → order info
        self.positions: dict = {}             # conId → position
        self.portfolio_pnl: float = 0.0

        # Internal events
        self._order_id_event = threading.Event()
        self._chain_event = threading.Event()
        self._account_event = threading.Event()

        # Req ID tracking
        self._req_map: dict = {}
        self._bar_data: dict = {}

        # VWAP bar pipeline — filled by historicalData + realtimeBars callbacks
        self.vwap_bars: list = []            # list of (high, low, close, volume) tuples
        self._hist_bars_done = threading.Event()  # signals historicalDataEnd received
        self._rt_bucket: dict = {            # accumulator for 5-sec → 5-min bucketing
            "highs": [], "lows": [], "closes": [], "volumes": [], "count": 0
        }

    # ── Connection
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self._order_id_event.set()
        log.info(f"Connected. Next order ID: {orderId}")

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        if errorCode in (2104, 2106, 2158, 2119):  # informational
            return
        log.error(f"IBKR Error [{reqId}] {errorCode}: {errorString}")

    # ── Account
    def updateAccountValue(self, key: str, val: str, currency: str, accountName: str):
        if key == "NetLiquidation" and currency == "USD":
            self.account_value = float(val)
            log.debug(f"Account NLV: ${self.account_value:,.2f}")

    def accountDownloadEnd(self, accountName: str):
        self._account_event.set()

    # ── Portfolio / Positions
    def updatePortfolio(self, contract, position, marketPrice, marketValue,
                        averageCost, unrealizedPNL, realizedPNL, accountName):
        self.portfolio_pnl = unrealizedPNL + realizedPNL
        key = contract.conId
        self.positions[key] = {
            "contract": contract,
            "position": position,
            "market_price": marketPrice,
            "unrealized_pnl": unrealizedPNL,
            "realized_pnl": realizedPNL,
        }

    # ── Market data ticks
    def tickPrice(self, reqId: TickerId, tickType, price: float, attrib):
        tag = self._req_map.get(reqId, "")
        if "SPX" in tag and tickType == 4:   # last price
            self.spx_price = price
        elif "VIX" in tag and tickType == 4:
            self.vix_price = price

    # ── Options chain via contract details
    def contractDetails(self, reqId: int, contractDetails):
        tag = self._req_map.get(reqId, "")
        if tag == "CHAIN":
            c = contractDetails.contract
            strike = c.strike
            right = c.right
            if strike not in self.option_chain:
                self.option_chain[strike] = {}
            self.option_chain[strike]["expiry"] = c.lastTradeDateOrContractMonth
            self.option_chain[strike]["right"] = right
            self.option_chain[strike]["conId"] = c.conId

    def contractDetailsEnd(self, reqId: int):
        if self._req_map.get(reqId) == "CHAIN":
            self._chain_event.set()

    # ── Option greeks (for delta filtering)
    def tickOptionComputation(self, reqId: TickerId, tickType, tickAttrib,
                               impliedVol, delta, optPrice, pvDividend,
                               gamma, vega, theta, undPrice):
        tag = self._req_map.get(reqId, "")
        if "GREEK" in tag:
            parts = tag.split("|")
            strike = float(parts[1])
            right = parts[2]
            if tickType == 13:   # model delta
                key = "call_delta" if right == "C" else "put_delta"
                if strike in self.option_chain:
                    self.option_chain[strike][key] = delta

    # ── Order status
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        log.info(f"Order {orderId} status: {status} filled={filled} avgPx={avgFillPrice:.2f}")
        if orderId in self.open_orders:
            self.open_orders[orderId]["status"] = status
            self.open_orders[orderId]["avg_fill"] = avgFillPrice

    def openOrder(self, orderId, contract, order, orderState):
        self.open_orders[orderId] = {
            "contract": contract,
            "order": order,
            "status": orderState.status,
            "avg_fill": 0.0,
        }

    def execDetails(self, reqId, contract, execution):
        log.info(f"Execution: {contract.symbol} {execution.side} {execution.shares} @ {execution.price}")

    # ── Historical bars — feeds the initial VWAP backfill ────────────────
    def historicalData(self, reqId: int, bar: BarData):
        """
        Called once per bar for the initial historical data request.

        SPX is an index (secType=IND) so IBKR delivers MIDPOINT bars.
        MIDPOINT bars have no volume field (bar.volume == -1).
        For VWAP on an index we substitute the WAP (weighted average price)
        field as a proxy volume weight when available; otherwise fall back
        to equal-weight (volume=1) so the VWAP formula stays meaningful.

        Date formats IBKR may return for 5-min bars:
          • "20240315 09:30:00 US/Eastern"   ← most common (single space)
          • "20240315  09:30:00 US/Eastern"  ← occasional double space
          • "1710494400"                      ← epoch integer string
        """
        if self._req_map.get(reqId) != "BARS_SPX":
            return
        try:
            raw_date = bar.date.strip()

            # ── Parse date ───────────────────────────────────────────────
            if raw_date.isdigit():
                # Epoch seconds
                bar_dt = datetime.fromtimestamp(int(raw_date), tz=ET)
            else:
                # Strip trailing timezone label (" US/Eastern", " America/New_York", etc.)
                date_part = re.split(r"\s+US/|\s+America/", raw_date)[0].strip()
                # Normalise multiple spaces between date and time to one
                date_part = re.sub(r"\s+", " ", date_part)
                for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        bar_dt = ET.localize(datetime.strptime(date_part, fmt))
                        break
                    except ValueError:
                        continue
                else:
                    log.warning(f"historicalData: unrecognised date format '{bar.date}' — skipping bar")
                    return

            # ── Filter: today's RTH session only (≥ 09:30 ET) ───────────
            today        = datetime.now(ET).date()
            session_open = ET.localize(datetime(today.year, today.month, today.day, 9, 30))

            if bar_dt.date() != today or bar_dt < session_open:
                return   # pre-market or prior-day bar — discard

            # ── Extract OHLC ─────────────────────────────────────────────
            high  = float(bar.high)
            low   = float(bar.low)
            close = float(bar.close)

            # Volume: -1 for index MIDPOINT bars — use WAP as weight proxy.
            # bar.barCount gives the number of underlying ticks; use it as a
            # volume surrogate (more ticks = more activity = more weight).
            if bar.volume not in (-1, 0):
                volume = float(bar.volume)
            elif hasattr(bar, "barCount") and bar.barCount > 0:
                volume = float(bar.barCount)
            else:
                volume = 1.0   # equal-weight fallback

            self.vwap_bars.append((high, low, close, volume))
            log.debug(f"  HistBar {bar_dt.strftime('%H:%M')} "
                      f"H={high:.2f} L={low:.2f} C={close:.2f} V={volume:.0f}")

        except Exception as e:
            log.warning(f"historicalData parse error reqId={reqId}: {e} | raw='{bar.date}'")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Signals that all historical bars have been delivered."""
        if self._req_map.get(reqId) != "BARS_SPX":
            return
        log.info(f"Historical bars loaded: {len(self.vwap_bars)} today-session bars | "
                 f"range {start} → {end}")
        self._hist_bars_done.set()

    # ── Real-time 5-sec bars — keeps VWAP live after backfill ────────────
    def realtimeBar(self, reqId: int, time_: int, open_: float, high: float,
                    low: float, close: float, volume: int, wap: float, count: int):
        """
        Called every 5 seconds by reqRealTimeBars (MIDPOINT for SPX index).

        MIDPOINT real-time bars for an index always deliver volume=-1 and wap=0.
        We use `count` (number of underlying data points in the 5-sec bar) as a
        volume-weight proxy — exactly as IBKR recommends for index VWAP.
        Buckets 60 × 5-sec ticks (~5 min) into a single OHLCV bar appended to
        vwap_bars, keeping VWAP current throughout the session.
        """
        if self._req_map.get(reqId) != "RT_BARS_SPX":
            return

        # Weight: prefer count (tick density), fall back to 1 (equal weight)
        weight = float(count) if count > 0 else 1.0

        b = self._rt_bucket
        b["highs"].append(high)
        b["lows"].append(low)
        b["closes"].append(close)
        b["volumes"].append(weight)
        b["count"] += 1

        # Every 60 ticks (~5 minutes) flush a completed bar into vwap_bars
        if b["count"] >= 60:
            bar_high   = max(b["highs"])
            bar_low    = min(b["lows"])
            bar_close  = b["closes"][-1]
            bar_volume = sum(b["volumes"])
            self.vwap_bars.append((bar_high, bar_low, bar_close, bar_volume))
            log.info(f"RT bar flushed → H={bar_high:.2f} L={bar_low:.2f} "
                     f"C={bar_close:.2f} ticks={bar_volume:.0f} | "
                     f"VWAP bars total: {len(self.vwap_bars)}")
            self._rt_bucket = {"highs": [], "lows": [], "closes": [], "volumes": [], "count": 0}


# ════════════════════════════════════════════════════════════════════════════
# ❸  IBKR CLIENT  — sends requests to TWS
# ════════════════════════════════════════════════════════════════════════════
class IBKRClient(EClient):
    def __init__(self, wrapper):
        super().__init__(wrapper)


# ════════════════════════════════════════════════════════════════════════════
# ❹  MARKET DATA & CHAIN HELPERS
# ════════════════════════════════════════════════════════════════════════════
def make_spx_contract() -> Contract:
    c = Contract()
    c.symbol = "SPX"
    c.secType = "IND"
    c.exchange = "CBOE"
    c.currency = "USD"
    return c


def make_vix_contract() -> Contract:
    c = Contract()
    c.symbol = "VIX"
    c.secType = "IND"
    c.exchange = "CBOE"
    c.currency = "USD"
    return c


def make_spx_option(expiry: str, strike: float, right: str) -> Contract:
    """right = 'C' or 'P'"""
    c = Contract()
    c.symbol = "SPX"
    c.secType = "OPT"
    c.exchange = "SMART"
    c.currency = "USD"
    c.lastTradeDateOrContractMonth = expiry
    c.strike = strike
    c.right = right
    c.multiplier = "100"
    return c


def today_expiry() -> str:
    """Returns today's date as YYYYMMDD for 0DTE contracts."""
    return datetime.now(ET).strftime("%Y%m%d")


def round_strike(price: float, width: int = 5) -> float:
    """Round to nearest 5-point SPX strike."""
    return round(price / width) * width


# ════════════════════════════════════════════════════════════════════════════
# ❺  RULES ENGINE  — all pre-trade filters
# ════════════════════════════════════════════════════════════════════════════
class RulesEngine:
    def __init__(self, cfg: Config, wrapper: IBKRWrapper):
        self.cfg = cfg
        self.w = wrapper
        self.vix_history: list = []           # (timestamp, vix) for spike detection
        self.consecutive_losses: int = 0
        self.daily_pnl_start: float = 0.0
        self.daily_pnl: float = 0.0
        self.trades_today: int = 0

    def record_vix(self):
        now = datetime.now(ET)
        self.vix_history.append((now, self.w.vix_price))
        # Keep 30 min window
        cutoff = now - timedelta(minutes=30)
        self.vix_history = [(t, v) for t, v in self.vix_history if t >= cutoff]

    def vix_spiking(self) -> bool:
        if len(self.vix_history) < 2:
            return False
        oldest_vix = self.vix_history[0][1]
        return (self.w.vix_price - oldest_vix) >= self.cfg.VIX_SPIKE_THRESHOLD

    def in_trading_window(self, start_hm: tuple, end_hm: tuple) -> bool:
        now = datetime.now(ET).time()
        from datetime import time as dtime
        s = dtime(*start_hm)
        e = dtime(*end_hm)
        return s <= now <= e

    def past_hard_close(self) -> bool:
        from datetime import time as dtime
        now = datetime.now(ET).time()
        return now >= dtime(*self.cfg.HARD_CLOSE_TIME)

    def daily_loss_exceeded(self) -> bool:
        loss_pct = abs(min(0, self.daily_pnl)) / self.w.account_value
        return loss_pct >= self.cfg.MAX_ACCOUNT_RISK_PCT

    def kill_switch_active(self) -> bool:
        if self.vix_spiking():
            log.warning("KILL SWITCH: VIX spike detected")
            return True
        if self.daily_loss_exceeded():
            log.warning("KILL SWITCH: Daily max loss exceeded")
            return True
        if self.consecutive_losses >= self.cfg.MAX_CONSECUTIVE_LOSSES:
            log.warning(f"KILL SWITCH: {self.consecutive_losses} consecutive losses")
            return True
        return False

    def can_trade_ic(self) -> tuple[bool, str]:
        v = self.w.vix_price
        if not (self.cfg.VIX_MIN_IC <= v <= self.cfg.VIX_MAX_IC):
            return False, f"VIX {v:.1f} outside IC range {self.cfg.VIX_MIN_IC}–{self.cfg.VIX_MAX_IC}"
        if self.vix_spiking():
            return False, "VIX spiking — no IC"
        if not self.in_trading_window(self.cfg.PRIME_START, self.cfg.IC_ENTRY_END):
            return False, "Outside IC entry window (10:30–11:30am)"
        return True, "OK"

    def can_trade_bwb(self) -> tuple[bool, str]:
        v = self.w.vix_price
        if not (self.cfg.VIX_MIN_BWB <= v <= self.cfg.VIX_MAX_BWB):
            return False, f"VIX {v:.1f} outside BWB range"
        if not self.in_trading_window(self.cfg.SETUP_START, self.cfg.BWB_ENTRY_END):
            return False, "Outside BWB entry window (10:00–11:30am)"
        return True, "OK"

    def can_trade_vs(self) -> tuple[bool, str]:
        if self.vix_spiking():
            return False, "VIX spiking — no Vertical Scalp"
        if not self.in_trading_window(self.cfg.PRIME_START, self.cfg.VS_ENTRY_END):
            return False, "Outside VS entry window (10:30–12:30pm)"
        return True, "OK"

    def score_setup(self, strategy: str) -> int:
        """Return 0–100 score. Trade only if >= 55."""
        score = 0
        v = self.w.vix_price

        # VIX in regime
        if strategy == "IC" and self.cfg.VIX_MIN_IC <= v <= self.cfg.VIX_MAX_IC:
            score += 20
        elif strategy in ("BWB", "VS") and v < 25:
            score += 20

        # VIX not spiking
        if not self.vix_spiking():
            score += 15

        # Time window
        if strategy == "IC" and self.in_trading_window(self.cfg.PRIME_START, self.cfg.IC_ENTRY_END):
            score += 15
        elif strategy == "BWB" and self.in_trading_window(self.cfg.SETUP_START, self.cfg.BWB_ENTRY_END):
            score += 15
        elif strategy == "VS" and self.in_trading_window(self.cfg.PRIME_START, self.cfg.VS_ENTRY_END):
            score += 15

        # Daily loss headroom
        if not self.daily_loss_exceeded():
            score += 15

        # Consecutive losses OK
        if self.consecutive_losses == 0:
            score += 20
        elif self.consecutive_losses == 1:
            score += 10

        # Price sanity (SPX > 0)
        if self.w.spx_price > 0:
            score += 15

        return min(100, score)


# ════════════════════════════════════════════════════════════════════════════
# ❻  ORDER BUILDERS
# ════════════════════════════════════════════════════════════════════════════
def limit_order(action: str, qty: int, limit_price: float) -> Order:
    o = Order()
    o.action = action
    o.totalQuantity = qty
    o.orderType = "LMT"
    o.lmtPrice = round(limit_price, 2)
    o.tif = "DAY"
    o.transmit = True
    return o


def combo_order(action: str, qty: int, limit_price: float) -> Order:
    """For multi-leg combos (IC, BWB, vertical spreads)."""
    o = Order()
    o.action = action
    o.totalQuantity = qty
    o.orderType = "LMT"
    o.lmtPrice = round(limit_price, 2)
    o.tif = "DAY"
    o.transmit = True
    o.smartComboRoutingParams = []
    return o


def make_combo_contract(legs: list[dict]) -> Contract:
    """
    legs = [{"conId": int, "ratio": int, "action": "BUY"|"SELL", "exchange": "SMART"}]
    """
    from ibapi.contract import ComboLeg
    c = Contract()
    c.symbol = "SPX"
    c.secType = "BAG"
    c.currency = "USD"
    c.exchange = "SMART"
    c.comboLegs = []
    for leg in legs:
        cl = ComboLeg()
        cl.conId = leg["conId"]
        cl.ratio = leg["ratio"]
        cl.action = leg["action"]
        cl.exchange = leg.get("exchange", "SMART")
        c.comboLegs.append(cl)
    return c


# ════════════════════════════════════════════════════════════════════════════
# ❼  STRATEGY BUILDERS
# ════════════════════════════════════════════════════════════════════════════
class StrategyBuilder:
    def __init__(self, cfg: Config, wrapper: IBKRWrapper, client: IBKRClient):
        self.cfg = cfg
        self.w = wrapper
        self.c = client

    def _get_qty(self, risk_pct: float, max_loss_per_contract: float) -> int:
        """Calculate number of contracts based on risk %."""
        risk_dollar = self.w.account_value * risk_pct
        qty = max(1, int(risk_dollar / (max_loss_per_contract * 100)))
        return qty

    def _next_id(self) -> int:
        oid = self.w.next_order_id
        self.w.next_order_id += 1
        return oid

    # ── Strategy 1: Iron Condor ──────────────────────────────────────────
    def build_iron_condor(self, session_high: float, session_low: float) -> Optional[dict]:
        cfg = self.cfg
        spx = self.w.spx_price
        vix = self.w.vix_price
        expiry = today_expiry()

        wing = cfg.IC_WING_WIDTH_HIGH if vix > 16 else cfg.IC_WING_WIDTH_LOW

        # Short strikes above session high / below session low
        short_call = round_strike(session_high + 10, wing)
        long_call  = short_call + wing
        short_put  = round_strike(session_low - 10, wing)
        long_put   = short_put - wing

        log.info(f"IC structure: {long_put}/{short_put}/{short_call}/{long_call} expiry={expiry}")

        # Fetch conIds for all 4 legs
        legs_def = [
            (long_put,   "P", "BUY"),
            (short_put,  "P", "SELL"),
            (short_call, "C", "SELL"),
            (long_call,  "C", "BUY"),
        ]

        legs = []
        for strike, right, action in legs_def:
            con = make_spx_option(expiry, strike, right)
            # In live code, resolve conId via reqContractDetails
            # For now return structure plan
            legs.append({"strike": strike, "right": right, "action": action, "contract": con})

        max_loss = (wing - cfg.IC_MIN_CREDIT) * 100
        qty = self._get_qty(cfg.TRADE_RISK_PCT_IC, max_loss / 100)

        return {
            "strategy": "IC",
            "qty": qty,
            "legs": legs,
            "credit_target": cfg.IC_MIN_CREDIT,
            "profit_target_pct": 0.50,
            "stop_multiplier": 2.0,
            "expiry": expiry,
            "wing_width": wing,
        }

    # ── Strategy 2: Broken Wing Butterfly ───────────────────────────────
    def build_bwb(self, direction: str) -> Optional[dict]:
        """direction = 'BULL' or 'BEAR'"""
        cfg = self.cfg
        spx = self.w.spx_price
        expiry = today_expiry()
        atm = round_strike(spx)

        if direction == "BULL":
            # Call BWB: buy ATM call, sell 2× OTM call, buy far OTM call (skip strike)
            long1_strike  = atm
            short_strike  = atm + cfg.BWB_BODY_WIDTH
            long2_strike  = atm + cfg.BWB_SKIP_WIDTH
            right = "C"
            legs_def = [
                (long1_strike,  right, "BUY",  1),
                (short_strike,  right, "SELL", 2),
                (long2_strike,  right, "BUY",  1),
            ]
        else:
            # Put BWB: buy ATM put, sell 2× OTM put, buy far OTM put
            long1_strike  = atm
            short_strike  = atm - cfg.BWB_BODY_WIDTH
            long2_strike  = atm - cfg.BWB_SKIP_WIDTH
            right = "P"
            legs_def = [
                (long1_strike,  right, "BUY",  1),
                (short_strike,  right, "SELL", 2),
                (long2_strike,  right, "BUY",  1),
            ]

        log.info(f"BWB {direction}: {long1_strike}/{short_strike}/{long2_strike} expiry={expiry}")

        legs = []
        for strike, r, action, ratio in legs_def:
            con = make_spx_option(expiry, strike, r)
            legs.append({"strike": strike, "right": r, "action": action,
                         "ratio": ratio, "contract": con})

        max_loss = cfg.BWB_SKIP_WIDTH * 100   # skip-strike risk
        qty = self._get_qty(cfg.TRADE_RISK_PCT_BWB, max_loss / 100)

        return {
            "strategy": "BWB",
            "direction": direction,
            "qty": qty,
            "legs": legs,
            "max_debit": cfg.BWB_MAX_DEBIT,
            "profit_target_pct": 0.65,
            "expiry": expiry,
            "body_width": cfg.BWB_BODY_WIDTH,
        }

    # ── Strategy 3: Vertical Scalp ───────────────────────────────────────
    def build_vertical(self, direction: str) -> Optional[dict]:
        """direction = 'BULL' or 'BEAR'"""
        cfg = self.cfg
        spx = self.w.spx_price
        expiry = today_expiry()
        atm = round_strike(spx)

        if direction == "BULL":
            buy_strike  = atm
            sell_strike = atm + cfg.VS_SPREAD_WIDTH
            right = "C"
            action_buy, action_sell = "BUY", "SELL"
        else:
            buy_strike  = atm
            sell_strike = atm - cfg.VS_SPREAD_WIDTH
            right = "P"
            action_buy, action_sell = "BUY", "SELL"

        max_debit = cfg.VS_SPREAD_WIDTH * cfg.VS_MAX_DEBIT_PCT
        log.info(f"VS {direction}: {buy_strike}/{sell_strike} {right} max_debit={max_debit:.2f}")

        legs = [
            {"strike": buy_strike,  "right": right, "action": action_buy,  "contract": make_spx_option(expiry, buy_strike, right)},
            {"strike": sell_strike, "right": right, "action": action_sell, "contract": make_spx_option(expiry, sell_strike, right)},
        ]

        max_loss_per = max_debit * 100
        qty = self._get_qty(cfg.TRADE_RISK_PCT_VS, max_debit)

        return {
            "strategy": "VS",
            "direction": direction,
            "qty": qty,
            "legs": legs,
            "max_debit": max_debit,
            "profit_target": cfg.VS_SPREAD_WIDTH * cfg.VS_PROFIT_TARGET_PCT,
            "stop_loss": max_debit * cfg.VS_STOP_PCT,
            "expiry": expiry,
        }


# ════════════════════════════════════════════════════════════════════════════
# ❽  POSITION MONITOR  — tracks open trades, applies exits
# ════════════════════════════════════════════════════════════════════════════
class PositionMonitor:
    def __init__(self, wrapper: IBKRWrapper, client: IBKRClient, rules: RulesEngine):
        self.w = wrapper
        self.c = client
        self.rules = rules
        self.open_trades: list = []   # list of trade dicts from StrategyBuilder

    def register_trade(self, trade: dict, entry_credit_or_debit: float, order_ids: list):
        trade["entry_price"] = entry_credit_or_debit
        trade["order_ids"] = order_ids
        trade["open_time"] = datetime.now(ET)
        trade["closed"] = False
        self.open_trades.append(trade)
        log.info(f"Registered trade: {trade['strategy']} entry={entry_credit_or_debit:.2f}")

    def check_exits(self):
        """Called on each tick loop iteration."""
        if self.rules.past_hard_close():
            self._close_all("HARD CLOSE — 2:00pm")
            return

        if self.rules.kill_switch_active():
            self._close_all("KILL SWITCH")
            return

        for trade in self.open_trades:
            if trade["closed"]:
                continue
            self._check_trade_exit(trade)

    def _check_trade_exit(self, trade: dict):
        strat = trade["strategy"]
        entry = trade["entry_price"]
        # Simplified P&L check via IBKR portfolio
        # In production, mark each leg to market via reqMktData
        current_pnl = self._estimate_pnl(trade)

        if strat == "IC":
            profit_target = entry * trade.get("profit_target_pct", 0.5)
            stop = entry * trade.get("stop_multiplier", 2.0)
            if current_pnl >= profit_target:
                self._close_trade(trade, f"IC profit target hit ({current_pnl:.2f})")
            elif current_pnl <= -stop:
                self._close_trade(trade, f"IC stop loss hit ({current_pnl:.2f})")

        elif strat == "BWB":
            target = entry * trade.get("profit_target_pct", 0.65)
            if current_pnl >= target:
                self._close_trade(trade, f"BWB profit target hit")

        elif strat == "VS":
            if current_pnl >= trade.get("profit_target", 3.5):
                self._close_trade(trade, f"VS profit target hit")
            elif current_pnl <= -trade.get("stop_loss", 0.875):
                self._close_trade(trade, f"VS stop loss hit")

    def _estimate_pnl(self, trade: dict) -> float:
        """
        In production, sum unrealized PNL across all legs from self.w.positions.
        Here we return 0.0 as placeholder until conIds are resolved.
        """
        total = 0.0
        for conId, pos in self.w.positions.items():
            total += pos.get("unrealized_pnl", 0.0)
        return total

    def _close_trade(self, trade: dict, reason: str):
        log.info(f"CLOSING {trade['strategy']}: {reason}")
        # Place market or limit orders to close each leg
        for leg in trade["legs"]:
            close_action = "SELL" if leg["action"] == "BUY" else "BUY"
            o = Order()
            o.action = close_action
            o.totalQuantity = trade["qty"] * leg.get("ratio", 1)
            o.orderType = "MKT"
            o.tif = "DAY"
            o.transmit = True
            oid = self.w.next_order_id
            self.w.next_order_id += 1
            self.c.placeOrder(oid, leg["contract"], o)
            log.info(f"  Close leg: {close_action} {leg['strike']}{leg['right']}")
        trade["closed"] = True
        trade["close_reason"] = reason
        trade["close_time"] = datetime.now(ET)

    def _close_all(self, reason: str):
        for trade in self.open_trades:
            if not trade["closed"]:
                self._close_trade(trade, reason)


# ════════════════════════════════════════════════════════════════════════════
# ❾  VWAP CALCULATOR  — simple rolling VWAP from bar data
# ════════════════════════════════════════════════════════════════════════════
# ❾  VWAP CALCULATOR
# Reads directly from wrapper.vwap_bars — the single source of truth that is
# populated by both historicalData (backfill) AND realtimeBar (live feed).
# This guarantees VWAP is never stale or empty once bars have arrived.
# ════════════════════════════════════════════════════════════════════════════
class VWAPCalculator:
    def __init__(self, wrapper: "IBKRWrapper"):
        self._w = wrapper   # reference — always reads live vwap_bars list

    @property
    def bars(self) -> list:
        """Live view of bars — no copy needed, list is mutated in-place by callbacks."""
        return self._w.vwap_bars

    def vwap(self) -> float:
        bars = self.bars
        if not bars:
            return 0.0
        total_vol = sum(v for _, _, _, v in bars)
        if total_vol == 0:
            # Volume sometimes missing for index — fall back to simple mean of typical price
            tp_sum = sum((h + l + c) / 3 for h, l, c, _ in bars)
            return tp_sum / len(bars)
        numerator = sum(((h + l + c) / 3) * v for h, l, c, v in bars)
        return numerator / total_vol

    def session_high(self) -> float:
        return max((h for h, _, _, _ in self.bars), default=0.0)

    def session_low(self) -> float:
        return min((l for _, l, _, _ in self.bars), default=0.0)

    def is_ready(self) -> bool:
        """Need at least 3 bars (15 min of data) before trusting signals."""
        return len(self.bars) >= 3

    def is_bullish_trend(self) -> bool:
        """3 consecutive higher closes, last close above VWAP."""
        if not self.is_ready():
            return False
        closes = [c for _, _, c, _ in self.bars[-3:]]
        vw = self.vwap()
        return closes[2] > closes[1] > closes[0] and closes[2] > vw

    def is_bearish_trend(self) -> bool:
        if not self.is_ready():
            return False
        closes = [c for _, _, c, _ in self.bars[-3:]]
        vw = self.vwap()
        return closes[2] < closes[1] < closes[0] and closes[2] < vw

    def is_range_bound(self) -> bool:
        """Last close within VWAP ± 0.3% AND no directional trend."""
        if not self.is_ready():
            return False
        vw = self.vwap()
        if vw == 0:
            return False
        last_close = self.bars[-1][2]
        pct_from_vwap = abs(last_close - vw) / vw
        return pct_from_vwap <= 0.003 and not self.is_bullish_trend() and not self.is_bearish_trend()

    def debug_str(self) -> str:
        vw = self.vwap()
        bars = self.bars
        last_c = bars[-1][2] if bars else 0
        return (f"VWAP={vw:.2f} | LastClose={last_c:.2f} | Bars={len(bars)} | "
                f"Bull={self.is_bullish_trend()} Bear={self.is_bearish_trend()} "
                f"Range={self.is_range_bound()}")


# ════════════════════════════════════════════════════════════════════════════
# ❿  MAIN BOT
# ════════════════════════════════════════════════════════════════════════════
class SPX0DTEBot:
    def __init__(self):
        self.cfg = Config()
        self.wrapper = IBKRWrapper()
        self.client = IBKRClient(self.wrapper)
        self.rules = RulesEngine(self.cfg, self.wrapper)
        self.builder = StrategyBuilder(self.cfg, self.wrapper, self.client)
        self.monitor = PositionMonitor(self.wrapper, self.client, self.rules)
        self.vwap = VWAPCalculator(self.wrapper)   # reads wrapper.vwap_bars directly
        self._running = False
        self._strategy_traded = {"IC": False, "BWB": False, "VS": False}

    def connect(self):
        self.client.connect(self.cfg.HOST, self.cfg.PORT, self.cfg.CLIENT_ID)
        t = threading.Thread(target=self.client.run, daemon=True)
        t.start()
        log.info("Waiting for next valid order ID...")
        self.wrapper._order_id_event.wait(timeout=10)
        if not self.wrapper.next_order_id:
            raise ConnectionError("Failed to connect to IBKR TWS/Gateway")
        log.info(f"✅ Connected to IBKR | Account: {self.cfg.ACCOUNT}")

    def subscribe_market_data(self):
        """Subscribe to SPX and VIX live quotes."""
        spx_req = 1001
        vix_req = 1002
        self.wrapper._req_map[spx_req] = "SPX_PRICE"
        self.wrapper._req_map[vix_req] = "VIX_PRICE"
        self.client.reqMktData(spx_req, make_spx_contract(), "", False, False, [])
        self.client.reqMktData(vix_req, make_vix_contract(), "", False, False, [])
        log.info("Subscribed to SPX and VIX market data")

    def subscribe_account(self):
        self.client.reqAccountUpdates(True, self.cfg.ACCOUNT)
        self.wrapper._account_event.wait(timeout=10)
        log.info(f"Account NLV: ${self.wrapper.account_value:,.0f}")

    def request_historical_bars(self):
        """
        Two-phase bar pipeline:
          Phase 1 — reqHistoricalData (MIDPOINT, useRTH=0):
                    Backfills all available 5-min bars for today, including
                    pre-market, then the callback filters to RTH-only (≥9:30am).
                    useRTH=0 is essential: with useRTH=1, if the bot starts
                    before 9:30am TWS returns zero bars and fires historicalDataEnd
                    immediately — exactly the "0 bars loaded" bug.

          Phase 2 — reqRealTimeBars (MIDPOINT):
                    Streams live 5-sec bars. The realtimeBar callback buckets
                    60 ticks (~5 min) into one OHLCV bar appended to vwap_bars.

        Why MIDPOINT, not TRADES?
          SPX is secType="IND" — an index has no executed trades.
          Requesting "TRADES" on an index causes IBKR to return error 321
          ("Invalid whatToShow") and deliver zero bars. MIDPOINT is the
          correct data type for index price history.
        """
        # ── Phase 1: Historical backfill ────────────────────────────────
        hist_req_id = 2001
        self.wrapper._req_map[hist_req_id] = "BARS_SPX"
        self.wrapper._hist_bars_done.clear()

        log.info("Requesting historical 5-min MIDPOINT bars for VWAP backfill...")
        self.client.reqHistoricalData(
            hist_req_id,
            make_spx_contract(),
            "",           # endDateTime: empty = now
            "1 D",        # durationStr: pull full day so pre-open bars exist
            "5 mins",     # barSizeSetting
            "TRADES",   # ← MIDPOINT (not TRADES) — SPX is an index, no trades
            1,            # useRTH=1: only include regular trading hours
            1,            # formatDate=1: string "YYYYMMDD HH:MM:SS TZ"
            False,        # keepUpToDate: False — live feed handled by reqRealTimeBars
            [],
        )

        # Block until historicalDataEnd fires (callback sets the event)
        done = self.wrapper._hist_bars_done.wait(timeout=20)
        n = len(self.wrapper.vwap_bars)
        if done and n > 0:
            log.info(f"✅ VWAP backfill complete — {n} RTH bars loaded")
        elif done and n == 0:
            log.warning(
                "⚠️  historicalDataEnd received but 0 RTH bars — "
                "market may not have opened yet. VWAP will seed from real-time bars."
            )
        else:
            log.warning(
                "⚠️  historicalDataEnd timed out after 20s. "
                "Possible causes: TWS not subscribed to SPX market data, "
                "or no IBKR data permission for SPX index. "
                "VWAP will seed from real-time bars once they arrive."
            )

        # ── Phase 2: Real-time 5-sec bar stream ─────────────────────────
        rt_req_id = 2002
        self.wrapper._req_map[rt_req_id] = "RT_BARS_SPX"
        log.info("Starting real-time 5-sec MIDPOINT bar stream...")
        self.client.reqRealTimeBars(
            rt_req_id,
            make_spx_contract(),
            5,            # barSize: 5 seconds (only value IBKR supports)
            "MIDPOINT",   # ← MIDPOINT — required for SPX index
            0,            # useRTH=0: stream starts immediately even pre-open
            [],
        )
        log.info("✅ Real-time bar stream active — VWAP will update every ~5 min")

    def evaluate_and_trade(self):
        """Core decision loop — runs every 60 seconds during prime window."""
        if self.rules.kill_switch_active():
            return

        if self.rules.past_hard_close():
            log.info("Past 2pm hard close — no new trades")
            return

        # Guard: don't trade before we have enough bars for reliable signals
        if not self.vwap.is_ready():
            log.warning(f"VWAP not ready yet — only {len(self.wrapper.vwap_bars)} bars loaded, "
                        f"need ≥3. Waiting for more data...")
            return

        vix = self.wrapper.vix_price
        spx = self.wrapper.spx_price
        self.rules.record_vix()

        log.info(f"[TICK] SPX={spx:.2f} | VIX={vix:.2f} | {self.vwap.debug_str()} | "
                 f"ConsecLoss={self.rules.consecutive_losses}")

        # ── Iron Condor ─────────────────────────────────────────────────
        if not self._strategy_traded["IC"]:
            ok, reason = self.rules.can_trade_ic()
            if ok and self.vwap.is_range_bound():
                score = self.rules.score_setup("IC")
                log.info(f"IC Score: {score}/100")
                if score >= 55:
                    trade = self.builder.build_iron_condor(
                        self.vwap.session_high(),
                        self.vwap.session_low()
                    )
                    if trade:
                        self._place_combo_trade(trade)
                        self._strategy_traded["IC"] = True
            else:
                log.debug(f"IC skip: {reason} | range_bound={self.vwap.is_range_bound()}")

        # ── Broken Wing Butterfly ────────────────────────────────────────
        if not self._strategy_traded["BWB"]:
            ok, reason = self.rules.can_trade_bwb()
            if ok:
                if self.vwap.is_bullish_trend():
                    direction = "BULL"
                elif self.vwap.is_bearish_trend():
                    direction = "BEAR"
                else:
                    direction = None

                if direction:
                    score = self.rules.score_setup("BWB")
                    log.info(f"BWB Score: {score}/100 | direction={direction}")
                    if score >= 55:
                        trade = self.builder.build_bwb(direction)
                        if trade:
                            self._place_combo_trade(trade)
                            self._strategy_traded["BWB"] = True
            else:
                log.debug(f"BWB skip: {reason}")

        # ── Vertical Scalp ───────────────────────────────────────────────
        if not self._strategy_traded["VS"]:
            ok, reason = self.rules.can_trade_vs()
            if ok:
                # Scalp requires fresh momentum confirmation
                if self.vwap.is_bullish_trend():
                    direction = "BULL"
                elif self.vwap.is_bearish_trend():
                    direction = "BEAR"
                else:
                    direction = None

                if direction:
                    score = self.rules.score_setup("VS")
                    log.info(f"VS Score: {score}/100 | direction={direction}")
                    if score >= 70:    # higher bar for scalp
                        trade = self.builder.build_vertical(direction)
                        if trade:
                            self._place_combo_trade(trade)
                            self._strategy_traded["VS"] = True
            else:
                log.debug(f"VS skip: {reason}")

    def _place_combo_trade(self, trade: dict):
        """
        In production with resolved conIds, builds a BAG contract and places
        a single combo limit order. Shown here as individual leg orders for clarity.
        """
        log.info(f"━━━ PLACING {trade['strategy']} | qty={trade['qty']} ━━━")
        order_ids = []
        for leg in trade["legs"]:
            oid = self.wrapper.next_order_id
            self.wrapper.next_order_id += 1
            qty = trade["qty"] * leg.get("ratio", 1)
            price = 0.00   # TODO: pull live mid from option chain
            o = limit_order(leg["action"], qty, price)
            self.client.placeOrder(oid, leg["contract"], o)
            order_ids.append(oid)
            log.info(f"  Leg: {leg['action']:4s} {qty}× {leg['strike']}{leg['right']} oid={oid}")
            time.sleep(0.1)

        entry_price = trade.get("credit_target", trade.get("max_debit", 0.0))
        self.monitor.register_trade(trade, entry_price, order_ids)

    def run(self):
        self._running = True
        log.info("=" * 60)
        log.info("SPX 0DTE Bot starting...")
        log.info(f"Account: {self.cfg.ACCOUNT} | Port: {self.cfg.PORT}")
        log.info("=" * 60)

        self.connect()
        self.subscribe_account()
        self.subscribe_market_data()
        time.sleep(2)   # let initial data flow in
        self.request_historical_bars()

        log.info("Bot running. Will evaluate every 60 seconds during trading hours.")

        try:
            while self._running:
                now = datetime.now(ET)
                # Only loop during trading hours
                from datetime import time as dtime
                if dtime(9, 30) <= now.time() <= dtime(16, 0):
                    self.monitor.check_exits()
                    # Evaluate every 60 seconds
                    if now.second < 5:
                        self.evaluate_and_trade()
                time.sleep(5)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — shutting down")
        finally:
            self.shutdown()

    def shutdown(self):
        log.info("Shutting down — closing all positions...")
        self.monitor._close_all("BOT SHUTDOWN")
        time.sleep(2)
        self.client.disconnect()
        log.info("Disconnected. Goodbye.")


# ════════════════════════════════════════════════════════════════════════════
# ⓫  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SPX 0DTE IBKR Bot")
    parser.add_argument("--port", type=int, default=4002, help="TWS port (4002=paper, 4001=live)")
    parser.add_argument("--account", type=str, default="DU000000", help="IBKR account ID")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="IBKR Gateway/TWS host")
    args = parser.parse_args()

    Config.PORT = args.port
    Config.ACCOUNT = args.account
    Config.HOST = args.host

    bot = SPX0DTEBot()
    bot.run()
