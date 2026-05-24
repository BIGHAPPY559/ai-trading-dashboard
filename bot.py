import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf
from ta.momentum import RSIIndicator
from ta.trend import MACD

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "HBAR-USD",
    "AVAX-USD", "VET-USD", "ICP-USD", "ATOM-USD", "ALGO-USD", "XLM-USD"
]

STOCK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "PLTR", "SPY", "QQQ"
]

ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS

CRYPTO_TRADE_WEBHOOK_URL = os.getenv("CRYPTO_TRADE_WEBHOOK_URL", "")
STOCK_TRADE_WEBHOOK_URL = os.getenv("STOCK_TRADE_WEBHOOK_URL", "")

SCAN_INTERVAL_MINUTES = int(os.getenv("BOT_SCAN_INTERVAL_MINUTES", "15"))
MIN_CONFIDENCE = float(os.getenv("AUTO_SIGNAL_MIN_CONFIDENCE", "75"))

SIGNAL_LOG_FILE = "bot_sent_signal_log.txt"


def get_asset_type(ticker):
    return "Crypto" if ticker.endswith("-USD") else "Stock"


def get_trade_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_TRADE_WEBHOOK_URL


def load_sent_signals():
    if os.path.exists(SIGNAL_LOG_FILE):
        with open(SIGNAL_LOG_FILE, "r", encoding="utf-8") as file:
            return set(line.strip() for line in file if line.strip())
    return set()


def save_sent_signals(sent_signals):
    with open(SIGNAL_LOG_FILE, "w", encoding="utf-8") as file:
        for item in sorted(sent_signals):
            file.write(f"{item}\n")


def send_discord_embed(webhook_url, title, color, fields):
    if not webhook_url:
        print("Missing webhook.")
        return False

    payload = {
        "embeds": [
            {
                "title": title,
                "color": color,
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        print("Discord status:", response.status_code, response.text[:200])
        return response.status_code in [200, 204]
    except Exception as error:
        print("Discord error:", error)
        return False


def signal_embed_color(signal):
    if "BUY" in signal:
        return 65280
    if "SELL" in signal:
        return 16711680
    return 16776960


def get_price_data(ticker, period="6mo"):
    try:
        data = yf.Ticker(ticker).history(period=period)
        if data is None or data.empty:
            return pd.DataFrame()
        return data.dropna()
    except Exception as error:
        print(f"Price data error for {ticker}:", error)
        return pd.DataFrame()


def calculate_indicators(data):
    data = data.copy()

    if len(data) < 50:
        return data

    close = data["Close"]

    data["RSI"] = RSIIndicator(close=close, window=14).rsi()

    macd_indicator = MACD(close=close)
    data["MACD"] = macd_indicator.macd()
    data["MACD Signal"] = macd_indicator.macd_signal()

    data["MA50"] = close.rolling(window=50).mean()
    data["MA200"] = close.rolling(window=200).mean()

    return data


def score_ticker(ticker):
    data = get_price_data(ticker, "6mo")

    if data.empty or len(data) < 50:
        return None

    data = calculate_indicators(data)
    latest = data.iloc[-1]

    current_price = float(latest["Close"])
    previous_price = float(data["Close"].iloc[-2])
    price_change_percent = ((current_price - previous_price) / previous_price) * 100

    rsi = float(latest.get("RSI", 0))
    macd = float(latest.get("MACD", 0))
    macd_signal = float(latest.get("MACD Signal", 0))
    ma50 = latest.get("MA50", 0)
    ma200 = latest.get("MA200", 0)

    technical_score = 0

    if pd.notna(ma50) and current_price > ma50:
        technical_score += 30

    if pd.notna(ma50) and pd.notna(ma200) and ma50 > ma200:
        technical_score += 30

    if pd.notna(rsi) and rsi < 70:
        technical_score += 20

    if pd.notna(rsi) and rsi > 40:
        technical_score += 20

    if pd.notna(macd) and pd.notna(macd_signal) and macd > macd_signal:
        technical_score += 20

    final_score = technical_score
    confidence_percent = (final_score / 120) * 100
    confidence_percent = max(0, min(confidence_percent, 100))

    if final_score >= 90:
        ai_signal = "STRONG BUY"
    elif final_score >= 75:
        ai_signal = "BUY"
    elif final_score >= 50:
        ai_signal = "HOLD"
    else:
        ai_signal = "SELL"

    return {
        "Ticker": ticker,
        "Market": get_asset_type(ticker),
        "Price": round(current_price, 2),
        "Daily Change %": round(price_change_percent, 2),
        "RSI": round(rsi, 2),
        "MACD": round(macd, 2),
        "MACD Signal": round(macd_signal, 2),
        "Technical Score": technical_score,
        "Final Score": final_score,
        "AI Confidence %": round(confidence_percent, 2),
        "AI Signal": ai_signal
    }


def send_signal_alert(row):
    fields = [
        {"name": "Ticker", "value": str(row["Ticker"]), "inline": True},
        {"name": "Market", "value": str(row["Market"]), "inline": True},
        {"name": "Price", "value": f"${row['Price']}", "inline": True},
        {"name": "Signal", "value": str(row["AI Signal"]), "inline": True},
        {"name": "Confidence", "value": f"{row['AI Confidence %']}%", "inline": True},
        {"name": "RSI", "value": str(row["RSI"]), "inline": True},
        {"name": "MACD", "value": str(row["MACD"]), "inline": True},
        {"name": "Final Score", "value": str(row["Final Score"]), "inline": True},
        {"name": "Time", "value": datetime.now().strftime("%Y-%m-%d %H:%M"), "inline": False},
    ]

    return send_discord_embed(
        get_trade_webhook(row["Ticker"]),
        f"{row['Market']} Market | {row['AI Signal']}",
        signal_embed_color(row["AI Signal"]),
        fields
    )


def run_scan():
    print("=" * 50)
    print("Running bot scan:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    sent_signals = load_sent_signals()
    today = datetime.now().strftime("%Y-%m-%d")

    scanned = 0
    candidates = 0
    sent_count = 0
    skipped_duplicates = 0

    for ticker in ALL_TICKERS:
        row = score_ticker(ticker)

        if row is None:
            continue

        scanned += 1

        signal = row["AI Signal"]
        confidence = row["AI Confidence %"]

        print(f"{ticker} | {signal} | {confidence}%")

        if signal not in ["STRONG BUY", "BUY", "SELL"]:
            continue

        if confidence < MIN_CONFIDENCE:
            continue

        candidates += 1

        alert_key = f"{ticker}_{signal}_{today}"

        if alert_key in sent_signals:
            skipped_duplicates += 1
            continue

        sent = send_signal_alert(row)

        if sent:
            sent_count += 1
            sent_signals.add(alert_key)
            save_sent_signals(sent_signals)
            time.sleep(1)

    print("Scan complete.")
    print("Scanned:", scanned)
    print("Candidates:", candidates)
    print("Sent:", sent_count)
    print("Skipped duplicates:", skipped_duplicates)


def main():
    print("AI Trading Bot started.")
    print(f"Scan interval: {SCAN_INTERVAL_MINUTES} minutes")
    print(f"Minimum confidence: {MIN_CONFIDENCE}%")

    while True:
        run_scan()
        print(f"Sleeping for {SCAN_INTERVAL_MINUTES} minutes...")
        time.sleep(SCAN_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()