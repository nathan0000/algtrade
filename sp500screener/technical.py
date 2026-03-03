"""
technical.py — Technical Analysis Engine
All indicators computed from raw OHLCV bars (no TA-Lib dependency).
Detects: Breakout, Trend Reversal, with scored output.
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Optional
from config import TechConfig as TC

log = logging.getLogger("Screener.Technical")


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class TechResult:
    symbol:          str
    signal:          str        = "NONE"    # "BREAKOUT" | "REVERSAL_LONG" | "REVERSAL_SHORT" | "NONE"
    score:           float      = 0.0       # 0–100
    passed:          bool       = False

    # Raw indicators
    close:           float      = 0.0
    volume:          float      = 0.0
    avg_volume_20:   float      = 0.0
    vol_ratio:       float      = 0.0

    rsi:             float      = 50.0
    adx:             float      = 0.0
    atr:             float      = 0.0
    atr_pct:         float      = 0.0
    macd:            float      = 0.0
    macd_signal:     float      = 0.0
    macd_hist:       float      = 0.0

    ema_fast:        float      = 0.0
    ema_slow:        float      = 0.0
    ema_trend:       float      = 0.0

    high_n:          float      = 0.0      # N-day high
    low_n:           float      = 0.0      # N-day low

    is_breakout:     bool       = False
    is_reversal:     bool       = False
    reversal_dir:    str        = ""       # "LONG" or "SHORT"
    is_hammer:       bool       = False
    is_engulfing:    bool       = False
    above_200:       bool       = False
    ema_aligned:     bool       = False

    reasons:         list       = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════
# INDICATOR FUNCTIONS — pure Python, no external TA libs
# ════════════════════════════════════════════════════════════════════════════

def _ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return [0.0] * len(values)
    k = 2.0 / (period + 1)
    result = [0.0] * len(values)
    # Seed with SMA of first `period` bars
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def _sma(values: list[float], period: int) -> list[float]:
    result = [0.0] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1: i + 1]) / period
    return result


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    result = [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        rs = avg_g / avg_l if avg_l != 0 else 999
        result[i + 1] = 100 - 100 / (1 + rs)
    return result


def _atr(highs: list[float], lows: list[float], closes: list[float],
         period: int = 14) -> list[float]:
    if len(closes) < 2:
        return [0.0] * len(closes)
    tr_list = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)
    result = [0.0] * len(closes)
    result[period - 1] = sum(tr_list[:period]) / period
    for i in range(period, len(closes)):
        result[i] = (result[i - 1] * (period - 1) + tr_list[i]) / period
    return result


def _adx(highs: list[float], lows: list[float], closes: list[float],
         period: int = 14) -> tuple[list, list, list]:
    """Returns (adx, plus_di, minus_di) as lists."""
    n = len(closes)
    if n < period * 2:
        return [0.0] * n, [0.0] * n, [0.0] * n

    atr_vals  = _atr(highs, lows, closes, period)
    plus_dm   = [0.0] * n
    minus_dm  = [0.0] * n
    for i in range(1, n):
        up   = highs[i]  - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i]  = up   if up > down and up > 0   else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    def smooth(vals, p):
        s = [0.0] * n
        s[p] = sum(vals[1: p + 1])
        for i in range(p + 1, n):
            s[i] = s[i - 1] - s[i - 1] / p + vals[i]
        return s

    sm_plus  = smooth(plus_dm,  period)
    sm_minus = smooth(minus_dm, period)
    sm_atr   = smooth(atr_vals, period)

    plus_di  = [0.0] * n
    minus_di = [0.0] * n
    dx_list  = [0.0] * n
    for i in range(period, n):
        if sm_atr[i] != 0:
            plus_di[i]  = 100 * sm_plus[i]  / sm_atr[i]
            minus_di[i] = 100 * sm_minus[i] / sm_atr[i]
        dsum = plus_di[i] + minus_di[i]
        if dsum != 0:
            dx_list[i] = 100 * abs(plus_di[i] - minus_di[i]) / dsum

    adx_vals = _sma(dx_list, period)
    return adx_vals, plus_di, minus_di


def _macd(closes: list[float],
          fast: int = 12, slow: int = 26, signal: int = 9
         ) -> tuple[list, list, list]:
    ema_f  = _ema(closes, fast)
    ema_s  = _ema(closes, slow)
    macd_l = [ema_f[i] - ema_s[i] for i in range(len(closes))]
    sig_l  = _ema(macd_l, signal)
    hist_l = [macd_l[i] - sig_l[i] for i in range(len(closes))]
    return macd_l, sig_l, hist_l


# ════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTORS
# ════════════════════════════════════════════════════════════════════════════

def _is_hammer(o, h, l, c) -> bool:
    """Hammer / Doji Hammer: small body at top, long lower wick."""
    body   = abs(c - o)
    total  = h - l
    if total == 0:
        return False
    lower  = min(o, c) - l
    return (body / total) < TC.REVERSAL_HAMMER_RATIO and lower > body * 2


def _is_bullish_engulfing(prev_o, prev_c, curr_o, curr_c) -> bool:
    prev_bear = prev_c < prev_o
    curr_bull = curr_c > curr_o
    engulfs   = curr_o < prev_c and curr_c > prev_o
    return prev_bear and curr_bull and engulfs


def _is_bearish_engulfing(prev_o, prev_c, curr_o, curr_c) -> bool:
    prev_bull = prev_c > prev_o
    curr_bear = curr_c < curr_o
    engulfs   = curr_o > prev_c and curr_c < prev_o
    return prev_bull and curr_bear and engulfs


def _consecutive_down_bars(closes: list[float], n: int) -> bool:
    if len(closes) < n + 1:
        return False
    recent = closes[-(n + 1):]
    return all(recent[i] < recent[i - 1] for i in range(1, len(recent)))


def _consecutive_up_bars(closes: list[float], n: int) -> bool:
    if len(closes) < n + 1:
        return False
    recent = closes[-(n + 1):]
    return all(recent[i] > recent[i - 1] for i in range(1, len(recent)))


# ════════════════════════════════════════════════════════════════════════════
# MAIN TECHNICAL ANALYSER
# ════════════════════════════════════════════════════════════════════════════

class TechnicalAnalyser:
    """
    Runs all technical filters on a symbol's OHLCV bar list.
    Returns a TechResult with signal, score, and individual indicator values.
    """

    def analyse(self, symbol: str, bars: list[dict]) -> TechResult:
        result = TechResult(symbol=symbol)

        if len(bars) < TC.ADX_PERIOD * 3:
            result.reasons.append(f"Insufficient bars: {len(bars)}")
            return result

        # Unpack arrays
        opens   = [b["open"]   for b in bars]
        highs   = [b["high"]   for b in bars]
        lows    = [b["low"]    for b in bars]
        closes  = [b["close"]  for b in bars]
        volumes = [b["volume"] for b in bars]

        n = len(closes)

        # ── Core indicators ──────────────────────────────────────────────
        ema_f_arr  = _ema(closes, TC.EMA_FAST)
        ema_s_arr  = _ema(closes, TC.EMA_SLOW)
        ema_t_arr  = _ema(closes, TC.EMA_TREND)
        rsi_arr    = _rsi(closes, TC.RSI_PERIOD)
        atr_arr    = _atr(highs, lows, closes, TC.ATR_PERIOD)
        adx_arr, plus_di, minus_di = _adx(highs, lows, closes, TC.ADX_PERIOD)
        macd_arr, macd_sig, macd_hist = _macd(
            closes, TC.MACD_FAST, TC.MACD_SLOW, TC.MACD_SIGNAL)

        # ── Latest values ────────────────────────────────────────────────
        c0  = closes[-1]
        o0  = opens[-1]
        h0  = highs[-1]
        l0  = lows[-1]
        v0  = volumes[-1]
        c1  = closes[-2]
        o1  = opens[-2]

        result.close       = c0
        result.volume      = v0
        result.rsi         = rsi_arr[-1]
        result.adx         = adx_arr[-1]
        result.atr         = atr_arr[-1]
        result.atr_pct     = atr_arr[-1] / c0 if c0 > 0 else 0
        result.macd        = macd_arr[-1]
        result.macd_signal = macd_sig[-1]
        result.macd_hist   = macd_hist[-1]
        result.ema_fast    = ema_f_arr[-1]
        result.ema_slow    = ema_s_arr[-1]
        result.ema_trend   = ema_t_arr[-1]

        # Volume average (20-day)
        vol_20 = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
        result.avg_volume_20 = sum(vol_20) / len(vol_20) if vol_20 else 1
        result.vol_ratio     = v0 / result.avg_volume_20 if result.avg_volume_20 > 0 else 0

        # N-day high/low (excluding today)
        lb = TC.BREAKOUT_LOOKBACK_DAYS
        window_highs = highs[-(lb + 1):-1]
        window_lows  = lows[-(lb + 1):-1]
        result.high_n = max(window_highs) if window_highs else 0
        result.low_n  = min(window_lows)  if window_lows  else 0

        # EMA alignment
        result.above_200  = c0 > ema_t_arr[-1] > 0
        result.ema_aligned = (ema_f_arr[-1] > ema_s_arr[-1] > ema_t_arr[-1]
                              and all(x > 0 for x in [ema_f_arr[-1], ema_s_arr[-1], ema_t_arr[-1]]))

        # Candlestick patterns
        result.is_hammer      = _is_hammer(o0, h0, l0, c0)
        result.is_engulfing   = _is_bullish_engulfing(o1, c1, o0, c0)

        # ── BREAKOUT DETECTION ───────────────────────────────────────────
        breakout_score = 0
        reasons_bo = []

        price_breakout = c0 > result.high_n * (1 + TC.BREAKOUT_ATR_BUFFER)
        vol_confirms   = result.vol_ratio >= TC.BREAKOUT_VOL_MULTIPLIER
        adx_trending   = result.adx >= TC.ADX_MIN_TREND
        macd_positive  = result.macd > result.macd_signal
        above_all_emas = result.ema_aligned

        if price_breakout:
            breakout_score += 35
            reasons_bo.append(f"Price broke {lb}D high ({result.high_n:.2f}→{c0:.2f})")
        if vol_confirms:
            breakout_score += 25
            reasons_bo.append(f"Volume {result.vol_ratio:.1f}× avg (need {TC.BREAKOUT_VOL_MULTIPLIER}×)")
        if adx_trending:
            breakout_score += 15
            reasons_bo.append(f"ADX={result.adx:.1f} (trend strength confirmed)")
        if macd_positive:
            breakout_score += 15
            reasons_bo.append("MACD above signal line")
        if above_all_emas:
            breakout_score += 10
            reasons_bo.append("EMA21 > EMA50 > EMA200 (fully aligned)")

        # ── REVERSAL DETECTION (LONG — oversold bounce) ──────────────────
        reversal_long_score = 0
        reasons_rl = []

        rsi_oversold   = result.rsi <= TC.REVERSAL_RSI_OVERSOLD
        consec_down    = _consecutive_down_bars(closes, TC.REVERSAL_CONSEC_DOWN_BARS)
        vol_spike_rev  = result.vol_ratio >= TC.REVERSAL_VOL_SPIKE
        hammer_pattern = result.is_hammer or result.is_engulfing
        near_support   = c0 <= result.ema_slow * 1.03 and c0 >= result.ema_slow * 0.95
        macd_cross_up  = (macd_hist[-1] > macd_hist[-2] > macd_hist[-3]
                          and macd_hist[-1] > -0.5)    # histogram rising from negative

        if rsi_oversold:
            reversal_long_score += 25
            reasons_rl.append(f"RSI={result.rsi:.1f} oversold (<{TC.REVERSAL_RSI_OVERSOLD})")
        if consec_down:
            reversal_long_score += 20
            reasons_rl.append(f"{TC.REVERSAL_CONSEC_DOWN_BARS}+ consecutive down bars")
        if vol_spike_rev:
            reversal_long_score += 20
            reasons_rl.append(f"Volume spike {result.vol_ratio:.1f}× on potential reversal day")
        if hammer_pattern:
            reversal_long_score += 20
            reasons_rl.append("Hammer / Bullish Engulfing pattern")
        if near_support:
            reversal_long_score += 10
            reasons_rl.append("Price near EMA50 support")
        if macd_cross_up:
            reversal_long_score += 5
            reasons_rl.append("MACD histogram turning up")

        # ── REVERSAL DETECTION (SHORT — overbought fade) ─────────────────
        reversal_short_score = 0
        reasons_rs = []

        rsi_overbought  = result.rsi >= TC.REVERSAL_RSI_OVERBOUGHT
        consec_up       = _consecutive_up_bars(closes, TC.REVERSAL_CONSEC_DOWN_BARS)
        bearish_engulf  = _is_bearish_engulfing(o1, c1, o0, c0)
        near_resistance = c0 >= result.ema_slow * 0.97 and c0 <= result.ema_slow * 1.05
        macd_cross_dn   = (macd_hist[-1] < macd_hist[-2] < macd_hist[-3]
                           and macd_hist[-1] < 0.5)

        if rsi_overbought:
            reversal_short_score += 25
            reasons_rs.append(f"RSI={result.rsi:.1f} overbought (>{TC.REVERSAL_RSI_OVERBOUGHT})")
        if consec_up:
            reversal_short_score += 20
            reasons_rs.append(f"{TC.REVERSAL_CONSEC_DOWN_BARS}+ consecutive up bars")
        if vol_spike_rev:
            reversal_short_score += 20
            reasons_rs.append("Volume spike on potential exhaustion")
        if bearish_engulf:
            reversal_short_score += 20
            reasons_rs.append("Bearish Engulfing pattern")
        if macd_cross_dn:
            reversal_short_score += 5
            reasons_rs.append("MACD histogram turning down")

        # ── SELECT SIGNAL ────────────────────────────────────────────────
        best_score = max(breakout_score, reversal_long_score, reversal_short_score)

        if best_score < 40:
            result.signal  = "NONE"
            result.score   = best_score
            result.reasons = ["Score too low for any signal"]
            return result

        if best_score == breakout_score and breakout_score >= reversal_long_score:
            result.signal     = "BREAKOUT"
            result.score      = float(breakout_score)
            result.is_breakout= True
            result.reasons    = reasons_bo

        elif reversal_long_score >= reversal_short_score:
            result.signal      = "REVERSAL_LONG"
            result.score       = float(reversal_long_score)
            result.is_reversal = True
            result.reversal_dir= "LONG"
            result.reasons     = reasons_rl

        else:
            result.signal      = "REVERSAL_SHORT"
            result.score       = float(reversal_short_score)
            result.is_reversal = True
            result.reversal_dir= "SHORT"
            result.reasons     = reasons_rs

        result.passed = result.score >= TC.MIN_TECH_SCORE
        log.debug(f"  {symbol} → {result.signal} score={result.score:.0f} "
                  f"passed={result.passed}")
        return result
