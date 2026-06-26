#!/usr/bin/env python3
"""
Multi-Market Momentum Screener
───────────────────────────────
Screens stocks from the S&P 500 (US) and Hang Seng / HKSE (HK).
Fetches symbol lists automatically, downloads historical data from
Yahoo Finance, calculates six momentum indicators, filters weak
stocks, scores each candidate, and prints the top results.

Requirements:
    pip install yfinance pandas numpy requests beautifulsoup4 lxml

Usage:
    python momentum_screener.py                      # both markets, top 10 each
    python momentum_screener.py --market us          # S&P 500 only
    python momentum_screener.py --market hk          # HKSE only
    python momentum_screener.py --market all --top 20
    python momentum_screener.py --rsi-low 50 --max-dist 15
    python momentum_screener.py --combined           # merge both into one ranked list
    python momentum_screener.py --teams WEBHOOK_URL  # post results to MS Teams
    python momentum_screener.py --teams-env           # read webhook from env var TEAMS_WEBHOOK_URL
    python momentum_screener.py --options             # scan 7-14 DTE calls for top candidates
    python momentum_screener.py --options --dte-min 7 --dte-max 14 --delta-min 0.30 --delta-max 0.45

HKSE notes:
    • Yahoo Finance uses the suffix .HK  (e.g. 0700.HK for Tencent)
    • Prices are in HKD; the min-price filter is scaled accordingly
    • Volume filter is also scaled (HK lots are typically 100–1000 shares)
"""

import argparse
import json
import math
import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from datetime import datetime, timedelta
from io import StringIO

# ═══════════════════════════════════════════════════════════
#  MARKET REGISTRY
#  Each entry drives symbol fetching, display, and defaults.
# ═══════════════════════════════════════════════════════════

MARKETS = {
    "us": {
        "label"      : "S&P 500  (US)",
        "currency"   : "USD",
        "flag"       : "🇺🇸",
        "min_price"  : 10.0,
        "min_avg_vol": 500_000,
    },
    "hk": {
        "label"      : "HKSE  (Hong Kong)",
        "currency"   : "HKD",
        "flag"       : "🇭🇰",
        "min_price"  : 1.0,       # HKD; many HKSE blue chips are sub-$100 HKD
        "min_avg_vol": 1_000_000, # HK daily turnover in shares
    },
}

# Shared defaults
DEFAULTS = dict(
    top_n       = 10,
    rsi_low     = 45,
    rsi_high    = 80,
    max_dist    = 30,
    batch_size  = 50,
    period_days = 280,
)


# ═══════════════════════════════════════════════════════════
#  STEP 1 – SYMBOL FETCHING
# ═══════════════════════════════════════════════════════════

# ── US ──────────────────────────────────────────────────────

_US_FALLBACK = [
    "AAPL","MSFT","AMZN","NVDA","GOOGL","META","TSLA","BRK-B","UNH","LLY",
    "JPM","V","XOM","AVGO","PG","MA","HD","CVX","MRK","ABBV","COST","PEP",
    "KO","WMT","BAC","CRM","MCD","TMO","CSCO","ACN","ABT","LIN","DHR","TXN",
    "ADBE","WFC","CMCSA","VZ","NKE","PM","NEE","ORCL","RTX","BMY","QCOM",
    "UPS","SPGI","MS","HON","INTU","AMGN","SBUX","IBM","CAT","DE","GS","BLK",
    "GILD","MDT","AMD","AXP","T","LMT","CVS","SYK","ISRG","ADI","PLD","CI",
    "MDLZ","VRTX","ZTS","MMC","TJX","SO","DUK","COP","ELV","APD","CB","AON",
    "ITW","SHW","GD","NSC","ICE","USB","EMR","BSX","PNC","ETN","REGN","ADP",
    "TMUS","TGT","KLAC","MCK","CME","PSX","HUM","MO","F","GM","NFLX","NOW",
]

def get_us_symbols() -> list[str]:
    """Fetch S&P 500 tickers from GitHub open-data → Wikipedia → fallback."""
    print("📥  [US] Fetching S&P 500 symbols …")
    try:
        url = (
            "https://raw.githubusercontent.com/datasets/"
            "s-and-p-500-companies/main/data/constituents.csv"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text))
        syms = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        print(f"   ✅  {len(syms)} symbols from GitHub.\n")
        return syms
    except Exception as e:
        print(f"   ⚠️  GitHub failed ({e}), trying Wikipedia …")

    try:
        hdrs = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"}
        r = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=hdrs, timeout=20,
        )
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        syms = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        print(f"   ✅  {len(syms)} symbols from Wikipedia.\n")
        return syms
    except Exception as e:
        print(f"   ⚠️  Wikipedia also failed ({e}). Using built-in list.\n")

    print(f"   ℹ️  Using built-in {len(_US_FALLBACK)}-stock representative list.\n")
    return _US_FALLBACK


# ── HK ──────────────────────────────────────────────────────

