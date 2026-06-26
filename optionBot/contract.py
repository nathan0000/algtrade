from ibapi.contract import Contract
import queue
import time
import logging

class ContractFactory:
    # --- Underlyings ---
    
    @staticmethod
    def create_stock(symbol: str, exchange: str = "SMART", currency: str = "USD") -> Contract:
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    @staticmethod
    def create_index(symbol: str, exchange: str = "CBOE", currency: str = "USD") -> Contract:
        """Creates an Index Contract (e.g., SPX, NDX, RUT)"""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "IND"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    @staticmethod
    def create_future(symbol: str, expiry: str, exchange: str = "CME", currency: str = "USD") -> Contract:
        """Creates a continuous or specific Underlying Future Contract (e.g., ES, NQ)"""
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "FUT"
        contract.exchange = exchange
        contract.currency = currency
        contract.lastTradeDateOrContractMonth = expiry  # e.g., '202609'
        return contract

    # --- Derivatives / Options ---

    @staticmethod
    def create_stock_option(symbol: str, expiry: str, strike: float, right: str, exchange: str = "SMART") -> Contract:
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = float(strike)
        contract.right = right  # "C" or "P"
        contract.multiplier = "100"
        return contract

    @staticmethod
    def create_index_option(symbol: str, expiry: str, strike: float, right: str, trading_class: str = None) -> Contract:
        contract = Contract()
        contract.symbol = symbol          # e.g., "SPX"
        contract.secType = "OPT"
        contract.lastTradeDateOrContractMonth = expiry # e.g., "20260624"
        contract.strike = float(strike)
        contract.right = right            # "P" or "C"
        
        # Core Index Routing parameters
        contract.exchange = "CBOE"  # Index options must route to CBOE
        contract.currency = "USD"
        contract.multiplier = "100"       
        
        # FIX: Handle the trading class argument explicitly for SPX vs SPXW differentiation
        if trading_class:
            contract.tradingClass = trading_class
        else:
            contract.tradingClass = symbol # Default fallback to the main symbol
            
        return contract

    @staticmethod
    def create_futures_option(symbol: str, expiry: str, strike: float, right: str, exchange: str = "CME", multiplier: str = "50") -> Contract:
        """
        Creates a Futures Option (FOP) contract (e.g., ES Options, NQ Options).
        Note: 'symbol' for CME FOPs is often the underlying future ticker (e.g., 'ES').
        """
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "FOP"  # Futures Option Security Type
        contract.exchange = exchange
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = float(strike)
        contract.right = right
        contract.multiplier = multiplier  # e.g., 50 for ES, 20 for NQ
        return contract

    # --- Chain Resolution ---

    @staticmethod
    def resolve_underlying_con_id(gateway_manager, contract: Contract) -> int:
        """Synchronously looks up the correct conId from the IB backend."""
        req_id = int(time.time() * 1000) % 1000000
        logging.info(f"Querying contract details to resolve conId for {contract.symbol} ({contract.secType})...")
        
        gateway_manager.app.reqContractDetails(req_id, contract)
        
        con_id = 0
        while True:
            try:
                res_id, payload = gateway_manager.app.contract_lookup_queue.get(timeout=5)
                if res_id == req_id:
                    if payload == "END":
                        break
                    con_id = payload  # Capture the actual integer conId
            except queue.Empty:
                logging.warning(f"Timeout waiting for contract details for {contract.symbol}")
                break
        return con_id

    @staticmethod
    def resolve_option_chain(gateway_manager, underlying_contract: Contract) -> tuple:
        """Two-step verification engine that completely eliminates Error 321."""
        # 1. Resolve the explicit conId first
        con_id = ContractFactory.resolve_underlying_con_id(gateway_manager, underlying_contract)
        if con_id == 0:
            logging.error(f"Cannot resolve option chain: failed to obtain a valid conId for {underlying_contract.symbol}")
            return [], []
        
        logging.info(f"Successfully resolved conId={con_id} for {underlying_contract.symbol}. Requesting option parameters...")

        # 2. Set strict parameters based on security guidelines
        req_id = int(time.time() * 1000) % 1000000
        sec_type = underlying_contract.secType
        symbol = underlying_contract.symbol
        
        # Mapping specific fields required by the IB validation gateway
        if sec_type in ["STK", "IND"]:
            fop_exchange = ""  # Must be empty for Equities and Indices
        elif sec_type == "FUT":
            fop_exchange = underlying_contract.exchange  # e.g., "CME"
        else:
            fop_exchange = ""

        # Dispatch with verified parameters
        gateway_manager.app.reqSecDefOptParams(req_id, symbol, fop_exchange, sec_type, con_id)
        
        expirations, strikes = set(), set()
        while True:
            try:
                res_id, exp, stk = gateway_manager.app.contract_details_queue.get(timeout=6)
                if res_id == req_id:
                    if exp == "END":
                        break
                    expirations.update(exp)
                    strikes.update(stk)
            except queue.Empty:
                logging.warning(f"Timeout reached waiting for {symbol} option chain parameters.")
                break
                
        return sorted(list(expirations)), sorted(list(strikes))