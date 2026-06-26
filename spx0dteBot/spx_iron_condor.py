"""
SPX 0DTE Iron Condor Auto-Trader
=================================
Uses IBKR Official TWS API (ibapi) — IB Gateway port 4002 (paper)

Strategy:
  - Sell 10–15 delta call spread + put spread on SPX (0DTE)
  - Protection wings: 30 points wide
  - Target net premium: $200–$300 total ($100–$150 per side)
  - Stop loss: if mark value of either spread >= total premium collected
  - Take profit: if short leg mark price <= $0.05

Architecture:
  - Single EClient/EWrapper subclass
  - Request chain: connect → SPX chain → filter strikes → price spreads →
    validate premium → place combo orders → monitor → manage exits
"""

import time
import threading
import logging
import sys
from datetime import datetime, date
from typing import Optional
from dataclasses import dataclass, field

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ComboLeg
from ibapi.order import Order
from ibapi.common import TickerId, BarData
from ibapi.ticktype import TickTypeEnum

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("iron_condor.log"),
    ],
)
log = logging.getLogger("IronCondor")

# ── Configuration ─────────────────────────────────────────────────────────────
HOST            = "192.168.1.116"
PORT            = 4002          # IB Gateway paper
CLIENT_ID       = 1

# Strategy params
DELTA_MIN       = 0.10          # 10 delta
DELTA_MAX       = 0.15          # 15 delta
WING_WIDTH      = 30            # points between short & long strike
TARGET_PREM_MIN = 200           # total net credit floor  ($)
TARGET_PREM_MAX = 300           # total net credit ceiling ($)
SIDE_PREM_MIN   = 100           # per-side credit floor   ($)
SIDE_PREM_MAX   = 150           # per-side credit ceiling ($)
MULTIPLIER      = 100           # SPX multiplier

TAKE_PROFIT_PRICE = 0.05        # short leg mark <= $0.05 → close
# Stop loss: triggered when mark of either spread >= total credit collected

ENTRY_LATEST_HOUR = 10          # don't enter after 10:00 ET
ENTRY_LATEST_MIN  = 30
EXIT_FORCE_HOUR   = 15          # force-close at 15:45 ET
EXIT_FORCE_MIN    = 45

POLL_INTERVAL   = 15            # seconds between P&L polls

# Request ID namespaces
REQ_TICKER_BASE = 1000
REQ_CHAIN       = 2000
REQ_HIST_BASE   = 3000


# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class OptionLeg:
    strike: float
    right: str          # "C" or "P"
    expiry: str         # YYYYMMDD
    con_id: int = 0
    bid: float = 0.0
    ask: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 2)
        return 0.0


@dataclass
class Spread:
    short: OptionLeg
    long:  OptionLeg
    side:  str          # "CALL" or "PUT"

    @property
    def net_credit(self) -> float:
        """Net credit in dollars (×100)."""
        return round((self.short.mid - self.long.mid) * MULTIPLIER, 2)


@dataclass
class IronCondor:
    call_spread: Optional[Spread] = None
    put_spread:  Optional[Spread] = None
    order_id_call: int = 0
    order_id_put:  int = 0
    filled_call_credit: float = 0.0     # actual fill credit per share
    filled_put_credit:  float = 0.0
    total_credit: float = 0.0           # dollars
    state: str = "INIT"                 # INIT → PLACED → FILLED → MANAGING → CLOSED