# Hang Seng Index constituents + broader HKSE blue chips.
# Yahoo Finance format: zero-padded 4-digit code + ".HK"
# Sources tried: Wikipedia HSI page → hardcoded comprehensive list
_HK_FALLBACK = [
    # ── Hang Seng Index core constituents ──
    "0700.HK",  # Tencent
    "0941.HK",  # China Mobile
    "1299.HK",  # AIA Group
    "0005.HK",  # HSBC Holdings
    "0939.HK",  # China Construction Bank
    "1398.HK",  # ICBC
    "2318.HK",  # Ping An Insurance
    "3988.HK",  # Bank of China
    "0388.HK",  # Hong Kong Exchanges
    "2628.HK",  # China Life Insurance
    "0016.HK",  # Sun Hung Kai Properties
    "0001.HK",  # CK Hutchison
    "2388.HK",  # BOC Hong Kong
    "0011.HK",  # Hang Seng Bank
    "0003.HK",  # HK & China Gas
    "0006.HK",  # Power Assets Holdings
    "0002.HK",  # CLP Holdings
    "0823.HK",  # Link REIT
    "0012.HK",  # Henderson Land
    "1038.HK",  # CK Infrastructure
    "0101.HK",  # Hang Lung Properties
    "0017.HK",  # New World Development
    "0083.HK",  # Sino Land
    "1997.HK",  # Wharf Real Estate
    "0004.HK",  # Wharf Holdings
    # ── Tech / Internet ──
    "9988.HK",  # Alibaba (HK-listed)
    "3690.HK",  # Meituan
    "9999.HK",  # NetEase
    "1024.HK",  # Kuaishou
    "9618.HK",  # JD.com (HK)
    "2382.HK",  # Sunny Optical
    "0992.HK",  # Lenovo
    "0981.HK",  # SMIC
    "6969.HK",  # Smoore International
    "0020.HK",  # SJM Holdings
    # ── Financials ──
    "2601.HK",  # China Pacific Insurance
    "1336.HK",  # New China Life
    "1339.HK",  # PICC Property & Casualty
    "6886.HK",  # HTSC (Huatai Securities)
    "3968.HK",  # China Merchants Bank
    "1988.HK",  # Minsheng Banking
    "2066.HK",  # CITIC Securities
    "6030.HK",  # CITIC Securities (another class)
    "1211.HK",  # BYD Company
    # ── Consumer / Retail ──
    "9901.HK",  # New Oriental Education
    "2020.HK",  # ANTA Sports
    "6110.HK",  # Topsports International
    "1929.HK",  # Chow Tai Fook
    "0291.HK",  # China Resources Beer
    "2319.HK",  # China Mengniu Dairy
    "0168.HK",  # Green Court Capital
    "6862.HK",  # Haidilao
    "9961.HK",  # Trip.com Group
    # ── Healthcare ──
    "1177.HK",  # Sino Biopharmaceutical
    "2269.HK",  # Wuxi Biologics
    "0867.HK",  # China Medical System
    "1093.HK",  # CSPC Pharmaceutical
    "2196.HK",  # Shanghai Fosun Pharma
    "6160.HK",  # BeiGene
    # ── Energy / Materials ──
    "0857.HK",  # PetroChina
    "0883.HK",  # CNOOC
    "0386.HK",  # Sinopec Corp
    "1088.HK",  # China Shenhua Energy
    "0358.HK",  # Jiangxi Copper
    "0347.HK",  # Angang Steel
    "1171.HK",  # Yanzhou Coal Mining
    # ── Industrials / Conglomerates ──
    "0066.HK",  # MTR Corporation
    "0293.HK",  # Cathay Pacific
    "1113.HK",  # CK Asset Holdings
    "0267.HK",  # CITIC Limited
    "0688.HK",  # China Overseas Land
    "2007.HK",  # Country Garden Holdings
    "3333.HK",  # Evergrande (distressed, included for completeness)
    "0960.HK",  # Longfor Group
    "1109.HK",  # China Resources Land
    # ── ETFs / REITs ──
    "2800.HK",  # Tracker Fund of HK (HSI ETF)
    "2828.HK",  # Hang Seng H-Share Index ETF
]

import re as _re

def _pad_hk(raw: str) -> "str | None":
    """
    Normalise any scraped HK stock code to Yahoo Finance format (e.g. '0700.HK').

    Handles common Wikipedia artefacts:
      - Non-breaking spaces / Unicode whitespace (\xa0, \u200b, ...)
      - Exchange prefixes like 'SEHK: 941' or 'HKEx:0700'
      - Bare integers: 700  -> '0700.HK'
      - Already-suffixed: 700.HK, 0700.HK, 0700.hk -> '0700.HK'
    Returns None if the string cannot be coerced to a valid numeric code.
    """
    s = str(raw)
    # 1. Flatten whitespace variants (non-breaking space, zero-width, etc.)
    s = s.replace("\xa0", " ").replace("\u200b", "").replace("\u00a0", " ")
    s = s.strip()
    # 2. Strip exchange prefix patterns: "SEHK: 941", "HKEx:700", "HKG:0700"
    s = _re.sub(r"(?i)^(sehk|hkex|hkg|hkse)\s*[:\-]?\s*", "", s)
    s = s.strip()
    # 3. Remove non-alphanumeric chars except the dot
    s = _re.sub(r"[^0-9A-Za-z.]", "", s)
    # 4. Strip .HK / .hk suffix to get bare numeric part
    s = _re.sub(r"(?i)\.hk$", "", s)
    # 5. Must be purely numeric now
    if not s.isdigit():
        return None
    # 6. Strip any excess leading zeros, then pad to exactly 4 digits
    s = s.lstrip('0') or '0'   # '00001' -> '1'; keep '0' for bare zero
    return s.zfill(4) + ".HK"


def _clean_hk_list(raw_list: list) -> list:
    """Run _pad_hk over a list, filter None, deduplicate, preserve order."""
    seen: set = set()
    out: list = []
    for item in raw_list:
        result = _pad_hk(item)
        if result and result not in seen:
            seen.add(result)
            out.append(result)
    return out


def get_hk_symbols() -> list:
    """
    Fetch HKSE tickers.
    Tries Wikipedia's Hang Seng Index constituent page first,
    then falls back to the comprehensive hardcoded list.
    """
    print("📥  [HK] Fetching HKSE symbols …")

    # 1) Wikipedia - HSI constituents table
    try:
        hdrs = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        r = requests.get(
            "https://en.wikipedia.org/wiki/Hang_Seng_Index",
            headers=hdrs, timeout=20,
        )
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        for tbl in tables:
            cols = [str(c).lower() for c in tbl.columns]
            code_col = next(
                (tbl.columns[i] for i, c in enumerate(cols)
                 if "code" in c or "ticker" in c or "stock" in c), None
            )
            if code_col is not None and len(tbl) >= 20:
                raw_codes = tbl[code_col].dropna().astype(str).tolist()
                syms = _clean_hk_list(raw_codes)
                if len(syms) >= 20:
                    print(f"   ✅  {len(syms)} symbols from Wikipedia (cleaned).\n")
                    return syms
                else:
                    print(f"   ⚠️  Wikipedia table found but only {len(syms)} valid codes after cleaning.")
    except Exception as e:
        print(f"   ⚠️  Wikipedia failed ({e}).")

    print(f"   ℹ️  Using built-in {len(_HK_FALLBACK)}-stock HKSE list.\n")
    return list(_HK_FALLBACK)



# ── Dispatcher ───────────────────────────────────────────────

def get_symbols(market: str) -> list[str]:
    if market == "us":
        return get_us_symbols()
    elif market == "hk":
        return get_hk_symbols()
    raise ValueError(f"Unknown market: {market!r}")


# ═══════════════════════════════════════════════════════════
#  STEP 2 – DOWNLOAD HISTORY
# ═══════════════════════════════════════════════════════════

