"""
gateway.py — Raw IBKR TWS API connection layer.

Responsibilities:
  - Maintain the socket connection to IB Gateway
  - Dispatch all EWrapper callbacks into thread-safe queues / dicts
  - Provide next_req_id() and next_order_id() utilities
  - NO strategy logic, NO market data interpretation, NO order construction

Every higher-level module (MarketData, OrderManager) receives a Gateway
instance and calls its methods + reads its callback stores.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from ibapi.client  import EClient
from ibapi.wrapper import EWrapper
from ibapi.common  import TickerId, SetOfFloat, SetOfString
from ibapi.contract import ContractDetails

log = logging.getLogger("Gateway")


class IBGateway(EWrapper, EClient):
    """
    Pure connection and callback dispatcher.

    All EWrapper callbacks store their data in public dicts/lists.
    Higher-level layers subscribe via register_callback() or poll directly.
    """

    def __init__(self, host: str, port: int, client_id: int):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.host      = host
        self.port      = port
        self.client_id = client_id

        # ── Connection state ──────────────────────────────────────────────────
        self._connected        = threading.Event()
        self._api_thread: Optional[threading.Thread] = None
        # Set by connectionClosed() when IBKR's server drops the socket
        # (daily maintenance reset, network blip, IB Gateway restart, etc).
        # This is DIFFERENT from a routine error()/system-bulletin callback —
        # see connectionClosed() below — and is the actual signal a caller
        # should watch for to know the connection needs re-establishing.
        self._connection_lost  = threading.Event()

        # ── ID counters (thread-safe) ─────────────────────────────────────────
        self._order_id_lock = threading.Lock()
        self._req_id_lock   = threading.Lock()
        self._next_order_id_val: int = 0
        self._req_id_counter:    int = 10_000   # start well above 0

        # ── Callback stores ───────────────────────────────────────────────────

        # Option chain: reqId → chain data (set when paramEnd fires)
        # shape: { req_id: {"expirations": [...], "strikes": [...],
        #                   "trading_class": str, "multiplier": str} }
        self.chain_data:  dict[int, dict]  = {}
        self._chain_events: dict[int, threading.Event] = {}

        # Contract details: req_id → list[ContractDetails]
        self.contract_details:  dict[int, list] = {}
        self._details_events:   dict[int, threading.Event] = {}

        # Tick prices & sizes: req_id → {"bid":f, "ask":f, "last":f, "close":f, ...}
        self.tick_data:  dict[int, dict] = {}
        self._tick_events: dict[int, threading.Event] = {}   # set on snapshot end

        # Historical bars: req_id → list[BarData]  (set when historicalDataEnd fires)
        self.historical_bars: dict[int, list] = {}
        self._historical_events: dict[int, threading.Event] = {}

        # Option computation (Greeks): req_id → {"delta", "gamma", "theta", "vega", "iv"}
        self.greek_data: dict[int, dict] = {}

        # Order status: order_id → {"status": str, "filled": float, "avg_price": float}
        self.order_status: dict[int, dict] = {}
        self._order_events: dict[int, threading.Event] = {}  # set on fill

        # Error store: list of (req_id, code, msg)
        self.errors: list[tuple] = []

        # Generic event subscribers: event_name → list[callable]
        self._subscribers: dict[str, list[Callable]] = {}

    # ── Connect / disconnect ──────────────────────────────────────────────────

    def connect_and_run(self, timeout: float = 15.0,
                        settle_sec: float = 1.0) -> bool:
        """
        Connect to IB Gateway and start the message loop thread.
        Returns True if connection handshake succeeds within timeout.

        After nextValidId fires, sleeps settle_sec before returning.
        IBKR's own connectivity documentation warns: "messages sent
        immediately after receiving nextValidId could be dropped and would
        need to be resent" — in rare cases there's a momentary delay in IB
        Gateway/TWS finishing its own connection to IB's servers even after
        it ack's our handshake. This matters most for short-lived processes
        like entry_runner.py, which connect fresh every few minutes and
        immediately fire reqContractDetails/reqMktData with zero settling
        time — exactly the scenario IBKR's docs call out as risky.
        """
        log.info(f"Connecting to IB Gateway {self.host}:{self.port} "
                 f"clientId={self.client_id}")
        self._connection_lost.clear()
        self.connect(self.host, self.port, self.client_id)

        self._api_thread = threading.Thread(
            target=self.run, name="ibapi-msg-loop", daemon=True)
        self._api_thread.start()

        if not self._connected.wait(timeout=timeout):
            log.error("Connection timed out — is IB Gateway running?")
            return False

        if settle_sec > 0:
            time.sleep(settle_sec)
        return True

    def is_connected_and_alive(self) -> bool:
        """
        True if we've completed the handshake AND haven't since received a
        connectionClosed() callback. Use this in long-running loops (e.g.
        monitor_daemon.py's discovery loop) to detect a server-side drop —
        IBKR explicitly documents that daily server maintenance resets will
        close the socket at least once a day, which is expected and
        recoverable, not a fatal error.
        """
        return self._connected.is_set() and not self._connection_lost.is_set()

    def reconnect(self, timeout: float = 15.0, max_attempts: int = 5,
                  backoff_sec: float = 5.0) -> bool:
        """
        Attempt to re-establish a dropped connection with linear backoff.
        Safe to call after connectionClosed() has fired. Resets internal
        connection state and retries connect_and_run() up to max_attempts.
        """
        for attempt in range(1, max_attempts + 1):
            log.warning(
                f"Reconnect attempt {attempt}/{max_attempts} to "
                f"{self.host}:{self.port} (clientId={self.client_id}) …"
            )
            self._connected.clear()
            try:
                self.disconnect()
            except Exception:
                pass   # already disconnected; ignore

            time.sleep(backoff_sec)

            if self.connect_and_run(timeout=timeout):
                log.info("Reconnected successfully")
                return True

        log.error(
            f"Failed to reconnect after {max_attempts} attempts — "
            f"giving up. Check that IB Gateway is running and logged in."
        )
        return False

    def safe_disconnect(self):
        log.info("Disconnecting from IB Gateway")
        self.disconnect()

    # ── ID utilities ─────────────────────────────────────────────────────────

    def next_req_id(self) -> int:
        with self._req_id_lock:
            self._req_id_counter += 1
            return self._req_id_counter

    def next_order_id(self) -> int:
        with self._order_id_lock:
            val = self._next_order_id_val
            self._next_order_id_val += 1
            return val

    # ── Event helpers ─────────────────────────────────────────────────────────

    def register_callback(self, event: str, fn: Callable):
        self._subscribers.setdefault(event, []).append(fn)

    def _emit(self, event: str, *args, **kwargs):
        for fn in self._subscribers.get(event, []):
            try:
                fn(*args, **kwargs)
            except Exception as e:
                log.exception(f"Subscriber error on event '{event}': {e}")

    # ── Prepare per-request event objects ────────────────────────────────────

    def prepare_chain_request(self, req_id: int):
        self.chain_data[req_id]   = {}
        self._chain_events[req_id] = threading.Event()

    def wait_for_chain(self, req_id: int, timeout: float = 30.0) -> bool:
        ev = self._chain_events.get(req_id)
        return ev.wait(timeout=timeout) if ev else False

    def prepare_details_request(self, req_id: int):
        self.contract_details[req_id] = []
        self._details_events[req_id]  = threading.Event()

    def wait_for_details(self, req_id: int, timeout: float = 20.0) -> bool:
        ev = self._details_events.get(req_id)
        return ev.wait(timeout=timeout) if ev else False

    def prepare_tick_request(self, req_id: int):
        self.tick_data[req_id]   = {}
        self.greek_data[req_id]  = {}
        self._tick_events[req_id] = threading.Event()

    def wait_for_tick(self, req_id: int, timeout: float = 10.0) -> bool:
        ev = self._tick_events.get(req_id)
        return ev.wait(timeout=timeout) if ev else False

    def prepare_historical_request(self, req_id: int):
        self.historical_bars[req_id]     = []
        self._historical_events[req_id]  = threading.Event()

    def wait_for_historical(self, req_id: int, timeout: float = 15.0) -> bool:
        ev = self._historical_events.get(req_id)
        return ev.wait(timeout=timeout) if ev else False

    def errors_for(self, req_id: int) -> list[tuple]:
        """Return all (reqId, code, msg) error tuples recorded for this req_id."""
        return [e for e in self.errors if e[0] == req_id]

    def prepare_order_event(self, order_id: int):
        self._order_events[order_id] = threading.Event()

    def wait_for_fill(self, order_id: int, timeout: float = 120.0) -> bool:
        ev = self._order_events.get(order_id)
        return ev.wait(timeout=timeout) if ev else False

    # =========================================================================
    # EWrapper callbacks — pure data collection, no business logic
    # =========================================================================

    # ── Connection ────────────────────────────────────────────────────────────

    def nextValidId(self, orderId: int):
        with self._order_id_lock:
            self._next_order_id_val = orderId
        log.info(f"Connected. Next order ID: {orderId}")
        self._connection_lost.clear()
        self._connected.set()
        self._emit("connected", orderId)

    def connectionClosed(self):
        """
        Fired when IBKR's server (or local IB Gateway) closes the TCP
        socket. This is NOT the same thing as a system bulletin delivered
        through error() (e.g. routine trading-status announcements) — those
        are informational messages over an OPEN connection. This callback
        means the connection itself is gone and must be re-established
        before any further requests can succeed.

        IBKR explicitly documents that daily server maintenance resets will
        trigger this at least once a day — expected, recoverable behavior,
        not necessarily a fatal error. Long-running callers (e.g.
        monitor_daemon.py) should check is_connected_and_alive() in their
        loop and call reconnect() when this fires, rather than continuing
        to issue requests against a dead socket.
        """
        log.warning("Connection closed by IB Gateway")
        self._connection_lost.set()
        self._connected.clear()
        self._emit("disconnected")

    # ── Errors & warnings ─────────────────────────────────────────────────────

    def error(self, reqId: TickerId, *args):
        """
        Accepts BOTH the old and new ibapi EWrapper.error() signatures:

          Old (ibapi <= 9.81, the version pinned on PyPI):
              error(self, reqId, errorCode, errorString, advancedOrderRejectJson="")

          New (ibapi >= 10.33, IBKR's official downloadable TWS API package —
          PyPI never received this update, so which one you have depends on
          whether you installed via `pip install ibapi` or IBKR's own
          installer/zip):
              error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson="")

        Calling this with the wrong fixed signature doesn't raise — Python
        just maps positional args wrong, silently corrupting the data. That
        is exactly what produced errors like (10002, 1782346134553, '162'):
        the millisecond epoch timestamp landed in the errorCode slot and the
        real code ('162') landed in errorString.

        Detection is by TYPE, not just argument count — both signatures can
        legally have 3 positional args after reqId (old: errorCode, errorString,
        advancedOrderRejectJson; new: errorTime, errorCode, errorString), so
        count alone is ambiguous. The reliable signal is that the new
        signature has TWO leading ints (errorTime, then errorCode) before
        the string errorString, while the old signature has only ONE
        leading int (errorCode) before the string.
        """
        if len(args) >= 2 and isinstance(args[0], int) and isinstance(args[1], int):
            # New signature: (errorTime: int, errorCode: int, errorString: str, [advancedOrderRejectJson])
            # Distinguishing feature: the first TWO positional args are both
            # ints (errorTime, then errorCode) before the string errorString.
            errorTime, errorCode, errorString = args[0], args[1], args[2] if len(args) > 2 else ""
        elif len(args) >= 1 and isinstance(args[0], int):
            # Old signature: (errorCode: int, errorString: str, [advancedOrderRejectJson])
            # Only ONE int before the string.
            errorCode, errorString = args[0], args[1] if len(args) > 1 else ""
        else:
            log.error(f"error() called with unrecognized args: reqId={reqId} args={args}")
            return

        # Suppress purely informational connectivity-status codes
        if errorCode in (2104, 2106, 2107, 2108, 2158, 2176):
            return

        # reqId == -1 means this is NOT tied to any specific request — it's
        # a server-pushed system bulletin or status notice (market open/close
        # announcements, instrument-specific trading-status notices like
        # "ZEROHASH trading resumed", daily connectivity status, etc).
        # These arrive over a perfectly healthy, OPEN connection and do not
        # indicate any problem with the connection or with any pending
        # request. Logging them at WARNING/ERROR made them look like
        # connection failures when they are routine broadcast messages.
        if reqId == -1:
            log.info(f"IBKR system message {errorCode}: {errorString}")
            self._emit("system_message", errorCode, errorString)
            return

        level = logging.WARNING if errorCode >= 2000 else logging.ERROR
        log.log(level, f"IBKR [{reqId}] {errorCode}: {errorString}")
        self.errors.append((reqId, errorCode, errorString))
        self._emit("error", reqId, errorCode, errorString)

    # ── Option chain ──────────────────────────────────────────────────────────

    def securityDefinitionOptionParameter(
        self, reqId: int, exchange: str, underlyingConId: int,
        tradingClass: str, multiplier: str,
        expirations: SetOfString, strikes: SetOfFloat
    ):
        # Collect only SMART exchange entries (IBKR aggregates all exchanges)
        if exchange != "SMART":
            return
        self.chain_data[reqId] = {
            "exchange":     exchange,
            "trading_class": tradingClass,
            "multiplier":   multiplier,
            "expirations":  sorted(expirations),
            "strikes":      sorted(float(s) for s in strikes),
        }
        log.debug(f"Chain [{reqId}]: {len(expirations)} expiries, "
                  f"{len(strikes)} strikes  class={tradingClass}")

    def securityDefinitionOptionParameterEnd(self, reqId: int):
        ev = self._chain_events.get(reqId)
        if ev:
            ev.set()
        self._emit("chain_ready", reqId)

    # ── Contract details ──────────────────────────────────────────────────────

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        self.contract_details.setdefault(reqId, []).append(contractDetails)

    def contractDetailsEnd(self, reqId: int):
        ev = self._details_events.get(reqId)
        if ev:
            ev.set()
        self._emit("details_ready", reqId)

    def bondContractDetails(self, reqId: int, contractDetails: ContractDetails):
        pass  # not used

    # ── Market data (tick prices / sizes) ────────────────────────────────────

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        store = self.tick_data.setdefault(reqId, {})
        mapping = {
            1: "bid",
            2: "ask",
            4: "last",
            6: "high",
            7: "low",
            9: "close",
           14: "open",
        }
        key = mapping.get(tickType)
        # IBKR's documented sentinel for "no data currently available" is
        # a tickPrice of -1 OR 0 (see IBKR EWrapper docs: "A tickPrice value
        # of -1 or 0 ... indicates there is no data for this field currently
        # available"). Using price > 0 here (not >= 0) is required —
        # treating a literal 0 as a valid price caused has_price checks
        # elsewhere (snapshot_prices_and_greeks' wait loop, OptionLeg.mid/
        # has_price) to falsely report "priced" for illiquid/no-quote legs,
        # which made the wait loop exit immediately instead of waiting the
        # full timeout for a real quote, and let stray $0 "last" values
        # corrupt credit math downstream (mid() would return 0.0 from a
        # fake last=0.0 rather than correctly falling through to "no price").
        if key and price > 0:
            store[key] = price
        self._emit("tick_price", reqId, tickType, price)

    def tickSize(self, reqId: int, tickType: int, size):
        store = self.tick_data.setdefault(reqId, {})
        mapping = {0: "bid_size", 3: "ask_size", 5: "last_size", 8: "volume"}
        key = mapping.get(tickType)
        if key:
            store[key] = size

    def tickGeneric(self, reqId: int, tickType: int, value: float):
        pass  # not used at this layer

    def tickString(self, reqId: int, tickType: int, value: str):
        pass

    def tickSnapshotEnd(self, reqId: int):
        ev = self._tick_events.get(reqId)
        if ev:
            ev.set()
        self._emit("tick_snapshot_end", reqId)

    # ── Historical bars (used as RTH-closed fallback for spot price) ─────────

    def historicalData(self, reqId: int, bar):
        self.historical_bars.setdefault(reqId, []).append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        ev = self._historical_events.get(reqId)
        if ev:
            ev.set()
        self._emit("historical_data_end", reqId)

    # ── Option Greeks (generic tick 100) ─────────────────────────────────────

    def tickOptionComputation(
        self, reqId: int, tickType: int, tickAttrib: int,
        impliedVol: float, delta: float, optPrice: float,
        pvDividend: float, gamma: float, vega: float,
        theta: float, undPrice: float
    ):
        """
        tickType 10 = BID_OPTION_COMPUTATION
        tickType 11 = ASK_OPTION_COMPUTATION
        tickType 12 = LAST_OPTION_COMPUTATION
        tickType 13 = MODEL_OPTION (the authoritative/theoretical greeks)

        IBKR's docs state all four tick types return delta/gamma/vega/theta;
        MODEL_OPTION additionally returns implied vol. We prefer MODEL_OPTION
        (13) since it's the theoretical/most stable value, independent of
        whatever the current bid/ask happens to be.

        IMPORTANT: IBKR's docs also state live greeks require market data
        subscriptions for BOTH the option AND the underlying. If MODEL_OPTION
        never arrives for a given leg, this method falls back to accepting
        delta/gamma/vega/theta from BID/ASK/LAST_OPTION_COMPUTATION (10/11/12)
        instead of leaving every leg's delta at a useless default.

        SENTINEL VALUES: mirroring the same fix applied to tickPrice() — a
        literal 0.0 on delta/gamma/vega/theta is IBKR's way of saying "not
        computed (yet)" for that field on this call, not a genuine value.
        A real 0.0 delta essentially never occurs for an OTM/ATM 0DTE
        option in this strategy's strike range, so treating 0.0 as "absent"
        rather than "valid" is safe here. The earlier version of this method
        accepted abs(delta) <= 1.5 (i.e. including exactly 0.0), which let a
        placeholder 0.0 on an EARLY partial tick (e.g. tickType 10 arriving
        before the model has actually computed anything for that contract)
        get treated as a final, valid delta — explaining why only the
        first few req_ids in a large batch got real deltas and everything
        after read back exactly 0.000: those legs' FIRST tick callback
        happened to carry a placeholder 0.0 that got accepted and then never
        revisited, since later ticks for the same req_id were skipped by
        the "don't clobber an existing value" logic below.

        Every tickType received here is logged at DEBUG with the raw delta
        value, regardless of whether it's accepted — if this diagnosis is
        wrong, the debug log will show the actual raw values IBKR is
        sending instead of requiring another guess-and-check round trip.
        """
        log.debug(
            f"tickOptionComputation reqId={reqId} tickType={tickType} "
            f"delta={delta} gamma={gamma} theta={theta} vega={vega} "
            f"iv={impliedVol} optPrice={optPrice} undPrice={undPrice}"
        )

        if tickType not in (10, 11, 12, 13):
            return

        store = self.greek_data.setdefault(reqId, {})

        # MODEL_OPTION (13) is authoritative and always wins if present.
        # For 10/11/12, only fill in delta/greeks if MODEL_OPTION hasn't
        # already supplied them — avoids a later, noisier bid/ask-based
        # computation overwriting a cleaner model value that arrived first.
        is_model = (tickType == 13)
        if not is_model and "delta" in store and store.get("_source") == "model":
            return   # already have the authoritative model value; don't clobber it

        if delta     is not None and 0 < abs(delta) <= 1.5:
            store["delta"] = delta
        if gamma     is not None and gamma > 0:
            store["gamma"] = gamma
        if theta     is not None and theta != 0:
            store["theta"] = theta
        if vega      is not None and vega > 0:
            store["vega"]  = vega
        if impliedVol is not None and impliedVol > 0:
            store["iv"]    = impliedVol
        if is_model:
            store["_source"] = "model"
        elif "_source" not in store:
            store["_source"] = f"tick{tickType}"

        self._emit("greek_update", reqId, store)

    # ── Order status ──────────────────────────────────────────────────────────

    def orderStatus(
        self, orderId: int, status: str, filled: float,
        remaining: float, avgFillPrice: float, permId: int,
        parentId: int, lastFillPrice: float, clientId: int,
        whyHeld: str, mktCapPrice: float
    ):
        record = self.order_status.setdefault(orderId, {})
        record.update({
            "status":    status,
            "filled":    filled,
            "remaining": remaining,
            "avg_price": avgFillPrice,
        })
        log.info(f"Order {orderId}: {status}  "
                 f"filled={filled}  avg_px={avgFillPrice:.4f}")

        if status == "Filled":
            ev = self._order_events.get(orderId)
            if ev:
                ev.set()
        self._emit("order_status", orderId, status, filled, avgFillPrice)

    def openOrder(self, orderId: int, contract, order, orderState):
        pass  # acknowledged; orderStatus handles lifecycle

    def openOrderEnd(self):
        pass

    def execDetails(self, reqId: int, contract, execution):
        log.info(f"Exec: order={execution.orderId}  "
                 f"side={execution.side}  shares={execution.shares}  "
                 f"px={execution.price}")
        self._emit("execution", execution)

    def commissionReport(self, commissionReport):
        log.debug(f"Commission: {commissionReport.commission} "
                  f"{commissionReport.currency}")

    # ── Account / portfolio (stubs) ───────────────────────────────────────────

    def updateAccountValue(self, key, val, currency, accountName):
        pass

    def updatePortfolio(self, contract, position, marketPrice, marketValue,
                        averageCost, unrealizedPNL, realizedPNL, accountName):
        pass

    def accountDownloadEnd(self, accountName):
        pass

    def position(self, account, contract, position, avgCost):
        pass

    def positionEnd(self):
        pass
