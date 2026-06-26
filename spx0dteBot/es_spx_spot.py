from ib_async import IB, Index, Future
import datetime
import pytz

def get_overnight_basis():
    ib = IB()
    ib.connect('192.168.1.116', 4002, clientId=2)
    
    # 1. Define and qualify contracts
    spx = Index(symbol='SPX', exchange='CBOE', currency='USD')
    es_template = Future(symbol='ES', lastTradeDateOrContractMonth='202609', exchange='CME', currency='USD')
    
    qualified_contracts = ib.qualifyContracts(spx, es_template)
    spx_contract = qualified_contracts[0]
    es_contract = qualified_contracts[1]
    
    # 2. Set target time to the current moment 
    # (Leaving endDateTime empty defaults to 'now' and grabs the most recent daily bars)
    print("Requesting daily bars to extract official session closes...")
    
    # 3. Request 1 Day bars (universally allowed without premium subscriptions)
    spx_bars = ib.reqHistoricalData(
        spx_contract, endDateTime='', durationStr='2 D',
        barSizeSetting='1 day', whatToShow='TRADES', useRTH=True
    )
    
    es_bars = ib.reqHistoricalData(
        es_contract, endDateTime='', durationStr='2 D',
        barSizeSetting='1 day', whatToShow='TRADES', useRTH=True
    )
    
    ib.disconnect()
    ib.waitOnUpdate(timeout=0.5)
    
    # 4. Extract data and compute basis
    if spx_bars and es_bars:
        # Grab the last completed daily bar
        spx_close = spx_bars[-1].close
        es_close = es_bars[-1].close
        basis = es_close - spx_close
        
        print(f"\n--- Official Closing Values ---")
        print(f"SPX Daily Close: {spx_close}")
        print(f"ES Future Daily Close: {es_close}")
        print(f"Calculated Overnight Basis: {basis:.2f}")
        return basis
    else:
        print("Failed to retrieve daily closing bars.")
        return None

if __name__ == '__main__':
    get_overnight_basis()