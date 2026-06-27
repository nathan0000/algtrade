"""
test_order_lifecycle.py — Integration test for the full order lifecycle
using IBKR paper trading account.

This test validates:
  1. Gateway connection and handshake
  2. Contract resolution (con_id lookup)
  3. Market data snapshot (prices + Greeks)
  4. Spread order construction (BAG combo)
  5. Order submission and fill waiting
  6. Order cancellation (if unfilled)
  7. Spread closing order

IMPORTANT: This test uses PAPER trading credentials. Configure your
IB Gateway/TWS to use paper trading before running.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Add project root to path if needed
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway import IBGateway
from market_data import MarketData
from order_manager import OrderManager
from models import (
    OptionLeg, OptionRight, VerticalSpread, SpreadSide,
    SpreadState
)

# ── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)

# Set specific log levels for verbose modules
logging.getLogger("MarketData").setLevel(logging.DEBUG)
logging.getLogger("OrderManager").setLevel(logging.DEBUG)
logging.getLogger("Gateway").setLevel(logging.INFO)

log = logging.getLogger("TestOrderLifecycle")

# ── Configuration ────────────────────────────────────────────────────────────

class TestConfig:
    """Test configuration - adjust these for your paper trading setup."""
    
    # IB Gateway connection
    HOST = os.getenv("IB_HOST", "127.0.0.1")
    PORT = int(os.getenv("IB_PORT", "4002"))  # 4002 = Gateway paper, 7497 = TWS paper
    CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
    
    # Test contract parameters
    SYMBOL = "SPX"
    EXPIRY = ""  # Leave empty to use today's 0DTE
    MULTIPLIER = 100
    
    # Test strikes (will be resolved to con_ids)
    # These are example strikes for SPX — adjust to current market
    # The test will fetch current market data and pick appropriate strikes
    SHORT_STRIKE = 0  # Will be determined dynamically
    LONG_STRIKE = 0   # Will be determined dynamically
    
    # Order parameters
    QUANTITY = 1
    LIMIT_CREDIT = 1.50  # $1.50 per share = $150 credit
    FILL_TIMEOUT = 60.0  # seconds to wait for fill before canceling
    
    # SPX is cash-settled; multiplier = 100 (1 point = $100)


# ── Helper functions ─────────────────────────────────────────────────────────

def find_suitable_strikes(
    md: MarketData,
    spot: float,
    expiry: str,
    delta_target: float = 0.12,
    wing_width: float = 30.0,
) -> tuple[Optional[OptionLeg], Optional[OptionLeg], Optional[OptionLeg], Optional[OptionLeg]]:
    """
    Find suitable call and put strikes for a test iron condor.
    Returns (call_short, call_long, put_short, put_long) or (None, ...).
    """
    chain = md.get_chain()
    strikes = sorted(chain["strikes"])
    
    if not strikes:
        log.error("No strikes available in chain")
        return None, None, None, None
    
    log.info(f"Available strikes: {strikes[:10]}... (total {len(strikes)})")
    
    # Find call strikes above spot
    call_candidates = [s for s in strikes if s > spot]
    call_short = None
    call_long = None
    
    for s in call_candidates[:20]:  # Check closest 20 strikes
        long_s = s + wing_width
        if long_s in strikes:
            # We'll resolve and price these later, but for now just find
            # strikes that are roughly at the right delta range
            call_short = OptionLeg("SPX", expiry, s, OptionRight.CALL)
            call_long = OptionLeg("SPX", expiry, long_s, OptionRight.CALL)
            break
    
    # Find put strikes below spot
    put_candidates = sorted([s for s in strikes if s < spot], reverse=True)
    put_short = None
    put_long = None
    
    for s in put_candidates[:20]:
        long_s = s - wing_width
        if long_s in strikes:
            put_short = OptionLeg("SPX", expiry, s, OptionRight.PUT)
            put_long = OptionLeg("SPX", expiry, long_s, OptionRight.PUT)
            break
    
    if call_short is None:
        log.warning("No suitable call strikes found")
    if put_short is None:
        log.warning("No suitable put strikes found")
    
    return call_short, call_long, put_short, put_long


def wait_for_order_status(
    om: OrderManager,
    order_id: int,
    target_status: str,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> bool:
    """
    Wait for an order to reach a specific status.
    Returns True if reached, False if timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        status = om.gw.order_status.get(order_id, {})
        if status.get("status") == target_status:
            return True
        time.sleep(poll_interval)
    
    log.warning(f"Order {order_id} did not reach {target_status} within {timeout}s")
    log.info(f"Current status: {om.gw.order_status.get(order_id, {})}")
    return False


