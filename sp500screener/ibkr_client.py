"""
ibkr_client.py — IBKR Native API: connection, bar fetching, fundamentals, snapshots.

DEBUG LOGGING
─────────────
Every stage of every IBKR request is logged with a bracketed tag so you can
grep the log file for exactly the category you care about:

  [CONN]   connection lifecycle
  [PACE]   pacing waits before reqHistoricalData
  [BARS]   reqHistoricalData send / receive / result
  [ERR]    all IBKR error callbacks (every code, no silent suppression)
  [FUND]   fundamental data requests
  [CHAIN]  option chain requests
  [TICK]   snapshot tick requests

Run with:   python main.py --loglevel DEBUG
Log file:   screener.log  (set LOG_FILE in config.py)

Root causes of "no historical data" and their log signatures:
  [ERR]  code=162  → pacing violation OR IBKR has no data for this symbol/period
  [ERR]  code=200  → contract not resolved (bad symbol, wrong secType/exchange)
  [ERR]  code=321  → whatToShow invalid for this secType
  [ERR]  code=354  → no market data subscription for this exchange
  [BARS] TIMEOUT   → historicalDataEnd never arrived (TWS hung or network drop)
  [BARS] EMPTY     → historicalDataEnd fired but 0 bars (symbol has no history
                     for the requested window — e.g. very new listing)

CRITICAL FIX in this version:
  The previous ibkr_client.py had TWO complete copies of ScreenerWrapper,
  ScreenerClient, and IBKRDataFetcher (lines 91-463 and lines 565-845).
  Python silently uses the LAST class definition, so all fixes applied to
  the first copies were dead code. This file is a single clean rewrite.
"""

import threading
import time
import logging
from datetime import datetime
from typing import Optional
import pytz
import xml.etree.ElementTree as _ET

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import TickerId, BarData

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, ACCOUNT, MARKET_DATA_TYPE

log = logging.getLogger("Screener.IBKR")
ET  = pytz.timezone("America/New_York")


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

# IBKR allows max 60 historical data requests per 10 minutes.
# At 11s intervals we send ~5.4/min — comfortably inside the limit.
HIST_REQUEST_INTERVAL = 11.0
_last_hist_ts = 0.0
_pace_lock    = threading.Lock()

# Codes that mean "this request will never deliver data" — unblock immediately.
_FAIL_CODES = {
    162,    # Hist data error: pacing violation OR no data for contract/period
    200,    # No security definition found for this contract
    321,    # Invalid whatToShow for this secType
    354,    # Not subscribed to market data for this exchange
    492,    # Historical data pacing violation (separate bucket from 162)
    10089,  # Duplicate ticker ID
    10090,  # Unknown rule set / bad contract params
    10197,  # No market data permissions for this contract
}

# Purely informational status codes from TWS — suppress at INFO level.
_INFO_CODES = {2104, 2106, 2107, 2108, 2119, 2158, 2176}

# Diagnostic hints for each failure code — printed inline with the WARNING log.
_DIAG = {
    162:   ("Pacing violation OR IBKR has no historical data for this "
            "contract/period. Causes: (a) symbol not traded on a supported "
            "exchange, (b) date range has no trading days, (c) whatToShow "
            "is technically valid but IBKR has no records."),
    200:   ("Contract not found in IBKR database. Check: symbol spelling, "
            "secType=STK, currency=USD, exchange=SMART."),
    321:   ("whatToShow is invalid for this security type. "
            "STK accepts TRADES or ADJUSTED_LAST. "
            "IND/INDEX requires MIDPOINT."),
    354:   ("No market data subscription for this exchange. "
            "Fix: set MARKET_DATA_TYPE=3 (delayed) in config.py, "
            "or add the exchange subscription in TWS Account Management."),
    492:   "Pacing violation — too many historical requests per 10 min.",
    10197: "No market data permissions. Check TWS Account Management.",
}


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _pace():
    """Block until HIST_REQUEST_INTERVAL seconds have elapsed since last request."""
    global _last_hist_ts
    with _pace_lock:
        wait = HIST_REQUEST_INTERVAL - (time.monotonic() - _last_hist_ts)
        if wait > 0:
            log.debug(f"[PACE] Waiting {wait:.2f}s (interval={HIST_REQUEST_INTERVAL}s)")
            time.sleep(wait)
        _last_hist_ts = time.monotonic()
        log.debug(f"[PACE] Slot granted at "
                  f"{datetime.now(ET).strftime('%H:%M:%S.%f')[:-3]} ET")