def download_all(
    symbols    : list[str],
    period_days: int = DEFAULTS["period_days"],
    batch_size : int = DEFAULTS["batch_size"],
    label      : str = "",
) -> dict[str, pd.DataFrame]:
    """Batch-download OHLCV history. Returns {symbol: DataFrame}."""
    end   = datetime.today()
    start = end - timedelta(days=period_days)
    all_data: dict[str, pd.DataFrame] = {}
    total_batches = math.ceil(len(symbols) / batch_size)
    tag = f"[{label}] " if label else ""

    print(
        f"📡  {tag}Downloading {period_days}d history for"
        f" {len(symbols)} symbols in {total_batches} batches …"
    )

    for i in range(0, len(symbols), batch_size):
        batch     = symbols[i : i + batch_size]
        batch_num = i // batch_size + 1
        print(f"   [{batch_num:>2}/{total_batches}]  {len(batch)} symbols …", end=" ", flush=True)

        try:
            raw = yf.download(
                batch,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            print(f"ERROR – {exc}")
            continue

        ok = 0
        if isinstance(raw.columns, pd.MultiIndex):
            for sym in batch:
                try:
                    df = raw.xs(sym, axis=1, level=1).dropna(how="all")
                    if len(df) > 60:
                        all_data[sym] = df
                        ok += 1
                except Exception:
                    pass
        else:
            sym = batch[0]
            df  = raw.dropna(how="all")
            if len(df) > 60:
                all_data[sym] = df
                ok += 1

        print(f"{ok} ok")
        time.sleep(0.25)

    print(f"\n   ✅  Valid data for {len(all_data)} symbols.\n")
    return all_data


# ═══════════════════════════════════════════════════════════
#  STEP 3 – INDICATORS
# ═══════════════════════════════════════════════════════════

def _rsi(close: pd.Series, period: int = 14) -> float:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def _rel_vol(df: pd.DataFrame, window: int = 20) -> float:
    if "Volume" not in df.columns or len(df) < window + 1:
        return 1.0
    avg = df["Volume"].iloc[-(window + 1) : -1].mean()
    return float(df["Volume"].iloc[-1] / avg) if avg > 0 else 1.0


def _ma_score(close: pd.Series) -> int:
    score = 0
    price = float(close.iloc[-1])
    if len(close) >= 20:
        ma20 = float(close.rolling(20).mean().iloc[-1])
        if price > ma20:
            score += 1
        if len(close) >= 50:
            ma50 = float(close.rolling(50).mean().iloc[-1])
            if ma20 > ma50:
                score += 1
            if len(close) >= 200:
                ma200 = float(close.rolling(200).mean().iloc[-1])
                if ma50 > ma200:
                    score += 1
    return score


def calc_indicators(
    sym      : str,
    df       : pd.DataFrame,
    min_price: float,
    market   : str,
) -> dict | None:
    close = df["Close"].dropna()
    if len(close) < 63:
        return None

    price = float(close.iloc[-1])
    if price < min_price:
        return None

    ret_1m = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else None
    ret_3m = (price / float(close.iloc[-63]) - 1) * 100 if len(close) >= 63 else None

    high_52w = float(close.tail(252).max())
    dist_52w = (price / high_52w - 1) * 100

    avg_vol = 0.0
    if "Volume" in df.columns and len(df) >= 20:
        avg_vol = float(df["Volume"].tail(20).mean())

    return {
        "symbol"    : sym,
        "market"    : market,
        "price"     : round(price, 3 if market == "hk" else 2),
        "ret_1m"    : round(ret_1m, 2) if ret_1m is not None else None,
        "ret_3m"    : round(ret_3m, 2) if ret_3m is not None else None,
        "rsi"       : round(_rsi(close), 1),
        "rel_vol"   : round(_rel_vol(df), 2),
        "ma_score"  : _ma_score(close),
        "dist_52w"  : round(dist_52w, 2),
        "avg_vol_20": avg_vol,
    }


# ═══════════════════════════════════════════════════════════
#  STEP 4 – SCORE & FILTER
# ═══════════════════════════════════════════════════════════

def momentum_score(row: pd.Series) -> float:
    """
    Composite momentum (market-agnostic, return-based).
      35%  3-month return
      25%  1-month return
      20%  MA trend layers   (0–3 stars → 0/10/20/30 pts)
      10%  RSI zone          (55–80 preferred)
      10%  Relative volume
    """
    s  = 0.0
    s += 0.35 * (row["ret_3m"] or 0)
    s += 0.25 * (row["ret_1m"] or 0)
    s += 0.20 * row["ma_score"] * 10
    s += 0.10 * max(0, min(row["rsi"] - 50, 30))
    s += 0.10 * min(row["rel_vol"], 3) * 10
    return round(s, 3)


def filter_and_score(
    records    : list[dict],
    min_avg_vol: float,
    rsi_low    : float,
    rsi_high   : float,
    max_dist   : float,
    market_tag : str = "",
) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return df
    before = len(df)

    df = df[df["avg_vol_20"] >= min_avg_vol]
    df = df[df["rsi"].between(rsi_low, rsi_high)]
    df = df[df["dist_52w"] >= -max_dist]
    df = df[df["ret_1m"].notna() & df["ret_3m"].notna()]
    df = df[df["ret_1m"] > 0]
    df = df[df["ma_score"] >= 1]

    tag = f"[{market_tag}] " if market_tag else ""
    print(f"   {tag}Filters: {before} → {len(df)} pass ({before - len(df)} removed).\n")

    df["score"] = df.apply(momentum_score, axis=1)
    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════
#  STEP 5 – DISPLAY
# ═══════════════════════════════════════════════════════════

_STARS = {3: "★★★", 2: "★★☆", 1: "★☆☆", 0: "☆☆☆"}
_W = 112

def _bar(score: float, maximum: float = 30.0, width: int = 12) -> str:
    filled = int(round(max(0, min(score, maximum)) / maximum * width))
    return "█" * filled + "░" * (width - filled)

_MARKET_META = {
    "us": {"flag": "🇺🇸", "currency": "USD", "label": "S&P 500  (US)"},
    "hk": {"flag": "🇭🇰", "currency": "HKD", "label": "HKSE  (Hong Kong)"},
}

def display_results(
    df         : pd.DataFrame,
    top_n      : int,
    market     : str,
    show_market: bool = False,
) -> None:
    top  = df.head(top_n).copy()
    meta = _MARKET_META.get(market, {"flag": "🌐", "currency": "???", "label": market.upper()})
    eq   = "═" * _W
    hd   = "─" * _W

    sym_w = 12 if show_market else 9   # wider symbol col in combined view

    print("\n" + eq)
    print(f"  {meta['flag']}  {meta['label']} — Top {top_n} Momentum Candidates")
    print(f"  📅  {datetime.today().strftime('%A, %d %B %Y  %H:%M')}")
    print(eq)
    hdr = (
        f"  {'#':<4}"
        + (f"{'Mkt':<5}" if show_market else "")
        + f"{'Symbol':<{sym_w}}{'Price':>10}"
        + f"{'1M %':>8}{'3M %':>8}{'RSI':>7}{'RVol':>7}"
        + f"  {'MA':<6}{'52W %':>7}{'Score':>8}  {'Strength'}"
    )
    print(hdr)
    print(hd)

    for idx, row in top.iterrows():
        rank  = idx + 1
        stars = _STARS.get(int(row["ma_score"]), "☆☆☆")
        bar   = _bar(row["score"])
        dist  = f"{row['dist_52w']:+.1f}%"
        r1m   = f"{row['ret_1m']:+.1f}%" if row["ret_1m"] is not None else "  N/A"
        r3m   = f"{row['ret_3m']:+.1f}%" if row["ret_3m"] is not None else "  N/A"
        mkt   = row.get("market", market)
        cur   = _MARKET_META.get(mkt, {}).get("currency", "")

        line = (
            f"  {rank:<4}"
            + (f"{mkt.upper():<5}" if show_market else "")
            + f"{row['symbol']:<{sym_w}}"
            + f"{row['price']:>9.2f} {cur[:3] if show_market else ''}"
            + f"{r1m:>8}{r3m:>8}"
            + f"{row['rsi']:>7.1f}"
            + f"{row['rel_vol']:>7.2f}x"
            + f"  {stars:<6}{dist:>7}"
            + f"{row['score']:>8.1f}  {bar}"
        )
        print(line)

    print(hd)
    _print_legend(eq)


def _print_legend(eq: str) -> None:
    print()
    print("  LEGEND")
    print("  1M % / 3M %  : price return vs 1 month / 3 months ago")
    print("  RSI          : 14-period Relative Strength Index (Wilder smoothing)")
    print("  RVol         : last session volume ÷ 20-day avg volume")
    print("  MA           : ★ = price>MA20 · ★ = MA20>MA50 · ★ = MA50>MA200")
    print("  52W %        : % distance from the 52-week high  (0 % = AT the high)")
    print("  Score        : 35% 3M-ret + 25% 1M-ret + 20% MA + 10% RSI + 10% RVol")
    print("  Strength     : visual bar proportional to score (max ≈ 30)")
    print(eq)
    print()
    print("  ⚠️  DISCLAIMER  Educational / research use only. Not financial advice.")
    print("      Past momentum ≠ future returns. Always do your own due diligence.")
    print(eq + "\n")



# ═══════════════════════════════════════════════════════════
#  MICROSOFT TEAMS NOTIFICATION
# ═══════════════════════════════════════════════════════════

_MA_LABEL = {3: "★★★ Full stack", 2: "★★☆ Partial", 1: "★☆☆ Weak", 0: "☆☆☆ None"}


def _teams_fact(name: str, value: str) -> dict:
    return {"name": name, "value": value}


def _row_to_section(rank: int, row: pd.Series) -> dict:
    """Convert one screener row into an Adaptive Card FactSet section."""
    mkt     = row.get("market", "us")
    meta    = _MARKET_META.get(mkt, {"flag": "🌐", "currency": "???"})
    flag    = meta["flag"]
    cur     = meta["currency"]
    ma_lbl  = _MA_LABEL.get(int(row["ma_score"]), "—")
    dist    = f"{row['dist_52w']:+.1f}%"
    r1m     = f"{row['ret_1m']:+.1f}%" if row.get("ret_1m") is not None else "N/A"
    r3m     = f"{row['ret_3m']:+.1f}%" if row.get("ret_3m") is not None else "N/A"

    return {
        "type": "Container",
        "style": "emphasis",
        "items": [
            {
                "type": "TextBlock",
                "text": f"#{rank}  {flag} **{row['symbol']}**  —  {row['price']:.2f} {cur}",
                "weight": "bolder",
                "size": "medium",
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    _teams_fact("1M / 3M return", f"{r1m}  /  {r3m}"),
                    _teams_fact("RSI (14)",        f"{row['rsi']:.1f}"),
                    _teams_fact("Rel. Volume",     f"{row['rel_vol']:.2f}×"),
                    _teams_fact("MA structure",    ma_lbl),
                    _teams_fact("Distance 52W H",  dist),
                    _teams_fact("Score",           f"{row['score']:.1f}"),
                ],
            },
        ],
        "spacing": "small",
        "separator": rank > 1,
    }


