"""
pipeline.py — Main Screener Pipeline Orchestrator
Runs the full 4-stage pipeline for every SP500 stock:
  Stage 1: Universe fetch (Wikipedia SP500 list)
  Stage 2: Technical screen (breakout / reversal)
  Stage 3: Fundamental analysis + 13F
  Stage 4: Sentiment score
  → Output: ranked candidates with option trade structures
"""

import json
import time
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional
import pytz

from config import (
    SYMBOL_OVERRIDE, OUTPUT_FILE,
    TechConfig as TC, FundConfig as FC, SentConfig as SC,
    ACCOUNT, MAX_POSITIONS,
)
from ibkr_client import IBKRDataFetcher
from technical import TechnicalAnalyser, TechResult
from fundamental import FundamentalAnalyser, FundResult
from sentiment import SentimentAnalyser, SentResult
from options import OptionsAnalyser, OptionTrade

log = logging.getLogger("Screener.Pipeline")
ET  = pytz.timezone("America/New_York")


# ════════════════════════════════════════════════════════════════════════════
# CANDIDATE
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Candidate:
    symbol:       str
    signal:       str         = "NONE"
    tech_score:   float       = 0.0
    fund_score:   float       = 0.0
    sent_score:   float       = 0.0
    composite:    float       = 0.0   # weighted final score
    tech_reasons: list        = field(default_factory=list)
    fund_reasons: list        = field(default_factory=list)
    sent_reasons: list        = field(default_factory=list)
    option_trade: Optional[dict] = None
    sector:       str         = ""
    industry:     str         = ""
    price:        float       = 0.0
    mkt_cap_b:    float       = 0.0
    scanned_at:   str         = ""

    # Pass flags
    tech_pass:    bool        = False
    fund_pass:    bool        = False
    sent_pass:    bool        = False
    all_pass:     bool        = False


# ════════════════════════════════════════════════════════════════════════════
# SP500 UNIVERSE  —  powered by pytickersymbols
# ════════════════════════════════════════════════════════════════════════════

# IBKR uses a space instead of a dot for multi-class shares (BRK.B → BRK B)
_IBKR_TICKER_MAP = {
    "BRK.B": "BRK B",
    "BRK.A": "BRK A",
    "BF.B":  "BF B",
    "BF.A":  "BF A",
}


def _yahoo_to_ibkr(yahoo_ticker: str) -> str:
    """
    Convert a Yahoo Finance ticker to the format expected by IBKR SMART routing.
    Rules:
      • Strip exchange prefix  (e.g. "NMS:AAPL" → "AAPL")
      • Replace dots with space (e.g. "BRK.B"   → "BRK B")
      • Uppercase and strip whitespace
    """
    t = yahoo_ticker.upper().strip()
    # Remove exchange prefix (pytickersymbols occasionally includes them)
    if ":" in t:
        t = t.split(":", 1)[1]
    # Apply known IBKR substitutions first
    if t in _IBKR_TICKER_MAP:
        return _IBKR_TICKER_MAP[t]
    # Generic dot → space (catches any future dual-class shares)
    return t.replace(".", " ")


