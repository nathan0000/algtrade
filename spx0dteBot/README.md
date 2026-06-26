# Scheduling: launchd setup for 11:30–14:30 ET entry window

## Why launchd, not cron

macOS's cron daemon is deprecated and doesn't reliably wake from sleep or
handle missed runs. `launchd` is Apple's native replacement, used by all
system schedulers, and handles sleep/wake and crash recovery correctly.

## Why this is two separate jobs, not one

You asked for: "always attempt a fresh entry every 30 min regardless of
existing position." That means a NEW position can be opened every 30
minutes inside the window, independent of whatever's already open. But
your `IronCondorStrategy.run()` blocks for the entire lifetime of a
position (until take-profit/stop-loss/EOD close) — if the 30-min trigger
called that directly, a position opened at 11:30 would still be running
its monitor loop at 12:00, and the next trigger would try to open a
**second** IBKR connection with the same client_id, which IBKR rejects.

So entry and monitoring are split into two independent processes:

| Process | Job | Connection | Lifetime |
|---|---|---|---|
| `entry_runner.py` | Finds strikes, places orders, exits | New client_id per run (100–199 band) | Seconds — exits right after fills |
| `monitor_daemon.py` | Watches ALL open positions for TP/SL/EOD | Fixed client_id (1), persistent | Runs continuously, all day |

They hand off through a shared JSON file (`~/.spx_trader/positions.json`),
written by `entry_runner.py` and read/updated by `monitor_daemon.py`. A
file lock (`fcntl.flock`) prevents corruption if both touch the file at
the same instant.

```
launchd (every 5 min)                launchd (persistent, KeepAlive)
        │                                      │
        ▼                                      ▼
 entry_runner.py                       monitor_daemon.py
 (self-gates to 11:30-14:30 ET,        (always running, polls
  exits in ms otherwise)                positions.json every 20s)
        │                                      ▲
        ▼                                      │
   places orders,                     discovers new positions,
   writes to ──────────────────────►  manages TP/SL/EOD close,
   positions.json                     writes status back
```

## Why the entry job fires every 5 minutes, not exactly at 11:30/12:00/...

Your Mac's clock is **Australia/Sydney**. The desired window is
**11:30–14:30 America/New_York**. The gap between these timezones is
**not constant** — it's 14h or 16h depending on which hemisphere is
currently in DST (they're opposite seasons). A `launchd`
`StartCalendarInterval` only understands the host's local wall-clock
time, with no timezone math. Hardcoding Sydney-time slots would silently
fire at the wrong ET time for about half the year.

The fix: `launchd` triggers `entry_runner.py` every 5 minutes, all day,
every day. The script itself computes the current `America/New_York` time
using Python's `zoneinfo` (which correctly tracks both the US and
Australian DST calendars) and only proceeds with an entry if:

1. It's a weekday, **and**
2. The current ET time is within `11:30–14:30`, **and**
3. The current ET time is within 4 minutes of a `:00` or `:30` boundary
   (tolerates scheduler jitter)

Every other invocation is a no-op that exits in milliseconds — no IBKR
connection is even attempted.

## Files

```
spx_trader/
├── entry_runner.py         # Entry-only — run by launchd every 5 min
├── monitor_daemon.py       # Persistent exit manager — run once, KeepAlive
├── position_store.py       # JSON handoff between the two processes
└── launchd/
    ├── com.spxtrader.entry.plist      # StartInterval=300s, KeepAlive=false
    ├── com.spxtrader.monitor.plist    # RunAtLoad+KeepAlive=true
    └── install_launchd.sh             # install / uninstall / status / logs
```

## Setup

### 1. Edit the plists with your real paths

Both plists have `TODO` comments marking three things to fix:

```xml
<key>ProgramArguments</key>
<array>
    <string>/usr/bin/python3</string>                                  <!-- 1: your python3 -->
    <string>/Users/nathanwang/algtrade/spx0dteBot/spx_trader/entry_runner.py</string>  <!-- 2: script path -->
</array>
<key>WorkingDirectory</key>
<string>/Users/nathanwang/algtrade/spx0dteBot</string>                 <!-- 3: project root -->
```

Find your real `python3` path:

```bash
which python3
```

If you're using a virtualenv, point at that interpreter instead of system
python3 so `ibapi` resolves correctly.

### 2. Install

```bash
cd spx_trader/launchd
chmod +x install_launchd.sh
./install_launchd.sh install
```

This copies both plists into `~/Library/LaunchAgents/` and loads them with
`launchctl load`. The install script refuses to proceed if the script
paths inside the plists don't actually exist, to catch path typos before
they fail silently at 11:30 tomorrow.

### 3. Verify

```bash
./install_launchd.sh status
```

```bash
# Watch logs live
./install_launchd.sh logs
```

You should see `entry_runner.stdout.log` getting a new "Outside entry
window" line every 5 minutes outside 11:30–14:30 ET, and actual entry
attempts only inside the window.

### 4. Uninstall

```bash
./install_launchd.sh uninstall
```

## Operational notes

- **IB Gateway must already be running and logged in** before either job
  fires. launchd does not start IB Gateway for you. Make sure paper
  trading auto-login is configured if you want this to survive Mac
  restarts unattended.
- **Client ID bands**: `entry_runner.py` uses `100 + (epoch_seconds % 100)`
  — effectively a fresh ID per run. `monitor_daemon.py` always uses `1`.
  If you run the original single-shot `main.py` manually at the same
  time, change its `CLIENT_ID` to something outside `1` and `100–199` to
  avoid a collision.
- **Monitor daemon must be started once** — it doesn't get re-triggered
  every 30 min like the entry job. `RunAtLoad=true` + `KeepAlive=true`
  means launchd starts it the moment you run `install`, and relaunches it
  if it ever crashes or the connection drops and the process exits.
- **No holiday calendar** — `_market_open_today()` only checks weekday,
  not market holidays. On a holiday, `entry_runner.py` will still attempt
  to connect and will fail/no-op naturally once `get_spx_spot()` or
  `get_chain()` can't get data, but it's worth adding a real holiday
  calendar (e.g. `pandas_market_calendars`) if unattended operation on
  holidays matters to you.
- **Daily restart recommended for the monitor daemon** — `positions.json`
  accumulates closed positions forever (`list_all_positions` includes
  history). Consider a daily cron/launchd job to archive/rotate the file,
  or restart the monitor daemon each morning via `launchctl kickstart`.
