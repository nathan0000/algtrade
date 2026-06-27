"""
models.py — Shared data types for the SPX trading system.

No IBKR imports here. All layers depend on these; nothing here depends on them.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class OptionRight(str, Enum):
    CALL = "C"
    PUT  = "P"


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LMT = "LMT"
    MKT = "MKT"


class SpreadSide(str, Enum):
    CALL = "CALL"
    PUT  = "PUT"


class SpreadState(str, Enum):
    """Lifecycle state of a single vertical spread position."""
    PENDING   = "PENDING"    # not yet ordered
    PLACED    = "PLACED"     # order submitted, awaiting fill
    FILLED    = "FILLED"     # fully filled, position open
    CLOSING   = "CLOSING"    # close order submitted
    CLOSED    = "CLOSED"     # fully closed
    CANCELLED = "CANCELLED"  # order cancelled without fill


class CondorState(str, Enum):
    """Lifecycle state of the full iron condor."""
    INIT     = "INIT"
    ENTERING = "ENTERING"   # both sides being entered
    OPEN     = "OPEN"       # at least one side filled and active
    CLOSED   = "CLOSED"     # all sides closed


# ── Option leg ────────────────────────────────────────────────────────────────

@dataclass
class OptionLeg:
    """A single option contract with optional pricing / greek data."""
    symbol:     str
    expiry:     str           # YYYYMMDD
    strike:     float
    right:      OptionRight
    con_id:     int   = 0
    trading_class: str = ""

    # Pricing (populated after market data request)
    bid:   float = 0.0
    ask:   float = 0.0
    last:  float = 0.0

    # Greeks (populated from IBKR streaming ticks, or local Black-Scholes
    # fallback when IBKR's don't arrive — see market_data.py)
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega:  float = 0.0
    iv:    float = 0.0

    # Provenance + comparison fields. delta_source records which path
    # ultimately populated `delta` ("ibkr" or "local"); delta_local always
    # holds the locally-computed Black-Scholes value when computable,
    # independent of which source won, so a value can be logged
    # side-by-side with whatever IBKR sent for sanity-checking even when
    # IBKR's value was used.
    delta_source: str = ""        # "ibkr" | "local" | "" (neither available)
    delta_local:  float = 0.0     # local Black-Scholes delta, if computed

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return round((self.bid + self.ask) / 2, 4)
        if self.last > 0:
            return self.last
        return 0.0

    @property
    def has_price(self) -> bool:
        return self.mid > 0

    def __repr__(self) -> str:
        return (f"OptionLeg({self.symbol} {self.expiry} "
                f"{self.right.value}{self.strike:.0f} "
                f"bid={self.bid} ask={self.ask} Δ={self.delta:.3f})")


# ── Vertical spread ───────────────────────────────────────────────────────────

@dataclass
class VerticalSpread:
    """
    A vertical credit spread: sell short_leg, buy long_leg.
    Both legs share the same symbol, expiry, and right.
    """
    side:       SpreadSide
    short_leg:  OptionLeg
    long_leg:   OptionLeg
    quantity:   int = 1
    multiplier: int = 100

    # Fill tracking
    state:            SpreadState = SpreadState.PENDING
    entry_order_id:   int   = 0
    close_order_id:   int   = 0
    filled_credit:    float = 0.0   # per share (e.g. 1.25)
    close_debit:      float = 0.0   # per share cost to close

    @property
    def credit_dollars(self) -> float:
        """Theoretical entry credit in dollars."""
        return round((self.short_leg.mid - self.long_leg.mid) * self.multiplier, 2)

    @property
    def filled_credit_dollars(self) -> float:
        return round(self.filled_credit * self.multiplier, 2)

    @property
    def close_debit_dollars(self) -> float:
        return round(self.close_debit * self.multiplier, 2)

    @property
    def pnl_dollars(self) -> float:
        """Realised P&L once closed."""
        return round(self.filled_credit_dollars - self.close_debit_dollars, 2)

    @property
    def width(self) -> float:
        return abs(self.short_leg.strike - self.long_leg.strike)

    @property
    def is_active(self) -> bool:
        return self.state == SpreadState.FILLED

    @property
    def is_closed(self) -> bool:
        return self.state in (SpreadState.CLOSED, SpreadState.CANCELLED)

    def current_mark(self) -> float:
        """
        Live mark debit cost to close (per share).
        Requires short_leg and long_leg prices to be refreshed externally.
        """
        return round(self.short_leg.mid - self.long_leg.mid, 4)

    def current_mark_dollars(self) -> float:
        return round(self.current_mark() * self.multiplier, 2)

    def unrealised_loss_dollars(self) -> float:
        """
        Positive = losing money.
        Cost to close now minus credit received.
        """
        return round(self.current_mark_dollars() - self.filled_credit_dollars, 2)

    def __repr__(self) -> str:
        return (f"VerticalSpread({self.side.value} "
                f"{self.short_leg.strike}/{self.long_leg.strike} "
                f"credit=${self.filled_credit_dollars:.0f} "
                f"state={self.state.value})")


# ── Iron condor ───────────────────────────────────────────────────────────────

@dataclass
class IronCondor:
    """
    Two vertical credit spreads forming an iron condor.
    Each side is managed independently; they share a total_credit reference
    for stop-loss calculation.
    """
    call_spread: Optional[VerticalSpread] = None
    put_spread:  Optional[VerticalSpread] = None
    state:       CondorState = CondorState.INIT

    @property
    def total_credit_dollars(self) -> float:
        call = self.call_spread.filled_credit_dollars if self.call_spread else 0
        put  = self.put_spread.filled_credit_dollars  if self.put_spread  else 0
        return round(call + put, 2)

    @property
    def both_active(self) -> bool:
        return (self.call_spread is not None and self.call_spread.is_active and
                self.put_spread  is not None and self.put_spread.is_active)

    @property
    def all_closed(self) -> bool:
        call_ok = self.call_spread is None or self.call_spread.is_closed
        put_ok  = self.put_spread  is None or self.put_spread.is_closed
        return call_ok and put_ok

    def active_spreads(self) -> list[VerticalSpread]:
        result = []
        if self.call_spread and self.call_spread.is_active:
            result.append(self.call_spread)
        if self.put_spread and self.put_spread.is_active:
            result.append(self.put_spread)
        return result

    def summary(self) -> str:
        lines = [f"IronCondor [{self.state.value}]"]
        if self.call_spread:
            lines.append(f"  CALL: {self.call_spread}")
        if self.put_spread:
            lines.append(f"  PUT:  {self.put_spread}")
        lines.append(f"  Total credit: ${self.total_credit_dollars:.0f}")
        return "\n".join(lines)


# ── Order request / result ────────────────────────────────────────────────────

@dataclass
class SpreadOrderRequest:
    """
    Fully describes a spread order to be submitted.
    Constructed by the strategy, consumed by the order manager.
    """
    spread:     VerticalSpread
    order_side: OrderSide       # BUY (to close) or SELL (to open)
    order_type: OrderType
    limit_price: Optional[float] = None   # required for LMT
    quantity:   int = 1
    tag:        str = ""        # free-form label for logging

    def validate(self):
        if self.order_type == OrderType.LMT and self.limit_price is None:
            raise ValueError("LMT order requires limit_price")
        if self.quantity < 1:
            raise ValueError("quantity must be >= 1")


@dataclass
class OrderResult:
    """Returned by OrderManager after placing an order."""
    order_id:    int
    spread_side: SpreadSide
    order_side:  OrderSide
    order_type:  OrderType
    limit_price: Optional[float]
    quantity:    int
    tag:         str = ""
    status:      str = "SUBMITTED"
    filled_price: Optional[float] = None
