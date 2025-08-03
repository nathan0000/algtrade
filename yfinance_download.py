import yfinance as yf

# Download AAPL and SPY daily data for the last 6 months
tickers = ["AAPL", "SPY"]
for ticker in tickers:
    df = yf.download(ticker, period="6mo", interval="1d")
    df.to_csv(f"{ticker}_daily.csv")
