"""
TastyTrade Options Data Fetcher — OAuth2 + DXLink Streamer
===========================================================
Auth flow (official OAuth2 pattern as of 2025):
  1. POST /oauth/token  {grant_type: refresh_token, client_secret, refresh_token}
     → access_token (15-min JWT), session_expiration
  2. All REST calls:   Authorization: Bearer <access_token>
  3. GET /api-quote-tokens → dxlink-url + streamer token
  4. DXLink WebSocket → live Quote, Greeks, Trade events

One-time setup (do this once in the TastyTrade developer portal):
  a. Create an OAuth application at https://developer.tastytrade.com
     → save your client_secret
  b. OAuth Applications > Manage > Create Grant
     → save your refresh_token  (never expires)

Store both in env vars or a .env file — never hard-code them.

Requirements:
    pip install requests websocket-client tabulate python-dotenv

Usage:
    export TASTY_CLIENT_SECRET="your-client-secret"
    export TASTY_REFRESH_TOKEN="your-refresh-token"
    python tastytrade_options.py
"""

import os
import sys
import json
import time
import threading
import requests
import websocket
from datetime import datetime
from tabulate import tabulate

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
BASE_URL      = "https://api.tastytrade.com"
CERT_URL      = "https://api.cert.tastytrade.com"   # sandbox
DXLINK_WS     = "wss://tasty-openapi-ws.dxfeed.com/realtime"

NUM_EXPIRIES  = 2    # expiries to display per underlying
MAX_STRIKES   = 10   # strikes around ATM per expiry (keep even)
REFRESH_SECS  = 3    # console redraw interval
TOKEN_BUFFER  = 60   # seconds before expiry to proactively refresh


# ══════════════════════════════════════════════════════════════════════════════
#  1. OAUTH2 SESSION
# ══════════════════════════════════════════════════════════════════════════════

