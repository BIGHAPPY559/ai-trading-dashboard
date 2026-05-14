import os
import random
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh
from ta.momentum import RSIIndicator
from ta.trend import MACD

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ======================================================
# PAGE SETUP
# ======================================================

st.set_page_config(page_title="AI Trading Dashboard", layout="wide")
st.title("AI Trading Dashboard")

st_autorefresh(interval=60000, key="market_refresh")

# ======================================================
# FILE PATHS
# ======================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PORTFOLIO_FILE = os.path.join(BASE_DIR, "portfolio.csv")
TRADE_HISTORY_FILE = os.path.join(BASE_DIR, "trade_history.csv")
BALANCE_FILE = os.path.join(BASE_DIR, "balance.txt")
EQUITY_FILE = os.path.join(BASE_DIR, "equity_history.csv")
NEWS_LOG_FILE = os.path.join(BASE_DIR, "sent_news_log.txt")
SUMMARY_LOG_FILE = os.path.join(BASE_DIR, "sent_summary_log.txt")
SIGNAL_LOG_FILE = os.path.join(BASE_DIR, "sent_signal_log.txt")
NEWS_SCHEDULE_FILE = os.path.join(BASE_DIR, "news_schedule_log.txt")
SIGNAL_SCHEDULE_FILE = os.path.join(BASE_DIR, "signal_schedule_log.txt")

# ======================================================
# SETTINGS
# ======================================================

APP_VERSION = "v17_old_style_summary_embed_only"

STARTING_BALANCE = 10000
STOP_LOSS_PERCENT = 5
TAKE_PROFIT_PERCENT = 10

# Daily summaries auto-send once per day after this server-local time.
# Railway usually runs in UTC unless you set a timezone.
AUTO_DAILY_SUMMARY_HOUR = int(os.getenv("AUTO_DAILY_SUMMARY_HOUR", "7"))
AUTO_DAILY_SUMMARY_MINUTE = int(os.getenv("AUTO_DAILY_SUMMARY_MINUTE", "0"))

# Automatic scanner signal alerts.
# Sends one alert per ticker/signal per day to avoid spam.
AUTO_SIGNAL_ALERTS_ENABLED = os.getenv("AUTO_SIGNAL_ALERTS_ENABLED", "true").lower() == "true"
AUTO_SIGNAL_MIN_CONFIDENCE = float(os.getenv("AUTO_SIGNAL_MIN_CONFIDENCE", "75"))
AUTO_SIGNAL_CHECK_INTERVAL_MINUTES = int(os.getenv("AUTO_SIGNAL_CHECK_INTERVAL_MINUTES", "15"))

# Automatic market news newsletter.
# This does not send on app open. It schedules the next run in the future,
# then sends a small digest only when the app is running and the time is reached.
AUTO_NEWS_ALERTS_ENABLED = os.getenv("AUTO_NEWS_ALERTS_ENABLED", "true").lower() == "true"
AUTO_NEWS_MIN_INTERVAL_MINUTES = int(os.getenv("AUTO_NEWS_MIN_INTERVAL_MINUTES", "180"))
AUTO_NEWS_MAX_INTERVAL_MINUTES = int(os.getenv("AUTO_NEWS_MAX_INTERVAL_MINUTES", "360"))
AUTO_NEWS_MAX_ARTICLES_PER_MARKET = int(os.getenv("AUTO_NEWS_MAX_ARTICLES_PER_MARKET", "5"))


# Add or remove tickers here
CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "HBAR-USD",
    "AVAX-USD", "VET-USD", "ICP-USD", "ATOM-USD", "ALGO-USD", "XLM-USD"
]

STOCK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "PLTR", "SPY", "QQQ"
]

ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS

# Discord webhook URLs should be added to your environment variables.
# Recommended Discord channels:
# CRYPTO_TRADE_WEBHOOK_URL = crypto buy/sell and AI signal alerts
# STOCK_TRADE_WEBHOOK_URL = stock buy/sell and AI signal alerts
# CRYPTO_NEWS_WEBHOOK_URL = crypto news alerts
# STOCK_NEWS_WEBHOOK_URL = stock news alerts
# CRYPTO_SUMMARY_WEBHOOK_URL = crypto daily AI market summary
# STOCK_SUMMARY_WEBHOOK_URL = stock daily AI market summary
# SUMMARY_WEBHOOK_URL = optional fallback daily AI market summary
CRYPTO_TRADE_WEBHOOK_URL = os.getenv(
    "CRYPTO_TRADE_WEBHOOK_URL",
    os.getenv("CRYPTO_WEBHOOK_URL", "")
)
STOCK_TRADE_WEBHOOK_URL = os.getenv(
    "STOCK_TRADE_WEBHOOK_URL",
    os.getenv("STOCK_WEBHOOK_URL", "")
)
CRYPTO_NEWS_WEBHOOK_URL = os.getenv("CRYPTO_NEWS_WEBHOOK_URL", "")
STOCK_NEWS_WEBHOOK_URL = os.getenv("STOCK_NEWS_WEBHOOK_URL", "")
CRYPTO_SUMMARY_WEBHOOK_URL = os.getenv("CRYPTO_SUMMARY_WEBHOOK_URL", "")
STOCK_SUMMARY_WEBHOOK_URL = os.getenv("STOCK_SUMMARY_WEBHOOK_URL", "")
SUMMARY_WEBHOOK_URL = os.getenv("SUMMARY_WEBHOOK_URL", "")

# Optional fallback for older setups.
# TRADE_WEBHOOK_URL keeps older trade alerts working.
# NEWS_WEBHOOK_URL keeps older news alerts working.
TRADE_WEBHOOK_URL = os.getenv("TRADE_WEBHOOK_URL", "")
NEWS_WEBHOOK_URL = os.getenv("NEWS_WEBHOOK_URL", "")

BULLISH_WORDS = [
    "beat", "growth", "upgrade", "surge", "profit", "strong", "bullish",
    "record", "rally", "partnership", "approval", "launch"
]

BEARISH_WORDS = [
    "miss", "downgrade", "fall", "drop", "loss", "weak", "bearish",
    "lawsuit", "investigation", "recall", "cut", "decline"
]

# ======================================================
# DATA FUNCTIONS
# ======================================================

@st.cache_data(ttl=60)
def get_price_data(ticker, period="6mo"):
    try:
        data = yf.Ticker(ticker).history(period=period)
        if data is None or data.empty:
            return pd.DataFrame()
        return data.dropna()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_news(ticker):
    try:
        news = yf.Ticker(ticker).news
        if news is None:
            return []
        return news
    except Exception:
        return []


def send_discord_alert(webhook_url, message, max_retries=2):
    if not webhook_url:
        print("Discord webhook missing.")
        return False

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(webhook_url, json={"content": message}, timeout=10)
            print("Discord status:", response.status_code, response.text[:200])

            if response.status_code in [200, 204]:
                return True

            if response.status_code == 429 and attempt < max_retries:
                try:
                    retry_after = response.json().get("retry_after", 1)
                except Exception:
                    retry_after = 1

                time.sleep(float(retry_after) + 0.5)
                continue

            return False

        except Exception as error:
            print("Discord send error:", error)
            if attempt < max_retries:
                time.sleep(1)
                continue
            return False

    return False




def send_discord_embed(webhook_url, title, color, fields, max_retries=2):
    if not webhook_url:
        print("Discord webhook missing.")
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

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            print("Discord embed status:", response.status_code, response.text[:200])

            if response.status_code in [200, 204]:
                return True

            if response.status_code == 429 and attempt < max_retries:
                try:
                    retry_after = response.json().get("retry_after", 1)
                except Exception:
                    retry_after = 1

                time.sleep(float(retry_after) + 0.5)
                continue

            return False

        except Exception as error:
            print("Discord embed send error:", error)
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


