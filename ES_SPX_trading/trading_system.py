import time
import threading
import logging
from datetime import datetime, timedelta
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract, ContractDetails
from ibapi.common import *
from ibapi.ticktype import TickTypeEnum, TickType

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("./logs/trading_system.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingSystem(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.req_id = 0
        self.contract_details = {}          # general contract details by reqId
        self.data = {}                       # market data by reqId (optional)
        self.connected = False
        self.next_order_id = None

        # ES futures tracking
        self.future_contract = None
        self.future_price = None
        self.future_close = None              # CLOSE price as fallback
        self.future_reqId = None               # reqId for future contract details
        self.future_mkt_reqId = None            # reqId for future market data

        # ES options tracking
        self.es_option_chain_reqIds = []        # list of pending reqIds
        self.es_option_contracts = []            # all ES option contracts from all expiries
        self.es_selected_reqIds = {}             # map reqId -> (strike, right, expiry)
        self.es_options_data = {}                 # (strike, right, expiry) -> {bid, ask, last}
        self.es_expiries_tried = []                # expiry strings already requested

        # SPX options tracking
        self.spx_estimate = None                  # estimated SPX price from ES future
        self.spx_option_chain_reqIds = []         # list of pending reqIds
        self.spx_option_contracts = []             # all SPX option contracts from all expiries
        self.spx_selected_reqIds = {}              # map reqId -> (strike, right, expiry)
        self.spx_options_data = {}                  # (strike, right, expiry) -> {bid, ask, last}
        self.spx_expiries_tried = []                 # expiry strings already requested

        # VIX tracking
        self.vix_price = None                      # current VIX index value
        self.vix_reqId = None                       # reqId for VIX market data
        self.vix_regime_thresholds = {
            'low': 15.0,
            'medium': 25.0
        }

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_order_id = orderId
        self.connected = True
        logger.info(f"Connected. Next order ID: {orderId}")

        # Use delayed data if you don't have live subscriptions (remove if you have live data)
        self.reqMarketDataType(4)   # 4 = delayed frozen data

        # Start by requesting the ES futures contract details
        self.request_es_futures_data()
        # Also request VIX data (independent)
        self.request_vix_data()

    def error(self, reqId: TickerId, errorCode: int, errorString: str, advancedOrderRejectJson="", arg5=""):
        logger.error(f"Error (reqId {reqId}): {errorCode} {errorString}")

    def contractDetails(self, reqId: int, contractDetails: ContractDetails):
        contract = contractDetails.contract
        logger.debug(f"ContractDetails received for reqId {reqId}: {contract.symbol} {contract.lastTradeDateOrContractMonth} "
                     f"strike={contract.strike} right={contract.right}")

        if reqId in self.es_option_chain_reqIds:
            self.es_option_contracts.append(contract)
            logger.debug(f"Added ES option: {contract.strike} {contract.right} expiry {contract.lastTradeDateOrContractMonth}")
        elif reqId in self.spx_option_chain_reqIds:
            self.spx_option_contracts.append(contract)
            logger.debug(f"Added SPX option: {contract.strike} {contract.right} expiry {contract.lastTradeDateOrContractMonth}")
        else:
            self.contract_details[reqId] = contractDetails

    def contractDetailsEnd(self, reqId: int):
        logger.info(f"ContractDetailsEnd for reqId {reqId}")
        if reqId == self.future_reqId:
            # Future contract details completed
            cd = self.contract_details.get(reqId)
            if cd:
                contract = cd.contract
                self.future_contract = contract
                logger.info(f"Future contract resolved: {contract.symbol} {contract.lastTradeDateOrContractMonth}")
                # Request market data for the future
                self.future_mkt_reqId = self.req_market_data(contract)
                # Request ES option chains for the next three trading days
                self.request_es_options_chains()
        elif reqId in self.es_option_chain_reqIds:
            # One ES option chain completed
            self.es_option_chain_reqIds.remove(reqId)
            logger.info(f"ES option chain reqId {reqId} completed. Total ES contracts so far: {len(self.es_option_contracts)}")
            if not self.es_option_chain_reqIds:
                # All requested ES chains are done
                self.process_es_option_chain()
        elif reqId in self.spx_option_chain_reqIds:
            self.spx_option_chain_reqIds.remove(reqId)
            logger.info(f"SPX option chain reqId {reqId} completed. Total SPX contracts so far: {len(self.spx_option_contracts)}")
            if not self.spx_option_chain_reqIds:
                self.process_spx_option_chain()
        # Other reqIds not handled

    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, attrib: TickAttrib):
        tick_name = TickTypeEnum.toStr(tickType)
        logger.debug(f"TickPrice. reqId: {reqId}, tickType: {tick_name} ({tickType}), price: {price}")

        # Update future price if this is the future market data stream
        if reqId == self.future_mkt_reqId:
            if tickType == 4:   # LAST price
                self.future_price = price
                self.spx_estimate = price
                logger.info(f"ES future LAST price updated: {price}")
                # Trigger SPX option chains if not already requested
                if not self.spx_option_chain_reqIds and not self.spx_expiries_tried:
                    logger.info("Triggering SPX option chain requests from LAST")
                    self.request_spx_options_chains()
            elif tickType == 9: # CLOSE price
                self.future_close = price
                logger.info(f"ES future CLOSE price received: {price}")
                if self.future_price is None and self.spx_estimate is None:
                    self.spx_estimate = price
                    logger.info(f"Setting SPX estimate from CLOSE: {price}")
                    if not self.spx_option_chain_reqIds and not self.spx_expiries_tried:
                        logger.info("Triggering SPX option chain requests from CLOSE")
                        self.request_spx_options_chains()
        # Update ES option data if this is an ES option request
        elif reqId in self.es_selected_reqIds:
            strike, right, expiry = self.es_selected_reqIds[reqId]
            key = (strike, right, expiry)
            if key not in self.es_options_data:
                self.es_options_data[key] = {}
            if tickType == 1:   # BID
                self.es_options_data[key]['bid'] = price
            elif tickType == 2: # ASK
                self.es_options_data[key]['ask'] = price
            elif tickType == 4: # LAST
                self.es_options_data[key]['last'] = price
        # Update SPX option data if this is an SPX option request
        elif reqId in self.spx_selected_reqIds:
            strike, right, expiry = self.spx_selected_reqIds[reqId]
            key = (strike, right, expiry)
            if key not in self.spx_options_data:
                self.spx_options_data[key] = {}
            if tickType == 1:   # BID
                self.spx_options_data[key]['bid'] = price
            elif tickType == 2: # ASK
                self.spx_options_data[key]['ask'] = price
            elif tickType == 4: # LAST
                self.spx_options_data[key]['last'] = price
        # Update VIX price if this is VIX data stream
        elif reqId == self.vix_reqId:
            if tickType == 4:   # LAST
                self.vix_price = price
                logger.info(f"VIX updated: {price}")
            elif tickType == 9: # CLOSE (fallback)
                self.vix_price = price
                logger.info(f"VIX CLOSE price received: {price}")

        # Optionally store in generic data dict
        key = f"req_{reqId}"
        if key not in self.data:
            self.data[key] = {}
        self.data[key][tick_name] = price

    def tickSize(self, reqId: TickerId, tickType: TickType, size: int):
        tick_name = TickTypeEnum.toStr(tickType)
        logger.debug(f"TickSize. reqId: {reqId}, tickType: {tick_name} ({tickType}), size: {size}")
        key = f"req_{reqId}"
        if key not in self.data:
            self.data[key] = {}
        self.data[key][tick_name] = size

    # ---------- Helper Methods ----------
    def next_req_id(self):
        self.req_id += 1
        return self.req_id

    def get_current_es_contract(self):
        """
        Determine the current ES futures contract based on days to expiry.
        Returns (Contract, local_symbol)
        """
        now = datetime.now()
        year = now.year
        month_codes = ['F','G','H','J','K','M','N','Q','U','V','X','Z']
        quarter_months = [2,5,8,11]  # 0‑based: Mar, Jun, Sep, Dec

        def third_friday(year, month):
            """Return datetime of the 3rd Friday of the given month (month is 0‑based)."""
            first_day = datetime(year, month+1, 1)
            days_to_friday = (4 - first_day.weekday() + 7) % 7   # weekday 4 = Friday
            first_friday = first_day + timedelta(days=days_to_friday)
            return first_friday + timedelta(days=14)

        # Collect all future quarterly expiries (up to 2 years ahead)
        expiries = []
        for q in quarter_months:
            for y in (year, year+1, year+2):
                exp = third_friday(y, q)
                if exp > now:
                    expiries.append((exp, y, q))
        expiries.sort()  # earliest first

        if len(expiries) < 2:
            raise RuntimeError("Unable to find two future ES expiries")

        first_exp, first_y, first_m = expiries[0]
        second_exp, second_y, second_m = expiries[1]

        days_to_first = (first_exp - now).days
        if days_to_first > 14:
            chosen_exp, chosen_y, chosen_m = first_exp, first_y, first_m
        else:
            chosen_exp, chosen_y, chosen_m = second_exp, second_y, second_m

        month_code = month_codes[chosen_m]
        year_short = str(chosen_y)[-1]
        local_symbol = f"ES{month_code}{year_short}"

        contract = Contract()
        contract.symbol = 'ES'
        contract.secType = 'FUT'
        contract.exchange = 'CME'
        contract.currency = 'USD'
        contract.lastTradeDateOrContractMonth = chosen_exp.strftime("%Y%m")
        contract.multiplier = '50'
        return contract, local_symbol

    def get_next_trading_dates(self, num_days=3):
        """
        Return a list of the next `num_days` trading days (Mon-Fri) as YYYYMMDD strings.
        Starts from today.
        """
        dates = []
        current = datetime.now()
        while len(dates) < num_days:
            # Skip Saturday and Sunday
            if current.weekday() < 5:  # Monday=0, Friday=4
                dates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        logger.info(f"Selected trading dates: {dates}")
        return dates

    def request_es_futures_data(self):
        """Request contract details for the appropriate ES future."""
        contract, symbol = self.get_current_es_contract()
        self.future_reqId = self.next_req_id()
        logger.info(f"Requesting contract details for ES: {contract.symbol} {contract.lastTradeDateOrContractMonth} ({symbol})")
        self.reqContractDetails(self.future_reqId, contract)

    def req_market_data(self, contract):
        """Request live market data for a contract. Returns the request ID."""
        req_id = self.next_req_id()
        logger.info(f"Requesting market data for {contract.symbol} {contract.lastTradeDateOrContractMonth} with reqId {req_id}")
        self.reqMktData(req_id, contract, "", False, False, [])
        return req_id

    def request_es_options_chains(self):
        """Request ES option chains for the next three trading days."""
        dates = self.get_next_trading_dates(3)
        self.es_expiries_tried = dates
        for expiry in dates:
            option_contract = Contract()
            option_contract.symbol = 'ES'
            option_contract.secType = "FOP"
            option_contract.exchange = "CME"
            option_contract.currency = "USD"
            option_contract.lastTradeDateOrContractMonth = expiry
            option_contract.strike = 0
            option_contract.right = ""
            option_contract.multiplier = "50"

            req_id = self.next_req_id()
            self.es_option_chain_reqIds.append(req_id)
            logger.info(f"Requesting ES option chain for expiry {expiry} with reqId {req_id}")
            self.reqContractDetails(req_id, option_contract)

    def process_es_option_chain(self):
        """Filter ES strikes near current future price from all collected contracts."""
        if self.future_price is not None:
            underlying = self.future_price
            source = "LAST"
        elif self.future_close is not None:
            underlying = self.future_close
            source = "CLOSE"
        else:
            underlying = 6700.0
            source = "DEFAULT"
        logger.info(f"Using {source} price {underlying} for ES strike selection")

        strike_range = 200
        selected = []
        for contract in self.es_option_contracts:
            strike = contract.strike
            if abs(strike - underlying) <= strike_range:
                selected.append(contract)

        logger.info(f"Selected {len(selected)} ES options near {underlying} across all expiries")
        for contract in selected:
            req_id = self.req_market_data(contract)
            self.es_selected_reqIds[req_id] = (contract.strike, contract.right, contract.lastTradeDateOrContractMonth)

    def request_spx_options_chains(self):
        """Request SPX option chains for the next three trading days, trying both SPX and SPXW."""
        if self.spx_estimate is None:
            logger.warning("SPX estimate not available yet, cannot request SPX options")
            return

        dates = self.get_next_trading_dates(3)
        self.spx_expiries_tried = dates
        for expiry in dates:
            # Try standard SPX first
            self._request_spx_chain_for_expiry(expiry, "SPX")
            # Also try weekly SPXW
            self._request_spx_chain_for_expiry(expiry, "SPXW")

    def _request_spx_chain_for_expiry(self, expiry, trading_class):
        """Helper to request a single SPX option chain."""
        option_contract = Contract()
        option_contract.symbol = "SPX"
        option_contract.secType = "OPT"
        option_contract.exchange = "CBOE"
        option_contract.currency = "USD"
        option_contract.lastTradeDateOrContractMonth = expiry
        option_contract.strike = 0
        option_contract.right = ""
        option_contract.multiplier = "100"
        option_contract.tradingClass = trading_class

        req_id = self.next_req_id()
        self.spx_option_chain_reqIds.append(req_id)
        logger.info(f"Requesting SPX option chain for expiry {expiry} tradingClass {trading_class} with reqId {req_id}")
        self.reqContractDetails(req_id, option_contract)

    def process_spx_option_chain(self):
        """Filter SPX strikes near estimated SPX price from all collected contracts."""
        underlying = self.spx_estimate
        if underlying is None:
            logger.error("SPX estimate is None, cannot process SPX options")
            return
        logger.info(f"Using estimated SPX price {underlying} for strike selection")

        strike_range = 80
        selected = []
        for contract in self.spx_option_contracts:
            strike = contract.strike
            if abs(strike - underlying) <= strike_range:
                selected.append(contract)

        logger.info(f"Selected {len(selected)} SPX options near {underlying} across all expiries")
        if not selected:
            logger.warning("No SPX options within strike range. Check if strikes are available.")
        else:
            logger.info(f"First few selected strikes: {[(c.strike, c.right, c.lastTradeDateOrContractMonth) for c in selected[:5]]}")
        for contract in selected:
            req_id = self.req_market_data(contract)
            self.spx_selected_reqIds[req_id] = (contract.strike, contract.right, contract.lastTradeDateOrContractMonth)

    def request_vix_data(self):
        """Request market data for the VIX index."""
        contract = Contract()
        contract.symbol = "VIX"
        contract.secType = "IND"
        contract.exchange = "CBOE"
        contract.currency = "USD"
        # VIX index has no multiplier or expiry
        self.vix_reqId = self.next_req_id()
        logger.info(f"Requesting VIX market data with reqId {self.vix_reqId}")
        self.reqMktData(self.vix_reqId, contract, "", False, False, [])

    def get_vix_regime(self):
        """Determine VIX regime based on current price."""
        if self.vix_price is None:
            return "UNKNOWN"
        if self.vix_price < self.vix_regime_thresholds['low']:
            return "LOW"
        elif self.vix_price < self.vix_regime_thresholds['medium']:
            return "MEDIUM"
        else:
            return "HIGH"

    def run_loop(self):
        """Run the client in a separate thread."""
        self.run()

