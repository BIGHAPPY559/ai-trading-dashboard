import yfinance as yf
from ta.momentum import RSIIndicator
import requests
import time
import os
from datetime import datetime

WEBHOOK_URL = os.getenv("WEBHOOK_URL")

SCAN_INTERVAL = 3600

BUY_RSI = 35
SELL_RSI = 70

ALERT_COOLDOWN = 21600

last_signals = {}
last_alert_times = {}

tickers = [
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


def send_discord_embed(title, color, fields):

    if not WEBHOOK_URL:
        print("WEBHOOK_URL not found.")
        return

    response = requests.post(
        WEBHOOK_URL,
        json={
            "embeds": [
                {
                    "title": title,
                    "color": color,
                    "fields": fields
                }
            ]
        }
    )

    print("Discord status:", response.status_code)


while True:

    summary = {
        "🟢 BUY SIGNALS": [],
        "🟡 HOLD SIGNALS": [],
        "🔴 SELL SIGNALS": []
    }

    print("Starting scan...")

    for ticker in tickers:

        try:

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

            rsi = RSIIndicator(
                close=data["Close"]
            ).rsi()

            current_rsi = rsi.iloc[-1]

            # MOVING AVERAGES

            ma50 = (
                data["Close"]
                .rolling(window=50)
                .mean()
                .iloc[-1]
            )

            ma200 = (
                data["Close"]
                .rolling(window=200)
                .mean()
                .iloc[-1]
            )

            # SIGNAL LOGIC

            if ma50 > ma200 and current_rsi < 30:

                decision = "STRONG BUY 🚀"

                reason = (
                    "Strong bullish trend "
                    "with heavily oversold RSI."
                )

            elif ma50 > ma200 and current_rsi < 45:

                decision = "BUY 🟢"

                reason = (
                    "Bullish trend with "
                    "healthy RSI pullback."
                )

            elif ma50 < ma200 and current_rsi > 75:

                decision = "STRONG SELL 🔴"

                reason = (
                    "Strong bearish trend "
                    "with extremely overheated RSI."
                )

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

            # SUMMARY CATEGORY

            summary_label = "🟡 HOLD SIGNALS"

            if "BUY" in decision:
                summary_label = "🟢 BUY SIGNALS"

            elif "SELL" in decision:
                summary_label = "🔴 SELL SIGNALS"

            summary[summary_label].append(
                f"{ticker} | "
                f"{score}% | "
                f"RSI {current_rsi:.2f}"
            )

            # PRINT INFO

            print("----------------------")
            print(f"Ticker: {ticker}")
            print(f"Decision: {decision}")
            print(f"Price: ${current_price:.2f}")
            print(f"RSI: {current_rsi:.2f}")
            print(f"50 MA: {ma50:.2f}")
            print(f"200 MA: {ma200:.2f}")
            print(f"Signal Strength: {score}%")

            # ALERT CONTROL

            previous_signal = last_signals.get(ticker)

            last_alert_time = (
                last_alert_times.get(ticker, 0)
            )

            current_time = time.time()

            can_send_alert = (
                current_time - last_alert_time
            ) >= ALERT_COOLDOWN

            # SEND INDIVIDUAL ALERTS

            if (
                (
                    "BUY" in decision
                    or "SELL" in decision
                )
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
                    title=f"{decision}",
                    color=embed_color,
                    fields=[
                        {
                            "name": "Ticker",
                            "value": ticker,
                            "inline": True
                        },
                        {
                            "name": "Price",
                            "value":
                            f"${current_price:,.2f}",
                            "inline": True
                        },
                        {
                            "name": "24h Change",
                            "value":
                            f"{price_change_percent:.2f}%",
                            "inline": True
                        },
                        {
                            "name": "RSI",
                            "value":
                            f"{current_rsi:.2f}",
                            "inline": True
                        },
                        {
                            "name": "50 MA",
                            "value":
                            f"{ma50:.2f}",
                            "inline": True
                        },
                        {
                            "name": "200 MA",
                            "value":
                            f"{ma200:.2f}",
                            "inline": True
                        },
                        {
                            "name": "Signal Strength",
                            "value":
                            f"{score}%",
                            "inline": True
                        },
                        {
                            "name": "Reason",
                            "value": reason,
                            "inline": False
                        },
                        {
                            "name": "Time",
                            "value":
                            datetime.now().strftime(
                                "%Y-%m-%d %H:%M"
                            ),
                            "inline": False
                        }
                    ]
                )

                last_signals[ticker] = decision

                last_alert_times[ticker] = current_time

            else:

                print(
                    f"No individual alert for "
                    f"{ticker}."
                )

        except Exception as e:

            print(
                f"Error scanning {ticker}: {e}"
            )

    # MARKET SUMMARY

    summary_fields = []

    for label, items in summary.items():

        if items:

            value = "\n".join(items)

        else:

            value = "None"

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
            "value":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            ),
            "inline": False
        }
    )

    send_discord_embed(
        title="📊 AI Market Summary",
        color=3447003,
        fields=summary_fields
    )

    print("Scan complete. Waiting...")

    time.sleep(SCAN_INTERVAL)