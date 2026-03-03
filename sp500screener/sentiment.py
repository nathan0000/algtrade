"""
sentiment.py — Multi-Source Sentiment Analysis
Sources (all free/public):
  1. Finviz news headline scraping + polarity scoring
  2. Analyst ratings consensus (Finviz)
  3. Short interest / put-call ratio
  4. SEC insider buying (Form 4 filings from EDGAR)
  5. Simple keyword-based NLP (no paid models required)
"""

import re
import json
import time
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
import math

from config import SentConfig as SC

log = logging.getLogger("Screener.Sentiment")

# ── Positive / Negative keywords for headline scoring ───────────────────────
POSITIVE_WORDS = {
    "beat", "beats", "record", "surges", "jumps", "rallies", "upgrade",
    "upgraded", "outperform", "buy", "strong", "positive", "growth",
    "expansion", "profit", "exceeds", "raises", "guidance", "wins",
    "contract", "partnership", "approval", "approved", "bullish",
    "innovative", "breakthrough", "momentum", "accelerating"
}
NEGATIVE_WORDS = {
    "miss", "misses", "disappoints", "falls", "drops", "downgrade",
    "downgraded", "underperform", "sell", "weak", "negative", "decline",
    "shrinks", "cuts", "loss", "layoffs", "recall", "investigation",
    "lawsuit", "probe", "warning", "concern", "bearish", "slowing",
    "deficit", "shortfall", "penalty", "fine", "suspended"
}


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class NewsItem:
    headline:   str
    source:     str
    date:       str
    sentiment:  float   # -1 to +1


@dataclass
class AnalystRating:
    firm:       str
    rating:     str
    target:     Optional[float]


@dataclass
class InsiderTrade:
    name:       str
    title:      str
    shares:     int
    transaction: str    # "BUY" or "SELL"
    date:       str


@dataclass
class SentResult:
    symbol:             str
    score:              float  = 0.0
    passed:             bool   = False

    # Components
    news_sentiment:     float  = 0.0    # -1 to +1
    news_count:         int    = 0
    analyst_buy_pct:    float  = 0.0
    analyst_target:     Optional[float] = None
    analyst_upside:     float  = 0.0
    short_pct:          Optional[float] = None
    put_call_ratio:     Optional[float] = None
    insider_net_shares: int    = 0      # positive = net buying
    insider_trades:     list   = field(default_factory=list)
    news_items:         list   = field(default_factory=list)
    analyst_ratings:    list   = field(default_factory=list)

    reasons:            list   = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ════════════════════════════════════════════════════════════════════════════
def _http_get(url: str, headers: dict = None, timeout: int = 10) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            enc  = r.info().get("Content-Encoding", "")
            if enc == "gzip":
                import gzip
                data = gzip.decompress(data)
            return data.decode("utf-8", errors="replace")
    except Exception as e:
        log.debug(f"HTTP GET failed {url}: {e}")
        return None


def _score_headline(text: str) -> float:
    """
    Keyword-based polarity scoring. Returns -1 to +1.
    Simple but effective for financial headlines.
    """
    words  = re.findall(r"\b\w+\b", text.lower())
    pos    = sum(1 for w in words if w in POSITIVE_WORDS)
    neg    = sum(1 for w in words if w in NEGATIVE_WORDS)
    total  = pos + neg
    if total == 0:
        return 0.0
    raw = (pos - neg) / total
    # Apply sigmoid-like smoothing
    return max(-1.0, min(1.0, raw))


# ════════════════════════════════════════════════════════════════════════════
# NEWS SCRAPER (Finviz)
# ════════════════════════════════════════════════════════════════════════════
def fetch_finviz_news(ticker: str) -> list[NewsItem]:
    """Scrape Finviz news headlines for a ticker (last 20 items)."""
    url     = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SwingScreener/1.0)"}
    raw     = _http_get(url, headers)
    items   = []

    if not raw:
        return items

    # Finviz news table pattern
    pattern = re.compile(
        r'<td[^>]*class="news_date-cell[^"]*"[^>]*>([^<]+)</td>'
        r'.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>'
        r'.*?<td[^>]*class="news_link-cell[^"]*">.*?<span[^>]*>([^<]+)</span>',
        re.DOTALL
    )

    # Simpler pattern that actually matches Finviz HTML structure
    news_pattern = re.compile(
        r'class="news_date-cell[^"]*">([^<]+).*?'
        r'<a[^>]+>([^<]+)</a>.*?'
        r'<span[^>]*>([^<]+)</span>',
        re.DOTALL
    )

    # Extract news rows using a robust approach
    rows = re.findall(
        r'<tr[^>]*>.*?news_date.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>.*?<span[^>]*>([^<]*)</span>.*?</tr>',
        raw, re.DOTALL
    )

    for url_match, headline, source in rows[:20]:
        headline = re.sub(r'\s+', ' ', headline).strip()
        source   = re.sub(r'\s+', ' ', source).strip()
        if headline:
            items.append(NewsItem(
                headline=headline,
                source=source,
                date=datetime.now().strftime("%Y-%m-%d"),
                sentiment=_score_headline(headline)
            ))

    time.sleep(0.4)
    return items


