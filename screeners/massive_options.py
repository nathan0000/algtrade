import os
from tkinter import TRUE
from massive import RESTClient

# Initialize the client
client = RESTClient(os.environ.get('MASSIVE_API_KEY'),trace=TRUE, verbose=TRUE)

# Example: Get historical aggregate bars for an SPX option contract
# (Ensure you use the correct ticker format, e.g., 'O:SPX251219C00650000')
ticker = "AAPL"

# List Aggregates (Bars)
aggs = []
for a in client.list_aggs(ticker=ticker, multiplier=1, timespan="minute", from_="2026-01-01", to="2026-06-13", limit=50000):
    aggs.append(a)

print(aggs)

# Get Last Trade
#trade = client.get_last_trade(ticker=ticker)
#print(trade)

# List Trades
#trades = client.list_trades(ticker=ticker, timestamp="2026-01-04")
#for trade in trades:
#    print(trade)

# Get Last Quote
#quote = client.get_last_quote(ticker=ticker)
#print(quote)

# List Quotes
#quotes = client.list_quotes(ticker=ticker, timestamp="2026-01-04")
#for quote in quotes:
#    print(quote)