def _days_to_duration(days: int) -> str:
    """
    Map calendar days to a valid IBKR durationStr for 1-day bar requests.
    IBKR rejects raw 'N D' for daily bars; only 'N M' and 'N Y' are accepted.
    """
    if days <= 30:  return "1 M"
    if days <= 90:  return "3 M"
    if days <= 180: return "6 M"
    if days <= 365: return "1 Y"
    if days <= 730: return "2 Y"
    return "3 Y"


# ════════════════════════════════════════════════════════════════════════════
# WRAPPER
# ════════════════════════════════════════════════════════════════════════════
class ScreenerWrapper(EWrapper):
    def __init__(self):
        super().__init__()
        self._id_event     = threading.Event()
        self.next_order_id = 0

        # Per-request dicts: reqId → payload / Event
        self._hist_bars:  dict = {}   # reqId → list[bar_dict]
        self._hist_done:  dict = {}   # reqId → threading.Event
        self._hist_meta:  dict = {}   # reqId → {symbol, wts, duration, sent_at}

        self._cd_data:    dict = {}   # reqId → list[ContractDetails]  ← contract qualify
        self._cd_done:    dict = {}   # reqId → threading.Event

        self._fund_data:  dict = {}   # reqId → parsed dict
        self._fund_done:  dict = {}

        self._chain_data: dict = {}   # reqId → list[chain_entry]
        self._chain_done: dict = {}

        self._tick_data:  dict = {}   # reqId → {bid, ask, last, close, volume}
        self._tick_done:  dict = {}

        self._lock = threading.Lock()

    # ── Connection ───────────────────────────────────────────────────────
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self._id_event.set()
        log.info(f"[CONN] nextValidId={orderId} — TWS handshake complete")

    def connectAck(self):
        log.debug("[CONN] connectAck received")

    def connectionClosed(self):
        log.warning("[CONN] connectionClosed — TWS dropped the connection!")

    # ── Contract qualification ────────────────────────────────────────────
    def contractDetails(self, reqId: int, contractDetails):
        """
        Called once per matching contract for reqContractDetails().
        For unambiguous STK contracts there is typically exactly one result.
        We collect all of them and pick the best one in qualify_contract().
        """
        with self._lock:
            if reqId not in self._cd_data:
                self._cd_data[reqId] = []
            self._cd_data[reqId].append(contractDetails)
        log.debug(f"[QUAL] contractDetails reqId={reqId} "
                  f"conId={contractDetails.contract.conId} "
                  f"sym={contractDetails.contract.symbol} "
                  f"prim={contractDetails.contract.primaryExch} "
                  f"exch={contractDetails.contract.exchange}")

    def contractDetailsEnd(self, reqId: int):
        with self._lock:
            n = len(self._cd_data.get(reqId, []))
            if reqId in self._cd_done:
                self._cd_done[reqId].set()
        log.debug(f"[QUAL] contractDetailsEnd reqId={reqId} results={n}")

    # ── Error handler — logs EVERY code; none are silently swallowed ─────
    def error(self, reqId: TickerId, *args):
        """
        Handle both ibapi signatures:
          Pre-10.33:  error(reqId, errorCode, errorString, advancedOrderRejectJson="")
          Post-10.33: error(reqId, errorTime, errorCode, errorString, advancedOrderRejectJson="")

        The errorTime field (Unix ms timestamp) was added in TWS API 10.33.
        Without this guard, errorTime lands in errorCode and you get a giant
        integer like 1772671564982 instead of the real code (e.g. 504).
        """
        # Detect which signature fired based on arg count + type of first arg
        # errorTime is a large int (Unix ms); errorCode is always < 100000
        if len(args) >= 2 and isinstance(args[0], int) and args[0] > 1_000_000_000:
            # Post-10.33: args = (errorTime, errorCode, errorString, [advJson])
            _errorTime  = args[0]
            errorCode   = int(args[1])
            errorString = str(args[2]) if len(args) > 2 else ""
        else:
            # Pre-10.33: args = (errorCode, errorString, [advJson])
            errorCode   = int(args[0]) if args else -1
            errorString = str(args[1]) if len(args) > 1 else ""

        if errorCode in _INFO_CODES:
            log.debug(f"[ERR ] INFO code={errorCode} reqId={reqId}: {errorString}")
            return

        diag     = _DIAG.get(errorCode, "")
        diag_str = f"\n         DIAG: {diag}" if diag else ""

        if errorCode in _FAIL_CODES:
            log.warning(
                f"[ERR ] FAIL reqId={reqId} code={errorCode}: "
                f"{errorString}{diag_str}"
            )
            with self._lock:
                for d in (self._hist_done, self._fund_done,
                          self._chain_done, self._tick_done, self._cd_done):
                    if reqId in d:
                        log.debug(f"[ERR ] Unblocking waiter for reqId={reqId}")
                        d[reqId].set()
            return

        # Catch-all: every unrecognised code is visible
        log.warning(f"[ERR ] code={errorCode} reqId={reqId}: {errorString}")

    # ── Historical bars ──────────────────────────────────────────────────
    def historicalData(self, reqId: int, bar: BarData):
        with self._lock:
            if reqId not in self._hist_bars:
                log.warning(f"[BARS] historicalData for unknown reqId={reqId} "
                            f"— creating bucket (race condition?)")
                self._hist_bars[reqId] = []
            idx = len(self._hist_bars[reqId])

        try:
            bar_dict = {
                "date":   bar.date.strip(),
                "open":   float(bar.open),
                "high":   float(bar.high),
                "low":    float(bar.low),
                "close":  float(bar.close),
                "volume": float(bar.volume) if bar.volume not in (-1, 0) else 0.0,
            }
            with self._lock:
                self._hist_bars[reqId].append(bar_dict)

            if idx == 0:
                log.debug(f"[BARS] reqId={reqId} FIRST bar: "
                          f"date={bar_dict['date']} "
                          f"O={bar_dict['open']} H={bar_dict['high']} "
                          f"L={bar_dict['low']} C={bar_dict['close']} "
                          f"V={bar_dict['volume']}")
            elif idx % 50 == 0:
                log.debug(f"[BARS] reqId={reqId} bar #{idx}: "
                          f"date={bar_dict['date']} C={bar_dict['close']}")

        except Exception as exc:
            log.error(f"[BARS] Parse error reqId={reqId}: {exc} | "
                      f"raw: date='{bar.date}' o={bar.open} h={bar.high} "
                      f"l={bar.low} c={bar.close} v={bar.volume}")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        with self._lock:
            n    = len(self._hist_bars.get(reqId, []))
            meta = self._hist_meta.get(reqId, {})

        sym     = meta.get("symbol",   "?")
        wts     = meta.get("wts",      "?")
        dur     = meta.get("duration", "?")
        sent_at = meta.get("sent_at",  0.0)
        elapsed = time.monotonic() - sent_at if sent_at else 0.0

        if n > 0:
            log.info(
                f"[BARS] ✅ historicalDataEnd reqId={reqId} sym={sym} "
                f"bars={n} range={start}→{end} "
                f"[{wts}/{dur}] elapsed={elapsed:.2f}s"
            )
        else:
            log.warning(
                f"[BARS] ⚠ historicalDataEnd reqId={reqId} sym={sym} "
                f"bars=0 range={start}→{end} [{wts}/{dur}] elapsed={elapsed:.2f}s\n"
                f"         DIAG: IBKR accepted the request but returned zero bars.\n"
                f"         Possible causes:\n"
                f"           (1) Symbol has no trade history in this date window\n"
                f"               (recently listed, suspended, or delisted).\n"
                f"           (2) Date range falls entirely on market holidays.\n"
                f"           (3) SMART routing resolved to an exchange with no data\n"
                f"               — try setting primaryExch='NYSE' or 'NASDAQ'."
            )

        with self._lock:
            if reqId in self._hist_done:
                self._hist_done[reqId].set()

    # ── Fundamental data ─────────────────────────────────────────────────
    def fundamentalData(self, reqId: int, data: str):
        log.debug(f"[FUND] reqId={reqId} received {len(data)} chars of XML")
        parsed = _parse_fundamental_xml(data)
        with self._lock:
            self._fund_data[reqId] = parsed
            if reqId in self._fund_done:
                self._fund_done[reqId].set()
        log.debug(f"[FUND] reqId={reqId} parsed: "
                  f"pe={parsed.get('pe_ratio')} "
                  f"mktcap={parsed.get('market_cap_b')}B "
                  f"sector='{parsed.get('sector')}'")

    # ── Option chain ─────────────────────────────────────────────────────
    def securityDefinitionOptionalParameter(
        self, reqId: int, exchange: str, underlyingConId: int,
        tradingClass: str, multiplier: str, expirations: set, strikes: set
    ):
        with self._lock:
            if reqId not in self._chain_data:
                self._chain_data[reqId] = []
            self._chain_data[reqId].append({
                "exchange":    exchange,
                "underConId":  underlyingConId,
                "expirations": sorted(expirations),
                "strikes":     sorted(float(s) for s in strikes),
                "multiplier":  multiplier,
            })
        log.debug(f"[CHAIN] reqId={reqId} exch={exchange} "
                  f"exp={len(expirations)} strikes={len(strikes)}")

    def securityDefinitionOptionalParameterEnd(self, reqId: int):
        with self._lock:
            n = len(self._chain_data.get(reqId, []))
            if reqId in self._chain_done:
                self._chain_done[reqId].set()
        log.debug(f"[CHAIN] End reqId={reqId} total_entries={n}")

    # ── Snapshot ticks ───────────────────────────────────────────────────
    def tickPrice(self, reqId: TickerId, tickType, price: float, attrib):
        if price <= 0:
            return
        # Live: 1=bid 2=ask 4=last 9=close | Delayed: 66=bid 67=ask 68=last 75=close
        TICK_MAP = {1:"bid", 2:"ask", 4:"last", 9:"close",
                    66:"bid", 67:"ask", 68:"last", 75:"close"}
        key = TICK_MAP.get(tickType)
        if not key:
            return
        with self._lock:
            if reqId not in self._tick_data:
                self._tick_data[reqId] = {}
            self._tick_data[reqId][key] = price
            log.debug(f"[TICK] reqId={reqId} tickType={tickType}({key}) "
                      f"price={price:.4f} delayed={tickType >= 66}")
            if key in ("last", "close") and reqId in self._tick_done:
                self._tick_done[reqId].set()

    def tickSize(self, reqId: TickerId, tickType, size: int):
        if tickType not in (8, 74):   # 8=live volume, 74=delayed volume
            return
        with self._lock:
            if reqId not in self._tick_data:
                self._tick_data[reqId] = {}
            self._tick_data[reqId]["volume"] = size

    def tickSnapshotEnd(self, reqId: int):
        with self._lock:
            if reqId in self._tick_done:
                self._tick_done[reqId].set()
        log.debug(f"[TICK] snapshotEnd reqId={reqId}")


