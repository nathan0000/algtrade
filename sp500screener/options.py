"""
options.py — Options Analyzer for Swing Positions
Selects optimal option contract (or vertical spread) for each screener candidate.
Holds 2–6 weeks, targets ITM debit spreads to control cost.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
import pytz

from config import OptionConfig as OC

log = logging.getLogger("Screener.Options")
ET  = pytz.timezone("America/New_York")


@dataclass
class OptionLeg:
    action:     str       # "BUY" or "SELL"
    right:      str       # "C" or "P"
    strike:     float
    expiry:     str       # YYYYMMDD
    dte:        int
    delta:      Optional[float] = None
    mid_price:  Optional[float] = None
    iv:         Optional[float] = None


@dataclass
class OptionTrade:
    symbol:         str
    signal:         str       # "BREAKOUT", "REVERSAL_LONG", "REVERSAL_SHORT"
    structure:      str       # "LONG_CALL", "DEBIT_CALL_SPREAD", "LONG_PUT", "DEBIT_PUT_SPREAD"
    legs:           list      = field(default_factory=list)
    max_cost:       float     = 0.0     # debit paid per spread * 100
    max_profit:     float     = 0.0
    breakeven:      float     = 0.0
    target_expiry:  str       = ""
    target_dte:     int       = 0
    risk_per_contract: float  = 0.0
    contracts:      int       = 1
    rationale:      str       = ""
    notes:          list      = field(default_factory=list)


class OptionsAnalyser:
    """
    Given a screener candidate (symbol, signal, current price, option chain),
    selects the optimal options structure for a 2–6 week swing hold.
    """

    def analyse(self, symbol: str, signal: str, current_price: float,
                chain: list[dict], account_value: float) -> Optional[OptionTrade]:
        """
        chain: list of dicts from IBKRDataFetcher.get_option_chain()
        Returns an OptionTrade or None if no suitable structure found.
        """
        if not chain or current_price <= 0:
            return None

        trade = OptionTrade(symbol=symbol, signal=signal)

        # ── Direction ────────────────────────────────────────────────────
        is_bullish = signal in ("BREAKOUT", "REVERSAL_LONG")
        right      = "C" if is_bullish else "P"
        trade.structure = ("DEBIT_CALL_SPREAD" if is_bullish and OC.USE_SPREADS
                           else "LONG_CALL" if is_bullish
                           else "DEBIT_PUT_SPREAD" if OC.USE_SPREADS
                           else "LONG_PUT")

        # ── Select expiry in target DTE window ───────────────────────────
        today     = datetime.now(ET).date()
        min_exp   = today + timedelta(days=OC.MIN_DTE)
        max_exp   = today + timedelta(days=OC.MAX_DTE)

        # Collect all valid expirations from chain
        valid_expiries = []
        for chain_entry in chain:
            for exp_str in chain_entry.get("expirations", []):
                try:
                    exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                    dte      = (exp_date - today).days
                    if min_exp <= exp_date <= max_exp:
                        valid_expiries.append((dte, exp_str, chain_entry.get("strikes", [])))
                except:
                    continue

        if not valid_expiries:
            log.debug(f"  {symbol}: no valid expiries in {OC.MIN_DTE}–{OC.MAX_DTE} DTE window")
            return None

        # Pick the expiry closest to 45 DTE (middle of our window)
        target_dte = 45
        best_exp   = min(valid_expiries, key=lambda x: abs(x[0] - target_dte))
        dte, expiry_str, strikes = best_exp

        trade.target_expiry = expiry_str
        trade.target_dte    = dte

        # ── Strike selection ─────────────────────────────────────────────
        strike_arr = sorted([float(s) for s in strikes])
        if not strike_arr:
            return None

        # Buy strike: slightly ITM
        delta_target = OC.LONG_CALL_DELTA if is_bullish else abs(OC.LONG_PUT_DELTA)
        atm          = min(strike_arr, key=lambda s: abs(s - current_price))

        # ITM for long leg: go 1–2 strikes ITM for ~0.65 delta
        itm_offset = round(current_price * 0.03 / 5) * 5   # ~3% ITM, rounded to $5
        if is_bullish:
            long_strike = max(s for s in strike_arr if s <= current_price - itm_offset + 5)
        else:
            long_strike = min(s for s in strike_arr if s >= current_price + itm_offset - 5)

        # Spread: short leg OTM by spread width
        spread_width = round(current_price * OC.SPREAD_WIDTH_PCT / 5) * 5
        spread_width = max(5.0, spread_width)   # minimum $5 wide

        if OC.USE_SPREADS:
            if is_bullish:
                short_strike = long_strike + spread_width
            else:
                short_strike = long_strike - spread_width

            # Validate both strikes exist in chain
            if short_strike not in strike_arr:
                short_strike = min(strike_arr, key=lambda s: abs(s - short_strike))

        # ── Build leg objects ────────────────────────────────────────────
        long_leg = OptionLeg(
            action="BUY",
            right=right,
            strike=long_strike,
            expiry=expiry_str,
            dte=dte,
            delta=delta_target,
        )
        trade.legs.append(long_leg)

        if OC.USE_SPREADS:
            short_leg = OptionLeg(
                action="SELL",
                right=right,
                strike=short_strike,
                expiry=expiry_str,
                dte=dte,
                delta=(delta_target - 0.25),
            )
            trade.legs.append(short_leg)
            trade.max_profit = (spread_width - trade.max_cost) * 100
        else:
            trade.max_profit = float("inf")   # uncapped

        # ── Cost estimate (before live fill) ────────────────────────────
        # Estimate debit using rough Black-Scholes approximation
        # (will be replaced by live IBKR mid-price at order time)
        estimated_iv = 0.28   # assume 28% IV as placeholder
        long_est  = _bs_price_approx(current_price, long_strike, dte / 365,
                                     estimated_iv, right)
        short_est = 0.0
        if OC.USE_SPREADS:
            short_est = _bs_price_approx(current_price, short_strike, dte / 365,
                                         estimated_iv, right)

        net_debit = long_est - short_est
        net_debit = max(0.05, net_debit)

        # Check debit vs max acceptable (40% of spread width)
        max_ok_debit = spread_width * OC.SPREAD_WIDTH_PCT * 8 if OC.USE_SPREADS else float("inf")
        if net_debit > max_ok_debit:
            trade.notes.append(f"⚠ Estimated debit ${net_debit:.2f} may exceed max acceptable")

        trade.max_cost = round(net_debit, 2)
        trade.breakeven = (long_strike + net_debit if is_bullish
                           else long_strike - net_debit)

        # ── Position sizing ──────────────────────────────────────────────
        risk_dollars = account_value * 0.02   # 2% account risk
        trade.risk_per_contract = net_debit * 100
        trade.contracts = max(1, int(risk_dollars / trade.risk_per_contract))

        # ── Rationale ────────────────────────────────────────────────────
        trade.rationale = (
            f"{'Bullish' if is_bullish else 'Bearish'} {trade.structure} on {symbol} | "
            f"Signal: {signal} | "
            f"{'Buy' if is_bullish else 'Sell'} ${long_strike:.0f}/"
            f"{'Sell' if is_bullish else 'Buy'} ${short_strike:.0f} "
            f"{expiry_str} ({dte}DTE) | "
            f"Est. debit: ${net_debit:.2f} | "
            f"Breakeven: ${trade.breakeven:.2f} | "
            f"Max risk: ${trade.risk_per_contract:.0f}/contract | "
            f"Contracts: {trade.contracts}"
            if OC.USE_SPREADS else
            f"Long {right} ${long_strike:.0f} {expiry_str} ({dte}DTE)"
        )

        trade.notes.extend([
            f"Hold target: {OC.HOLD_WEEKS_MIN}–{OC.HOLD_WEEKS_MAX} weeks",
            f"Close if debit loses 50% of value",
            f"Take profit at 80–100% of spread width",
            f"Close at least 2 weeks before expiry",
        ])

        log.info(f"  {symbol} option: {trade.rationale}")
        return trade


def _bs_price_approx(S: float, K: float, T: float,
                     sigma: float, option_type: str) -> float:
    """
    Simplified Black-Scholes approximation (no scipy needed).
    T = time in years, sigma = annualized IV.
    """
    import math
    if T <= 0:
        return max(0, S - K) if option_type == "C" else max(0, K - S)

    r  = 0.05   # risk-free rate
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    def norm_cdf(x: float) -> float:
        # Abramowitz and Stegun approximation
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
               + t * (-1.821255978 + t * 1.330274429))))
        cdf = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x ** 2) * poly
        return cdf if x >= 0 else 1.0 - cdf

    if option_type == "C":
        price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

    return max(0.01, price)
