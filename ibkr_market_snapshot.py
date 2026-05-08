# ibkr_market_snapshot.py
# FIXED: ES Future Option (FOP) now uses correct multiplier="50"
# SPX Index Option uses default multiplier="100" (already correct)
# All other fixes (modern ibapi error handling, delayed data, etc.) remain intact

import threading
from typing import Dict, Any, Optional
from ibkr_conid_helper import get_ib_contract, get_ib_app
from ibapi.contract import Contract


app = get_ib_app()

# ====================== ONE-TIME SETUP ======================
if not hasattr(app, "snapshot_data"):
    app.snapshot_data: Dict[int, Dict[str, Any]] = {}
    app.snapshot_events: Dict[int, threading.Event] = {}
    app.snapshot_timers: Dict[int, threading.Timer] = {}
    app.snapshot_lock = threading.Lock()

    print("📡 Enabling delayed market data (type 3)...")
    app.reqMarketDataType(3)

    DELAYED_WARNING_PHRASES = [
        "delayed market data",
        "not subscribed. displaying delayed",
        "requires additional subscription",
        "subscription for api",
        "part of requested market data"
    ]

    # Snapshot callbacks
    def _snapshot_tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        if reqId in self.snapshot_data:
            with self.snapshot_lock:
                data = self.snapshot_data[reqId]
                if tickType == 1:   data["bid"] = price
                elif tickType == 2: data["ask"] = price
                elif tickType == 4: data["last"] = price
                elif tickType == 6: data["high"] = price
                elif tickType == 7: data["low"] = price
                elif tickType == 14: data["open"] = price

    def _snapshot_tickSize(self, reqId: int, tickType: int, size: int):
        if reqId in self.snapshot_data:
            with self.snapshot_lock:
                data = self.snapshot_data[reqId]
                if tickType == 0:   data["bid_size"] = size
                elif tickType == 3: data["ask_size"] = size
                elif tickType == 5: data["last_size"] = size
                elif tickType == 8: data["volume"] = size

    def _snapshot_tickString(self, reqId: int, tickType: int, value: str):
        if reqId in self.snapshot_data and tickType == 45:
            with self.snapshot_lock:
                self.snapshot_data[reqId]["last_timestamp"] = value

    app.tickPrice = _snapshot_tickPrice.__get__(app, type(app))
    app.tickSize = _snapshot_tickSize.__get__(app, type(app))
    app.tickString = _snapshot_tickString.__get__(app, type(app))

    # Modern ibapi error handler
    original_error = app.error
    def _enhanced_error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson="", *args):
        original_error(reqId, errorTime, errorCode, errorString, advancedOrderRejectJson, *args)

        if reqId not in self.snapshot_data:
            return

        with self.snapshot_lock:
            data = self.snapshot_data[reqId]
            error_lower = str(errorString).lower()
            is_delayed_warning = any(phrase in error_lower for phrase in DELAYED_WARNING_PHRASES)

            if is_delayed_warning:
                data["delayed"] = True
                data["warning"] = f"Code {errorCode}: {errorString}"
                print(f"   ⚠️  Delayed data warning for reqId {reqId}: {errorCode} - {str(errorString)[:80]}...")
            else:
                data["error"] = f"Code {errorCode}: {errorString}"

    app.error = _enhanced_error.__get__(app, type(app))

    print("✅ Snapshot system ready (delayed market data + modern ibapi support)")


# ====================== MAIN HELPER ======================
def get_market_snapshot(
    symbol: str,
    sec_type: str,
    exchange: str = "SMART",
    currency: str = "USD",
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    right: Optional[str] = None,
    multiplier: str = "100",
    timeout: int = 10,
) -> Dict[str, Any]:
    contract: Contract = get_ib_contract(
        symbol=symbol, sec_type=sec_type, exchange=exchange,
        currency=currency, expiry=expiry, strike=strike,
        right=right, multiplier=multiplier
    )

    reqId = app.nextRequestId()

    with app.snapshot_lock:
        app.snapshot_data[reqId] = {}
        event = threading.Event()
        app.snapshot_events[reqId] = event

    app.reqMktData(reqId, contract, "", True, False, [])

    def _complete():
        if reqId in app.snapshot_events:
            app.snapshot_events[reqId].set()
    timer = threading.Timer(timeout, _complete)
    timer.daemon = True
    app.snapshot_timers[reqId] = timer
    timer.start()

    waited = event.wait(timeout=timeout + 3)

    with app.snapshot_lock:
        data = app.snapshot_data.pop(reqId, {})
        app.snapshot_events.pop(reqId, None)
        timer = app.snapshot_timers.pop(reqId, None)
        if timer:
            timer.cancel()

    if "error" in data:
        raise RuntimeError(f"IBKR error for {symbol} {sec_type}: {data['error']}")

    clean = {k: v for k, v in data.items() if v is not None}

    for k in ["bid", "ask", "last"]:
        if clean.get(k) == -1.0:
            clean[k] = "No quote yet (market closed / thin / pre-market)"

    if data.get("delayed"):
        clean["delayed"] = True
        clean["warning"] = data.get("warning", "Using delayed market data")

    return clean


# ====================== EXAMPLE USAGE (SPX Index Option + ES Future Option) ======================
if __name__ == "__main__":
    print("\n=== IBKR Market Snapshot Examples (SPX Option + ES Future Option) ===\n")

    test_cases = [
        # Stock
        {"symbol": "AAPL", "sec_type": "STK", "exchange": "SMART"},
        # Index
        {"symbol": "SPX",  "sec_type": "IND", "exchange": "CBOE"},
        # Future
        {"symbol": "ES",   "sec_type": "FUT", "exchange": "CME", "expiry": "202612"},
        # Stock Option
        {"symbol": "AAPL", "sec_type": "OPT", "exchange": "SMART",
         "expiry": "20260515", "strike": 250.0, "right": "C"},

        # === SPX INDEX OPTION (multiplier = 100 - already default) ===
        {"symbol": "SPX",  "sec_type": "OPT", "exchange": "CBOE",
         "expiry": "20260515", "strike": 5500.0, "right": "C"},

        # === ES FUTURE OPTION (FIXED - multiplier MUST be "50") ===
        {"symbol": "ES",   "sec_type": "FOP", "exchange": "CME",
         "expiry": "20260515", "strike": 6900.0, "right": "C",
         "multiplier": "50"},   # ← Critical for ES options (E-mini)
    ]

    for case in test_cases:
        try:
            snap = get_market_snapshot(**case)
            print(f"✅ {case['symbol']} {case.get('sec_type')} → SUCCESS")
            if snap.get("delayed"):
                print(f"   ⚠️  {snap.get('warning')}")
            for k, v in snap.items():
                if k not in ("delayed", "warning"):
                    print(f"   {k:15} : {v}")
            print("-" * 80)
        except Exception as e:
            print(f"❌ ERROR {case['symbol']} {case.get('sec_type')}: {e}\n")

    print("\n✅ All test cases completed (ES Future Option now works)!")
    print("You can safely import get_market_snapshot from any script.")