def build_teams_payload(
    results   : dict,          # {market: DataFrame}
    top_n     : int,
    combined  : bool = False,
) -> dict:
    """
    Build an Adaptive Card payload for the Teams Incoming Webhook.
    Works for single-market, per-market, and combined modes.
    """
    now      = datetime.now().strftime("%d %b %Y  %H:%M")
    sections = []

    if combined and len(results) > 1:
        # Merge and re-rank
        merged = pd.concat(list(results.values()), ignore_index=True)
        merged = merged.sort_values("score", ascending=False).head(top_n * len(results)).reset_index(drop=True)
        title  = f"🌐 Combined Momentum Ranking — {now}"
        for i, (_, row) in enumerate(merged.iterrows()):
            sections.append(_row_to_section(i + 1, row))
    else:
        title_parts = []
        for mkt, df in results.items():
            if df.empty:
                continue
            meta = _MARKET_META.get(mkt, {"flag": "🌐", "label": mkt.upper()})
            title_parts.append(f"{meta['flag']} {meta['label']}")
            # Market sub-header
            sections.append({
                "type": "TextBlock",
                "text": f"**{meta['flag']} {meta['label']}**",
                "weight": "bolder",
                "size": "large",
                "color": "accent",
                "spacing": "medium" if sections else "none",
            })
            for i, (_, row) in enumerate(df.head(top_n).iterrows()):
                sections.append(_row_to_section(i + 1, row))
        title = "📈 Momentum Screener — " + "  +  ".join(title_parts) + f"  |  {now}"

    body = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "bolder",
            "size": "large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "Indicators: 1M/3M return · RSI(14) · Relative volume · "
                "MA trend structure · Distance from 52W high.  "
                "Score = 35% 3M + 25% 1M + 20% MA + 10% RSI + 10% RVol."
            ),
            "size": "small",
            "isSubtle": True,
            "wrap": True,
        },
        {"type": "Separator"},
        *sections,
        {"type": "Separator"},
        {
            "type": "TextBlock",
            "text": "⚠️ Educational / research use only. Not financial advice.",
            "size": "small",
            "isSubtle": True,
            "wrap": True,
        },
    ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }


