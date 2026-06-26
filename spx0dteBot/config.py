"""
Iron Condor Configuration
Edit this file to tune strategy parameters without touching trading logic.
"""

# ── Connection ────────────────────────────────────────────────────────────────
HOST        = "192.168.1.116"
PORT        = 4002          # 4002 = IB Gateway PAPER  |  7497 = TWS PAPER
CLIENT_ID   = 1             # unique per running instance

# ── Strategy ──────────────────────────────────────────────────────────────────
DELTA_MIN        = 0.10     # short strike minimum delta (absolute)
DELTA_MAX        = 0.15     # short strike maximum delta (absolute)
WING_WIDTH       = 30       # points between short & long strike
QUANTITY         = 1        # number of spreads per side

# Premium targets (in USD, 1 contract = 100 shares)
TARGET_PREM_MIN  = 200      # total net credit floor
TARGET_PREM_MAX  = 300      # total net credit ceiling
SIDE_PREM_MIN    = 100      # per-side credit floor
SIDE_PREM_MAX    = 150      # per-side credit ceiling

# ── Exit rules ────────────────────────────────────────────────────────────────
TAKE_PROFIT_PRICE = 0.05    # close when short leg mark ≤ this
# Stop loss: triggered when unrealised loss on either spread ≥ total credit collected

# ── Time gates (24h, ET) ──────────────────────────────────────────────────────
ENTRY_LATEST_HOUR = 10      # don't enter after this hour …
ENTRY_LATEST_MIN  = 30      # … and minute
EXIT_FORCE_HOUR   = 15      # force-close at …
EXIT_FORCE_MIN    = 45      # … 15:45 ET

# ── Polling ───────────────────────────────────────────────────────────────────
POLL_INTERVAL     = 15      # seconds between mark price polls
FILL_TIMEOUT      = 120     # seconds to wait for order fills before cancelling
