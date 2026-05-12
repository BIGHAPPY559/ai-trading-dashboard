import yfinance as yf
from ta.momentum import RSIIndicator
import requests
import time
import os
from datetime import datetime

CRYPTO_ALERT_WEBHOOK_URL = os.getenv("CRYPTO_ALERT_WEBHOOK_URL")
CRYPTO_SUMMARY_WEBHOOK_URL = os.getenv("CRYPTO_SUMMARY_WEBHOOK_URL")

STOCK_ALERT_WEBHOOK_URL = os.getenv("STOCK_ALERT_WEBHOOK_URL")
STOCK_SUMMARY_WEBHOOK_URL = os.getenv("STOCK_SUMMARY_WEBHOOK_URL")

SCAN_INTERVAL = 900
ALERT_COOLDOWN = 21600
SUMMARY_INTERVAL = 21600

BUY_RSI = 45
STRONG_BUY_RSI = 30

SELL_RSI = 70
STRONG_SELL_RSI = 75

last_signals = {}
last_alert_times = {}

last_crypto_summary_time = 0
last_stock_summary_time = 0

crypto_tickers = [
    "ETH-USD",
    "BTC-USD",
    "AVAX-USD",
    "VET-USD",
    "XRP-USD",
    "ADA-USD",
    "HBAR-USD",
    "ICP-USD",
    "ATOM-USD",
    "ALGO-USD",
    "XTZ-USD",
    "ETC-USD",
    "XLM-USD"
]

stock_tickers = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "GOOGL",
    "META",
    "AMD",
    "PLTR",
    "SPY",
    "QQQ"
]


def send_discord_embed(webhook_url, title, color, fields):
    if not webhook_url:
        print(f"Webhook missing for {title}")
        return

    response = requests.post(
        webhook_url,
        json={
            "embeds": [
                {
                    "title": title,
                    "color": color,
                    "fields": fields
                }
            ]
        },
        timeout=10
    )

    print(f"{title} Discord status:", response.status_code)


def scan_market(market_name, tickers, alert_webhook, summary_webhook):
    summary = {
        "🟢 BUY SIGNALS": [],
        "🟡 HOLD SIGNALS": [],
        "🔴 SELL SIGNALS": []
    }

    print(f"Starting {market_name} scan...")

    for ticker in tickers:

        time.sleep(2)

        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period="1y")

            if data.empty:
                print(f"{ticker}: no data found, skipping")
                continue

            current_price = data["Close"].iloc[-1]
            previous_price = data["Close"].iloc[-2]

            price_change_percent = (
                (current_price - previous_price) / previous_price
            ) * 100

            rsi = RSIIndicator(close=data["Close"]).rsi()
            current_rsi = rsi.iloc[-1]

            ma50 = data["Close"].rolling(window=50).mean().iloc[-1]
            ma200 = data["Close"].rolling(window=200).mean().iloc[-1]

            if ma50 > ma200 and current_rsi < STRONG_BUY_RSI:
                decision = "STRONG BUY 🚀"
                reason = "Strong bullish trend with heavily oversold RSI."

            elif ma50 > ma200 and current_rsi < BUY_RSI:
                decision = "BUY 🟢"
                reason = "Bullish trend with healthy RSI pullback."

            elif ma50 < ma200 and current_rsi > STRONG_SELL_RSI:
                decision = "STRONG SELL 🔴"
                reason = "Strong bearish trend with extremely overheated RSI."

            elif current_rsi > SELL_RSI:
                decision = "SELL 🚨"
                reason = "RSI is overheated."

            else:
                decision = "HOLD ⏸️"
                reason = "Trend is unclear."

            score = 0

            if ma50 > ma200:
                score += 50

            if current_rsi < SELL_RSI:
                score += 30

            if current_rsi > 40:
                score += 20

            summary_label = "🟡 HOLD SIGNALS"

            if "BUY" in decision:
                summary_label = "🟢 BUY SIGNALS"

            elif "SELL" in decision:
                summary_label = "🔴 SELL SIGNALS"

            summary[summary_label].append(
                f"{ticker} | {score}% | RSI {current_rsi:.2f}"
            )

            print("----------------------")
            print(f"Market: {market_name}")
            print(f"Ticker: {ticker}")
            print(f"Decision: {decision}")
            print(f"Price: ${current_price:.2f}")
            print(f"RSI: {current_rsi:.2f}")
            print(f"50 MA: {ma50:.2f}")
            print(f"200 MA: {ma200:.2f}")
            print(f"Signal Strength: {score}%")

            signal_key = f"{market_name}_{ticker}"

            previous_signal = last_signals.get(signal_key)
            last_alert_time = last_alert_times.get(signal_key, 0)
            current_time = time.time()

            can_send_alert = (
                current_time - last_alert_time
            ) >= ALERT_COOLDOWN

            if (
                ("BUY" in decision or "SELL" in decision)
                and "HOLD" not in decision
                and previous_signal != decision
                and can_send_alert
            ):
                embed_color = 16776960

                if "BUY" in decision:
                    embed_color = 65280

                elif "SELL" in decision:
                    embed_color = 16711680

                send_discord_embed(
                    alert_webhook,
                    f"{market_name} | {decision}",
                    embed_color,
                    [
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
                )

                last_signals[signal_key] = decision
                last_alert_times[signal_key] = current_time

            else:
                print(f"No individual alert for {ticker}.")

        except Exception as e:
            print(f"Error scanning {ticker}: {e}")

    summary_fields = []

    for label, items in summary.items():
        value = "\n".join(items) if items else "None"

        summary_fields.append(
            {
                "name": label,
                "value": value,
                "inline": False
            }
        )

    summary_fields.append(
        {
            "name": "Time",
            "value": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "inline": False
        }
    )

    if summary_webhook:

        send_discord_embed(
            summary_webhook,
            f"📊 {market_name} Summary",
            3447003,
            summary_fields
        )


while True:

    current_time = time.time()

    # CRYPTO MARKET

    if (
        current_time - last_crypto_summary_time
    ) >= SUMMARY_INTERVAL:

        scan_market(
            "Crypto Market",
            crypto_tickers,
            CRYPTO_ALERT_WEBHOOK_URL,
            CRYPTO_SUMMARY_WEBHOOK_URL
        )

        last_crypto_summary_time = current_time

    else:

        scan_market(
            "Crypto Market",
            crypto_tickers,
            CRYPTO_ALERT_WEBHOOK_URL,
            None
        )

    time.sleep(10)

    # STOCK MARKET

    if (
        current_time - last_stock_summary_time
    ) >= SUMMARY_INTERVAL:

        scan_market(
            "Stock Market",
            stock_tickers,
            STOCK_ALERT_WEBHOOK_URL,
            STOCK_SUMMARY_WEBHOOK_URL
        )

        last_stock_summary_time = current_time

    else:

        scan_market(
            "Stock Market",
            stock_tickers,
            STOCK_ALERT_WEBHOOK_URL,
            None
        )

    print("Waiting for next scan...")

    time.sleep(SCAN_INTERVAL)