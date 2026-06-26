"""
strategy_iron_condor.py — SPX 0DTE Iron Condor strategy.

Responsibilities:
  - Define entry criteria (delta range, wing width, premium targets)
  - Select strikes from chain data
  - Coordinate entry of call and put sides independently
  - Run the monitor loop (take-profit, stop-loss, EOD close)
  - Define exit rules

Knows nothing about IBKR API specifics — delegates entirely to
MarketData and OrderManager.

To add a new strategy (e.g. straddle, strangle, naked put):
  - Create a new file alongside this one
  - Implement the same interface: __init__(md, om, config) + run()
  - The runner (main.py) instantiates whichever strategy is selected
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from .market_data  import MarketData
from .order_manager import OrderManager
from .models import (
    OptionLeg, OptionRight, VerticalSpread, IronCondor,
    SpreadSide, SpreadState, CondorState
)

log = logging.getLogger("IronCondor")


# ── Strategy configuration ────────────────────────────────────────────────────

@dataclass
class IronCondorConfig:
    # Delta targeting
    delta_min: float = 0.10
    delta_max: float = 0.15

    # Wing width (points between short and long strike)
    wing_width: float = 30.0

    # Premium targets (USD, per 1 contract = 100 shares)
    total_credit_min: float = 200.0
    total_credit_max: float = 300.0
    side_credit_min:  float = 100.0
    side_credit_max:  float = 150.0

    # Exit rules
    take_profit_short_leg_price: float = 0.05   # close when short ≤ this
    # stop loss: either side's unrealised loss ≥ total_credit_collected

    # Time gates (hour, minute in local/ET time)
    entry_cutoff_hour: int = 10
    entry_cutoff_min:  int = 30
    force_close_hour:  int = 15
    force_close_min:   int = 45

    # Operational
    quantity:          int   = 1
    poll_interval_sec: float = 15.0
    fill_timeout_sec:  float = 120.0

    # Whether to enter both sides or just one
    enter_call_side: bool = True
    enter_put_side:  bool = True


# ── Strategy ──────────────────────────────────────────────────────────────────

class IronCondorStrategy:
    """
    SPX 0DTE Iron Condor.

    External interface:
        strategy = IronCondorStrategy(market_data, order_manager, config)
        strategy.run()           # blocking; runs full lifecycle
        strategy.request_stop()  # signal monitor to stop (from another thread)
    """

    def __init__(
        self,
        market_data:   MarketData,
        order_manager: OrderManager,
        config:        Optional[IronCondorConfig] = None
    ):
        self.md     = market_data
        self.om     = order_manager
        self.config = config or IronCondorConfig()

        self.condor = IronCondor()
        self._stop_event = threading.Event()

    def request_stop(self):
        """Signal the monitor loop to exit cleanly."""
        self._stop_event.set()

    # =========================================================================
    # Main lifecycle
    # =========================================================================

    def run(self):
        """
        Full strategy lifecycle — blocking.
        Returns when the position is closed or an unrecoverable error occurs.

        For decoupled entry-only / monitor-only operation (e.g. a cron-style
        entry trigger separate from a persistent monitor daemon), call
        enter_condor() and run_monitor_only() independently instead.
        """
        condor = self.enter_condor()
        if condor is None or not condor.active_spreads():
            return
        self.run_monitor_only(condor)

    def enter_condor(self) -> Optional[IronCondor]:
        """
        Entry-only lifecycle: time gate → market data → strike selection →
        place call/put orders → wait for fills. Does NOT monitor or block
        on exit management.

        Returns the IronCondor (with whichever sides filled), or None if
        entry was skipped/aborted before any order was placed.

        Safe to call repeatedly (e.g. from a scheduled trigger) — each call
        is independent and uses fresh strike selection against current
        market data.
        """
        cfg = self.config
        try:
            # ── Time gate ─────────────────────────────────────────────────────
            now = datetime.now()
            cutoff = now.replace(
                hour=cfg.entry_cutoff_hour,
                minute=cfg.entry_cutoff_min,
                second=0, microsecond=0
            )
            if now >= cutoff:
                log.warning(
                    f"Past entry cutoff {cfg.entry_cutoff_hour}:"
                    f"{cfg.entry_cutoff_min:02d} — skipping"
                )
                return None

            # ── Market data ───────────────────────────────────────────────────
            spot   = self.md.get_spx_spot()
            expiry = self.md.today_expiry()

            if not self.md.validate_expiry(expiry):
                log.error(
                    f"No 0DTE expiry {expiry} in chain. "
                    f"First available: {self.md.available_expirations()[:3]}"
                )
                return None

            # ── Strike selection ──────────────────────────────────────────────
            condor = self._select_strikes(spot, expiry)
            if condor is None:
                log.error("No valid iron condor found — aborting")
                return None
            self.condor = condor
            log.info(f"\n{condor.summary()}")

            # ── Enter call side ───────────────────────────────────────────────
            if cfg.enter_call_side and condor.call_spread:
                call_filled = self._enter_side(condor.call_spread)
                if not call_filled:
                    log.error("Call spread entry failed — aborting")
                    if cfg.enter_put_side and condor.put_spread:
                        self.om.cancel_spread_entry(condor.put_spread)
                    return condor

            # ── Enter put side ────────────────────────────────────────────────
            if cfg.enter_put_side and condor.put_spread:
                put_filled = self._enter_side(condor.put_spread)
                if not put_filled:
                    log.error("Put spread entry failed")
                    # Call side already filled — we now have a one-sided position
                    log.warning(
                        "Continuing with call-side-only position. "
                        "The monitor daemon will manage whatever filled."
                    )

            # ── Update condor state ───────────────────────────────────────────
            if condor.call_spread and condor.call_spread.is_active:
                condor.state = CondorState.OPEN
            if condor.put_spread and condor.put_spread.is_active:
                condor.state = CondorState.OPEN

            if not condor.active_spreads():
                log.error("No active spreads after entry attempt")
                return condor

            log.info(
                f"Entry complete. Total credit = "
                f"${condor.total_credit_dollars:.0f}."
            )
            return condor

        except Exception:
            log.exception("Unhandled error during entry")
            return None

    def run_monitor_only(self, condor: IronCondor):
        """
        Monitor-only lifecycle for an already-entered condor — blocking.
        Use this from a persistent monitor process that owns one or more
        IronCondor positions and manages their exits independently of
        whatever process performed entry.
        """
        if not condor.active_spreads():
            log.warning("run_monitor_only called with no active spreads")
            return
        log.info(f"Starting monitor loop for condor "
                 f"(total credit=${condor.total_credit_dollars:.0f}) …")
        self._monitor(condor)

    # =========================================================================
    # Strike selection
    # =========================================================================

    def _select_strikes(self, spot: float, expiry: str) -> Optional[IronCondor]:
        """
        Find call and put spreads that satisfy all criteria.

        Algorithm:
          1. Collect all OTM call strikes above spot (sorted ascending)
          2. Collect all OTM put  strikes below spot (sorted descending)
          3. For each candidate short strike, check if short+width exists
          4. Resolve con_ids and fetch prices + greeks for all candidates
          5. Walk inward from far-OTM to find first strike within delta band
             that produces a credit in [side_credit_min, side_credit_max]
        """
        cfg    = self.config
        chain  = self.md.get_chain()
        strikes = chain["strikes"]

        # ── Candidate strikes ─────────────────────────────────────────────────
        # Keep strikes within ±8% of spot (plenty of room, avoids deep OTM noise)
        band = spot * 0.08

        call_short_candidates = sorted(
            [s for s in strikes if s > spot and s <= spot + band]
        )
        put_short_candidates = sorted(
            [s for s in strikes if s < spot and s >= spot - band],
            reverse=True
        )

        if not call_short_candidates or not put_short_candidates:
            log.error("No candidate strikes within ±8% of spot")
            return None

        # ── Build candidate leg pairs ─────────────────────────────────────────
        call_pairs: list[tuple[OptionLeg, OptionLeg]] = []
        for s in call_short_candidates:
            long_s = s + cfg.wing_width
            if long_s in strikes:
                call_pairs.append((
                    OptionLeg("SPX", expiry, s,      OptionRight.CALL),
                    OptionLeg("SPX", expiry, long_s, OptionRight.CALL),
                ))

        put_pairs: list[tuple[OptionLeg, OptionLeg]] = []
        for s in put_short_candidates:
            long_s = s - cfg.wing_width
            if long_s in strikes:
                put_pairs.append((
                    OptionLeg("SPX", expiry, s,      OptionRight.PUT),
                    OptionLeg("SPX", expiry, long_s, OptionRight.PUT),
                ))

        if not call_pairs:
            log.error(f"No call spreads with {cfg.wing_width:.0f}-point wings found")
            return None
        if not put_pairs:
            log.error(f"No put spreads with {cfg.wing_width:.0f}-point wings found")
            return None

        # ── Batch resolve + price all candidate legs ──────────────────────────
        all_legs = [l for pair in call_pairs + put_pairs for l in pair]

        log.info(f"Resolving {len(all_legs)} candidate legs …")
        self.md.resolve_conids(all_legs)
        self.md.snapshot_prices_and_greeks(all_legs)

        # ── Select best call spread ───────────────────────────────────────────
        best_call = self._pick_best_spread(
            call_pairs, SpreadSide.CALL, cfg
        )
        best_put = self._pick_best_spread(
            put_pairs, SpreadSide.PUT, cfg
        )

        if not best_call and not best_put:
            log.error("No spreads met delta + premium criteria")
            return None

        # ── Validate combined premium ─────────────────────────────────────────
        if best_call and best_put:
            total = best_call.credit_dollars + best_put.credit_dollars
            if not (cfg.total_credit_min <= total <= cfg.total_credit_max):
                log.warning(
                    f"Combined credit ${total:.0f} outside target range "
                    f"${cfg.total_credit_min:.0f}–${cfg.total_credit_max:.0f}. "
                    "Proceeding anyway (enter_call_side / enter_put_side flags "
                    "control which sides to trade)."
                )

        condor = IronCondor(
            call_spread = best_call,
            put_spread  = best_put,
        )
        return condor

    def _pick_best_spread(
        self,
        pairs: list[tuple[OptionLeg, OptionLeg]],
        side: SpreadSide,
        cfg: IronCondorConfig
    ) -> Optional[VerticalSpread]:
        """
        Walk candidate pairs (closest to ATM first for call side,
        highest put first for put side) and return first that meets:
          - Short leg delta within [delta_min, delta_max]
          - Net credit within [side_credit_min, side_credit_max]
        """
        for short_leg, long_leg in pairs:
            delta = abs(short_leg.delta)
            credit = round(
                (short_leg.mid - long_leg.mid) * self.md.multiplier, 2
            )

            log.debug(
                f"  {side.value} {short_leg.strike}/{long_leg.strike}  "
                f"Δ={delta:.3f}  credit=${credit:.0f}  "
                f"(short bid={short_leg.bid} ask={short_leg.ask}  "
                f"long bid={long_leg.bid} ask={long_leg.ask})"
            )

            # Skip legs where we couldn't get prices or delta
            if not short_leg.has_price or not long_leg.has_price:
                log.debug(f"    → skipped (no price)")
                continue
            if delta == 0:
                log.debug(f"    → skipped (no delta)")
                continue

            if not (cfg.delta_min <= delta <= cfg.delta_max):
                log.debug(f"    → delta {delta:.3f} outside range")
                continue

            if not (cfg.side_credit_min <= credit <= cfg.side_credit_max):
                log.debug(f"    → credit ${credit:.0f} outside range")
                continue

            spread = VerticalSpread(
                side      = side,
                short_leg = short_leg,
                long_leg  = long_leg,
                quantity  = cfg.quantity,
                multiplier = self.md.multiplier,
            )
            log.info(
                f"✓ Selected {side.value} spread  "
                f"{short_leg.strike}/{long_leg.strike}  "
                f"Δ={delta:.3f}  credit=${credit:.0f}"
            )
            return spread

        log.warning(f"No {side.value} spread met all criteria")
        return None

    # =========================================================================
    # Entry
    # =========================================================================

    def _enter_side(self, spread: VerticalSpread) -> bool:
        """
        Enter one side of the iron condor.
        Returns True if filled, False if cancelled/failed.
        """
        limit = round(spread.short_leg.mid - spread.long_leg.mid, 2)
        self.om.enter_spread(
            spread,
            limit_credit = limit,
            quantity     = self.config.quantity,
            tag          = f"{spread.side.value} {spread.short_leg.strike}/"
                           f"{spread.long_leg.strike}"
        )
        return self.om.wait_for_entry_fill(
            spread, timeout=self.config.fill_timeout_sec
        )

    # =========================================================================
    # Monitor loop
    # =========================================================================

    def _monitor(self, condor: IronCondor):
        """
        Poll loop: check take-profit and stop-loss conditions every poll_interval.
        Each side is evaluated and closed independently.
        """
        cfg = self.config

        # Track which sides are still open
        call_open = (condor.call_spread is not None and
                     condor.call_spread.is_active)
        put_open  = (condor.put_spread  is not None and
                     condor.put_spread.is_active)

        while not self._stop_event.is_set():
            now = datetime.now()

            # ── EOD force close ───────────────────────────────────────────────
            if (now.hour > cfg.force_close_hour or
                    (now.hour == cfg.force_close_hour and
                     now.minute >= cfg.force_close_min)):
                log.info("⏰ EOD force-close triggered")
                if call_open:
                    self._close_side(condor.call_spread, "EOD")
                    call_open = False
                if put_open:
                    self._close_side(condor.put_spread, "EOD")
                    put_open = False
                break

            # ── Refresh mark prices for all active legs ───────────────────────
            active_spreads = condor.active_spreads()
            for spread in active_spreads:
                self.md.refresh_spread_prices(spread)

            # ── Per-side checks ───────────────────────────────────────────────
            if call_open and condor.call_spread.is_active:
                action = self._check_exit(
                    condor.call_spread, condor.total_credit_dollars
                )
                if action:
                    self._close_side(condor.call_spread, action)
                    call_open = False

            if put_open and condor.put_spread.is_active:
                action = self._check_exit(
                    condor.put_spread, condor.total_credit_dollars
                )
                if action:
                    self._close_side(condor.put_spread, action)
                    put_open = False

            # ── All closed? ───────────────────────────────────────────────────
            if not call_open and not put_open:
                condor.state = CondorState.CLOSED
                log.info("All spreads closed. Strategy complete.")
                break

            self._log_status(condor, call_open, put_open)
            time.sleep(cfg.poll_interval_sec)

    def _check_exit(
        self,
        spread: VerticalSpread,
        total_credit_dollars: float
    ) -> Optional[str]:
        """
        Evaluate exit conditions for one spread.
        Returns a string reason if the spread should be closed, else None.

        Exit conditions (checked in priority order):
          1. Take profit: short leg mark ≤ take_profit_short_leg_price
          2. Stop loss:   unrealised loss ≥ total credit collected
        """
        cfg = self.config
        short_mid = spread.short_leg.mid
        mark      = spread.current_mark()
        loss      = spread.unrealised_loss_dollars()

        # Take profit: the short leg has decayed to near-zero
        if short_mid > 0 and short_mid <= cfg.take_profit_short_leg_price:
            log.info(
                f"✓ TAKE PROFIT {spread.side.value}  "
                f"short leg mid=${short_mid:.3f} ≤ ${cfg.take_profit_short_leg_price}"
            )
            return "TAKE_PROFIT"

        # Stop loss: losing more than total credit collected on this side
        if loss >= total_credit_dollars:
            log.warning(
                f"⚠ STOP LOSS {spread.side.value}  "
                f"unrealised loss=${loss:.0f} ≥ "
                f"total credit=${total_credit_dollars:.0f}"
            )
            return "STOP_LOSS"

        return None

    def _close_side(self, spread: VerticalSpread, reason: str):
        """Close one spread and wait for the fill (best-effort)."""
        log.info(f"Closing {spread.side.value} spread [{reason}]")
        result = self.om.close_spread(spread, use_market=True)
        if result:
            self.om.wait_for_close_fill(spread, timeout=30.0)

    def _log_status(self, condor: IronCondor, call_open: bool, put_open: bool):
        parts = []
        if call_open and condor.call_spread:
            s = condor.call_spread
            parts.append(
                f"CALL {s.short_leg.strike}/{s.long_leg.strike}  "
                f"short=${s.short_leg.mid:.2f}  "
                f"mark=${s.current_mark():.2f}  "
                f"loss=${s.unrealised_loss_dollars():.0f}"
            )
        if put_open and condor.put_spread:
            s = condor.put_spread
            parts.append(
                f"PUT {s.short_leg.strike}/{s.long_leg.strike}  "
                f"short=${s.short_leg.mid:.2f}  "
                f"mark=${s.current_mark():.2f}  "
                f"loss=${s.unrealised_loss_dollars():.0f}"
            )
        if parts:
            log.info("Status — " + "  |  ".join(parts))