def send_signal_embed(row):
    ticker = row["Ticker"]
    market = row["Market"]
    signal = row["AI Signal"]

    fields = [
        {"name": "Ticker", "value": str(ticker), "inline": True},
        {"name": "Price", "value": f"${row['Price']}", "inline": True},
        {"name": "Signal", "value": str(signal), "inline": True},
        {"name": "Confidence", "value": f"{row['AI Confidence %']}%", "inline": True},
        {"name": "RSI", "value": str(row["RSI"]), "inline": True},
        {"name": "MACD", "value": str(row["MACD"]), "inline": True},
        {"name": "Final Score", "value": str(row["Final Score"]), "inline": True},
        {"name": "Time", "value": datetime.now().strftime("%Y-%m-%d %H:%M"), "inline": False},
    ]

    return send_discord_embed(
        get_trade_webhook(ticker),
        f"{market} Market | {signal}",
        signal_embed_color(signal),
        fields
    )

def load_balance():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, "r") as file:
                return float(file.read())
        except Exception:
            return STARTING_BALANCE
    return STARTING_BALANCE


def save_balance(balance):
    with open(BALANCE_FILE, "w") as file:
        file.write(str(balance))


def load_csv_records(file_path):
    try:
        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            return pd.read_csv(file_path).to_dict("records")
    except Exception:
        pass
    return []


def save_records(file_path, records):
    pd.DataFrame(records).to_csv(file_path, index=False)


def load_equity_history():
    try:
        if os.path.exists(EQUITY_FILE) and os.path.getsize(EQUITY_FILE) > 0:
            equity_df = pd.read_csv(EQUITY_FILE)
            if "Equity" in equity_df.columns:
                return equity_df["Equity"].dropna().tolist()
    except Exception:
        pass
    return []


def save_equity_history(equity_history):
    pd.DataFrame({"Equity": equity_history}).to_csv(EQUITY_FILE, index=False)


