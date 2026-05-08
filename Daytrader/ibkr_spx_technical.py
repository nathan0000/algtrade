import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

# ====================== LOAD DATA WITH ROBUST DATE CLEANING ======================
conn = sqlite3.connect("ibkr_spx_daytrader.db")
df = pd.read_sql("SELECT date, open, high, low, close FROM spx_5min ORDER BY date", conn)
conn.close()

# Robust cleaning: remove ANY " US/..." suffix and parse
df['date'] = df['date'].str.replace(r' US/.*$', '', regex=True)   # strip any timezone
df['date'] = pd.to_datetime(df['date'], format='%Y%m%d %H:%M:%S', errors='coerce')

# Drop any rows that failed to parse (should be zero)
df = df.dropna(subset=['date'])
df.set_index('date', inplace=True)

# Get today's (or most recent trading day) bars
today_date = df.index.date[-1]
today_df = df[df.index.date == today_date].copy()

print(f"Analysing {len(today_df)} bars for {today_date}...\n")

# ====================== TECHNICAL CALCULATIONS ======================
today_df['ema20'] = today_df['close'].ewm(span=20, adjust=False).mean()

today_open  = today_df['open'].iloc[0]
today_close = today_df['close'].iloc[-1]
today_high  = today_df['high'].max()
today_low   = today_df['low'].min()

daily_range_pct = (today_high - today_low) / today_open * 100

# Linear regression slope (trend strength)
x = np.arange(len(today_df))
slope = np.polyfit(x, today_df['close'], 1)[0]

above_ema = today_close > today_df['ema20'].iloc[-1]

# ====================== CLASSIFICATION ======================
if slope > 0.08 and above_ema and daily_range_pct > 0.75:
    regime = "BULLISH TRENDING"
    bias = "Strong upside momentum — favour debit call spreads or call verticals"
    action = "BUY"

elif slope < -0.08 and not above_ema and daily_range_pct > 0.75:
    regime = "BEARISH TRENDING"
    bias = "Strong downside momentum — favour debit put spreads or put verticals"
    action = "BUY"

else:
    regime = "RANGE-BOUND"
    bias = "Choppy / sideways day — favour credit spreads, iron condors, or short strangles"
    action = "SELL"

# ====================== OUTPUT ======================
print(f"SPX Technical Regime for {today_date}:")
print(f"   → {regime}")
print(f"   Range: {daily_range_pct:.2f}% | Slope: {slope:.4f} | Close vs EMA20: {'Above' if above_ema else 'Below'}")
print(f"\n🎯 Recommended bias: {bias}")
print(f"   Suggested action in ibkr_option_strategy.py → action='{action}'")

print("\nSuggested next step:")
print("1. Open ibkr_option_strategy.py")
print(f"2. Set action=\"{action}\" in build_vertical_spread()")
print("3. Run it — it will auto-pick the best legs for today's regime.")

print("\nRun this file anytime for fresh daily technical regime.")