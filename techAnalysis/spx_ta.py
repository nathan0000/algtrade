from ta import *
import yfinance as yf
import pandas as pd

def calculate_macd(df):
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    df['SwingBuy_Signal'] = (df['SMA_20'] > df['SMA_50'])
    df['SwingSell_Signal'] = (df['SMA_20'] < df['SMA_50'])

    # calculate 12/26 MACD
    # Calculate the 12-period EMA
    df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
    # Calculate the 26-period EMA
    df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
    # Calculate the MACD line
    df['MACD'] = df['EMA_12'] - df['EMA_26']
    # Calculate the Signal Line
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()

    df['Buy_Signal_MACD'] = (df['MACD'] > df['Signal_Line'])
    df['Sell_Signal_MACD'] = (df['MACD'] < df['Signal_Line'])

    df['Combined_Buy_Signal'] = (df['SwingBuy_Signal'] & df['Buy_Signal_MACD'])
    df['Combined_Sell_Signal'] = (df['SwingSell_Signal'] & df['Sell_Signal_MACD'])
    return df

def calculate_rsi(df, window=14):
    delta = df['Close'].diff(1) # Calculate the difference in closing prices
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean() # Calculate gains
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean() # Calculate loss
    rs = gain / loss # Calculate the relative strength
    rsi = 100 - (100 / (1 + rs)) # Calcuate the RSI
    return rsi

def backtest(capin, df):
    shares = 0
    capital = capin

    for index, row in df.iterrows():
#        print(f'index: {index} row: {row["Combined_Sell_Signal"]}')

        if row['Combined_Buy_Signal']:
            add_shares = capital // row['Close'] #all-in strategy
            capital -= add_shares * row['Close'] #remaining capital
            shares += add_shares
        elif row['Combined_Sell_Signal']:
            capital += shares * row['Close']
            shares = 0
#        print(f'index: {index} buy: {row["Combined_Buy_Signal"]}, sell: {row["Combined_Sell_Signal"]}, cap: {capital}, shares: {shares}')
    
    final_capital = capital + shares * df.iloc[-1]['Close'] # calculate final capital
    return final_capital
for ticker in ["^GSPC", "^NDX", "^DJI", "^RUT"]:
    df_ticker = yf.download(ticker, start='2022-01-01', end='2025-01-01')
    df_ticker.columns = df_ticker.columns.droplevel(1)

#    print(df_ticker.head())
    #getting simple/expnontial moving average
    df_ticker = calculate_macd(df_ticker)
#    print(df_ticker.head())

    df_ticker['RSI'] = calculate_rsi(df_ticker)
    df_ticker['RSI_Signal'] = (df_ticker['RSI'] < 30) | (df_ticker['RSI'] > 70)
    df_ticker['Combined_Buy_Signal'] = df_ticker['Combined_Buy_Signal'] & df_ticker['RSI_Signal']
    df_ticker['Combined_Sell_Signal'] = df_ticker['Combined_Sell_Signal'] & df_ticker['RSI_Signal']

    df_ticker.to_csv(f'{ticker}.csv')
    initial_cap = 10000
    final_cap = backtest(initial_cap, df_ticker)

    print(f'ticker: {ticker},Initial capital: {initial_cap}\n Final capital: {final_cap}')


