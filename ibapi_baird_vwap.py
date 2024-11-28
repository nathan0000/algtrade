import pandas as pd
import numpy as np
import backtrader as bt
import backtrader.feeds as btfeeds
from ibapi import wrapper
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.order import Order
import datetime

# Create a class to handle IBKR messages and events
class IBKRClient(EClient, wrapper.EWrapper):
    def __init__(self):
        EClient.__init__(self, self)

# Define the VWAP breakout strategy
class VWAPBreakoutStrategy(bt.Strategy):
    params = (
        ("vwap_period", 20),  # VWAP lookback period
        ("entry_dev", 0.02),  # Entry deviation from VWAP
        ("stop_loss", 0.02),  # Stop loss deviation from entry price
        ("take_profit", 0.03),  # Take profit deviation from entry price
    )

    def __init__(self):
        self.data_live = False
        self.vwap = bt.indicators.VolumeWeightedAveragePrice(self.data, period=self.params.vwap_period)
        self.order = None

    def next(self):
        if not self.data_live:
            return

        if self.data.close[0] > self.vwap[0] * (1 + self.params.entry_dev):
            # Place a long order when price crosses above VWAP + entry_dev
            self.buy()
        elif self.data.close[0] < self.data.close[-1] * (1 - self.params.stop_loss):
            # Close the long position when price crosses below entry price - stop_loss
            self.close()
        elif self.data.close[0] > self.data.close[-1] * (1 + self.params.take_profit):
            # Close the long position when price crosses above entry price + take_profit
            self.close()

# Create a Backtrader Cerebro engine
cerebro = bt.Cerebro()

# Create an IBKR client
ibkr_client = IBKRClient()
ibkr_client.connect("127.0.0.1", 7497, 1)

# Define the contract
contract = Contract()
contract.symbol = "AAPL"
contract.secType = "STK"
contract.exchange = "SMART"
contract.currency = "USD"

# Define the data feed
data = btfeeds.IBData(dataname=contract, historical=True, fromdate=datetime.datetime(2022, 1, 1))
cerebro.adddata(data)

# Add the strategy to the engine
cerebro.addstrategy(VWAPBreakoutStrategy)

# Set the initial cash and commission
cerebro.broker.set_cash(100000)
cerebro.broker.setcommission(commission=0.005)  # Assuming a $0.005/share commission

# Print the starting cash
print(f"Starting Portfolio Value: {cerebro.broker.getvalue()}")

# Run the backtest
cerebro.run()

# Print the final cash
print(f"Final Portfolio Value: {cerebro.broker.getvalue()}")

# Disconnect from IBKR
ibkr_client.disconnect()

# Plot Backtest results
cerebro.plot()
