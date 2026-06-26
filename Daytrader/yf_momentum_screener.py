import pandas as pd
import numpy as np
import yfinance as yf
from ta.momentum import RSIIndicator
from tqdm import tqdm
from scipy.stats import rankdata

# ==========================================
# SETTINGS
# ==========================================

LOOKBACK_1M = 21
LOOKBACK_3M = 63

MIN_PRICE = 10
MIN_AVG_VOLUME = 1_000_000

TOP_N = 10

# ==========================================
# LOAD SP500 SYMBOLS
# ==========================================

sp500_url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

sp500 = pd.read_html(sp500_url)[0]

symbols = sp500['Symbol'].tolist()

# Yahoo finance ticker cleanup
symbols = [s.replace(".", "-") for s in symbols]

print(f"Loaded {len(symbols)} SP500 symbols")

# ==========================================
# DOWNLOAD DATA
# ==========================================

data = yf.download(
    tickers=symbols,
    period="6mo",
    auto_adjust=True,
    group_by='ticker',
    threads=True,
    progress=True
)

results = []

# ==========================================
# PROCESS EACH STOCK
# ==========================================

for symbol in tqdm(symbols):

    try:

        df = data[symbol].dropna()

        if len(df) < 100:
            continue

        close = df['Close']
        volume = df['Volume']

        current_price = close.iloc[-1]

        # ----------------------------------
        # Liquidity Filters
        # ----------------------------------

        avg_volume = volume.tail(20).mean()

        if current_price < MIN_PRICE:
            continue

        if avg_volume < MIN_AVG_VOLUME:
            continue

        # ----------------------------------
        # Momentum Metrics
        # ----------------------------------

        ret_1m = (
            close.iloc[-1] / close.iloc[-LOOKBACK_1M] - 1
        )

        ret_3m = (
            close.iloc[-1] / close.iloc[-LOOKBACK_3M] - 1
        )

        # RSI
        rsi = RSIIndicator(close, window=14).rsi().iloc[-1]

        # Moving averages
        sma20 = close.rolling(20).mean().iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1] \
            if len(close) >= 200 else np.nan

        # Trend filter
        bullish_trend = (
            current_price > sma20 > sma50
        )

        # Relative volume
        rel_volume = (
            volume.iloc[-1] / volume.tail(20).mean()
        )

        # Distance from 52w high
        high_52w = close.max()

        dist_from_high = (
            current_price / high_52w
        )

        # ----------------------------------
        # Additional Momentum Filters
        # ----------------------------------

        if not bullish_trend:
            continue

        if rsi < 55:
            continue

        if dist_from_high < 0.85:
            continue

        # ----------------------------------
        # Store
        # ----------------------------------

        results.append({
            'Symbol': symbol,
            'Price': round(current_price, 2),
            '1M Return %': round(ret_1m * 100, 2),
            '3M Return %': round(ret_3m * 100, 2),
            'RSI': round(rsi, 2),
            'Rel Volume': round(rel_volume, 2),
            'Dist 52W High': round(dist_from_high, 2)
        })

    except Exception as e:
        print(f"{symbol}: {e}")

# ==========================================
# BUILD RANKING
# ==========================================

df = pd.DataFrame(results)

if len(df) == 0:
    print("No candidates found")
    exit()

# Rank metrics
df['score_1m'] = rankdata(df['1M Return %'])
df['score_3m'] = rankdata(df['3M Return %'])
df['score_rsi'] = rankdata(df['RSI'])
df['score_volume'] = rankdata(df['Rel Volume'])
df['score_high'] = rankdata(df['Dist 52W High'])

# Weighted composite score
df['Momentum Score'] = (
    df['score_1m'] * 0.30 +
    df['score_3m'] * 0.30 +
    df['score_rsi'] * 0.15 +
    df['score_volume'] * 0.15 +
    df['score_high'] * 0.10
)

# Sort
df = df.sort_values(
    'Momentum Score',
    ascending=False
)

# Top 10
top10 = df.head(TOP_N)

# Cleanup
cols = [
    'Symbol',
    'Price',
    '1M Return %',
    '3M Return %',
    'RSI',
    'Rel Volume',
    'Dist 52W High',
    'Momentum Score'
]

print("\n===== TOP MOMENTUM STOCKS =====\n")

print(
    top10[cols].to_string(index=False)
)