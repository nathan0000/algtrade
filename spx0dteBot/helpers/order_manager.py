"""
order_manager.py — Spread order entry and lifecycle management.

Responsibilities:
  - Build IBKR BAG combo contracts from VerticalSpread objects
  - Submit entry (SELL LMT) and close (BUY MKT/LMT) orders
  - Track fill status and average fill prices per order
  - Expose per-side methods so call and put spreads are managed independently
  - No strategy logic — only order construction + submission + tracking

Key design:
  - enter_spread()  → submits one spread (call OR put) independently
  - close_spread()  → closes one spread independently
  - wait_for_fill() → blocks until filled or timeout, then cancels
  - Each spread carries its own entry_order_id and close_order_id
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ibapi.contract import Contract, ComboLeg
from ibapi.order    import Order

from .gateway import IBGateway
from .models  import (
    VerticalSpread, SpreadOrderRequest, OrderResult,
    OrderSide, OrderType, SpreadState, SpreadSide
)

log = logging.getLogger("OrderManager")

# Default exchange routing for SPX options
SMART = "SMART"


class OrderError(RuntimeError):
    pass


class OrderManager:
    """
    All order placement and tracking against IBKR TWS API.

    Usage:
        om = OrderManager(gateway)

        # Enter call side
        result = om.enter_spread(call_spread, limit_credit=1.25)
        filled = om.wait_for_fill(call_spread, timeout=120)

        # Enter put side separately (can be concurrent or sequential)
        result = om.enter_spread(put_spread, limit_credit=1.10)
        filled = om.wait_for_fill(put_spread, timeout=120)

        # Close one side
        om.close_spread(call_spread)

        # Cancel an unfilled entry
        om.cancel_spread_entry(call_spread)
    """

    def __init__(self, gateway: IBGateway, multiplier: int = 100):
        self.gw         = gateway
        self.multiplier = multiplier

    # =========================================================================
    # Public entry interface
    # =========================================================================

    def enter_spread(
        self,
        spread: VerticalSpread,
        limit_credit: Optional[float] = None,
        quantity: int = 1,
        tag: str = ""
    ) -> OrderResult:
        """
        Submit a SELL LMT order to open a vertical credit spread.

        Args:
            spread:       VerticalSpread to enter (con_ids must be resolved)
            limit_credit: per-share credit limit price; defaults to current mid
            quantity:     number of spreads
            tag:          free-form label for logging

        Returns:
            OrderResult with the assigned order_id

        Raises:
            OrderError if con_ids are missing or limit_credit cannot be
            determined
        """
        self._validate_conids(spread)

        if limit_credit is None:
            limit_credit = round(
                spread.short_leg.mid - spread.long_leg.mid, 2
            )
            if limit_credit <= 0:
                raise OrderError(
                    f"Cannot determine positive limit credit for "
                    f"{spread.side.value} spread — "
                    f"short mid={spread.short_leg.mid}, "
                    f"long mid={spread.long_leg.mid}"
                )

        contract  = self._build_spread_contract(spread)
        order     = self._sell_limit_order(limit_credit, quantity)
        order_id  = self.gw.next_order_id()

        label = (tag or f"{spread.side.value} "
                 f"{spread.short_leg.strike}/{spread.long_leg.strike}")

        log.info(
            f"[{label}] ENTER SPREAD  order={order_id}  "
            f"SELL {quantity}x {spread.side.value} "
            f"{spread.short_leg.strike}/{spread.long_leg.strike}  "
            f"LMT ${limit_credit:.2f}/share  "
            f"(${limit_credit * self.multiplier:.0f} credit)"
        )

        # Register fill event before placing so we don't miss the callback
        self.gw.prepare_order_event(order_id)
        self.gw.placeOrder(order_id, contract, order)

        # Update spread state
        spread.entry_order_id = order_id
        spread.state          = SpreadState.PLACED

        return OrderResult(
            order_id    = order_id,
            spread_side = spread.side,
            order_side  = OrderSide.SELL,
            order_type  = OrderType.LMT,
            limit_price = limit_credit,
            quantity    = quantity,
            tag         = label,
        )

    def close_spread(
        self,
        spread: VerticalSpread,
        use_market: bool = True,
        limit_debit: Optional[float] = None,
        quantity: int = 1,
        tag: str = ""
    ) -> Optional[OrderResult]:
        """
        Submit a BUY order to close an open vertical spread.

        Args:
            spread:      VerticalSpread to close (must be in FILLED state)
            use_market:  if True, submit MKT order; else LMT at limit_debit
            limit_debit: per-share debit limit (required if use_market=False)
            quantity:    number of spreads to close
            tag:         label for logging

        Returns:
            OrderResult, or None if spread is not active
        """
        if not spread.is_active:
            log.warning(f"close_spread called on non-active spread: {spread}")
            return None

        self._validate_conids(spread)

        contract = self._build_spread_contract(spread)
        order_id = self.gw.next_order_id()

        label = (tag or f"{spread.side.value} "
                 f"{spread.short_leg.strike}/{spread.long_leg.strike}")

        if use_market:
            order = self._buy_market_order(quantity)
            log.info(
                f"[{label}] CLOSE SPREAD  order={order_id}  "
                f"BUY {quantity}x {spread.side.value}  MKT"
            )
        else:
            if limit_debit is None:
                raise OrderError("limit_debit required for LMT close order")
            order = self._buy_limit_order(limit_debit, quantity)
            log.info(
                f"[{label}] CLOSE SPREAD  order={order_id}  "
                f"BUY {quantity}x {spread.side.value}  LMT ${limit_debit:.2f}"
            )

        self.gw.prepare_order_event(order_id)
        self.gw.placeOrder(order_id, contract, order)

        spread.close_order_id = order_id
        spread.state          = SpreadState.CLOSING

        return OrderResult(
            order_id    = order_id,
            spread_side = spread.side,
            order_side  = OrderSide.BUY,
            order_type  = OrderType.MKT if use_market else OrderType.LMT,
            limit_price = None if use_market else limit_debit,
            quantity    = quantity,
            tag         = label,
        )

    # =========================================================================
    # Fill waiting
    # =========================================================================

    def wait_for_entry_fill(
        self,
        spread: VerticalSpread,
        timeout: float = 120.0
    ) -> bool:
        """
        Block until the entry order for this spread is filled or timeout.

        On fill: updates spread.filled_credit and sets state=FILLED.
        On timeout: cancels the order and sets state=CANCELLED.

        Returns True if filled, False if cancelled.
        """
        order_id = spread.entry_order_id
        if not order_id:
            log.error("wait_for_entry_fill: spread has no entry_order_id")
            return False

        log.info(f"Waiting for {spread.side.value} entry fill "
                 f"(order={order_id}, timeout={timeout}s) …")

        filled = self.gw.wait_for_fill(order_id, timeout=timeout)

        if filled:
            status = self.gw.order_status.get(order_id, {})
            avg_px = status.get("avg_price", 0.0)
            spread.filled_credit = avg_px
            spread.state         = SpreadState.FILLED
            log.info(
                f"✓ {spread.side.value} entry FILLED  "
                f"avg_px={avg_px:.4f}  "
                f"credit=${avg_px * self.multiplier:.0f}"
            )
            return True
        else:
            log.warning(
                f"⚠ {spread.side.value} entry fill timeout — cancelling "
                f"order {order_id}"
            )
            self.cancel_spread_entry(spread)
            return False

    def wait_for_close_fill(
        self,
        spread: VerticalSpread,
        timeout: float = 60.0
    ) -> bool:
        """
        Block until the close order for this spread is filled.
        On fill: updates spread.close_debit and sets state=CLOSED.
        """
        order_id = spread.close_order_id
        if not order_id:
            return False

        filled = self.gw.wait_for_fill(order_id, timeout=timeout)
        if filled:
            status = self.gw.order_status.get(order_id, {})
            avg_px = status.get("avg_price", 0.0)
            spread.close_debit = avg_px
            spread.state       = SpreadState.CLOSED
            log.info(
                f"✓ {spread.side.value} close FILLED  "
                f"avg_px={avg_px:.4f}  "
                f"debit=${avg_px * self.multiplier:.0f}  "
                f"P&L=${spread.pnl_dollars:.0f}"
            )
        return filled

    # =========================================================================
    # Cancel
    # =========================================================================

    def cancel_spread_entry(self, spread: VerticalSpread):
        """Cancel the open entry order for a spread."""
        oid = spread.entry_order_id
        if oid and self.gw.order_status.get(oid, {}).get("status") != "Filled":
            log.info(f"Cancelling entry order {oid} for {spread.side.value}")
            self.gw.cancelOrder(oid, "")
            spread.state = SpreadState.CANCELLED

    def cancel_all_open(self):
        """Emergency: cancel all open orders via reqGlobalCancel."""
        log.warning("Global cancel — cancelling all open orders")
        self.gw.reqGlobalCancel()

    # =========================================================================
    # Status queries
    # =========================================================================

    def is_filled(self, order_id: int) -> bool:
        return (self.gw.order_status
                    .get(order_id, {})
                    .get("status") == "Filled")

    def fill_price(self, order_id: int) -> float:
        return self.gw.order_status.get(order_id, {}).get("avg_price", 0.0)

    # =========================================================================
    # Contract / order builders (private)
    # =========================================================================

    def _build_spread_contract(self, spread: VerticalSpread) -> Contract:
        """
        Build an IBKR BAG combo contract for a vertical spread.

        SELL short_leg + BUY long_leg  (credit spread entry).
        The BAG action=SELL at the combo level means:
            - short_leg leg action = SELL
            - long_leg  leg action = BUY
        This is consistent with how TWS represents credit spread orders.
        """
        c = Contract()
        c.symbol     = spread.short_leg.symbol
        c.secType    = "BAG"
        c.currency   = "USD"
        c.exchange   = SMART

        short_leg_combo        = ComboLeg()
        short_leg_combo.conId    = spread.short_leg.con_id
        short_leg_combo.ratio    = 1
        short_leg_combo.action   = "SELL"
        short_leg_combo.exchange = SMART

        long_leg_combo         = ComboLeg()
        long_leg_combo.conId     = spread.long_leg.con_id
        long_leg_combo.ratio     = 1
        long_leg_combo.action    = "BUY"
        long_leg_combo.exchange  = SMART

        c.comboLegs = [short_leg_combo, long_leg_combo]
        return c

    @staticmethod
    def _sell_limit_order(limit_credit: float, quantity: int) -> Order:
        """
        LMT SELL order for a credit spread combo.

        lmtPrice is the NET credit per share we require.
        IBKR interprets a positive lmtPrice on a combo SELL as a credit.
        """
        o = Order()
        o.action        = "SELL"
        o.orderType     = "LMT"
        o.totalQuantity = quantity
        o.lmtPrice      = round(abs(limit_credit), 2)
        o.tif           = "DAY"
        o.transmit      = True
        o.outsideRth    = False
        return o

    @staticmethod
    def _buy_market_order(quantity: int) -> Order:
        """MKT BUY order to close a credit spread combo."""
        o = Order()
        o.action        = "BUY"
        o.orderType     = "MKT"
        o.totalQuantity = quantity
        o.tif           = "DAY"
        o.transmit      = True
        o.outsideRth    = False
        return o

    @staticmethod
    def _buy_limit_order(limit_debit: float, quantity: int) -> Order:
        """LMT BUY order to close a credit spread at a max debit."""
        o = Order()
        o.action        = "BUY"
        o.orderType     = "LMT"
        o.totalQuantity = quantity
        o.lmtPrice      = round(abs(limit_debit), 2)
        o.tif           = "DAY"
        o.transmit      = True
        o.outsideRth    = False
        return o

    @staticmethod
    def _validate_conids(spread: VerticalSpread):
        missing = []
        if not spread.short_leg.con_id:
            missing.append(f"short {spread.short_leg.strike}")
        if not spread.long_leg.con_id:
            missing.append(f"long {spread.long_leg.strike}")
        if missing:
            raise OrderError(
                f"Missing con_ids for {spread.side.value} spread legs: "
                + ", ".join(missing)
            )
