"""
market_data.py — SPX market data retrieval layer.

Responsibilities:
  - Fetch SPX index price (spot)
  - Fetch and cache the SPX/SPXW option chain (expirations + strikes)
  - Resolve contract details (con_ids) for specific legs
  - Snapshot bid/ask + Greeks for a list of OptionLeg objects
  - Refresh live prices for already-resolved legs (used by monitor)

Knows nothing about order placement or strategy logic.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Optional

from ibapi.contract import Contract

from .gateway import IBGateway
from .models  import OptionLeg, OptionRight
from .greeks  import greeks_from_market_price, time_to_expiry_years

log = logging.getLogger("MarketData")

# Generic tick string that requests option greeks (model + implied vol)
GREEK_TICK = "100"


class MarketDataError(RuntimeError):
    pass


class MarketData:
    """
    All market data operations against IBKR TWS API.

    Usage:
        md = MarketData(gateway)
        spot   = md.get_spx_spot()
        chain  = md.get_chain(expiry="20250120")
        legs   = [OptionLeg("SPX","20250120",5200,OptionRight.CALL), ...]
        md.resolve_conids(legs)
        md.snapshot_prices_and_greeks(legs)
    """

    def __init__(self, gateway: IBGateway, symbol: str = "SPX",
                 multiplier: int = 100):
        self.gw         = gateway
        self.symbol     = symbol
        self.multiplier = multiplier

        # Cache for chain data keyed by expiry
        self._chain_cache: Optional[dict] = None   # raw chain from gateway
        self._underlying_con_id: int = 0            # resolved lazily, required
                                                      # by reqSecDefOptParams

    # =========================================================================
    # SPX spot price
    # =========================================================================

    def get_spx_spot(self, timeout: float = 12.0,
                      allow_historical_fallback: bool = True) -> float:
        """
        Return the current SPX index level.

        Primary path: live snapshot market data request (bid/ask/last/close).
        Fallback path: when outside RTH (or live data is unavailable for any
        reason — no permission, weekend, holiday, IBKR quirks with index
        snapshots), request the most recent daily historical bar and use its
        close. This makes spot retrieval resilient across market sessions
        rather than raising whenever live ticks don't show up.
        """
        try:
            return self._get_spx_spot_live(timeout=timeout)
        except MarketDataError as live_err:
            if not allow_historical_fallback:
                raise
            log.warning(
                f"Live SPX snapshot failed ({live_err}) — "
                f"falling back to historical daily bar"
            )
            return self._get_spx_spot_from_history()

    def _get_spx_spot_live(self, timeout: float = 12.0) -> float:
        """Live snapshot path. Raises MarketDataError if no usable tick."""
        contract = self._spx_index_contract()
        req_id   = self.gw.next_req_id()
        self.gw.prepare_tick_request(req_id)

        log.info(f"Requesting SPX live spot price (reqId={req_id})")
        self.gw.reqMktData(req_id, contract, "", True, False, [])

        # tickSnapshotEnd sets the event; wait for it
        if not self.gw.wait_for_tick(req_id, timeout=timeout):
            log.warning("SPX spot snapshot timed out — checking partial data")

        ticks = self.gw.tick_data.get(req_id, {})
        log.debug(f"SPX live tick data: {ticks}")

        # Prefer last trade, then ask (during market hours ask ≈ last for index)
        for key in ("last", "ask", "bid", "close"):
            val = ticks.get(key, 0.0)
            if val and val > 0:
                log.info(f"SPX spot = {val:.2f} (source: live {key})")
                return val

        ib_errors = self.gw.errors_for(req_id)
        raise MarketDataError(
            f"No usable live tick for SPX. Tick data: {ticks}. "
            f"IBKR errors for this request: {ib_errors or 'none reported'}"
        )

    def _get_spx_spot_from_history(self, timeout: float = 15.0) -> float:
        """
        Fallback path used when live data is unavailable (outside RTH,
        no delayed-data fallback, weekends/holidays, etc).

        Requests the most recent daily bar via reqHistoricalData and
        returns its close.

        whatToShow="TRADES" — empirically confirmed working for SPX on this
        account/data subscription. Note: IBKR's own docs generally recommend
        "MIDPOINT" for index contracts since indices have no real trade
        tape, and MIDPOINT did NOT return data in testing here (likely a
        permissions/entitlement difference on this specific account/feed).
        TRADES does return data for SPX in practice on this setup, so that's
        what's used. If this stops working after a permissions change, try
        switching back to MIDPOINT or check entitlements for SPX historical
        data specifically (separate from live SPX/SPXW market data).
        """
        contract = self._spx_index_contract()
        req_id   = self.gw.next_req_id()
        self.gw.prepare_historical_request(req_id)

        log.info(f"Requesting SPX daily historical bar (reqId={req_id}) "
                 f"as RTH-closed fallback")

        # endDateTime="" → "now" in TWS local time; useRTH=1 restricts the
        # bar's close to regular trading hours so we don't get a stale/odd
        # extended-hours print.
        self.gw.reqHistoricalData(
            reqId           = req_id,
            contract        = contract,
            endDateTime     = "",
            durationStr     = "2 D",
            barSizeSetting  = "1 day",
            whatToShow      = "TRADES",
            useRTH          = 1,
            formatDate      = 1,
            keepUpToDate    = False,
            chartOptions    = [],
        )

        if not self.gw.wait_for_historical(req_id, timeout=timeout):
            ib_errors = self.gw.errors_for(req_id)
            raise MarketDataError(
                "Historical data request timed out — could not resolve "
                "SPX spot via either live ticks or daily bars. "
                f"IBKR errors for this request: {ib_errors or 'none reported'}. "
                "Check market data subscriptions for SPX/CBOE, and confirm "
                "IB Gateway is connected and logged in."
            )

        bars = self.gw.historical_bars.get(req_id, [])
        if not bars:
            ib_errors = self.gw.errors_for(req_id)
            raise MarketDataError(
                "Historical data request returned no bars for SPX. "
                f"IBKR errors for this request: {ib_errors or 'none reported'}. "
                "Check market data subscriptions for SPX/CBOE."
            )

        # Bars are returned in chronological order; the last one is most recent.
        latest_bar = bars[-1]
        close = float(latest_bar.close)
        if close <= 0:
            raise MarketDataError(
                f"Historical bar close price invalid: {close}"
            )

        log.info(
            f"SPX spot = {close:.2f} "
            f"(source: historical daily bar, date={latest_bar.date})"
        )
        return close

    # =========================================================================
    # Underlying conId resolution (required by reqSecDefOptParams)
    # =========================================================================

    def resolve_underlying_conid(self, timeout: float = 15.0) -> int:
        """
        Resolve and cache the conId of the SPX index contract itself.

        IBKR's reqSecDefOptParams REQUIRES the real underlying conId —
        passing 0 causes error 321 ("error validating request") and the
        chain request is silently dropped server-side, which otherwise
        manifests as a confusing 30s timeout with no further detail.
        """
        if self._underlying_con_id:
            return self._underlying_con_id

        contract = self._spx_index_contract()
        req_id   = self.gw.next_req_id()
        self.gw.prepare_details_request(req_id)

        log.info(f"Resolving SPX underlying conId (reqId={req_id})")
        self.gw.reqContractDetails(req_id, contract)

        if not self.gw.wait_for_details(req_id, timeout=timeout):
            ib_errors = self.gw.errors_for(req_id)
            raise MarketDataError(
                "Timed out resolving SPX underlying conId via "
                f"reqContractDetails (reqId={req_id}, timeout={timeout}s). "
                f"IBKR errors for this request: {ib_errors or 'none reported'}. "
                "Note: this is a contract lookup, NOT a market data request — "
                "it does not require any market data subscription/permission. "
                "If no IBKR error was reported, this usually means IB Gateway "
                "hadn't finished its own startup/login handshake yet when the "
                "request was sent, or the connection was still settling after "
                "a reconnect. Confirm IB Gateway shows logged-in/green status "
                "before this process connects, and consider increasing this "
                "timeout if it consistently fires right after a fresh connect."
            )

        details_list = self.gw.contract_details.get(req_id, [])
        if not details_list:
            ib_errors = self.gw.errors_for(req_id)
            raise MarketDataError(
                "No contract details returned for SPX index. "
                f"IBKR errors for this request: {ib_errors or 'none reported'}. "
                "Verify symbol='SPX', secType='IND', exchange='CBOE' is valid "
                "for this account."
            )

        con_id = details_list[0].contract.conId
        if not con_id:
            raise MarketDataError("SPX contract details returned conId=0")

        self._underlying_con_id = con_id
        log.info(f"SPX underlying conId resolved: {con_id}")
        return con_id

    # =========================================================================
    # Option chain
    # =========================================================================

    def get_chain(self, force_refresh: bool = False) -> dict:
        """
        Fetch (or return cached) SPX option chain.

        Returns dict with keys:
            expirations  : list[str]  sorted YYYYMMDD strings
            strikes      : list[float] sorted
            trading_class: str  (usually "SPXW" for weeklies)
            multiplier   : str
        """
        if self._chain_cache and not force_refresh:
            return self._chain_cache

        # reqSecDefOptParams requires the REAL underlying conId.
        # Passing 0 triggers IBKR error 321 and the request never resolves.
        underlying_con_id = self.resolve_underlying_conid()

        req_id = self.gw.next_req_id()
        self.gw.prepare_chain_request(req_id)

        log.info(f"Requesting SPX option chain (reqId={req_id}, "
                 f"underlyingConId={underlying_con_id})")
        self.gw.reqSecDefOptParams(
            req_id, self.symbol, "", "IND", underlying_con_id
        )

        if not self.gw.wait_for_chain(req_id, timeout=30.0):
            raise MarketDataError("Option chain request timed out after 30s")

        chain = self.gw.chain_data.get(req_id)
        if not chain:
            raise MarketDataError(
                "No chain data returned. Check symbol and exchange settings."
            )

        self._chain_cache = chain
        log.info(f"Chain cached: {len(chain['expirations'])} expiries, "
                 f"{len(chain['strikes'])} strikes  "
                 f"class={chain['trading_class']}")
        return chain

    def available_expirations(self) -> list[str]:
        return self.get_chain()["expirations"]

    def available_strikes(self) -> list[float]:
        return self.get_chain()["strikes"]

    def today_expiry(self) -> str:
        """Return today's date as YYYYMMDD."""
        return date.today().strftime("%Y%m%d")

    def validate_expiry(self, expiry: str) -> bool:
        return expiry in self.available_expirations()

    # =========================================================================
    # Contract details (con_id resolution)
    # =========================================================================

    def resolve_conids(self, legs: list[OptionLeg], timeout: float = 20.0):
        """
        Populate leg.con_id and leg.trading_class for each leg in-place.

        Batches all requests before waiting, so N legs take ~1 round-trip
        worth of latency rather than N sequential round-trips.

        Throttled to stay under IBKR's 50 msg/sec API rate limit —
        reqContractDetails doesn't consume a market data line, but it's
        still an API message and counts toward the same rate cap as
        reqMktData. A tight loop of 100+ calls with no pacing risks
        dropped requests or a forced disconnect.
        """
        chain = self.get_chain()
        trading_class = chain["trading_class"]

        # Map req_id → leg index so we can match replies
        pending: dict[int, int] = {}   # req_id → list index
        events:  dict[int, threading.Event] = {}

        requests_sent = 0
        for i, leg in enumerate(legs):
            if leg.con_id > 0:
                continue   # already resolved

            req_id = self.gw.next_req_id()
            self.gw.prepare_details_request(req_id)
            pending[req_id] = i
            events[req_id]  = self.gw._details_events[req_id]

            contract = self._option_contract(
                leg.strike, leg.right, leg.expiry, trading_class
            )
            self.gw.reqContractDetails(req_id, contract)

            requests_sent += 1
            if requests_sent % self.MAX_REQ_PER_SECOND == 0:
                time.sleep(1.0)

        if not pending:
            log.debug("All legs already have con_ids")
            return

        log.info(f"Resolving {len(pending)} con_ids …")
        deadline = time.time() + timeout

        for req_id, leg_idx in pending.items():
            remaining = deadline - time.time()
            if remaining <= 0:
                log.warning(f"con_id resolution timed out (leg {leg_idx})")
                break
            events[req_id].wait(timeout=remaining)

            details_list = self.gw.contract_details.get(req_id, [])
            if details_list:
                cd = details_list[0]
                legs[leg_idx].con_id        = cd.contract.conId
                legs[leg_idx].trading_class = cd.contract.tradingClass
                log.debug(f"  {legs[leg_idx].right.value}{legs[leg_idx].strike} "
                          f"→ con_id={legs[leg_idx].con_id}")
            else:
                log.warning(f"No contract details for leg {leg_idx} "
                            f"({legs[leg_idx]})")

        resolved = sum(1 for l in legs if l.con_id > 0)
        log.info(f"Resolved {resolved}/{len(legs)} con_ids")

    # =========================================================================
    # Snapshot prices + Greeks
    # =========================================================================

    # IBKR enforces a hard 50 messages/second API rate limit and a default
    # 100 concurrent market data line limit per account. These are enforced
    # here as defense-in-depth — the strategy layer should already be
    # sending small candidate batches, but this guards against any caller
    # (including future strategies) sending an unbounded leg list and
    # silently getting zero data back, the way "Priced 0/478 legs" did.
    MAX_REQ_PER_SECOND   = 40    # stay under IBKR's 50/sec with margin
    MAX_CONCURRENT_LINES = 90    # stay under IBKR's default 100-line cap

    def snapshot_prices_and_greeks(
        self,
        legs: list[OptionLeg],
        timeout: float = 15.0,
        spot: Optional[float] = None,
    ):
        """
        Populate bid/ask AND delta/gamma/theta/vega for each leg in-place.

        IBKR's streamed Greeks are PRIMARY; our own local Black-Scholes
        calculation (greeks.py) is a FALLBACK used per-leg whenever IBKR's
        don't arrive in time. When both are available for a leg, they are
        logged side-by-side for comparison (and the discrepancy is flagged
        if it's large), but IBKR's value is what gets used — IBKR's model
        reflects the market's actual priced-in skew and term structure,
        which a flat-vol Black-Scholes fallback cannot replicate exactly.

        This requires STREAMING market data (snapshot=False), since IBKR
        rejects snapshot=True combined with a non-empty genericTickList
        (Greeks require generic tick "100"). Streaming subscriptions must
        be explicitly cancelled afterward — they don't auto-close like
        snapshots do.

        Args:
            spot: current SPX/underlying price, used only for the LOCAL
                  fallback calculation (IBKR's own Greeks don't need it
                  from us). If not provided and a fallback computation is
                  needed, this method calls self.get_spx_spot() lazily —
                  but only once, and only if IBKR's Greeks didn't cover
                  every leg.

        SAFETY CAP: if more than MAX_CONCURRENT_LINES legs are passed in,
        only the first MAX_CONCURRENT_LINES are requested — see prior
        "Priced 0/478 legs" incident for why this cap exists.
        """
        chain = self.get_chain()
        trading_class = chain["trading_class"]

        if len(legs) > self.MAX_CONCURRENT_LINES:
            log.warning(
                f"snapshot_prices_and_greeks called with {len(legs)} legs, "
                f"exceeding the {self.MAX_CONCURRENT_LINES}-line safety cap. "
                f"Truncating to the first {self.MAX_CONCURRENT_LINES}. "
                f"The caller should narrow its candidate set instead of "
                f"relying on this cap."
            )
            legs = legs[:self.MAX_CONCURRENT_LINES]

        req_to_leg: dict[int, int] = {}   # req_id → leg index (full mapping, kept)

        for i, leg in enumerate(legs):
            req_id = self.gw.next_req_id()
            self.gw.prepare_tick_request(req_id)
            req_to_leg[req_id] = i

            contract = self._option_contract(
                leg.strike, leg.right, leg.expiry, trading_class
            )
            # snapshot=False (streaming) — required for generic tick "100"
            # (Greeks). Snapshot mode rejects any non-empty genericTickList.
            self.gw.reqMktData(req_id, contract, GREEK_TICK, False, False, [])

            # Throttle to stay under IBKR's 50 msg/sec API rate limit.
            if (i + 1) % self.MAX_REQ_PER_SECOND == 0:
                time.sleep(1.0)

        log.info(f"Streaming Greeks+price requested for {len(legs)} legs …")

        # Poll directly rather than waiting on _tick_events: that event is
        # only ever set by tickSnapshotEnd, which streaming requests never
        # receive. We wait until each req_id has both a price and a delta,
        # or until the shared deadline passes.
        outstanding = set(req_to_leg.keys())
        deadline = time.time() + timeout
        while time.time() < deadline and outstanding:
            still_outstanding = set()
            for req_id in outstanding:
                ticks  = self.gw.tick_data.get(req_id, {})
                greeks = self.gw.greek_data.get(req_id, {})
                has_price = ("bid" in ticks and "ask" in ticks) or "last" in ticks
                has_delta = "delta" in greeks
                if not (has_price and has_delta):
                    still_outstanding.add(req_id)
            outstanding = still_outstanding
            if outstanding:
                time.sleep(0.2)

        if outstanding:
            log.info(
                f"{len(outstanding)}/{len(legs)} leg(s) did not get IBKR "
                f"Greeks within {timeout}s — local Black-Scholes fallback "
                f"will be used for those"
            )

        # ── Apply IBKR data (price always; Greeks where available) ───────────
        for req_id, leg_idx in req_to_leg.items():
            ticks  = self.gw.tick_data.get(req_id, {})
            greeks = self.gw.greek_data.get(req_id, {})

            leg = legs[leg_idx]
            leg.bid  = ticks.get("bid",  0.0)
            leg.ask  = ticks.get("ask",  0.0)
            leg.last = ticks.get("last", 0.0)

            if "delta" in greeks:
                leg.delta = greeks.get("delta", 0.0)
                leg.gamma = greeks.get("gamma", 0.0)
                leg.theta = greeks.get("theta", 0.0)
                leg.vega  = greeks.get("vega",  0.0)
                leg.iv    = greeks.get("iv",    0.0)
                leg.delta_source = "ibkr"

            self.gw.cancelMktData(req_id)

        priced = sum(1 for l in legs if l.has_price)
        ibkr_greeked = sum(1 for l in legs if l.delta_source == "ibkr")
        log.info(f"Priced {priced}/{len(legs)} legs  "
                 f"(IBKR Greeks: {ibkr_greeked}/{len(legs)})")

        # ── Local fallback + comparison ───────────────────────────────────────
        # Compute the local Black-Scholes value for EVERY priced leg — not
        # just the ones missing IBKR Greeks — so a side-by-side comparison
        # is possible whenever both numbers exist. The local value never
        # overwrites a leg that already has leg.delta_source == "ibkr"
        # unless IBKR genuinely never supplied one.
        legs_needing_fallback = [l for l in legs if l.has_price]
        if legs_needing_fallback:
            if spot is None:
                spot = self.get_spx_spot()
            time_years = time_to_expiry_years(legs[0].expiry)

            local_computed = 0
            fallback_used  = 0
            for leg in legs_needing_fallback:
                g = greeks_from_market_price(
                    market_price=leg.mid,
                    spot=spot,
                    strike=leg.strike,
                    time_years=time_years,
                    right=leg.right.value,
                )
                if g is None:
                    if leg.delta_source != "ibkr":
                        log.debug(
                            f"  {leg.right.value}{leg.strike}: local IV solve "
                            f"failed AND no IBKR delta — leg has no usable "
                            f"Greeks at all"
                        )
                    continue

                leg.delta_local = g.delta
                local_computed += 1

                if leg.delta_source == "ibkr":
                    # Both available — log comparison, keep IBKR's value.
                    diff = abs(leg.delta - g.delta)
                    level = log.warning if diff > 0.05 else log.debug
                    level(
                        f"  {leg.right.value}{leg.strike}: Δ_ibkr={leg.delta:.3f} "
                        f"vs Δ_local={g.delta:.3f}  diff={diff:.3f}"
                        + ("  ⚠ LARGE DISCREPANCY" if diff > 0.05 else "")
                    )
                else:
                    # IBKR never supplied a delta for this leg — use local.
                    leg.delta = g.delta
                    leg.gamma = g.gamma
                    leg.theta = g.theta
                    leg.vega  = g.vega
                    leg.iv    = g.iv
                    leg.delta_source = "local"
                    fallback_used += 1
                    log.debug(
                        f"  {leg.right.value}{leg.strike}: no IBKR delta — "
                        f"using local Δ={g.delta:.3f} (IV={g.iv:.3f})"
                    )

            log.info(
                f"Local Black-Scholes computed for {local_computed}/"
                f"{len(legs_needing_fallback)} priced legs "
                f"({fallback_used} used as fallback where IBKR had none, "
                f"{local_computed - fallback_used} kept for comparison only)"
            )

    def refresh_leg_prices(self, legs: list[OptionLeg], timeout: float = 10.0):
        """
        Lightweight price-only refresh (no Greeks) for monitor loop.
        Reuses same mechanism but skips greek tick to reduce latency.
        """
        chain         = self.get_chain()
        trading_class = chain["trading_class"]
        pending: dict[int, int] = {}

        for i, leg in enumerate(legs):
            req_id = self.gw.next_req_id()
            self.gw.prepare_tick_request(req_id)
            pending[req_id] = i

            contract = self._option_contract(
                leg.strike, leg.right, leg.expiry, trading_class
            )
            self.gw.reqMktData(req_id, contract, "", True, False, [])

        deadline = time.time() + timeout
        for req_id, leg_idx in pending.items():
            remaining = deadline - time.time()
            if remaining > 0:
                self.gw.wait_for_tick(req_id, timeout=remaining)

            ticks = self.gw.tick_data.get(req_id, {})
            leg   = legs[leg_idx]
            leg.bid  = ticks.get("bid",  leg.bid)
            leg.ask  = ticks.get("ask",  leg.ask)
            leg.last = ticks.get("last", leg.last)

    def refresh_spread_prices(self, spread, timeout: float = 10.0):
        """Refresh prices for both legs of a VerticalSpread."""
        self.refresh_leg_prices(
            [spread.short_leg, spread.long_leg], timeout=timeout
        )

    # =========================================================================
    # Contract builders (private)
    # =========================================================================

    def _spx_index_contract(self) -> Contract:
        c = Contract()
        c.symbol   = self.symbol
        c.secType  = "IND"
        c.currency = "USD"
        c.exchange = "CBOE"
        return c

    def _option_contract(
        self,
        strike: float,
        right: OptionRight,
        expiry: str,
        trading_class: str
    ) -> Contract:
        c = Contract()
        c.symbol     = self.symbol
        c.secType    = "OPT"
        c.currency   = "USD"
        c.exchange   = "SMART"
        c.strike     = strike
        c.right      = right.value          # "C" or "P"
        c.lastTradeDateOrContractMonth = expiry
        c.multiplier = str(self.multiplier)
        c.tradingClass = trading_class
        return c
