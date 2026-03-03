"""
ibkr_client.py — IBKR Native API Connection + Data Layer
Fetches: price bars, option chains, fundamental snapshots via IBKR
"""

import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional
import pytz

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import TickerId, BarData

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, ACCOUNT

log = logging.getLogger("Screener.IBKR")
ET = pytz.timezone("America/New_York")


# ════════════════════════════════════════════════════════════════════════════
# WRAPPER — all TWS callbacks
# ════════════════════════════════════════════════════════════════════════════
class ScreenerWrapper(EWrapper):
    def __init__(self):
        super().__init__()
        self.next_order_id = 0
        self._id_event = threading.Event()

        # Keyed storage: req_id → results
        self._hist_bars: dict[int, list]       = {}   # req_id → list of bar dicts
        self._hist_done: dict[int, threading.Event] = {}

        self._fund_data: dict[int, dict]       = {}   # req_id → fundamentals dict
        self._fund_done: dict[int, threading.Event] = {}

        self._chain_data: dict[int, list]      = {}   # req_id → list of option contracts
        self._chain_done: dict[int, threading.Event] = {}

        self._tick_data: dict[int, dict]       = {}   # req_id → {bid, ask, last, volume}
        self._tick_done: dict[int, threading.Event] = {}

        self._req_symbol: dict[int, str]       = {}   # req_id → symbol tag

        self._lock = threading.Lock()

    # ── Lifecycle ────────────────────────────────────────────────────────
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self._id_event.set()
        log.info(f"Connected. NextOrderId={orderId}")

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        if errorCode in (2104, 2106, 2158, 2119, 2176):
            return  # harmless info messages
        if errorCode == 200:
            # No security definition — mark as done so we don't hang
            log.warning(f"  [{reqId}] No security definition: {errorString}")
            for done_dict in [self._hist_done, self._fund_done, self._chain_done, self._tick_done]:
                if reqId in done_dict:
                    done_dict[reqId].set()
            return
        log.debug(f"IBKR [{reqId}] {errorCode}: {errorString}")

    # ── Historical Bars ──────────────────────────────────────────────────
    def historicalData(self, reqId: int, bar: BarData):
        with self._lock:
            if reqId not in self._hist_bars:
                self._hist_bars[reqId] = []
        try:
            bar_dict = {
                "date":   bar.date.strip(),
                "open":   float(bar.open),
                "high":   float(bar.high),
                "low":    float(bar.low),
                "close":  float(bar.close),
                "volume": float(bar.volume) if bar.volume != -1 else 0.0,
            }
            with self._lock:
                self._hist_bars[reqId].append(bar_dict)
        except Exception as e:
            log.debug(f"historicalData parse error reqId={reqId}: {e}")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId in self._hist_done:
            self._hist_done[reqId].set()

    # ── Fundamental Data ─────────────────────────────────────────────────
    def fundamentalData(self, reqId: int, data: str):
        """
        IBKR returns fundamental data as an XML string.
        We parse the key fields we need for the pipeline.
        """
        with self._lock:
            self._fund_data[reqId] = {"raw_xml": data}
        try:
            parsed = _parse_fundamental_xml(data)
            with self._lock:
                self._fund_data[reqId] = parsed
        except Exception as e:
            log.debug(f"fundamentalData parse error reqId={reqId}: {e}")
        if reqId in self._fund_done:
            self._fund_done[reqId].set()

    # ── Option Chain ─────────────────────────────────────────────────────
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
                "strikes":     sorted(strikes),
                "multiplier":  multiplier,
            })

    def securityDefinitionOptionalParameterEnd(self, reqId: int):
        if reqId in self._chain_done:
            self._chain_done[reqId].set()

    # ── Snapshot ticks ───────────────────────────────────────────────────
    def tickPrice(self, reqId: TickerId, tickType, price: float, attrib):
        with self._lock:
            if reqId not in self._tick_data:
                self._tick_data[reqId] = {}
            if tickType == 1:   self._tick_data[reqId]["bid"]  = price
            elif tickType == 2: self._tick_data[reqId]["ask"]  = price
            elif tickType == 4: self._tick_data[reqId]["last"] = price
            elif tickType == 9: self._tick_data[reqId]["close"] = price

    def tickSize(self, reqId: TickerId, tickType, size: int):
        with self._lock:
            if reqId not in self._tick_data:
                self._tick_data[reqId] = {}
            if tickType == 8:
                self._tick_data[reqId]["volume"] = size

    def tickSnapshotEnd(self, reqId: int):
        if reqId in self._tick_done:
            self._tick_done[reqId].set()


