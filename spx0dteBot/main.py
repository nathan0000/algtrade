"""
main.py — Entry point for the SPX options auto-trader.

Wires together:
    IBGateway       ← connection + callbacks
    MarketData      ← uses gateway
    OrderManager    ← uses gateway
    IronCondorStrategy ← uses MarketData + OrderManager

To add a new strategy, import it here and swap the strategy class.
"""

import logging
import sys
import signal
import threading

from helpers.gateway               import IBGateway
from helpers.market_data           import MarketData
from helpers.order_manager         import OrderManager
from helpers.strategy_iron_condor  import IronCondorStrategy, IronCondorConfig

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-14s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("spx_trader.log"),
    ],
)
log = logging.getLogger("Main")

# ── Connection settings ───────────────────────────────────────────────────────
HOST      = "192.168.1.116"
PORT      = 4002    # IB Gateway paper; use 4001 for live
CLIENT_ID = 1

# ── Strategy configuration ────────────────────────────────────────────────────
CONFIG = IronCondorConfig(
    delta_min   = 0.10,
    delta_max   = 0.15,
    wing_width  = 30.0,

    total_credit_min = 200.0,
    total_credit_max = 300.0,
    side_credit_min  = 100.0,
    side_credit_max  = 150.0,

    take_profit_short_leg_price = 0.05,

    entry_cutoff_hour = 10,
    entry_cutoff_min  = 30,
    force_close_hour  = 15,
    force_close_min   = 45,

    quantity          = 1,
    poll_interval_sec = 15.0,
    fill_timeout_sec  = 120.0,

    enter_call_side = True,
    enter_put_side  = True,
)


def main():
    # ── Instantiate layers ────────────────────────────────────────────────────
    gateway  = IBGateway(HOST, PORT, CLIENT_ID)
    md       = MarketData(gateway, symbol="SPX", multiplier=100)
    om       = OrderManager(gateway, multiplier=100)
    strategy = IronCondorStrategy(md, om, CONFIG)

    # ── Graceful shutdown on Ctrl-C ───────────────────────────────────────────
    def _shutdown(sig, frame):
        log.warning("Interrupt received — requesting strategy stop")
        strategy.request_stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Connect ───────────────────────────────────────────────────────────────
    if not gateway.connect_and_run(timeout=15.0):
        log.error("Failed to connect to IB Gateway on "
                  f"{HOST}:{PORT} — is it running?")
        sys.exit(1)

    # ── Run strategy (blocking) ───────────────────────────────────────────────
    try:
        strategy.run()
    finally:
        gateway.safe_disconnect()
        log.info("Done.")


if __name__ == "__main__":
    main()
