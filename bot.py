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


# ======================================================
# WATCHLISTS
# ======================================================

CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "HBAR-USD",
    "AVAX-USD", "VET-USD", "ICP-USD", "ATOM-USD", "ALGO-USD", "XLM-USD"
]

STOCK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "PLTR", "SPY", "QQQ"
]

ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS


# ======================================================
# SAFE ENVIRONMENT HELPERS
# ======================================================

def get_env_bool(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in ["1", "true", "yes", "y", "on"]


def get_env_int(name, default):
    value = os.getenv(name)

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_env_float(name, default):
    value = os.getenv(name)

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ======================================================
# ENVIRONMENT VARIABLES
# ======================================================

CRYPTO_TRADE_WEBHOOK_URL = os.getenv(
    "CRYPTO_TRADE_WEBHOOK_URL",
    os.getenv("CRYPTO_WEBHOOK_URL", "")
)

STOCK_TRADE_WEBHOOK_URL = os.getenv(
    "STOCK_TRADE_WEBHOOK_URL",
    os.getenv("STOCK_WEBHOOK_URL", "")
)

CRYPTO_SUMMARY_WEBHOOK_URL = os.getenv(
    "CRYPTO_SUMMARY_WEBHOOK_URL",
    os.getenv("SUMMARY_WEBHOOK_URL", "")
)

STOCK_SUMMARY_WEBHOOK_URL = os.getenv(
    "STOCK_SUMMARY_WEBHOOK_URL",
    os.getenv("SUMMARY_WEBHOOK_URL", "")
)

SCAN_INTERVAL_MINUTES = max(1, get_env_int("BOT_SCAN_INTERVAL_MINUTES", 15))
MIN_CONFIDENCE = max(0, min(get_env_float("AUTO_SIGNAL_MIN_CONFIDENCE", 75), 100))

SEND_STARTUP_MESSAGE = get_env_bool("BOT_SEND_STARTUP_MESSAGE", True)
SEND_SUMMARIES = get_env_bool("BOT_SEND_SUMMARIES", True)
SUMMARY_INTERVAL_HOURS = max(1, get_env_float("BOT_SUMMARY_INTERVAL_HOURS", 6))

SIGNAL_LOG_FILE = "bot_sent_signal_log.txt"
SUMMARY_LOG_FILE = "bot_sent_summary_log.txt"


# ======================================================
# HELPER FUNCTIONS
# ======================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def log(message):
    print(message, flush=True)


def get_asset_type(ticker):
    return "Crypto" if ticker.endswith("-USD") else "Stock"


def get_trade_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_TRADE_WEBHOOK_URL


def get_summary_webhook(market):
    if market == "Crypto":
        return CRYPTO_SUMMARY_WEBHOOK_URL
    return STOCK_SUMMARY_WEBHOOK_URL


def load_log(file_path):
    try:
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
    except Exception as error:
        log(f"Could not load {file_path}: {error}")

    return set()


def save_log(file_path, items):
    try:
        with open(file_path, "w", encoding="utf-8") as file:
            for item in sorted(items):
                file.write(f"{item}\n")
    except Exception as error:
        log(f"Could not save {file_path}: {error}")


def send_discord_embed(webhook_url, title, color, fields, max_retries=2):
    if not webhook_url:
        log(f"Missing webhook for: {title}")
        return False

    clean_fields = []

    for field in fields:
        clean_fields.append({
            "name": str(field.get("name", " ")),
            "value": str(field.get("value", "N/A"))[:1024] or "N/A",
            "inline": bool(field.get("inline", False))
        })

    payload = {
        "embeds": [
            {
                "title": title[:256],
                "color": color,
                "fields": clean_fields[:25],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            log(f"Discord status: {response.status_code} {response.text[:200]}")

            if response.status_code in [200, 204]:
                return True

            if response.status_code == 429 and attempt < max_retries:
                try:
                    retry_after = float(response.json().get("retry_after", 1))
                except Exception:
                    retry_after = 1

                log(f"Rate limited. Waiting {retry_after} seconds...")
                time.sleep(retry_after + 0.5)
                continue

            return False

        except Exception as error:
            log(f"Discord error: {error}")

            if attempt < max_retries:
                time.sleep(1)
                continue

            return False

    return False


def signal_embed_color(signal):
    if "BUY" in signal:
        return 65280
    if "SELL" in signal:
        return 16711680
    return 16776960


def get_price_data(ticker, period="1y"):
    try:
        data = yf.Ticker(ticker).history(period=period, auto_adjust=False)

        if data is None or data.empty:
            log(f"{ticker}: no price data returned.")
            return pd.DataFrame()

        return data.dropna()

    except Exception as error:
        log(f"Price data error for {ticker}: {error}")
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


def calculate_signal_and_confidence(final_score):
    bullish_confidence = (final_score / 120) * 100
    bearish_confidence = ((120 - final_score) / 120) * 100

    if final_score >= 90:
        return "STRONG BUY", bullish_confidence

    if final_score >= 75:
        return "BUY", bullish_confidence

    if final_score <= 30:
        return "STRONG SELL", bearish_confidence

    if final_score < 50:
        return "SELL", bearish_confidence

    hold_confidence = 100 - abs(60 - final_score)
    hold_confidence = max(0, min(hold_confidence, 100))
    return "HOLD", hold_confidence


def score_ticker(ticker):
    data = get_price_data(ticker, "1y")

    if data.empty or len(data) < 50:
        return None

    data = calculate_indicators(data)
    latest = data.iloc[-1]

    current_price = float(latest["Close"])
    previous_price = float(data["Close"].iloc[-2])

    if previous_price == 0:
        price_change_percent = 0
    else:
        price_change_percent = ((current_price - previous_price) / previous_price) * 100

    rsi = latest.get("RSI")
    macd = latest.get("MACD")
    macd_signal = latest.get("MACD Signal")
    ma50 = latest.get("MA50")
    ma200 = latest.get("MA200")

    rsi_value = float(rsi) if pd.notna(rsi) else 0
    macd_value = float(macd) if pd.notna(macd) else 0
    macd_signal_value = float(macd_signal) if pd.notna(macd_signal) else 0

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
    ai_signal, confidence_percent = calculate_signal_and_confidence(final_score)
    confidence_percent = max(0, min(confidence_percent, 100))

    return {
        "Ticker": ticker,
        "Market": get_asset_type(ticker),
        "Price": round(current_price, 2),
        "Daily Change %": round(price_change_percent, 2),
        "RSI": round(rsi_value, 2),
        "MACD": round(macd_value, 2),
        "MACD Signal": round(macd_signal_value, 2),
        "Technical Score": technical_score,
        "Final Score": final_score,
        "AI Confidence %": round(confidence_percent, 2),
        "AI Signal": ai_signal
    }


# ======================================================
# DISCORD ALERTS
# ======================================================

def send_startup_message():
    if not SEND_STARTUP_MESSAGE:
        return

    fields = [
        {"name": "Status", "value": "Railway worker is online.", "inline": False},
        {"name": "Scan Interval", "value": f"{SCAN_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "Minimum Confidence", "value": f"{MIN_CONFIDENCE}%", "inline": True},
        {"name": "Summaries", "value": "On" if SEND_SUMMARIES else "Off", "inline": True},
        {"name": "Time", "value": now_text(), "inline": False},
    ]

    if CRYPTO_TRADE_WEBHOOK_URL:
        send_discord_embed(
            CRYPTO_TRADE_WEBHOOK_URL,
            "🤖 AI Trading Bot Started",
            3447003,
            fields
        )

    if STOCK_TRADE_WEBHOOK_URL and STOCK_TRADE_WEBHOOK_URL != CRYPTO_TRADE_WEBHOOK_URL:
        send_discord_embed(
            STOCK_TRADE_WEBHOOK_URL,
            "🤖 AI Trading Bot Started",
            3447003,
            fields
        )


def send_signal_alert(row):
    fields = [
        {"name": "Ticker", "value": str(row["Ticker"]), "inline": True},
        {"name": "Market", "value": str(row["Market"]), "inline": True},
        {"name": "Price", "value": f"${row['Price']}", "inline": True},
        {"name": "Daily Change", "value": f"{row['Daily Change %']}%", "inline": True},
        {"name": "Signal", "value": str(row["AI Signal"]), "inline": True},
        {"name": "Confidence", "value": f"{row['AI Confidence %']}%", "inline": True},
        {"name": "RSI", "value": str(row["RSI"]), "inline": True},
        {"name": "MACD", "value": str(row["MACD"]), "inline": True},
        {"name": "Final Score", "value": str(row["Final Score"]), "inline": True},
        {"name": "Time", "value": now_text(), "inline": False},
    ]

    return send_discord_embed(
        get_trade_webhook(row["Ticker"]),
        f"{row['Market']} Market | {row['AI Signal']}",
        signal_embed_color(row["AI Signal"]),
        fields
    )


def build_summary_fields(rows, market):
    market_rows = [row for row in rows if row["Market"] == market]

    if not market_rows:
        return []

    buy_lines = []
    hold_lines = []
    sell_lines = []

    for row in sorted(market_rows, key=lambda item: item["AI Confidence %"], reverse=True):
        line = f"{row['Ticker']} | {row['AI Confidence %']}% | RSI {row['RSI']}"

        if "BUY" in row["AI Signal"]:
            buy_lines.append(line)
        elif "SELL" in row["AI Signal"]:
            sell_lines.append(line)
        else:
            hold_lines.append(line)

    summary_text = (
        "🟢 BUY SIGNALS\n"
        f"{chr(10).join(buy_lines) if buy_lines else 'None'}\n\n"
        "🟡 HOLD SIGNALS\n"
        f"{chr(10).join(hold_lines) if hold_lines else 'None'}\n\n"
        "🔴 SELL SIGNALS\n"
        f"{chr(10).join(sell_lines) if sell_lines else 'None'}\n\n"
        "Time\n"
        f"{now_text()}"
    )

    return [{"name": " ", "value": summary_text, "inline": False}]


def send_market_summary(rows, market):
    fields = build_summary_fields(rows, market)

    if not fields:
        log(f"No {market} rows available for summary.")
        return False

    return send_discord_embed(
        get_summary_webhook(market),
        f"📊 {market} Market Summary",
        3447003,
        fields
    )


def get_summary_key(market):
    current_bucket = int(time.time() // (SUMMARY_INTERVAL_HOURS * 3600))
    return f"{market}_{current_bucket}"


def summary_already_sent(market):
    sent_summaries = load_log(SUMMARY_LOG_FILE)
    return get_summary_key(market) in sent_summaries


def mark_summary_sent(market):
    sent_summaries = load_log(SUMMARY_LOG_FILE)
    sent_summaries.add(get_summary_key(market))
    save_log(SUMMARY_LOG_FILE, sent_summaries)


def maybe_send_summary(rows, market):
    if not SEND_SUMMARIES:
        return False

    if summary_already_sent(market):
        return False

    sent = send_market_summary(rows, market)

    if sent:
        mark_summary_sent(market)

    return sent


# ======================================================
# SCAN LOOP
# ======================================================

def run_scan():
    log("=" * 50)
    log(f"Running bot scan: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    sent_signals = load_log(SIGNAL_LOG_FILE)
    today = datetime.now().strftime("%Y-%m-%d")

    scanned_rows = []
    scanned = 0
    candidates = 0
    sent_count = 0
    skipped_duplicates = 0

    for ticker in ALL_TICKERS:
        row = score_ticker(ticker)

        if row is None:
            continue

        scanned_rows.append(row)
        scanned += 1

        signal = row["AI Signal"]
        confidence = row["AI Confidence %"]

        log(f"{ticker} | {signal} | {confidence}%")

        if signal not in ["STRONG BUY", "BUY", "STRONG SELL", "SELL"]:
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
            save_log(SIGNAL_LOG_FILE, sent_signals)
            time.sleep(1)

    maybe_send_summary(scanned_rows, "Crypto")
    time.sleep(1)
    maybe_send_summary(scanned_rows, "Stock")

    log("Scan complete.")
    log(f"Scanned: {scanned}")
    log(f"Candidates: {candidates}")
    log(f"Sent: {sent_count}")
    log(f"Skipped duplicates: {skipped_duplicates}")


def main():
    log("AI Trading Bot started.")
    log(f"Scan interval: {SCAN_INTERVAL_MINUTES} minutes")
    log(f"Minimum confidence: {MIN_CONFIDENCE}%")
    log(f"Summaries enabled: {SEND_SUMMARIES}")
    log(f"Summary interval: {SUMMARY_INTERVAL_HOURS} hours")

    if not CRYPTO_TRADE_WEBHOOK_URL:
        log("WARNING: CRYPTO_TRADE_WEBHOOK_URL is missing.")

    if not STOCK_TRADE_WEBHOOK_URL:
        log("WARNING: STOCK_TRADE_WEBHOOK_URL is missing.")

    send_startup_message()

    while True:
        try:
            run_scan()
        except Exception as error:
            log(f"Unexpected scan error: {error}")

        log(f"Sleeping for {SCAN_INTERVAL_MINUTES} minutes...")
        time.sleep(SCAN_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