# ════════════════════════════════════════════════════════════════════════════
# CLIENT — sends requests
# ════════════════════════════════════════════════════════════════════════════
class ScreenerClient(EClient):
    def __init__(self, wrapper):
        super().__init__(wrapper)


# ════════════════════════════════════════════════════════════════════════════
# IBKRDataFetcher — high-level data API used by the pipeline
# ════════════════════════════════════════════════════════════════════════════
class IBKRDataFetcher:
    """
    Thread-safe data fetcher. Each method blocks until IBKR delivers data
    or times out. Designed for sequential screener pipeline use.
    """
    _REQ_COUNTER = 3000   # start above 0DTE bot's req IDs

    def __init__(self):
        self.wrapper = ScreenerWrapper()
        self.client  = ScreenerClient(self.wrapper)
        self._connected = False
        self._req_lock = threading.Lock()

    def _next_req_id(self) -> int:
        with self._req_lock:
            IBKRDataFetcher._REQ_COUNTER += 1
            return IBKRDataFetcher._REQ_COUNTER

    def connect(self):
        self.client.connect(IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID)
        t = threading.Thread(target=self.client.run, daemon=True)
        t.start()
        ok = self.wrapper._id_event.wait(timeout=15)
        if not ok:
            raise ConnectionError("IBKR connection timed out — is TWS/Gateway running?")
        self._connected = True
        log.info(f"✅ IBKRDataFetcher connected on port {IBKR_PORT}")

    def disconnect(self):
        self.client.disconnect()
        self._connected = False

    # ── Stock contract helper ────────────────────────────────────────────
    @staticmethod
    def stock_contract(symbol: str) -> Contract:
        c = Contract()
        c.symbol   = symbol
        c.secType  = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        return c

    # ── Daily OHLCV bars (for technical analysis) ────────────────────────
    def get_daily_bars(self, symbol: str, days: int = 252,
                       timeout: float = 20.0) -> list[dict]:
        """
        Returns list of daily OHLCV dicts, newest last.
        days=252 → ~1 year of data (enough for all indicators).
        """
        req_id = self._next_req_id()
        event  = threading.Event()
        self.wrapper._hist_bars[req_id]  = []
        self.wrapper._hist_done[req_id]  = event

        duration = f"{max(1, days // 252 + 1)} Y" if days >= 252 else f"{days} D"

        self.client.reqHistoricalData(
            req_id,
            self.stock_contract(symbol),
            "",           # endDateTime: now
            duration,
            "1 day",
            "TRADES",
            1,            # useRTH
            1,            # formatDate string
            False,
            [],
        )

        done = event.wait(timeout=timeout)
        bars = self.wrapper._hist_bars.pop(req_id, [])
        self.wrapper._hist_done.pop(req_id, None)

        if not done or not bars:
            log.debug(f"  {symbol}: no daily bars (timeout={not done})")
            return []

        # Trim to requested days
        return bars[-days:] if len(bars) > days else bars

    # ── Fundamental data via IBKR ReportSnapshot ────────────────────────
    def get_fundamentals(self, symbol: str, timeout: float = 15.0) -> dict:
        """
        Fetches IBKR's 'ReportSnapshot' fundamental XML and returns parsed dict.
        Requires IBKR fundamental data subscription (included in most plans).
        """
        req_id = self._next_req_id()
        event  = threading.Event()
        self.wrapper._fund_data[req_id] = {}
        self.wrapper._fund_done[req_id] = event

        self.client.reqFundamentalData(
            req_id,
            self.stock_contract(symbol),
            "ReportSnapshot",
            [],
        )

        done = event.wait(timeout=timeout)
        data = self.wrapper._fund_data.pop(req_id, {})
        self.wrapper._fund_done.pop(req_id, None)

        if not done or not data:
            log.debug(f"  {symbol}: no fundamental data")
        return data

    # ── Option chain params ──────────────────────────────────────────────
    def get_option_chain(self, symbol: str, under_con_id: int = 0,
                         timeout: float = 15.0) -> list[dict]:
        req_id = self._next_req_id()
        event  = threading.Event()
        self.wrapper._chain_data[req_id] = []
        self.wrapper._chain_done[req_id] = event

        self.client.reqSecDefOptParams(
            req_id,
            symbol,
            "",          # futFopExchange
            "STK",
            under_con_id,
        )

        done = event.wait(timeout=timeout)
        chain = self.wrapper._chain_data.pop(req_id, [])
        self.wrapper._chain_done.pop(req_id, None)
        return chain

    # ── Snapshot quote ───────────────────────────────────────────────────
    def get_snapshot(self, symbol: str, timeout: float = 10.0) -> dict:
        req_id = self._next_req_id()
        event  = threading.Event()
        self.wrapper._tick_data[req_id] = {}
        self.wrapper._tick_done[req_id] = event

        self.client.reqMktData(
            req_id,
            self.stock_contract(symbol),
            "",
            True,    # snapshot=True
            False,
            [],
        )

        done = event.wait(timeout=timeout)
        snap = self.wrapper._tick_data.pop(req_id, {})
        self.wrapper._tick_done.pop(req_id, None)
        return snap