# ── Main App ──────────────────────────────────────────────────────────────────
class IronCondorApp(EWrapper, EClient):

    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.condor = IronCondor()
        self.next_order_id: int = 0
        self._req_id_counter: int = REQ_TICKER_BASE

        # Threading events
        self._connected       = threading.Event()
        self._chain_ready     = threading.Event()
        self._prices_ready    = threading.Event()
        self._order_ready     = threading.Event()

        # Option chain data
        self.option_expirations: list[str] = []
        self.option_strikes: list[float] = []
        self.option_rights: list[str] = []
        self.chain_params: dict = {}           # exchange, multiplier

        # Conid map: (strike, right) → con_id
        self.conid_map: dict[tuple, int] = {}
        self._pending_details: int = 0
        self._details_lock = threading.Lock()

        # Price data: req_id → (bid, ask)
        self.price_data: dict[int, dict] = {}
        self._price_reqs: set[int] = set()

        # Monitor thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()

        # Mark prices for open positions (req_id → mid)
        self.mark_prices: dict[int, float] = {}
        self.mark_req_map: dict[int, str] = {}   # req_id → "call_short"|"call_long"|"put_short"|"put_long"

        # Order tracking
        self.open_orders: dict[int, str] = {}    # order_id → status
        self.filled_prices: dict[int, float] = {}  # order_id → avg fill price

    # ── EWrapper callbacks ───────────────────────────────────────────────────

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        log.info(f"Connected. Next order ID: {orderId}")
        self._connected.set()

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        # Suppress informational codes
        if errorCode in (2104, 2106, 2158, 2176):
            return
        level = logging.WARNING if errorCode < 2000 else logging.ERROR
        log.log(level, f"[{reqId}] Error {errorCode}: {errorString}")

    # Option chain
    def securityDefinitionOptionParameter(
        self, reqId, exchange, underlyingConId, tradingClass,
        multiplier, expirations, strikes
    ):
        if exchange != "SMART":
            return
        self.chain_params = {"exchange": exchange, "multiplier": multiplier,
                             "tradingClass": tradingClass}
        self.option_expirations = sorted(expirations)
        self.option_strikes     = sorted(strikes)
        log.info(f"Chain: {len(expirations)} expiries, {len(strikes)} strikes")

    def securityDefinitionOptionParameterEnd(self, reqId):
        self._chain_ready.set()

    # Contract details (for con_ids)
    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        key = (c.strike, c.right)
        self.conid_map[key] = c.conId
        with self._details_lock:
            self._pending_details -= 1

    def contractDetailsEnd(self, reqId):
        pass   # use counter instead

    # Market data (bid/ask for pricing)
    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId not in self.price_data:
            self.price_data[reqId] = {}
        name = TickTypeEnum.to_str(tickType)
        if tickType == 1:    # BID
            self.price_data[reqId]["bid"] = price
        elif tickType == 2:  # ASK
            self.price_data[reqId]["ask"] = price

        # If we have both, check completeness
        d = self.price_data[reqId]
        if "bid" in d and "ask" in d:
            self._price_reqs.discard(reqId)

    def tickSnapshotEnd(self, reqId):
        self._price_reqs.discard(reqId)

    # Order callbacks
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        log.info(f"Order {orderId}: {status}  filled={filled}  avgPx={avgFillPrice}")
        self.open_orders[orderId] = status
        if status == "Filled" and avgFillPrice > 0:
            self.filled_prices[orderId] = avgFillPrice

    def openOrder(self, orderId, contract, order, orderState):
        pass

    def execDetails(self, reqId, contract, execution):
        log.info(f"Exec: order={execution.orderId}  side={execution.side}  "
                 f"shares={execution.shares}  px={execution.price}")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _next_req_id(self) -> int:
        self._req_id_counter += 1
        return self._req_id_counter

    def _spx_contract(self) -> Contract:
        c = Contract()
        c.symbol   = "SPX"
        c.secType  = "IND"
        c.currency = "USD"
        c.exchange = "CBOE"
        return c

    def _option_contract(self, strike: float, right: str, expiry: str) -> Contract:
        c = Contract()
        c.symbol    = "SPX"
        c.secType   = "OPT"
        c.currency  = "USD"
        c.exchange  = "SMART"
        c.strike    = strike
        c.right     = right
        c.lastTradeDateOrContractMonth = expiry
        c.multiplier = "100"
        c.tradingClass = self.chain_params.get("tradingClass", "SPXW")
        return c

    def _today_expiry(self) -> str:
        return date.today().strftime("%Y%m%d")

    # ── Step 1: Fetch option chain ────────────────────────────────────────────

    def fetch_chain(self):
        log.info("Requesting SPX option chain …")
        spx = self._spx_contract()
        self.reqSecDefOptParams(REQ_CHAIN, "SPX", "", "IND", 0)
        if not self._chain_ready.wait(timeout=30):
            raise TimeoutError("Option chain request timed out")

    # ── Step 2: Resolve con_ids for target strikes ────────────────────────────

    def resolve_conids(self, strikes_rights: list[tuple[float, str]], expiry: str):
        """Request contract details to get con_ids for given (strike, right) pairs."""
        self._pending_details = len(strikes_rights)
        for strike, right in strikes_rights:
            rid = self._next_req_id()
            c = self._option_contract(strike, right, expiry)
            self.reqContractDetails(rid, c)

        # Wait until all details arrive
        deadline = time.time() + 20
        while time.time() < deadline:
            with self._details_lock:
                if self._pending_details <= 0:
                    break
            time.sleep(0.1)
        log.info(f"Resolved {len(self.conid_map)} con_ids")

    # ── Step 3: Price legs ────────────────────────────────────────────────────

    def price_legs(self, legs: list[OptionLeg], expiry: str) -> dict[tuple, tuple]:
        """Snapshot bid/ask for each leg. Returns {(strike,right): (bid,ask)}."""
        req_map: dict[int, tuple] = {}   # req_id → (strike, right)
        self._price_reqs.clear()
        self.price_data.clear()

        for leg in legs:
            rid = self._next_req_id()
            c = self._option_contract(leg.strike, leg.right, expiry)
            req_map[rid] = (leg.strike, leg.right)
            self._price_reqs.add(rid)
            self.reqMktData(rid, c, "", True, False, [])   # snapshot

        # Wait up to 15 s for all snapshots
        deadline = time.time() + 15
        while self._price_reqs and time.time() < deadline:
            time.sleep(0.2)
        self.cancelMktData  # snapshots auto-cancel

        result = {}
        for rid, key in req_map.items():
            d = self.price_data.get(rid, {})
            result[key] = (d.get("bid", 0.0), d.get("ask", 0.0))
        return result

    # ── Step 4: Select strikes ────────────────────────────────────────────────

    def select_strikes(self, spx_price: float, expiry: str) -> Optional[IronCondor]:
        """
        Find call & put spreads that satisfy delta & premium criteria.
        We approximate delta from strike distance. Real delta requires
        option Greeks (reqMktData with 100 generic ticks) — implemented below.
        """
        log.info(f"SPX spot ≈ {spx_price:.2f}  expiry={expiry}")

        # Filter strikes within a reasonable range (±5% of spot)
        lower = spx_price * 0.95
        upper = spx_price * 1.05
        candidates = [s for s in self.option_strikes if lower <= s <= upper]

        # Build candidate legs: OTM calls above spot, OTM puts below
        call_shorts = [s for s in candidates if s > spx_price]
        put_shorts  = [s for s in candidates if s < spx_price]

        if not call_shorts or not put_shorts:
            log.error("No suitable OTM strikes found")
            return None

        # Prepare all legs we might need
        all_legs = []
        call_spread_candidates = []
        for s in call_shorts:
            long_s = s + WING_WIDTH
            if long_s in self.option_strikes:
                short_leg = OptionLeg(s, "C", expiry)
                long_leg  = OptionLeg(long_s, "C", expiry)
                all_legs += [short_leg, long_leg]
                call_spread_candidates.append((short_leg, long_leg))

        put_spread_candidates = []
        for s in put_shorts:
            long_s = s - WING_WIDTH
            if long_s in self.option_strikes:
                short_leg = OptionLeg(s, "P", expiry)
                long_leg  = OptionLeg(long_s, "P", expiry)
                all_legs += [short_leg, long_leg]
                put_spread_candidates.append((short_leg, long_leg))

        if not call_spread_candidates or not put_spread_candidates:
            log.error("No spread candidates with 30-point wings found")
            return None

        # Resolve con_ids for all legs
        pairs = list({(l.strike, l.right) for l in all_legs})
        self.resolve_conids(pairs, expiry)

        # Price all legs at once
        price_map = self.price_legs(all_legs, expiry)
        for leg in all_legs:
            bid, ask = price_map.get((leg.strike, leg.right), (0.0, 0.0))
            leg.bid = bid
            leg.ask = ask

        # ── Fetch Greeks for delta filtering ─────────────────────────────────
        greek_map = self.fetch_greeks(all_legs, expiry)

        # ── Select best call spread ───────────────────────────────────────────
        best_call: Optional[Spread] = None
        for short_leg, long_leg in sorted(call_spread_candidates, key=lambda x: x[0].strike):
            delta = abs(greek_map.get((short_leg.strike, "C"), {}).get("delta", 0))
            if not (DELTA_MIN <= delta <= DELTA_MAX):
                continue
            spread = Spread(short_leg, long_leg, "CALL")
            credit = spread.net_credit
            log.info(f"  CALL spread {short_leg.strike}/{long_leg.strike}  "
                     f"delta={delta:.3f}  credit=${credit:.0f}")
            if SIDE_PREM_MIN <= credit <= SIDE_PREM_MAX:
                best_call = spread
                break   # take first valid one (closest to ATM)

        # ── Select best put spread ────────────────────────────────────────────
        best_put: Optional[Spread] = None
        for short_leg, long_leg in sorted(put_spread_candidates,
                                          key=lambda x: x[0].strike, reverse=True):
            delta = abs(greek_map.get((short_leg.strike, "P"), {}).get("delta", 0))
            if not (DELTA_MIN <= delta <= DELTA_MAX):
                continue
            spread = Spread(short_leg, long_leg, "PUT")
            credit = spread.net_credit
            log.info(f"  PUT  spread {short_leg.strike}/{long_leg.strike}  "
                     f"delta={delta:.3f}  credit=${credit:.0f}")
            if SIDE_PREM_MIN <= credit <= SIDE_PREM_MAX:
                best_put = spread
                break

        if not best_call or not best_put:
            log.warning("Could not find spreads meeting all criteria")
            return None

        total = best_call.net_credit + best_put.net_credit
        if not (TARGET_PREM_MIN <= total <= TARGET_PREM_MAX):
            log.warning(f"Total premium ${total:.0f} outside ${TARGET_PREM_MIN}–${TARGET_PREM_MAX}")
            return None

        log.info(f"✓ Iron Condor selected  "
                 f"CALL {best_call.short.strike}/{best_call.long.strike} "
                 f"PUT {best_put.short.strike}/{best_put.long.strike}  "
                 f"total credit=${total:.0f}")

        ic = IronCondor()
        ic.call_spread = best_call
        ic.put_spread  = best_put
        ic.total_credit = total
        return ic

    # ── Fetch Greeks via generic tick 100 ────────────────────────────────────

    def fetch_greeks(self, legs: list[OptionLeg], expiry: str) -> dict:
        """
        Returns {(strike, right): {"delta": float, "gamma": float, ...}}
        Uses reqMktData with generic tick 100 (option greeks).
        """
        greek_map: dict[tuple, dict] = {}
        req_to_key: dict[int, tuple] = {}
        pending: set[int] = set()

        # Temporarily override tickOptionComputation
        _greek_data: dict[int, dict] = {}

        original_toc = self.tickOptionComputation

        def _capture_greek(reqId, tickType, tickAttrib, impliedVol,
                           delta, optPrice, pvDividend, gamma, vega, theta, undPrice):
            if tickType in (13, 53):   # 13=MODEL, 53=DELAYED_MODEL
                _greek_data.setdefault(reqId, {})
                if delta is not None and abs(delta) <= 1:
                    _greek_data[reqId]["delta"] = delta
                if gamma is not None:
                    _greek_data[reqId]["gamma"] = gamma

        self.tickOptionComputation = _capture_greek

        for leg in legs:
            key = (leg.strike, leg.right)
            if key in greek_map:
                continue
            rid = self._next_req_id()
            c = self._option_contract(leg.strike, leg.right, expiry)
            req_to_key[rid] = key
            pending.add(rid)
            self.reqMktData(rid, c, "100", True, False, [])

        deadline = time.time() + 15
        while pending and time.time() < deadline:
            for rid in list(pending):
                if rid in _greek_data and "delta" in _greek_data[rid]:
                    pending.discard(rid)
            time.sleep(0.2)

        self.tickOptionComputation = original_toc

        for rid, key in req_to_key.items():
            greek_map[key] = _greek_data.get(rid, {})

        found = sum(1 for v in greek_map.values() if "delta" in v)
        log.info(f"Greeks fetched: {found}/{len(req_to_key)} legs")
        return greek_map

    # ── Step 5: Get SPX spot price ────────────────────────────────────────────

    def get_spx_price(self) -> float:
        rid = self._next_req_id()
        self.price_data[rid] = {}
        spx = self._spx_contract()
        self.reqMktData(rid, spx, "", True, False, [])
        deadline = time.time() + 10
        while time.time() < deadline:
            d = self.price_data.get(rid, {})
            for key in ("ask", "bid", "last", "close"):
                if key in d and d[key] > 0:
                    self.cancelMktData(rid)
                    return d[key]
            time.sleep(0.3)
        raise RuntimeError("Could not get SPX spot price")

    def tickPrice(self, reqId, tickType, price, attrib):
        """Override to also catch last/close for SPX spot."""
        if reqId not in self.price_data:
            self.price_data[reqId] = {}
        if price > 0:
            tick_name = {1: "bid", 2: "ask", 4: "last",
                         6: "high", 7: "low", 9: "close"}.get(tickType)
            if tick_name:
                self.price_data[reqId][tick_name] = price
        # Also handle bid/ask for options
        if tickType == 1:
            self.price_data[reqId]["bid"] = price if price > 0 else 0
        elif tickType == 2:
            self.price_data[reqId]["ask"] = price if price > 0 else 0
        d = self.price_data[reqId]
        if "bid" in d and "ask" in d:
            self._price_reqs.discard(reqId)

    def tickSnapshotEnd(self, reqId):
        self._price_reqs.discard(reqId)

    # ── Step 6: Place orders ──────────────────────────────────────────────────

    def _make_spread_contract(self, spread: Spread) -> Contract:
        """BAG contract for a vertical spread."""
        c = Contract()
        c.symbol   = "SPX"
        c.secType  = "BAG"
        c.currency = "USD"
        c.exchange = "SMART"

        short_id = self.conid_map.get((spread.short.strike, spread.short.right), 0)
        long_id  = self.conid_map.get((spread.long.strike,  spread.long.right),  0)

        if not short_id or not long_id:
            raise ValueError(f"Missing con_ids for {spread.side} spread")

        leg1 = ComboLeg()
        leg1.conId     = short_id
        leg1.ratio     = 1
        leg1.action    = "SELL"
        leg1.exchange  = "SMART"

        leg2 = ComboLeg()
        leg2.conId     = long_id
        leg2.ratio     = 1
        leg2.action    = "BUY"
        leg2.exchange  = "SMART"

        c.comboLegs = [leg1, leg2]
        return c

    def _credit_limit_order(self, credit_per_share: float, quantity: int = 1) -> Order:
        """LMT order to sell a spread for a net credit."""
        o = Order()
        o.action        = "SELL"
        o.orderType     = "LMT"
        o.totalQuantity = quantity
        o.lmtPrice      = round(credit_per_share, 2)
        o.transmit      = True
        o.tif           = "DAY"
        o.outsideRth    = False
        return o

    def place_iron_condor(self, ic: IronCondor):
        """Place both spread orders."""
        call_credit_per_share = (ic.call_spread.short.mid - ic.call_spread.long.mid)
        put_credit_per_share  = (ic.put_spread.short.mid  - ic.put_spread.long.mid)

        call_contract = self._make_spread_contract(ic.call_spread)
        put_contract  = self._make_spread_contract(ic.put_spread)

        call_order = self._credit_limit_order(call_credit_per_share)
        put_order  = self._credit_limit_order(put_credit_per_share)

        call_oid = self.next_order_id
        self.next_order_id += 1
        put_oid  = self.next_order_id
        self.next_order_id += 1

        ic.order_id_call = call_oid
        ic.order_id_put  = put_oid

        log.info(f"Placing CALL spread order #{call_oid}  "
                 f"credit=${call_credit_per_share:.2f}/share")
        self.placeOrder(call_oid, call_contract, call_order)
        time.sleep(1)

        log.info(f"Placing PUT spread order #{put_oid}  "
                 f"credit=${put_credit_per_share:.2f}/share")
        self.placeOrder(put_oid, put_contract, put_order)

        ic.state = "PLACED"

    # ── Step 7: Wait for fills ────────────────────────────────────────────────

    def wait_for_fills(self, ic: IronCondor, timeout: int = 120) -> bool:
        """Block until both orders are filled or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            call_status = self.open_orders.get(ic.order_id_call, "")
            put_status  = self.open_orders.get(ic.order_id_put,  "")
            if call_status == "Filled" and put_status == "Filled":
                ic.filled_call_credit = self.filled_prices.get(ic.order_id_call, 0)
                ic.filled_put_credit  = self.filled_prices.get(ic.order_id_put,  0)
                ic.total_credit = (ic.filled_call_credit + ic.filled_put_credit) * MULTIPLIER
                log.info(f"Both spreads filled. Total credit = ${ic.total_credit:.0f}")
                ic.state = "FILLED"
                return True
            # Cancel unfilled after 2 min
            time.sleep(2)
        log.warning("Fill timeout — cancelling unfilled orders")
        for oid in (ic.order_id_call, ic.order_id_put):
            if self.open_orders.get(oid, "") != "Filled":
                self.cancelOrder(oid, "")
        return False

    # ── Step 8: Monitor & manage ──────────────────────────────────────────────

    def _get_leg_mid(self, strike: float, right: str, expiry: str) -> float:
        """Snapshot mid price of a single option."""
        rid = self._next_req_id()
        self.price_data[rid] = {}
        self._price_reqs.add(rid)
        c = self._option_contract(strike, right, expiry)
        self.reqMktData(rid, c, "", True, False, [])
        deadline = time.time() + 8
        while rid in self._price_reqs and time.time() < deadline:
            time.sleep(0.2)
        d = self.price_data.get(rid, {})
        bid = d.get("bid", 0.0)
        ask = d.get("ask", 0.0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        return 0.0

    def _spread_mark(self, spread: Spread, expiry: str) -> float:
        """Current mark value of the spread (short mid - long mid), in dollars."""
        short_mid = self._get_leg_mid(spread.short.strike, spread.short.right, expiry)
        long_mid  = self._get_leg_mid(spread.long.strike,  spread.long.right,  expiry)
        return round((short_mid - long_mid) * MULTIPLIER, 2)

    def _close_spread(self, spread: Spread, expiry: str, label: str):
        """Market order to buy back (close) the spread."""
        contract = self._make_spread_contract(spread)
        o = Order()
        o.action        = "BUY"
        o.orderType     = "MKT"
        o.totalQuantity = 1
        o.transmit      = True
        o.tif           = "DAY"

        oid = self.next_order_id
        self.next_order_id += 1
        log.info(f"Closing {label} spread (MKT BUY) order #{oid}")
        self.placeOrder(oid, contract, o)
        return oid

    def _close_leg_at_market(self, leg: OptionLeg, expiry: str, action: str, label: str):
        """Close a single option leg at market."""
        c = self._option_contract(leg.strike, leg.right, expiry)
        o = Order()
        o.action        = action   # "BUY" to close a short, "SELL" to close a long
        o.orderType     = "MKT"
        o.totalQuantity = 1
        o.transmit      = True
        o.tif           = "DAY"
        oid = self.next_order_id
        self.next_order_id += 1
        log.info(f"{label}: {action} 1 {leg.right} {leg.strike}  order #{oid}")
        self.placeOrder(oid, c, o)
        return oid

    def monitor_position(self, ic: IronCondor):
        """Main monitoring loop — runs in background thread."""
        expiry = ic.call_spread.short.expiry
        log.info(f"Starting monitor loop  poll_interval={POLL_INTERVAL}s")
        ic.state = "MANAGING"

        while not self._stop_monitor.is_set():
            now = datetime.now()

            # Force-close near EOD
            if (now.hour > EXIT_FORCE_HOUR or
                    (now.hour == EXIT_FORCE_HOUR and now.minute >= EXIT_FORCE_MIN)):
                log.info("EOD force-close triggered")
                self._close_spread(ic.call_spread, expiry, "CALL")
                self._close_spread(ic.put_spread,  expiry, "PUT")
                ic.state = "CLOSED"
                break

            # ── Take-profit check ─────────────────────────────────────────
            call_short_mid = self._get_leg_mid(
                ic.call_spread.short.strike, "C", expiry)
            put_short_mid  = self._get_leg_mid(
                ic.put_spread.short.strike, "P", expiry)

            log.info(f"Mark — call short: ${call_short_mid:.2f}  "
                     f"put short: ${put_short_mid:.2f}")

            tp_call = call_short_mid <= TAKE_PROFIT_PRICE
            tp_put  = put_short_mid  <= TAKE_PROFIT_PRICE

            if tp_call and tp_put:
                log.info("✓ TAKE PROFIT both sides ≤ $0.05 — closing all")
                self._close_spread(ic.call_spread, expiry, "CALL")
                self._close_spread(ic.put_spread,  expiry, "PUT")
                ic.state = "CLOSED"
                break
            elif tp_call:
                log.info("✓ TAKE PROFIT call side — closing call spread")
                self._close_spread(ic.call_spread, expiry, "CALL")
            elif tp_put:
                log.info("✓ TAKE PROFIT put side — closing put spread")
                self._close_spread(ic.put_spread, expiry, "PUT")

            # ── Stop-loss check ───────────────────────────────────────────
            # Trigger if mark debit to close either spread >= total credit received
            sl_threshold = ic.total_credit  # in dollars

            call_spread_mark = self._spread_mark(ic.call_spread, expiry)
            put_spread_mark  = self._spread_mark(ic.put_spread,  expiry)

            # When short > long the spread has moved against us (we're net debit to close)
            # Spread mark loss = (cost to buy back) - (original credit received per side)
            call_loss = call_spread_mark - (ic.filled_call_credit * MULTIPLIER)
            put_loss  = put_spread_mark  - (ic.filled_put_credit  * MULTIPLIER)

            log.info(f"P&L — call spread loss: ${call_loss:.0f}  "
                     f"put spread loss: ${put_loss:.0f}  "
                     f"SL threshold: ${sl_threshold:.0f}")

            if call_loss >= sl_threshold:
                log.warning(f"⚠ STOP LOSS call spread (loss ${call_loss:.0f})")
                self._close_spread(ic.call_spread, expiry, "CALL")

            if put_loss >= sl_threshold:
                log.warning(f"⚠ STOP LOSS put spread (loss ${put_loss:.0f})")
                self._close_spread(ic.put_spread, expiry, "PUT")

            if ic.state == "CLOSED":
                break

            time.sleep(POLL_INTERVAL)

        log.info(f"Monitor loop ended. Final state: {ic.state}")

    # ── Main entry ────────────────────────────────────────────────────────────

    def run_strategy(self):
        """Full strategy lifecycle."""
        try:
            # ── Entry time gate ───────────────────────────────────────────────
            now = datetime.now()
            if (now.hour > ENTRY_LATEST_HOUR or
                    (now.hour == ENTRY_LATEST_HOUR and now.minute > ENTRY_LATEST_MIN)):
                log.warning(f"After {ENTRY_LATEST_HOUR}:{ENTRY_LATEST_MIN:02d} ET — skipping entry")
                return

            # ── Get SPX spot ──────────────────────────────────────────────────
            log.info("Getting SPX spot price …")
            spot = self.get_spx_price()
            log.info(f"SPX spot: {spot:.2f}")

            # ── Fetch chain ───────────────────────────────────────────────────
            self.fetch_chain()

            # ── Determine today's 0DTE expiry ─────────────────────────────────
            expiry = self._today_expiry()
            if expiry not in self.option_expirations:
                log.error(f"No 0DTE expiry {expiry} in chain. "
                          f"Available: {self.option_expirations[:5]}")
                return

            # ── Select strikes ────────────────────────────────────────────────
            ic = self.select_strikes(spot, expiry)
            if ic is None:
                log.error("No valid iron condor found — exiting")
                return
            self.condor = ic

            # ── Place orders ──────────────────────────────────────────────────
            self.place_iron_condor(ic)

            # ── Wait for fills ────────────────────────────────────────────────
            if not self.wait_for_fills(ic, timeout=120):
                log.error("Could not get fills — aborting")
                return

            # ── Monitor ───────────────────────────────────────────────────────
            self._monitor_thread = threading.Thread(
                target=self.monitor_position, args=(ic,), daemon=True)
            self._monitor_thread.start()
            self._monitor_thread.join()

        except Exception as e:
            log.exception(f"Strategy error: {e}")
        finally:
            log.info("Disconnecting …")
            self.disconnect()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = IronCondorApp()

    log.info(f"Connecting to IB Gateway {HOST}:{PORT} (paper) clientId={CLIENT_ID}")
    app.connect(HOST, PORT, CLIENT_ID)

    # Start EClient message loop in background thread
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    # Wait for connection handshake
    if not app._connected.wait(timeout=15):
        log.error("Connection timed out — is IB Gateway running on port 4002?")
        sys.exit(1)

    # Run the strategy (blocks until done)
    app.run_strategy()


if __name__ == "__main__":
    main()
