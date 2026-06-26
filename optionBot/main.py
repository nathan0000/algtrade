import time
import logging
from gateway import IBGatewayManager
from contract import ContractFactory
from market_data import MarketDataManager

if __name__ == "__main__":
    # Initialize Core Gateway
    gateway = IBGatewayManager(host="192.168.1.116", port=4002, clientId=10)
    gateway.connect()
    time.sleep(2)  # Wait for API connection validation frame
    
    # Initialize Engine & Request Delayed / Out-Of-RTH data configuration
    md_manager = MarketDataManager(gateway)
    md_manager.start()
    
    # ==========================================
    # CASE 1: Stock Options (AAPL)
    # ==========================================
    aapl_stock = ContractFactory.create_stock("AAPL")
    aapl_exp, aapl_strikes = ContractFactory.resolve_option_chain(gateway, aapl_stock)
    
    if aapl_exp and aapl_strikes:
        # Pick Near-term Expiry and ATM Strike
        aapl_opt = ContractFactory.create_stock_option("AAPL", aapl_exp[0], aapl_strikes[len(aapl_strikes)//2], "C")
        md_manager.request_realtime_options_data(req_id=1001, contract=aapl_opt)
        md_manager.request_historical_bars(req_id=1002, contract=aapl_opt)

    # ==========================================
    # CASE 2: Index Options (SPX)
    # ==========================================
    spx_index = ContractFactory.create_index("SPX", exchange="CBOE")
    spx_exp, spx_strikes = ContractFactory.resolve_option_chain(gateway, spx_index)
    
    if spx_exp and spx_strikes:
        # Generate standard monthly SPX options (Trading Class "SPX")
        # Change trading_class to "SPXW" if choosing weekly expirations
        spx_opt = ContractFactory.create_index_option(
            symbol="SPX", 
            expiry=spx_exp[0], 
            strike=spx_strikes[len(spx_strikes)//2], 
            right="P", 
            trading_class="SPX"
        )
        md_manager.request_realtime_options_data(req_id=2001, contract=spx_opt)
        md_manager.request_historical_bars(req_id=2002, contract=spx_opt)

    # ==========================================
    # CASE 3: Futures Options (ES)
    # ==========================================
    # First define or query the underlying future. (e.g., September 2026 contract)
    es_future = ContractFactory.create_future("ES", expiry="202609", exchange="CME")
    es_exp, es_strikes = ContractFactory.resolve_option_chain(gateway, es_future)
    
    if es_exp and es_strikes:
        # Generate Futures Option (FOP) Contract. Multiplier for ES options is '50'.
        es_fop = ContractFactory.create_futures_option(
            symbol="ES", 
            expiry=es_exp[0], 
            strike=es_strikes[len(es_strikes)//2], 
            right="C", 
            multiplier="50"
        )
        md_manager.request_realtime_options_data(req_id=3001, contract=es_fop)
        md_manager.request_historical_bars(req_id=3002, contract=es_fop)

    # Run system for 20 seconds to stream incoming data pipelines concurrently
    logging.info("Streaming and polling historical backlogs across Stock/Index/Futures Option classes...")
    time.sleep(20)
    
    gateway.disconnect()