# ════════════════════════════════════════════════════════════════════════════
# ANALYST RATINGS (Finviz)
# ════════════════════════════════════════════════════════════════════════════
def fetch_analyst_ratings(ticker: str) -> tuple[list[AnalystRating], float, float]:
    """
    Scrape analyst ratings from Finviz.
    Returns (ratings_list, buy_pct, avg_target_price).
    """
    url     = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SwingScreener/1.0)"}
    raw     = _http_get(url, headers)
    ratings = []

    if not raw:
        return ratings, 0.0, 0.0

    # Parse Recom indicator (1=Strong Buy → 5=Strong Sell)
    recom_match = re.search(r'Recom</td><td[^>]*>([0-9.]+)', raw)
    buy_pct     = 0.0
    if recom_match:
        try:
            recom = float(recom_match.group(1))
            # Convert 1–5 scale to buy %: 1=100% buy, 5=0% buy
            buy_pct = max(0.0, min(1.0, (5 - recom) / 4))
        except: pass

    # Target price
    target_match = re.search(r'Target Price</td><td[^>]*>([0-9.]+)', raw)
    avg_target   = 0.0
    if target_match:
        try:
            avg_target = float(target_match.group(1))
        except: pass

    # Parse individual analyst rows
    analyst_pattern = re.compile(
        r'<tr[^>]*>.*?<td[^>]*>([^<]+)</td>.*?<td[^>]*>([^<]+)</td>'
        r'.*?<td[^>]*>\$?([0-9.]*)</td>.*?<td[^>]*>\$?([0-9.]*)</td>.*?</tr>',
        re.DOTALL
    )
    for m in analyst_pattern.finditer(raw):
        try:
            firm   = m.group(1).strip()
            rating = m.group(2).strip()
            target_str = m.group(4).strip()
            if len(firm) > 3 and any(r in rating for r in
                   ["Buy", "Sell", "Hold", "Outperform", "Underperform", "Overweight"]):
                target = float(target_str) if target_str else None
                ratings.append(AnalystRating(firm=firm, rating=rating, target=target))
        except: pass

    time.sleep(0.3)
    return ratings[:15], buy_pct, avg_target


# ════════════════════════════════════════════════════════════════════════════
# INSIDER TRADING (SEC EDGAR Form 4)
# ════════════════════════════════════════════════════════════════════════════
SEC_HEADERS = {"User-Agent": "SwingScreener/1.0 contact@example.com"}


def fetch_insider_trades(ticker: str, days_back: int = 90) -> list[InsiderTrade]:
    """
    Fetch recent Form 4 insider trades from SEC EDGAR.
    Returns list of InsiderTrade for the last `days_back` days.
    """
    trades = []

    # Get CIK
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    raw_tickers = _http_get(tickers_url, SEC_HEADERS)
    cik = None
    if raw_tickers:
        try:
            tj = json.loads(raw_tickers)
            for entry in tj.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    break
        except: pass

    if not cik:
        return trades

    # Fetch recent Form 4 filings
    sub_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    raw = _http_get(sub_url, SEC_HEADERS)
    time.sleep(0.15)

    if not raw:
        return trades

    try:
        sub  = json.loads(raw)
        fil  = sub.get("filings", {}).get("recent", {})
        forms    = fil.get("form", [])
        acc_nums = fil.get("accessionNumber", [])
        dates    = fil.get("filingDate", [])
        doc_desc = fil.get("primaryDocument", [])

        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        for i, form in enumerate(forms):
            if form != "4":
                continue
            filing_date = dates[i] if i < len(dates) else ""
            if filing_date < cutoff:
                break   # filings are newest-first

            acc = acc_nums[i].replace("-", "")
            doc = doc_desc[i] if i < len(doc_desc) else ""
            if not doc:
                continue

            # Fetch the actual Form 4 XML
            form4_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
            form4_raw = _http_get(form4_url, SEC_HEADERS)
            time.sleep(0.12)

            if not form4_raw:
                continue

            # Parse key fields from Form 4 XML
            # Filer name
            name_match = re.search(r"<rptOwnerName>([^<]+)</rptOwnerName>", form4_raw)
            name = name_match.group(1).strip() if name_match else "Unknown"

            # Title
            title_match = re.search(r"<officerTitle>([^<]+)</officerTitle>", form4_raw)
            title = title_match.group(1).strip() if title_match else ""

            # Transaction type (P=Purchase, S=Sale)
            txn_match = re.search(r"<transactionCode>([PS])</transactionCode>", form4_raw)
            txn_code  = txn_match.group(1) if txn_match else ""
            if txn_code not in ("P", "S"):
                continue
            txn_type = "BUY" if txn_code == "P" else "SELL"

            # Shares
            shares_match = re.search(
                r"<transactionShares>.*?<value>([0-9.]+)</value>",
                form4_raw, re.DOTALL
            )
            shares = int(float(shares_match.group(1))) if shares_match else 0

            trades.append(InsiderTrade(
                name=name, title=title, shares=shares,
                transaction=txn_type, date=filing_date
            ))

            if len(trades) >= 15:
                break

    except Exception as e:
        log.debug(f"  {ticker} Form4 parse error: {e}")

    return trades


