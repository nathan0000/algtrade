# ibkr_conid_helper.py
# FULL, COMPLETE, PRODUCTION-READY VERSION (April 2026)
# Features:
#   • Loads settings from config.py (optional, safe defaults)
#   • Thread-safe singleton IBKR connection
#   • Supports STK, IND, FUT, OPT, and FOP (Futures Option)
#   • Automatic tradingClass="ES" for FOP → eliminates ambiguous contract errors
#   • Corrected ES FOP example (uses futures contract month "202606" — this is the standard format that works for FOP on CME)

import threading
from typing import Optional
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ContractDetails
from ibapi.common import TickerId

# ====================== LOAD CONFIG (optional) ======================
try:
    from config import IB_HOST, IB_PORT, IB_CLIENT_ID, IB_TIMEOUT
except (ImportError, AttributeError):
    # Safe defaults if config.py is missing
    IB_HOST = "127.0.0.1"
    IB_PORT = 7497
    IB_CLIENT_ID = 999
    IB_TIMEOUT = 15

print(f"📡 IBKR Config loaded → Host: {IB_HOST}, Port: {IB_PORT}, ClientID: {IB_CLIENT_ID}, Timeout: {IB_TIMEOUT}s")


class IBKRApp(EWrapper, EClient):
    """Singleton-friendly IBKR connection manager with thread-safe contract details handling."""

    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, self)

        # Request tracking
        self.next_req_id_counter = 10000
        self.next_req_id_lock = threading.Lock()

        # Per-request storage
        self.contract_details_dict: dict[int, list] = {}
        self.contract_details_event: dict[int, threading.Event] = {}
        self.request_errors: dict[int, list[str]] = {}

        # Connection state
        self.connected_event = threading.Event()
        self.connection_lock = threading.Lock()
        self.is_connected = False
        self.connection_thread: Optional[threading.Thread] = None

    def nextRequestId(self) -> int:
        """Thread-safe unique reqId generator."""
        with self.next_req_id_lock:
            self.next_req_id_counter += 1
            return self.next_req_id_counter

    # ==================== IBKR CALLBACKS ====================
    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson: str = "", arg5: str = ""):
        super().error(reqId, errorCode, errorString, advancedOrderRejectJson)
        if reqId > 0:
            if reqId not in self.request_errors:
                self.request_errors[reqId] = []
            self.request_errors[reqId].append(f"Code {errorCode}: {errorString} {advancedOrderRejectJson or ''}")

    def contractDetails(self, reqId: int, contractDetails: "ContractDetails"):
        if reqId not in self.contract_details_dict:
            self.contract_details_dict[reqId] = []
        self.contract_details_dict[reqId].append(contractDetails)

    def contractDetailsEnd(self, reqId: int):
        if reqId in self.contract_details_event:
            self.contract_details_event[reqId].set()

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.connected_event.set()

    def connectionClosed(self):
        self.is_connected = False
        self.connected_event.clear()

    # ==================== CONNECTION MANAGEMENT ====================
    def ensure_connected(self, host: str = None, port: int = None, clientId: int = None) -> bool:
        host = host or IB_HOST
        port = port or IB_PORT
        clientId = clientId or IB_CLIENT_ID

        with self.connection_lock:
            if self.is_connected and self.isConnected():
                return True

            if self.connection_thread and self.connection_thread.is_alive():
                self.disconnect()
                threading.Event().wait(0.5)

            self.connect(host, port, clientId)

            if not self.connection_thread or not self.connection_thread.is_alive():
                self.connection_thread = threading.Thread(target=self.run, daemon=True)
                self.connection_thread.start()

            if not self.connected_event.wait(timeout=15):
                self.disconnect()
                raise ConnectionError(
                    f"Failed to connect to TWS/IB Gateway at {host}:{port} (clientId={clientId}). "
                    "Is TWS/Gateway running? Is API enabled?"
                )

            self.is_connected = True
            return True


# ====================== MODULE-LEVEL SINGLETON ======================
_ib_app: Optional[IBKRApp] = None
_app_lock = threading.Lock()


def get_ib_app(host: str = None, port: int = None, client_id: int = None) -> IBKRApp:
    """Returns the shared (thread-safe) IBKR connection. Lazy init + auto-reconnect."""
    global _ib_app
    with _app_lock:
        if _ib_app is None:
            _ib_app = IBKRApp()
        _ib_app.ensure_connected(host, port, client_id)
        return _ib_app


# ====================== INTERNAL CONTRACT BUILDER (FOP FIX) ======================
def _build_contract(
    symbol: str,
    sec_type: str,
    exchange: str = "SMART",
    currency: str = "USD",
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    right: Optional[str] = None,
    multiplier: str = "100",
) -> Contract:
    """Builds Contract object with correct settings for all types (including FOP)."""
    contract = Contract()
    contract.symbol = symbol.upper()
    contract.secType = sec_type.upper()
    contract.exchange = exchange.upper()
    contract.currency = currency.upper()

    if expiry:
        contract.lastTradeDateOrContractMonth = expiry

    if sec_type.upper() in ("OPT", "FOP"):
        if strike is not None:
            contract.strike = float(strike)
        if right:
            contract.right = right.upper()
        contract.multiplier = str(multiplier)

        # CRITICAL FIX: Prevents "Ambiguous contract" for future options
        if sec_type.upper() == "FOP":
            contract.tradingClass = symbol.upper()  # "ES" for ES options

    return contract


