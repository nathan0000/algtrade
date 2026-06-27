"""
greeks.py — Self-contained Black-Scholes Greeks calculator.

WHY THIS EXISTS:
  IBKR's live-streamed Greeks (tickOptionComputation) have proven
  unreliable in practice for this strategy across multiple debugging
  rounds: MODEL_OPTION (tick type 13) frequently never arrives at all for
  a large fraction of a multi-leg batch, and even the bid/ask-derived
  fallback (tick types 10/11/12) sends ambiguous placeholder values
  (e.g. delta=0.0 on a partial/incomplete tick) that are indistinguishable
  from "not computed yet" without very fragile heuristics.

  Rather than continuing to fight IBKR's tick delivery pipeline, this
  module computes Greeks ourselves from data we already reliably have:
  spot price, strike, time-to-expiry, risk-free rate, and the option's
  OWN market price (mid of bid/ask). This is the same approach commercial
  options platforms use under the hood when they show "Greeks" next to a
  live quote — back out implied volatility from the market price via
  Black-Scholes inversion, then compute delta/gamma/theta/vega from that
  IV.

DEPENDENCIES: stdlib only (math.erf for the normal CDF) — no scipy,
  no py_vollib. This keeps the module portable across environments that
  may not have scipy installed (e.g. a fresh macOS Python without scipy).

LIMITATIONS:
  - European-style pricing — correct for SPX/SPXW, which are both
    cash-settled, European-exercise (no early exercise risk to model).
  - Assumes no dividends on the index for the 0DTE timeframe (dividend
    yield defaults to 0; SPX's effective dividend drag over a single day
    is negligible for delta purposes).
  - IV inversion uses Newton-Raphson with a bisection fallback; for deep
    ITM/OTM 0DTE options with very little time value, IV solving can be
    numerically unstable — see solve_implied_vol()'s bounds/clamping.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

log = logging.getLogger("Greeks")

# ── Constants ──────────────────────────────────────────────────────────────────

# Risk-free rate proxy. For 0DTE (hours of time value), the choice of rate
# barely moves delta/gamma at all — this is a reasonable fixed default
# rather than fetching a live treasury rate for a same-day option.
DEFAULT_RISK_FREE_RATE = 0.05

# IV solver bounds and tolerances
IV_MIN           = 0.01    # 1% — floor to avoid degenerate near-zero vol
IV_MAX           = 5.00    # 500% — ceiling for extreme 0DTE moves
IV_INITIAL_GUESS = 0.20    # 20% — reasonable starting point for SPX
IV_TOLERANCE     = 1e-6    # solver convergence threshold (price-space)
IV_MAX_ITER      = 100

# Minimum time-to-expiry floor (in years) to avoid division-by-zero /
# degenerate Black-Scholes behavior in the last seconds before expiry.
MIN_TIME_TO_EXPIRY_YEARS = 1.0 / (365 * 24 * 60)   # 1 minute, in years


# ── Normal distribution helpers (stdlib only, no scipy) ───────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf — exact, no scipy needed."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ── Time-to-expiry helper ──────────────────────────────────────────────────────

def time_to_expiry_years(
    expiry: str,
    now: Optional[datetime] = None,
    expiry_hour_et: int = 16,
    expiry_minute_et: int = 0,
) -> float:
    """
    Convert an expiry string (YYYYMMDD) into years-until-expiry from now.

    SPX/SPXW options settle based on the index value at market close
    (16:00 ET) on the expiry date. For a 0DTE option entered intraday,
    this correctly returns a fraction-of-a-day time value rather than
    treating "today" as zero time (which would break Black-Scholes).

    Args:
        expiry: "YYYYMMDD" string, e.g. "20260626"
        now: current datetime (defaults to datetime.now(); pass explicitly
             for testability)
        expiry_hour_et / expiry_minute_et: assumed settlement time on
             expiry date, in the SAME timezone as `now` is expressed in.
             Caller is responsible for timezone consistency — if `now` is
             tz-aware ET, pass an ET expiry time; this function does not
             do its own timezone conversion.
    """
    if now is None:
        now = datetime.now()

    expiry_date = datetime.strptime(expiry, "%Y%m%d")
    expiry_dt = expiry_date.replace(
        hour=expiry_hour_et, minute=expiry_minute_et, second=0, microsecond=0
    )
    # Preserve tzinfo from `now` if present, so comparisons don't crash on
    # naive-vs-aware mismatches.
    if now.tzinfo is not None:
        expiry_dt = expiry_dt.replace(tzinfo=now.tzinfo)

    delta = expiry_dt - now
    years = delta.total_seconds() / (365.0 * 24 * 60 * 60)
    return max(years, MIN_TIME_TO_EXPIRY_YEARS)


# ── Black-Scholes pricing ──────────────────────────────────────────────────────

@dataclass
class BSInputs:
    spot:   float      # underlying price (S)
    strike: float       # strike price (K)
    time_years: float   # time to expiry in years (T)
    vol:    float        # implied volatility, annualized (sigma)
    rate:   float = DEFAULT_RISK_FREE_RATE   # risk-free rate (r)
    dividend_yield: float = 0.0              # continuous dividend yield (q)

    def _d1_d2(self) -> tuple[float, float]:
        S, K, T, sigma, r, q = (
            self.spot, self.strike, self.time_years, self.vol,
            self.rate, self.dividend_yield
        )
        if T <= 0 or sigma <= 0:
            # Degenerate case — should be prevented by callers clamping
            # time_years/vol, but guard here too rather than raising.
            return (0.0, 0.0)
        d1 = (
            math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T
        ) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    def price(self, right: str) -> float:
        """Black-Scholes theoretical price. right = 'C' or 'P'."""
        S, K, T, r, q = (
            self.spot, self.strike, self.time_years, self.rate,
            self.dividend_yield
        )
        d1, d2 = self._d1_d2()
        if right == "C":
            return (
                S * math.exp(-q * T) * _norm_cdf(d1)
                - K * math.exp(-r * T) * _norm_cdf(d2)
            )
        else:
            return (
                K * math.exp(-r * T) * _norm_cdf(-d2)
                - S * math.exp(-q * T) * _norm_cdf(-d1)
            )

    def vega_raw(self) -> float:
        """Vega per 1.0 (100%) change in vol — NOT per 1% point."""
        S, T, q = self.spot, self.time_years, self.dividend_yield
        d1, _ = self._d1_d2()
        return S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float    # per calendar day (already divided by 365)
    vega:  float     # per 1 percentage-point (1.0 = 100%) change in IV
    iv:    float


def compute_greeks(
    spot: float,
    strike: float,
    time_years: float,
    vol: float,
    right: str,
    rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
) -> Greeks:
    """
    Compute delta/gamma/theta/vega from known inputs (vol already known —
    use solve_implied_vol() first if you only have a market price).
    """
    inputs = BSInputs(spot, strike, time_years, vol, rate, dividend_yield)
    S, K, T, sigma, r, q = spot, strike, time_years, vol, rate, dividend_yield
    d1, d2 = inputs._d1_d2()

    if right == "C":
        delta = math.exp(-q * T) * _norm_cdf(d1)
    else:
        delta = -math.exp(-q * T) * _norm_cdf(-d1)

    gamma = (
        math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * math.sqrt(T))
        if T > 0 and sigma > 0 and S > 0 else 0.0
    )

    # Theta: standard BS theta is per YEAR; convert to per-CALENDAR-DAY,
    # which is the convention most traders (and IBKR's own Greeks) use.
    term1 = (
        -(S * math.exp(-q * T) * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
        if T > 0 else 0.0
    )
    if right == "C":
        term2 = -r * K * math.exp(-r * T) * _norm_cdf(d2)
        term3 = q * S * math.exp(-q * T) * _norm_cdf(d1)
        theta_per_year = term1 + term2 + term3
    else:
        term2 = r * K * math.exp(-r * T) * _norm_cdf(-d2)
        term3 = -q * S * math.exp(-q * T) * _norm_cdf(-d1)
        theta_per_year = term1 + term2 + term3
    theta_per_day = theta_per_year / 365.0

    # Vega per 1 percentage point (0.01) of vol, matching IBKR's convention
    # of "vega per 1% change in IV" — vega_raw() is per 100% (1.0), so
    # divide by 100.
    vega_per_point = inputs.vega_raw() / 100.0

    return Greeks(
        delta=delta, gamma=gamma, theta=theta_per_day,
        vega=vega_per_point, iv=vol
    )


# ── Implied volatility solver ──────────────────────────────────────────────────

def solve_implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    time_years: float,
    right: str,
    rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
    initial_guess: float = IV_INITIAL_GUESS,
) -> Optional[float]:
    """
    Back out implied volatility from a market price via Newton-Raphson,
    falling back to bisection if Newton-Raphson fails to converge or
    produces a nonsensical (negative/runaway) result.

    Returns None if the market price is below intrinsic value or solving
    otherwise fails — callers should treat None as "no usable IV/delta
    for this leg" rather than crashing.
    """
    if market_price <= 0 or spot <= 0 or strike <= 0 or time_years <= 0:
        return None

    # Reject prices below intrinsic value (arbitrage-violating quotes,
    # e.g. stale/crossed bid-ask on an illiquid 0DTE strike) — no IV
    # solves these correctly, Newton-Raphson would just diverge.
    intrinsic = (
        max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    )
    if market_price < intrinsic - 1e-6:
        log.debug(
            f"Market price {market_price} below intrinsic {intrinsic} "
            f"for {right} strike={strike} spot={spot} — skipping IV solve"
        )
        return None

    # ── Newton-Raphson ────────────────────────────────────────────────────────
    vol = initial_guess
    for _ in range(IV_MAX_ITER):
        inputs = BSInputs(spot, strike, time_years, vol, rate, dividend_yield)
        price = inputs.price(right)
        vega = inputs.vega_raw()   # per 100% vol, i.e. d(price)/d(vol)

        diff = price - market_price
        if abs(diff) < IV_TOLERANCE:
            return _clamp_iv(vol)

        if vega < 1e-8:
            break   # vega too small — Newton step would explode; fall through to bisection

        vol = vol - diff / vega
        if vol <= 0 or vol > IV_MAX * 2:
            break   # diverged — fall through to bisection

    # ── Bisection fallback ─────────────────────────────────────────────────────
    return _bisect_implied_vol(
        market_price, spot, strike, time_years, right, rate, dividend_yield
    )


def _bisect_implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    time_years: float,
    right: str,
    rate: float,
    dividend_yield: float,
    lo: float = IV_MIN,
    hi: float = IV_MAX,
    max_iter: int = 100,
) -> Optional[float]:
    """Robust bisection fallback for IV solving when Newton-Raphson fails."""
    price_lo = BSInputs(spot, strike, time_years, lo, rate, dividend_yield).price(right)
    price_hi = BSInputs(spot, strike, time_years, hi, rate, dividend_yield).price(right)

    if (price_lo - market_price) * (price_hi - market_price) > 0:
        # market_price isn't bracketed by [lo, hi] — can't bisect reliably.
        log.debug(
            f"IV bisection: market_price={market_price} not bracketed by "
            f"price range [{price_lo:.4f}, {price_hi:.4f}] for vol range "
            f"[{lo}, {hi}] — giving up"
        )
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        price_mid = BSInputs(spot, strike, time_years, mid, rate, dividend_yield).price(right)
        diff = price_mid - market_price

        if abs(diff) < IV_TOLERANCE:
            return _clamp_iv(mid)

        if (price_lo - market_price) * diff < 0:
            hi = mid
        else:
            lo = mid
            price_lo = price_mid

    return _clamp_iv((lo + hi) / 2.0)


def _clamp_iv(vol: float) -> float:
    return max(IV_MIN, min(IV_MAX, vol))


# ── High-level convenience function ───────────────────────────────────────────

def greeks_from_market_price(
    market_price: float,
    spot: float,
    strike: float,
    time_years: float,
    right: str,
    rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
) -> Optional[Greeks]:
    """
    One-call convenience: market price → implied vol → full Greeks.
    Returns None if IV cannot be solved (e.g. price below intrinsic,
    zero/negative inputs) — callers should treat this as "no Greeks
    available for this leg" exactly like a missing IBKR tick.
    """
    iv = solve_implied_vol(
        market_price, spot, strike, time_years, right, rate, dividend_yield
    )
    if iv is None:
        return None
    return compute_greeks(
        spot, strike, time_years, iv, right, rate, dividend_yield
    )
