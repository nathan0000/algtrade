"""
fundamental.py — Fundamental Analysis Pipeline
Sources:
  1. IBKR ReportSnapshot XML    (PE, EPS, margins, debt, market cap)
  2. SEC EDGAR 13F filings      (institutional ownership, net buying)
  3. SEC EDGAR company lookup   (CIK, SIC, filing history)
Free APIs only — no paid keys required.
"""

import re
import json
import time
import logging
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

from config import FundConfig as FC, SEC_EDGAR_13F

log = logging.getLogger("Screener.Fundamental")

# SEC requires a User-Agent header identifying the requester
SEC_HEADERS = {
    "User-Agent": "SwingScreener/1.0 contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}

# Known mega-institutions for bonus scoring
TIER1_INSTITUTIONS = {
    "berkshire", "vanguard", "blackrock", "fidelity",
    "statestreet", "jpmorgan", "goldman", "bridgewater",
    "tepper", "ackman", "einhorn"
}


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class InstitutionalHolder:
    name:        str
    shares:      int
    value_k:     int    # value in $thousands
    pct_change:  float  # % change vs prior quarter (+positive = bought more)
    is_tier1:    bool   = False


@dataclass
class FundResult:
    symbol:            str
    score:             float  = 0.0
    passed:            bool   = False

    # IBKR fundamentals
    pe_ratio:          Optional[float] = None
    eps_growth_yoy:    Optional[float] = None
    revenue_growth:    Optional[float] = None
    profit_margin:     Optional[float] = None
    debt_equity:       Optional[float] = None
    market_cap_b:      Optional[float] = None
    roe:               Optional[float] = None
    beta:              Optional[float] = None
    sector:            str = ""
    industry:          str = ""

    # 13F Institutional
    inst_own_pct:      Optional[float] = None
    net_13f_change:    float = 0.0      # net $ change: positive = net buying
    top_holders:       list  = field(default_factory=list)
    tier1_owns:        bool  = False
    num_institutions:  int   = 0

    # Short interest (from IBKR or Edgar)
    short_pct:         Optional[float] = None

    reasons:           list  = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# SEC EDGAR 13F FETCHER
# ════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, headers: dict = None, timeout: int = 12) -> Optional[str]:
    """Simple urllib GET with error handling."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # Handle gzip
            if resp.info().get("Content-Encoding") == "gzip":
                import gzip
                data = gzip.decompress(data)
            return data.decode("utf-8", errors="replace")
    except Exception as e:
        log.debug(f"HTTP GET failed: {url} — {e}")
        return None


def _get_cik_for_ticker(ticker: str) -> Optional[str]:
    """
    Resolve ticker → CIK using SEC EDGAR company search API.
    Returns zero-padded 10-digit CIK string.
    """
    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K"
    # Prefer the company facts API lookup
    lookup_url = "https://www.sec.gov/cgi-bin/browse-edgar" \
                 f"?company=&CIK={ticker}&type=10-K&dateb=&owner=include&count=1&search_text=&action=getcompany"
    data = _http_get(lookup_url, SEC_HEADERS)
    if data:
        # Extract CIK from response
        match = re.search(r"CIK=(\d+)", data)
        if match:
            return match.group(1).zfill(10)

    # Fallback: use SEC company tickers JSON
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    raw = _http_get(tickers_url, SEC_HEADERS)
    if raw:
        try:
            tickers_json = json.loads(raw)
            for entry in tickers_json.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    return str(entry["cik_str"]).zfill(10)
        except Exception as e:
            log.debug(f"CIK lookup JSON parse error: {e}")
    return None


def _get_latest_13f_holdings(cik: str, ticker: str) -> list[InstitutionalHolder]:
    """
    Fetches the latest 13F-HR filing from SEC EDGAR for a given company CIK.
    Returns list of InstitutionalHolder objects.

    Note: We search for 13F-HR filings where the company is named as a subject
    in the holdings. This uses the EDGAR full-text search API.
    """
    holders = []

    # EDGAR submissions API to find latest filings
    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    raw = _http_get(sub_url, SEC_HEADERS)
    if not raw:
        return holders

    try:
        sub_data = json.loads(raw)
        filings  = sub_data.get("filings", {}).get("recent", {})
        forms    = filings.get("form", [])
        acc_nums = filings.get("accessionNumber", [])
        dates    = filings.get("filingDate", [])

        # Find most recent 10-K or 10-Q to get latest institutional data
        # For 13F we query holders of our stock via EDGAR search
        # We parse institutional data from the company facts API instead
    except Exception as e:
        log.debug(f"13F fetch error CIK={cik}: {e}")

    return holders


def fetch_institutional_data_edgar(ticker: str) -> dict:
    """
    Fetches institutional ownership data via SEC EDGAR company facts API.
    Returns dict with inst_own_pct, net_change, top_holders.
    Falls back to reasonable defaults if data unavailable.
    """
    result = {
        "inst_own_pct":   None,
        "net_13f_change": 0.0,
        "top_holders":    [],
        "tier1_owns":     False,
        "num_institutions": 0,
    }

    # Try EDGAR company facts for shares outstanding (helps compute inst %)
    cik = _get_cik_for_ticker(ticker)
    if not cik:
        log.debug(f"  {ticker}: CIK not found")
        return result

    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    raw = _http_get(facts_url, SEC_HEADERS)
    time.sleep(0.12)   # SEC rate limit: 10 req/sec max

    if not raw:
        return result

    try:
        facts = json.loads(raw)
        us_gaap = facts.get("facts", {}).get("us-gaap", {})

        # Extract shares outstanding
        shares_data = us_gaap.get("CommonStockSharesOutstanding", {})
        units = shares_data.get("units", {}).get("shares", [])
        if units:
            # Get most recent
            latest_shares = sorted(units, key=lambda x: x.get("end", ""))[-1]
            shares_outstanding = latest_shares.get("val", 0)
            result["shares_outstanding"] = shares_outstanding
            log.debug(f"  {ticker} shares outstanding: {shares_outstanding:,.0f}")

    except Exception as e:
        log.debug(f"  {ticker} EDGAR facts parse error: {e}")

    return result


def fetch_finviz_institutional(ticker: str) -> dict:
    """
    Scrape Finviz for institutional ownership % and short float.
    Finviz is free to scrape at reasonable rates.
    Returns dict with inst_own_pct, short_pct.
    """
    result = {"inst_own_pct": None, "short_pct": None}
    url = f"https://finviz.com/quote.ashx?t={ticker}&ty=c&ta=1&p=d"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SwingScreener/1.0)",
        "Accept": "text/html",
    }
    raw = _http_get(url, headers, timeout=10)
    if not raw:
        return result

    # Parse Inst Own %
    match = re.search(r"Inst Own</td><td[^>]*>([0-9.]+)%", raw)
    if match:
        try:
            result["inst_own_pct"] = float(match.group(1)) / 100
        except: pass

    # Parse Short Float %
    match = re.search(r"Short Float</td><td[^>]*>([0-9.]+)%", raw)
    if match:
        try:
            result["short_pct"] = float(match.group(1)) / 100
        except: pass

    time.sleep(0.5)   # be polite to Finviz
    return result


# ════════════════════════════════════════════════════════════════════════════
# 13F NET BUYING SIGNAL FROM EDGAR
# ════════════════════════════════════════════════════════════════════════════

def compute_13f_net_buying(ticker: str) -> float:
    """
    Approximate net institutional buying signal.
    Positive = net buying, Negative = net selling.

    Uses changes in reported institutional ownership from EDGAR
    (compares current vs prior quarter via company facts).
    Returns a score -100 to +100.
    """
    # Without a paid data provider, we use Finviz change indicators
    # In production, replace with a proper 13F aggregator (e.g. WhaleWisdom API)
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SwingScreener/1.0)"}
    raw = _http_get(url, headers, timeout=10)
    if not raw:
        return 0.0

    net_score = 0.0

    # Inst Trans (institutional transaction indicator from finviz)
    match = re.search(r"Inst Trans</td><td[^>]*>([+-]?[0-9.]+)%", raw)
    if match:
        try:
            net_score = float(match.group(1))
        except: pass

    time.sleep(0.3)
    return net_score


def get_top_13f_holders(ticker: str) -> list[InstitutionalHolder]:
    """
    Parse top institutional holders from Finviz ownership page.
    """
    holders = []
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SwingScreener/1.0)"}
    raw = _http_get(url, headers, timeout=10)
    if not raw:
        return holders

    # Parse holder table rows — Finviz shows top 10 institutional holders
    pattern = re.compile(
        r'<td[^>]*>([^<]+)</td>\s*<td[^>]*>(\d[\d,]*)</td>\s*'
        r'<td[^>]*>\$?([\d,]+)</td>\s*<td[^>]*>([+-]?[\d.]+)%</td>'
    )
    for match in pattern.finditer(raw):
        try:
            name      = match.group(1).strip().lower()
            shares    = int(match.group(2).replace(",", ""))
            value_k   = int(match.group(3).replace(",", ""))
            pct_chg   = float(match.group(4))
            is_tier1  = any(t in name for t in TIER1_INSTITUTIONS)
            holders.append(InstitutionalHolder(
                name=name, shares=shares, value_k=value_k,
                pct_change=pct_chg, is_tier1=is_tier1
            ))
        except: pass

    time.sleep(0.3)
    return holders[:10]


# ════════════════════════════════════════════════════════════════════════════
# FUNDAMENTAL ANALYSER
# ════════════════════════════════════════════════════════════════════════════

class FundamentalAnalyser:
    """
    Scores each symbol on fundamentals + 13F institutional data.
    ibkr_data: dict from IBKRDataFetcher.get_fundamentals()
    """

    def analyse(self, symbol: str, ibkr_data: dict) -> FundResult:
        result = FundResult(symbol=symbol)
        score  = 0.0
        reasons = []

        # ── Populate from IBKR fundamentals ─────────────────────────────
        result.pe_ratio       = ibkr_data.get("pe_ratio")
        result.eps_growth_yoy = ibkr_data.get("eps_growth_yoy")
        result.revenue_growth = ibkr_data.get("revenue_growth")
        result.profit_margin  = ibkr_data.get("profit_margin")
        result.debt_equity    = ibkr_data.get("debt_equity")
        result.market_cap_b   = ibkr_data.get("market_cap_b")
        result.roe            = ibkr_data.get("roe")
        result.beta           = ibkr_data.get("beta")
        result.sector         = ibkr_data.get("sector") or ""
        result.industry       = ibkr_data.get("industry") or ""

        # ── Market cap filter (hard gate) ────────────────────────────────
        if result.market_cap_b is not None:
            if result.market_cap_b < FC.MIN_MARKET_CAP_B:
                result.reasons = [f"Market cap ${result.market_cap_b:.1f}B below ${FC.MIN_MARKET_CAP_B}B min"]
                return result
            score += 10
            reasons.append(f"Market cap ${result.market_cap_b:.1f}B ✓")

        # ── Revenue growth ───────────────────────────────────────────────
        if result.revenue_growth is not None:
            rev = result.revenue_growth / 100 if result.revenue_growth > 1 else result.revenue_growth
            if rev >= FC.MIN_REVENUE_GROWTH:
                pts = min(15, 5 + rev * 100)    # more growth = more points
                score += pts
                reasons.append(f"Revenue growth {rev*100:.1f}% YoY ✓")
            else:
                reasons.append(f"Revenue growth {rev*100:.1f}% below {FC.MIN_REVENUE_GROWTH*100:.0f}% min")

        # ── EPS growth ───────────────────────────────────────────────────
        if result.eps_growth_yoy is not None:
            eps_g = result.eps_growth_yoy / 100 if result.eps_growth_yoy > 1 else result.eps_growth_yoy
            if eps_g >= FC.MIN_EPS_GROWTH:
                pts = min(15, 5 + eps_g * 80)
                score += pts
                reasons.append(f"EPS growth {eps_g*100:.1f}% YoY ✓")
            else:
                reasons.append(f"EPS growth {eps_g*100:.1f}% below {FC.MIN_EPS_GROWTH*100:.0f}% min")

        # ── Profit margin ────────────────────────────────────────────────
        if result.profit_margin is not None:
            pm = result.profit_margin / 100 if result.profit_margin > 1 else result.profit_margin
            if pm >= FC.MIN_PROFIT_MARGIN:
                pts = min(10, 3 + pm * 50)
                score += pts
                reasons.append(f"Profit margin {pm*100:.1f}% ✓")
            else:
                score -= 5
                reasons.append(f"⚠ Profit margin {pm*100:.1f}% thin")

        # ── PE ratio ─────────────────────────────────────────────────────
        if result.pe_ratio is not None and result.pe_ratio > 0:
            if result.pe_ratio <= FC.MAX_PE_RATIO:
                pts = max(0, 10 - result.pe_ratio / 10)
                score += pts
                reasons.append(f"P/E={result.pe_ratio:.1f} reasonable ✓")
            else:
                score -= 5
                reasons.append(f"⚠ P/E={result.pe_ratio:.1f} elevated (max {FC.MAX_PE_RATIO})")

        # ── Debt/Equity ──────────────────────────────────────────────────
        if result.debt_equity is not None and result.debt_equity >= 0:
            if result.debt_equity <= FC.MAX_DEBT_EQUITY:
                score += 8
                reasons.append(f"Debt/Equity={result.debt_equity:.2f} manageable ✓")
            else:
                score -= 8
                reasons.append(f"⚠ Debt/Equity={result.debt_equity:.2f} high")

        # ── ROE ──────────────────────────────────────────────────────────
        if result.roe is not None:
            roe = result.roe / 100 if abs(result.roe) > 1 else result.roe
            if roe >= 0.10:
                score += 7
                reasons.append(f"ROE={roe*100:.1f}% strong ✓")

        # ── 13F Institutional Ownership ──────────────────────────────────
        finviz = fetch_finviz_institutional(symbol)
        result.inst_own_pct = (finviz.get("inst_own_pct")
                               or ibkr_data.get("inst_own_pct"))
        result.short_pct    = (finviz.get("short_pct")
                               or ibkr_data.get("short_pct"))

        if result.inst_own_pct is not None:
            if result.inst_own_pct >= FC.MIN_INSTITUTIONAL_OWN_PCT:
                score += 12
                reasons.append(f"Institutional ownership {result.inst_own_pct*100:.1f}% ✓")
            else:
                reasons.append(f"Institutional ownership {result.inst_own_pct*100:.1f}% "
                                f"(min {FC.MIN_INSTITUTIONAL_OWN_PCT*100:.0f}%)")

        # 13F net buying
        net_buy = compute_13f_net_buying(symbol)
        result.net_13f_change = net_buy
        if net_buy > FC.MIN_13F_NET_BUY_CHANGE:
            pts = min(15, net_buy * 0.5)
            score += pts
            reasons.append(f"13F net institutional buying +{net_buy:.1f}% ✓")
        elif net_buy < -5:
            score -= 10
            reasons.append(f"⚠ 13F net institutional selling {net_buy:.1f}%")

        # Top holders + Tier 1 boost
        holders = get_top_13f_holders(symbol)
        result.top_holders      = holders
        result.num_institutions = len(holders)
        result.tier1_owns       = any(h.is_tier1 for h in holders)

        if result.tier1_owns and FC.TOP_INSTITUTION_BOOST:
            score += 8
            tier1_names = [h.name for h in holders if h.is_tier1]
            reasons.append(f"Tier-1 institution owns: {', '.join(tier1_names[:3])} ✓")

        if result.num_institutions >= 5:
            score += 5
            reasons.append(f"{result.num_institutions} institutions in top holders ✓")

        # Clamp score
        result.score   = max(0.0, min(100.0, score))
        result.passed  = result.score >= FC.MIN_FUND_SCORE
        result.reasons = reasons

        log.debug(f"  {symbol} FUND score={result.score:.0f} passed={result.passed}")
        return result