# ════════════════════════════════════════════════════════════════════════════
# FUNDAMENTAL XML PARSER — extracts key fields from IBKR's ReportSnapshot XML
# ════════════════════════════════════════════════════════════════════════════
def _parse_fundamental_xml(xml: str) -> dict:
    """
    Parse IBKR ReportSnapshot XML into a flat dict of key financial metrics.
    Uses stdlib xml.etree — no extra dependencies.
    """
    import xml.etree.ElementTree as ET_xml

    result = {
        "pe_ratio":         None,
        "eps_ttm":          None,
        "eps_growth_yoy":   None,
        "revenue_growth":   None,
        "profit_margin":    None,
        "debt_equity":      None,
        "market_cap_b":     None,
        "revenue_ttm":      None,
        "inst_own_pct":     None,
        "short_pct":        None,
        "dividend_yield":   None,
        "beta":             None,
        "roe":              None,
        "roa":              None,
        "sector":           None,
        "industry":         None,
    }

    try:
        root = ET_xml.fromstring(xml)

        def find_val(tag: str, attrib: str = None, text_fallback=True):
            for elem in root.iter(tag):
                if attrib and elem.get(attrib):
                    try: return float(elem.get(attrib))
                    except: return elem.get(attrib)
                if text_fallback and elem.text:
                    try: return float(elem.text.replace(",", "").replace("%", ""))
                    except: return elem.text
            return None

        result["pe_ratio"]       = find_val("Ratio", None) or find_val("PeRatio")
        result["eps_ttm"]        = find_val("EpsExclExtraItemsTTM")
        result["eps_growth_yoy"] = find_val("EPSGrowth")
        result["revenue_ttm"]    = find_val("TotalRevenueTTM")
        result["revenue_growth"] = find_val("RevenueGrowth")
        result["profit_margin"]  = find_val("NetProfitMarginTTM")
        result["debt_equity"]    = find_val("TotalDebtToEquityMRQ")
        result["inst_own_pct"]   = find_val("PercentHeldByInstitutions")
        result["short_pct"]      = find_val("PercentOfSharesOutstandingShortInterest")
        result["beta"]           = find_val("Beta")
        result["roe"]            = find_val("ReturnOnEquityTTM")
        result["roa"]            = find_val("ReturnOnAssetsTTM")
        result["dividend_yield"] = find_val("DividendYield")

        # Market cap
        mc = find_val("MarketCapitalization")
        if mc:
            result["market_cap_b"] = float(mc) / 1e9

        # Sector / Industry from IssueID
        for issue in root.iter("Issue"):
            desc = issue.find("IssueType")
            if desc is not None:
                result["sector"]   = issue.findtext("Sector") or result["sector"]
                result["industry"] = issue.findtext("Industry") or result["industry"]

    except Exception as e:
        log.debug(f"XML parse error: {e} | snippet: {xml[:200]}")

    return result
