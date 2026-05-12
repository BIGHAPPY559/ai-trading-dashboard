import yfinance as yf
from ta.momentum import RSIIndicator
import requests
import time
import os

from datetime import datetime

# DISCORD WEBHOOK

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# SETTINGS
SCAN_INTERVAL = 3600

BUY_RSI = 35

SELL_RSI = 70

last_signals = {}

last_alert_times = {}

ALERT_COOLDOWN = 21600

tickers = [
    # MAIN FOCUS
    "ETH-USD",
    "BTC-USD",
    "AVAX-USD",
    "VET-USD",
    "XRP-USD",

    # MONITOR
    "ADA-USD",
    "HBAR-USD",
    "ICP-USD",
    "ATOM-USD",
    "ALGO-USD",
    "XTZ-USD",
    "ETC-USD",
    "XLM-USD"
]

while True:

    for ticker in tickers:

        stock = yf.Ticker(ticker)

        data = stock.history(period="1y")

        if data.empty:
            print(f"{ticker}: no data found, skipping")
            continue

        current_price = data["Close"].iloc[-1]

        previous_price = data["Close"].iloc[-2]

        price_change_percent = (
            (current_price - previous_price)
            / previous_price
        ) * 100

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

        if ma50 > ma200 and current_rsi < BUY_RSI:
            decision = "STRONG BUY 🚀"
            reason = "Bullish trend with oversold RSI."

        elif current_rsi > SELL_RSI:
            decision = "SELL 🚨"
            reason = "RSI is overheated."

        else:
            decision = "HOLD ⏸️"
            reason = "Trend is unclear."

        # SIGNAL STRENGTH SCORE

        score = 0

        if ma50 > ma200:
            score += 50

        if current_rsi < SELL_RSI:
            score += 30

        if current_rsi > 40:
            score += 20

        # DISCORD MESSAGE

        message = (
            f"🚨 {decision}\n"
            f"Ticker: {ticker}\n"
            f"Price: ${current_price:,.2f}\n"
            f"RSI: {current_rsi:.2f}\n"
            f"50 MA: {ma50:.2f}\n"
            f"200 MA: {ma200:.2f}\n"
            f"Signal Strength: {score}%\n"
            f"Reason: {reason}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )

        print(message)

        # SEND TO DISCORD ONLY FOR IMPORTANT SIGNALS

        previous_signal = last_signals.get(ticker)

        last_alert_time = last_alert_times.get(ticker, 0)

        current_time = time.time()

        can_send_alert = (
            current_time - last_alert_time
        ) >= ALERT_COOLDOWN

        if (
            decision != "HOLD ⏸️"
            and previous_signal != decision
            and can_send_alert
        ):

            embed_color = 65280

            if "SELL" in decision:
                embed_color = 16711680

            response = requests.post(
                WEBHOOK_URL,
                json={
                    "embeds": [
                        {
                            "title": f"{decision}",
                            "color": embed_color,
                            "fields": [
                                {
                                    "name": "Ticker",
                                    "value": ticker,
                                    "inline": True
                                },
                                {
                                    "name": "Price",
                                    "value": f"${current_price:,.2f}",
                                    "inline": True
                                },
                                {
                                    "name": "24h Change",
                                    "value": f"{price_change_percent:.2f}%",
                                    "inline": True
                                },
                                {
                                    "name": "RSI",
                                    "value": f"{current_rsi:.2f}",
                                    "inline": True
                                },
                                {
                                    "name": "50 MA",
                                    "value": f"{ma50:.2f}",
                                    "inline": True
                                },
                                {
                                    "name": "200 MA",
                                    "value": f"{ma200:.2f}",
                                    "inline": True
                                },
                                {
                                    "name": "Signal Strength",
                                    "value": f"{score}%",
                                    "inline": True
                                },
                                {
                                    "name": "Reason",
                                    "value": reason,
                                    "inline": False
                                },
                                {
                                    "name": "Time",
                                    "value": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "inline": False
                                }
                            ]
                        }
                    ]
                }
            )

            print("Discord status:", response.status_code)

            last_signals[ticker] = decision

            last_alert_times[ticker] = current_time

        else:

            print(f"No new alert for {ticker}. Signal unchanged or HOLD.")

    print("Scan complete. Waiting...")
    time.sleep(SCAN_INTERVAL)