# ====================== PUBLIC HELPERS ======================
def get_conid(
    symbol: str,
    sec_type: str,
    exchange: str = "SMART",
    currency: str = "USD",
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    right: Optional[str] = None,
    multiplier: str = "100",
    host: str = None,
    port: int = None,
    client_id: int = None,
    timeout: int = None,
) -> int:
    """
    Retrieve IBKR conid for STOCK, INDEX, FUTURE, OPTION, or FUTURE OPTION.
    Thread-safe and production-ready.
    """
    if timeout is None:
        timeout = IB_TIMEOUT

    app = get_ib_app(host, port, client_id)
    contract = _build_contract(symbol, sec_type, exchange, currency, expiry, strike, right, multiplier)

    reqId = app.nextRequestId()
    app.contract_details_dict[reqId] = []
    event = threading.Event()
    app.contract_details_event[reqId] = event

    app.reqContractDetails(reqId, contract)

    waited = event.wait(timeout=timeout)
    errors = app.request_errors.pop(reqId, None)
    details_list = app.contract_details_dict.pop(reqId, [])
    app.contract_details_event.pop(reqId, None)

    if errors:
        error_msg = "; ".join(errors)
        if any("200" in e for e in errors):
            raise RuntimeError(
                f"IBKR API error (200): No security definition found for {symbol} {sec_type}. "
                "Check expiry, strike, exchange, right, or multiplier."
            ) from None
        raise RuntimeError(f"IBKR API error for {symbol} {sec_type}: {error_msg}")

    if not waited or not details_list:
        raise ValueError(f"No security definition found for {symbol} ({sec_type})")

    if len(details_list) > 1:
        conids = [cd.contract.conId for cd in details_list]
        raise ValueError(f"Ambiguous contract! Multiple conids: {conids}. Provide more specific parameters.")

    conid = details_list[0].contract.conId
    if conid <= 0:
        raise ValueError("IBKR returned invalid conid = 0")

    return conid


def get_ib_contract(
    symbol: str,
    sec_type: str,
    exchange: str = "SMART",
    currency: str = "USD",
    expiry: Optional[str] = None,
    strike: Optional[float] = None,
    right: Optional[str] = None,
    multiplier: str = "100",
    host: str = None,
    port: int = None,
    client_id: int = None,
    timeout: int = None,
) -> Contract:
    """
    Returns the FULL Contract object populated by IBKR (recommended for market data requests).
    Automatically handles FOP (future options) correctly.
    """
    if timeout is None:
        timeout = IB_TIMEOUT

    app = get_ib_app(host, port, client_id)
    contract = _build_contract(symbol, sec_type, exchange, currency, expiry, strike, right, multiplier)

    reqId = app.nextRequestId()
    app.contract_details_dict[reqId] = []
    event = threading.Event()
    app.contract_details_event[reqId] = event

    app.reqContractDetails(reqId, contract)

    waited = event.wait(timeout=timeout)
    errors = app.request_errors.pop(reqId, None)
    details_list = app.contract_details_dict.pop(reqId, [])
    app.contract_details_event.pop(reqId, None)

    if errors:
        if any("200" in e for e in errors):
            raise RuntimeError(f"No security definition found for {symbol} {sec_type}.")
        raise RuntimeError(f"IBKR error: {'; '.join(errors)}")

    if not waited or not details_list:
        raise ValueError(f"No contract details returned for {symbol} {sec_type}")

    if len(details_list) > 1:
        raise ValueError(f"Ambiguous contract! Multiple matches found.")

    return details_list[0].contract


# ====================== EXAMPLE USAGE ======================
if __name__ == "__main__":
    print("=== IBKR conid Helper Examples (including FOP) ===")

    print("AAPL STK conid :", get_conid("AAPL", "STK"))
    print("SPX IND conid  :", get_conid("SPX", "IND", exchange="CBOE"))
    print("ES FUT conid   :", get_conid("ES", "FUT", expiry="202612", exchange="CME"))
    print("AAPL OPT conid :", get_conid("AAPL", "OPT", expiry="20260515", strike=250.0, right="C", exchange="SMART"))

    # Index Option (full YYYYMMDD date works for index options)
    print("SPX OPT conid  :", get_conid("SPX", "OPT", expiry="20260515", strike=5500.0, right="C", exchange="CBOE"))

    # Future Option (FOP) - CORRECTED
    # Use futures contract month (YYYYMM) for FOP on CME, not the option expiry date.
    # June 2026 ES futures option (valid as of April 2026)
    print("ES FOP conid   :", get_conid("ES", "FOP", expiry="202606", strike=6900.0, right="C",
                                      multiplier="50", exchange="CME"))

    print("\n✅ Helper is fully ready! Import get_conid or get_ib_contract from any script.")