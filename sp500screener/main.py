"""
main.py — SP500 Swing Screener Entry Point
Usage:
  python main.py                   # run once, print results
  python main.py --schedule        # run every hour during market hours
  python main.py --symbols AAPL MSFT NVDA   # test specific symbols
  python main.py --report          # pretty-print last saved results
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
import pytz

from config import (
    LOG_FILE, OUTPUT_FILE, SCAN_INTERVAL_MINUTES,
    SYMBOL_OVERRIDE, ACCOUNT
)

ET = pytz.timezone("America/New_York")

# ── Logging setup ─────────────────────────────────────────────────────────────
# IMPORTANT: _setup_logging() is called from main() AFTER argparse so that
# --loglevel actually takes effect on every handler.
#
# Two log files are always written:
#   screener.log        — INFO+ (clean summary of every scan)
#   screener_debug.log  — DEBUG (full IBKR wire-level trace, always on)
#
# The debug log is essential for diagnosing "no historical data" problems
# because the [BARS]/[ERR]/[PACE] tags in ibkr_client.py log at DEBUG level.
# Without this file you would need --loglevel DEBUG just to see them.
#
# To diagnose historical data issues, look for these patterns:
#   grep "\[ERR \]" screener_debug.log   # IBKR error callbacks
#   grep "\[BARS\]" screener_debug.log   # bar fetch lifecycle
#   grep "TIMEOUT\|EMPTY\|FAIL" screener_debug.log

DEBUG_LOG_FILE = LOG_FILE.replace(".log", "_debug.log")
_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _setup_logging(console_level: str = "INFO") -> None:
    """
    Configure root logger with three handlers:
      1. StreamHandler (stdout)    — console_level (INFO by default, DEBUG with --loglevel DEBUG)
      2. FileHandler screener.log  — INFO+, append mode
      3. FileHandler screener_debug.log — DEBUG+, always on regardless of console_level

    Must be called after argparse so --loglevel is known.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)      # root must pass everything; handlers filter

    # Remove any handlers added by a prior basicConfig call
    for h in root.handlers[:]:
        root.removeHandler(h)

    fmt = logging.Formatter(_FMT)

    # 1. Console — respects --loglevel
    console_h = logging.StreamHandler(sys.stdout)
    console_h.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console_h.setFormatter(fmt)
    root.addHandler(console_h)

    # 2. Main log file — INFO+ summary (human-readable scan results)
    info_h = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    info_h.setLevel(logging.INFO)
    info_h.setFormatter(fmt)
    root.addHandler(info_h)

    # 3. Debug log file — DEBUG+ always on (IBKR wire trace, never filtered)
    debug_h = logging.FileHandler(DEBUG_LOG_FILE, mode="w", encoding="utf-8")
    debug_h.setLevel(logging.DEBUG)
    debug_h.setFormatter(fmt)
    root.addHandler(debug_h)

    # Silence noisy third-party loggers that we don't control
    logging.getLogger("ibapi").setLevel(logging.WARNING)

    log = logging.getLogger("Screener")
    log.info(f"Logging initialised | console={console_level} | "
             f"info_log={LOG_FILE} | debug_log={DEBUG_LOG_FILE}")
    log.debug("[LOGGING] DEBUG handler active — all IBKR callbacks will be traced")


log = logging.getLogger("Screener")


def is_market_hours() -> bool:
    from datetime import time as dtime
    now = datetime.now(ET)
    if now.weekday() >= 5:   # Saturday / Sunday
        return False
    t = now.time()
    return dtime(9, 30) <= t <= dtime(16, 0)


def run_once(account_value: float = 100_000, symbols_override=None):
    """Run a single full scan and save results."""
    if symbols_override:
        import config as cfg_mod
        cfg_mod.SYMBOL_OVERRIDE = symbols_override

    from pipeline import SwingScreenerPipeline

    pipeline = SwingScreenerPipeline()
    try:
        pipeline.connect()
        candidates = pipeline.run(account_value=account_value)
        pipeline.save_results(OUTPUT_FILE)
        return pipeline.top_candidates()
    finally:
        pipeline.disconnect()


