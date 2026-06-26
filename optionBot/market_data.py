import threading
import queue
import logging
from typing import Dict, Any

class MarketDataManager:
    def __init__(self, gateway_manager):
        self.gm = gateway_manager
        self.active_tickers: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._worker_thread = None

    def start(self):
        self._running = True
        # The target points to the method defined below
        self._worker_thread = threading.Thread(
            target=self._process_queue, 
            name="MarketData_Processor_Thread", 
            daemon=True
        )
        self._worker_thread.start()
        
        # 3 = Delayed data, 4 = Delayed Frozen data (essential for out-of-RTH)
        self.gm.app.reqMarketDataType(3) 
        logging.info("Market Data Manager processing thread initiated.")

    def request_realtime_options_data(self, req_id: int, contract: Any):
        with self._lock:
            self.active_tickers[req_id] = {"contract": contract, "ticks": {}, "greeks": {}}
        
        # 106 = Option Implied Volatility / Greeks, 101 = Open Interest
        self.gm.app.reqMktData(req_id, contract, "101,106", False, False, [])
        logging.info(f"Requested streaming market data for ReqId: {req_id}")

    def request_historical_bars(self, req_id: int, contract: Any, duration: str = "1 D", bar_size: str = "5 mins"):
        self.gm.app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="BID_ASK", # Always use BID_ASK for multi-asset / out-of-RTH options
            useRTH=0,             # 0 = Include out-of-RTH data
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )
        logging.info(f"Requested historical bars for ReqId: {req_id} (Out-of-RTH Enabled)")

    def _process_queue(self):
        """The background worker loop that safely unloads the network queue."""
        while self._running:
            try:
                # Thread-safe consumption from the Gateway's queue
                item = self.gm.app.market_data_queue.get(timeout=1)
                event_type = item[0]
                
                if event_type == 'tickPrice' or event_type == 'tickSize':
                    req_id, tick_type, val = item[1], item[2], item[3]
                    with self._lock:
                        if req_id in self.active_tickers:
                            self.active_tickers[req_id]["ticks"][tick_type] = val
                            
                elif event_type == 'tickOption':
                    req_id, tick_type, iv, delta, opt_price, gamma, vega, theta, und_price = item[1:]
                    with self._lock:
                        if req_id in self.active_tickers:
                            self.active_tickers[req_id]["greeks"] = {
                                "IV": iv, "Delta": delta, "Gamma": gamma, 
                                "Vega": vega, "Theta": theta, "UnderlyingPrice": und_price, "OptPrice": opt_price
                            }
                            logging.info(f"ReqId {req_id} Greeks Update -> Delta: {delta:.3f}, IV: {iv:.2%}")

                elif event_type == 'historicalBar':
                    req_id, bar = item[1], item[2]
                    logging.info(f"Hist Bar ReqId {req_id} -> Date: {bar.date}, Close: {bar.close}")
                    
                elif event_type == 'historicalEnd':
                    req_id = item[1]
                    logging.info(f"Historical Data Query complete for ReqId: {req_id}")

            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Error in data processing loop: {e}")

    def stop(self):
        self._running = False
        if self._worker_thread:
            self._worker_thread.join()