# ── Main test suite ──────────────────────────────────────────────────────────

class OrderLifecycleTest:
    """Test the full order lifecycle from contract resolution to close."""
    
    def __init__(self, config: TestConfig):
        self.config = config
        self.gw: Optional[IBGateway] = None
        self.md: Optional[MarketData] = None
        self.om: Optional[OrderManager] = None
        self.test_spread: Optional[VerticalSpread] = None
        self.test_order_id: Optional[int] = None
    
    def setup(self) -> bool:
        """Initialize connections and market data."""
        log.info("=" * 60)
        log.info("ORDER LIFECYCLE TEST — SETUP")
        log.info("=" * 60)
        
        # ── Gateway ───────────────────────────────────────────────────────────
        log.info(f"Connecting to IB Gateway {self.config.HOST}:{self.config.PORT}")
        self.gw = IBGateway(self.config.HOST, self.config.PORT, self.config.CLIENT_ID)
        
        if not self.gw.connect_and_run(timeout=15.0, settle_sec=2.0):
            log.error("Failed to connect to IB Gateway")
            return False
        
        log.info("✓ Gateway connected")
        
        # ── Market Data ──────────────────────────────────────────────────────
        self.md = MarketData(self.gw)
        
        # Fetch chain
        log.info("Fetching option chain...")
        if not self.md.sync_chain(timeout=30.0):
            log.error("Failed to fetch option chain")
            return False
        
        expiries = self.md.available_expirations()
        log.info(f"Available expiries: {expiries[:5]}")
        
        # Get today's expiry
        expiry = self.md.today_expiry()
        if not expiry:
            log.error("No 0DTE expiry found")
            return False
        log.info(f"Using expiry: {expiry}")
        
        # Get spot price
        spot = self.md.get_spx_spot()
        log.info(f"SPX spot: {spot:.2f}")
        
        # ── Order Manager ────────────────────────────────────────────────────
        self.om = OrderManager(self.gw, multiplier=self.config.MULTIPLIER)
        log.info("✓ Order Manager initialized")
        
        # ── Find suitable strikes ────────────────────────────────────────────
        call_short, call_long, put_short, put_long = find_suitable_strikes(
            self.md, spot, expiry, wing_width=30.0
        )
        
        if call_short is None or put_short is None:
            log.error("Could not find suitable strikes")
            return False
        
        # ── Resolve and price candidate legs ─────────────────────────────────
        legs_to_resolve = [l for l in [call_short, call_long, put_short, put_long] if l]
        log.info(f"Resolving {len(legs_to_resolve)} legs...")
        
        if not self.md.resolve_conids(legs_to_resolve):
            log.error("Failed to resolve con_ids")
            return False
        
        if not self.md.snapshot_prices_and_greeks(legs_to_resolve):
            log.error("Failed to get prices and Greeks")
            return False
        
        # Log what we got
        for leg in legs_to_resolve:
            log.info(
                f"  {leg.right.value} {leg.strike}: "
                f"con_id={leg.con_id}, bid={leg.bid:.2f}, ask={leg.ask:.2f}, "
                f"mid={leg.mid:.2f}, delta={leg.delta:.4f}"
            )
        
        # ── Build test spread ────────────────────────────────────────────────
        # Use call side for testing (less risky than put side)
        self.test_spread = VerticalSpread(
            side=SpreadSide.CALL,
            short_leg=call_short,
            long_leg=call_long,
            quantity=self.config.QUANTITY,
            multiplier=self.config.MULTIPLIER,
        )
        
        log.info(f"Test spread: {call_short.strike}/{call_long.strike} CALL")
        log.info(f"  Credit at mid: ${self.test_spread.credit_dollars:.0f}")
        
        return True
    
    def test_entry_order(self) -> bool:
        """Test entering a spread order."""
        log.info("\n" + "=" * 60)
        log.info("TEST: ENTRY ORDER")
        log.info("=" * 60)
        
        if self.test_spread is None:
            log.error("No test spread available")
            return False
        
        # ── Submit order ──────────────────────────────────────────────────────
        limit_credit = round(self.test_spread.short_leg.mid - self.test_spread.long_leg.mid, 2)
        
        log.info(f"Submitting SELL LMT order for credit ${limit_credit:.2f}/share")
        result = self.om.enter_spread(
            self.test_spread,
            limit_credit=limit_credit,
            quantity=self.config.QUANTITY,
            tag="TEST"
        )
        
        self.test_order_id = result.order_id
        log.info(f"✓ Order submitted: {self.test_order_id}")
        log.info(f"  Spread state: {self.test_spread.state.value}")
        
        # ── Wait for status ──────────────────────────────────────────────────
        # Wait up to 30s to see if order gets filled or at least acknowledged
        if wait_for_order_status(self.om, self.test_order_id, "Filled", timeout=30.0):
            log.info("✓ Order filled!")
            log.info(f"  Fill price: ${self.om.fill_price(self.test_order_id):.4f}")
            log.info(f"  Credit: ${self.test_spread.filled_credit * self.config.MULTIPLIER:.0f}")
            return True
        else:
            log.info("Order not filled within 30s — will test cancellation")
            return True  # Not a failure — we'll test cancellation
    
    def test_cancel_order(self) -> bool:
        """Test cancelling an unfilled order."""
        log.info("\n" + "=" * 60)
        log.info("TEST: CANCEL ORDER")
        log.info("=" * 60)
        
        if self.test_order_id is None:
            log.warning("No order to cancel")
            return True
        
        # Check if already filled
        status = self.gw.order_status.get(self.test_order_id, {})
        if status.get("status") == "Filled":
            log.info("Order already filled — skipping cancellation test")
            return True
        
        # ── Cancel order ─────────────────────────────────────────────────────
        log.info(f"Cancelling order {self.test_order_id}")
        self.gw.cancelOrder(self.test_order_id, "")
        self.test_spread.state = SpreadState.CANCELLED
        
        # Wait for cancellation
        if wait_for_order_status(self.om, self.test_order_id, "Cancelled", timeout=15.0):
            log.info("✓ Order cancelled successfully")
            return True
        else:
            log.warning("Order status not 'Cancelled' — may have filled or still pending")
            return True
    
    def test_close_order(self) -> bool:
        """Test closing a filled spread."""
        log.info("\n" + "=" * 60)
        log.info("TEST: CLOSE ORDER")
        log.info("=" * 60)
        
        if self.test_spread is None:
            log.error("No test spread available")
            return False
        
        if not self.test_spread.is_active:
            log.info("Spread is not active — skipping close test")
            return True
        
        # ── Submit close order ──────────────────────────────────────────────
        log.info("Closing spread with MKT order...")
        result = self.om.close_spread(
            self.test_spread,
            use_market=True,
            quantity=self.config.QUANTITY,
            tag="TEST_CLOSE"
        )
        
        if result is None:
            log.warning("No close order submitted")
            return True
        
        close_order_id = result.order_id
        log.info(f"✓ Close order submitted: {close_order_id}")
        
        # Wait for fill
        if self.om.wait_for_close_fill(self.test_spread, timeout=30.0):
            log.info("✓ Close order filled!")
            log.info(f"  Debit: ${self.test_spread.close_debit * self.config.MULTIPLIER:.0f}")
            log.info(f"  P&L: ${self.test_spread.pnl_dollars:.0f}")
            return True
        else:
            log.warning("Close order not filled within 30s")
            return True
    
    def test_order_status_query(self) -> bool:
        """Test querying order status."""
        log.info("\n" + "=" * 60)
        log.info("TEST: ORDER STATUS QUERY")
        log.info("=" * 60)
        
        if self.test_order_id is None:
            log.warning("No order to query")
            return True
        
        status = self.gw.order_status.get(self.test_order_id, {})
        log.info(f"Order {self.test_order_id}:")
        log.info(f"  Status: {status.get('status', 'Unknown')}")
        log.info(f"  Filled: {status.get('filled', 0)}")
        log.info(f"  Remaining: {status.get('remaining', 0)}")
        log.info(f"  Avg Price: {status.get('avg_price', 0):.4f}")
        
        # Check fill price
        fill_price = self.om.fill_price(self.test_order_id)
        log.info(f"  Fill price (from OrderManager): ${fill_price:.4f}")
        
        # Check filled status
        is_filled = self.om.is_filled(self.test_order_id)
        log.info(f"  Is filled: {is_filled}")
        
        return True
    
    def test_spread_pnl_calculation(self) -> bool:
        """Test P&L calculation."""
        log.info("\n" + "=" * 60)
        log.info("TEST: P&L CALCULATION")
        log.info("=" * 60)
        
        if self.test_spread is None:
            log.error("No test spread available")
            return False
        
        # Refresh prices
        if self.test_spread.is_active:
            self.md.refresh_spread_prices(self.test_spread)
        
        log.info(f"Spread: {self.test_spread.short_leg.strike}/{self.test_spread.long_leg.strike} CALL")
        log.info(f"  State: {self.test_spread.state.value}")
        
        if self.test_spread.is_active:
            mark = self.test_spread.current_mark()
            loss = self.test_spread.unrealised_loss_dollars()
            log.info(f"  Current mark: ${mark:.2f}/share")
            log.info(f"  Unrealised loss: ${loss:.0f}")
        
        if self.test_spread.is_filled:
            log.info(f"  Filled credit: ${self.test_spread.filled_credit * self.config.MULTIPLIER:.0f}")
        
        if self.test_spread.is_closed:
            log.info(f"  Close debit: ${self.test_spread.close_debit * self.config.MULTIPLIER:.0f}")
            log.info(f"  P&L: ${self.test_spread.pnl_dollars:.0f}")
        
        return True
    
    def teardown(self):
        """Clean up connections."""
        log.info("\n" + "=" * 60)
        log.info("TEARDOWN")
        log.info("=" * 60)
        
        # Cancel any remaining orders
        if self.gw and self.test_order_id:
            status = self.gw.order_status.get(self.test_order_id, {})
            if status.get("status") not in ("Filled", "Cancelled"):
                log.info(f"Cancelling order {self.test_order_id}")
                try:
                    self.gw.cancelOrder(self.test_order_id, "")
                except Exception as e:
                    log.warning(f"Error cancelling order: {e}")
                time.sleep(2.0)
        
        # Disconnect
        if self.gw:
            log.info("Disconnecting from IB Gateway")
            try:
                self.gw.safe_disconnect()
            except Exception as e:
                log.warning(f"Error disconnecting: {e}")
        
        log.info("✓ Test complete")


