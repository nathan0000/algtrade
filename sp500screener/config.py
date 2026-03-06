"""
config.py — SP500 Swing Screener Configuration
All parameters in one place. Edit here before running.
"""

# ─── IBKR Connection ────────────────────────────────────────────────────────
IBKR_HOST      = "127.0.0.1"
IBKR_PORT      = 4002          # 7497=TWS paper | 7496=TWS live | 4002=GW paper
IBKR_CLIENT_ID = 10            # use a different ID from your 0DTE bot

# ─── Account ────────────────────────────────────────────────────────────────
ACCOUNT        = "DU3232524"    # replace with your account ID
MAX_POSITIONS  = 10            # max concurrent swing positions
POSITION_RISK_PCT = 0.02       # 2% account risk per position

# ─── Screener Universe ──────────────────────────────────────────────────────
# Set to [] to pull the full live SP500 list from Wikipedia
SYMBOL_OVERRIDE = []           # e.g. ["AAPL", "MSFT"] to test a subset

# ─── Technical Screener Thresholds ──────────────────────────────────────────
class TechConfig:
    # Breakout filter
    BREAKOUT_LOOKBACK_DAYS    = 50        # N-day high breakout
    BREAKOUT_VOL_MULTIPLIER   = 1.5       # volume must be Nx above 20-day avg
    BREAKOUT_ATR_BUFFER       = 0.005     # price must close > high + 0.5% ATR buffer

    # Trend reversal filter
    REVERSAL_RSI_OVERSOLD     = 35        # RSI below this = potential long reversal
    REVERSAL_RSI_OVERBOUGHT   = 65        # RSI above = potential short reversal
    REVERSAL_CONSEC_DOWN_BARS = 5         # minimum consecutive down days before reversal
    REVERSAL_VOL_SPIKE        = 1.8       # volume spike on reversal day
    REVERSAL_HAMMER_RATIO     = 0.33      # body / total range for hammer candle

    # Trend quality (ADX)
    ADX_MIN_TREND             = 25        # ADX above = trending, below = avoid
    ADX_PERIOD                = 14

    # Moving averages
    EMA_FAST                  = 21
    EMA_SLOW                  = 50
    EMA_TREND                 = 200

    # ATR
    ATR_PERIOD                = 14

    # RSI
    RSI_PERIOD                = 14

    # MACD
    MACD_FAST                 = 12
    MACD_SLOW                 = 26
    MACD_SIGNAL               = 9

    # Minimum score to pass technical stage (0–100)
    MIN_TECH_SCORE            = 60

# ─── Fundamental Screener Thresholds ────────────────────────────────────────
class FundConfig:
    # Revenue growth (YoY)
    MIN_REVENUE_GROWTH        = 0.05      # 5%
    # EPS growth (YoY)
    MIN_EPS_GROWTH            = 0.05
    # Profit margin floor
    MIN_PROFIT_MARGIN         = 0.05      # 5%
    # P/E ceiling (growth-adjusted)
    MAX_PE_RATIO              = 60
    # Debt/Equity ceiling
    MAX_DEBT_EQUITY           = 3.0
    # Market cap floor (avoid micro caps)
    MIN_MARKET_CAP_B          = 2.0       # $2B minimum

    # 13F institutional analysis
    MIN_INSTITUTIONAL_OWN_PCT = 0.40      # 40%+ institutional ownership
    MIN_13F_NET_BUY_CHANGE    = 0.0       # net buying > 0 in latest quarter
    TOP_INSTITUTION_BOOST     = True      # boost score if Berkshire/Vanguard/etc owns

    # Minimum score to pass fundamental stage (0–100)
    MIN_FUND_SCORE            = 50

# ─── Sentiment Analysis ─────────────────────────────────────────────────────
class SentConfig:
    # News sentiment thresholds (scored -1 to +1)
    MIN_NEWS_SENTIMENT        = 0.10      # slightly positive minimum
    # Analyst consensus
    MIN_BUY_RATING_PCT        = 0.55      # 55%+ analysts must be Buy/Strong Buy
    # Short interest ceiling
    MAX_SHORT_INTEREST_PCT    = 0.15      # 15% short float max (avoid short squeezes)
    # Options flow (put/call ratio)
    MAX_PUT_CALL_RATIO        = 1.20      # below = more calls = bullish flow
    # Social sentiment score (0-100 scale from aggregation)
    MIN_SOCIAL_SCORE          = 50

    MIN_SENT_SCORE            = 50

# ─── Options Parameters (for position sizing) ───────────────────────────────
class OptionConfig:
    HOLD_WEEKS_MIN            = 2
    HOLD_WEEKS_MAX            = 6
    # Target DTE range for entries
    MIN_DTE                   = 30        # avoid < 30 DTE (theta risk)
    MAX_DTE                   = 60        # ~2 month expiry
    # Delta targets
    LONG_CALL_DELTA           = 0.65      # slightly ITM for swing
    LONG_PUT_DELTA            = -0.65
    # Max IV percentile (don't buy expensive options)
    MAX_IV_PERCENTILE         = 60
    # Spread type preference
    USE_SPREADS               = True      # vertical debit spreads to cap cost
    SPREAD_WIDTH_PCT          = 0.05      # 5% of stock price for spread width

# ─── Pipeline ───────────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES = 60              # re-scan every 60 min during market hours
OUTPUT_FILE           = "screener_results.json"
LOG_FILE              = "screener.log"

# ─── Market Data Type ────────────────────────────────────────────────────────
# Controls what kind of market data IBKR delivers for snapshots/quotes.
# Historical bars (reqHistoricalData) are UNAFFECTED — they use a separate
# IBKR data service and always work with a basic IBKR account.
#
#   1 = Live          requires paid per-exchange subscription
#   2 = Frozen        last known price when market is closed (free)
#   3 = Delayed       15-min delayed prices during market hours (free)
#   4 = Delayed-Frozen  delayed when open + frozen when closed  ← recommended
#
# Use 4 for the screener — you get real prices (15-min delayed) during market
# hours and last-known prices outside hours, all without any subscription.
MARKET_DATA_TYPE = 4
SEC_EDGAR_13F  = "https://data.sec.gov/submissions/"
FINVIZ_BASE    = "https://finviz.com/quote.ashx?t="

# Optional: set API keys for enhanced data
ALPHA_VANTAGE_KEY  = ""   # free tier: 25 req/day
FINNHUB_KEY        = ""   # free tier: sentiment + analyst ratings