# ════════════════════════════════════════════════════════════════════════════
# CLIENT
# ════════════════════════════════════════════════════════════════════════════
class ScreenerClient(EClient):
    def __init__(self, wrapper):
        super().__init__(wrapper)


# ════════════════════════════════════════════════════════════════════════════
# IBKRDataFetcher
# ════════════════════════════════════════════════════════════════════════════
class IBKRDataFetcher:
    _REQ_COUNTER = 3000   # above 0DTE bot IDs

    def __init__(self):
        self.wrapper         = ScreenerWrapper()
        self.client          = ScreenerClient(self.wrapper)
        self._connected      = False
        self._req_lock       = threading.Lock()
        self._contract_cache: dict = {}   # symbol → fully-qualified Contract

    def _next_req_id(self) -> int:
        with self._req_lock:
            IBKRDataFetcher._REQ_COUNTER += 1
            rid = IBKRDataFetcher._REQ_COUNTER
        log.debug(f"[REQID] allocated={rid}")
        return rid

    # ── Lifecycle ─────────────────────────────────────────────────────────
    def connect(self):
        log.info(f"[CONN] Connecting → {IBKR_HOST}:{IBKR_PORT} "
                 f"clientId={IBKR_CLIENT_ID}")
        self.client.connect(IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID)
        t = threading.Thread(target=self.client.run, daemon=True, name="IBKRReader")
        t.start()
        log.debug("[CONN] Reader thread started — awaiting nextValidId …")

        ok = self.wrapper._id_event.wait(timeout=15)
        if not ok:
            raise ConnectionError(
                f"[CONN] Timed out waiting for nextValidId (15s).\n"
                f"  Checklist: TWS/Gateway running on port {IBKR_PORT}? "
                f"API enabled in TWS settings? IP {IBKR_HOST} in trusted list?"
            )

        mode_label = {1:"Live", 2:"Frozen", 3:"Delayed", 4:"Delayed-Frozen"}
        self.client.reqMarketDataType(MARKET_DATA_TYPE)
        log.info(
            f"[CONN] ✅ Connected | port={IBKR_PORT} | "
            f"marketDataType={MARKET_DATA_TYPE} "
            f"({mode_label.get(MARKET_DATA_TYPE, 'Unknown')})"
        )
        log.info(
            "[CONN] NOTE: reqMarketDataType affects live/snapshot ticks ONLY. "
            "Historical bar requests use a separate IBKR data path and are "
            "unaffected by the market data type setting."
        )
        self._connected = True

    def disconnect(self):
        log.info("[CONN] Disconnecting from IBKR")
        self.client.disconnect()
        self._connected = False

    # ── Contract factory ──────────────────────────────────────────────────
    @staticmethod
    def stock_contract(symbol: str) -> Contract:
        """
        Bare unqualified contract — kept for compatibility but prefer
        qualify_contract() + _get_contract() for any data requests.
        """
        c = Contract()
        c.symbol   = symbol
        c.secType  = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        return c

    @staticmethod
    def _bare_contract(symbol: str) -> Contract:
        """
        Build a Contract suitable for reqContractDetails lookup.

        Space-suffixed symbols (e.g. "BRK B", "BF B") represent multi-class
        shares where the IBKR localSymbol contains a space.  Setting
        contract.symbol = "BRK B" causes reqContractDetails to return 0 results
        because IBKR looks up contract.symbol as the root ticker.

        Correct form:
          contract.symbol      = "BRK"   ← root ticker (no class suffix)
          contract.localSymbol = "BRK B" ← full IBKR local symbol with class
          contract.secType     = "STK"
          contract.currency    = "USD"
          contract.exchange    = "SMART"

        For all other symbols, symbol == localSymbol (both set to the ticker).
        """
        c = Contract()
        c.secType  = "STK"
        c.currency = "USD"
        c.exchange = "SMART"

        if " " in symbol:
            # e.g. "BRK B" → symbol="BRK", localSymbol="BRK B"
            c.symbol      = symbol.split()[0]
            c.localSymbol = symbol
        else:
            c.symbol = symbol

        return c

    def qualify_contract(self, symbol: str, timeout: float = 10.0) -> Optional[Contract]:
        """
        Resolve a ticker symbol to a fully-qualified IBKR Contract with
        conId and primaryExch populated, then cache it for reuse.

        Why symbol+SMART is not enough
        ────────────────────────────────
        reqHistoricalData with only symbol + exchange="SMART" triggers error 200
        ("No security definition") for a large fraction of S&P 500 names because
        SMART is a routing overlay, not a listing venue. IBKR needs either:
          (a) conId populated — uniquely identifies the contract, no ambiguity
          (b) primaryExch set (e.g. "NASDAQ", "NYSE") alongside exchange="SMART"

        reqContractDetails resolves the ambiguity: IBKR matches the symbol and
        returns the full contract including conId and primaryExch. We use that
        qualified contract for all subsequent reqHistoricalData calls, which
        eliminates error 200 entirely.

        reqContractDetails is NOT subject to the 60/10min historical data
        pacing limit — it is a separate API call with its own (much higher) quota,
        so qualifying all 500 S&P stocks upfront is safe.
        """
        if symbol in self._contract_cache:
            cached = self._contract_cache[symbol]
            log.debug(f"[QUAL] {symbol}: cache hit — "
                      f"conId={cached.conId} primaryExch={cached.primaryExch}")
            return cached

        req_id = self._next_req_id()
        event  = threading.Event()
        with self.wrapper._lock:
            self.wrapper._cd_data[req_id] = []
            self.wrapper._cd_done[req_id] = event

        log.debug(f"[QUAL] → reqContractDetails reqId={req_id} sym={symbol}")
        self.client.reqContractDetails(req_id, self._bare_contract(symbol))

        signalled = event.wait(timeout=timeout)

        with self.wrapper._lock:
            details_list = self.wrapper._cd_data.pop(req_id, [])
            self.wrapper._cd_done.pop(req_id, None)

        if not signalled or not details_list:
            log.warning(
                f"[QUAL] ✗ {symbol}: no contract details returned "
                f"(signalled={signalled} count={len(details_list)})\n"
                f"         Symbol may be delisted, mis-spelled, or not "
                f"tradeable on your account tier. Falling back to bare contract."
            )
            return None

        # Multiple results can come back for dual-listed stocks — prefer primary US exchanges
        PREFERRED = {"NASDAQ", "NYSE", "ARCA", "BATS", "IEX"}
        best = None
        for cd in details_list:
            if cd.contract.primaryExch in PREFERRED:
                best = cd.contract
                break
        if best is None:
            best = details_list[0].contract   # first result as fallback

        log.info(
            f"[QUAL] ✅ {symbol} qualified → "
            f"conId={best.conId} "
            f"primaryExch={best.primaryExch} "
            f"tradingClass={best.tradingClass} "
            f"(from {len(details_list)} result(s))"
        )
        self._contract_cache[symbol] = best
        return best

    def _get_contract(self, symbol: str) -> Contract:
        """Return the cached qualified contract, or bare contract as last resort."""
        return self._contract_cache.get(symbol) or self._bare_contract(symbol)

    # ── Daily OHLCV bars ──────────────────────────────────────────────────
    def get_daily_bars(self, symbol: str, days: int = 252,
                       timeout: float = 30.0,
                       max_retries: int = 2) -> list:
        """
        Fetch `days` of daily OHLCV bars for `symbol`.
        Returns list of bar dicts (oldest first), or [] on failure.

        whatToShow waterfall:
          1. TRADES        — standard OHLCV + volume; works for US stocks
          2. ADJUSTED_LAST — split/div-adjusted close; fallback for ETFs

        All steps logged with [BARS]/[PACE]/[ERR] tags.
        Use --loglevel DEBUG for full trace.
        """
        duration = _days_to_duration(days)
        log.info(f"[BARS] {symbol}: fetch start — days={days} duration='{duration}'")

        # Qualify the contract first so reqHistoricalData gets a conId + primaryExch.
        # Without this, symbol+SMART alone causes error 200 for many S&P 500 names.
        contract = self._get_contract(symbol)
        if not contract.conId:
            contract = self.qualify_contract(symbol) or contract
        log.debug(f"[BARS] {symbol}: using contract conId={contract.conId} "
                  f"primaryExch={getattr(contract, 'primaryExch', '(unset)')} "
                  f"exch={contract.exchange}")

        for wts in ("TRADES", "ADJUSTED_LAST"):
            for attempt in range(1, max_retries + 1):
                req_id  = self._next_req_id()
                event   = threading.Event()

                # Register storage BEFORE sending the request.
                # If we register after, historicalDataEnd can fire into a missing
                # key and the waiting thread blocks forever.
                with self.wrapper._lock:
                    self.wrapper._hist_bars[req_id] = []
                    self.wrapper._hist_done[req_id] = event
                    self.wrapper._hist_meta[req_id] = {
                        "symbol":   symbol,
                        "wts":      wts,
                        "duration": duration,
                        "sent_at":  0.0,
                    }

                # Enforce pacing before sending
                _pace()

                sent_at = time.monotonic()
                with self.wrapper._lock:
                    self.wrapper._hist_meta[req_id]["sent_at"] = sent_at

                log.info(
                    f"[BARS] → reqHistoricalData reqId={req_id} sym={symbol} "
                    f"duration='{duration}' barSize='1 day' "
                    f"whatToShow={wts} useRTH=1 "
                    f"attempt={attempt}/{max_retries}"
                )

                self.client.reqHistoricalData(
                    req_id,
                    contract,   # fully qualified: conId + primaryExch already set
                    "",         # endDateTime empty = right now
                    duration,
                    "1 day",    # lowercase + single space — required by IBKR API
                    wts,
                    1,          # useRTH=1
                    1,          # formatDate=1 → "YYYYMMDD" string dates
                    False,      # keepUpToDate=False
                    [],
                )

                log.debug(f"[BARS] reqId={req_id} waiting up to {timeout}s …")
                signalled = event.wait(timeout=timeout)
                elapsed   = time.monotonic() - sent_at

                with self.wrapper._lock:
                    bars = self.wrapper._hist_bars.pop(req_id, [])
                    self.wrapper._hist_done.pop(req_id, None)
                    self.wrapper._hist_meta.pop(req_id, None)

                if not signalled:
                    log.warning(
                        f"[BARS] ⏱ TIMEOUT reqId={req_id} sym={symbol} [{wts}] "
                        f"after {elapsed:.1f}s — historicalDataEnd never arrived.\n"
                        f"         DIAG: (1) TWS may be frozen/overloaded, "
                        f"(2) network issue, (3) reqId={req_id} collision. "
                        f"bars buffered so far: {len(bars)}"
                    )
                elif not bars:
                    # Empty response — historicalDataEnd already logged the DIAG
                    log.warning(
                        f"[BARS] ⚠ EMPTY reqId={req_id} sym={symbol} "
                        f"[{wts}] in {elapsed:.1f}s"
                    )
                else:
                    log.info(
                        f"[BARS] ✅ {symbol} {len(bars)} bars [{wts}] "
                        f"in {elapsed:.1f}s | "
                        f"first={bars[0]['date']} last={bars[-1]['date']}"
                    )
                    return bars[-days:] if len(bars) > days else bars

                if attempt < max_retries:
                    backoff = 30 * attempt
                    log.info(f"[BARS] {symbol} back-off {backoff}s "
                             f"(attempt {attempt}/{max_retries})")
                    time.sleep(backoff)

            log.debug(f"[BARS] {symbol}: '{wts}' exhausted — trying next whatToShow")

        log.warning(f"[BARS] ✗ {symbol}: all options exhausted — returning []")
        return []

    # ── Fundamental data ──────────────────────────────────────────────────
    def get_fundamentals(self, symbol: str, timeout: float = 15.0) -> dict:
        req_id = self._next_req_id()
        event  = threading.Event()
        with self.wrapper._lock:
            self.wrapper._fund_data[req_id] = {}
            self.wrapper._fund_done[req_id] = event

        log.debug(f"[FUND] → reqFundamentalData reqId={req_id} sym={symbol}")
        sent_at = time.monotonic()
        self.client.reqFundamentalData(
            req_id, self._get_contract(symbol), "ReportSnapshot", []
        )

        done    = event.wait(timeout=timeout)
        elapsed = time.monotonic() - sent_at
        with self.wrapper._lock:
            data = self.wrapper._fund_data.pop(req_id, {})
            self.wrapper._fund_done.pop(req_id, None)

        if not done:
            log.warning(f"[FUND] ⏱ TIMEOUT reqId={req_id} sym={symbol} "
                        f"after {elapsed:.1f}s")
        elif not data:
            log.warning(f"[FUND] ⚠ EMPTY reqId={req_id} sym={symbol} "
                        f"in {elapsed:.1f}s")
        else:
            log.debug(f"[FUND] ✅ {symbol} in {elapsed:.1f}s")
        return data

    # ── Option chain ──────────────────────────────────────────────────────
    def get_option_chain(self, symbol: str, under_con_id: int = 0,
                         timeout: float = 15.0) -> list:
        req_id = self._next_req_id()
        event  = threading.Event()
        with self.wrapper._lock:
            self.wrapper._chain_data[req_id] = []
            self.wrapper._chain_done[req_id] = event

        log.debug(f"[CHAIN] → reqSecDefOptParams reqId={req_id} sym={symbol}")
        sent_at  = time.monotonic()
        # reqSecDefOptParams takes symbol + underlyingConId (not a Contract object).
        # Use the conId from the qualified contract if available — it's more
        # reliable than passing 0 which forces IBKR to resolve by symbol.
        cached   = self._contract_cache.get(symbol)
        con_id   = cached.conId if cached and cached.conId else under_con_id
        self.client.reqSecDefOptParams(req_id, symbol, "", "STK", con_id)

        done    = event.wait(timeout=timeout)
        elapsed = time.monotonic() - sent_at
        with self.wrapper._lock:
            chain = self.wrapper._chain_data.pop(req_id, [])
            self.wrapper._chain_done.pop(req_id, None)

        if not done:
            log.warning(f"[CHAIN] ⏱ TIMEOUT reqId={req_id} sym={symbol} "
                        f"after {elapsed:.1f}s")
        else:
            log.debug(f"[CHAIN] ✅ {symbol} {len(chain)} entries "
                      f"in {elapsed:.1f}s")
        return chain

    # ── Snapshot quote ────────────────────────────────────────────────────
    def get_snapshot(self, symbol: str, timeout: float = 12.0) -> dict:
        """
        snapshot=False + cancelMktData() pattern.
        With delayed market data (type 3/4), snapshot=True is unreliable —
        TWS often ignores it. We subscribe, wait for the first usable price
        tick (which fires the event), then immediately cancel.
        """
        req_id = self._next_req_id()
        event  = threading.Event()
        with self.wrapper._lock:
            self.wrapper._tick_data[req_id] = {}
            self.wrapper._tick_done[req_id] = event

        log.debug(f"[TICK] → reqMktData reqId={req_id} sym={symbol} snapshot=False")
        sent_at = time.monotonic()
        self.client.reqMktData(
            req_id, self._get_contract(symbol), "", False, False, []
        )

        event.wait(timeout=timeout)
        self.client.cancelMktData(req_id)
        elapsed = time.monotonic() - sent_at

        with self.wrapper._lock:
            snap = self.wrapper._tick_data.pop(req_id, {})
            self.wrapper._tick_done.pop(req_id, None)

        log.debug(f"[TICK] {symbol} snap={snap} elapsed={elapsed:.1f}s")
        return snap


