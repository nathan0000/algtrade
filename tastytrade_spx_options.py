"""
TastyTrade Options Data Fetcher — OAuth2 + DXLink Streamer
===========================================================
Auth flow (official OAuth2 pattern):
  1. POST /oauth/token  {grant_type: refresh_token, client_secret, refresh_token}
     → access_token (15-min JWT)
  2. All REST calls: Authorization: Bearer <access_token>
  3. GET /api-quote-tokens → dxlink-url + streamer token
  4. DXLink WebSocket → live Quote, Greeks, Trade events

One-time setup (TastyTrade developer portal):
  a. Create OAuth application → save client_secret
  b. OAuth Applications > Manage > Create Grant → save refresh_token (never expires)

Requirements:
    pip install requests websocket-client tabulate python-dotenv

Usage:
    export TASTY_CLIENT_ID="your-client-id"
    export TASTY_CLIENT_SECRET="your-client-secret"
    export TASTY_REFRESH_TOKEN="your-refresh-token"
    python tastytrade_options.py

NOTE: The sandbox (TASTY_SANDBOX=true) does NOT support market-data quotes or
futures option chains — those endpoints return 404/502 in cert. Use production
credentials for full functionality.
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
# Both URLs confirmed from official tastytrade SDK source (tastyware/tastytrade)
PROD_URL      = "https://api.tastyworks.com"
CERT_URL      = "https://api.cert.tastyworks.com"   # sandbox — note: tastyworks.com not tastytrade.com
DXLINK_WS     = "wss://tasty-openapi-ws.dxfeed.com/realtime"

NUM_EXPIRIES  = 2    # expiries per underlying
MAX_STRIKES   = 10   # strikes around ATM (keep even)
REFRESH_SECS  = 3    # console redraw interval
TOKEN_BUFFER  = 60   # seconds before expiry to proactively refresh


# ══════════════════════════════════════════════════════════════════════════════
#  1. OAUTH2 SESSION
# ══════════════════════════════════════════════════════════════════════════════

class OAuthSession:
    """
    OAuth2 session manager.

    Token endpoint: POST /oauth/token
      body: { grant_type, client_id, client_secret, refresh_token }
      → { access_token, expires_in, ... }

    All API calls: Authorization: Bearer <access_token>
    Access tokens last 15 min; refresh tokens never expire.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 is_test: bool = False):
        self.client_id      = client_id
        self.client_secret  = client_secret
        self.refresh_token  = refresh_token
        self.base_url       = CERT_URL if is_test else PROD_URL
        self.access_token   = None
        self.expires_at     = 0.0
        self._lock          = threading.Lock()
        self._http          = requests.Session()
        self._http.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })
        self._exchange_token()

    def _exchange_token(self) -> None:
        """POST /oauth/token with refresh_token grant to get a new access_token."""
        # Must NOT send Authorization header on the token endpoint itself
        hdrs = dict(self._http.headers)
        hdrs.pop("Authorization", None)

        resp = requests.post(
            self.base_url + "/oauth/token",
            headers=hdrs,
            json={
                "grant_type":    "refresh_token",
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
        )
        if not resp.ok:
            raise RuntimeError(
                f"OAuth token exchange failed ({resp.status_code}): {resp.text}"
            )
        data              = resp.json()
        self.access_token = data["access_token"]
        expires_in        = int(data.get("expires_in", 900))
        self.expires_at   = time.time() + expires_in
        self._http.headers["Authorization"] = f"Bearer {self.access_token}"

    def _refresh_if_needed(self) -> None:
        if time.time() >= self.expires_at - TOKEN_BUFFER:
            with self._lock:
                if time.time() >= self.expires_at - TOKEN_BUFFER:
                    self._exchange_token()

    def get(self, path: str, **kwargs) -> requests.Response:
        self._refresh_if_needed()
        resp = self._http.get(self.base_url + path, **kwargs)
        if not resp.ok:
            resp.raise_for_status()
        return resp

    @property
    def token_ttl(self) -> str:
        remaining = max(0, int(self.expires_at - time.time()))
        return f"{remaining}s"


# ══════════════════════════════════════════════════════════════════════════════
#  2. STREAMER TOKEN
# ══════════════════════════════════════════════════════════════════════════════

def get_streamer_token(session: OAuthSession) -> tuple[str, str]:
    """GET /api-quote-tokens → (dxlink_ws_url, streamer_token)."""
    data   = session.get("/api-quote-tokens").json()["data"]
    ws_url = data.get("dxlink-url") or DXLINK_WS
    token  = data["token"]
    print(f"  ✓ Streamer token obtained  (endpoint: {ws_url})\n")
    return ws_url, token


# ══════════════════════════════════════════════════════════════════════════════
#  3. REST — OPTION CHAINS
#
#  The /option-chains/{symbol}/nested endpoint returns:
#    { "data": { "items": [ <chain-object> ] } }
#  where <chain-object> is a dict with keys like "underlying-symbol",
#  "root-symbol", "expirations", etc.
#
#  The /futures-option-chains/{symbol} endpoint returns:
#    { "data": { "items": [ <futures-chain-object> ] } }
#
#  In both cases items[0] is a dict (not a string).  The previous bug was
#  triggered when items[0] was unexpectedly a string — we now guard with
#  isinstance() and print the raw response on unexpected shapes.
# ══════════════════════════════════════════════════════════════════════════════

def _first_dict_item(items: list) -> dict:
    """Return the first dict element in items, ignoring strings/nulls."""
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _parse_chain_body(body, symbol: str, endpoint: str) -> list[dict]:
    """
    Safely extract expirations from a chain response body.
    Handles unexpected shapes (string body, missing keys, etc.) with clear errors.
    """
    if not isinstance(body, dict):
        raise ValueError(
            f"Expected JSON object from {endpoint} but got "
            f"{type(body).__name__}: {str(body)[:200]}"
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise ValueError(
            f"Response 'data' field missing or not a dict for {symbol}. "
            f"Full response: {str(body)[:400]}"
        )
    items = data.get("items", [])
    chain = _first_dict_item(items)
    if not chain:
        raise ValueError(
            f"No chain dict found in items for {symbol}. "
            f"items={str(items)[:400]}"
        )
    expirations = chain.get("expirations", [])
    if not expirations:
        raise ValueError(
            f"Chain found but 'expirations' is empty for {symbol}. "
            f"Chain keys: {list(chain.keys())}"
        )
    return expirations


def get_equity_chain(session: OAuthSession, symbol: str) -> list[dict]:
    """GET /option-chains/{symbol}/nested -> list of expiration dicts."""
    path = f"/option-chains/{requests.utils.quote(symbol, safe='')}/nested"
    body = session.get(path).json()
    return _parse_chain_body(body, symbol, path)


def get_futures_chain(session: OAuthSession, symbol: str) -> list[dict]:
    """GET /futures-option-chains/{symbol} -> list of expiration dicts."""
    path = f"/futures-option-chains/{requests.utils.quote(symbol, safe='')}"
    body = session.get(path).json()
    return _parse_chain_body(body, symbol, path)

def get_underlying_price(session: OAuthSession, symbol: str, fallback: float) -> float:
    """GET /market-data/quotes?symbols[]={symbol} → last price."""
    try:
        items = session.get(
            "/market-data/quotes",
            params={"symbols[]": symbol},
        ).json().get("data", {}).get("items", [])
        if items:
            q = _first_dict_item(items) if isinstance(items[0], dict) else {}
            v = q.get("last") or q.get("last-trade-price")
            if v is not None:
                return float(v)
    except Exception as exc:
        print(f"\n  [WARN] Could not fetch price for {symbol}: {exc}")
    return fallback


def select_atm_strikes(expiration: dict, price: float,
                        max_strikes: int = MAX_STRIKES) -> list[dict]:
    """Return up to max_strikes strike dicts centred on the ATM."""
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
    DXLink WebSocket (COMPACT format) — subscribes Quote + Greeks + Trade.

    Handshake:
      C→S SETUP → S→C SETUP → C→S AUTH → S→C AUTH_STATE(AUTHORIZED)
      → C→S CHANNEL_REQUEST → S→C CHANNEL_OPENED
      → C→S FEED_SETUP (declare fields, COMPACT)
      → C→S FEED_SUBSCRIPTION (add symbols)
      → S→C FEED_DATA (streaming)
      ← C→S KEEPALIVE echo
    """

    CHANNEL = 1

    def __init__(self, ws_url: str, streamer_token: str,
                 symbols: list[str], session: OAuthSession):
        self.ws_url         = ws_url
        self.streamer_token = streamer_token
        self.symbols        = symbols
        self.session        = session

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
                # Refresh streamer token before reconnect
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
            "type": "SETUP", "channel": 0,
            "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60,
            "version": "0.1",
        })

    def _on_message(self, ws, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        mtype = msg.get("type", "")

        if mtype == "SETUP":
            self._send({"type": "AUTH", "channel": 0, "token": self.streamer_token})

        elif mtype == "AUTH_STATE":
            if msg.get("state") == "AUTHORIZED":
                self._send({
                    "type": "CHANNEL_REQUEST", "channel": self.CHANNEL,
                    "service": "FEED", "parameters": {"contract": "AUTO"},
                })
            else:
                print(f"\n  [DXLink] Auth rejected: {msg}")
                self.stop()

        elif mtype == "CHANNEL_OPENED" and msg.get("channel") == self.CHANNEL:
            self._send({
                "type": "FEED_SETUP", "channel": self.CHANNEL,
                "acceptAggregationPeriod": 1,
                "acceptDataFormat": "COMPACT",
                "acceptEventFields": {
                    "Quote":  ["eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize"],
                    "Greeks": ["eventSymbol", "volatility", "delta", "gamma", "theta", "vega", "rho"],
                    "Trade":  ["eventSymbol", "price", "size", "dayVolume", "change"],
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
        COMPACT: alternating (header_dict, event_batch) pairs.
        event_batch = [[v, v, ...], ...] or a single flat [v, v, ...].
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
            if not fields or not values:
                continue
            # Normalise to list-of-lists
            events = values if isinstance(values[0], list) else [values]
            with self._lock:
                for ev in events:
                    if len(ev) != len(fields):
                        continue
                    rec = dict(zip(fields, ev))
                    sym = rec.get("eventSymbol")
                    if sym not in self._data:
                        continue
                    d = self._data[sym]
                    if etype == "Quote" and rec.get("bidPrice") is not None:
                        d.update(bid=rec["bidPrice"], ask=rec.get("askPrice"),
                                 bid_size=rec.get("bidSize"), ask_size=rec.get("askSize"))
                    elif etype == "Greeks" and rec.get("volatility") is not None:
                        d.update(iv=rec["volatility"], delta=rec.get("delta"),
                                 gamma=rec.get("gamma"), theta=rec.get("theta"),
                                 vega=rec.get("vega"), rho=rec.get("rho"))
                    elif etype == "Trade" and rec.get("price") is not None:
                        d.update(last=rec["price"], volume=rec.get("dayVolume"),
                                 change=rec.get("change"))


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
    if not expirations:
        lines.append("  (no expirations loaded)")
        return "\n".join(lines)

    headers = [
        "Strike",
        "C Last", "C Bid",  "C Ask",  "C Sz",
        "C IV%",  "C Δ",    "C Γ",    "C Θ",    "C Vega",
        "P Last", "P Bid",  "P Ask",  "P Sz",
        "P IV%",  "P Δ",    "P Θ",
    ]
    for exp in expirations[:NUM_EXPIRIES]:
        lines.append(
            f"\n  Expiry {exp.get('expiration-date','?')}  "
            f"|  DTE {exp.get('days-to-expiration','?')}  "
            f"|  {exp.get('expiration-type','')}"
        )
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
    "/ESM6": {"label": "/ES — E-mini S&P 500 Futures Options",
            "chain_fn": get_futures_chain, "fallback": 5500.0},
    "VIX": {"label": "VIX — Volatility Index Options",
            "chain_fn": get_equity_chain,  "fallback": 18.0},
}


def main():
    print("=" * 60)
    print("  TastyTrade Options — OAuth2 + DXLink Live Streamer")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    client_id     = os.environ.get("TASTY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TASTY_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("TASTY_REFRESH_TOKEN", "").strip()
    is_test       = os.environ.get("TASTY_SANDBOX", "").lower() in ("1", "true", "yes")

    if not client_id:
        client_id     = input("\nTastyTrade client ID:     ").strip()
    if not client_secret:
        client_secret = input("TastyTrade client secret: ").strip()
    if not refresh_token:
        refresh_token = input("TastyTrade refresh token:  ").strip()

    env_label = "SANDBOX (api.cert.tastyworks.com)" if is_test else "PRODUCTION (api.tastyworks.com)"
    print(f"\n  Environment: {env_label}")
    if is_test:
        print("  ⚠  Sandbox note: market-data quotes and futures chains are")
        print("     unavailable in cert. Switch to production for full data.\n")

    # ── OAuth2 ────────────────────────────────────────────────────────────────
    print("  Authenticating …", end=" ", flush=True)
    try:
        session = OAuthSession(client_id, client_secret, refresh_token, is_test=is_test)
        print(f"✓  (token TTL: {session.token_ttl})\n")
    except Exception as exc:
        print(f"\n  Auth failed: {exc}")
        sys.exit(1)

    # ── Streamer token ────────────────────────────────────────────────────────
    ws_url, streamer_token = get_streamer_token(session)

    # ── Load chains ───────────────────────────────────────────────────────────
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
        print("  Nothing to subscribe — check DEBUG output above.")
        return

    # ── DXLink ────────────────────────────────────────────────────────────────
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

    # ── Live display loop ─────────────────────────────────────────────────────
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = "LIVE" if streamer.connected else "reconnecting…"
            print(
                f"TastyTrade Options  |  OAuth2 TTL: {session.token_ttl}"
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