def print_report(results_path: str = OUTPUT_FILE):
    """Pretty-print the last saved screener results."""
    try:
        with open(results_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"No results file found at {results_path}. Run a scan first.")
        return

    generated = data.get("generated_at", "unknown")
    total     = data.get("total_scanned", 0)
    passed    = data.get("total_passed", 0)
    cands     = data.get("candidates", [])

    print("\n" + "═" * 80)
    print(f"  SP500 SWING SCREENER RESULTS")
    print(f"  Generated: {generated}")
    print(f"  Scanned: {total} | Passed all stages: {passed}")
    print("═" * 80)

    top = [c for c in cands if c.get("all_pass")][:15]

    if not top:
        print("  No candidates passed all screening stages.")
        print("═" * 80)
        return

    # Header
    print(f"\n{'#':>3}  {'SYMBOL':<8} {'SIGNAL':<20} {'COMP':>5} "
          f"{'TECH':>5} {'FUND':>5} {'SENT':>5}  {'SECTOR':<20} {'OPTION STRUCTURE'}")
    print("─" * 110)

    for i, c in enumerate(top, 1):
        signal  = c.get("signal", "")
        comp    = c.get("composite", 0)
        tech    = c.get("tech_score", 0)
        fund    = c.get("fund_score", 0)
        sent    = c.get("sent_score", 0)
        sector  = (c.get("sector") or "")[:20]
        opt     = c.get("option_trade") or {}
        struct  = opt.get("structure", "—")

        signal_emoji = {"BREAKOUT": "🚀", "REVERSAL_LONG": "↗️", "REVERSAL_SHORT": "↘️"}.get(signal, "")
        print(f"{i:>3}  {c['symbol']:<8} {signal_emoji}{signal:<19} {comp:>5.1f} "
              f"{tech:>5.0f} {fund:>5.0f} {sent:>5.0f}  {sector:<20} {struct}")

    print("\n" + "─" * 110)
    print("  DETAIL: Top 5 Candidates")
    print("─" * 110)

    for c in top[:5]:
        sym    = c["symbol"]
        signal = c.get("signal", "")
        price  = c.get("price", 0)
        opt    = c.get("option_trade") or {}

        print(f"\n  ▶ {sym} | {signal} | ${price:.2f} | "
              f"composite={c.get('composite',0):.1f}")
        print(f"    Sector: {c.get('sector','')} | {c.get('industry','')}")
        print(f"    Market Cap: ${c.get('mkt_cap_b',0):.1f}B")

        print(f"\n    📐 Technical [{c.get('tech_score',0):.0f}/100]:")
        for r in c.get("tech_reasons", [])[:4]:
            print(f"       • {r}")

        print(f"\n    📊 Fundamental [{c.get('fund_score',0):.0f}/100]:")
        for r in c.get("fund_reasons", [])[:4]:
            print(f"       • {r}")

        print(f"\n    💬 Sentiment [{c.get('sent_score',0):.0f}/100]:")
        for r in c.get("sent_reasons", [])[:4]:
            print(f"       • {r}")

        if opt:
            print(f"\n    🎯 Options Trade:")
            print(f"       {opt.get('rationale','')}")
            for note in opt.get("notes", []):
                print(f"       • {note}")

        print()

    print("═" * 80 + "\n")


def schedule_loop(account_value: float):
    """Run the screener on a schedule during market hours."""
    log.info(f"Scheduled mode: scan every {SCAN_INTERVAL_MINUTES} min during market hours")
    while True:
        if is_market_hours():
            log.info("Market is open — running scan...")
            try:
                run_once(account_value=account_value)
                print_report()
            except Exception as e:
                log.error(f"Scan failed: {e}", exc_info=True)
        else:
            now = datetime.now(ET)
            log.info(f"Market closed ({now.strftime('%H:%M ET')}) — waiting...")

        log.info(f"Next scan in {SCAN_INTERVAL_MINUTES} minutes")
        time.sleep(SCAN_INTERVAL_MINUTES * 60)


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SP500 Swing Screener — IBKR + 13F + Sentiment"
    )
    parser.add_argument("--schedule", action="store_true",
                        help="Run continuously on a schedule during market hours")
    parser.add_argument("--report",   action="store_true",
                        help="Print last saved results (no scan)")
    parser.add_argument("--symbols",  nargs="+", default=None,
                        help="Override SP500 universe with specific symbols")
    parser.add_argument("--account",  type=float, default=100_000,
                        help="Account value in USD for position sizing (default 100000)")
    parser.add_argument("--port",     type=int,   default=4002,
                        help="IBKR TWS port (7497=paper, 7496=live)")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG","INFO","WARNING","ERROR"])
    args = parser.parse_args()

    # Initialise logging NOW — after argparse so --loglevel is known.
    # This replaces the module-level basicConfig that ran too early.
    _setup_logging(args.loglevel)

    # Apply port override
    import config as cfg_mod
    cfg_mod.IBKR_PORT = args.port

    if args.report:
        print_report()
        sys.exit(0)

    if args.schedule:
        schedule_loop(account_value=args.account)
    else:
        log.info(f"Running single scan | account=${args.account:,.0f} | port={args.port}")
        tops = run_once(account_value=args.account, symbols_override=args.symbols)
        print_report()

        if tops:
            log.info(f"\n{'═'*50}")
            log.info(f"TOP CANDIDATES ({len(tops)}):")
            for c in tops:
                opt = c.option_trade or {}
                log.info(f"  {c.symbol:<8} {c.signal:<20} composite={c.composite:.1f} "
                         f"| {opt.get('rationale','no option structure')[:80]}")