# ════════════════════════════════════════════════════════════════════════════
# FUNDAMENTAL XML PARSER
# ════════════════════════════════════════════════════════════════════════════
def _parse_fundamental_xml(xml_str: str) -> dict:
    """Parse IBKR ReportSnapshot XML into a flat metrics dict."""
    result = {
        "pe_ratio": None, "eps_ttm": None, "eps_growth_yoy": None,
        "revenue_growth": None, "profit_margin": None, "debt_equity": None,
        "market_cap_b": None, "revenue_ttm": None, "inst_own_pct": None,
        "short_pct": None, "dividend_yield": None, "beta": None,
        "roe": None, "roa": None, "sector": None, "industry": None,
    }
    try:
        root = _ET.fromstring(xml_str)

        RATIO_MAP = {
            "PEEXCLXOR":    "pe_ratio",
            "EPSEXCLXOR":   "eps_ttm",
            "EPSGROWTH":    "eps_growth_yoy",
            "REVENUEGROWTH":"revenue_growth",
            "NETMARGIN":    "profit_margin",
            "TOTALDEBT_EQ": "debt_equity",
            "MKTCAP":       "market_cap_b",   # $thousands → /1e9
            "TTMREVPS":     "revenue_ttm",
            "INSTNHOLDERSP":"inst_own_pct",
            "SHORTINTDAYSTOC":"short_pct",
            "DIVYIELD":     "dividend_yield",
            "BETA":         "beta",
            "TTMROEPCT":    "roe",
            "TTMROAPCT":    "roa",
        }
        for el in root.iter("Ratio"):
            key = RATIO_MAP.get(el.get("FieldName", ""))
            if key and el.text:
                try:
                    v = float(el.text.replace(",", "").strip())
                    result[key] = v / 1e9 if key == "market_cap_b" else v
                except ValueError:
                    pass

        for co in root.iter("CoID"):
            t = co.get("Type", "")
            if t == "IndustryInfo":
                result["industry"] = (co.text or "").strip() or result["industry"]
            elif t == "Sector":
                result["sector"]   = (co.text or "").strip() or result["sector"]

        if not result["sector"]:
            el = root.find(".//GICS[@Name]")
            if el is not None:
                result["sector"] = el.get("Name")

    except Exception as exc:
        log.debug(f"[FUND] XML parse error: {exc} | "
                  f"first 300 chars: {xml_str[:300]}")
    return result
