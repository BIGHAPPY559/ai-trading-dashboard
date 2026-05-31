import os
import json
import random
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

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

def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ["1", "true", "yes", "y", "on"]


def get_env_float(name, default):
    value = os.getenv(name)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_env_int(name, default):
    value = os.getenv(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_env_list(name, default_items):
    value = os.getenv(name, "")
    if not value.strip():
        return list(default_items)
    items = []
    for item in value.split(","):
        cleaned = item.strip().upper()
        if cleaned and not any(char.isspace() for char in cleaned) and cleaned not in items:
            items.append(cleaned)
    return items if items else list(default_items)


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
DATA_DIR = os.getenv("DASHBOARD_DATA_DIR", os.getenv("BOT_DATA_DIR", BASE_DIR)).strip() or BASE_DIR
os.makedirs(DATA_DIR, exist_ok=True)

PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.csv")
TRADE_HISTORY_FILE = os.path.join(DATA_DIR, "trade_history.csv")
BALANCE_FILE = os.path.join(DATA_DIR, "balance.txt")
EQUITY_FILE = os.path.join(DATA_DIR, "equity_history.csv")
NEWS_LOG_FILE = os.path.join(DATA_DIR, "sent_news_log.txt")
SUMMARY_LOG_FILE = os.path.join(DATA_DIR, "sent_summary_log.txt")
SIGNAL_LOG_FILE = os.path.join(DATA_DIR, "bot_sent_signal_log.txt")  # shared with background bot to prevent duplicate signal alerts
NEWS_SCHEDULE_FILE = os.path.join(DATA_DIR, "news_schedule_log.txt")
SIGNAL_SCHEDULE_FILE = os.path.join(DATA_DIR, "signal_schedule_log.txt")
ALERT_HISTORY_FILE = os.path.join(DATA_DIR, "alert_history.csv")
SIGNAL_HISTORY_FILE = os.path.join(DATA_DIR, "signal_history.csv")
BOT_STATUS_FILE = os.path.join(DATA_DIR, "bot_last_status.json")

PAPER_TRADES_FILE = os.path.join(DATA_DIR, "paper_trades.csv")
PAPER_EQUITY_FILE = os.path.join(DATA_DIR, "paper_trade_equity_curve.csv")

# ======================================================
# SETTINGS
# ======================================================

APP_VERSION = "v32_paper_trade_tracking"

STARTING_BALANCE = 10000
STOP_LOSS_PERCENT = 5
TAKE_PROFIT_PERCENT = 10

# Daily summaries auto-send once per day after this server-local time.
# Railway usually runs in UTC unless you set a timezone.
AUTO_DAILY_SUMMARY_HOUR = get_env_int("AUTO_DAILY_SUMMARY_HOUR", 7)
AUTO_DAILY_SUMMARY_MINUTE = get_env_int("AUTO_DAILY_SUMMARY_MINUTE", 0)
DASHBOARD_AUTO_SUMMARIES_ENABLED = get_env_bool("DASHBOARD_AUTO_SUMMARIES_ENABLED", False)

# Automatic scanner signal alerts.
# Sends one alert per ticker/signal per day to avoid spam.
# Keep dashboard auto-alerts OFF by default because bot.py handles background alerts.
# You can still use the dashboard's manual test/send buttons.
AUTO_SIGNAL_ALERTS_ENABLED = get_env_bool("DASHBOARD_AUTO_SIGNAL_ALERTS_ENABLED", False)
AUTO_SIGNAL_MIN_CONFIDENCE = get_env_float("AUTO_SIGNAL_MIN_CONFIDENCE", 75)
AUTO_SIGNAL_CHECK_INTERVAL_MINUTES = get_env_int("AUTO_SIGNAL_CHECK_INTERVAL_MINUTES", 15)

# Automatic market news newsletter.
# This does not send on app open. It schedules the next run in the future,
# then sends a small digest only when the app is running and the time is reached.
# Keep dashboard auto-news OFF by default because bot.py handles background news.
# You can still use the dashboard's manual news buttons.
AUTO_NEWS_ALERTS_ENABLED = get_env_bool("DASHBOARD_AUTO_NEWS_ALERTS_ENABLED", False)
AUTO_NEWS_MIN_INTERVAL_MINUTES = get_env_int("AUTO_NEWS_MIN_INTERVAL_MINUTES", 180)
AUTO_NEWS_MAX_INTERVAL_MINUTES = get_env_int("AUTO_NEWS_MAX_INTERVAL_MINUTES", 360)
AUTO_NEWS_MAX_ARTICLES_PER_MARKET = get_env_int("AUTO_NEWS_MAX_ARTICLES_PER_MARKET", 5)

# Keep dashboard signals aligned with the v13 Railway bot by default.
# Turn this on only if you intentionally want dashboard scoring to include Yahoo headlines.
DASHBOARD_NEWS_SCORE_ENABLED = get_env_bool("DASHBOARD_NEWS_SCORE_ENABLED", False)
DASHBOARD_YFINANCE_NEWS_ENABLED = get_env_bool("DASHBOARD_YFINANCE_NEWS_ENABLED", False)
YFINANCE_TIMEOUT_SECONDS = max(5, get_env_int("YFINANCE_TIMEOUT_SECONDS", 20))
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "America/Los_Angeles")
DISCORD_MESSAGE_LIMIT = max(500, min(get_env_int("DISCORD_MESSAGE_LIMIT", 1900), 2000))