class OAuthSession:
    """
    Manages an OAuth2 session against the TastyTrade API.

    Credentials needed (one-time setup):
      • client_secret  – from your OAuth application in the developer portal
      • refresh_token  – generated via OAuth Applications > Manage > Create Grant
                         (refresh tokens never expire)

    Access tokens last 15 minutes. This class refreshes them automatically,
    always keeping the Authorization header up to date for REST requests.

    Token endpoint:
      POST /oauth/token
      Body (JSON): {
        "grant_type":    "refresh_token",
        "client_secret": "<client_secret>",
        "refresh_token": "<refresh_token>"
      }
      → { "access_token": "...", "expires_in": 900, ... }

    All subsequent REST requests use:
      Authorization: Bearer <access_token>
    """

    TOKEN_PATH = "/oauth/token"

    def __init__(self, client_secret: str, refresh_token: str,
                 is_test: bool = False):
        self.client_secret  = client_secret
        self.refresh_token  = refresh_token
        self.base_url       = CERT_URL if is_test else BASE_URL
        self.access_token   = None
        self.expires_at     = 0.0   # Unix timestamp
        self._lock          = threading.Lock()

        self._http = requests.Session()
        self._http.headers.update({"Content-Type": "application/json"})

        # Perform initial token exchange
        self._exchange_token()

    # ── Token management ──────────────────────────────────────────────────────

    def _exchange_token(self) -> None:
        """Exchange refresh_token → access_token via POST /oauth/token."""
        # Explicitly drop Authorization header for this call
        self._http.headers.pop("Authorization", None)

        resp = self._http.post(
            self.base_url + self.TOKEN_PATH,
            json={
                "grant_type":    "refresh_token",
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
        )
        if not resp.ok:
            raise RuntimeError(
                f"OAuth token exchange failed ({resp.status_code}): {resp.text}"
            )
        data = resp.json()
        self.access_token = data["access_token"]
        expires_in        = int(data.get("expires_in", 900))
        self.expires_at   = time.time() + expires_in

        # Keep session header current
        self._http.headers["Authorization"] = f"Bearer {self.access_token}"

    def _refresh_if_needed(self) -> None:
        if time.time() >= self.expires_at - TOKEN_BUFFER:
            with self._lock:
                if time.time() >= self.expires_at - TOKEN_BUFFER:
                    self._exchange_token()

    # ── Convenience REST wrappers ─────────────────────────────────────────────

    def get(self, path: str, **kwargs) -> requests.Response:
        self._refresh_if_needed()
        resp = self._http.get(self.base_url + path, **kwargs)
        resp.raise_for_status()
        return resp

    def post(self, path: str, **kwargs) -> requests.Response:
        self._refresh_if_needed()
        resp = self._http.post(self.base_url + path, **kwargs)
        resp.raise_for_status()
        return resp

    @property
    def auth_header(self) -> str:
        self._refresh_if_needed()
        return f"Bearer {self.access_token}"

    @property
    def token_expiry_str(self) -> str:
        remaining = max(0, int(self.expires_at - time.time()))
        return f"{remaining}s"


# ══════════════════════════════════════════════════════════════════════════════
#  2. STREAMER TOKEN (DXLink)
# ══════════════════════════════════════════════════════════════════════════════

def get_streamer_token(session: OAuthSession) -> tuple[str, str]:
    """Return (dxlink_ws_url, streamer_token) from GET /api-quote-tokens."""
    data = session.get("/api-quote-tokens").json()["data"]
    ws_url = data.get("dxlink-url", DXLINK_WS)
    token  = data["token"]
    print(f"  ✓ Streamer token obtained  (endpoint: {ws_url})\n")
    return ws_url, token


# ══════════════════════════════════════════════════════════════════════════════
#  3. REST — OPTION CHAINS
# ══════════════════════════════════════════════════════════════════════════════

def get_equity_chain(session: OAuthSession, symbol: str) -> list:
    path = f"/option-chains/{requests.utils.quote(symbol, safe='')}/nested"
    items = session.get(path).json().get("data", {}).get("items", [])
    return items[0].get("expirations", []) if items else []


def get_futures_chain(session: OAuthSession, symbol: str) -> list:
    path = f"/futures-option-chains/{requests.utils.quote(symbol, safe='')}"
    items = session.get(path).json().get("data", {}).get("items", [])
    return items[0].get("expirations", []) if items else []


def get_underlying_price(session: OAuthSession, symbol: str,
                          fallback: float) -> float:
    try:
        items = session.get(
            "/market-data/quotes",
            params={"symbols[]": symbol},
        ).json().get("data", {}).get("items", [])
        if items:
            q = items[0]
            v = q.get("last") or q.get("last-trade-price")
            return float(v)
    except Exception:
        pass
    return fallback


def select_atm_strikes(expiration: dict, price: float,
                        max_strikes: int = MAX_STRIKES) -> list[dict]:
    strikes = sorted(
        expiration.get("strikes", []),
        key=lambda s: float(s.get("strike-price", 0)),
    )
    if not strikes:
        return []
    prices  = [float(s.get("strike-price", 0)) for s in strikes]
    atm_idx = min(range(len(prices)), key=lambda i: abs(prices[i] - price))
    half    = max_strikes // 2
    return strikes[max(0, atm_idx - half): min(len(strikes), atm_idx + half)]


def collect_streamer_symbols(strikes: list[dict]) -> list[str]:
    syms = []
    for s in strikes:
        for side in ("call", "put"):
            sym = s.get(side, {}).get("streamer-symbol", "")
            if sym:
                syms.append(sym)
    return syms


# ══════════════════════════════════════════════════════════════════════════════
#  4. DXLINK STREAMER
# ══════════════════════════════════════════════════════════════════════════════

class DXLinkStreamer:
    """
    DXLink WebSocket protocol (COMPACT format):
      C→S  SETUP            version + keepalive params
      S→C  SETUP            server ack
      C→S  AUTH             streamer token
      S→C  AUTH_STATE       "AUTHORIZED"
      C→S  CHANNEL_REQUEST  open FEED channel 1
      S→C  CHANNEL_OPENED
      C→S  FEED_SETUP       declare event fields (COMPACT format)
      C→S  FEED_SUBSCRIPTION  add Quote + Greeks + Trade symbols
      S→C  FEED_DATA        streaming compact event batches
      C↔S  KEEPALIVE        heartbeat
    """

    CHANNEL = 1

    def __init__(self, ws_url: str, streamer_token: str,
                 symbols: list[str], session: OAuthSession):
        self.ws_url         = ws_url
        self.streamer_token = streamer_token
        self.symbols        = symbols
        self.session        = session   # for token refresh on reconnect

        self._lock   = threading.Lock()
        self._data: dict[str, dict] = {s: {} for s in symbols}

        self._ws         = None
        self._keep_going = True
        self._thread     = None
        self.connected   = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._keep_going = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def get(self, symbol: str) -> dict:
        with self._lock:
            return dict(self._data.get(symbol, {}))

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {s: dict(v) for s, v in self._data.items()}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        self._ws = websocket.WebSocketApp(
            self.ws_url,
            on_open    = self._on_open,
            on_message = self._on_message,
            on_error   = self._on_error,
            on_close   = self._on_close,
        )
        while self._keep_going:
            try:
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                print(f"\n  [DXLink] Exception: {exc}")
            if self._keep_going:
                # Refresh streamer token before reconnect (access token may expire)
                try:
                    _, self.streamer_token = get_streamer_token(self.session)
                except Exception:
                    pass
                time.sleep(3)

    def _send(self, msg: dict):
        try:
            if self._ws:
                self._ws.send(json.dumps(msg))
        except Exception:
            pass

    def _on_open(self, ws):
        self.connected = False
        self._send({
            "type":                   "SETUP",
            "channel":                0,
            "keepaliveTimeout":       60,
            "acceptKeepaliveTimeout": 60,
            "version":                "0.1",
        })

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        mtype = msg.get("type", "")

        if mtype == "SETUP":
            self._send({"type": "AUTH", "channel": 0,
                        "token": self.streamer_token})

        elif mtype == "AUTH_STATE":
            if msg.get("state") == "AUTHORIZED":
                self._send({
                    "type":       "CHANNEL_REQUEST",
                    "channel":    self.CHANNEL,
                    "service":    "FEED",
                    "parameters": {"contract": "AUTO"},
                })
            else:
                print(f"\n  [DXLink] Auth rejected: {msg}")
                self.stop()

        elif mtype == "CHANNEL_OPENED" and msg.get("channel") == self.CHANNEL:
            self._send({
                "type":    "FEED_SETUP",
                "channel": self.CHANNEL,
                "acceptAggregationPeriod": 1,
                "acceptDataFormat":        "COMPACT",
                "acceptEventFields": {
                    "Quote":  ["eventSymbol", "bidPrice", "askPrice",
                               "bidSize", "askSize"],
                    "Greeks": ["eventSymbol", "volatility", "delta",
                               "gamma", "theta", "vega", "rho"],
                    "Trade":  ["eventSymbol", "price", "size",
                               "dayVolume", "change"],
                },
            })
            subs = (
                [{"type": "Quote",  "symbol": s} for s in self.symbols] +
                [{"type": "Greeks", "symbol": s} for s in self.symbols] +
                [{"type": "Trade",  "symbol": s} for s in self.symbols]
            )
            self._send({"type": "FEED_SUBSCRIPTION", "channel": self.CHANNEL,
                        "reset": True, "add": subs})
            self.connected = True

        elif mtype == "FEED_DATA" and msg.get("channel") == self.CHANNEL:
            self._process_feed(msg.get("data", []))

        elif mtype == "KEEPALIVE":
            self._send({"type": "KEEPALIVE", "channel": 0})

        elif mtype == "ERROR":
            print(f"\n  [DXLink] Error: {msg.get('error')} — {msg.get('message')}")

    def _on_error(self, ws, error):
        print(f"\n  [DXLink] WS error: {error}")

    def _on_close(self, ws, code, reason):
        self.connected = False

    def _process_feed(self, data: list):
        """
        COMPACT format: flat list of (header_dict, event_batch) pairs.
        event_batch is [[field, ...], [field, ...]] or a single flat list.
        """
        i = 0
        while i < len(data) - 1:
            header = data[i]
            values = data[i + 1]
            i += 2
            if not isinstance(header, dict) or not isinstance(values, list):
                continue
            etype  = header.get("type")
            fields = header.get("eventFields", [])
            if not fields:
                continue
            events = values if (values and isinstance(values[0], list)) else [values]
            with self._lock:
                for ev in events:
                    if len(ev) != len(fields):
                        continue
                    rec = dict(zip(fields, ev))
                    sym = rec.get("eventSymbol")
                    if sym not in self._data:
                        continue
                    d = self._data[sym]
                    if etype == "Quote":
                        if rec.get("bidPrice") is not None:
                            d["bid"]      = rec["bidPrice"]
                            d["ask"]      = rec.get("askPrice")
                            d["bid_size"] = rec.get("bidSize")
                            d["ask_size"] = rec.get("askSize")
                    elif etype == "Greeks":
                        if rec.get("volatility") is not None:
                            d["iv"]    = rec["volatility"]
                            d["delta"] = rec.get("delta")
                            d["gamma"] = rec.get("gamma")
                            d["theta"] = rec.get("theta")
                            d["vega"]  = rec.get("vega")
                            d["rho"]   = rec.get("rho")
                    elif etype == "Trade":
                        if rec.get("price") is not None:
                            d["last"]   = rec["price"]
                            d["volume"] = rec.get("dayVolume")
                            d["change"] = rec.get("change")


# ══════════════════════════════════════════════════════════════════════════════
#  5. DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

def _f(v, dec=2, suffix="") -> str:
    try:    return f"{float(v):.{dec}f}{suffix}"
    except: return "--"


def build_chain_block(label: str, expirations: list,
                       streamer: DXLinkStreamer, price: float) -> str:
    lines = [
        f"\n{'═'*104}",
        f"  {label}   (underlying ≈ {_f(price)})",
        f"{'═'*104}",
    ]
    headers = [
        "Strike",
        "C Last", "C Bid",  "C Ask",  "C Size",
        "C IV%",  "C Δ",    "C Γ",    "C Θ",    "C Vega",
        "P Last", "P Bid",  "P Ask",  "P Size",
        "P IV%",  "P Δ",    "P Θ",
    ]
    for exp in expirations[:NUM_EXPIRIES]:
        exp_date = exp.get("expiration-date", "?")
        dte      = exp.get("days-to-expiration", "?")
        exp_type = exp.get("expiration-type", "")
        lines.append(f"\n  Expiry {exp_date}  |  DTE {dte}  |  {exp_type}")
        rows = []
        for s in select_atm_strikes(exp, price, MAX_STRIKES):
            strike   = float(s.get("strike-price", 0))
            call_sym = s.get("call", {}).get("streamer-symbol", "")
            put_sym  = s.get("put",  {}).get("streamer-symbol", "")
            cd = streamer.get(call_sym) if call_sym else {}
            pd = streamer.get(put_sym)  if put_sym  else {}
            atm = abs(strike - price) < price * 0.002
            rows.append([
                f"{'▶' if atm else ' '} {strike:.2f}",
                _f(cd.get("last")),  _f(cd.get("bid")),  _f(cd.get("ask")),
                _f(cd.get("ask_size"), 0),
                _f(cd.get("iv"), 1, "%"), _f(cd.get("delta"), 3),
                _f(cd.get("gamma"), 4),   _f(cd.get("theta"), 3),
                _f(cd.get("vega"), 3),
                _f(pd.get("last")),  _f(pd.get("bid")),  _f(pd.get("ask")),
                _f(pd.get("ask_size"), 0),
                _f(pd.get("iv"), 1, "%"), _f(pd.get("delta"), 3),
                _f(pd.get("theta"), 3),
            ])
        lines.append(tabulate(rows, headers=headers, tablefmt="simple"))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  6. MAIN
# ══════════════════════════════════════════════════════════════════════════════

UNDERLYINGS = {
    "SPX": {"label": "SPX — S&P 500 Index Options",
            "chain_fn": get_equity_chain,  "fallback": 5500.0},
    "/ES": {"label": "/ES — E-mini S&P 500 Futures Options",
            "chain_fn": get_futures_chain, "fallback": 5500.0},
    "VIX": {"label": "VIX — Volatility Index Options",
            "chain_fn": get_equity_chain,  "fallback": 18.0},
}


def main():
    print("=" * 60)
    print("  TastyTrade Options — OAuth2 + DXLink Live Streamer")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Credentials (env vars or .env file) ───────────────────────────────────
    client_secret = os.environ.get("TASTY_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("TASTY_REFRESH_TOKEN", "").strip()
    is_test       = os.environ.get("TASTY_SANDBOX", "").lower() in ("1", "true", "yes")

    if not client_secret:
        client_secret = input("\nTastyTrade client secret: ").strip()
    if not refresh_token:
        refresh_token = input("TastyTrade refresh token:  ").strip()

    print(f"\n  Mode: {'SANDBOX (cert)' if is_test else 'PRODUCTION'}")

    # ── OAuth2 session ────────────────────────────────────────────────────────
    print("  Authenticating via OAuth2 …", end=" ", flush=True)
    try:
        session = OAuthSession(client_secret, refresh_token, is_test=is_test)
        print(f"✓  (token valid for {session.token_expiry_str})\n")
    except Exception as exc:
        print(f"\n  Auth failed: {exc}")
        sys.exit(1)

    # ── Streamer token ────────────────────────────────────────────────────────
    ws_url, streamer_token = get_streamer_token(session)

    # ── REST: chains + prices ─────────────────────────────────────────────────
    chain_data = {}
    prices     = {}
    all_syms   = []

    for sym, cfg in UNDERLYINGS.items():
        print(f"  Loading {cfg['label']} …", end=" ", flush=True)
        try:
            exps  = cfg["chain_fn"](session, sym)
            price = get_underlying_price(session, sym, cfg["fallback"])
            chain_data[sym] = exps
            prices[sym]     = price

            strike_rows = []
            for exp in exps[:NUM_EXPIRIES]:
                strike_rows.extend(select_atm_strikes(exp, price, MAX_STRIKES))
            syms = collect_streamer_symbols(strike_rows)
            all_syms.extend(syms)
            print(f"✓  ({len(exps)} expiries, {len(syms)} option symbols)")
        except Exception as exc:
            print(f"✗  {exc}")
            chain_data[sym] = []

    all_syms = list(dict.fromkeys(all_syms))
    print(f"\n  Total DXLink subscriptions: {len(all_syms)}")
    if not all_syms:
        print("  Nothing to subscribe — check errors above.")
        return

    # ── DXLink WebSocket ──────────────────────────────────────────────────────
    print("  Connecting to DXLink …")
    streamer = DXLinkStreamer(ws_url, streamer_token, all_syms, session)
    streamer.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        snap = streamer.snapshot()
        if any(v.get("bid") is not None or v.get("iv") is not None
               for v in snap.values()):
            break
        time.sleep(0.25)
    print("  ✓ Live data flowing.  Press Ctrl-C to exit.\n")

    # ── Live refresh loop ─────────────────────────────────────────────────────
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = "LIVE" if streamer.connected else "reconnecting…"
            token_ttl = session.token_expiry_str
            print(
                f"TastyTrade Options  |  OAuth2 token TTL: {token_ttl}"
                f"  |  DXLink [{status}]  |  {now}  |  Ctrl-C to quit"
            )
            for sym, cfg in UNDERLYINGS.items():
                print(build_chain_block(
                    cfg["label"],
                    chain_data.get(sym, []),
                    streamer,
                    prices.get(sym, 0.0),
                ))
            time.sleep(REFRESH_SECS)

    except KeyboardInterrupt:
        print("\n\n  Stopping …")

    finally:
        streamer.stop()
        print("  Done.")


if __name__ == "__main__":
    main()