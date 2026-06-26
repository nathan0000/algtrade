"""
entry_runner.py — Entry-only trigger for scheduled (launchd/cron) invocation.

Designed to be invoked frequently (e.g. every 5 minutes) by a scheduler.
On each invocation it:
  1. Checks whether the current time (in America/New_York) falls inside
     the configured RTH entry window AND on a 30-minute boundary slot.
     If not, exits immediately (no-op) — this makes the script safe to
     call far more often than you actually want it to trade, which sidesteps
     all the DST/timezone-offset problems of trying to encode ET times
     directly into a Sydney-clock launchd schedule.
  2. Connects to IB Gateway with a FRESH, unique client_id (derived from
     the current time) so overlapping runs never collide on client_id.
  3. Calls strategy.enter_condor() — entry ONLY, no monitor loop, no
     blocking on exit management.
  4. Persists the resulting IronCondor to the shared PositionStore so the
     separate, persistent monitor_daemon.py can pick it up and manage exits.
  5. Disconnects and exits. Process lifetime is seconds, not hours.

This script intentionally does NOT monitor positions — see monitor_daemon.py
for the always-on process that handles take-profit / stop-loss / EOD close
for every position this script enters.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from helpers.gateway              import IBGateway
from helpers.market_data          import MarketData
from helpers.order_manager        import OrderManager
from helpers.strategy_iron_condor import IronCondorStrategy, IronCondorConfig
from helpers.position_store       import PositionStore

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-14s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/entry_runner.log"),
    ],
)
log = logging.getLogger("EntryRunner")

# ── Connection ────────────────────────────────────────────────────────────────
HOST = "192.168.1.116"
PORT = 4002   # IB Gateway paper

# Client IDs 100-199 reserved for entry-runner processes (derived per-run).
# Client ID 1 is reserved for the persistent monitor daemon (see monitor_daemon.py).
# Keeping these bands non-overlapping avoids "client id already in use" errors
# if an entry run and the monitor daemon happen to connect at the same instant.
ENTRY_CLIENT_ID_BASE  = 100
ENTRY_CLIENT_ID_RANGE = 100   # 100-199

# ── RTH entry window (America/New_York) ───────────────────────────────────────
ET_ZONE          = ZoneInfo("America/New_York")
ENTRY_WINDOW_START = (11, 00)   # (hour, minute) ET — inclusive
ENTRY_WINDOW_END    = (15, 00)   # (hour, minute) ET — inclusive
SLOT_INTERVAL_MIN   = 30         # only act on :00/:30 boundaries
SLOT_TOLERANCE_MIN  = 4          # allow scheduler jitter (e.g. launchd firing
                                  # at :01 instead of exactly :00)

# ── Strategy configuration (same targets as the interactive run) ─────────────
CONFIG = IronCondorConfig(
    delta_min   = 0.10,
    delta_max   = 0.15,
    wing_width  = 30.0,

    total_credit_min = 200.0,
    total_credit_max = 300.0,
    side_credit_min  = 100.0,
    side_credit_max  = 150.0,

    take_profit_short_leg_price = 0.05,

    # entry_cutoff_* in IronCondorConfig guards a single intraday cutoff;
    # the *window* check below is the real gate for this scheduled runner.
    entry_cutoff_hour = 23,
    entry_cutoff_min  = 59,

    force_close_hour  = 15,
    force_close_min   = 45,

    quantity          = 1,
    poll_interval_sec = 15.0,
    fill_timeout_sec  = 120.0,

    enter_call_side = True,
    enter_put_side  = True,
)


def _in_entry_window(now_et: datetime) -> bool:
    """True if now_et falls within [ENTRY_WINDOW_START, ENTRY_WINDOW_END] ET."""
    start_h, start_m = ENTRY_WINDOW_START
    end_h,   end_m   = ENTRY_WINDOW_END
    start_minutes = start_h * 60 + start_m
    end_minutes   = end_h   * 60 + end_m
    now_minutes   = now_et.hour * 60 + now_et.minute
    return start_minutes <= now_minutes <= end_minutes


def _on_slot_boundary(now_et: datetime) -> bool:
    """
    True if now_et is within SLOT_TOLERANCE_MIN minutes of a
    SLOT_INTERVAL_MIN-minute boundary (e.g. :00 or :30).

    This makes the script tolerant of scheduler jitter without needing
    launchd to fire at the exact second.
    """
    minute_of_hour = now_et.minute
    distance_to_slot = min(
        minute_of_hour % SLOT_INTERVAL_MIN,
        SLOT_INTERVAL_MIN - (minute_of_hour % SLOT_INTERVAL_MIN)
    )
    return distance_to_slot <= SLOT_TOLERANCE_MIN


def _market_open_today(now_et: datetime) -> bool:
    """
    Basic weekday check. Does NOT check US market holidays — if you need
    full holiday awareness, integrate a calendar (e.g. `pandas_market_calendars`)
    here and return False on holidays too.
    """
    return now_et.weekday() < 5   # Mon=0 .. Fri=4


def _derive_client_id() -> int:
    """
    Derive a client_id unique enough to avoid collisions between back-to-back
    entry runs, within the reserved entry-runner band.
    """
    # Use seconds-since-epoch mod range, offset into the entry band.
    # Two runs 30 minutes apart will essentially never collide;
    # even rapid manual re-runs within the same second are vanishingly
    # unlikely given this is triggered on a 30-min cadence.
    return ENTRY_CLIENT_ID_BASE + (int(time.time()) % ENTRY_CLIENT_ID_RANGE)


def main():
    now_et = datetime.now(ET_ZONE)
    log.info(f"Entry runner invoked at {now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    if not _market_open_today(now_et):
        log.info("Weekend — skipping (no holiday calendar check configured)")
        return

    if not _in_entry_window(now_et):
        log.info(
            f"Outside entry window "
            f"{ENTRY_WINDOW_START[0]:02d}:{ENTRY_WINDOW_START[1]:02d}–"
            f"{ENTRY_WINDOW_END[0]:02d}:{ENTRY_WINDOW_END[1]:02d} ET "
            f"(now {now_et.strftime('%H:%M')} ET) — skipping"
        )
        return

    if not _on_slot_boundary(now_et):
        log.info(
            f"Not within {SLOT_TOLERANCE_MIN} min of a "
            f"{SLOT_INTERVAL_MIN}-min slot boundary "
            f"(now :{now_et.minute:02d}) — skipping"
        )
        return

    log.info("Within entry window and slot boundary — proceeding with entry")

    client_id = _derive_client_id()
    gateway   = IBGateway(HOST, PORT, client_id)
    md        = MarketData(gateway, symbol="SPX", multiplier=100)
    om        = OrderManager(gateway, multiplier=100)
    strategy  = IronCondorStrategy(md, om, CONFIG)
    store     = PositionStore()

    if not gateway.connect_and_run(timeout=15.0):
        log.error(f"Failed to connect to IB Gateway {HOST}:{PORT} "
                  f"(client_id={client_id}) — is it running?")
        sys.exit(1)

    try:
        condor = strategy.enter_condor()

        if condor is None:
            log.info("No position entered this cycle.")
            return

        if not condor.active_spreads():
            log.warning("Entry attempted but no sides filled — nothing to store")
            return

        position_id = f"ic-{now_et.strftime('%Y%m%d-%H%M')}-{uuid.uuid4().hex[:6]}"
        store.add_position(condor, position_id)
        log.info(
            f"Position '{position_id}' stored for monitor daemon. "
            f"{condor.summary()}"
        )

    except Exception:
        log.exception("Entry runner failed")
    finally:
        gateway.safe_disconnect()
        log.info("Entry runner done — process exiting (no monitor loop here).")


if __name__ == "__main__":
    main()