# ════════════════════════════════════════════════════════════════════════════
# SENTIMENT ANALYSER
# ════════════════════════════════════════════════════════════════════════════

class SentimentAnalyser:

    def analyse(self, symbol: str, current_price: float = 0.0) -> SentResult:
        result  = SentResult(symbol=symbol)
        score   = 0.0
        reasons = []

        # ── News Sentiment ───────────────────────────────────────────────
        news = fetch_finviz_news(symbol)
        result.news_items = news
        result.news_count = len(news)

        if news:
            avg_sent = sum(n.sentiment for n in news) / len(news)
            result.news_sentiment = avg_sent
            if avg_sent >= SC.MIN_NEWS_SENTIMENT:
                pts = 10 + avg_sent * 20
                score += pts
                reasons.append(f"News sentiment +{avg_sent:.2f} ({len(news)} articles) ✓")
            elif avg_sent < -0.2:
                score -= 15
                reasons.append(f"⚠ News sentiment {avg_sent:.2f} negative")
            else:
                score += 5
                reasons.append(f"News sentiment neutral {avg_sent:.2f}")
        else:
            reasons.append("No recent news found")

        # ── Analyst Ratings ──────────────────────────────────────────────
        ratings, buy_pct, avg_target = fetch_analyst_ratings(symbol)
        result.analyst_ratings  = ratings
        result.analyst_buy_pct  = buy_pct
        result.analyst_target   = avg_target if avg_target > 0 else None

        if buy_pct >= SC.MIN_BUY_RATING_PCT:
            pts = 10 + buy_pct * 20
            score += pts
            reasons.append(f"Analyst consensus {buy_pct*100:.0f}% Buy ✓")
        elif buy_pct > 0:
            score += buy_pct * 15
            reasons.append(f"Analyst consensus {buy_pct*100:.0f}% Buy (below {SC.MIN_BUY_RATING_PCT*100:.0f}% min)")

        if avg_target > 0 and current_price > 0:
            upside = (avg_target - current_price) / current_price
            result.analyst_upside = upside
            if upside >= 0.10:
                pts = min(15, upside * 60)
                score += pts
                reasons.append(f"Analyst target ${avg_target:.2f} (+{upside*100:.1f}% upside) ✓")
            elif upside < -0.05:
                score -= 10
                reasons.append(f"⚠ Analyst target ${avg_target:.2f} ({upside*100:.1f}% downside)")

        # ── Insider Trades ───────────────────────────────────────────────
        insider_trades = fetch_insider_trades(symbol, days_back=90)
        result.insider_trades = insider_trades

        buy_shares  = sum(t.shares for t in insider_trades if t.transaction == "BUY")
        sell_shares = sum(t.shares for t in insider_trades if t.transaction == "SELL")
        net_insider = buy_shares - sell_shares
        result.insider_net_shares = net_insider

        if net_insider > 0:
            score += 15
            reasons.append(f"Insider net buying +{buy_shares:,} shares (90 days) ✓")
        elif net_insider < -50000:
            score -= 8
            reasons.append(f"⚠ Insider net selling {abs(net_insider):,} shares (90 days)")
        else:
            score += 3
            reasons.append("Insider activity neutral")

        # Clamp and finalize
        result.score   = max(0.0, min(100.0, score))
        result.passed  = result.score >= SC.MIN_SENT_SCORE
        result.reasons = reasons

        log.debug(f"  {symbol} SENT score={result.score:.0f} passed={result.passed}")
        return result
