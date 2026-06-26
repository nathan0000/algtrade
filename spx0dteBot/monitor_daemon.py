"""
monitor_daemon.py — Persistent process that manages exits for ALL open
iron condor positions, regardless of which entry_runner.py invocation
created them.

Run this ONCE, continuously, e.g. as its own launchd job with
KeepAlive=true (see com.spxtrader.monitor.plist). It:

  1. Connects to IB Gateway with a single, fixed client_id.
  2. Polls PositionStore every poll_interval seconds for new OPEN positions
     that don't yet have an active monitor thread.
  3. Spins up one IronCondorStrategy + monitor thread per discovered
     position, each managing take-profit / stop-loss / EOD-close
     independently (same logic as the interactive single-shot run).
  4. Writes position state back to PositionStore as spreads close, so
     entry_runner.py instances (and you) can inspect status from the
     shared JSON file.
  5. Forces all remaining open positions closed at force_close time and
     exits cleanly — useful if you want the daemon itself to be
     restarted fresh each trading day rather than running 24/7.

This process owns the IBKR connection for monitoring; entry_runner.py
uses SEPARATE short-lived connections with different client_ids, so the
two never collide.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from spx_trader.gateway              import IBGateway
from spx_trader.market_data          import MarketData
from spx_trader.order_manager        import OrderManager
from spx_trader.strategy_iron_condor import IronCondorStrategy, IronCondorConfig
from spx_trader.position_store       import PositionStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-14s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("monitor_daemon.log"),
    ],
)
log = logging.getLogger("MonitorDaemon")

HOST      = "127.0.0.1"
PORT      = 4002
CLIENT_ID = 1     # fixed; reserved exclusively for this daemon

ET_ZONE = ZoneInfo("America/New_York")

# Same exit rules as the interactive config — entry-related fields are
# irrelevant here since this daemon never calls enter_condor().
CONFIG = IronCondorConfig(
    take_profit_short_leg_price = 0.05,
    force_close_hour = 15,
    force_close_min  = 45,
    poll_interval_sec = 15.0,
)

DISCOVERY_INTERVAL_SEC = 20.0   # how often to check PositionStore for new work


class MonitorDaemon:
    def __init__(self):
        self.gateway = IBGateway(HOST, PORT, CLIENT_ID)
        self.md      = MarketData(self.gateway, symbol="SPX", multiplier=100)
        self.om      = OrderManager(self.gateway, multiplier=100)
        self.store   = PositionStore()

        # position_id → (IronCondorStrategy, threading.Thread)
        self._active: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def start(self):
        if not self.gateway.connect_and_run(timeout=15.0):
            log.error(f"Failed to connect to IB Gateway {HOST}:{PORT} "
                      f"(client_id={CLIENT_ID})")
            sys.exit(1)
        log.info("Monitor daemon connected. Watching for open positions …")
        self._discovery_loop()

    def stop(self):
        log.info("Stop requested — signalling all monitor threads")
        self._stop_event.set()
        with self._lock:
            for position_id, (strategy, _) in self._active.items():
                strategy.request_stop()

    # =========================================================================
    # Discovery loop — runs in main thread
    # =========================================================================

    def _discovery_loop(self):
        while not self._stop_event.is_set():
            # ── Connection health check ───────────────────────────────────────
            # IBKR explicitly documents that the server-side connection WILL
            # drop at least once a day for scheduled maintenance, separate
            # from any system bulletin/status message delivered over an
            # otherwise-healthy socket. If that happens while positions are
            # open, we must reconnect rather than silently spin issuing
            # requests against a dead connection — that would mean
            # stop-loss/take-profit monitoring stops working with no
            # visible failure until the process eventually crashes.
            if not self.gateway.is_connected_and_alive():
                with self._lock:
                    open_position_count = len(self._active)
                log.warning(
                    f"IB Gateway connection lost. "
                    f"{open_position_count} position(s) currently being "
                    f"monitored — attempting reconnect …"
                )
                if not self.gateway.reconnect(timeout=15.0, max_attempts=5,
                                              backoff_sec=10.0):
                    log.error(
                        "Could not reconnect to IB Gateway after multiple "
                        "attempts. Open positions are NOT being monitored "
                        "for stop-loss/take-profit while disconnected. "
                        "Will keep retrying every discovery cycle."
                    )
                    time.sleep(DISCOVERY_INTERVAL_SEC)
                    continue
                else:
                    log.info(
                        "Connection restored. Resuming position discovery "
                        "and monitoring."
                    )

            try:
                self._discover_new_positions()
                self._reap_finished_threads()
            except Exception:
                log.exception("Error in discovery loop")

            # EOD safety: if every known position is closed and we're past
            # force-close time, the daemon can idle quietly until next restart.
            now_et = datetime.now(ET_ZONE)
            if (now_et.hour > CONFIG.force_close_hour or
                    (now_et.hour == CONFIG.force_close_hour and
                     now_et.minute >= CONFIG.force_close_min + 5)):
                with self._lock:
                    still_running = len(self._active)
                if still_running == 0:
                    log.info("Past force-close window and no active monitors — "
                             "daemon idling. (Restart daily via launchd.)")

            time.sleep(DISCOVERY_INTERVAL_SEC)

    def _discover_new_positions(self):
        open_positions = self.store.list_open_positions()
        for position_id, condor in open_positions:
            with self._lock:
                already_running = position_id in self._active
            if already_running:
                continue

            if not condor.active_spreads():
                continue  # nothing to monitor (e.g. both sides failed entry)

            log.info(f"Discovered new position to monitor: {position_id}")
            self._start_monitor_thread(position_id, condor)

    def _start_monitor_thread(self, position_id: str, condor):
        strategy = IronCondorStrategy(self.md, self.om, CONFIG)
        strategy.condor = condor

        def _run():
            try:
                strategy.run_monitor_only(condor)
            except Exception:
                log.exception(f"Monitor thread for {position_id} crashed")
            finally:
                # Persist final state and mark closed
                self.store.update_position(position_id, condor)
                if condor.all_closed:
                    self.store.mark_closed(position_id)
                    log.info(f"Position {position_id} fully closed and persisted")

        thread = threading.Thread(
            target=_run, name=f"monitor-{position_id}", daemon=True
        )
        with self._lock:
            self._active[position_id] = (strategy, thread)
        thread.start()

        # Periodically persist live state from a lightweight side-thread
        # so PositionStore reflects current marks even before close.
        self._start_persistence_pinger(position_id, condor, strategy)

    def _start_persistence_pinger(self, position_id, condor, strategy):
        def _ping():
            while not strategy._stop_event.is_set() and not condor.all_closed:
                time.sleep(CONFIG.poll_interval_sec)
                try:
                    self.store.update_position(position_id, condor)
                except Exception:
                    log.exception(f"Failed to persist position {position_id}")

        threading.Thread(
            target=_ping, name=f"persist-{position_id}", daemon=True
        ).start()

    def _reap_finished_threads(self):
        with self._lock:
            finished = [
                pid for pid, (strat, thread) in self._active.items()
                if not thread.is_alive()
            ]
            for pid in finished:
                log.info(f"Monitor thread for {pid} finished — removing from active set")
                del self._active[pid]


def main():
    daemon = MonitorDaemon()

    import signal
    def _shutdown(sig, frame):
        log.warning("Signal received — shutting down monitor daemon")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    daemon.start()


if __name__ == "__main__":
    main()
