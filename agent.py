import yfinance as yf
from ta.momentum import RSIIndicator
import requests
import time

# DISCORD WEBHOOK
WEBHOOK_URL = "https://discordapp.com/api/webhooks/1501805105954029648/HvBch_wxbW7Sbtao846pmvnAuSsDq71Vx-AfpMgYIVibbhbTt_fvpHtbD7DxAJZXgg4i"

# SETTINGS
SCAN_INTERVAL = 3600
SELL_RSI = 70

# TICKERS
tickers = ["AAPL", "TSLA", "NVDA", "BTC-USD", "SPY", "ETH-USD"]

while True:

    for ticker in tickers:

        stock = yf.Ticker(ticker)

        data = stock.history(period="1y")

        current_price = data["Close"].iloc[-1]

        # RSI
        rsi = RSIIndicator(close=data["Close"]).rsi()
        current_rsi = rsi.iloc[-1]

        # MOVING AVERAGES
        ma50 = data["Close"].rolling(window=50).mean().iloc[-1]
        ma200 = data["Close"].rolling(window=200).mean().iloc[-1]

        # PRINT INFO
        print("----------------------")
        print(f"Ticker: {ticker}")
        print(f"Price: ${current_price:.2f}")
        print(f"RSI: {current_rsi:.2f}")
        print(f"50 MA: {ma50:.2f}")
        print(f"200 MA: {ma200:.2f}")

        # AI LOGIC
        if ma50 > ma200 and current_rsi < SELL_RSI:
            decision = "STRONG BUY 🚀"
            reason = "Golden cross trend with healthy RSI."

        elif current_rsi > SELL_RSI:
            decision = "SELL 🚨"
            reason = "RSI is overheated."

        else:
            decision = "HOLD ⏸️"
            reason = "Trend is unclear."

        # DISCORD MESSAGE
        message = f"""
Ticker: {ticker}

Price: ${current_price:.2f}
RSI: {current_rsi:.2f}

50 MA: {ma50:.2f}
200 MA: {ma200:.2f}

Decision: {decision}

Reason:
{reason}
"""

        print(message)

        # SEND TO DISCORD
        response = requests.post(
            WEBHOOK_URL,
            json={"content": message}
        )

        print("Discord status:", response.status_code)

    print("Scan complete. Waiting...")
    time.sleep(SCAN_INTERVAL)