def now_dt():
    try:
        return datetime.now(ZoneInfo(BOT_TIMEZONE))
    except Exception:
        return datetime.now()


def now_text(include_seconds=False):
    fmt = "%Y-%m-%d %H:%M:%S" if include_seconds else "%Y-%m-%d %H:%M"
    return now_dt().strftime(fmt)


def observed_date(month, day, year):
    base = datetime(year, month, day).date()
    if base.weekday() == 5:
        return base - timedelta(days=1)
    if base.weekday() == 6:
        return base + timedelta(days=1)
    return base


def nth_weekday(year, month, weekday, n):
    first = datetime(year, month, 1).date()
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def last_weekday(year, month, weekday):
    if month == 12:
        next_month = datetime(year + 1, 1, 1).date()
    else:
        next_month = datetime(year, month + 1, 1).date()
    day = next_month - timedelta(days=1)
    while day.weekday() != weekday:
        day -= timedelta(days=1)
    return day


def easter_date(year):
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day).date()


def us_stock_market_holidays(year):
    return {
        observed_date(1, 1, year),                 # New Year's Day
        nth_weekday(year, 1, 0, 3),                # Martin Luther King Jr. Day
        nth_weekday(year, 2, 0, 3),                # Washington's Birthday
        easter_date(year) - timedelta(days=2),  # Good Friday
        last_weekday(year, 5, 0),                  # Memorial Day
        observed_date(6, 19, year),                # Juneteenth
        observed_date(7, 4, year),                 # Independence Day
        nth_weekday(year, 9, 0, 1),                # Labor Day
        nth_weekday(year, 11, 3, 4),               # Thanksgiving Day
        observed_date(12, 25, year),               # Christmas Day
    }


def get_stock_market_status(current_dt):
    market_date = current_dt.date()

    if current_dt.weekday() >= 5:
        return "CLOSED"

    holiday_dates = (
        us_stock_market_holidays(current_dt.year)
        | us_stock_market_holidays(current_dt.year + 1)
    )

    if market_date in holiday_dates:
        return "CLOSED"

    market_minutes = current_dt.hour * 60 + current_dt.minute
    open_minutes = 6 * 60 + 30
    close_minutes = 13 * 60

    return "OPEN" if open_minutes <= market_minutes < close_minutes else "CLOSED"


# Add or remove tickers here
CRYPTO_TICKERS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "HBAR-USD",
    "AVAX-USD", "VET-USD", "ICP-USD", "ATOM-USD", "ALGO-USD", "XLM-USD"
]

STOCK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "PLTR", "SPY", "QQQ"
]