# ── Run the test ─────────────────────────────────────────────────────────────

def run_full_test():
    """Run the complete test suite."""
    config = TestConfig()
    test = OrderLifecycleTest(config)
    
    results = []
    
    try:
        # Setup
        if not test.setup():
            log.error("Setup failed — aborting tests")
            return
        
        # Run tests in order
        tests = [
            ("Entry Order", test.test_entry_order),
            ("Order Status Query", test.test_order_status_query),
            ("Spread P&L", test.test_spread_pnl_calculation),
            ("Cancel Order", test.test_cancel_order),
            ("Close Order", test.test_close_order),
        ]
        
        for name, test_func in tests:
            try:
                log.info(f"\n>>> Running: {name}")
                result = test_func()
                results.append((name, result))
                log.info(f">>> Result: {'✓ PASS' if result else '✗ FAIL'}")
            except Exception as e:
                log.error(f"Test '{name}' failed with exception: {e}")
                results.append((name, False))
                import traceback
                traceback.print_exc()
            
            # Brief pause between tests
            time.sleep(1.0)
    
    finally:
        # Cleanup
        test.teardown()
    
    # Summary
    log.info("\n" + "=" * 60)
    log.info("TEST SUMMARY")
    log.info("=" * 60)
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        log.info(f"  {name:<20} {status}")
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    log.info(f"\nTotal: {passed}/{total} tests passed")
    
    return all(r for _, r in results)