# ---------- Main Execution ----------
def main():
    app = TradingSystem()
    app.connect("127.0.0.1", 4002, clientId=1)

    # Start the socket in a background thread
    api_thread = threading.Thread(target=app.run_loop, daemon=True)
    api_thread.start()

    # Wait for connection and data to start flowing
    time.sleep(2)
    if not app.connected:
        logger.error("Not connected after 2 seconds, exiting.")
        return

    # Keep the main thread alive and periodically print option data and VIX
    try:
        while True:
            time.sleep(5)
            vix_regime = app.get_vix_regime()
            logger.info("\n--- Current ES Future Price: {} (CLOSE: {}) ---".format(app.future_price, app.future_close))
            logger.info("--- VIX: {} ({}) ---".format(app.vix_price, vix_regime))
            logger.info("ES Options with recent quotes:")
            for (strike, right, expiry), quotes in app.es_options_data.items():
                bid = quotes.get('bid', 'N/A')
                ask = quotes.get('ask', 'N/A')
                last = quotes.get('last', 'N/A')
                logger.info(f"  {expiry} {strike} {right}: bid={bid}, ask={ask}, last={last}")
            logger.info("SPX Options with recent quotes:")
            for (strike, right, expiry), quotes in app.spx_options_data.items():
                bid = quotes.get('bid', 'N/A')
                ask = quotes.get('ask', 'N/A')
                last = quotes.get('last', 'N/A')
                logger.info(f"  {expiry} {strike} {right}: bid={bid}, ask={ask}, last={last}")
            logger.info("")
    except KeyboardInterrupt:
        logger.info("Disconnecting...")
        app.disconnect()

if __name__ == "__main__":
    main()