def fetch_sp500_symbols() -> list[str]:
    """
    Return IBKR-compatible ticker symbols for all S&P 500 constituents
    using pytickersymbols as the data source.

    pytickersymbols bundles a versioned, regularly-updated JSON dataset
    (no network call required at runtime — the data ships with the package).
    This makes the universe fetch fully offline and immune to Wikipedia
    HTML changes or scraping blocks.

    Flow:
      1. Honour SYMBOL_OVERRIDE in config.py (useful for backtests / CI).
      2. Use PyTickerSymbols.get_stocks_by_index('S&P 500') to stream every
         constituent dict.  Each dict contains a 'symbols' list; we prefer
         the Yahoo ticker inside that list because it's the most reliable
         plain symbol (e.g. 'AAPL', 'BRK.B') and convert it to IBKR format.
      3. Fall back to the stock's top-level 'symbol' key if no Yahoo ticker
         is present in the symbols list.
      4. Deduplicate (some multi-class shares appear under one company entry)
         and return a sorted, deterministic list.
    """
    if SYMBOL_OVERRIDE:
        log.info(f"Symbol override active — using {len(SYMBOL_OVERRIDE)} symbols: "
                 f"{SYMBOL_OVERRIDE[:10]}{'...' if len(SYMBOL_OVERRIDE) > 10 else ''}")
        return list(SYMBOL_OVERRIDE)

    log.info("Building S&P 500 universe via pytickersymbols...")
    try:
        from pytickersymbols import PyTickerSymbols
        pts = PyTickerSymbols()

        ibkr_symbols: list[str] = []
        metadata: list[dict]    = []   # kept for optional enrichment downstream

        for stock in pts.get_stocks_by_index("S&P 500"):
            # ── Extract Yahoo ticker from the symbols list ────────────────
            yahoo_ticker = None
            for sym_entry in stock.get("symbols", []):
                # Each entry is a dict: {"yahoo": "AAPL", "google": "NASDAQ:AAPL"}
                if isinstance(sym_entry, dict) and sym_entry.get("yahoo"):
                    yahoo_ticker = sym_entry["yahoo"]
                    break

            # ── Fall back to top-level 'symbol' if no Yahoo entry ─────────
            raw = yahoo_ticker or stock.get("symbol") or ""
            if not raw:
                log.debug(f"  Skipping stock with no ticker: {stock.get('name','?')}")
                continue

            ibkr_sym = _yahoo_to_ibkr(raw)
            if not ibkr_sym:
                continue

            ibkr_symbols.append(ibkr_sym)
            metadata.append({
                "ibkr":     ibkr_sym,
                "yahoo":    raw,
                "name":     stock.get("name", ""),
                "country":  stock.get("country", ""),
                "industries": stock.get("industries", []),
            })

        # Deduplicate while preserving first-seen order
        seen:   set[str]  = set()
        unique: list[str] = []
        for s in ibkr_symbols:
            if s not in seen:
                seen.add(s)
                unique.append(s)

        if not unique:
            raise ValueError("pytickersymbols returned an empty S&P 500 list")

        log.info(
            f"pytickersymbols: {len(unique)} unique IBKR symbols loaded "
            f"({len(ibkr_symbols) - len(unique)} duplicates removed)"
        )
        log.debug(f"First 10 symbols: {unique[:10]}")
        return unique

    except ImportError:
        log.error(
            "pytickersymbols is not installed. "
            "Run: pip install pytickersymbols"
        )
    except Exception as e:
        log.error(f"pytickersymbols fetch failed: {e}", exc_info=True)

    # ── Hard fallback: large-cap subset so the pipeline never crashes ────
    log.warning("Falling back to built-in large-cap subset (install pytickersymbols!)")
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
        "BRK B", "LLY", "JPM", "UNH", "V",    "XOM",  "MA",   "AVGO",
        "HD",   "CVX",  "MRK",  "ABBV", "COST", "PEP",  "KO",  "ADBE",
        "WMT",  "MCD",  "CRM",  "TMO",  "BAC",  "ACN",  "NFLX",
    ]