def load_sent_news():
    try:
        if os.path.exists(NEWS_LOG_FILE) and os.path.getsize(NEWS_LOG_FILE) > 0:
            with open(NEWS_LOG_FILE, "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
    except Exception as error:
        print("News log load error:", error)

    return set()


def save_sent_news(news_set):
    try:
        with open(NEWS_LOG_FILE, "w", encoding="utf-8") as file:
            file.write("\n".join(sorted(news_set)))
    except Exception as error:
        print("News log save error:", error)


def clear_sent_news_log():
    try:
        if os.path.exists(NEWS_LOG_FILE):
            os.remove(NEWS_LOG_FILE)
    except Exception as error:
        print("News log clear error:", error)


def load_sent_summaries():
    try:
        if os.path.exists(SUMMARY_LOG_FILE) and os.path.getsize(SUMMARY_LOG_FILE) > 0:
            with open(SUMMARY_LOG_FILE, "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
    except Exception as error:
        print("Summary log load error:", error)

    return set()


def save_sent_summaries(summary_set):
    try:
        with open(SUMMARY_LOG_FILE, "w", encoding="utf-8") as file:
            file.write("\n".join(sorted(summary_set)))
    except Exception as error:
        print("Summary log save error:", error)


def clear_sent_summary_log():
    try:
        if os.path.exists(SUMMARY_LOG_FILE):
            os.remove(SUMMARY_LOG_FILE)
    except Exception as error:
        print("Summary log clear error:", error)


def load_sent_signals():
    try:
        if os.path.exists(SIGNAL_LOG_FILE) and os.path.getsize(SIGNAL_LOG_FILE) > 0:
            with open(SIGNAL_LOG_FILE, "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
    except Exception as error:
        print("Signal log load error:", error)

    return set()


def save_sent_signals(signal_set):
    try:
        cleaned_signals = sorted(list(signal_set))

        with open(SIGNAL_LOG_FILE, "w", encoding="utf-8") as file:
            for signal in cleaned_signals:
                file.write(f"{signal}\n")

    except Exception as error:
        print("Signal log save error:", error)


def clear_sent_signal_log():
    try:
        if os.path.exists(SIGNAL_LOG_FILE):
            os.remove(SIGNAL_LOG_FILE)
    except Exception as error:
        print("Signal log clear error:", error)


def load_next_signal_time():
    try:
        if os.path.exists(SIGNAL_SCHEDULE_FILE) and os.path.getsize(SIGNAL_SCHEDULE_FILE) > 0:
            with open(SIGNAL_SCHEDULE_FILE, "r", encoding="utf-8") as file:
                return float(file.read().strip())
    except Exception as error:
        print("Signal schedule load error:", error)

    return 0


def save_next_signal_time(next_time):
    try:
        with open(SIGNAL_SCHEDULE_FILE, "w", encoding="utf-8") as file:
            file.write(str(next_time))
    except Exception as error:
        print("Signal schedule save error:", error)


def schedule_next_signal_time():
    interval_minutes = max(1, AUTO_SIGNAL_CHECK_INTERVAL_MINUTES)
    next_time = time.time() + (interval_minutes * 60)
    save_next_signal_time(next_time)
    return next_time


def load_next_news_time():
    try:
        if os.path.exists(NEWS_SCHEDULE_FILE) and os.path.getsize(NEWS_SCHEDULE_FILE) > 0:
            with open(NEWS_SCHEDULE_FILE, "r", encoding="utf-8") as file:
                return float(file.read().strip())
    except Exception as error:
        print("News schedule load error:", error)

    return 0


def save_next_news_time(next_time):
    try:
        with open(NEWS_SCHEDULE_FILE, "w", encoding="utf-8") as file:
            file.write(str(next_time))
    except Exception as error:
        print("News schedule save error:", error)


def schedule_next_news_time():
    min_minutes = max(1, AUTO_NEWS_MIN_INTERVAL_MINUTES)
    max_minutes = max(min_minutes, AUTO_NEWS_MAX_INTERVAL_MINUTES)
    delay_minutes = random.randint(min_minutes, max_minutes)
    next_time = time.time() + (delay_minutes * 60)
    save_next_news_time(next_time)
    return next_time


def build_news_digest(tickers, market_name, max_articles):
    digest_items = []
    today = datetime.now().strftime("%Y-%m-%d")

    for ticker in tickers:
        news_items = get_news(ticker)

        if not news_items:
            continue

        for article in news_items[:3]:
            headline = get_article_title(article)
            article_url = get_article_url(article)
            publisher = get_article_publisher(article)

            if not headline:
                continue

            news_key = f"{ticker}_{headline}_{today}"

            if news_key in st.session_state.sent_news:
                continue

            digest_items.append({
                "key": news_key,
                "ticker": ticker,
                "headline": headline,
                "url": article_url,
                "publisher": publisher
            })

            if len(digest_items) >= max_articles:
                return digest_items

    return digest_items


def send_news_digest(tickers, market_name, max_articles=5):
    webhook_url = CRYPTO_NEWS_WEBHOOK_URL if market_name == "Crypto" else STOCK_NEWS_WEBHOOK_URL

    if not webhook_url:
        print(f"Missing {market_name} news webhook.")
        return 0

    digest_items = build_news_digest(tickers, market_name, max_articles)

    if not digest_items:
        print(f"No new {market_name} news articles to send.")
        return 0

    message = f"📰 {market_name.upper()} MARKET NEWS DIGEST\n"
    message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    for number, item in enumerate(digest_items, start=1):
        line = (
            f"{number}. {item['ticker']} | {item['headline']}\n"
            f"Source: {item['publisher']}"
        )

        if item["url"]:
            line += f"\n{item['url']}"

        line += "\n\n"

        if len(message) + len(line) > 1900:
            break

        message += line

    sent = send_discord_alert(webhook_url, message)

    if sent:
        for item in digest_items:
            st.session_state.sent_news.add(item["key"])
        save_sent_news(st.session_state.sent_news)
        return len(digest_items)

    return 0


def send_auto_newsletter_if_due():
    if not AUTO_NEWS_ALERTS_ENABLED:
        return 0

    current_time = time.time()

    if current_time < st.session_state.next_auto_news_time:
        return 0

    total_sent = 0
    total_sent += send_news_digest(
        CRYPTO_TICKERS,
        "Crypto",
        max_articles=AUTO_NEWS_MAX_ARTICLES_PER_MARKET
    )

    time.sleep(1)

    total_sent += send_news_digest(
        STOCK_TICKERS,
        "Stock",
        max_articles=AUTO_NEWS_MAX_ARTICLES_PER_MARKET
    )

    st.session_state.next_auto_news_time = schedule_next_news_time()
    return total_sent


def should_send_daily_summary(market):
    now = datetime.now()
    scheduled_time_reached = (
        now.hour > AUTO_DAILY_SUMMARY_HOUR
        or (now.hour == AUTO_DAILY_SUMMARY_HOUR and now.minute >= AUTO_DAILY_SUMMARY_MINUTE)
    )

    if not scheduled_time_reached:
        return False

    summary_key = f"{market}_{now.strftime('%Y-%m-%d')}"
    return summary_key not in st.session_state.sent_summaries


def mark_daily_summary_sent(market):
    summary_key = f"{market}_{datetime.now().strftime('%Y-%m-%d')}"
    st.session_state.sent_summaries.add(summary_key)
    save_sent_summaries(st.session_state.sent_summaries)


def send_scheduled_daily_summary(watchlist_df, market):
    sent = send_market_summary_embed(watchlist_df, market)

    if sent:
        mark_daily_summary_sent(market)
        print(f"Scheduled {market} summary sent.")
        return True

    print(f"Scheduled {market} summary failed or webhook missing.")
    return False


def send_auto_signal_alerts(watchlist_df):
    if not AUTO_SIGNAL_ALERTS_ENABLED or watchlist_df.empty:
        return 0

    current_time = time.time()

    if current_time < st.session_state.next_auto_signal_time:
        return 0

    alert_signals = ["STRONG BUY", "BUY", "SELL"]
    candidates = watchlist_df[
        watchlist_df["AI Signal"].isin(alert_signals)
        & (watchlist_df["AI Confidence %"] >= AUTO_SIGNAL_MIN_CONFIDENCE)
    ].copy()

    if candidates.empty:
        st.session_state.next_auto_signal_time = schedule_next_signal_time()
        return 0

    sent_count = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for _, row in candidates.iterrows():
        ticker = row["Ticker"]
        signal = row["AI Signal"]
        alert_key = f"{ticker}_{signal}_{today}"

        if alert_key in st.session_state.sent_signal_alerts:
            print("Skipping duplicate signal:", alert_key)
            continue

        sent = send_signal_embed(row)

        if sent:
            sent_count += 1
            st.session_state.sent_signal_alerts.add(alert_key)
            save_sent_signals(st.session_state.sent_signal_alerts)
            time.sleep(0.5)

    st.session_state.next_auto_signal_time = schedule_next_signal_time()
    return sent_count


def get_asset_type(ticker):
    return "Crypto" if ticker.endswith("-USD") else "Stock"


def get_trade_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_TRADE_WEBHOOK_URL or TRADE_WEBHOOK_URL
    return STOCK_TRADE_WEBHOOK_URL or TRADE_WEBHOOK_URL


def get_news_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_NEWS_WEBHOOK_URL or NEWS_WEBHOOK_URL
    return STOCK_NEWS_WEBHOOK_URL or NEWS_WEBHOOK_URL


def get_summary_webhook(market):
    if market == "Crypto":
        return CRYPTO_SUMMARY_WEBHOOK_URL or SUMMARY_WEBHOOK_URL
    if market == "Stock":
        return STOCK_SUMMARY_WEBHOOK_URL or SUMMARY_WEBHOOK_URL
    return SUMMARY_WEBHOOK_URL


def format_summary_section(market_df, signals):
    section_df = market_df[market_df["AI Signal"].isin(signals)].copy()

    if section_df.empty:
        return "None"

    lines = []

    for _, row in section_df.iterrows():
        confidence = int(round(float(row["AI Confidence %"])))
        rsi = row.get("RSI", "N/A")
        lines.append(f"{row['Ticker']} | {confidence}% | RSI {rsi}")

    section_text = "\n".join(lines)

    if len(section_text) > 1000:
        section_text = section_text[:997] + "..."

    return section_text


def build_market_summary_fields(watchlist_df, market):
    market_df = watchlist_df[watchlist_df["Market"] == market].copy()

    if market_df.empty:
        return []

    buy_section = format_summary_section(market_df, ["STRONG BUY", "BUY"])
    hold_section = format_summary_section(market_df, ["HOLD"])
    sell_section = format_summary_section(market_df, ["SELL", "STRONG SELL"])

    return [
        {"name": "🟢 BUY SIGNALS", "value": buy_section, "inline": False},
        {"name": "🟡 HOLD SIGNALS", "value": hold_section, "inline": False},
        {"name": "🔴 SELL SIGNALS", "value": sell_section, "inline": False},
        {"name": "Time", "value": datetime.now().strftime("%Y-%m-%d %H:%M"), "inline": False},
    ]


def send_market_summary_embed(watchlist_df, market):
    fields = build_market_summary_fields(watchlist_df, market)

    if not fields:
        print(f"No {market} summary available.")
        return False

    return send_discord_embed(
        get_summary_webhook(market),
        f"📊 {market} Market Summary",
        3447003,
        fields
    )


def build_market_summary(watchlist_df, market):
    # Fallback text version only. Discord daily summaries use send_market_summary_embed()
    # so they keep the old card-style format.
    market_df = watchlist_df[watchlist_df["Market"] == market].copy()

    if market_df.empty:
        return ""

    return (
        f"📊 {market} Market Summary\n\n"
        f"🟢 BUY SIGNALS\n{format_summary_section(market_df, ['STRONG BUY', 'BUY'])}\n\n"
        f"🟡 HOLD SIGNALS\n{format_summary_section(market_df, ['HOLD'])}\n\n"
        f"🔴 SELL SIGNALS\n{format_summary_section(market_df, ['SELL', 'STRONG SELL'])}\n\n"
        f"Time\n{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

def get_article_url(article):
    content = article.get("content", {})
    canonical_url = content.get("canonicalUrl", {})

    if isinstance(canonical_url, dict):
        return canonical_url.get("url", "")

    if isinstance(article.get("link"), str):
        return article.get("link", "")

    return ""


def get_article_title(article):
    content = article.get("content", {})

    if isinstance(content, dict):
        title = content.get("title", "")
        if title:
            return title

    title = article.get("title", "")
    if title:
        return title

    return ""


def get_article_publisher(article):
    content = article.get("content", {})
    provider = content.get("provider", {}) if isinstance(content, dict) else {}

    if isinstance(provider, dict):
        display_name = provider.get("displayName", "")
        if display_name:
            return display_name

    publisher = article.get("publisher", "")
    if publisher:
        return publisher

    return "Yahoo Finance"


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

    news_score = get_news_score(ticker)
    final_score = technical_score + news_score

    confidence_percent = (final_score / 120) * 100
    confidence_percent = max(0, min(confidence_percent, 100))

    if confidence_percent >= 80:
        confidence_level = "HIGH"
    elif confidence_percent >= 60:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

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
        "News Score": news_score,
        "Final Score": final_score,
        "AI Confidence %": round(confidence_percent, 2),
        "Confidence Level": confidence_level,
        "AI Signal": ai_signal
    }


def get_news_score(ticker):
    news_score = 0

    try:
        news_items = get_news(ticker)[:3]

        for article in news_items:
            headline = get_article_title(article)
            title = headline.lower()
            article_url = get_article_url(article)
            publisher = get_article_publisher(article)

            for word in BULLISH_WORDS:
                if word in title:
                    news_score += 5

            for word in BEARISH_WORDS:
                if word in title:
                    news_score -= 5

            # IMPORTANT:
            # Do not send Discord news alerts from get_news_score().
            # This function runs every time the dashboard/watchlist refreshes,
            # which can spam Discord when the dashboard is opened.
            # News alerts should be sent only through the Manual News Scan buttons
            # or through a separate scheduled news job later.

    except Exception:
        news_score = 0

    return news_score




def send_latest_news_for_tickers(tickers, market_name, max_articles_per_ticker=2, force_send=False):
    webhook_url = CRYPTO_NEWS_WEBHOOK_URL if market_name == "Crypto" else STOCK_NEWS_WEBHOOK_URL

    if not webhook_url:
        return 0, 0, "Missing news webhook."

    sent_count = 0
    checked_count = 0

    for ticker in tickers:
        news_items = get_news(ticker)

        if not news_items:
            print(f"No news returned for {ticker}.")
            continue

        for article in news_items[:max_articles_per_ticker]:
            headline = get_article_title(article)
            article_url = get_article_url(article)
            publisher = get_article_publisher(article)

            if not headline:
                continue

            checked_count += 1
            news_key = f"{ticker}_{headline}_{datetime.now().strftime('%Y-%m-%d')}"

            if not force_send and news_key in st.session_state.sent_news:
                continue

            message = (
                f"📰 NEWS ALERT\n"
                f"Market: {market_name}\n"
                f"Ticker: {ticker}\n"
                f"Source: {publisher}\n"
                f"Headline: {headline}"
            )

            if article_url:
                message += f"\nLink: {article_url}"

            sent = send_discord_alert(webhook_url, message)

            if sent:
                sent_count += 1
                st.session_state.sent_news.add(news_key)
                save_sent_news(st.session_state.sent_news)
                time.sleep(0.5)

    return sent_count, checked_count, "Completed news scan."


def build_watchlist(tickers):
    results = []

    for ticker in tickers:
        result = score_ticker(ticker)
        if result is not None:
            results.append(result)

    if not results:
        return pd.DataFrame()

    watchlist_df = pd.DataFrame(results)
    return watchlist_df.sort_values(by="AI Confidence %", ascending=False)


def create_ai_summary(row):
    return (
        f"{row['Ticker']} is a {row['Market']} currently priced around ${row['Price']}. "
        f"The AI signal is {row['AI Signal']} with {row['AI Confidence %']}% confidence. "
        f"RSI is {row['RSI']}, MACD is {row['MACD']}, and the final score is {row['Final Score']}."
    )


def create_price_chart(ticker, data):
    chart_data = calculate_indicators(data)

    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=chart_data.index,
            open=chart_data["Open"],
            high=chart_data["High"],
            low=chart_data["Low"],
            close=chart_data["Close"],
            name="Candles"
        )
    )

    if "MA50" in chart_data.columns:
        fig.add_trace(
            go.Scatter(
                x=chart_data.index,
                y=chart_data["MA50"],
                line=dict(width=2),
                name="50 MA"
            )
        )

    if "MA200" in chart_data.columns:
        fig.add_trace(
            go.Scatter(
                x=chart_data.index,
                y=chart_data["MA200"],
                line=dict(width=2),
                name="200 MA"
            )
        )

    fig.update_layout(
        title=f"{ticker} Price Chart",
        xaxis_title="Date",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        height=500
    )

    return fig


def buy_position(ticker, dollar_amount, action="BUY"):
    data = get_price_data(ticker, "1d")

    if data.empty:
        st.error("Could not get current price.")
        return

    current_price = float(data["Close"].iloc[-1])
    shares = dollar_amount / current_price
    cost = shares * current_price

    if st.session_state.balance < cost:
        st.error("Not enough balance.")
        return

    st.session_state.balance -= cost
    save_balance(st.session_state.balance)

    st.session_state.portfolio.append({
        "Ticker": ticker,
        "Shares": shares,
        "Buy Price": current_price,
        "Stop Loss": current_price * (1 - STOP_LOSS_PERCENT / 100),
        "Take Profit": current_price * (1 + TAKE_PROFIT_PERCENT / 100)
    })

    st.session_state.trade_history.append({
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Action": action,
        "Ticker": ticker,
        "Shares": shares,
        "Buy Price": current_price,
        "Sell Price": None,
        "Profit/Loss $": None,
        "Profit/Loss %": None
    })

    save_records(PORTFOLIO_FILE, st.session_state.portfolio)
    save_records(TRADE_HISTORY_FILE, st.session_state.trade_history)

    send_discord_alert(
        get_trade_webhook(ticker),
        f"🟢 PAPER BUY\nTicker: {ticker}\nAmount: ${cost:.2f}\nPrice: ${current_price:.2f}\nShares: {shares:.6f}"
    )

    st.success(f"Bought ${cost:.2f} of {ticker}.")
    st.rerun()


def sell_position(index, row, reason="SELL"):
    data = get_price_data(row["Ticker"], "1d")

    if data.empty:
        st.error("Could not get current price.")
        return

    sell_price = float(data["Close"].iloc[-1])
    shares = float(row["Shares"])
    buy_price = float(row["Buy Price"])
    proceeds = shares * sell_price

    st.session_state.balance += proceeds
    save_balance(st.session_state.balance)

    profit = proceeds - (shares * buy_price)
    profit_percent = (profit / (shares * buy_price)) * 100

    st.session_state.trade_history.append({
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Action": reason,
        "Ticker": row["Ticker"],
        "Shares": shares,
        "Buy Price": buy_price,
        "Sell Price": sell_price,
        "Profit/Loss $": profit,
        "Profit/Loss %": profit_percent
    })

    st.session_state.portfolio.pop(index)

    save_records(PORTFOLIO_FILE, st.session_state.portfolio)
    save_records(TRADE_HISTORY_FILE, st.session_state.trade_history)

    send_discord_alert(
        get_trade_webhook(row["Ticker"]),
        f"🔴 PAPER SELL\nTicker: {row['Ticker']}\nReason: {reason}\nSell Price: ${sell_price:.2f}\nProfit/Loss: ${profit:.2f}"
    )

    st.success(f"Sold {row['Ticker']}.")
    st.rerun()

# ======================================================
# SESSION STATE
# ======================================================

if "balance" not in st.session_state:
    st.session_state.balance = load_balance()

if "portfolio" not in st.session_state:
    st.session_state.portfolio = load_csv_records(PORTFOLIO_FILE)

if "trade_history" not in st.session_state:
    st.session_state.trade_history = load_csv_records(TRADE_HISTORY_FILE)

if "equity_history" not in st.session_state:
    st.session_state.equity_history = load_equity_history()

if "sent_news" not in st.session_state:
    st.session_state.sent_news = load_sent_news()

if "sent_summaries" not in st.session_state:
    st.session_state.sent_summaries = load_sent_summaries()

if "sent_signal_alerts" not in st.session_state:
    loaded_signals = load_sent_signals()

    if isinstance(loaded_signals, set):
        st.session_state.sent_signal_alerts = loaded_signals
    else:
        st.session_state.sent_signal_alerts = set()

if "next_auto_signal_time" not in st.session_state:
    saved_signal_time = load_next_signal_time()

    # Avoid sending scanner alerts immediately just because the dashboard was opened.
    # If the saved time is already expired on startup, schedule a new future check.
    if saved_signal_time > time.time():
        st.session_state.next_auto_signal_time = saved_signal_time
    else:
        st.session_state.next_auto_signal_time = schedule_next_signal_time()

if "next_auto_news_time" not in st.session_state:
    saved_news_time = load_next_news_time()

    # Avoid sending news immediately just because the dashboard was opened.
    # If the saved time is already expired on startup, schedule a new future time.
    if saved_news_time > time.time():
        st.session_state.next_auto_news_time = saved_news_time
    else:
        st.session_state.next_auto_news_time = schedule_next_news_time()

# ======================================================
# TOP METRICS
# ======================================================

current_time = datetime.now()
market_hour = current_time.hour
market_status = "OPEN" if 6 <= market_hour < 13 else "CLOSED"

btc_data = get_price_data("BTC-USD", "2d")
eth_data = get_price_data("ETH-USD", "2d")

if not btc_data.empty and not eth_data.empty:
    btc_price = float(btc_data["Close"].iloc[-1])
    eth_price = float(eth_data["Close"].iloc[-1])
    btc_dominance = (btc_price / (btc_price + eth_price)) * 100
    btc_dominance_text = f"{btc_dominance:.2f}%"
else:
    btc_dominance_text = "N/A"

portfolio_value = 0

for position in st.session_state.portfolio:
    current_data = get_price_data(position["Ticker"], "1d")
    if current_data.empty:
        continue
    current_price = float(current_data["Close"].iloc[-1])
    portfolio_value += float(position["Shares"]) * current_price

total_equity = st.session_state.balance + portfolio_value

col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Time", current_time.strftime("%Y-%m-%d %H:%M:%S"))
col2.metric("Stock Market", market_status)
col3.metric("BTC/ETH Dominance", btc_dominance_text)
col4.metric("Total Equity", f"${total_equity:.2f}")

# Save equity history
if not st.session_state.equity_history or st.session_state.equity_history[-1] != total_equity:
    st.session_state.equity_history.append(total_equity)
    save_equity_history(st.session_state.equity_history)

# ======================================================
# TABS
# ======================================================

account_tab, crypto_tab, stock_tab, scanner_tab, backtest_tab, settings_tab = st.tabs([
    "Paper Account",
    "Crypto",
    "Stocks",
    "AI Scanner",
    "Backtesting",
    "Settings"
])

# ======================================================
# PAPER ACCOUNT TAB
# ======================================================

with account_tab:
    st.header("Paper Trading Account")

    col1, col2, col3 = st.columns(3)
    col1.metric("Account Balance", f"${st.session_state.balance:.2f}")
    col2.metric("Open Positions", len(st.session_state.portfolio))
    col3.metric("Portfolio Value", f"${portfolio_value:.2f}")

    if st.button("Reset Paper Account"):
        st.session_state.balance = STARTING_BALANCE
        st.session_state.portfolio = []
        st.session_state.trade_history = []
        st.session_state.equity_history = []

        save_balance(st.session_state.balance)
        save_records(PORTFOLIO_FILE, st.session_state.portfolio)
        save_records(TRADE_HISTORY_FILE, st.session_state.trade_history)
        save_equity_history(st.session_state.equity_history)

        st.success("Paper account reset.")
        st.rerun()

    st.subheader("Portfolio Performance Over Time")
    equity_df = pd.DataFrame({"Equity": st.session_state.equity_history})

    if not equity_df.empty:
        st.line_chart(equity_df)

        if len(equity_df) > 1:
            starting_equity = equity_df["Equity"].iloc[0]
            current_equity = equity_df["Equity"].iloc[-1]
            total_return = ((current_equity - starting_equity) / starting_equity) * 100
            rolling_high = equity_df["Equity"].cummax()
            drawdown = ((equity_df["Equity"] - rolling_high) / rolling_high) * 100
            max_drawdown = drawdown.min()

            col1, col2 = st.columns(2)
            col1.metric("Total Return", f"{total_return:.2f}%")
            col2.metric("Max Drawdown", f"{max_drawdown:.2f}%")

    if st.session_state.portfolio:
        st.subheader("Portfolio Holdings")

        portfolio_df = pd.DataFrame(st.session_state.portfolio)
        current_values = []
        profits = []
        profit_percents = []

        for _, row in portfolio_df.iterrows():
            ticker = row["Ticker"]
            shares = float(row["Shares"])
            buy_price = float(row["Buy Price"])
            current_data = get_price_data(ticker, "1d")

            if current_data.empty:
                current_values.append(0)
                profits.append(0)
                profit_percents.append(0)
                continue

            current_price = float(current_data["Close"].iloc[-1])
            current_value = shares * current_price
            cost_basis = shares * buy_price
            profit = current_value - cost_basis
            profit_percent = (profit / cost_basis) * 100

            current_values.append(current_value)
            profits.append(profit)
            profit_percents.append(profit_percent)

        portfolio_df["Current Value"] = current_values
        portfolio_df["Profit/Loss $"] = profits
        portfolio_df["Profit/Loss %"] = profit_percents

        st.dataframe(portfolio_df, width="stretch")

        total_unrealized = portfolio_df["Profit/Loss $"].sum()
        st.metric("Unrealized Portfolio P/L", f"${total_unrealized:.2f}")

        allocation_df = portfolio_df.groupby("Ticker")["Current Value"].sum().reset_index()
        allocation_df["Allocation %"] = (
            allocation_df["Current Value"] / allocation_df["Current Value"].sum()
        ) * 100

        fig_allocation = go.Figure(
            data=[
                go.Pie(
                    labels=allocation_df["Ticker"],
                    values=allocation_df["Current Value"],
                    hole=0.4
                )
            ]
        )
        fig_allocation.update_layout(title="Portfolio Allocation")
        st.plotly_chart(fig_allocation, width="stretch")

        largest_position = allocation_df.loc[allocation_df["Allocation %"].idxmax()]
        if largest_position["Allocation %"] > 50:
            st.warning(
                f"High concentration risk: {largest_position['Ticker']} is "
                f"{largest_position['Allocation %']:.2f}% of your portfolio."
            )

        st.subheader("Sell Positions")

        for index, row in portfolio_df.iterrows():
            current_price = row["Current Value"] / row["Shares"] if row["Shares"] else 0
            stop_loss = row["Stop Loss"]
            take_profit = row["Take Profit"]

            if current_price <= stop_loss:
                st.error(f"{row['Ticker']} hit STOP LOSS level.")
            elif current_price >= take_profit:
                st.success(f"{row['Ticker']} hit TAKE PROFIT level.")
            else:
                st.info(f"{row['Ticker']} is still within trade range.")

            if st.button(
                f"Sell {row['Ticker']} #{index}",
                key=f"sell_{row['Ticker']}_{index}_{row['Buy Price']}_{row['Shares']}"
            ):
                sell_position(index, row, "SELL")

        if st.button("Sell All Positions"):
            portfolio_copy = portfolio_df.copy()
            sold_count = 0
            unsold_positions = []

            for index, row in reversed(list(portfolio_copy.iterrows())):
                data = get_price_data(row["Ticker"], "1d")

                if data.empty:
                    unsold_positions.append({
                        "Ticker": row["Ticker"],
                        "Shares": float(row["Shares"]),
                        "Buy Price": float(row["Buy Price"]),
                        "Stop Loss": float(row["Stop Loss"]),
                        "Take Profit": float(row["Take Profit"])
                    })
                    continue

                sell_price = float(data["Close"].iloc[-1])
                shares = float(row["Shares"])
                buy_price = float(row["Buy Price"])
                proceeds = shares * sell_price
                profit = proceeds - (shares * buy_price)
                profit_percent = (profit / (shares * buy_price)) * 100

                st.session_state.balance += proceeds
                sold_count += 1

                st.session_state.trade_history.append({
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Action": "BULK SELL",
                    "Ticker": row["Ticker"],
                    "Shares": shares,
                    "Buy Price": buy_price,
                    "Sell Price": sell_price,
                    "Profit/Loss $": profit,
                    "Profit/Loss %": profit_percent
                })

            if sold_count > 0:
                st.session_state.portfolio = unsold_positions
                save_balance(st.session_state.balance)
                save_records(PORTFOLIO_FILE, st.session_state.portfolio)
                save_records(TRADE_HISTORY_FILE, st.session_state.trade_history)

                if unsold_positions:
                    st.warning(
                        f"Sold {sold_count} position(s), but kept "
                        f"{len(unsold_positions)} position(s) because price data could not be loaded."
                    )
                else:
                    st.success(f"Sold {sold_count} position(s).")

                st.rerun()
            else:
                st.error("Could not sell any positions because prices could not be loaded.")

    trade_history_df = pd.DataFrame(st.session_state.trade_history)

    if not trade_history_df.empty:
        st.subheader("Trade History")
        st.dataframe(trade_history_df, width="stretch")

        if "Action" in trade_history_df.columns:
            sell_trades = trade_history_df[
                trade_history_df["Action"].astype(str).str.contains("SELL", na=False)
            ]
        else:
            sell_trades = pd.DataFrame()

        if not sell_trades.empty and "Profit/Loss $" in sell_trades.columns:
            sell_trades = sell_trades.dropna(subset=["Profit/Loss $"])

            if not sell_trades.empty:
                total_sells = len(sell_trades)
                winning_trades = sell_trades[sell_trades["Profit/Loss $"] > 0]
                win_rate = (len(winning_trades) / total_sells) * 100
                total_profit = sell_trades["Profit/Loss $"].sum()
                average_profit = sell_trades["Profit/Loss $"].mean()

                st.subheader("Performance Stats")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Closed Trades", total_sells)
                col2.metric("Win Rate", f"{win_rate:.2f}%")
                col3.metric("Total P/L", f"${total_profit:.2f}")
                col4.metric("Average P/L", f"${average_profit:.2f}")

# ======================================================
# CRYPTO TAB
# ======================================================

with crypto_tab:
    st.header("Crypto Dashboard")

    crypto_watchlist_df = build_watchlist(CRYPTO_TICKERS)

    if crypto_watchlist_df.empty:
        st.warning("No crypto data available.")
    else:
        top_crypto = crypto_watchlist_df.iloc[0]
        st.subheader("Top Crypto AI Pick")

        col1, col2, col3 = st.columns(3)
        col1.metric("Ticker", top_crypto["Ticker"])
        col2.metric("AI Confidence", f"{top_crypto['AI Confidence %']:.2f}%")
        col3.metric("Signal", top_crypto["AI Signal"])

        st.write(create_ai_summary(top_crypto))
        st.dataframe(crypto_watchlist_df, width="stretch")

        selected_crypto = st.selectbox("Choose crypto", CRYPTO_TICKERS, key="selected_crypto")
        crypto_data = get_price_data(selected_crypto, "6mo")

        if not crypto_data.empty:
            st.plotly_chart(create_price_chart(selected_crypto, crypto_data), width="stretch")

            dollar_amount = st.number_input(
                "Dollar amount to buy",
                min_value=1.00,
                value=50.00,
                step=10.00,
                key="crypto_buy_amount"
            )

            if st.button(f"Buy {selected_crypto}", key="buy_crypto"):
                buy_position(selected_crypto, dollar_amount, "BUY")

# ======================================================
# STOCK TAB
# ======================================================

with stock_tab:
    st.header("Stock Dashboard")

    stock_watchlist_df = build_watchlist(STOCK_TICKERS)

    if stock_watchlist_df.empty:
        st.warning("No stock data available.")
    else:
        top_stock = stock_watchlist_df.iloc[0]
        st.subheader("Top Stock AI Pick")

        col1, col2, col3 = st.columns(3)
        col1.metric("Ticker", top_stock["Ticker"])
        col2.metric("AI Confidence", f"{top_stock['AI Confidence %']:.2f}%")
        col3.metric("Signal", top_stock["AI Signal"])

        st.write(create_ai_summary(top_stock))
        st.dataframe(stock_watchlist_df, width="stretch")

        selected_stock = st.selectbox("Choose stock", STOCK_TICKERS, key="selected_stock")
        stock_data = get_price_data(selected_stock, "6mo")

        if not stock_data.empty:
            st.plotly_chart(create_price_chart(selected_stock, stock_data), width="stretch")

            dollar_amount = st.number_input(
                "Dollar amount to buy",
                min_value=1.00,
                value=100.00,
                step=10.00,
                key="stock_buy_amount"
            )

            if st.button(f"Buy {selected_stock}", key="buy_stock"):
                buy_position(selected_stock, dollar_amount, "BUY")

# ======================================================
# AI SCANNER TAB
# ======================================================

with scanner_tab:
    st.header("AI Scanner")

    watchlist_df = build_watchlist(ALL_TICKERS)

    if watchlist_df.empty:
        st.warning("No scanner data available.")
    else:
        top_pick = watchlist_df.iloc[0]
        average_confidence = watchlist_df["AI Confidence %"].mean()

        if average_confidence >= 75:
            market_sentiment = "BULLISH"
        elif average_confidence >= 55:
            market_sentiment = "NEUTRAL"
        else:
            market_sentiment = "BEARISH"

        st.subheader("Top AI Pick")
        col1, col2, col3 = st.columns(3)
        col1.metric("Ticker", top_pick["Ticker"])
        col2.metric("AI Confidence", f"{top_pick['AI Confidence %']:.2f}%")
        col3.metric("Signal", top_pick["AI Signal"])

        st.subheader("AI Market Sentiment")
        col1, col2 = st.columns(2)
        col1.metric("Market Mood", market_sentiment)
        col2.metric("Average AI Confidence", f"{average_confidence:.2f}%")
        st.progress(average_confidence / 100)

        scanner_tab1, scanner_tab2, scanner_tab3 = st.tabs([
            "Crypto Scanner",
            "Stock Scanner",
            "Filtered Scanner"
        ])

        with scanner_tab1:
            st.dataframe(
                watchlist_df[watchlist_df["Market"] == "Crypto"],
                width="stretch"
            )

        with scanner_tab2:
            st.dataframe(
                watchlist_df[watchlist_df["Market"] == "Stock"],
                width="stretch"
            )

        with scanner_tab3:
            signal_filter = st.selectbox(
                "Filter AI Signals",
                ["ALL", "STRONG BUY", "BUY", "HOLD", "SELL"]
            )

            market_filter = st.selectbox(
                "Filter Market",
                ["ALL", "Crypto", "Stock"]
            )

            ticker_search = st.text_input("Search Ticker")

            filtered_watchlist = watchlist_df.copy()

            if signal_filter != "ALL":
                filtered_watchlist = filtered_watchlist[
                    filtered_watchlist["AI Signal"] == signal_filter
                ]

            if market_filter != "ALL":
                filtered_watchlist = filtered_watchlist[
                    filtered_watchlist["Market"] == market_filter
                ]

            if ticker_search:
                filtered_watchlist = filtered_watchlist[
                    filtered_watchlist["Ticker"].str.contains(ticker_search.upper(), na=False)
                ]

            st.dataframe(filtered_watchlist, width="stretch")

            filtered_buy_amount = st.number_input(
                "Dollar amount per filtered ticker",
                min_value=1.00,
                value=50.00,
                step=10.00,
                key="filtered_buy_amount"
            )

            if st.button("Bulk Buy Filtered Watchlist"):
                if filtered_watchlist.empty:
                    st.error("No filtered tickers available.")
                else:
                    bought_count = 0

                    for _, row in filtered_watchlist.iterrows():
                        ticker = row["Ticker"]
                        current_price = float(row["Price"])
                        shares = filtered_buy_amount / current_price
                        cost = shares * current_price

                        if st.session_state.balance >= cost:
                            st.session_state.balance -= cost
                            bought_count += 1

                            st.session_state.portfolio.append({
                                "Ticker": ticker,
                                "Shares": shares,
                                "Buy Price": current_price,
                                "Stop Loss": current_price * (1 - STOP_LOSS_PERCENT / 100),
                                "Take Profit": current_price * (1 + TAKE_PROFIT_PERCENT / 100)
                            })

                            st.session_state.trade_history.append({
                                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "Action": "BULK BUY FILTERED",
                                "Ticker": ticker,
                                "Shares": shares,
                                "Buy Price": current_price,
                                "Sell Price": None,
                                "Profit/Loss $": None,
                                "Profit/Loss %": None
                            })

                            send_discord_alert(
                                get_trade_webhook(ticker),
                                f"🟢 BULK PAPER BUY\nTicker: {ticker}\nAmount: ${cost:.2f}\nPrice: ${current_price:.2f}\nShares: {shares:.6f}"
                            )

                    save_balance(st.session_state.balance)
                    save_records(PORTFOLIO_FILE, st.session_state.portfolio)
                    save_records(TRADE_HISTORY_FILE, st.session_state.trade_history)

                    if bought_count > 0:
                        st.success(f"Bulk buy completed for {bought_count} ticker(s).")
                        st.rerun()
                    else:
                        st.error("Not enough balance to buy any filtered tickers.")

        st.subheader("AI Watchlist Heatmap")
        heatmap_fig = go.Figure(
            data=go.Heatmap(
                z=[watchlist_df["AI Confidence %"]],
                x=watchlist_df["Ticker"],
                y=["AI Confidence"],
                text=[watchlist_df["AI Signal"]],
                texttemplate="%{text}",
                colorscale="RdYlGn"
            )
        )
        heatmap_fig.update_layout(xaxis_title="Ticker", yaxis_title="Metric")
        st.plotly_chart(heatmap_fig, width="stretch")

        csv = watchlist_df.to_csv(index=False)
        st.download_button(
            label="Download Watchlist CSV",
            data=csv,
            file_name="ai_watchlist.csv",
            mime="text/csv"
        )

        crypto_summary_message = build_market_summary(watchlist_df, "Crypto")
        stock_summary_message = build_market_summary(watchlist_df, "Stock")

        # Auto-send daily summaries once per day after scheduled server-local time.
        if should_send_daily_summary("Crypto"):
            send_scheduled_daily_summary(watchlist_df, "Crypto")

        if should_send_daily_summary("Stock"):
            send_scheduled_daily_summary(watchlist_df, "Stock")

        send_auto_signal_alerts(watchlist_df)
        send_auto_newsletter_if_due()

        if st.button("Send Crypto Daily Summary"):
            sent = send_market_summary_embed(watchlist_df, "Crypto")
            if sent:
                mark_daily_summary_sent("Crypto")
                st.success("Crypto daily summary sent to Discord.")
            else:
                st.warning("Crypto summary webhook not found, failed, or no crypto data was available.")

        if st.button("Send Stock Daily Summary"):
            sent = send_market_summary_embed(watchlist_df, "Stock")
            if sent:
                mark_daily_summary_sent("Stock")
                st.success("Stock daily summary sent to Discord.")
            else:
                st.warning("Stock summary webhook not found, failed, or no stock data was available.")

        if st.button("Send Both Daily Summaries"):
            crypto_sent = send_market_summary_embed(watchlist_df, "Crypto")
            time.sleep(1)
            stock_sent = send_market_summary_embed(watchlist_df, "Stock")

            if crypto_sent:
                mark_daily_summary_sent("Crypto")

            if stock_sent:
                mark_daily_summary_sent("Stock")

            if crypto_sent and stock_sent:
                st.success("Crypto and stock summaries sent to Discord.")
            elif crypto_sent:
                st.warning("Crypto summary sent, but stock summary failed or webhook is missing.")
            elif stock_sent:
                st.warning("Stock summary sent, but crypto summary failed or webhook is missing.")
            else:
                st.error("No summaries were sent. Check your summary webhooks.")

        if st.button("Send Strong Buy Alerts"):
            strong_buys = watchlist_df[watchlist_df["AI Signal"].isin(["STRONG BUY", "BUY"])]

            if strong_buys.empty:
                st.info("No strong buy or buy alerts right now.")
            else:
                for _, row in strong_buys.iterrows():
                    alert_key = f"{row['Ticker']}_{row['AI Signal']}_{datetime.now().strftime('%Y-%m-%d')}"

                    if alert_key not in st.session_state.sent_signal_alerts:
                        sent = send_signal_embed(row)
                        if sent:
                            st.session_state.sent_signal_alerts.add(alert_key)
                        save_sent_signals(st.session_state.sent_signal_alerts)

                st.success("Signal alerts sent.")

# ======================================================
# BACKTESTING TAB
# ======================================================

with backtest_tab:
    st.header("Strategy Backtesting")

    backtest_ticker = st.selectbox("Choose ticker for backtest", ALL_TICKERS)

    backtest_strategy = st.selectbox(
        "Choose backtest strategy",
        ["RSI Strategy", "MACD Strategy", "Moving Average Strategy"]
    )

    if st.button("Run Backtest"):
        backtest_data = get_price_data(backtest_ticker, "1y")

        if backtest_data.empty:
            st.error("No backtest data available.")
        else:
            backtest_data = calculate_indicators(backtest_data)

            balance = 10000
            shares = 0
            trade_log = []

            for index, row in backtest_data.iterrows():
                price = row["Close"]
                rsi_value = row.get("RSI", None)
                macd_value = row.get("MACD", None)
                macd_signal_value = row.get("MACD Signal", None)
                ma50_value = row.get("MA50", None)
                ma200_value = row.get("MA200", None)

                if pd.isna(price):
                    continue

                buy_signal = False
                sell_signal = False

                if backtest_strategy == "RSI Strategy":
                    buy_signal = pd.notna(rsi_value) and rsi_value < 35
                    sell_signal = pd.notna(rsi_value) and rsi_value > 70

                elif backtest_strategy == "MACD Strategy":
                    buy_signal = pd.notna(macd_value) and pd.notna(macd_signal_value) and macd_value > macd_signal_value
                    sell_signal = pd.notna(macd_value) and pd.notna(macd_signal_value) and macd_value < macd_signal_value

                elif backtest_strategy == "Moving Average Strategy":
                    buy_signal = pd.notna(ma50_value) and pd.notna(ma200_value) and ma50_value > ma200_value
                    sell_signal = pd.notna(ma50_value) and pd.notna(ma200_value) and ma50_value < ma200_value

                if buy_signal and balance > 0:
                    shares = balance / price
                    balance = 0
                    trade_log.append({
                        "Date": index.strftime("%Y-%m-%d"),
                        "Action": "BUY",
                        "Price": price,
                        "Portfolio Value": balance + (shares * price)
                    })

                elif sell_signal and shares > 0:
                    balance = shares * price
                    shares = 0
                    trade_log.append({
                        "Date": index.strftime("%Y-%m-%d"),
                        "Action": "SELL",
                        "Price": price,
                        "Portfolio Value": balance
                    })

            final_value = balance
            if shares > 0:
                final_value += shares * backtest_data["Close"].iloc[-1]

            total_return = ((final_value - 10000) / 10000) * 100

            st.success(f"Backtest Complete | Final Portfolio Value: ${final_value:.2f}")
            st.metric("Backtest Return", f"{total_return:.2f}%")

            trade_log_df = pd.DataFrame(trade_log)

            if trade_log_df.empty:
                st.info("No trades were triggered by this strategy.")
            else:
                buy_prices = trade_log_df[trade_log_df["Action"] == "BUY"]["Price"].tolist()
                sell_prices = trade_log_df[trade_log_df["Action"] == "SELL"]["Price"].tolist()

                completed_trades = min(len(buy_prices), len(sell_prices))
                wins = 0
                losses = 0
                trade_results = []

                for i in range(completed_trades):
                    result = ((sell_prices[i] - buy_prices[i]) / buy_prices[i]) * 100
                    trade_results.append(result)

                    if result > 0:
                        wins += 1
                    else:
                        losses += 1

                win_rate = (wins / completed_trades) * 100 if completed_trades > 0 else 0
                average_win = sum([x for x in trade_results if x > 0]) / wins if wins > 0 else 0
                average_loss = sum([x for x in trade_results if x <= 0]) / losses if losses > 0 else 0

                st.subheader("Backtest Stats")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Completed Trades", completed_trades)
                col2.metric("Wins", wins)
                col3.metric("Losses", losses)
                col4.metric("Win Rate", f"{win_rate:.2f}%")

                col5, col6 = st.columns(2)
                col5.metric("Average Win", f"{average_win:.2f}%")
                col6.metric("Average Loss", f"{average_loss:.2f}%")

                st.dataframe(trade_log_df, width="stretch")

                st.subheader("Backtest Equity Curve")
                st.line_chart(trade_log_df, x="Date", y="Portfolio Value")

                buy_trades = trade_log_df[trade_log_df["Action"] == "BUY"]
                sell_trades = trade_log_df[trade_log_df["Action"] == "SELL"]

                signal_fig = go.Figure()
                signal_fig.add_trace(
                    go.Scatter(
                        x=backtest_data.index,
                        y=backtest_data["Close"],
                        mode="lines",
                        name="Price"
                    )
                )
                signal_fig.add_trace(
                    go.Scatter(
                        x=buy_trades["Date"],
                        y=buy_trades["Price"],
                        mode="markers",
                        name="BUY",
                        marker=dict(size=10, symbol="triangle-up")
                    )
                )
                signal_fig.add_trace(
                    go.Scatter(
                        x=sell_trades["Date"],
                        y=sell_trades["Price"],
                        mode="markers",
                        name="SELL",
                        marker=dict(size=10, symbol="triangle-down")
                    )
                )
                st.plotly_chart(signal_fig, width="stretch")

# ======================================================
# SETTINGS TAB
# ======================================================

with settings_tab:
    st.header("Settings")
    st.caption(f"Running {APP_VERSION}")

    st.subheader("Active Watchlists")

    col1, col2 = st.columns(2)

    with col1:
        st.write("Crypto Tickers")
        st.write(CRYPTO_TICKERS)

    with col2:
        st.write("Stock Tickers")
        st.write(STOCK_TICKERS)

    st.subheader("Discord Webhook Status")
    st.write("Crypto Trade Webhook:", "Connected" if CRYPTO_TRADE_WEBHOOK_URL else "Not connected")
    st.write("Stock Trade Webhook:", "Connected" if STOCK_TRADE_WEBHOOK_URL else "Not connected")
    st.write("Crypto News Webhook:", "Connected" if CRYPTO_NEWS_WEBHOOK_URL else "Not connected")
    st.write("Stock News Webhook:", "Connected" if STOCK_NEWS_WEBHOOK_URL else "Not connected")
    st.write("Crypto Summary Webhook:", "Connected" if CRYPTO_SUMMARY_WEBHOOK_URL else "Not connected")
    st.write("Stock Summary Webhook:", "Connected" if STOCK_SUMMARY_WEBHOOK_URL else "Not connected")
    st.write("Old Summary Webhook Fallback:", "Connected" if SUMMARY_WEBHOOK_URL else "Not connected")
    st.write("Old Trade Webhook Fallback:", "Connected" if TRADE_WEBHOOK_URL else "Not connected")
    st.write("Old News Webhook Fallback:", "Connected" if NEWS_WEBHOOK_URL else "Not connected")

    st.subheader("Discord Alert Tests")

    col_test1, col_test2 = st.columns(2)

    with col_test1:
        if st.button("Test Crypto Trade Webhook"):
            sent = send_discord_alert(
                get_trade_webhook("BTC-USD"),
                "TEST CRYPTO TRADE ALERT"
            )
            st.success("Crypto trade test sent.") if sent else st.error("Crypto trade test failed.")

        if st.button("Test Crypto News Webhook"):
            sent = send_discord_alert(
                CRYPTO_NEWS_WEBHOOK_URL or NEWS_WEBHOOK_URL,
                "TEST CRYPTO NEWS ALERT"
            )
            st.success("Crypto news test sent.") if sent else st.error("Crypto news test failed.")

        if st.button("Test Crypto Summary Webhook"):
            sent = send_discord_alert(
                get_summary_webhook("Crypto"),
                "TEST CRYPTO SUMMARY ALERT"
            )
            st.success("Crypto summary test sent.") if sent else st.error("Crypto summary test failed.")

    with col_test2:
        if st.button("Test Stock Trade Webhook"):
            sent = send_discord_alert(
                get_trade_webhook("AAPL"),
                "TEST STOCK TRADE ALERT"
            )
            st.success("Stock trade test sent.") if sent else st.error("Stock trade test failed.")

        if st.button("Test Stock News Webhook"):
            sent = send_discord_alert(
                STOCK_NEWS_WEBHOOK_URL or NEWS_WEBHOOK_URL,
                "TEST STOCK NEWS ALERT"
            )
            st.success("Stock news test sent.") if sent else st.error("Stock news test failed.")

        if st.button("Test Stock Summary Webhook"):
            sent = send_discord_alert(
                get_summary_webhook("Stock"),
                "TEST STOCK SUMMARY ALERT"
            )
            st.success("Stock summary test sent.") if sent else st.error("Stock summary test failed.")

    st.subheader("Manual News Scan")

    st.write("Automatic news newsletter enabled:", AUTO_NEWS_ALERTS_ENABLED)
    st.write("News interval minutes:", f"{AUTO_NEWS_MIN_INTERVAL_MINUTES} to {AUTO_NEWS_MAX_INTERVAL_MINUTES}")
    st.write("Max articles per market digest:", AUTO_NEWS_MAX_ARTICLES_PER_MARKET)
    st.write("Next automatic news digest:", datetime.fromtimestamp(st.session_state.next_auto_news_time).strftime("%Y-%m-%d %H:%M:%S"))

    col_news1, col_news2, col_news3 = st.columns(3)

    with col_news1:
        if st.button("Send Latest Crypto News Now"):
            sent_count, checked_count, status_message = send_latest_news_for_tickers(
                CRYPTO_TICKERS,
                "Crypto",
                max_articles_per_ticker=2,
                force_send=True
            )
            if sent_count > 0:
                st.success(f"Sent {sent_count} crypto news alert(s). Checked {checked_count} article(s).")
            else:
                st.warning(f"No crypto news alerts sent. Checked {checked_count} article(s). {status_message}")

    with col_news2:
        if st.button("Send Latest Stock News Now"):
            sent_count, checked_count, status_message = send_latest_news_for_tickers(
                STOCK_TICKERS,
                "Stock",
                max_articles_per_ticker=2,
                force_send=True
            )
            if sent_count > 0:
                st.success(f"Sent {sent_count} stock news alert(s). Checked {checked_count} article(s).")
            else:
                st.warning(f"No stock news alerts sent. Checked {checked_count} article(s). {status_message}")

    with col_news3:
        if st.button("Clear Sent News Log"):
            st.session_state.sent_news = set()
            clear_sent_news_log()
            st.success("Sent news log cleared.")

    st.subheader("Scheduled Daily Summaries")
    st.write(
        "Auto daily summaries send once per day after "
        f"{AUTO_DAILY_SUMMARY_HOUR:02d}:{AUTO_DAILY_SUMMARY_MINUTE:02d} server time."
    )
    st.write("Sent summary records today:", sorted(st.session_state.sent_summaries))

    if st.button("Clear Sent Summary Log"):
        st.session_state.sent_summaries = set()
        clear_sent_summary_log()
        st.success("Sent summary log cleared.")

    st.subheader("Automatic Signal Alerts")
    st.write("Auto signal alerts enabled:", AUTO_SIGNAL_ALERTS_ENABLED)
    st.write("Minimum confidence for auto signal alerts:", AUTO_SIGNAL_MIN_CONFIDENCE)
    st.write("Signal check interval minutes:", AUTO_SIGNAL_CHECK_INTERVAL_MINUTES)
    st.write("Next automatic signal check:", datetime.fromtimestamp(st.session_state.next_auto_signal_time).strftime("%Y-%m-%d %H:%M:%S"))
    st.write("Sent signal records today:", sorted(st.session_state.sent_signal_alerts))

    st.subheader("Automatic News Newsletter")
    st.write("Automatic news newsletter enabled:", AUTO_NEWS_ALERTS_ENABLED)
    st.write("News interval minutes:", f"{AUTO_NEWS_MIN_INTERVAL_MINUTES} to {AUTO_NEWS_MAX_INTERVAL_MINUTES}")
    st.write("Max articles per market digest:", AUTO_NEWS_MAX_ARTICLES_PER_MARKET)
    st.write("Next automatic news digest:", datetime.fromtimestamp(st.session_state.next_auto_news_time).strftime("%Y-%m-%d %H:%M:%S"))
    st.info("News does not send just because you open the dashboard. It waits until the next scheduled random interval, then sends one small crypto digest and one small stock digest while the app is running.")

    if st.button("Clear Sent Signal Log"):
        st.session_state.sent_signal_alerts = set()
        clear_sent_signal_log()
        st.success("Sent signal log cleared.")

    st.info(
        "Recommended environment variables: CRYPTO_TRADE_WEBHOOK_URL, "
        "STOCK_TRADE_WEBHOOK_URL, CRYPTO_NEWS_WEBHOOK_URL, "
        "STOCK_NEWS_WEBHOOK_URL, CRYPTO_SUMMARY_WEBHOOK_URL, "
        "STOCK_SUMMARY_WEBHOOK_URL. Optional schedule variables: AUTO_DAILY_SUMMARY_HOUR, AUTO_DAILY_SUMMARY_MINUTE, AUTO_SIGNAL_ALERTS_ENABLED, AUTO_SIGNAL_MIN_CONFIDENCE, AUTO_SIGNAL_CHECK_INTERVAL_MINUTES, AUTO_NEWS_ALERTS_ENABLED, AUTO_NEWS_MIN_INTERVAL_MINUTES, AUTO_NEWS_MAX_INTERVAL_MINUTES, and AUTO_NEWS_MAX_ARTICLES_PER_MARKET. SUMMARY_WEBHOOK_URL still works as a fallback. Older variables like "
        "CRYPTO_WEBHOOK_URL, STOCK_WEBHOOK_URL, TRADE_WEBHOOK_URL, and "
        "NEWS_WEBHOOK_URL still work as fallbacks."
    )

st.divider()
st.caption("AI Trading Dashboard | Stocks and Crypto Only | For education and paper trading, not financial advice.")