def post_to_teams(webhook_url: str, payload: dict) -> bool:
    """
    POST the Adaptive Card payload to a Teams Incoming Webhook.
    Returns True on success, False on failure (prints reason).
    """
    print("📨  Posting results to Microsoft Teams …")
    try:
        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=15,
        )
        if resp.status_code in (200, 202):
            print(f"   ✅  Teams notification sent (HTTP {resp.status_code}).\n")
            return True
        else:
            print(
                f"   ❌  Teams returned HTTP {resp.status_code}: {resp.text[:200]}\n"
            )
            return False
    except requests.exceptions.ConnectionError as e:
        print(f"   ❌  Connection error: {e}\n")
        return False
    except requests.exceptions.Timeout:
        print("   ❌  Request timed out after 15s.\n")
        return False
    except Exception as e:
        print(f"   ❌  Unexpected error: {e}\n")
        return False


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Multi-Market Momentum Screener (S&P 500 + HKSE)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--market", choices=["us", "hk", "all"], default="all",
        help="Market to screen: us = S&P 500, hk = HKSE, all = both",
    )
    p.add_argument(
        "--combined", action="store_true",
        help="Merge US and HK into a single ranked list (implies --market all)",
    )
    p.add_argument("--top",       type=int,   default=DEFAULTS["top_n"],       help="Candidates to display per market")
    p.add_argument("--rsi-low",   type=float, default=DEFAULTS["rsi_low"],     help="RSI lower bound filter")
    p.add_argument("--rsi-high",  type=float, default=DEFAULTS["rsi_high"],    help="RSI upper bound filter")
    p.add_argument("--max-dist",  type=float, default=DEFAULTS["max_dist"],    help="Max %% below 52-week high")
    p.add_argument("--min-vol",   type=int,   default=0,
                   help="Override min avg daily volume (0 = use market default)")
    p.add_argument("--min-price", type=float, default=0.0,
                   help="Override min price (0 = use market default)")
    p.add_argument("--period",    type=int,   default=DEFAULTS["period_days"], help="History window (days)")
    p.add_argument("--batch",     type=int,   default=DEFAULTS["batch_size"],  help="Download batch size")

    # ── Teams notification ──────────────────────────────────────────────────
    tg = p.add_argument_group("Microsoft Teams")
    tg.add_argument(
        "--teams",
        metavar="WEBHOOK_URL",
        default=None,
        help="Post results to this Teams Incoming Webhook URL",
    )
    tg.add_argument(
        "--teams-env",
        action="store_true",
        help="Read webhook URL from env var TEAMS_WEBHOOK_URL",
    )
    tg.add_argument(
        "--teams-only",
        action="store_true",
        help="Suppress terminal output; only post to Teams (requires --teams or --teams-env)",
    )

    # ── Options scanning ────────────────────────────────────────────────────
    og = p.add_argument_group("Options signals")
    og.add_argument(
        "--options",
        action="store_true",
        help="Scan 7-14 DTE call options for top momentum candidates (US only)",
    )
    og.add_argument(
        "--dte-min",  type=int,   default=OPT_DTE_MIN,
        help="Minimum days to expiry for option scan",
    )
    og.add_argument(
        "--dte-max",  type=int,   default=OPT_DTE_MAX,
        help="Maximum days to expiry for option scan",
    )
    og.add_argument(
        "--delta-min", type=float, default=OPT_DELTA_MIN,
        help="Minimum delta for option sweet spot",
    )
    og.add_argument(
        "--delta-max", type=float, default=OPT_DELTA_MAX,
        help="Maximum delta for option sweet spot",
    )
    return p.parse_args()


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def run_market(
    market  : str,
    args    : argparse.Namespace,
) -> pd.DataFrame:
    """Download, calculate, filter and score one market. Returns ranked DataFrame."""
    cfg       = MARKETS[market]
    min_price = args.min_price if args.min_price > 0 else cfg["min_price"]
    min_vol   = args.min_vol   if args.min_vol   > 0 else cfg["min_avg_vol"]

    symbols = get_symbols(market)
    if not symbols:
        print(f"❌  [{market.upper()}] No symbols found.")
        return pd.DataFrame()

    all_data = download_all(
        symbols,
        period_days=args.period,
        batch_size=args.batch,
        label=market.upper(),
    )
    if not all_data:
        print(f"❌  [{market.upper()}] No data downloaded.")
        return pd.DataFrame()

    print(f"🔢  [{market.upper()}] Calculating indicators …")
    records = []
    for sym, df in all_data.items():
        row = calc_indicators(sym, df, min_price=min_price, market=market)
        if row:
            records.append(row)
    print(f"   ✅  {len(records)} symbols with valid indicators.\n")

    if not records:
        return pd.DataFrame()

    print(f"🔍  [{market.upper()}] Filtering & scoring …")
    ranked = filter_and_score(
        records,
        min_avg_vol=min_vol,
        rsi_low=args.rsi_low,
        rsi_high=args.rsi_high,
        max_dist=args.max_dist,
        market_tag=market.upper(),
    )
    return ranked

# ═══════════════════════════════════════════════════════════
#  OPTIONS ENGINE
#  Finds 7-14 DTE calls in the delta sweet spot (0.30-0.45)
#  for US momentum candidates. Computes full Black-Scholes
#  Greeks from Yahoo Finance implied volatility.
#  HK stocks are skipped (no options data on Yahoo Finance).
# ═══════════════════════════════════════════════════════════

from scipy.stats import norm as _norm
import math as _math

# ── Config ───────────────────────────────────────────────────
OPT_DTE_MIN       = 7       # minimum days to expiry
OPT_DTE_MAX       = 14      # maximum days to expiry
OPT_DELTA_MIN     = 0.30    # sweet spot lower bound
OPT_DELTA_MAX     = 0.45    # sweet spot upper bound
OPT_MIN_OI        = 50      # minimum open interest (liquidity)
OPT_MIN_VOL       = 10      # minimum option volume
OPT_MAX_SPREAD_PCT = 0.20   # max bid-ask spread as % of mid (20%)
RISK_FREE_RATE    = 0.053   # US 3-month T-bill approx (update as needed)

# HK has no listed options on Yahoo — mark explicitly
_MARKETS_WITH_OPTIONS = {"us"}


# ── Black-Scholes Greeks ─────────────────────────────────────