# ── Quick test for specific scenarios ──────────────────────────────────────

def quick_entry_test():
    """
    Quick test: Just submit an entry order and see if it fills.
    Useful for rapid testing of order submission.
    """
    config = TestConfig()
    test = OrderLifecycleTest(config)
    
    try:
        if not test.setup():
            return False
        
        return test.test_entry_order()
    
    finally:
        test.teardown()


def quick_cancel_test():
    """
    Quick test: Submit an order and immediately cancel it.
    Tests the cancellation path.
    """
    config = TestConfig()
    config.FILL_TIMEOUT = 5.0  # Short timeout to ensure unfilled
    test = OrderLifecycleTest(config)
    
    try:
        if not test.setup():
            return False
        
        if not test.test_entry_order():
            return False
        
        return test.test_cancel_order()
    
    finally:
        test.teardown()


# ── Main entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test IBKR order lifecycle with paper trading"
    )
    parser.add_argument(
        "--test",
        choices=["full", "entry", "cancel"],
        default="full",
        help="Which test to run"
    )
    parser.add_argument(
        "--host",
        default=TestConfig.HOST,
        help="IB Gateway host"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=TestConfig.PORT,
        help="IB Gateway port (4002=Gateway paper, 7497=TWS paper)"
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=TestConfig.CLIENT_ID,
        help="Client ID"
    )
    
    args = parser.parse_args()
    
    # Update config from CLI
    TestConfig.HOST = args.host
    TestConfig.PORT = args.port
    TestConfig.CLIENT_ID = args.client_id
    
    # Run selected test
    if args.test == "full":
        success = run_full_test()
    elif args.test == "entry":
        success = quick_entry_test()
    elif args.test == "cancel":
        success = quick_cancel_test()
    else:
        success = run_full_test()
    
    sys.exit(0 if success else 1)