CRYPTO_TICKERS = get_env_list("BOT_CRYPTO_TICKERS", CRYPTO_TICKERS)
STOCK_TICKERS = get_env_list("BOT_STOCK_TICKERS", STOCK_TICKERS)
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
        data = yf.download(
            ticker,
            period=period,
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=YFINANCE_TIMEOUT_SECONDS,
        )
        if data is None or data.empty:
            return pd.DataFrame()
        if isinstance(data.columns, pd.MultiIndex):
            try:
                data.columns = data.columns.get_level_values(0)
            except Exception:
                data.columns = [str(col[0]) for col in data.columns]
        if "Close" not in data.columns:
            return pd.DataFrame()
        return data.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_news(ticker):
    if not DASHBOARD_YFINANCE_NEWS_ENABLED:
        return []
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
            response = requests.post(webhook_url, json={"content": str(message)[:DISCORD_MESSAGE_LIMIT]}, timeout=10)
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

    clean_fields = []
    for field in fields:
        clean_fields.append({
            "name": str(field.get("name", " "))[:256] or " ",
            "value": str(field.get("value", "N/A"))[:1024] or "N/A",
            "inline": bool(field.get("inline", False)),
        })

    payload = {
        "embeds": [
            {
                "title": str(title)[:256],
                "color": color,
                "fields": clean_fields[:25],
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
        {"name": "Time", "value": now_text(), "inline": False},
    ]

    sent = send_discord_embed(
        get_trade_webhook(ticker),
        f"{market} Market | {signal}",
        signal_embed_color(signal),
        fields
    )

    if sent:
        log_trade_notification(
            ticker=ticker,
            market=market,
            signal=signal,
            confidence=row["AI Confidence %"],
            rsi=row["RSI"],
            price=row["Price"],
            source="AI Signal Alert"
        )

    return sent

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

def load_paper_trades_df():
    try:
        if os.path.exists(PAPER_TRADES_FILE) and os.path.getsize(PAPER_TRADES_FILE) > 0:
            return pd.read_csv(PAPER_TRADES_FILE)
    except Exception as error:
        st.warning(f"Could not load paper_trades.csv: {error}")
    return pd.DataFrame()


def load_paper_equity_df():
    try:
        if os.path.exists(PAPER_EQUITY_FILE) and os.path.getsize(PAPER_EQUITY_FILE) > 0:
            return pd.read_csv(PAPER_EQUITY_FILE)
    except Exception as error:
        st.warning(f"Could not load paper_trade_equity_curve.csv: {error}")
    return pd.DataFrame()


def paper_trade_metrics(trades_df):
    if trades_df.empty:
        return {
            "total_closed": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_pnl": 0,
            "best_ticker": "N/A",
            "worst_ticker": "N/A",
            "average_winner": 0,
            "average_loser": 0,
        }
    closed = trades_df[trades_df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])] if "status" in trades_df.columns else pd.DataFrame()
    if closed.empty:
        return {
            "total_closed": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_pnl": 0,
            "best_ticker": "N/A",
            "worst_ticker": "N/A",
            "average_winner": 0,
            "average_loser": 0,
        }
    pnl = pd.to_numeric(closed.get("pnl_dollars", 0), errors="coerce").fillna(0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_wins = wins.sum()
    gross_losses = abs(losses.sum())
    by_ticker = closed.assign(_pnl=pnl).groupby("ticker")["_pnl"].sum() if "ticker" in closed.columns else pd.Series(dtype=float)
    return {
        "total_closed": len(closed),
        "win_rate": round((len(wins) / len(closed)) * 100, 2) if len(closed) else 0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 0),
        "total_pnl": round(pnl.sum(), 2),
        "best_ticker": by_ticker.idxmax() if not by_ticker.empty else "N/A",
        "worst_ticker": by_ticker.idxmin() if not by_ticker.empty else "N/A",
        "average_winner": round(wins.mean(), 2) if not wins.empty else 0,
        "average_loser": round(losses.mean(), 2) if not losses.empty else 0,
    }


def load_bot_status():
    try:
        if os.path.exists(BOT_STATUS_FILE) and os.path.getsize(BOT_STATUS_FILE) > 0:
            with open(BOT_STATUS_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
    except Exception as error:
        print("Bot status load error:", error)
    return {}


def status_age_minutes(status):
    try:
        timestamp_text = status.get("timestamp_utc", "")
        if not timestamp_text:
            return None
        timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - timestamp).total_seconds() / 60, 2)
    except Exception:
        return None


def load_alert_history():
    try:
        if os.path.exists(ALERT_HISTORY_FILE) and os.path.getsize(ALERT_HISTORY_FILE) > 0:
            return pd.read_csv(ALERT_HISTORY_FILE).to_dict("records")
    except Exception as error:
        print("Alert history load error:", error)

    return []


def save_alert_history(alert_history):
    try:
        pd.DataFrame(alert_history).to_csv(ALERT_HISTORY_FILE, index=False)
    except Exception as error:
        print("Alert history save error:", error)