def _bs_greeks(
    S: float,    # spot price
    K: float,    # strike
    T: float,    # time to expiry in years
    r: float,    # risk-free rate
    sigma: float # implied volatility (annualised)
) -> dict:
    """
    Compute full Black-Scholes Greeks for a European call.
    Returns dict with keys: delta, gamma, theta, vega, rho, d1, d2.
    Returns all-zero dict if inputs are invalid.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "rho": 0.0, "d1": 0.0, "d2": 0.0}
    try:
        d1 = (_math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _math.sqrt(T))
        d2 = d1 - sigma * _math.sqrt(T)

        delta = float(_norm.cdf(d1))
        gamma = float(_norm.pdf(d1) / (S * sigma * _math.sqrt(T)))
        # Theta per calendar day (divide annual by 365)
        theta = float(
            (-S * _norm.pdf(d1) * sigma / (2 * _math.sqrt(T))
             - r * K * _math.exp(-r * T) * _norm.cdf(d2))
            / 365
        )
        vega  = float(S * _norm.pdf(d1) * _math.sqrt(T) / 100)  # per 1% IV move
        rho   = float(K * T * _math.exp(-r * T) * _norm.cdf(d2) / 100)

        return {"delta": delta, "gamma": gamma, "theta": theta,
                "vega": vega, "rho": rho, "d1": d1, "d2": d2}
    except Exception:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "rho": 0.0, "d1": 0.0, "d2": 0.0}


# ── Option signal quality score ───────────────────────────────

def _option_signal_score(opt: dict, stock_score: float) -> float:
    """
    Rank option candidates within a ticker.
    Rewards:
      • Delta closest to 0.375 (midpoint of sweet spot)
      • Higher open interest (liquidity)
      • Tighter bid-ask spread
      • Underlying momentum score carries through
    """
    delta_penalty = abs(opt["delta"] - 0.375) * 10   # 0 = perfect
    oi_score      = min(_math.log1p(opt["open_interest"]) / 10, 1.0)
    spread_score  = max(0, 1 - opt["spread_pct"] / OPT_MAX_SPREAD_PCT)
    return (
        stock_score * 0.50
        - delta_penalty * 0.30
        + oi_score      * 0.10
        + spread_score  * 0.10
    )


# ── Fetch options chain for one ticker ───────────────────────

def fetch_option_signals(
    symbol      : str,
    spot        : float,
    market      : str,
    stock_score : float,
    dte_min     : int = OPT_DTE_MIN,
    dte_max     : int = OPT_DTE_MAX,
    delta_min   : float = OPT_DELTA_MIN,
    delta_max   : float = OPT_DELTA_MAX,
) -> list[dict]:
    """
    Fetch the options chain for *symbol*, filter to 7-14 DTE calls in
    the delta sweet spot, compute Greeks, rank, and return candidate list.
    Returns [] if the market doesn't support options or no chain found.
    """
    if market not in _MARKETS_WITH_OPTIONS:
        return []

    try:
        ticker = yf.Ticker(symbol)
        expirations = ticker.options          # tuple of 'YYYY-MM-DD' strings
    except Exception as e:
        print(f"      ⚠️  {symbol}: could not fetch expirations ({e})")
        return []

    if not expirations:
        return []

    today = datetime.today().date()
    candidates: list[dict] = []

    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        dte = (exp_date - today).days
        if dte < dte_min:
            continue
        if dte > dte_max:
            break    # expirations are sorted ascending; no point scanning further

        try:
            chain  = ticker.option_chain(exp_str)
            calls  = chain.calls.copy()
        except Exception:
            continue

        if calls.empty:
            continue

        # Ensure required columns exist
        required = {"strike", "bid", "ask", "impliedVolatility", "openInterest"}
        if not required.issubset(calls.columns):
            continue

        calls = calls.dropna(subset=["strike", "bid", "ask", "impliedVolatility"])
        calls = calls[calls["bid"] > 0]

        T = dte / 365.0

        for _, row in calls.iterrows():
            K     = float(row["strike"])
            bid   = float(row["bid"])
            ask   = float(row["ask"])
            iv    = float(row["impliedVolatility"])
            oi    = int(row.get("openInterest", 0) or 0)
            vol   = int(row.get("volume", 0) or 0)

            # Skip illiquid
            if oi < OPT_MIN_OI:
                continue

            mid = (bid + ask) / 2
            if mid <= 0:
                continue

            spread_pct = (ask - bid) / mid
            if spread_pct > OPT_MAX_SPREAD_PCT:
                continue

            # Black-Scholes Greeks
            greeks = _bs_greeks(spot, K, T, RISK_FREE_RATE, iv)
            delta  = greeks["delta"]

            if not (delta_min <= delta <= delta_max):
                continue

            # Moneyness label
            moneyness_pct = (K / spot - 1) * 100
            if moneyness_pct < -1:
                mono_label = "ITM"
            elif moneyness_pct > 1:
                mono_label = "OTM"
            else:
                mono_label = "ATM"

            opt = {
                "symbol"        : symbol,
                "market"        : market,
                "spot"          : spot,
                "expiry"        : exp_str,
                "dte"           : dte,
                "strike"        : K,
                "moneyness"     : mono_label,
                "moneyness_pct" : round(moneyness_pct, 1),
                "bid"           : bid,
                "ask"           : ask,
                "mid"           : round(mid, 3),
                "spread_pct"    : round(spread_pct, 4),
                "iv"            : round(iv, 4),
                "open_interest" : oi,
                "volume"        : vol,
                "delta"         : round(delta, 4),
                "gamma"         : round(greeks["gamma"], 6),
                "theta"         : round(greeks["theta"], 4),
                "vega"          : round(greeks["vega"], 4),
                "contract"      : str(row.get("contractSymbol", f"{symbol}{exp_str}C{K:.0f}")),
                "stock_score"   : stock_score,
            }
            opt["signal_score"] = _option_signal_score(opt, stock_score)
            candidates.append(opt)

    # Return top-3 by signal score per ticker
    candidates.sort(key=lambda x: x["signal_score"], reverse=True)
    return candidates[:3]


# ── Scan all top candidates ───────────────────────────────────

def scan_options(
    results : dict,       # {market: ranked_DataFrame}
    top_n   : int,
    dte_min : int = OPT_DTE_MIN,
    dte_max : int = OPT_DTE_MAX,
) -> list[dict]:
    """
    For every top-N stock in *results*, attempt to fetch option signals.
    Returns flat list of best option per ticker (for US only).
    """
    signals: list[dict] = []

    for mkt, df in results.items():
        if mkt not in _MARKETS_WITH_OPTIONS:
            if not df.empty:
                print(f"   ℹ️  [{mkt.upper()}] Options not available on Yahoo Finance — skipping.\n")
            continue

        top = df.head(top_n)
        print(f"🎯  [{mkt.upper()}] Scanning options chains for {len(top)} stocks …")
        print(f"     DTE window: {dte_min}–{dte_max} days | "
              f"Delta sweet spot: {OPT_DELTA_MIN:.2f}–{OPT_DELTA_MAX:.2f}\n")

        for _, row in top.iterrows():
            sym   = row["symbol"]
            spot  = row["price"]
            score = row["score"]
            print(f"   🔎  {sym} (spot: {spot:.2f}) …", end=" ", flush=True)

            opts = fetch_option_signals(sym, spot, mkt, score, dte_min, dte_max)
            if opts:
                best = opts[0]
                print(
                    f"found {len(opts)} candidate(s)  "
                    f"→  best: {best['expiry']} C{best['strike']:.0f}  "
                    f"Δ={best['delta']:.3f}  mid=${best['mid']:.2f}"
                )
                signals.append(best)
            else:
                print("no options in range")

            time.sleep(0.3)   # rate-limit courtesy

        print()

    return signals


# ── Terminal display ──────────────────────────────────────────

def display_option_signals(signals: list[dict]) -> None:
    if not signals:
        print("⚠️   No option signals found in the 7–14 DTE window.\n")
        return

    eq = "═" * 120
    hd = "─" * 120
    now = datetime.today().strftime("%d %b %Y  %H:%M")

    print("\n" + eq)
    print(f"  🎯  OPTIONS TRADING SIGNALS  —  {now}")
    print(f"  Strategy: BUY CALL  |  DTE: {OPT_DTE_MIN}–{OPT_DTE_MAX} days  "
          f"|  Delta sweet spot: {OPT_DELTA_MIN:.2f}–{OPT_DELTA_MAX:.2f}")
    print(eq)
    print(
        f"  {'#':<3}{'Symbol':<8}{'Expiry':<12}{'Strike':>8}{'Mono':>5}"
        f"{'DTE':>5}{'Mid $':>8}{'Bid':>7}{'Ask':>7}"
        f"{'IV':>7}{'Delta':>7}{'Gamma':>8}{'Theta':>8}{'Vega':>7}"
        f"{'OI':>7}{'Vol':>7}  Signal"
    )
    print(hd)

    for i, s in enumerate(signals):
        mono_sym = {"ITM": "🟢", "ATM": "🟡", "OTM": "🔵"}.get(s["moneyness"], "  ")
        print(
            f"  {i+1:<3}{s['symbol']:<8}{s['expiry']:<12}"
            f"{s['strike']:>8.1f}{mono_sym:>5}"
            f"{s['dte']:>5}"
            f"{s['mid']:>8.2f}{s['bid']:>7.2f}{s['ask']:>7.2f}"
            f"{s['iv']*100:>6.1f}%"
            f"{s['delta']:>7.3f}{s['gamma']:>8.5f}"
            f"{s['theta']:>8.4f}{s['vega']:>7.3f}"
            f"{s['open_interest']:>7,}{s['volume']:>7,}"
            f"  {s['contract']}"
        )

    print(hd)
    print()
    print("  COLUMN GUIDE")
    print("  Mono    : 🟢 ITM (delta > 0.5+)  🟡 ATM  🔵 OTM")
    print("  Mid $   : (bid + ask) / 2  — suggested limit order price")
    print("  IV      : implied volatility annualised")
    print("  Delta   : $/$ sensitivity to spot move (target: 0.30–0.45)")
    print("  Gamma   : rate of delta change per $1 spot move")
    print("  Theta   : time decay per calendar day (negative = cost of holding)")
    print("  Vega    : P&L per 1% change in IV")
    print("  OI / Vol: open interest / today's volume")
    print(eq)
    print()
    print("  TRADE RATIONALE")
    print(f"  • Momentum-confirmed tickers with 7–14 DTE capture the short-term")
    print(f"    directional move identified by the screener without excessive theta burn.")
    print(f"  • Delta 0.30–0.45 (slightly OTM) maximises leverage while keeping a")
    print(f"    meaningful probability of profit (~35–50% ITM at expiry).")
    print(f"  • Suggested entry: limit order at mid-price or 1 tick above bid.")
    print(f"  • Suggested stop: exit if option loses >50% of premium paid.")
    print(f"  • Suggested target: exit at 2×–3× premium or before last 3 DTE.")
    print(eq)
    print()
    print("  ⚠️  DISCLAIMER  Educational / research use only. Not financial advice.")
    print("      Options carry significant risk including total loss of premium.")
    print("      Verify liquidity and pricing via your broker before trading.")
    print(eq + "\n")


# ── Teams card for options ────────────────────────────────────

def _option_signal_card(rank: int, s: dict) -> dict:
    """One Adaptive Card Container per option signal."""
    mono_emoji = {"ITM": "🟢", "ATM": "🟡", "OTM": "🔵"}.get(s["moneyness"], "")
    direction  = "above" if s["moneyness_pct"] > 0 else "below"
    mono_desc  = (
        f"{s['moneyness']} {mono_emoji}  "
        f"({abs(s['moneyness_pct']):.1f}% {direction} spot)"
    )

    return {
        "type": "Container",
        "style": "emphasis",
        "separator": rank > 1,
        "spacing": "small",
        "items": [
            {
                "type": "TextBlock",
                "text": (
                    f"#{rank}  🎯 **{s['symbol']}**  "
                    f"BUY CALL  {s['expiry']}  "
                    f"Strike {s['strike']:.1f}  "
                    f"({s['dte']} DTE)"
                ),
                "weight": "bolder",
                "size": "medium",
                "wrap": True,
                "color": "good",
            },
            {
                "type": "FactSet",
                "facts": [
                    _teams_fact("Contract",       s["contract"]),
                    _teams_fact("Entry (mid)",    f"${s['mid']:.2f}  (bid ${s['bid']:.2f} / ask ${s['ask']:.2f})"),
                    _teams_fact("Moneyness",      mono_desc),
                    _teams_fact("Implied Vol",    f"{s['iv']*100:.1f}%"),
                    _teams_fact("Delta / Gamma",  f"{s['delta']:.3f}  /  {s['gamma']:.5f}"),
                    _teams_fact("Theta (daily)",  f"${s['theta']:.4f}"),
                    _teams_fact("Vega (per 1%)",  f"${s['vega']:.3f}"),
                    _teams_fact("OI / Volume",    f"{s['open_interest']:,}  /  {s['volume']:,}"),
                    _teams_fact("Signal score",   f"{s['signal_score']:.2f}"),
                    _teams_fact("Suggested stop", "Exit if premium loses > 50%"),
                    _teams_fact("Suggested target","2×–3× premium or < 3 DTE"),
                ],
            },
        ],
    }


def build_options_teams_payload(signals: list[dict]) -> dict:
    """Build a standalone Adaptive Card payload for option signals."""
    now  = datetime.now().strftime("%d %b %Y  %H:%M")
    body = [
        {
            "type": "TextBlock",
            "text": f"🎯 Options Trading Signals  |  {now}",
            "weight": "bolder",
            "size": "large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"Strategy: **BUY CALL**  |  "
                f"DTE: {OPT_DTE_MIN}–{OPT_DTE_MAX} days  |  "
                f"Delta sweet spot: {OPT_DELTA_MIN:.2f}–{OPT_DELTA_MAX:.2f}  |  "
                f"Entry: limit at mid-price"
            ),
            "size": "small",
            "isSubtle": True,
            "wrap": True,
        },
        {"type": "Separator"},
    ]

    if signals:
        for i, s in enumerate(signals):
            body.append(_option_signal_card(i + 1, s))
    else:
        body.append({
            "type": "TextBlock",
            "text": "⚠️ No option signals found in the 7–14 DTE window.",
            "color": "warning",
            "wrap": True,
        })

    body += [
        {"type": "Separator"},
        {
            "type": "TextBlock",
            "text": (
                "⚠️ Educational / research use only. Not financial advice. "
                "Options carry significant risk including total loss of premium. "
                "Verify via your broker before trading."
            ),
            "size": "small",
            "isSubtle": True,
            "wrap": True,
        },
    ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }
        ],
    }

def main() -> None:
    args = parse_args()
    t0   = time.time()

    markets_to_run = (
        ["us", "hk"]
        if (args.market == "all" or args.combined)
        else [args.market]
    )

    banner_markets = " + ".join(
        f"{MARKETS[m]['flag']} {MARKETS[m]['label']}" for m in markets_to_run
    )
    print("\n" + "═" * 70)
    print(f"  MULTI-MARKET MOMENTUM SCREENER")
    print(f"  Markets: {banner_markets}")
    print("═" * 70 + "\n")

    results: dict[str, pd.DataFrame] = {}
    for mkt in markets_to_run:
        ranked = run_market(mkt, args)
        results[mkt] = ranked

    # ── Output ──────────────────────────────────────────────
    if args.combined and len(markets_to_run) > 1:
        # Merge into one list – note: scores across different-currency markets
        # are return-based (%), so they are directly comparable.
        combined = pd.concat(list(results.values()), ignore_index=True)
        combined = combined.sort_values("score", ascending=False).reset_index(drop=True)
        if combined.empty:
            print("❌  No stocks passed filters in any market.")
            sys.exit(1)
        print("🏆  COMBINED RANKING (all markets)")
        display_results(
            combined,
            top_n=args.top * len(markets_to_run),
            market="combined",
            show_market=True,
        )
        # Override meta for combined header
        _display_combined(combined, args.top * len(markets_to_run))
    else:
        for mkt, ranked in results.items():
            if ranked.empty:
                print(f"⚠️   [{mkt.upper()}] No stocks passed filters.")
                print("     Try:  --rsi-low 40  --max-dist 40\n")
                continue
            display_results(ranked, top_n=args.top, market=mkt)

    # ── Options signals ────────────────────────────────────────────────────
    option_signals: list[dict] = []
    if getattr(args, "options", False):
        option_signals = scan_options(
            results=results,
            top_n=args.top,
            dte_min=args.dte_min,
            dte_max=args.dte_max,
        )
        display_option_signals(option_signals)

    # ── Teams notification ──────────────────────────────────────────────────
    webhook = args.teams
    if not webhook and getattr(args, "teams_env", False):
        webhook = os.environ.get("TEAMS_WEBHOOK_URL", "")
        if not webhook:
            print("⚠️   --teams-env set but TEAMS_WEBHOOK_URL is not defined in environment.")

    if webhook:
        payload = build_teams_payload(
            results=results,
            top_n=args.top,
            combined=args.combined and len(markets_to_run) > 1,
        )
        post_to_teams(webhook, payload)
        # Also send option signals card if any were found
        if option_signals:
            opt_payload = build_options_teams_payload(option_signals)
            post_to_teams(webhook, opt_payload)
    elif getattr(args, "teams_only", False):
        print("⚠️   --teams-only requires --teams <URL> or --teams-env.")

    elapsed = time.time() - t0
    print(f"⏱️   Total time: {elapsed:.1f}s\n")


def _display_combined(df: pd.DataFrame, top_n: int) -> None:
    """Print a combined cross-market table with market column."""
    top  = df.head(top_n).copy()
    eq   = "═" * 116
    hd   = "─" * 116

    print("\n" + eq)
    print(f"  🌐  COMBINED MOMENTUM RANKING — Top {top_n} Across All Markets")
    print(f"  📅  {datetime.today().strftime('%A, %d %B %Y  %H:%M')}")
    print(eq)
    print(
        f"  {'#':<4}{'Mkt':<5}{'Symbol':<12}{'Price':>12}"
        f"{'Curr':<5}{'1M %':>8}{'3M %':>8}{'RSI':>7}{'RVol':>7}"
        f"  {'MA':<6}{'52W %':>7}{'Score':>8}  {'Strength'}"
    )
    print(hd)

    for idx, row in top.iterrows():
        mkt   = row.get("market", "??")
        meta  = _MARKET_META.get(mkt, {"flag": "🌐", "currency": "???", "label": mkt})
        flag  = meta["flag"]
        cur   = meta["currency"]
        stars = _STARS.get(int(row["ma_score"]), "☆☆☆")
        bar   = _bar(row["score"])

        print(
            f"  {idx+1:<4}{flag}{mkt.upper():<4}{row['symbol']:<12}"
            f"{row['price']:>12.3f}{cur:<5}"
            f"{row['ret_1m']:>+7.1f}%{row['ret_3m']:>+7.1f}%"
            f"{row['rsi']:>7.1f}{row['rel_vol']:>7.2f}x"
            f"  {stars:<6}{row['dist_52w']:>+6.1f}%"
            f"{row['score']:>8.1f}  {bar}"
        )

    print(hd)
    _print_legend(eq)


if __name__ == "__main__":
    main()