# ════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ════════════════════════════════════════════════════════════════════════════
class SwingScreenerPipeline:

    def __init__(self):
        self.ibkr        = IBKRDataFetcher()
        self.tech_engine = TechnicalAnalyser()
        self.fund_engine = FundamentalAnalyser()
        self.sent_engine = SentimentAnalyser()
        self.opt_engine  = OptionsAnalyser()
        self.results:    list[Candidate] = []

    def connect(self):
        self.ibkr.connect()

    def disconnect(self):
        self.ibkr.disconnect()

    # ── Stage 1: Technical Screen ─────────────────────────────────────────
    def _run_technical(self, symbol: str) -> Optional[TechResult]:
        try:
            bars = self.ibkr.get_daily_bars(symbol, days=252)
            if not bars:
                log.debug(f"  {symbol}: no bars")
                return None
            return self.tech_engine.analyse(symbol, bars)
        except Exception as e:
            log.warning(f"  {symbol} technical error: {e}")
            return None

    # ── Stage 2: Fundamental Screen ───────────────────────────────────────
    def _run_fundamental(self, symbol: str) -> FundResult:
        try:
            ibkr_data = self.ibkr.get_fundamentals(symbol)
            return self.fund_engine.analyse(symbol, ibkr_data)
        except Exception as e:
            log.warning(f"  {symbol} fundamental error: {e}")
            return FundResult(symbol=symbol)

    # ── Stage 3: Sentiment Screen ─────────────────────────────────────────
    def _run_sentiment(self, symbol: str, price: float) -> SentResult:
        try:
            return self.sent_engine.analyse(symbol, current_price=price)
        except Exception as e:
            log.warning(f"  {symbol} sentiment error: {e}")
            return SentResult(symbol=symbol)

    # ── Stage 4: Options Structure ────────────────────────────────────────
    def _run_options(self, symbol: str, signal: str,
                     price: float, account_val: float) -> Optional[OptionTrade]:
        try:
            chain = self.ibkr.get_option_chain(symbol)
            if not chain:
                return None
            return self.opt_engine.analyse(symbol, signal, price, chain, account_val)
        except Exception as e:
            log.warning(f"  {symbol} options error: {e}")
            return None

    # ── Composite Scoring ─────────────────────────────────────────────────
    @staticmethod
    def _composite(tech: float, fund: float, sent: float) -> float:
        """
        Weighted composite: Technical 40%, Fundamental 35%, Sentiment 25%.
        Technical gets highest weight — price action is the primary trigger.
        """
        return tech * 0.40 + fund * 0.35 + sent * 0.25

    # ── Main scan ─────────────────────────────────────────────────────────
    def run(self, account_value: float = 100_000) -> list[Candidate]:
        symbols = fetch_sp500_symbols()
        total   = len(symbols)
        log.info(f"═══ Starting scan of {total} symbols ═══")

        passed_tech = []
        candidates  = []
        ts_start    = datetime.now(ET)

        # ─ STAGE 1: Technical pass (all symbols) ─────────────────────────
        log.info("── Stage 1: Technical Screen ──")
        for i, sym in enumerate(symbols):
            log.info(f"  [{i+1}/{total}] {sym}")
            tech = self._run_technical(sym)
            if tech is None:
                continue
            if tech.passed:
                log.info(f"  ✅ {sym} TECH PASS: {tech.signal} score={tech.score:.0f}")
                passed_tech.append((sym, tech))
            # Throttle IBKR requests slightly
            time.sleep(0.05)

        log.info(f"Stage 1 complete: {len(passed_tech)}/{total} passed technical")

        # ─ STAGE 2+3+4: Fundamental + Sentiment + Options (tech passes) ──
        log.info("── Stages 2–4: Fundamental + Sentiment + Options ──")
        for sym, tech in passed_tech:
            log.info(f"  ── Analysing: {sym} ({tech.signal}) ──")
            cand = Candidate(
                symbol=sym,
                signal=tech.signal,
                tech_score=tech.score,
                tech_reasons=tech.reasons,
                tech_pass=tech.passed,
                price=tech.close,
                scanned_at=datetime.now(ET).isoformat(),
            )

            # Stage 2: Fundamentals
            fund = self._run_fundamental(sym)
            cand.fund_score   = fund.score
            cand.fund_reasons = fund.reasons
            cand.fund_pass    = fund.passed
            cand.sector       = fund.sector
            cand.industry     = fund.industry
            cand.mkt_cap_b    = fund.market_cap_b or 0.0

            if not fund.passed:
                log.info(f"  ❌ {sym} failed fundamental (score={fund.score:.0f})")
                # Still include in output but mark as failed
                cand.composite = self._composite(tech.score, fund.score, 0)
                candidates.append(cand)
                time.sleep(0.3)
                continue

            log.info(f"  ✅ {sym} FUND PASS: score={fund.score:.0f}")

            # Stage 3: Sentiment
            sent = self._run_sentiment(sym, tech.close)
            cand.sent_score   = sent.score
            cand.sent_reasons = sent.reasons
            cand.sent_pass    = sent.passed

            if not sent.passed:
                log.info(f"  ❌ {sym} failed sentiment (score={sent.score:.0f})")
                cand.composite = self._composite(tech.score, fund.score, sent.score)
                candidates.append(cand)
                time.sleep(0.3)
                continue

            log.info(f"  ✅ {sym} SENT PASS: score={sent.score:.0f}")

            # Stage 4: Options structure
            opt = self._run_options(sym, tech.signal, tech.close, account_value)
            if opt:
                # Serialize option trade to dict for JSON output
                cand.option_trade = {
                    "structure":   opt.structure,
                    "rationale":   opt.rationale,
                    "expiry":      opt.target_expiry,
                    "dte":         opt.target_dte,
                    "max_cost":    opt.max_cost,
                    "contracts":   opt.contracts,
                    "breakeven":   opt.breakeven,
                    "notes":       opt.notes,
                    "legs": [
                        {
                            "action": leg.action,
                            "right":  leg.right,
                            "strike": leg.strike,
                            "expiry": leg.expiry,
                            "dte":    leg.dte,
                        }
                        for leg in opt.legs
                    ]
                }

            cand.composite = self._composite(tech.score, fund.score, sent.score)
            cand.all_pass  = True
            candidates.append(cand)
            log.info(f"  🎯 {sym} ALL STAGES PASSED | composite={cand.composite:.1f}")
            time.sleep(0.3)

        # ─ Sort and rank ──────────────────────────────────────────────────
        candidates.sort(key=lambda c: c.composite, reverse=True)
        self.results = candidates

        elapsed = (datetime.now(ET) - ts_start).total_seconds()
        passed_all = [c for c in candidates if c.all_pass]
        log.info(f"═══ Scan complete in {elapsed:.0f}s ═══")
        log.info(f"    {len(passed_all)} candidates passed all stages")
        log.info(f"    Top 5:")
        for c in passed_all[:5]:
            log.info(f"      {c.symbol:6s} {c.signal:18s} composite={c.composite:.1f} "
                     f"T={c.tech_score:.0f} F={c.fund_score:.0f} S={c.sent_score:.0f}")

        return candidates

    def save_results(self, path: str = OUTPUT_FILE):
        """Persist results to JSON."""
        output = {
            "generated_at":  datetime.now(ET).isoformat(),
            "total_scanned": len(self.results),
            "total_passed":  len([c for c in self.results if c.all_pass]),
            "candidates":    [asdict(c) for c in self.results],
        }
        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        log.info(f"Results saved to {path}")

    def top_candidates(self, n: int = MAX_POSITIONS) -> list[Candidate]:
        return [c for c in self.results if c.all_pass][:n]