def log_trade_notification(ticker, market, signal, confidence, rsi, price, source):
    alert = {
        "Time": now_text(include_seconds=True),
        "Ticker": ticker,
        "Market": market,
        "Signal": signal,
        "Confidence %": confidence,
        "RSI": rsi,
        "Price": price,
        "Source": source
    }

    st.session_state.alert_history.insert(0, alert)
    st.session_state.alert_history = st.session_state.alert_history[:100]
    save_alert_history(st.session_state.alert_history)

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
    today = now_dt().strftime('%Y-%m-%d')

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
    webhook_url = get_news_webhook("BTC-USD") if market_name == "Crypto" else get_news_webhook("AAPL")

    if not webhook_url:
        print(f"Missing {market_name} news webhook.")
        return 0

    digest_items = build_news_digest(tickers, market_name, max_articles)

    if not digest_items:
        print(f"No new {market_name} news articles to send.")
        return 0

    message = f"📰 {market_name.upper()} MARKET NEWS DIGEST\n"
    message += f"Time: {now_text()}\n\n"

    for number, item in enumerate(digest_items, start=1):
        line = (
            f"{number}. {item['ticker']} | {item['headline']}\n"
            f"Source: {item['publisher']}"
        )

        if item["url"]:
            line += f"\n{item['url']}"

        line += "\n\n"

        if len(message) + len(line) > DISCORD_MESSAGE_LIMIT:
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
    now = now_dt()
    scheduled_time_reached = (
        now.hour > AUTO_DAILY_SUMMARY_HOUR
        or (now.hour == AUTO_DAILY_SUMMARY_HOUR and now.minute >= AUTO_DAILY_SUMMARY_MINUTE)
    )

    if not scheduled_time_reached:
        return False

    summary_key = f"{market}_{now.strftime('%Y-%m-%d')}"
    return summary_key not in st.session_state.sent_summaries


def mark_daily_summary_sent(market):
    summary_key = f"{market}_{now_dt().strftime('%Y-%m-%d')}"
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

    alert_signals = ["STRONG BUY", "BUY", "STRONG SELL", "SELL"]
    candidates = watchlist_df[
        watchlist_df["AI Signal"].isin(alert_signals)
        & (watchlist_df["AI Confidence %"] >= AUTO_SIGNAL_MIN_CONFIDENCE)
    ].copy()

    if candidates.empty:
        st.session_state.next_auto_signal_time = schedule_next_signal_time()
        return 0

    sent_count = 0
    today = now_dt().strftime('%Y-%m-%d')

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
        return CRYPTO_SUMMARY_WEBHOOK_URL or SUMMARY_WEBHOOK_URL or CRYPTO_TRADE_WEBHOOK_URL or TRADE_WEBHOOK_URL
    if market == "Stock":
        return STOCK_SUMMARY_WEBHOOK_URL or SUMMARY_WEBHOOK_URL or STOCK_TRADE_WEBHOOK_URL or TRADE_WEBHOOK_URL
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

    summary_text = (
        f"🟢 BUY SIGNALS\n"
        f"{format_summary_section(market_df, ['STRONG BUY', 'BUY'])}\n\n"
        f"🟡 HOLD SIGNALS\n"
        f"{format_summary_section(market_df, ['HOLD'])}\n\n"
        f"🔴 SELL SIGNALS\n"
        f"{format_summary_section(market_df, ['SELL', 'STRONG SELL'])}\n\n"
        f"Time\n"
        f"{now_text()}"
    )

    return [
        {
            "name": " ",
            "value": summary_text,
            "inline": False
        }
    ]


def send_market_summary_embed(watchlist_df, market):
    fields = build_market_summary_fields(watchlist_df, market)

    if not fields:
        print(f"No {market} summary available.")
        return False

    print(f"Sending {market} summary as Discord embed/card format.")
    return send_discord_embed(
        get_summary_webhook(market),
        f"📊 {market} Market Summary",
        3447003,
        fields
    )


