# SP500 Swing Screener — Setup & Architecture Guide

## Quick Start

```bash
pip install ibapi pytz

# Test with a few symbols (paper account)
python main.py --symbols AAPL MSFT NVDA TSLA AMZN --port 7497

# Full SP500 scan
python main.py --port 7497 --account 150000

# Scheduled mode (runs every 60 min during market hours)
python main.py --schedule --port 7497 --account 150000

# Print last saved results without re-scanning
python main.py --report
```

---

## Architecture — 4-Stage Pipeline

```
Universe (SP500 ~503 symbols)
         │
         ▼ Stage 1: Technical Screen [IBKR daily bars]
    ┌────────────────────────────────────────────┐
    │ Breakout Detection                          │
    │  • N-day price high breakout               │
    │  • Volume 1.5× avg confirmation            │
    │  • ADX ≥ 25 (trend strength)               │
    │  • EMA21 > EMA50 > EMA200 alignment        │
    │  • MACD above signal line                  │
    │                                            │
    │ Trend Reversal Detection                   │
    │  • RSI oversold/overbought (35/65)         │
    │  • 5+ consecutive directional bars         │
    │  • Volume spike on reversal day            │
    │  • Hammer / Bullish Engulfing pattern      │
    │  • MACD histogram turning                  │
    └────────────────────────────────────────────┘
         │ ~10–20% pass
         ▼ Stage 2: Fundamental Analysis [IBKR + SEC EDGAR + Finviz]
    ┌────────────────────────────────────────────┐
    │ Company Quality                             │
    │  • Revenue growth ≥ 5% YoY                │
    │  • EPS growth ≥ 5% YoY                    │
    │  • Profit margin ≥ 5%                      │
    │  • P/E ≤ 60                               │
    │  • Debt/Equity ≤ 3.0                      │
    │  • Market cap ≥ $2B                        │
    │                                            │
    │ 13F Institutional Analysis                 │
    │  • Institutional ownership ≥ 40%           │
    │  • Net 13F buying (Finviz inst. trans)     │
    │  • Tier-1 holder bonus (Vanguard etc.)     │
    │  • SEC EDGAR company facts API             │
    └────────────────────────────────────────────┘
         │ ~30–50% of tech passes
         ▼ Stage 3: Sentiment Score [Finviz + SEC Form 4]
    ┌────────────────────────────────────────────┐
    │ News Sentiment                              │
    │  • Keyword NLP on Finviz headlines         │
    │  • Polarity score -1 → +1                  │
    │                                            │
    │ Analyst Consensus                          │
    │  • Buy rating % ≥ 55%                     │
    │  • Price target upside ≥ 10%               │
    │                                            │
    │ Insider Activity (SEC Form 4)              │
    │  • Net insider buying (90 days)            │
    │  • Transaction count + size                │
    └────────────────────────────────────────────┘
         │ Final candidates
         ▼ Stage 4: Options Structure [IBKR chain]
    ┌────────────────────────────────────────────┐
    │  • 30–60 DTE window (2–6 week hold)        │
    │  • Slightly ITM debit spread (~0.65 delta) │
    │  • Size: 2% account risk per trade         │
    │  • Black-Scholes cost estimate             │
    │  • Breakeven & max profit calculated       │
    └────────────────────────────────────────────┘
         │
         ▼ Ranked output (composite score)
    screener_results.json
```

---

## Composite Scoring Weights

| Stage | Weight | Min Score |
|-------|--------|-----------|
| Technical | **40%** | 60/100 |
| Fundamental | **35%** | 50/100 |
| Sentiment | **25%** | 50/100 |

---

## File Structure

```
sp500_swing_screener/
├── main.py           ← Entry point + CLI + scheduler
├── pipeline.py       ← 4-stage orchestrator
├── technical.py      ← All TA indicators (pure Python)
├── fundamental.py    ← Fundamentals + 13F + EDGAR
├── sentiment.py      ← News NLP + analyst + insider
├── options.py        ← Option structure builder + BS pricing
├── ibkr_client.py    ← IBKR native API wrapper
├── config.py         ← All parameters
├── requirements.txt
└── SETUP.md          ← This file
```

---

## IBKR TWS Setup

Same as 0DTE bot:
- Enable API in TWS: Edit → Global Configuration → API → Settings
- Allow 127.0.0.1, disable read-only
- Paper port: **7497** | Live port: **7496**

### Required IBKR subscriptions:
- US Stocks (for daily bars)
- IBKR Fundamentals (for ReportSnapshot XML)
- US Options (for option chain params)

---

## Enhancing 13F Data (Optional)

The free pipeline uses Finviz for institutional data. For richer 13F analysis:

```python
# In fundamental.py, replace fetch_finviz_institutional() with:
# - WhaleWisdom API (free tier): https://whalewisdom.com/api
# - SEC EDGAR full-text search for 13F-HR filings
# - Quiver Quantitative (free): https://api.quiverquant.com
```

---

## Example Output

```
#  SYMBOL   SIGNAL               COMP  TECH  FUND  SENT  SECTOR               OPTION STRUCTURE
─────────────────────────────────────────────────────────────────────────────────────────────
1  NVDA     🚀BREAKOUT             84.2    92    79    72  Technology           DEBIT_CALL_SPREAD
2  META     🚀BREAKOUT             76.8    88    65    68  Communication        DEBIT_CALL_SPREAD
3  AAPL     ↗️REVERSAL_LONG        71.4    74    71    68  Technology           DEBIT_CALL_SPREAD
4  JPM      ↗️REVERSAL_LONG        68.2    71    68    63  Financials           DEBIT_CALL_SPREAD
5  XOM      ↘️REVERSAL_SHORT       65.1    77    55    58  Energy               DEBIT_PUT_SPREAD
```

---

## Caveats & Disclaimer

- Always paper trade first (--port 7497)
- SEC EDGAR rate limit: 10 req/sec — built-in delays handle this
- Finviz scraping: use responsibly (0.3–0.5s delays built in)
- Options structures are **estimated** — verify live pricing before submitting
- This software is for educational purposes only