def build_market_summary(watchlist_df, market):
    # Text preview only. Discord sends summaries through send_market_summary_embed().
    # Do not put old AI DAILY summary formatting here.
    market_df = watchlist_df[watchlist_df["Market"] == market].copy()

    if market_df.empty:
        return ""

    return (
        f"📊 {market} Market Summary\n\n"
        f"🟢 BUY SIGNALS\n{format_summary_section(market_df, ['STRONG BUY', 'BUY'])}\n\n"
        f"🟡 HOLD SIGNALS\n{format_summary_section(market_df, ['HOLD'])}\n\n"
        f"🔴 SELL SIGNALS\n{format_summary_section(market_df, ['SELL', 'STRONG SELL'])}\n\n"
        f"Time\n{now_text()}"
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
    data = get_price_data(ticker, "1y")

    if data.empty or len(data) < 50:
        return None

    data = calculate_indicators(data)
    latest = data.iloc[-1]

    current_price = float(latest["Close"])
    previous_price = float(data["Close"].iloc[-2])
    price_change_percent = 0 if previous_price == 0 else ((current_price - previous_price) / previous_price) * 100

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

    bullish_confidence = (final_score / 120) * 100
    bearish_confidence = ((120 - final_score) / 120) * 100

    if final_score >= 90:
        ai_signal = "STRONG BUY"
        confidence_percent = bullish_confidence
    elif final_score >= 75:
        ai_signal = "BUY"
        confidence_percent = bullish_confidence
    elif final_score <= 30:
        ai_signal = "STRONG SELL"
        confidence_percent = bearish_confidence
    elif final_score < 50:
        ai_signal = "SELL"
        confidence_percent = bearish_confidence
    else:
        ai_signal = "HOLD"
        # HOLD is neutral, so keep the displayed confidence realistic instead of showing 90-100%.
        confidence_percent = 100 - abs(60 - final_score)
        confidence_percent = min(confidence_percent, 50)

    confidence_percent = max(0, min(confidence_percent, 100))

    if confidence_percent >= 80:
        confidence_level = "HIGH"
    elif confidence_percent >= 60:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

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

    if not DASHBOARD_NEWS_SCORE_ENABLED:
        return 0

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
    webhook_url = get_news_webhook("BTC-USD") if market_name == "Crypto" else get_news_webhook("AAPL")

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
            news_key = f"{ticker}_{headline}_{now_dt().strftime('%Y-%m-%d')}"

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

def confidence_display_text(row):
    signal = str(row.get("AI Signal", ""))
    confidence = float(row.get("AI Confidence %", 0) or 0)
    level = row.get("Confidence Level", "")
    if signal == "HOLD":
        level = "NEUTRAL"
    return f"{confidence:.2f}% ({level})" if level else f"{confidence:.2f}%"


def clean_watchlist_for_display(df):
    if df is None or df.empty:
        return df
    display_df = df.copy()
    display_df["Confidence Display"] = display_df.apply(confidence_display_text, axis=1)
    preferred = [
        "Ticker", "Market", "Price", "Daily Change %", "AI Signal", "Confidence Display",
        "RSI", "MACD", "Final Score", "Technical Score", "News Score"
    ]
    existing = [column for column in preferred if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in existing and column != "AI Confidence %"]
    return display_df[existing + remaining]


def load_signal_history():
    try:
        if os.path.exists(SIGNAL_HISTORY_FILE) and os.path.getsize(SIGNAL_HISTORY_FILE) > 0:
            return pd.read_csv(SIGNAL_HISTORY_FILE)
    except Exception as error:
        print("Signal history load error:", error)
    return pd.DataFrame()


def log_signal_history_from_row(row, alert_status):
    try:
        history = load_signal_history()
        record = {
            "Time": now_text(include_seconds=True),
            "Ticker": row.get("Ticker", ""),
            "Market": row.get("Market", ""),
            "Signal": row.get("AI Signal", ""),
            "Confidence %": row.get("AI Confidence %", 0),
            "Price": row.get("Price", 0),
            "Final Score": row.get("Final Score", 0),
            "Alert Status": alert_status,
        }
        history = pd.concat([history, pd.DataFrame([record])], ignore_index=True).tail(1000)
        history.to_csv(SIGNAL_HISTORY_FILE, index=False)
    except Exception as error:
        print("Signal history save error:", error)


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
        "Timestamp": now_text(include_seconds=True),
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
        "Timestamp": now_text(include_seconds=True),
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

if "alert_history" not in st.session_state:
    st.session_state.alert_history = load_alert_history()

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

current_time = now_dt()
market_status = get_stock_market_status(current_time)

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

account_tab, open_trades_tab, closed_trades_tab, crypto_tab, stock_tab, scanner_tab, alerts_tab, backtest_tab, bot_status_tab, settings_tab = st.tabs([
    "Paper Account",
    "Open Trades",
    "Closed Trades",
    "Crypto",
    "Stocks",
    "AI Scanner",
    "Trade Notifications",
    "Backtesting",
    "Bot Status",
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
                    "Timestamp": now_text(include_seconds=True),
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
# v32 OPEN TRADES TAB
# ======================================================

with open_trades_tab:
    st.header("v32 Open Paper Trades")
    paper_trades_df = load_paper_trades_df()

    if paper_trades_df.empty:
        st.info("No v32 paper trades have been opened yet. New bot alerts will populate paper_trades.csv automatically.")
    else:
        open_df = paper_trades_df[paper_trades_df["status"].astype(str).isin(["OPEN", "TP1_HIT"])] if "status" in paper_trades_df.columns else pd.DataFrame()
        col1, col2, col3 = st.columns(3)
        col1.metric("Open Trades", len(open_df))
        col2.metric("TP1 Hit / Still Open", len(open_df[open_df["status"].astype(str) == "TP1_HIT"]) if not open_df.empty else 0)
        unrealized_pnl = pd.to_numeric(open_df.get("pnl_dollars", 0), errors="coerce").fillna(0).sum() if not open_df.empty else 0
        col3.metric("Open P/L", f"${unrealized_pnl:.2f}")

        if open_df.empty:
            st.info("No trades are currently open.")
        else:
            display_cols = [
                "ticker", "market", "signal", "entry_price", "current_price", "stop_loss",
                "tp1", "tp2", "confidence", "position_size", "status", "date_opened",
                "last_updated", "pnl_percent", "pnl_dollars", "risk_reward_2", "signal_rank", "quality_score"
            ]
            display_cols = [col for col in display_cols if col in open_df.columns]
            st.dataframe(open_df[display_cols], width="stretch")
            st.download_button(
                label="Download Open Trades CSV",
                data=open_df.to_csv(index=False),
                file_name="open_paper_trades.csv",
                mime="text/csv"
            )

# ======================================================
# v32 CLOSED TRADES TAB
# ======================================================

with closed_trades_tab:
    st.header("v32 Closed Paper Trades & Performance")
    paper_trades_df = load_paper_trades_df()
    equity_curve_df = load_paper_equity_df()

    if paper_trades_df.empty:
        st.info("No closed paper trades yet. Let the bot open and monitor trades first.")
    else:
        closed_df = paper_trades_df[paper_trades_df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])] if "status" in paper_trades_df.columns else pd.DataFrame()
        metrics = paper_trade_metrics(paper_trades_df)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Closed Trades", metrics["total_closed"])
        col2.metric("Win Rate", f"{metrics['win_rate']}%")
        col3.metric("Profit Factor", metrics["profit_factor"])
        col4.metric("Total P/L", f"${metrics['total_pnl']:.2f}")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Avg Winner", f"${metrics['average_winner']:.2f}")
        col2.metric("Avg Loser", f"${metrics['average_loser']:.2f}")
        col3.metric("Best Ticker", metrics["best_ticker"])
        col4.metric("Worst Ticker", metrics["worst_ticker"])

        st.subheader("Equity Curve")
        if not equity_curve_df.empty and "equity" in equity_curve_df.columns:
            st.line_chart(equity_curve_df.set_index("timestamp")["equity"] if "timestamp" in equity_curve_df.columns else equity_curve_df["equity"])
        else:
            st.info("Equity curve will appear after the bot monitor records paper trade outcomes.")

        st.subheader("Closed Trades")
        if closed_df.empty:
            st.info("No closed trades yet.")
        else:
            display_cols = [
                "ticker", "market", "signal", "result", "entry_price", "current_price",
                "pnl_percent", "pnl_dollars", "confidence", "date_opened", "date_closed",
                "risk_reward_2", "signal_rank", "quality_score"
            ]
            display_cols = [col for col in display_cols if col in closed_df.columns]
            st.dataframe(closed_df[display_cols], width="stretch")
            st.download_button(
                label="Download Closed Trades CSV",
                data=closed_df.to_csv(index=False),
                file_name="closed_paper_trades.csv",
                mime="text/csv"
            )

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
        col2.metric("AI Confidence", confidence_display_text(top_crypto))
        col3.metric("Signal", top_crypto["AI Signal"])

        st.write(create_ai_summary(top_crypto))
        st.dataframe(clean_watchlist_for_display(crypto_watchlist_df), width="stretch")

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
        col2.metric("AI Confidence", confidence_display_text(top_stock))
        col3.metric("Signal", top_stock["AI Signal"])

        st.write(create_ai_summary(top_stock))
        st.dataframe(clean_watchlist_for_display(stock_watchlist_df), width="stretch")

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
        col2.metric("AI Confidence", confidence_display_text(top_pick))
        col3.metric("Signal", top_pick["AI Signal"])

        st.subheader("AI Market Sentiment")
        col1, col2 = st.columns(2)
        col1.metric("Market Mood", market_sentiment)
        col2.metric("Average AI Confidence", f"{average_confidence:.2f}%")
        st.progress(max(0.0, min(float(average_confidence) / 100, 1.0)))

        scanner_tab1, scanner_tab2, scanner_tab3 = st.tabs([
            "Crypto Scanner",
            "Stock Scanner",
            "Filtered Scanner"
        ])

        with scanner_tab1:
            st.dataframe(
                clean_watchlist_for_display(watchlist_df[watchlist_df["Market"] == "Crypto"]),
                width="stretch"
            )

        with scanner_tab2:
            st.dataframe(
                clean_watchlist_for_display(watchlist_df[watchlist_df["Market"] == "Stock"]),
                width="stretch"
            )

        with scanner_tab3:
            signal_filter = st.selectbox(
                "Filter AI Signals",
                ["ALL", "STRONG BUY", "BUY", "HOLD", "STRONG SELL", "SELL"]
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

            st.dataframe(clean_watchlist_for_display(filtered_watchlist), width="stretch")

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
                                "Timestamp": now_text(include_seconds=True),
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
        if DASHBOARD_AUTO_SUMMARIES_ENABLED:
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

        if st.button("Send Buy/Sell Signal Alerts"):
            signal_rows = watchlist_df[watchlist_df["AI Signal"].isin(["STRONG BUY", "BUY", "STRONG SELL", "SELL"])]

            if signal_rows.empty:
                st.info("No buy or sell alerts right now.")
            else:
                sent_count = 0
                skipped_count = 0

                for _, row in signal_rows.iterrows():
                    alert_key = f"{row['Ticker']}_{row['AI Signal']}_{now_dt().strftime('%Y-%m-%d')}"

                    if alert_key in st.session_state.sent_signal_alerts:
                        skipped_count += 1
                        log_signal_history_from_row(row, "DUPLICATE_SKIPPED")
                        continue

                    sent = send_signal_embed(row)
                    if sent:
                        sent_count += 1
                        st.session_state.sent_signal_alerts.add(alert_key)
                        log_signal_history_from_row(row, "SENT")
                    else:
                        log_signal_history_from_row(row, "SEND_FAILED")
                    save_sent_signals(st.session_state.sent_signal_alerts)

                if sent_count:
                    st.success(f"Signal alerts sent: {sent_count}. Duplicates skipped: {skipped_count}.")
                else:
                    st.info(f"No new signal alerts sent. Duplicates skipped: {skipped_count}.")

# ======================================================
# TRADE NOTIFICATIONS TAB
# ======================================================

with alerts_tab:
    st.header("Trade Notifications Panel")

    alert_df = pd.DataFrame(st.session_state.alert_history)

    if alert_df.empty:
        st.info("No trade notifications logged yet.")
    else:
        col1, col2, col3 = st.columns(3)

        total_alerts = len(alert_df)
        buy_alerts = len(alert_df[alert_df["Signal"].astype(str).str.contains("BUY", na=False)])
        sell_alerts = len(alert_df[alert_df["Signal"].astype(str).str.contains("SELL", na=False)])

        col1.metric("Total Alerts", total_alerts)
        col2.metric("Buy Alerts", buy_alerts)
        col3.metric("Sell Alerts", sell_alerts)

        st.subheader("Recent Alerts")

        market_filter = st.selectbox(
            "Filter Market",
            ["ALL", "Crypto", "Stock"],
            key="alert_market_filter"
        )

        signal_filter = st.selectbox(
            "Filter Signal",
            ["ALL", "STRONG BUY", "BUY", "HOLD", "STRONG SELL", "SELL"],
            key="alert_signal_filter"
        )

        filtered_alerts = alert_df.copy()

        if market_filter != "ALL":
            filtered_alerts = filtered_alerts[filtered_alerts["Market"] == market_filter]

        if signal_filter != "ALL":
            filtered_alerts = filtered_alerts[filtered_alerts["Signal"] == signal_filter]

        st.dataframe(filtered_alerts, width="stretch")

        csv_alerts = filtered_alerts.to_csv(index=False)

        st.download_button(
            label="Download Alert History CSV",
            data=csv_alerts,
            file_name="trade_notifications.csv",
            mime="text/csv"
        )

        if st.button("Clear Trade Notifications"):
            st.session_state.alert_history = []
            save_alert_history(st.session_state.alert_history)
            st.success("Trade notifications cleared.")
            st.rerun()


    st.subheader("Signal History")
    signal_history_df = load_signal_history()

    if signal_history_df.empty:
        st.info("No signal history logged yet.")
    else:
        st.dataframe(signal_history_df.sort_index(ascending=False).head(250), width="stretch")
        st.download_button(
            label="Download Signal History CSV",
            data=signal_history_df.to_csv(index=False),
            file_name="signal_history.csv",
            mime="text/csv"
        )

# ======================================================
# BACKTESTING TAB
# ======================================================

with backtest_tab:
    st.header("Strategy Backtesting")

    backtest_ticker = st.selectbox("Choose ticker for backtest", ALL_TICKERS) if ALL_TICKERS else None

    backtest_strategy = st.selectbox(
        "Choose backtest strategy",
        ["RSI Strategy", "MACD Strategy", "Moving Average Strategy"]
    )

    if st.button("Run Backtest") and backtest_ticker:
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
# BOT STATUS TAB
# ======================================================

with bot_status_tab:
    st.header("Background Bot Status")

    bot_status = load_bot_status()

    if not bot_status:
        st.warning(
            "No bot_last_status.json file found yet. If your bot is running on Railway, "
            "set the same BOT_DATA_DIR / DASHBOARD_DATA_DIR volume path for both apps so the dashboard can read live bot status."
        )
    else:
        age_minutes = status_age_minutes(bot_status)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Bot Version", bot_status.get("bot_version", "Unknown"))
        col2.metric("Last Status", bot_status.get("timestamp", "Unknown"))
        col3.metric("Status Age", f"{age_minutes} min" if age_minutes is not None else "Unknown")
        col4.metric("Scanned", bot_status.get("scanned", 0))

        col5, col6, col7, col8 = st.columns(4)
        col5.metric("Candidates", bot_status.get("candidates", 0))
        col6.metric("Sent", bot_status.get("sent", 0))
        col7.metric("Ticker Errors", bot_status.get("ticker_errors", 0))
        col8.metric("Post Scan Errors", bot_status.get("post_scan_errors", 0))

        if bot_status.get("interrupted"):
            st.error("Last scan was interrupted.")
        elif int(bot_status.get("ticker_errors", 0) or 0) + int(bot_status.get("post_scan_errors", 0) or 0) > 0:
            st.warning("Last scan completed with warnings.")
        else:
            st.success("Last scan completed normally.")

        with st.expander("Raw bot status JSON"):
            st.json(bot_status)

    st.subheader("v30.6 Feature Compatibility Checklist")
    checklist = pd.DataFrame([
        {"System": "Market Regime Detection", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "AI Signal Ranking", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Position Sizing", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Trailing Stops", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Portfolio Exposure Controls", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Walk-Forward Backtesting", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Signal Outcome Tracking", "Dashboard Visibility": "Bot Status / Google Sheets", "Source of Truth": "Background bot"},
        {"System": "Dashboard Analytics", "Dashboard Visibility": "Google Sheets tab + local Bot Status", "Source of Truth": "Background bot"},
    ])
    st.dataframe(checklist, width="stretch")

    st.info(
        "Important: the dashboard's local scanner is still a lightweight visual scanner. "
        "Your v30.6+ background bot remains the source of truth for ranked alerts, exposure controls, trade plans, and advanced backtesting."
    )


# ======================================================
# SETTINGS TAB
# ======================================================

with settings_tab:
    st.header("Settings")
    st.caption(f"Running {APP_VERSION}")
    st.write("Dashboard data dir:", DATA_DIR)
    st.write("Dashboard timezone:", BOT_TIMEZONE)
    st.write("Discord message limit:", DISCORD_MESSAGE_LIMIT)

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
            latest_watchlist_df = build_watchlist(ALL_TICKERS)
            sent = send_market_summary_embed(latest_watchlist_df, "Crypto")
            st.success("Crypto summary embed test sent.") if sent else st.error("Crypto summary embed test failed.")

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
            latest_watchlist_df = build_watchlist(ALL_TICKERS)
            sent = send_market_summary_embed(latest_watchlist_df, "Stock")
            st.success("Stock summary embed test sent.") if sent else st.error("Stock summary embed test failed.")

    st.subheader("Manual News Scan")

    st.write("Automatic news newsletter enabled:", AUTO_NEWS_ALERTS_ENABLED)
    st.write("Yahoo/yfinance news enabled:", DASHBOARD_YFINANCE_NEWS_ENABLED)
    st.write("Dashboard news score enabled:", DASHBOARD_NEWS_SCORE_ENABLED)
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
    st.info("Background daily summaries should run from bot.py. Dashboard auto summaries are off by default to prevent duplicate Discord messages.")
    st.write("Dashboard auto summaries enabled:", DASHBOARD_AUTO_SUMMARIES_ENABLED)
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
    st.info("Background alerts should run from bot.py. Dashboard auto signal alerts are off by default to prevent duplicate Discord messages.")
    st.write("Dashboard auto signal alerts enabled:", AUTO_SIGNAL_ALERTS_ENABLED)
    st.write("Minimum confidence for auto signal alerts:", AUTO_SIGNAL_MIN_CONFIDENCE)
    st.write("Signal check interval minutes:", AUTO_SIGNAL_CHECK_INTERVAL_MINUTES)
    st.write("Next automatic signal check:", datetime.fromtimestamp(st.session_state.next_auto_signal_time).strftime("%Y-%m-%d %H:%M:%S"))
    st.write("Sent signal records today:", sorted(st.session_state.sent_signal_alerts))

    st.subheader("Automatic News Newsletter")
    st.info("Background news should run from bot.py. Dashboard auto news is off by default to prevent duplicate Discord messages.")
    st.write("Dashboard automatic news newsletter enabled:", AUTO_NEWS_ALERTS_ENABLED)
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
