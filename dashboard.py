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
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

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


def arrow_safe_df(df):
    """Make Streamlit/PyArrow display stable when a table mixes strings, booleans, and decimals."""
    if df is None or df.empty:
        return df
    safe = df.copy()
    for column in safe.columns:
        if safe[column].dtype == "object":
            safe[column] = safe[column].astype(str)
    return safe


# Global display guard: every st.dataframe call passes through arrow_safe_df.
# This prevents PyArrow warnings when metric tables mix values like 45.0%, True, and 1.5.
_ORIGINAL_ST_DATAFRAME = st.dataframe


def safe_streamlit_dataframe(data=None, *args, **kwargs):
    if isinstance(data, pd.DataFrame):
        data = arrow_safe_df(data)
    return _ORIGINAL_ST_DATAFRAME(data, *args, **kwargs)


st.dataframe = safe_streamlit_dataframe


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
BOT_STATUS_FILE = os.path.join(DATA_DIR, "bot_last_status.json")
SIGNAL_HISTORY_FILE = os.path.join(DATA_DIR, "signal_history.csv")
PAPER_TRADES_FILE = os.path.join(DATA_DIR, "paper_trades.csv")
PAPER_EQUITY_FILE = os.path.join(DATA_DIR, "paper_trade_equity_curve.csv")

# ======================================================
# SETTINGS
# ======================================================

APP_VERSION = "v32.21.2_yfinance_symbol_guard_dashboard"

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

# v32.18.1 Shared Status Sync dashboard settings.
# Lets the dashboard read bot status and paper trades from Google Sheets when
# bot and dashboard run in separate Railway projects.
GOOGLE_SHEETS_ENABLED = get_env_bool("GOOGLE_SHEETS_ENABLED", True)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
DASHBOARD_SHARED_STATUS_SYNC_ENABLED = get_env_bool("DASHBOARD_SHARED_STATUS_SYNC_ENABLED", True)
DASHBOARD_SHARED_STATUS_PREFER_GOOGLE = get_env_bool("DASHBOARD_SHARED_STATUS_PREFER_GOOGLE", True)

# v32.3 Paper Trade Quality dashboard settings.
# These mirror the v32.2 bot variables so the dashboard can show the active guardrails.
BOT_PAPER_TRADE_MAX_OPEN_TOTAL = get_env_int("BOT_PAPER_TRADE_MAX_OPEN_TOTAL", 10)
BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED = get_env_bool("BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED", True)
BOT_PAPER_TRADE_MIN_BACKTEST_PF = get_env_float("BOT_PAPER_TRADE_MIN_BACKTEST_PF", 1.0)
BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE = get_env_float("BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE", 50)
BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS = get_env_int("BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS", 20)
BOT_PAPER_TRADE_AVOID_TICKERS = get_env_list("BOT_PAPER_TRADE_AVOID_TICKERS", [])
BOT_SEND_PAPER_TRADE_SUMMARY = get_env_bool("BOT_SEND_PAPER_TRADE_SUMMARY", True)
BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS = get_env_float("BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS", 6)

# v32.5 Performance Gate settings.
# These are the pass/fail rules before moving to v33 3Commas paper automation.
BOT_PERFORMANCE_GATE_MIN_TRADES = get_env_int("BOT_PERFORMANCE_GATE_MIN_TRADES", 100)
BOT_PERFORMANCE_GATE_MIN_WIN_RATE = get_env_float("BOT_PERFORMANCE_GATE_MIN_WIN_RATE", 50)
BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR = get_env_float("BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR", 1.5)
BOT_PERFORMANCE_GATE_REQUIRE_POSITIVE_EQUITY = get_env_bool("BOT_PERFORMANCE_GATE_REQUIRE_POSITIVE_EQUITY", True)
BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT = get_env_float("BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT", 20)
BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY = get_env_float("BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY", 70)

# v32.6 Trade Intelligence settings.
# These controls prevent weak sample sizes from creating fake "best ticker" conclusions.
BOT_TRADE_INTELLIGENCE_MIN_SAMPLE = get_env_int("BOT_TRADE_INTELLIGENCE_MIN_SAMPLE", 5)
BOT_TRADE_INTELLIGENCE_STRONG_PF = get_env_float("BOT_TRADE_INTELLIGENCE_STRONG_PF", 1.5)
BOT_TRADE_INTELLIGENCE_STRONG_WR = get_env_float("BOT_TRADE_INTELLIGENCE_STRONG_WR", 50)

# v32.7 Adaptive Trade Filter settings.
# Dashboard-only intelligence layer. These do not execute trades; they recommend filters.
BOT_ADAPTIVE_FILTERS_MIN_SAMPLE = get_env_int("BOT_ADAPTIVE_FILTERS_MIN_SAMPLE", 5)
BOT_ADAPTIVE_AVOID_MAX_PF = get_env_float("BOT_ADAPTIVE_AVOID_MAX_PF", 1.0)
BOT_ADAPTIVE_AVOID_MAX_WR = get_env_float("BOT_ADAPTIVE_AVOID_MAX_WR", 45)
BOT_ADAPTIVE_FAVORITE_MIN_PF = get_env_float("BOT_ADAPTIVE_FAVORITE_MIN_PF", 1.5)
BOT_ADAPTIVE_FAVORITE_MIN_WR = get_env_float("BOT_ADAPTIVE_FAVORITE_MIN_WR", 55)
BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE = get_env_int("BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE", 5)
BOT_ADAPTIVE_REGIME_MIN_SAMPLE = get_env_int("BOT_ADAPTIVE_REGIME_MIN_SAMPLE", 5)

# v32.10.1 Setup Intelligence dashboard settings.
# These mirror the v32.10 bot analytics so the dashboard can show which setups are working.
BOT_SETUP_ANALYTICS_MIN_SAMPLE = get_env_int("BOT_SETUP_ANALYTICS_MIN_SAMPLE", 5)
BOT_SETUP_ANALYTICS_STRONG_PF = get_env_float("BOT_SETUP_ANALYTICS_STRONG_PF", 1.5)
BOT_SETUP_ANALYTICS_STRONG_WR = get_env_float("BOT_SETUP_ANALYTICS_STRONG_WR", 50)
BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS = get_env_int("BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS", 10)
BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE = get_env_int("BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE", 20)
BOT_DYNAMIC_CONFIDENCE_TARGET_PF = get_env_float("BOT_DYNAMIC_CONFIDENCE_TARGET_PF", 1.5)
BOT_DYNAMIC_CONFIDENCE_TARGET_WR = get_env_float("BOT_DYNAMIC_CONFIDENCE_TARGET_WR", 50)

# v32.12 Automation Readiness Center settings.
BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES = get_env_int("BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES", 100)
BOT_AUTOMATION_READINESS_TARGET_WR = get_env_float("BOT_AUTOMATION_READINESS_TARGET_WR", 50)
BOT_AUTOMATION_READINESS_TARGET_PF = get_env_float("BOT_AUTOMATION_READINESS_TARGET_PF", 1.5)
BOT_AUTOMATION_READINESS_TARGET_SCORE = get_env_float("BOT_AUTOMATION_READINESS_TARGET_SCORE", 80)
BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT = get_env_float("BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT", 20)
BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES = get_env_int("BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES", 1)

# v32.13 Trade Lifecycle Analytics dashboard settings.
BOT_TRADE_LIFECYCLE_MIN_SAMPLE = get_env_int("BOT_TRADE_LIFECYCLE_MIN_SAMPLE", 5)
BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS = get_env_float("BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS", 24)
BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS = get_env_float("BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS", 10)
BOT_TRADE_LIFECYCLE_STRONG_RETURN_PER_DAY = get_env_float("BOT_TRADE_LIFECYCLE_STRONG_RETURN_PER_DAY", 0.5)

# v32.14-v32.18 Outcome Intelligence dashboard settings.
BOT_OUTCOME_ATTRIBUTION_MIN_SAMPLE = get_env_int("BOT_OUTCOME_ATTRIBUTION_MIN_SAMPLE", 5)
BOT_SIGNAL_INTELLIGENCE_MIN_SAMPLE = get_env_int("BOT_SIGNAL_INTELLIGENCE_MIN_SAMPLE", 5)
BOT_CONFIDENCE_CALIBRATION_MIN_SAMPLE = get_env_int("BOT_CONFIDENCE_CALIBRATION_MIN_SAMPLE", 5)
BOT_REGIME_PERFORMANCE_MIN_SAMPLE = get_env_int("BOT_REGIME_PERFORMANCE_MIN_SAMPLE", 5)


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
    "AVAX-USD", "VET-USD", "ICP-USD", "ATOM-USD", "ALGO-USD", "XLM-USD",
    "LINK-USD", "ONDO-USD", "INJ-USD", "SEI-USD"
]

STOCK_TICKERS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD",
    "PLTR", "SPY", "QQQ"
]

# v32.21.2 Yahoo Finance symbol guard. Mirrors bot.py so dashboard scans
# do not waste time on tickers that repeatedly returned no yfinance data.
BOT_SKIP_UNSUPPORTED_TICKERS = get_env_bool("BOT_SKIP_UNSUPPORTED_TICKERS", True)
BOT_YFINANCE_DISABLED_TICKERS = get_env_list("BOT_YFINANCE_DISABLED_TICKERS", [
    "SUI-USD", "UNI-USD", "APT-USD", "TAO-USD", "RNDR-USD", "GRT-USD"
])


def is_yfinance_disabled_ticker(ticker):
    return BOT_SKIP_UNSUPPORTED_TICKERS and str(ticker or "").strip().upper() in set(BOT_YFINANCE_DISABLED_TICKERS)


def filter_yfinance_disabled_tickers(tickers):
    cleaned = get_env_list("__unused__", tickers) if False else list(tickers)
    if not BOT_SKIP_UNSUPPORTED_TICKERS:
        return cleaned
    return [str(ticker).strip().upper() for ticker in cleaned if str(ticker).strip().upper() not in set(BOT_YFINANCE_DISABLED_TICKERS)]


CRYPTO_TICKERS = filter_yfinance_disabled_tickers(get_env_list("BOT_CRYPTO_TICKERS", CRYPTO_TICKERS))
STOCK_TICKERS = filter_yfinance_disabled_tickers(get_env_list("BOT_STOCK_TICKERS", STOCK_TICKERS))
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
    if is_yfinance_disabled_ticker(ticker):
        return pd.DataFrame()

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


DASHBOARD_GOOGLE_CLIENT = None
DASHBOARD_GOOGLE_SPREADSHEET = None
DASHBOARD_GOOGLE_WORKSHEET_CACHE = {}


def dashboard_google_available():
    return bool(
        DASHBOARD_SHARED_STATUS_SYNC_ENABLED
        and GOOGLE_SHEETS_ENABLED
        and GOOGLE_SHEET_ID
        and GOOGLE_SERVICE_ACCOUNT_JSON
        and gspread is not None
        and Credentials is not None
    )


def get_dashboard_google_spreadsheet():
    global DASHBOARD_GOOGLE_CLIENT
    global DASHBOARD_GOOGLE_SPREADSHEET
    if not dashboard_google_available():
        return None
    if DASHBOARD_GOOGLE_SPREADSHEET is not None:
        return DASHBOARD_GOOGLE_SPREADSHEET
    try:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(info, scopes=scopes)
        DASHBOARD_GOOGLE_CLIENT = gspread.authorize(credentials)
        DASHBOARD_GOOGLE_SPREADSHEET = DASHBOARD_GOOGLE_CLIENT.open_by_key(GOOGLE_SHEET_ID)
        return DASHBOARD_GOOGLE_SPREADSHEET
    except Exception as error:
        print("Dashboard Google Sheets connection error:", error)
        DASHBOARD_GOOGLE_CLIENT = None
        DASHBOARD_GOOGLE_SPREADSHEET = None
        return None


@st.cache_data(ttl=60, show_spinner=False)
def _cached_google_worksheet_records(sheet_id, title, cache_buster_minute):
    # cache_buster_minute intentionally changes once per minute so the dashboard
    # stays fresh without hammering Google Sheets on every Streamlit rerun.
    spreadsheet = get_dashboard_google_spreadsheet()
    if spreadsheet is None:
        return []
    try:
        worksheet = DASHBOARD_GOOGLE_WORKSHEET_CACHE.get(title)
        if worksheet is None:
            worksheet = spreadsheet.worksheet(title)
            DASHBOARD_GOOGLE_WORKSHEET_CACHE[title] = worksheet
        return worksheet.get_all_records()
    except Exception as error:
        print(f"Dashboard Google worksheet load error for {title}: {error}")
        return []


def load_google_worksheet_records(title):
    if not dashboard_google_available():
        return []
    cache_buster_minute = int(time.time() // 60)
    return _cached_google_worksheet_records(GOOGLE_SHEET_ID, title, cache_buster_minute)


def load_shared_bot_status_from_google_sheets():
    records = load_google_worksheet_records("Shared Bot Status")
    if not records:
        return {}
    fallback = {}
    for row in records:
        metric = str(row.get("Metric", "")).strip()
        value = row.get("Value", "")
        if metric == "Status JSON":
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    parsed["_shared_status_source"] = "Google Sheets"
                    return parsed
            except Exception:
                pass
        if metric:
            fallback[metric] = value
    if not fallback:
        return {}
    return {
        "bot_version": fallback.get("Bot Version", "Unknown"),
        "timestamp": fallback.get("Timestamp", "Unknown"),
        "timestamp_utc": fallback.get("Timestamp UTC", ""),
        "uptime_minutes": fallback.get("Uptime Minutes", ""),
        "scanned": fallback.get("Scanned", 0),
        "candidates": fallback.get("Candidates", 0),
        "sent": fallback.get("Sent", 0),
        "skipped_duplicates": fallback.get("Skipped Duplicates", 0),
        "ticker_errors": fallback.get("Ticker Errors", 0),
        "post_scan_errors": fallback.get("Post Scan Errors", 0),
        "interrupted": str(fallback.get("Interrupted", "False")).lower() == "true",
        "paper_trade_file_diagnostics": {
            "paper_trades_file": fallback.get("Paper Trades File", ""),
            "paper_trades_file_exists": str(fallback.get("Paper Trades Exists", "False")).lower() == "true",
            "paper_trades_rows": fallback.get("Paper Trades Rows", 0),
            "paper_trades_open_rows": fallback.get("Paper Trades Open Rows", 0),
            "paper_trades_closed_rows": fallback.get("Paper Trades Closed Rows", 0),
            "paper_trades_tp1_rows": fallback.get("Paper Trades TP1 Rows", 0),
            "paper_trades_tickers_open": [item.strip() for item in str(fallback.get("Paper Trades Open Tickers", "")).split(",") if item.strip()],
        },
        "_shared_status_source": "Google Sheets",
    }


def load_shared_paper_trades_from_google_sheets():
    records = load_google_worksheet_records("Shared Paper Trades")
    if not records:
        return pd.DataFrame()
    return normalize_paper_trade_df(pd.DataFrame(records))


def load_shared_paper_equity_from_google_sheets():
    records = load_google_worksheet_records("Shared Paper Equity")
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def load_paper_trades_df():
    # v32.18.1: prefer Google Sheets when configured so separate Railway projects share evidence.
    if DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared = load_shared_paper_trades_from_google_sheets()
        if shared is not None and not shared.empty:
            return normalize_paper_trade_df(shared)
    try:
        if os.path.exists(PAPER_TRADES_FILE) and os.path.getsize(PAPER_TRADES_FILE) > 0:
            return normalize_paper_trade_df(pd.read_csv(PAPER_TRADES_FILE))
    except Exception as error:
        st.warning(f"Could not load paper_trades.csv: {error}")
    if not DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared = load_shared_paper_trades_from_google_sheets()
        if shared is not None and not shared.empty:
            return normalize_paper_trade_df(shared)
    return pd.DataFrame()


def load_paper_equity_df():
    # v32.18.1: prefer Google Sheets when configured so separate Railway projects share evidence.
    if DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared = load_shared_paper_equity_from_google_sheets()
        if shared is not None and not shared.empty:
            return shared
    try:
        if os.path.exists(PAPER_EQUITY_FILE) and os.path.getsize(PAPER_EQUITY_FILE) > 0:
            return pd.read_csv(PAPER_EQUITY_FILE)
    except Exception as error:
        st.warning(f"Could not load paper_trade_equity_curve.csv: {error}")
    if not DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared = load_shared_paper_equity_from_google_sheets()
        if shared is not None and not shared.empty:
            return shared
    return pd.DataFrame()


def dashboard_file_modified_text(file_path):
    try:
        if not os.path.exists(file_path):
            return "Missing"
        return datetime.fromtimestamp(os.path.getmtime(file_path)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as error:
        return f"Unavailable: {error}"


def dashboard_file_size_bytes(file_path):
    try:
        return os.path.getsize(file_path) if os.path.exists(file_path) else 0
    except Exception:
        return 0


def build_dashboard_file_diagnostics():
    trades_df = load_paper_trades_df()
    equity_df = load_paper_equity_df()
    diagnostics = {
        "Dashboard DATA_DIR": DATA_DIR,
        "Dashboard BOT_DATA_DIR env": os.getenv("BOT_DATA_DIR", ""),
        "Dashboard DASHBOARD_DATA_DIR env": os.getenv("DASHBOARD_DATA_DIR", ""),
        "paper_trades.csv path": PAPER_TRADES_FILE,
        "paper_trades.csv exists": os.path.exists(PAPER_TRADES_FILE),
        "paper_trades.csv size bytes": dashboard_file_size_bytes(PAPER_TRADES_FILE),
        "paper_trades.csv modified": dashboard_file_modified_text(PAPER_TRADES_FILE),
        "paper_trades.csv rows visible to dashboard": int(len(trades_df)) if trades_df is not None else 0,
        "paper_trade_equity_curve.csv path": PAPER_EQUITY_FILE,
        "paper_trade_equity_curve.csv exists": os.path.exists(PAPER_EQUITY_FILE),
        "paper_trade_equity_curve.csv size bytes": dashboard_file_size_bytes(PAPER_EQUITY_FILE),
        "paper_trade_equity_curve.csv modified": dashboard_file_modified_text(PAPER_EQUITY_FILE),
        "paper_trade_equity_curve.csv rows visible to dashboard": int(len(equity_df)) if equity_df is not None else 0,
    }
    if trades_df is not None and not trades_df.empty and "status" in trades_df.columns:
        status_series = trades_df["status"].astype(str)
        diagnostics["Open rows visible"] = int(status_series.isin(["OPEN", "TP1_HIT"]).sum())
        diagnostics["Closed rows visible"] = int(status_series.isin(["TP2_HIT", "STOPPED", "CLOSED"]).sum())
        diagnostics["Status counts visible"] = {str(k): int(v) for k, v in status_series.value_counts().to_dict().items()}
    else:
        diagnostics["Open rows visible"] = 0
        diagnostics["Closed rows visible"] = 0
        diagnostics["Status counts visible"] = {}
    return diagnostics


def normalize_dashboard_path(value):
    try:
        return os.path.normpath(str(value or "").strip())
    except Exception:
        return str(value or "").strip()


def bool_status_text(value):
    return "YES" if bool(value) else "NO"


def build_paper_trade_data_flow_diagnostics(bot_status):
    """Compare bot-written diagnostics with dashboard-visible files so data-flow issues are obvious."""
    bot_status = bot_status or {}
    bot_diag = bot_status.get("paper_trade_file_diagnostics", {}) or {}
    dashboard_diag = build_dashboard_file_diagnostics()

    bot_file = normalize_dashboard_path(bot_diag.get("paper_trades_file", ""))
    dashboard_file = normalize_dashboard_path(dashboard_diag.get("paper_trades.csv path", ""))
    bot_rows = safe_float_dashboard(bot_diag.get("paper_trades_rows", 0), 0)
    dashboard_rows = safe_float_dashboard(dashboard_diag.get("paper_trades.csv rows visible to dashboard", 0), 0)
    bot_open = safe_float_dashboard(bot_diag.get("paper_trades_open_rows", 0), 0)
    dashboard_open = safe_float_dashboard(dashboard_diag.get("Open rows visible", 0), 0)
    bot_closed = safe_float_dashboard(bot_diag.get("paper_trades_closed_rows", 0), 0)
    dashboard_closed = safe_float_dashboard(dashboard_diag.get("Closed rows visible", 0), 0)
    bot_tp1 = safe_float_dashboard(bot_diag.get("paper_trades_tp1_rows", 0), 0)

    has_bot_diag = bool(bot_diag)
    status_source = str(bot_status.get("_shared_status_source", "Local File"))
    using_shared_google = status_source == "Google Sheets"
    path_match = True if using_shared_google else bool(bot_file and dashboard_file and bot_file == dashboard_file)
    rows_match = bool(has_bot_diag and int(bot_rows) == int(dashboard_rows))
    open_match = bool(has_bot_diag and int(bot_open) == int(dashboard_open))
    closed_match = bool(has_bot_diag and int(bot_closed) == int(dashboard_closed))
    file_exists_match = bool(
        has_bot_diag
        and bool(bot_diag.get("paper_trades_file_exists")) == bool(dashboard_diag.get("paper_trades.csv exists"))
    )

    warnings = []
    if not has_bot_diag:
        warnings.append("Bot has not written paper-trade diagnostics yet. Let the bot complete one scan after deploy.")
    if has_bot_diag and not path_match:
        warnings.append("Bot and dashboard are not pointing at the same paper_trades.csv path.")
    if has_bot_diag and path_match and not rows_match:
        warnings.append("Bot row count and dashboard row count do not match. Check Google Sheets Shared Paper Trades sync or local shared volume settings.")
    if has_bot_diag and not bool(bot_diag.get("paper_trades_file_exists")):
        warnings.append("Bot does not see paper_trades.csv yet. This is normal before the first paper trade is created.")
    if has_bot_diag and bool(bot_diag.get("paper_trades_file_exists")) and int(bot_rows) == 0:
        warnings.append("paper_trades.csv exists but has 0 visible rows. Evidence collection has not started yet.")
    if has_bot_diag and int(bot_rows) > 0 and int(bot_open + bot_closed) == 0:
        warnings.append("Paper-trade rows exist, but no OPEN/TP1_HIT/TP2_HIT/STOPPED/CLOSED statuses were detected.")

    if not has_bot_diag:
        health = "WAITING FOR BOT DIAGNOSTICS"
    elif warnings and (not path_match or not rows_match):
        health = "DATA FLOW MISMATCH"
    elif int(bot_rows) == 0:
        health = "CONNECTED - WAITING FOR PAPER TRADES"
    else:
        health = "HEALTHY - DATA FLOW CONNECTED"

    summary = {
        "Health": health,
        "Status Source": status_source,
        "Bot Diagnostics Present": bool_status_text(has_bot_diag),
        "Path Match": bool_status_text(path_match),
        "File Exists Match": bool_status_text(file_exists_match),
        "Rows Match": bool_status_text(rows_match),
        "Open Rows Match": bool_status_text(open_match),
        "Closed Rows Match": bool_status_text(closed_match),
        "Bot Rows": int(bot_rows),
        "Dashboard Rows": int(dashboard_rows),
        "Bot Open Rows": int(bot_open),
        "Dashboard Open Rows": int(dashboard_open),
        "Bot Closed Rows": int(bot_closed),
        "Dashboard Closed Rows": int(dashboard_closed),
        "Bot TP1 Rows": int(bot_tp1),
        "Bot Open Tickers": ", ".join(bot_diag.get("paper_trades_tickers_open", []) or []) or "None",
        "Warnings": " | ".join(warnings) if warnings else "None",
    }

    rows = [
        {"Check": "Bot diagnostics present", "Bot": bool_status_text(has_bot_diag), "Dashboard": "Required", "Match": bool_status_text(has_bot_diag), "Meaning": "Bot wrote paper_trade_file_diagnostics into bot_last_status.json."},
        {"Check": "Data source", "Bot": status_source, "Dashboard": "Google Sheets" if using_shared_google else "Local File", "Match": "YES", "Meaning": "v32.18.1 supports separate Railway projects through Google Sheets."},
        {"Check": "paper_trades.csv path", "Bot": bot_file or "Missing", "Dashboard": dashboard_file or "Missing", "Match": bool_status_text(path_match), "Meaning": "Path match is required for local-volume mode; Google Sheets mode can bridge separate projects."},
        {"Check": "paper_trades.csv exists", "Bot": bool_status_text(bot_diag.get("paper_trades_file_exists")) if has_bot_diag else "Unknown", "Dashboard": bool_status_text(dashboard_diag.get("paper_trades.csv exists")), "Match": bool_status_text(file_exists_match), "Meaning": "Confirms both services can see the same trade file."},
        {"Check": "paper_trades.csv rows", "Bot": int(bot_rows), "Dashboard": int(dashboard_rows), "Match": bool_status_text(rows_match), "Meaning": "Main evidence counter. Rows should match."},
        {"Check": "Open rows", "Bot": int(bot_open), "Dashboard": int(dashboard_open), "Match": bool_status_text(open_match), "Meaning": "Active paper trades visible to both services."},
        {"Check": "Closed rows", "Bot": int(bot_closed), "Dashboard": int(dashboard_closed), "Match": bool_status_text(closed_match), "Meaning": "Completed evidence trades visible to both services."},
        {"Check": "TP1 rows", "Bot": int(bot_tp1), "Dashboard": dashboard_diag.get("Status counts visible", {}).get("TP1_HIT", 0), "Match": "INFO", "Meaning": "Trades that reached TP1 and are still being tracked."},
        {"Check": "Bot status counts", "Bot": json.dumps(bot_diag.get("paper_trades_status_counts", {}), sort_keys=True), "Dashboard": json.dumps(dashboard_diag.get("Status counts visible", {}), sort_keys=True), "Match": "INFO", "Meaning": "Quick status distribution comparison."},
    ]

    return summary, pd.DataFrame(rows), dashboard_diag, bot_diag, warnings


def load_signal_history_df():
    try:
        if os.path.exists(SIGNAL_HISTORY_FILE) and os.path.getsize(SIGNAL_HISTORY_FILE) > 0:
            return pd.read_csv(SIGNAL_HISTORY_FILE)
    except Exception as error:
        print("Signal history load error:", error)
    return pd.DataFrame()


def paper_trade_metrics(trades_df):
    trades_df = normalize_paper_trade_df(trades_df)
    if trades_df.empty or "status" not in trades_df.columns:
        return {"total_closed": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0, "best_ticker": "N/A", "worst_ticker": "N/A", "average_winner": 0, "average_loser": 0}
    closed = trades_df[trades_df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])]
    if closed.empty:
        return {"total_closed": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0, "best_ticker": "N/A", "worst_ticker": "N/A", "average_winner": 0, "average_loser": 0}
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


def confidence_display_text(row):
    signal = str(row.get("AI Signal", ""))
    confidence = float(row.get("AI Confidence %", 0) or 0)
    level = row.get("Confidence Level", "")
    if signal == "HOLD":
        level = "NEUTRAL"
        confidence = min(confidence, 50)
    return f"{confidence:.2f}% ({level})" if level else f"{confidence:.2f}%"


def clean_watchlist_for_display(df):
    if df is None or df.empty:
        return df
    display_df = df.copy()
    display_df["Confidence Display"] = display_df.apply(confidence_display_text, axis=1)
    preferred = ["Ticker", "Market", "Price", "Daily Change %", "AI Signal", "Confidence Display", "RSI", "MACD", "Final Score", "Technical Score", "News Score"]
    existing = [column for column in preferred if column in display_df.columns]
    remaining = [column for column in display_df.columns if column not in existing and column != "AI Confidence %"]
    return display_df[existing + remaining]


def decision_badge_from_trade(row):
    ticker = str(row.get("ticker", "")).upper()
    status = str(row.get("status", ""))
    quality = float(row.get("quality_score", 0) or 0)
    rr = float(row.get("risk_reward_2", 0) or 0)
    confidence = float(row.get("confidence", 0) or 0)
    if ticker in set(BOT_PAPER_TRADE_AVOID_TICKERS):
        return "AVOID"
    if status in ["STOPPED"]:
        return "AVOID"
    if quality >= 90 and rr >= 2 and confidence >= 80:
        return "TAKE / PRIORITY"
    if quality >= 75 and rr >= 1.5 and confidence >= 75:
        return "TAKE"
    if quality >= 60 or rr >= 1.2:
        return "WATCH"
    return "AVOID"


def build_decision_dashboard_df(trades_df):
    trades = add_quality_badges(trades_df)
    if trades is None or trades.empty:
        return pd.DataFrame()
    decision_df = trades.copy()
    decision_df["decision"] = decision_df.apply(decision_badge_from_trade, axis=1)
    decision_df["decision_score"] = (
        pd.to_numeric(decision_df.get("quality_score", 0), errors="coerce").fillna(0) * 0.55
        + pd.to_numeric(decision_df.get("confidence", 0), errors="coerce").fillna(0) * 0.30
        + (pd.to_numeric(decision_df.get("risk_reward_2", 0), errors="coerce").fillna(0).clip(0, 3) / 3 * 100) * 0.15
    ).round(2)
    if "ticker" in decision_df.columns:
        decision_df["avoid_list"] = decision_df["ticker"].astype(str).str.upper().isin(set(BOT_PAPER_TRADE_AVOID_TICKERS))
    else:
        decision_df["avoid_list"] = False
    return decision_df.sort_values(by="decision_score", ascending=False)


def calculate_equity_curve_stats(equity_df):
    if equity_df is None or equity_df.empty or "equity" not in equity_df.columns:
        return {
            "starting_equity": 0,
            "current_equity": 0,
            "equity_return_pct": 0,
            "max_drawdown_pct": 0,
            "positive_equity": False,
        }
    clean = equity_df.copy()
    clean["equity"] = pd.to_numeric(clean["equity"], errors="coerce")
    clean = clean.dropna(subset=["equity"])
    if clean.empty:
        return {
            "starting_equity": 0,
            "current_equity": 0,
            "equity_return_pct": 0,
            "max_drawdown_pct": 0,
            "positive_equity": False,
        }
    starting = float(clean["equity"].iloc[0])
    current = float(clean["equity"].iloc[-1])
    equity_return = ((current - starting) / starting * 100) if starting else 0
    rolling_high = clean["equity"].cummax()
    drawdown = ((clean["equity"] - rolling_high) / rolling_high * 100).fillna(0)
    max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0
    return {
        "starting_equity": round(starting, 2),
        "current_equity": round(current, 2),
        "equity_return_pct": round(equity_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "positive_equity": current > starting,
    }


def build_performance_gate_report(trades_df, equity_df):
    metrics = paper_trade_metrics(trades_df)
    quality = paper_quality_summary(trades_df)
    equity = calculate_equity_curve_stats(equity_df)

    checks = [
        {
            "Gate": "Minimum Closed Trades",
            "Required": BOT_PERFORMANCE_GATE_MIN_TRADES,
            "Current": metrics.get("total_closed", 0),
            "Passed": metrics.get("total_closed", 0) >= BOT_PERFORMANCE_GATE_MIN_TRADES,
            "Why It Matters": "Enough sample size before automation decisions.",
        },
        {
            "Gate": "Win Rate",
            "Required": f">= {BOT_PERFORMANCE_GATE_MIN_WIN_RATE}%",
            "Current": f"{metrics.get('win_rate', 0)}%",
            "Passed": metrics.get("win_rate", 0) >= BOT_PERFORMANCE_GATE_MIN_WIN_RATE,
            "Why It Matters": "Strategy must win more often than the minimum threshold.",
        },
        {
            "Gate": "Profit Factor",
            "Required": f">= {BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR}",
            "Current": metrics.get("profit_factor", 0),
            "Passed": metrics.get("profit_factor", 0) >= BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR,
            "Why It Matters": "Gross winners must meaningfully exceed gross losers.",
        },
        {
            "Gate": "Positive Equity Curve",
            "Required": "Yes" if BOT_PERFORMANCE_GATE_REQUIRE_POSITIVE_EQUITY else "Optional",
            "Current": "Yes" if equity.get("positive_equity") else "No",
            "Passed": equity.get("positive_equity") if BOT_PERFORMANCE_GATE_REQUIRE_POSITIVE_EQUITY else True,
            "Why It Matters": "The tracked strategy should be making money overall.",
        },
        {
            "Gate": "Max Drawdown",
            "Required": f"<= {BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT}%",
            "Current": f"{equity.get('max_drawdown_pct', 0)}%",
            "Passed": equity.get("max_drawdown_pct", 0) <= BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT,
            "Why It Matters": "Keeps risk controlled before automation.",
        },
        {
            "Gate": "Average Quality Score",
            "Required": f">= {BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY}",
            "Current": quality.get("avg_quality", 0),
            "Passed": quality.get("avg_quality", 0) >= BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY,
            "Why It Matters": "Confirms the bot is selecting higher-quality setups.",
        },
    ]

    checks_df = pd.DataFrame(checks)
    passed = int(checks_df["Passed"].sum()) if not checks_df.empty else 0
    total = len(checks_df)
    readiness_pct = round((passed / total) * 100, 2) if total else 0
    automation_ready = passed == total

    if automation_ready:
        recommendation = "READY FOR v33 3COMMAS PAPER AUTOMATION"
    elif metrics.get("total_closed", 0) < BOT_PERFORMANCE_GATE_MIN_TRADES:
        recommendation = "KEEP COLLECTING PAPER TRADES"
    elif metrics.get("profit_factor", 0) < BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR:
        recommendation = "IMPROVE FILTERS BEFORE AUTOMATION"
    elif not equity.get("positive_equity"):
        recommendation = "WAIT FOR POSITIVE EQUITY CURVE"
    else:
        recommendation = "CLOSE, BUT NOT READY YET"

    summary = {
        "readiness_pct": readiness_pct,
        "passed_checks": passed,
        "total_checks": total,
        "automation_ready": automation_ready,
        "recommendation": recommendation,
        **metrics,
        **equity,
        "avg_quality": quality.get("avg_quality", 0),
        "avg_rr": quality.get("avg_rr", 0),
        "avg_confidence": quality.get("avg_confidence", 0),
    }
    return checks_df, summary



# ======================================================
# v32.12 AUTOMATION READINESS CENTER HELPERS
# ======================================================

def readiness_points_dashboard(value, target, max_points, higher_is_better=True):
    value = safe_float_dashboard(value, 0)
    target = safe_float_dashboard(target, 0)
    max_points = safe_float_dashboard(max_points, 0)
    if max_points <= 0:
        return 0
    if target <= 0:
        return max_points if value > 0 else 0
    if higher_is_better:
        return round(max(0, min(max_points, (value / target) * max_points)), 2)
    if value <= target:
        return max_points
    if value <= 0:
        return max_points
    return round(max(0, min(max_points, (target / value) * max_points)), 2)


def automation_readiness_status_dashboard(score):
    score = safe_float_dashboard(score, 0)
    if score >= BOT_AUTOMATION_READINESS_TARGET_SCORE:
        return "READY FOR v33 PAPER AUTOMATION"
    if score >= 60:
        return "NEARLY READY - KEEP TESTING"
    if score >= 40:
        return "EARLY TESTING - NOT READY"
    return "NOT READY - COLLECT MORE DATA"


def build_automation_readiness_dashboard_report(trades_df, equity_df):
    trades_df = normalize_paper_trade_df(trades_df)
    metrics = paper_trade_metrics(trades_df)
    equity = calculate_equity_curve_stats(equity_df)
    setup_tables = build_setup_performance_tables(trades_df)
    setup_perf = setup_tables.get("setup_perf", pd.DataFrame())
    confidence_df, confidence_recommendation = build_dynamic_confidence_dashboard(trades_df)

    if setup_perf is None or setup_perf.empty:
        strong_count = 0
        weak_count = 0
        best_strategy = "N/A"
        weak_strategy = "N/A"
    else:
        reliable = setup_perf[setup_perf["Trades"] >= BOT_SETUP_ANALYTICS_MIN_SAMPLE].copy()
        strong = reliable[(reliable["Profit Factor"] >= BOT_SETUP_ANALYTICS_STRONG_PF) & (reliable["Win Rate %"] >= BOT_SETUP_ANALYTICS_STRONG_WR)]
        weak = reliable[(reliable["Profit Factor"] <= BOT_ADAPTIVE_AVOID_MAX_PF) | (reliable["Win Rate %"] <= BOT_ADAPTIVE_AVOID_MAX_WR)]
        strong_count = len(strong)
        weak_count = len(weak)
        best_strategy = f"{strong.iloc[0]['Group']} | PF {strong.iloc[0]['Profit Factor']} | WR {strong.iloc[0]['Win Rate %']}%" if not strong.empty else "No strong setup yet"
        weak_strategy = f"{weak.iloc[0]['Group']} | PF {weak.iloc[0]['Profit Factor']} | WR {weak.iloc[0]['Win Rate %']}%" if not weak.empty else "No confirmed weak setup"

    closed_points = readiness_points_dashboard(metrics.get("total_closed", 0), BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES, 20)
    wr_points = readiness_points_dashboard(metrics.get("win_rate", 0), BOT_AUTOMATION_READINESS_TARGET_WR, 20)
    pf_points = readiness_points_dashboard(metrics.get("profit_factor", 0), BOT_AUTOMATION_READINESS_TARGET_PF, 20)
    equity_points = 15 if equity.get("positive_equity") else 0
    drawdown_points = readiness_points_dashboard(equity.get("max_drawdown_pct", 0), BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT, 10, higher_is_better=False)
    strategy_points = 10 if strong_count >= BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES and weak_count == 0 else 7 if strong_count >= BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES else 3 if setup_perf is not None and not setup_perf.empty else 0
    confidence_points = 5 if not confidence_df.empty else 0

    score = round(closed_points + wr_points + pf_points + equity_points + drawdown_points + strategy_points + confidence_points, 2)
    status = automation_readiness_status_dashboard(score)

    blockers = []
    if metrics.get("total_closed", 0) < BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES:
        blockers.append(f"Need {BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES - metrics.get('total_closed', 0)} more closed paper trades.")
    if metrics.get("win_rate", 0) < BOT_AUTOMATION_READINESS_TARGET_WR:
        blockers.append("Win rate is below target.")
    if metrics.get("profit_factor", 0) < BOT_AUTOMATION_READINESS_TARGET_PF:
        blockers.append("Profit factor is below target.")
    if not equity.get("positive_equity"):
        blockers.append("Equity curve is not positive yet.")
    if weak_count > 0:
        blockers.append(f"{weak_count} weak setup(s) should not be automated.")
    if strong_count < BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES:
        blockers.append("Need at least one strong setup before v33.")

    recommendation = "READY: Begin v33 3Commas paper automation planning." if score >= BOT_AUTOMATION_READINESS_TARGET_SCORE and not blockers else "NOT READY: " + " ".join(blockers[:4]) if blockers else "NEARLY READY: Continue collecting paper-trade evidence."

    checks = pd.DataFrame([
        {"Gate": "Automation Readiness Score", "Current": score, "Target": BOT_AUTOMATION_READINESS_TARGET_SCORE, "Passed": score >= BOT_AUTOMATION_READINESS_TARGET_SCORE, "Score Contribution": score, "Notes": status},
        {"Gate": "Closed Paper Trades", "Current": metrics.get("total_closed", 0), "Target": BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES, "Passed": metrics.get("total_closed", 0) >= BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES, "Score Contribution": closed_points, "Notes": "Sample size before automation."},
        {"Gate": "Win Rate", "Current": f"{metrics.get('win_rate', 0)}%", "Target": f">= {BOT_AUTOMATION_READINESS_TARGET_WR}%", "Passed": metrics.get("win_rate", 0) >= BOT_AUTOMATION_READINESS_TARGET_WR, "Score Contribution": wr_points, "Notes": "Paper-trade win rate."},
        {"Gate": "Profit Factor", "Current": metrics.get("profit_factor", 0), "Target": f">= {BOT_AUTOMATION_READINESS_TARGET_PF}", "Passed": metrics.get("profit_factor", 0) >= BOT_AUTOMATION_READINESS_TARGET_PF, "Score Contribution": pf_points, "Notes": "Gross winners vs gross losers."},
        {"Gate": "Positive Equity Curve", "Current": "YES" if equity.get("positive_equity") else "NO", "Target": "YES", "Passed": equity.get("positive_equity"), "Score Contribution": equity_points, "Notes": f"Return {equity.get('equity_return_pct', 0)}%"},
        {"Gate": "Max Drawdown", "Current": f"{equity.get('max_drawdown_pct', 0)}%", "Target": f"<= {BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT}%", "Passed": equity.get("max_drawdown_pct", 0) <= BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT, "Score Contribution": drawdown_points, "Notes": "Risk control before automation."},
        {"Gate": "Strong Setups", "Current": strong_count, "Target": BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES, "Passed": strong_count >= BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES, "Score Contribution": strategy_points, "Notes": best_strategy},
        {"Gate": "Weak / Do-Not-Automate Setups", "Current": weak_count, "Target": 0, "Passed": weak_count == 0, "Score Contribution": 0 if weak_count else 5, "Notes": weak_strategy},
        {"Gate": "Dynamic Confidence", "Current": "Available" if not confidence_df.empty else "Needs Data", "Target": "Recommendation", "Passed": not confidence_df.empty, "Score Contribution": confidence_points, "Notes": confidence_recommendation},
    ])

    return {
        "score": score,
        "status": status,
        "recommendation": recommendation,
        "checks": checks,
        "metrics": metrics,
        "equity": equity,
        "strong_count": strong_count,
        "weak_count": weak_count,
        "best_strategy": best_strategy,
        "weak_strategy": weak_strategy,
        "confidence_recommendation": confidence_recommendation,
    }



# ======================================================
# v32.13 TRADE LIFECYCLE ANALYTICS HELPERS
# ======================================================

def parse_trade_datetime_dashboard(value):
    try:
        text = str(value or "").strip()
        if not text or text.lower() in ["nan", "none"]:
            return None
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(ZoneInfo(BOT_TIMEZONE)).replace(tzinfo=None)
            return parsed
        except Exception:
            return None
    except Exception:
        return None


def lifecycle_hours_between_dashboard(start_value, end_value=None):
    start = parse_trade_datetime_dashboard(start_value)
    if start is None:
        return 0
    end = parse_trade_datetime_dashboard(end_value) if end_value else now_dt().replace(tzinfo=None)
    if end is None:
        return 0
    return round(max(0, (end - start).total_seconds() / 3600), 2)


def lifecycle_avg_dashboard(series):
    values = pd.to_numeric(series, errors="coerce").fillna(0)
    values = values[values > 0]
    return round(float(values.mean()), 2) if not values.empty else 0


def enrich_lifecycle_trades_dashboard(df):
    trades = normalize_paper_trade_df(df)
    if trades.empty:
        return trades
    for column in ["date_tp1", "date_tp2", "date_stopped", "hours_open", "days_open", "hours_to_tp1", "hours_to_tp2", "hours_to_stop", "lifecycle_stage"]:
        if column not in trades.columns:
            trades[column] = "" if column.startswith("date_") or column == "lifecycle_stage" else 0
    for idx, row in trades.iterrows():
        status = str(row.get("status", "OPEN"))
        opened = row.get("date_opened", "")
        end_time = row.get("date_closed", "") if status in ["TP2_HIT", "STOPPED", "CLOSED"] else now_text()
        hours_open = safe_float_dashboard(row.get("hours_open", 0), 0)
        if hours_open <= 0:
            hours_open = lifecycle_hours_between_dashboard(opened, end_time)
            trades.at[idx, "hours_open"] = hours_open
            trades.at[idx, "days_open"] = round(hours_open / 24, 2)
        if safe_float_dashboard(row.get("hours_to_tp1", 0), 0) <= 0 and str(row.get("date_tp1", "")).strip():
            trades.at[idx, "hours_to_tp1"] = lifecycle_hours_between_dashboard(opened, row.get("date_tp1", ""))
        if safe_float_dashboard(row.get("hours_to_tp2", 0), 0) <= 0 and str(row.get("date_tp2", "")).strip():
            trades.at[idx, "hours_to_tp2"] = lifecycle_hours_between_dashboard(opened, row.get("date_tp2", ""))
        if safe_float_dashboard(row.get("hours_to_stop", 0), 0) <= 0 and str(row.get("date_stopped", "")).strip():
            trades.at[idx, "hours_to_stop"] = lifecycle_hours_between_dashboard(opened, row.get("date_stopped", ""))
        if not str(row.get("lifecycle_stage", "")).strip() or str(row.get("lifecycle_stage", "")).lower() == "nan":
            trades.at[idx, "lifecycle_stage"] = "TP2_CLOSED" if status == "TP2_HIT" else "STOP_CLOSED" if status == "STOPPED" else "TP1_OPEN" if status == "TP1_HIT" else status
    return trades


def build_trade_lifecycle_dashboard_report(df):
    trades = enrich_lifecycle_trades_dashboard(df)
    if trades.empty:
        return {"summary": {}, "setup_df": pd.DataFrame(), "recent_df": pd.DataFrame(), "recommendations": ["No paper trades yet. Lifecycle analytics will populate after trades open and close."]}
    closed = trades[trades["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])] if "status" in trades.columns else pd.DataFrame()
    open_df = trades[trades["status"].astype(str).isin(["OPEN", "TP1_HIT"])] if "status" in trades.columns else pd.DataFrame()
    tp1_df = trades[pd.to_numeric(trades.get("hours_to_tp1", 0), errors="coerce").fillna(0) > 0]
    tp2_df = closed[closed["status"].astype(str) == "TP2_HIT"] if not closed.empty else pd.DataFrame()
    stop_df = closed[closed["status"].astype(str) == "STOPPED"] if not closed.empty else pd.DataFrame()
    hours_open = pd.to_numeric(closed.get("hours_open", 0), errors="coerce").fillna(0) if not closed.empty else pd.Series(dtype=float)
    pnl_pct = pd.to_numeric(closed.get("pnl_percent", 0), errors="coerce").fillna(0) if not closed.empty else pd.Series(dtype=float)
    avg_return_per_day = 0
    if not closed.empty:
        days = hours_open.replace(0, pd.NA) / 24
        rpd = pd.to_numeric(pnl_pct / days, errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
        avg_return_per_day = round(float(rpd.mean()), 2) if not rpd.empty else 0
    summary = {
        "total_trades": len(trades),
        "open_trades": len(open_df),
        "closed_trades": len(closed),
        "tp1_hits": len(tp1_df),
        "tp2_hits": len(tp2_df),
        "stop_hits": len(stop_df),
        "avg_hours_open": lifecycle_avg_dashboard(hours_open),
        "avg_days_open": round(lifecycle_avg_dashboard(hours_open) / 24, 2) if lifecycle_avg_dashboard(hours_open) else 0,
        "avg_hours_to_tp1": lifecycle_avg_dashboard(tp1_df.get("hours_to_tp1", pd.Series(dtype=float))) if not tp1_df.empty else 0,
        "avg_hours_to_tp2": lifecycle_avg_dashboard(tp2_df.get("hours_to_tp2", pd.Series(dtype=float))) if not tp2_df.empty else 0,
        "avg_hours_to_stop": lifecycle_avg_dashboard(stop_df.get("hours_to_stop", pd.Series(dtype=float))) if not stop_df.empty else 0,
        "avg_return_per_day": avg_return_per_day,
        "slow_closed_count": int((hours_open > BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS * 24).sum()) if not closed.empty else 0,
        "fast_tp1_count": int((pd.to_numeric(tp1_df.get("hours_to_tp1", 0), errors="coerce").fillna(0) <= BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS).sum()) if not tp1_df.empty else 0,
    }
    setup_rows = []
    if not closed.empty and "setup_name" in closed.columns:
        for setup_name, group in closed.groupby("setup_name"):
            group_hours = pd.to_numeric(group.get("hours_open", 0), errors="coerce").fillna(0)
            group_pnl = pd.to_numeric(group.get("pnl_percent", 0), errors="coerce").fillna(0)
            days = group_hours.replace(0, pd.NA) / 24
            rpd = pd.to_numeric(group_pnl / days, errors="coerce").replace([float("inf"), float("-inf")], pd.NA).dropna()
            wins = len(group_pnl[group_pnl > 0])
            trade_count = len(group)
            setup_rows.append({
                "Setup Name": setup_name,
                "Trades": trade_count,
                "Wins": wins,
                "Losses": trade_count - wins,
                "Win Rate %": round((wins / trade_count) * 100, 2) if trade_count else 0,
                "Avg Hours Open": lifecycle_avg_dashboard(group_hours),
                "Avg Days Open": round(lifecycle_avg_dashboard(group_hours) / 24, 2) if lifecycle_avg_dashboard(group_hours) else 0,
                "Avg Return %": round(float(group_pnl.mean()), 2) if trade_count else 0,
                "Avg Return / Day %": round(float(rpd.mean()), 2) if not rpd.empty else 0,
                "Avg Hours To TP1": lifecycle_avg_dashboard(group.get("hours_to_tp1", pd.Series(dtype=float))),
                "Avg Hours To TP2": lifecycle_avg_dashboard(group.get("hours_to_tp2", pd.Series(dtype=float))),
                "Avg Hours To Stop": lifecycle_avg_dashboard(group.get("hours_to_stop", pd.Series(dtype=float))),
                "Sample Status": "Reliable" if trade_count >= BOT_TRADE_LIFECYCLE_MIN_SAMPLE else "Needs More Data",
            })
    setup_df = pd.DataFrame(setup_rows)
    if not setup_df.empty:
        setup_df = setup_df.sort_values(by=["Avg Return / Day %", "Win Rate %"], ascending=False)
    recommendations = []
    if summary["closed_trades"] < BOT_TRADE_LIFECYCLE_MIN_SAMPLE:
        recommendations.append(f"Collect more closed trades before judging lifecycle efficiency ({summary['closed_trades']}/{BOT_TRADE_LIFECYCLE_MIN_SAMPLE}).")
    if summary["avg_hours_to_tp1"] and summary["avg_hours_to_tp1"] <= BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS:
        recommendations.append(f"TP1 speed is healthy: average TP1 hit in {summary['avg_hours_to_tp1']} hours.")
    if summary["slow_closed_count"] > 0:
        recommendations.append(f"Review {summary['slow_closed_count']} slow closed trade(s) over {BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS} days.")
    strong = setup_df[(setup_df["Trades"] >= BOT_TRADE_LIFECYCLE_MIN_SAMPLE) & (setup_df["Avg Return / Day %"] >= BOT_TRADE_LIFECYCLE_STRONG_RETURN_PER_DAY)] if not setup_df.empty else pd.DataFrame()
    if not strong.empty:
        top = strong.iloc[0]
        recommendations.append(f"Most capital-efficient setup: {top['Setup Name']} | {top['Avg Return / Day %']}%/day | WR {top['Win Rate %']}%.")
    if not recommendations:
        recommendations.append("Lifecycle data is not decisive yet. Keep collecting outcomes.")
    recent_cols = [col for col in ["ticker", "signal", "status", "setup_name", "date_opened", "date_tp1", "date_tp2", "date_stopped", "date_closed", "hours_open", "hours_to_tp1", "hours_to_tp2", "hours_to_stop", "pnl_percent", "pnl_dollars"] if col in trades.columns]
    recent_df = trades.sort_values(by="last_updated", ascending=False).head(25)[recent_cols] if "last_updated" in trades.columns and recent_cols else trades.head(25)
    return {"summary": summary, "setup_df": setup_df, "recent_df": recent_df, "recommendations": recommendations[:8]}

def performance_gate_color(readiness_pct):
    try:
        readiness_pct = float(readiness_pct)
    except Exception:
        readiness_pct = 0
    if readiness_pct >= 100:
        return "🟢"
    if readiness_pct >= 70:
        return "🟡"
    return "🔴"


def closed_paper_trades(df):
    trades = normalize_paper_trade_df(df)
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame()
    return trades[trades["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])].copy()


def profit_factor_from_pnl(pnl_series):
    pnl = pd.to_numeric(pnl_series, errors="coerce").fillna(0)
    gross_wins = pnl[pnl > 0].sum()
    gross_losses = abs(pnl[pnl < 0].sum())
    if gross_losses > 0:
        return round(gross_wins / gross_losses, 2)
    if gross_wins > 0:
        return round(gross_wins, 2)
    return 0


def win_rate_from_pnl(pnl_series):
    pnl = pd.to_numeric(pnl_series, errors="coerce").fillna(0)
    if len(pnl) == 0:
        return 0
    return round((len(pnl[pnl > 0]) / len(pnl)) * 100, 2)


def build_group_performance(df, group_col, min_sample=1):
    closed = closed_paper_trades(df)
    if closed.empty or group_col not in closed.columns:
        return pd.DataFrame()

    rows = []
    for key, group in closed.groupby(group_col):
        pnl = pd.to_numeric(group.get("pnl_dollars", 0), errors="coerce").fillna(0)
        pnl_pct = pd.to_numeric(group.get("pnl_percent", 0), errors="coerce").fillna(0)
        trade_count = len(group)
        rows.append({
            "Group": key,
            "Trades": trade_count,
            "Win Rate %": win_rate_from_pnl(pnl),
            "Profit Factor": profit_factor_from_pnl(pnl),
            "Total P/L $": round(pnl.sum(), 2),
            "Avg P/L $": round(pnl.mean(), 2) if trade_count else 0,
            "Avg Return %": round(pnl_pct.mean(), 2) if trade_count else 0,
            "Avg Confidence": round(pd.to_numeric(group.get("confidence", 0), errors="coerce").fillna(0).mean(), 2),
            "Avg Quality": round(pd.to_numeric(group.get("quality_score", 0), errors="coerce").fillna(0).mean(), 2),
            "Sample Status": "Reliable" if trade_count >= min_sample else "Needs More Data",
        })

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values(by=["Profit Factor", "Win Rate %", "Total P/L $"], ascending=False)


def direction_from_signal(signal):
    signal = str(signal or "").upper()
    if "BUY" in signal:
        return "LONG"
    if "SELL" in signal:
        return "SHORT"
    return "UNKNOWN"


def confidence_bucket(value):
    confidence = float(value or 0)
    if confidence >= 90:
        return "90-100%"
    if confidence >= 80:
        return "80-89%"
    if confidence >= 70:
        return "70-79%"
    if confidence >= 60:
        return "60-69%"
    return "<60%"


def classify_market_condition(row):
    text = " ".join([
        str(row.get("notes", "")),
        str(row.get("market_regime", "")) if "market_regime" in row else "",
        str(row.get("risk_mode", "")) if "risk_mode" in row else "",
    ]).lower()
    if any(word in text for word in ["bull", "risk-on", "constructive"]):
        return "Bull / Risk-On"
    if any(word in text for word in ["bear", "risk-off", "defensive"]):
        return "Bear / Risk-Off"
    if any(word in text for word in ["sideways", "neutral", "mixed", "volatile"]):
        return "Sideways / Neutral"
    return "Unknown"


def enrich_trade_intelligence_df(df):
    trades = normalize_paper_trade_df(df)
    if trades.empty:
        return trades
    if "signal" in trades.columns:
        trades["direction"] = trades["signal"].apply(direction_from_signal)
    else:
        trades["direction"] = "UNKNOWN"
    if "confidence" in trades.columns:
        trades["confidence_bucket"] = trades["confidence"].apply(confidence_bucket)
    else:
        trades["confidence_bucket"] = "Unknown"
    trades["market_condition"] = trades.apply(classify_market_condition, axis=1)
    return trades


def build_trade_intelligence_tables(df):
    trades = enrich_trade_intelligence_df(df)
    closed = closed_paper_trades(trades)

    ticker_perf = build_group_performance(trades, "ticker", BOT_TRADE_INTELLIGENCE_MIN_SAMPLE)
    direction_perf = build_group_performance(trades, "direction", 1)
    confidence_perf = build_group_performance(trades, "confidence_bucket", 1)
    market_perf = build_group_performance(trades, "market_condition", 1)

    if not ticker_perf.empty:
        best_tickers = ticker_perf[ticker_perf["Sample Status"] == "Reliable"].head(10)
        worst_tickers = ticker_perf[ticker_perf["Sample Status"] == "Reliable"].sort_values(
            by=["Profit Factor", "Win Rate %", "Total P/L $"],
            ascending=True
        ).head(10)
        needs_more_data = ticker_perf[ticker_perf["Sample Status"] != "Reliable"].head(10)
    else:
        best_tickers = pd.DataFrame()
        worst_tickers = pd.DataFrame()
        needs_more_data = pd.DataFrame()

    if not closed.empty:
        scorecard_cols = [
            "ticker", "market", "signal", "direction", "confidence_bucket",
            "pnl_percent", "pnl_dollars", "confidence", "risk_reward_2",
            "quality_score", "status", "date_opened", "date_closed"
        ]
        scorecard_cols = [col for col in scorecard_cols if col in closed.columns]
        top_trades = closed.sort_values(by="pnl_dollars", ascending=False).head(10)[scorecard_cols]
        worst_trades = closed.sort_values(by="pnl_dollars", ascending=True).head(10)[scorecard_cols]
    else:
        top_trades = pd.DataFrame()
        worst_trades = pd.DataFrame()

    return {
        "trades": trades,
        "closed": closed,
        "ticker_perf": ticker_perf,
        "best_tickers": best_tickers,
        "worst_tickers": worst_tickers,
        "needs_more_data": needs_more_data,
        "direction_perf": direction_perf,
        "confidence_perf": confidence_perf,
        "market_perf": market_perf,
        "top_trades": top_trades,
        "worst_trades": worst_trades,
    }


def build_trade_intelligence_recommendations(tables):
    recs = []
    best = tables.get("best_tickers", pd.DataFrame())
    worst = tables.get("worst_tickers", pd.DataFrame())
    confidence = tables.get("confidence_perf", pd.DataFrame())
    direction = tables.get("direction_perf", pd.DataFrame())

    if not best.empty:
        leaders = best[
            (best["Profit Factor"] >= BOT_TRADE_INTELLIGENCE_STRONG_PF)
            & (best["Win Rate %"] >= BOT_TRADE_INTELLIGENCE_STRONG_WR)
        ].head(3)
        for _, row in leaders.iterrows():
            recs.append(f"✅ Trade {row['Group']} more cautiously when it matches quality filters: PF {row['Profit Factor']} | WR {row['Win Rate %']}%.")

    if not worst.empty:
        laggards = worst[
            (worst["Profit Factor"] < 1)
            | (worst["Win Rate %"] < BOT_TRADE_INTELLIGENCE_STRONG_WR)
        ].head(3)
        for _, row in laggards.iterrows():
            recs.append(f"⚠️ Reduce or avoid {row['Group']} until performance improves: PF {row['Profit Factor']} | WR {row['Win Rate %']}%.")

    if not confidence.empty:
        conf_sorted = confidence.sort_values(by="Group", ascending=False)
        weak_conf = conf_sorted[(conf_sorted["Profit Factor"] < 1) & (conf_sorted["Trades"] >= BOT_TRADE_INTELLIGENCE_MIN_SAMPLE)]
        if not weak_conf.empty:
            row = weak_conf.iloc[0]
            recs.append(f"⚠️ Confidence bucket {row['Group']} is underperforming with PF {row['Profit Factor']}; consider raising minimum confidence.")

    if not direction.empty and "Group" in direction.columns:
        for _, row in direction.iterrows():
            if row["Trades"] >= BOT_TRADE_INTELLIGENCE_MIN_SAMPLE and row["Profit Factor"] < 1:
                recs.append(f"⚠️ {row['Group']} trades are weak right now: PF {row['Profit Factor']} | WR {row['Win Rate %']}%.")

    if not recs:
        recs.append("Collect more closed paper trades before making major strategy changes.")
    return recs[:8]





def adaptive_action_from_perf(row):
    """Classify a ticker/setup based on actual closed paper-trade performance."""
    trades = int(row.get("Trades", 0) or 0)
    pf = float(row.get("Profit Factor", 0) or 0)
    wr = float(row.get("Win Rate %", 0) or 0)

    if trades < BOT_ADAPTIVE_FILTERS_MIN_SAMPLE:
        return "NEEDS MORE DATA"
    if pf <= BOT_ADAPTIVE_AVOID_MAX_PF or wr <= BOT_ADAPTIVE_AVOID_MAX_WR:
        return "AUTO-AVOID"
    if pf >= BOT_ADAPTIVE_FAVORITE_MIN_PF and wr >= BOT_ADAPTIVE_FAVORITE_MIN_WR:
        return "AUTO-FAVORITE"
    return "NEUTRAL"


def build_adaptive_ticker_filters(df):
    """Build auto-avoid and auto-favorite recommendations from closed paper trades."""
    tables = build_trade_intelligence_tables(df)
    ticker_perf = tables.get("ticker_perf", pd.DataFrame())

    if ticker_perf is None or ticker_perf.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    adaptive = ticker_perf.copy()
    adaptive["Adaptive Action"] = adaptive.apply(adaptive_action_from_perf, axis=1)
    adaptive["Suggested Variable"] = adaptive.apply(
        lambda row: (
            "Add to BOT_PAPER_TRADE_AVOID_TICKERS"
            if row["Adaptive Action"] == "AUTO-AVOID"
            else "Consider priority watchlist"
            if row["Adaptive Action"] == "AUTO-FAVORITE"
            else "No variable change yet"
        ),
        axis=1
    )

    avoid_df = adaptive[adaptive["Adaptive Action"] == "AUTO-AVOID"].copy()
    favorite_df = adaptive[adaptive["Adaptive Action"] == "AUTO-FAVORITE"].copy()

    return adaptive, avoid_df, favorite_df


def build_confidence_optimization(df):
    tables = build_trade_intelligence_tables(df)
    confidence = tables.get("confidence_perf", pd.DataFrame())

    if confidence is None or confidence.empty:
        return pd.DataFrame(), "Collect more confidence-bucket data before changing confidence thresholds."

    out = confidence.copy()
    out["Adaptive Action"] = out.apply(
        lambda row: (
            "WEAK - CONSIDER RAISING MIN CONFIDENCE"
            if int(row.get("Trades", 0) or 0) >= BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE
            and float(row.get("Profit Factor", 0) or 0) < BOT_ADAPTIVE_AVOID_MAX_PF
            else "STRONG BUCKET"
            if int(row.get("Trades", 0) or 0) >= BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE
            and float(row.get("Profit Factor", 0) or 0) >= BOT_ADAPTIVE_FAVORITE_MIN_PF
            and float(row.get("Win Rate %", 0) or 0) >= BOT_ADAPTIVE_FAVORITE_MIN_WR
            else "NEEDS MORE DATA / NEUTRAL"
        ),
        axis=1
    )

    reliable = out[out["Trades"] >= BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE].copy()
    if reliable.empty:
        recommendation = f"Need at least {BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE} closed trades per confidence bucket before changing thresholds."
    else:
        strong = reliable[reliable["Adaptive Action"] == "STRONG BUCKET"]
        weak = reliable[reliable["Adaptive Action"].str.contains("WEAK", na=False)]
        if not strong.empty:
            best_bucket = strong.sort_values(by=["Profit Factor", "Win Rate %"], ascending=False).iloc[0]
            recommendation = f"Best confidence bucket so far: {best_bucket['Group']} | PF {best_bucket['Profit Factor']} | WR {best_bucket['Win Rate %']}%."
        elif not weak.empty:
            recommendation = "Lower-confidence buckets are weak. Consider raising BOT_SIGNAL_MIN_CONFIDENCE after more data confirms this."
        else:
            recommendation = "Confidence buckets are mixed. Keep current confidence settings until more data comes in."

    return out, recommendation


def build_market_regime_optimization(df):
    tables = build_trade_intelligence_tables(df)
    market_perf = tables.get("market_perf", pd.DataFrame())

    if market_perf is None or market_perf.empty:
        return pd.DataFrame(), "No market-regime performance data yet."

    out = market_perf.copy()
    out["Adaptive Action"] = out.apply(
        lambda row: (
            "REDUCE EXPOSURE"
            if int(row.get("Trades", 0) or 0) >= BOT_ADAPTIVE_REGIME_MIN_SAMPLE
            and float(row.get("Profit Factor", 0) or 0) < BOT_ADAPTIVE_AVOID_MAX_PF
            else "FAVORABLE REGIME"
            if int(row.get("Trades", 0) or 0) >= BOT_ADAPTIVE_REGIME_MIN_SAMPLE
            and float(row.get("Profit Factor", 0) or 0) >= BOT_ADAPTIVE_FAVORITE_MIN_PF
            else "NEEDS MORE DATA / NEUTRAL"
        ),
        axis=1
    )

    weak = out[out["Adaptive Action"] == "REDUCE EXPOSURE"]
    strong = out[out["Adaptive Action"] == "FAVORABLE REGIME"]

    if not strong.empty:
        top = strong.sort_values(by="Profit Factor", ascending=False).iloc[0]
        recommendation = f"Best market condition so far: {top['Group']} | PF {top['Profit Factor']}."
    elif not weak.empty:
        low = weak.sort_values(by="Profit Factor", ascending=True).iloc[0]
        recommendation = f"Weak market condition detected: {low['Group']} | PF {low['Profit Factor']}. Consider lower exposure in that regime after more confirmation."
    else:
        recommendation = "Market-regime data is not decisive yet. Keep collecting paper trades."

    return out, recommendation


def build_adaptive_filter_report(df):
    adaptive, avoid_df, favorite_df = build_adaptive_ticker_filters(df)
    confidence_df, confidence_recommendation = build_confidence_optimization(df)
    regime_df, regime_recommendation = build_market_regime_optimization(df)

    avoid_tickers = avoid_df["Group"].astype(str).tolist() if not avoid_df.empty and "Group" in avoid_df.columns else []
    favorite_tickers = favorite_df["Group"].astype(str).tolist() if not favorite_df.empty and "Group" in favorite_df.columns else []

    recommendations = []
    if avoid_tickers:
        recommendations.append("AUTO-AVOID candidates: " + ", ".join(avoid_tickers))
    else:
        recommendations.append("No auto-avoid ticker candidates yet.")

    if favorite_tickers:
        recommendations.append("AUTO-FAVORITE candidates: " + ", ".join(favorite_tickers))
    else:
        recommendations.append("No auto-favorite ticker candidates yet.")

    recommendations.append(confidence_recommendation)
    recommendations.append(regime_recommendation)

    avoid_variable = "BOT_PAPER_TRADE_AVOID_TICKERS=" + ",".join(avoid_tickers) if avoid_tickers else "BOT_PAPER_TRADE_AVOID_TICKERS="
    favorite_note = ",".join(favorite_tickers) if favorite_tickers else "None yet"

    return {
        "adaptive": adaptive,
        "avoid_df": avoid_df,
        "favorite_df": favorite_df,
        "confidence_df": confidence_df,
        "regime_df": regime_df,
        "recommendations": recommendations,
        "avoid_variable": avoid_variable,
        "favorite_note": favorite_note,
    }




def fallback_setup_name(row):
    """Build a readable setup name when older paper trades do not have v32.10 setup_name yet."""
    signal = str(row.get("signal", "UNKNOWN") or "UNKNOWN").upper()
    direction = "Long" if "BUY" in signal else "Short" if "SELL" in signal else "Unknown"
    tags = []
    notes = str(row.get("notes", "") or "").lower()
    quality = safe_float_dashboard(row.get("quality_score", 0), 0)
    rr = safe_float_dashboard(row.get("risk_reward_2", 0), 0)
    confidence = safe_float_dashboard(row.get("confidence", 0), 0)
    if confidence >= 80:
        tags.append("HighConf")
    elif confidence >= 75:
        tags.append("MinConf")
    if rr >= 2:
        tags.append("RR2+")
    elif rr >= 1.5:
        tags.append("RR1.5+")
    if quality >= 85:
        tags.append("EliteQS")
    elif quality >= 70:
        tags.append("StrongQS")
    if "mtf" in notes:
        tags.append("MTF")
    if "volume" in notes:
        tags.append("Volume")
    if "market" in notes or "risk-on" in notes or "risk-off" in notes:
        tags.append("Market")
    if not tags:
        tags.append("General")
    return f"{direction}: " + "+".join(tags[:4])


def safe_float_dashboard(value, default=0):
    try:
        value = float(value)
        if pd.isna(value):
            return default
        return value
    except Exception:
        return default


def ensure_setup_columns(df):
    trades = normalize_paper_trade_df(df)
    if trades.empty:
        return trades
    if "setup_name" not in trades.columns:
        trades["setup_name"] = trades.apply(fallback_setup_name, axis=1)
    else:
        trades["setup_name"] = trades["setup_name"].replace(["", "nan", "None"], pd.NA)
        missing = trades["setup_name"].isna()
        if missing.any():
            trades.loc[missing, "setup_name"] = trades[missing].apply(fallback_setup_name, axis=1)
    if "setup_tags" not in trades.columns:
        trades["setup_tags"] = trades["setup_name"].astype(str).str.replace(": ", "+", regex=False)
    if "setup_score" not in trades.columns:
        trades["setup_score"] = pd.to_numeric(trades.get("quality_score", 0), errors="coerce").fillna(0)
    else:
        trades["setup_score"] = pd.to_numeric(trades["setup_score"], errors="coerce").fillna(pd.to_numeric(trades.get("quality_score", 0), errors="coerce").fillna(0))
    return trades


def build_setup_performance_tables(df):
    trades = ensure_setup_columns(df)
    closed = closed_paper_trades(trades)
    setup_perf = build_group_performance(trades, "setup_name", BOT_SETUP_ANALYTICS_MIN_SAMPLE)
    tag_perf = build_group_performance(trades, "setup_tags", BOT_SETUP_ANALYTICS_MIN_SAMPLE)
    if setup_perf.empty:
        best_setups = pd.DataFrame()
        worst_setups = pd.DataFrame()
        needs_more_data = pd.DataFrame()
    else:
        best_setups = setup_perf[
            (setup_perf["Trades"] >= BOT_SETUP_ANALYTICS_MIN_SAMPLE)
            & (setup_perf["Profit Factor"] >= BOT_SETUP_ANALYTICS_STRONG_PF)
            & (setup_perf["Win Rate %"] >= BOT_SETUP_ANALYTICS_STRONG_WR)
        ].head(BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS)
        reliable = setup_perf[setup_perf["Trades"] >= BOT_SETUP_ANALYTICS_MIN_SAMPLE]
        worst_setups = reliable[
            (reliable["Profit Factor"] <= BOT_ADAPTIVE_AVOID_MAX_PF)
            | (reliable["Win Rate %"] <= BOT_ADAPTIVE_AVOID_MAX_WR)
        ].sort_values(by=["Profit Factor", "Win Rate %", "Total P/L $"], ascending=True).head(BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS)
        needs_more_data = setup_perf[setup_perf["Trades"] < BOT_SETUP_ANALYTICS_MIN_SAMPLE].head(BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS)
    if closed.empty:
        recent_closed = pd.DataFrame()
    else:
        scorecard_cols = [
            "ticker", "market", "signal", "setup_name", "setup_tags", "setup_score",
            "confidence", "quality_score", "risk_reward_2", "pnl_percent", "pnl_dollars",
            "status", "date_opened", "date_closed"
        ]
        scorecard_cols = [col for col in scorecard_cols if col in closed.columns]
        recent_closed = closed.sort_values(by="date_closed", ascending=False).head(25)[scorecard_cols] if "date_closed" in closed.columns else closed.head(25)[scorecard_cols]
    return {
        "trades": trades,
        "closed": closed,
        "setup_perf": setup_perf,
        "tag_perf": tag_perf,
        "best_setups": best_setups,
        "worst_setups": worst_setups,
        "needs_more_data": needs_more_data,
        "recent_closed": recent_closed,
    }


def build_setup_intelligence_recommendations(tables):
    recs = []
    best = tables.get("best_setups", pd.DataFrame())
    worst = tables.get("worst_setups", pd.DataFrame())
    setup_perf = tables.get("setup_perf", pd.DataFrame())
    if not best.empty:
        for _, row in best.head(3).iterrows():
            recs.append(f"✅ Favor setup '{row['Group']}' when other filters agree: PF {row['Profit Factor']} | WR {row['Win Rate %']}% | Trades {row['Trades']}.")
    if not worst.empty:
        for _, row in worst.head(3).iterrows():
            recs.append(f"⚠️ Do not automate setup '{row['Group']}' yet: PF {row['Profit Factor']} | WR {row['Win Rate %']}% | Trades {row['Trades']}.")
    if setup_perf.empty:
        recs.append("Collect setup-tagged paper trades first. v32.10 bot trades will populate setup_name/setup_tags automatically.")
    elif not recs:
        recs.append("Setup data is not decisive yet. Keep collecting closed paper trades before turning setup rules into automation.")
    return recs[:8]


def build_dynamic_confidence_dashboard(df):
    trades = ensure_setup_columns(df)
    confidence_df = build_group_performance(trades, "confidence_bucket", 1) if "confidence_bucket" in trades.columns else pd.DataFrame()
    if confidence_df.empty:
        enriched = enrich_trade_intelligence_df(trades)
        confidence_df = build_group_performance(enriched, "confidence_bucket", 1)
    if confidence_df.empty:
        return confidence_df, "No confidence-bucket results yet. Keep current BOT_SIGNAL_MIN_CONFIDENCE until more trades close."
    reliable = confidence_df[confidence_df["Trades"] >= BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE].copy()
    if reliable.empty:
        return confidence_df, f"Need {BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE}+ closed trades per confidence bucket before changing BOT_SIGNAL_MIN_CONFIDENCE."
    strong = reliable[
        (reliable["Profit Factor"] >= BOT_DYNAMIC_CONFIDENCE_TARGET_PF)
        & (reliable["Win Rate %"] >= BOT_DYNAMIC_CONFIDENCE_TARGET_WR)
    ].sort_values(by=["Profit Factor", "Win Rate %"], ascending=False)
    if strong.empty:
        return confidence_df, "No confidence bucket has met the target yet. Keep the current confidence threshold and collect more data."
    best = strong.iloc[0]
    return confidence_df, f"Best reliable confidence bucket: {best['Group']} | PF {best['Profit Factor']} | WR {best['Win Rate %']}%. Use this as guidance before raising automation thresholds."

def normalize_paper_trade_df(df):
    if df is None or df.empty:
        return pd.DataFrame()
    clean_df = df.copy()
    numeric_columns = [
        "entry_price", "current_price", "stop_loss", "tp1", "tp2", "confidence",
        "position_size", "position_value", "pnl_percent", "pnl_dollars",
        "risk_reward_2", "signal_rank", "quality_score", "setup_score",
        "hours_open", "days_open", "hours_to_tp1", "hours_to_tp2", "hours_to_stop"
    ]
    for column in numeric_columns:
        if column in clean_df.columns:
            clean_df[column] = pd.to_numeric(clean_df[column], errors="coerce")
    for column in ["ticker", "market", "signal", "status", "result", "notes", "setup_name", "setup_tags", "lifecycle_stage", "date_tp1", "date_tp2", "date_stopped"]:
        if column in clean_df.columns:
            clean_df[column] = clean_df[column].astype(str)
    return clean_df


def filter_paper_trades(df, statuses=None, market="ALL", ticker_search=""):
    if df is None or df.empty:
        return pd.DataFrame()
    filtered = df.copy()
    if statuses and "status" in filtered.columns:
        filtered = filtered[filtered["status"].astype(str).isin(statuses)]
    if market != "ALL" and "market" in filtered.columns:
        filtered = filtered[filtered["market"].astype(str) == market]
    if ticker_search and "ticker" in filtered.columns:
        filtered = filtered[filtered["ticker"].astype(str).str.contains(ticker_search.upper(), na=False)]
    return filtered


def quality_badge(row):
    quality = float(row.get("quality_score", 0) or 0)
    rr = float(row.get("risk_reward_2", 0) or 0)
    confidence = float(row.get("confidence", 0) or 0)
    if quality >= 90 and rr >= 2 and confidence >= 80:
        return "Elite"
    if quality >= 75 and rr >= 1.5:
        return "Strong"
    if quality >= 60:
        return "Watch"
    return "Weak"


def paper_quality_summary(df):
    if df is None or df.empty:
        return {
            "total": 0, "open": 0, "closed": 0, "blocked_like": 0,
            "avg_quality": 0, "avg_rr": 0, "avg_confidence": 0,
            "best_setup": "N/A", "weakest_setup": "N/A",
            "avoid_open_count": 0, "max_open_used_pct": 0,
        }
    trades = normalize_paper_trade_df(df)
    open_df = filter_paper_trades(trades, ["OPEN", "TP1_HIT"])
    closed_df = filter_paper_trades(trades, ["TP2_HIT", "STOPPED", "CLOSED"])
    blocked_like = trades[
        trades.get("status", pd.Series(dtype=str)).astype(str).str.contains("BLOCK|SKIP|REJECT|AVOID", case=False, na=False)
        | trades.get("notes", pd.Series(dtype=str)).astype(str).str.contains("blocked|avoid|weak", case=False, na=False)
    ] if not trades.empty else pd.DataFrame()
    avoid_set = set(BOT_PAPER_TRADE_AVOID_TICKERS)
    avoid_open = open_df[open_df["ticker"].astype(str).isin(avoid_set)] if not open_df.empty and avoid_set else pd.DataFrame()
    ranked = trades.copy()
    if "quality_score" in ranked.columns:
        ranked = ranked.sort_values(by="quality_score", ascending=False)
    best = ranked.iloc[0] if not ranked.empty else {}
    weakest = ranked.iloc[-1] if not ranked.empty else {}
    max_open_used_pct = (len(open_df) / BOT_PAPER_TRADE_MAX_OPEN_TOTAL) * 100 if BOT_PAPER_TRADE_MAX_OPEN_TOTAL else 0
    return {
        "total": len(trades),
        "open": len(open_df),
        "closed": len(closed_df),
        "blocked_like": len(blocked_like),
        "avg_quality": round(pd.to_numeric(trades.get("quality_score", 0), errors="coerce").fillna(0).mean(), 2),
        "avg_rr": round(pd.to_numeric(trades.get("risk_reward_2", 0), errors="coerce").fillna(0).mean(), 2),
        "avg_confidence": round(pd.to_numeric(trades.get("confidence", 0), errors="coerce").fillna(0).mean(), 2),
        "best_setup": f"{best.get('ticker', 'N/A')} | QS {best.get('quality_score', 0)} | R/R {best.get('risk_reward_2', 0)}" if len(trades) else "N/A",
        "weakest_setup": f"{weakest.get('ticker', 'N/A')} | QS {weakest.get('quality_score', 0)} | R/R {weakest.get('risk_reward_2', 0)}" if len(trades) else "N/A",
        "avoid_open_count": len(avoid_open),
        "max_open_used_pct": round(max_open_used_pct, 2),
    }


def add_quality_badges(df):
    if df is None or df.empty:
        return df
    out = normalize_paper_trade_df(df)
    out["quality_badge"] = out.apply(quality_badge, axis=1)
    return out

def load_bot_status():
    # v32.18.1: load status from Google Sheets first when bot/dashboard are separate Railway projects.
    if DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared_status = load_shared_bot_status_from_google_sheets()
        if shared_status:
            return shared_status
    try:
        if os.path.exists(BOT_STATUS_FILE) and os.path.getsize(BOT_STATUS_FILE) > 0:
            with open(BOT_STATUS_FILE, "r", encoding="utf-8") as file:
                status = json.load(file)
                status["_shared_status_source"] = "Local File"
                return status
    except Exception as error:
        print("Bot status load error:", error)
    if not DASHBOARD_SHARED_STATUS_PREFER_GOOGLE:
        shared_status = load_shared_bot_status_from_google_sheets()
        if shared_status:
            return shared_status
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
# v32.14-v32.18 OUTCOME INTELLIGENCE DASHBOARD HELPERS
# ======================================================

def dashboard_bucket_confidence(value):
    value = safe_float_dashboard(value, 0)
    if value >= 90: return "90-100"
    if value >= 80: return "80-89"
    if value >= 70: return "70-79"
    if value >= 60: return "60-69"
    return "<60"


def dashboard_text_has_any(value, words):
    text = str(value or "").lower()
    return any(str(word).lower() in text for word in words)


def dashboard_stored_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ["1", "true", "yes", "y", "on"]


def dashboard_build_outcome_attribution_fields(row):
    notes = " ".join([str(row.get(c, "")) for c in ["notes", "setup_name", "setup_tags", "attribution_notes", "regime_bucket"]])
    mtf_aligned = dashboard_stored_bool(row.get("mtf_aligned", False)) or dashboard_text_has_any(notes, ["mtf"])
    volume_confirmed = dashboard_stored_bool(row.get("volume_confirmed", False)) or dashboard_text_has_any(notes, ["volume", "spike"])
    market_aligned = dashboard_stored_bool(row.get("market_aligned", False)) or dashboard_text_has_any(notes, ["market", "risk-on", "risk-off", "bull", "bear"])
    sr_confirmed = dashboard_stored_bool(row.get("sr_confirmed", False)) or dashboard_text_has_any(notes, ["s/r", "support", "resistance"])
    news_confirmed = dashboard_stored_bool(row.get("news_confirmed", False)) or dashboard_text_has_any(notes, ["news"])
    rr_confirmed = dashboard_stored_bool(row.get("rr_confirmed", False)) or safe_float_dashboard(row.get("risk_reward_2", 0), 0) >= 2
    confidence = safe_float_dashboard(row.get("confidence", 0), 0)
    quality = safe_float_dashboard(row.get("quality_score", 0), 0)
    status = str(row.get("status", "OPEN")).upper()
    pnl_pct = safe_float_dashboard(row.get("pnl_percent", 0), 0)
    if status in ["TP2_HIT", "CLOSED"] or pnl_pct > 0:
        outcome, bucket = "WIN", "Strong Win" if pnl_pct >= 2 else "Small Win"
    elif status == "STOPPED" or pnl_pct < 0:
        outcome, bucket = "LOSS", "Large Loss" if pnl_pct <= -2 else "Small Loss"
    elif status == "TP1_HIT":
        outcome, bucket = "PARTIAL_WIN", "TP1 Open"
    else:
        outcome, bucket = "OPEN", "Open / Monitoring"
    drivers=[]
    if mtf_aligned: drivers.append("MTF")
    if volume_confirmed: drivers.append("Volume")
    if market_aligned: drivers.append("Market")
    if sr_confirmed: drivers.append("S/R")
    if news_confirmed: drivers.append("News")
    if rr_confirmed: drivers.append("Risk/Reward")
    if confidence >= 80: drivers.append("High Confidence")
    if quality >= 80: drivers.append("High Quality")
    weak=[]
    if not mtf_aligned: weak.append("No MTF")
    if not volume_confirmed: weak.append("No Volume")
    if not market_aligned: weak.append("Market Unclear")
    if not sr_confirmed: weak.append("No S/R")
    if not rr_confirmed: weak.append("Weak R/R")
    score = min(100, len(drivers)*12 + max(0, confidence-50)*0.5 + max(0, quality-50)*0.3)
    regime = str(row.get("regime_bucket", "Unknown") or "Unknown")
    if regime.lower() in ["", "nan", "none"]: regime = "Unknown"
    return pd.Series({
        "outcome": outcome, "outcome_bucket": bucket, "attribution_score": round(score,2),
        "primary_driver": drivers[0] if drivers else "Needs Data",
        "secondary_driver": drivers[1] if len(drivers)>1 else "None",
        "weakness_driver": weak[0] if weak else "None",
        "mtf_aligned": mtf_aligned, "volume_confirmed": volume_confirmed, "market_aligned": market_aligned,
        "sr_confirmed": sr_confirmed, "news_confirmed": news_confirmed, "rr_confirmed": rr_confirmed,
        "confidence_bucket": str(row.get("confidence_bucket", "")) if str(row.get("confidence_bucket", "")).strip() else dashboard_bucket_confidence(confidence),
        "regime_bucket": regime,
        "attribution_notes": "Drivers: " + (", ".join(drivers) if drivers else "Needs Data") + " | Weakness: " + (", ".join(weak[:3]) if weak else "None"),
    })


def dashboard_enrich_outcome_intelligence(df):
    trades = normalize_paper_trade_df(df)
    if trades.empty:
        return trades
    needed = ["outcome", "outcome_bucket", "attribution_score", "primary_driver", "secondary_driver", "weakness_driver", "mtf_aligned", "volume_confirmed", "market_aligned", "sr_confirmed", "news_confirmed", "rr_confirmed", "confidence_bucket", "regime_bucket", "attribution_notes"]
    computed = trades.apply(dashboard_build_outcome_attribution_fields, axis=1)
    for col in needed:
        if col not in trades.columns or trades[col].astype(str).replace("nan", "").str.strip().eq("").all():
            trades[col] = computed[col]
        else:
            missing = trades[col].astype(str).replace("nan", "").str.strip().eq("")
            trades.loc[missing, col] = computed.loc[missing, col]
    return trades


def dashboard_closed_outcome_trades(df):
    trades = dashboard_enrich_outcome_intelligence(df)
    if trades.empty or "status" not in trades.columns:
        return pd.DataFrame()
    return trades[trades["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])].copy()


def dashboard_profit_factor(pnl):
    pnl = pd.to_numeric(pnl, errors="coerce").fillna(0)
    wins = pnl[pnl > 0].sum(); losses = abs(pnl[pnl < 0].sum())
    if losses > 0: return round(wins/losses, 2)
    return round(wins, 2) if wins > 0 else 0


def dashboard_win_rate(pnl):
    pnl = pd.to_numeric(pnl, errors="coerce").fillna(0)
    return round((len(pnl[pnl>0])/len(pnl))*100, 2) if len(pnl) else 0


def dashboard_group_outcome_performance(df, group_col, min_sample=1):
    closed = dashboard_closed_outcome_trades(df)
    if closed.empty or group_col not in closed.columns:
        return pd.DataFrame()
    rows=[]
    for group_name, group in closed.groupby(group_col):
        pnl_d = pd.to_numeric(group.get("pnl_dollars", 0), errors="coerce").fillna(0)
        pnl_p = pd.to_numeric(group.get("pnl_percent", 0), errors="coerce").fillna(0)
        count = len(group)
        rows.append({
            "Group": str(group_name), "Trades": count, "Win Rate %": dashboard_win_rate(pnl_d),
            "Profit Factor": dashboard_profit_factor(pnl_d), "Total P/L $": round(float(pnl_d.sum()),2),
            "Avg Return %": round(float(pnl_p.mean()),2) if count else 0,
            "Avg Attribution Score": round(float(pd.to_numeric(group.get("attribution_score",0), errors="coerce").fillna(0).mean()),2) if count else 0,
            "Sample Status": "Reliable" if count >= min_sample else "Needs More Data",
        })
    out = pd.DataFrame(rows)
    return out.sort_values(by=["Profit Factor", "Win Rate %", "Total P/L $"], ascending=False) if not out.empty else out


def dashboard_outcome_summary(df):
    trades = dashboard_enrich_outcome_intelligence(df)
    closed = dashboard_closed_outcome_trades(trades)
    open_count = len(trades[trades["status"].astype(str).isin(["OPEN", "TP1_HIT"])]) if not trades.empty and "status" in trades.columns else 0
    return {
        "total": len(trades), "open": open_count, "closed": len(closed),
        "win_rate": dashboard_win_rate(closed.get("pnl_dollars", pd.Series(dtype=float))) if not closed.empty else 0,
        "profit_factor": dashboard_profit_factor(closed.get("pnl_dollars", pd.Series(dtype=float))) if not closed.empty else 0,
        "total_pnl": round(float(pd.to_numeric(closed.get("pnl_dollars", 0), errors="coerce").fillna(0).sum()), 2) if not closed.empty else 0,
    }

# ======================================================
# TABS
# ======================================================

account_tab, open_trades_tab, closed_trades_tab, paper_quality_tab, decision_tab, performance_gate_tab, automation_readiness_tab, trade_lifecycle_tab, outcome_attribution_tab, setup_db_tab, regime_performance_tab, confidence_calibration_tab, signal_intelligence_tab, trade_intelligence_tab, adaptive_filters_tab, setup_intelligence_tab, crypto_tab, stock_tab, scanner_tab, alerts_tab, backtest_tab, bot_status_tab, settings_tab = st.tabs([
    "Paper Account",
    "Open Trades",
    "Closed Trades",
    "Paper Quality",
    "Decision Dashboard",
    "Performance Gate",
    "Automation Readiness",
    "Trade Lifecycle",
    "Outcome Attribution",
    "Setup Database",
    "Regime Performance",
    "Confidence Calibration",
    "Signal Intelligence",
    "Trade Intelligence",
    "Adaptive Filters",
    "Setup Intelligence",
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
        open_df = filter_paper_trades(paper_trades_df, ["OPEN", "TP1_HIT"])
        open_df = add_quality_badges(open_df)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Open Trades", len(open_df))
        col2.metric("Max Open Limit", BOT_PAPER_TRADE_MAX_OPEN_TOTAL)
        col3.metric("Capacity Used", f"{round((len(open_df) / BOT_PAPER_TRADE_MAX_OPEN_TOTAL) * 100, 2) if BOT_PAPER_TRADE_MAX_OPEN_TOTAL else 0}%")
        col4.metric("Open P/L", f"${pd.to_numeric(open_df.get('pnl_dollars', 0), errors='coerce').fillna(0).sum():.2f}" if not open_df.empty else "$0.00")
        market_filter = st.selectbox("Open Trade Market", ["ALL", "Crypto", "Stock"], key="open_trade_market_filter")
        ticker_search = st.text_input("Search Open Trade Ticker", key="open_trade_search")
        open_df = filter_paper_trades(open_df, ["OPEN", "TP1_HIT"], market_filter, ticker_search)
        if open_df.empty:
            st.info("No trades match the current filters.")
        else:
            display_cols = ["ticker", "market", "signal", "decision", "quality_badge", "entry_price", "current_price", "stop_loss", "tp1", "tp2", "confidence", "pnl_percent", "pnl_dollars", "risk_reward_2", "quality_score", "status", "date_opened", "last_updated"]
            if "decision" not in open_df.columns:
                open_df = build_decision_dashboard_df(open_df)
            display_cols = [col for col in display_cols if col in open_df.columns]
            st.dataframe(open_df[display_cols], width="stretch")
            st.download_button("Download Open Trades CSV", open_df.to_csv(index=False), "open_paper_trades.csv", "text/csv")

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
        closed_df = filter_paper_trades(paper_trades_df, ["TP2_HIT", "STOPPED", "CLOSED"])
        metrics = paper_trade_metrics(paper_trades_df)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Closed Trades", metrics["total_closed"])
        col2.metric("Win Rate", f"{metrics['win_rate']}%")
        col3.metric("Profit Factor", metrics["profit_factor"])
        col4.metric("Total P/L", f"${metrics['total_pnl']:.2f}")
        col5, col6, col7, col8 = st.columns(4)
        col5.metric("Avg Winner", f"${metrics['average_winner']:.2f}")
        col6.metric("Avg Loser", f"${metrics['average_loser']:.2f}")
        col7.metric("Best Ticker", metrics["best_ticker"])
        col8.metric("Worst Ticker", metrics["worst_ticker"])
        st.subheader("Equity Curve")
        if not equity_curve_df.empty and "equity" in equity_curve_df.columns:
            st.line_chart(equity_curve_df.set_index("timestamp")["equity"] if "timestamp" in equity_curve_df.columns else equity_curve_df["equity"])
        else:
            st.info("Equity curve will appear after closed paper trades are recorded.")
        st.subheader("Closed Trades")
        if closed_df.empty:
            st.info("No closed trades yet.")
        else:
            closed_df = add_quality_badges(closed_df)
            market_filter = st.selectbox("Closed Trade Market", ["ALL", "Crypto", "Stock"], key="closed_trade_market_filter")
            ticker_search = st.text_input("Search Closed Trade Ticker", key="closed_trade_search")
            closed_df = filter_paper_trades(closed_df, ["TP2_HIT", "STOPPED", "CLOSED"], market_filter, ticker_search)
            display_cols = ["ticker", "market", "signal", "result", "quality_badge", "entry_price", "current_price", "pnl_percent", "pnl_dollars", "confidence", "risk_reward_2", "quality_score", "date_opened", "date_closed"]
            display_cols = [col for col in display_cols if col in closed_df.columns]
            st.dataframe(closed_df[display_cols], width="stretch")
            st.download_button("Download Closed Trades CSV", closed_df.to_csv(index=False), "closed_paper_trades.csv", "text/csv")

# ======================================================
# v32.3 PAPER QUALITY TAB
# ======================================================

with paper_quality_tab:
    st.header("v32.3 Paper Trade Quality")
    paper_trades_df = load_paper_trades_df()
    summary = paper_quality_summary(paper_trades_df)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Paper Trades", summary["total"])
    col2.metric("Open", summary["open"])
    col3.metric("Closed", summary["closed"])
    col4.metric("Max Open Used", f"{summary['max_open_used_pct']}%")
    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Avg Quality", summary["avg_quality"])
    col6.metric("Avg R/R", summary["avg_rr"])
    col7.metric("Avg Confidence", f"{summary['avg_confidence']}%")
    col8.metric("Avoid-List Open", summary["avoid_open_count"])
    st.subheader("Active Guardrails")
    guardrails = pd.DataFrame([
        {"Guardrail": "Quality Filter", "Value": BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED},
        {"Guardrail": "Max Open Paper Trades", "Value": BOT_PAPER_TRADE_MAX_OPEN_TOTAL},
        {"Guardrail": "Minimum Backtest PF", "Value": BOT_PAPER_TRADE_MIN_BACKTEST_PF},
        {"Guardrail": "Minimum Backtest Win Rate", "Value": f"{BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE}%"},
        {"Guardrail": "Minimum Backtest Signals", "Value": BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS},
        {"Guardrail": "Avoid Tickers", "Value": ", ".join(BOT_PAPER_TRADE_AVOID_TICKERS) if BOT_PAPER_TRADE_AVOID_TICKERS else "None"},
    ])
    st.dataframe(arrow_safe_df(guardrails), width="stretch")
    if paper_trades_df.empty:
        st.info("No paper trade quality data yet.")
    else:
        quality_df = add_quality_badges(paper_trades_df)
        st.subheader("Quality Breakdown")
        if "quality_badge" in quality_df.columns:
            st.bar_chart(quality_df["quality_badge"].value_counts())
        st.dataframe(quality_df, width="stretch")

# ======================================================
# v32.4 DECISION DASHBOARD TAB
# ======================================================

with decision_tab:
    st.header("v32.4 Paper Trade Decision Dashboard")
    st.caption("Source of truth: tracked paper trades from bot.py. Use this to decide what to focus on, watch, or avoid.")
    paper_trades_df = load_paper_trades_df()
    decision_df = build_decision_dashboard_df(paper_trades_df)
    if decision_df.empty:
        st.info("No decision data yet. Let the bot open paper trades first.")
    else:
        open_decisions = decision_df[decision_df["status"].astype(str).isin(["OPEN", "TP1_HIT"])] if "status" in decision_df.columns else decision_df
        take_df = open_decisions[open_decisions["decision"].astype(str).str.contains("TAKE", na=False)]
        watch_df = open_decisions[open_decisions["decision"].astype(str) == "WATCH"]
        avoid_df = open_decisions[open_decisions["decision"].astype(str) == "AVOID"]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Take / Priority", len(take_df))
        col2.metric("Watch", len(watch_df))
        col3.metric("Avoid", len(avoid_df))
        col4.metric("Readiness Score", f"{round(open_decisions['decision_score'].mean(), 2) if not open_decisions.empty else 0}/100")
        st.subheader("Best Trades To Focus On")
        best_cols = ["ticker", "market", "signal", "decision", "decision_score", "quality_badge", "confidence", "risk_reward_2", "quality_score", "entry_price", "current_price", "stop_loss", "tp1", "tp2", "pnl_percent", "pnl_dollars", "status"]
        best_cols = [col for col in best_cols if col in decision_df.columns]
        if take_df.empty:
            st.info("No TAKE setups are currently open.")
        else:
            st.dataframe(take_df[best_cols], width="stretch")
        st.subheader("Watchlist-Only Signals")
        if watch_df.empty:
            st.info("No WATCH setups are currently open.")
        else:
            st.dataframe(watch_df[best_cols], width="stretch")
        st.subheader("Avoid / Weak Setups")
        if avoid_df.empty:
            st.success("No open avoid setups detected.")
        else:
            st.dataframe(avoid_df[best_cols], width="stretch")
        st.subheader("All Decision Rows")
        st.dataframe(decision_df[best_cols], width="stretch")
        st.download_button("Download Decision Dashboard CSV", decision_df.to_csv(index=False), "paper_trade_decision_dashboard.csv", "text/csv")


# ======================================================
# v32.5 PERFORMANCE GATE TAB
# ======================================================

with performance_gate_tab:
    st.header("v32.5 Performance Gate")
    st.caption("This is the go/no-go checklist before v33 3Commas paper automation.")

    paper_trades_df = load_paper_trades_df()
    equity_curve_df = load_paper_equity_df()
    checks_df, gate_summary = build_performance_gate_report(paper_trades_df, equity_curve_df)

    gate_icon = performance_gate_color(gate_summary["readiness_pct"])
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Automation Readiness", f"{gate_icon} {gate_summary['readiness_pct']}%")
    col2.metric("Passed Checks", f"{gate_summary['passed_checks']} / {gate_summary['total_checks']}")
    col3.metric("Closed Trades", gate_summary["total_closed"])
    col4.metric("Recommendation", gate_summary["recommendation"])

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Win Rate", f"{gate_summary['win_rate']}%")
    col6.metric("Profit Factor", gate_summary["profit_factor"])
    col7.metric("Equity Return", f"{gate_summary['equity_return_pct']}%")
    col8.metric("Max Drawdown", f"{gate_summary['max_drawdown_pct']}%")

    if gate_summary["automation_ready"]:
        st.success("All performance gates passed. The system is ready to begin v33 3Commas PAPER automation testing.")
    else:
        st.warning("Do not move to v33 yet. Keep collecting paper-trade data or improve filters until all gates pass.")

    st.subheader("Gate Checklist")
    display_checks = checks_df.copy()
    if not display_checks.empty:
        display_checks["Status"] = display_checks["Passed"].apply(lambda value: "PASS" if value else "FAIL")
        st.dataframe(display_checks[["Status", "Gate", "Required", "Current", "Why It Matters"]], width="stretch")

    st.subheader("Success Criteria")
    criteria_df = pd.DataFrame([
        {"Metric": "Closed Paper Trades", "Target": BOT_PERFORMANCE_GATE_MIN_TRADES, "Current": gate_summary["total_closed"]},
        {"Metric": "Win Rate", "Target": f"> {BOT_PERFORMANCE_GATE_MIN_WIN_RATE}%", "Current": f"{gate_summary['win_rate']}%"},
        {"Metric": "Profit Factor", "Target": f"> {BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR}", "Current": gate_summary["profit_factor"]},
        {"Metric": "Positive Equity Curve", "Target": "Required", "Current": "Yes" if gate_summary["positive_equity"] else "No"},
        {"Metric": "Max Drawdown", "Target": f"< {BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT}%", "Current": f"{gate_summary['max_drawdown_pct']}%"},
        {"Metric": "Average Quality Score", "Target": f"> {BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY}", "Current": gate_summary["avg_quality"]},
    ])
    st.dataframe(criteria_df, width="stretch")

    st.subheader("Equity Curve Gate")
    if not equity_curve_df.empty and "equity" in equity_curve_df.columns:
        chart_df = equity_curve_df.copy()
        chart_df["equity"] = pd.to_numeric(chart_df["equity"], errors="coerce")
        chart_df = chart_df.dropna(subset=["equity"])
        if "timestamp" in chart_df.columns:
            st.line_chart(chart_df.set_index("timestamp")["equity"])
        else:
            st.line_chart(chart_df["equity"])
    else:
        st.info("Equity curve will appear after closed paper trades are recorded.")

    st.subheader("Final Deploy Decision")
    if gate_summary["automation_ready"]:
        st.success("Deploy recommendation: Start v33 3Commas paper automation only, not live trading.")
    else:
        st.info("Deploy recommendation: Stay on v32.x and keep tracking paper trades until the gate passes.")

    st.download_button(
        label="Download Performance Gate Report CSV",
        data=checks_df.to_csv(index=False),
        file_name="performance_gate_report.csv",
        mime="text/csv"
    )


# ======================================================
# v32.6 TRADE INTELLIGENCE TAB
# ======================================================

with trade_intelligence_tab:
    st.header("v32.6 Trade Intelligence Dashboard")
    st.caption("This turns closed paper trades into strategy intelligence: what to trade more, what to avoid, and whether confidence is actually predictive.")

    paper_trades_df = load_paper_trades_df()
    equity_curve_df = load_paper_equity_df()
    tables = build_trade_intelligence_tables(paper_trades_df)
    closed_df = tables["closed"]
    metrics = paper_trade_metrics(paper_trades_df)
    checks_df, gate_summary = build_performance_gate_report(paper_trades_df, equity_curve_df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Closed Trades", metrics["total_closed"])
    col2.metric("Win Rate", f"{metrics['win_rate']}%")
    col3.metric("Profit Factor", metrics["profit_factor"])
    col4.metric("Readiness", f"{gate_summary['readiness_pct']}%")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Best Ticker", metrics["best_ticker"])
    col6.metric("Worst Ticker", metrics["worst_ticker"])
    col7.metric("Min Sample", BOT_TRADE_INTELLIGENCE_MIN_SAMPLE)
    col8.metric("Recommendation", gate_summary["recommendation"])

    if closed_df.empty:
        st.info("Trade Intelligence will become useful after closed paper trades are recorded. Keep the bot running until paper trades hit TP2 or stop loss.")
    else:
        st.subheader("AI Recommendations")
        for rec in build_trade_intelligence_recommendations(tables):
            st.write(rec)

        st.subheader("Best Performing Tickers")
        if tables["best_tickers"].empty:
            st.info(f"No ticker has reached the minimum sample size of {BOT_TRADE_INTELLIGENCE_MIN_SAMPLE} closed trades yet.")
        else:
            st.dataframe(tables["best_tickers"], width="stretch")

        st.subheader("Worst Performing Tickers")
        if tables["worst_tickers"].empty:
            st.info(f"No reliable worst-ticker ranking yet. Need {BOT_TRADE_INTELLIGENCE_MIN_SAMPLE}+ closed trades per ticker.")
        else:
            st.dataframe(tables["worst_tickers"], width="stretch")

        st.subheader("Tickers That Need More Data")
        if tables["needs_more_data"].empty:
            st.success("All ranked tickers meet the minimum sample size.")
        else:
            st.dataframe(tables["needs_more_data"], width="stretch")

        st.subheader("Confidence Score Validation")
        if tables["confidence_perf"].empty:
            st.info("No confidence bucket data yet.")
        else:
            st.dataframe(tables["confidence_perf"], width="stretch")
            chart_conf = tables["confidence_perf"].set_index("Group")[["Win Rate %", "Profit Factor"]]
            st.bar_chart(chart_conf)

        st.subheader("Long vs Short Performance")
        if tables["direction_perf"].empty:
            st.info("No long/short performance data yet.")
        else:
            st.dataframe(tables["direction_perf"], width="stretch")
            st.bar_chart(tables["direction_perf"].set_index("Group")[["Win Rate %", "Profit Factor"]])

        st.subheader("Market Condition Performance")
        if tables["market_perf"].empty:
            st.info("No market-condition performance data yet.")
        else:
            st.dataframe(tables["market_perf"], width="stretch")

        st.subheader("Top 10 Closed Trade Scorecards")
        if tables["top_trades"].empty:
            st.info("No closed trade scorecards yet.")
        else:
            st.dataframe(tables["top_trades"], width="stretch")

        st.subheader("Worst 10 Closed Trade Scorecards")
        if tables["worst_trades"].empty:
            st.info("No losing trade scorecards yet.")
        else:
            st.dataframe(tables["worst_trades"], width="stretch")

        export_df = tables["trades"]
        st.download_button(
            "Download Trade Intelligence CSV",
            export_df.to_csv(index=False),
            "trade_intelligence_dashboard.csv",
            "text/csv"
        )

    st.subheader("v33 Automation Readiness Reminder")
    readiness_notes = pd.DataFrame([
        {"Gate": "Closed Trades", "Target": BOT_PERFORMANCE_GATE_MIN_TRADES, "Current": metrics["total_closed"]},
        {"Gate": "Win Rate", "Target": f">= {BOT_PERFORMANCE_GATE_MIN_WIN_RATE}%", "Current": f"{metrics['win_rate']}%"},
        {"Gate": "Profit Factor", "Target": f">= {BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR}", "Current": metrics["profit_factor"]},
        {"Gate": "Positive Equity", "Target": "Required", "Current": "Yes" if gate_summary["positive_equity"] else "No"},
    ])
    st.dataframe(readiness_notes, width="stretch")



# ======================================================
# v32.7 ADAPTIVE FILTERS TAB
# ======================================================

with adaptive_filters_tab:
    st.header("v32.7 Adaptive Trade Filters")
    st.caption("Dashboard-only recommendations. This tab does not place trades or change bot behavior automatically.")

    paper_trades_df = load_paper_trades_df()
    report = build_adaptive_filter_report(paper_trades_df)
    adaptive_df = report["adaptive"]
    avoid_df = report["avoid_df"]
    favorite_df = report["favorite_df"]

    closed_count = len(closed_paper_trades(paper_trades_df))

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Closed Trades Analyzed", closed_count)
    col2.metric("Auto-Avoid Candidates", len(avoid_df))
    col3.metric("Auto-Favorite Candidates", len(favorite_df))
    col4.metric("Min Sample", BOT_ADAPTIVE_FILTERS_MIN_SAMPLE)

    st.subheader("Adaptive Recommendations")
    for recommendation in report["recommendations"]:
        st.write(recommendation)

    st.subheader("Suggested Avoid Variable")
    st.code(report["avoid_variable"], language="bash")
    st.caption("Use this only after enough paper-trade data confirms the ticker is weak. This is a recommendation, not an automatic bot change.")

    st.subheader("Auto-Avoid Candidates")
    if avoid_df.empty:
        st.success("No reliable auto-avoid candidates yet.")
    else:
        st.dataframe(avoid_df, width="stretch")

    st.subheader("Auto-Favorite Candidates")
    if favorite_df.empty:
        st.info("No reliable auto-favorite candidates yet.")
    else:
        st.dataframe(favorite_df, width="stretch")
        st.write("Favorite note:", report["favorite_note"])

    st.subheader("All Adaptive Ticker Scores")
    if adaptive_df.empty:
        st.info("No adaptive ticker data yet. Closed paper trades are required.")
    else:
        st.dataframe(adaptive_df, width="stretch")
        st.download_button(
            "Download Adaptive Ticker Scores CSV",
            adaptive_df.to_csv(index=False),
            "adaptive_ticker_scores.csv",
            "text/csv"
        )

    st.subheader("Confidence Optimization")
    confidence_df = report["confidence_df"]
    if confidence_df.empty:
        st.info("No confidence optimization data yet.")
    else:
        st.dataframe(confidence_df, width="stretch")
        if "Group" in confidence_df.columns:
            st.bar_chart(confidence_df.set_index("Group")[["Win Rate %", "Profit Factor"]])

    st.subheader("Market Regime Optimization")
    regime_df = report["regime_df"]
    if regime_df.empty:
        st.info("No market regime optimization data yet.")
    else:
        st.dataframe(regime_df, width="stretch")

    st.subheader("Guardrails")
    guardrail_df = pd.DataFrame([
        {"Setting": "Minimum sample per ticker", "Value": BOT_ADAPTIVE_FILTERS_MIN_SAMPLE},
        {"Setting": "Auto-avoid if PF <=", "Value": BOT_ADAPTIVE_AVOID_MAX_PF},
        {"Setting": "Auto-avoid if WR <=", "Value": f"{BOT_ADAPTIVE_AVOID_MAX_WR}%"},
        {"Setting": "Auto-favorite if PF >=", "Value": BOT_ADAPTIVE_FAVORITE_MIN_PF},
        {"Setting": "Auto-favorite if WR >=", "Value": f"{BOT_ADAPTIVE_FAVORITE_MIN_WR}%"},
        {"Setting": "Confidence bucket min sample", "Value": BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE},
        {"Setting": "Market regime min sample", "Value": BOT_ADAPTIVE_REGIME_MIN_SAMPLE},
    ])
    st.dataframe(arrow_safe_df(guardrail_df), width="stretch")

    st.info(
        "Deploy recommendation: keep these adaptive filters as dashboard recommendations until you have 100+ closed paper trades. "
        "After that, we can promote the strongest rules into bot.py."
    )




# ======================================================
# v32.12 AUTOMATION READINESS TAB
# ======================================================

with automation_readiness_tab:
    st.header("v32.12 Automation Readiness Center")
    st.caption("This tells you whether the paper-trading system has enough evidence to move toward v33 3Commas paper automation.")

    paper_trades_df = load_paper_trades_df()
    paper_equity_df = load_paper_equity_df()
    readiness = build_automation_readiness_dashboard_report(paper_trades_df, paper_equity_df)

    score = readiness.get("score", 0)
    status = readiness.get("status", "N/A")
    metrics = readiness.get("metrics", {})
    equity = readiness.get("equity", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Readiness Score", f"{score}/100")
    col2.metric("Closed Trades", metrics.get("total_closed", 0))
    col3.metric("Win Rate", f"{metrics.get('win_rate', 0)}%")
    col4.metric("Profit Factor", metrics.get("profit_factor", 0))

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Equity Return", f"{equity.get('equity_return_pct', 0)}%")
    col6.metric("Max Drawdown", f"{equity.get('max_drawdown_pct', 0)}%")
    col7.metric("Strong Setups", readiness.get("strong_count", 0))
    col8.metric("Weak Setups", readiness.get("weak_count", 0))

    if score >= BOT_AUTOMATION_READINESS_TARGET_SCORE:
        st.success(status)
    elif score >= 60:
        st.warning(status)
    else:
        st.error(status)

    st.subheader("Deploy Recommendation")
    st.write(readiness.get("recommendation", "N/A"))

    st.subheader("Automation Readiness Gates")
    checks = readiness.get("checks", pd.DataFrame())
    if checks.empty:
        st.info("No readiness data yet.")
    else:
        st.dataframe(arrow_safe_df(checks), width="stretch")

    st.subheader("Strategy Automation Notes")
    st.write("Best strategy:", readiness.get("best_strategy", "N/A"))
    st.write("Weak strategy:", readiness.get("weak_strategy", "N/A"))
    st.write("Dynamic confidence:", readiness.get("confidence_recommendation", "N/A"))

    st.subheader("v33 Rule")
    st.info(
        f"Do not start v33 3Commas paper automation until readiness score is {BOT_AUTOMATION_READINESS_TARGET_SCORE}+ "
        f"with {BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES}+ closed paper trades, PF >= {BOT_AUTOMATION_READINESS_TARGET_PF}, "
        f"WR >= {BOT_AUTOMATION_READINESS_TARGET_WR}%, and a positive equity curve."
    )



# ======================================================
# v32.13 TRADE LIFECYCLE ANALYTICS TAB
# ======================================================

with trade_lifecycle_tab:
    st.header("v32.13 Trade Lifecycle Analytics")
    st.caption("This shows how long paper trades stay open, how quickly TP1/TP2/stop events happen, and which setups use capital most efficiently.")

    paper_trades_df = load_paper_trades_df()
    lifecycle = build_trade_lifecycle_dashboard_report(paper_trades_df)
    summary = lifecycle.get("summary", {})

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Closed Trades", summary.get("closed_trades", 0))
    col2.metric("Avg Days Open", summary.get("avg_days_open", 0))
    col3.metric("Avg Hours To TP1", summary.get("avg_hours_to_tp1", 0))
    col4.metric("Avg Return / Day", f"{summary.get('avg_return_per_day', 0)}%")

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("TP1 Hits", summary.get("tp1_hits", 0))
    col6.metric("TP2 Hits", summary.get("tp2_hits", 0))
    col7.metric("Stop Hits", summary.get("stop_hits", 0))
    col8.metric("Slow Closed Trades", summary.get("slow_closed_count", 0))

    st.subheader("Lifecycle Recommendations")
    for recommendation in lifecycle.get("recommendations", []):
        st.write(recommendation)

    if summary.get("closed_trades", 0) < BOT_TRADE_LIFECYCLE_MIN_SAMPLE:
        st.warning(
            f"Lifecycle analytics needs more closed trades before making strong conclusions. "
            f"Current: {summary.get('closed_trades', 0)} | Target: {BOT_TRADE_LIFECYCLE_MIN_SAMPLE}+"
        )

    st.subheader("Setup Capital Efficiency")
    setup_df = lifecycle.get("setup_df", pd.DataFrame())
    if setup_df.empty:
        st.info("No closed setup lifecycle data yet.")
    else:
        st.dataframe(arrow_safe_df(setup_df), width="stretch")
        if "Setup Name" in setup_df.columns and "Avg Return / Day %" in setup_df.columns:
            st.bar_chart(setup_df.set_index("Setup Name")[["Avg Return / Day %"]])
        st.download_button(
            "Download Trade Lifecycle CSV",
            setup_df.to_csv(index=False),
            "trade_lifecycle_dashboard.csv",
            "text/csv"
        )

    st.subheader("Recent Trade Lifecycle Events")
    recent_df = lifecycle.get("recent_df", pd.DataFrame())
    if recent_df.empty:
        st.info("No paper trades available yet.")
    else:
        st.dataframe(arrow_safe_df(recent_df), width="stretch")

    st.subheader("Lifecycle Guardrails")
    guardrail_df = pd.DataFrame([
        {"Setting": "Minimum lifecycle sample", "Value": BOT_TRADE_LIFECYCLE_MIN_SAMPLE},
        {"Setting": "Fast TP1 threshold", "Value": f"<= {BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS} hours"},
        {"Setting": "Slow hold threshold", "Value": f"> {BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS} days"},
        {"Setting": "Strong return/day threshold", "Value": f">= {BOT_TRADE_LIFECYCLE_STRONG_RETURN_PER_DAY}%/day"},
    ])

    st.dataframe(arrow_safe_df(guardrail_df), width="stretch")


# ======================================================
# v32.14 OUTCOME ATTRIBUTION TAB
# ======================================================

with outcome_attribution_tab:
    st.header("v32.14 Trade Outcome Attribution Engine")
    st.caption("Explains why paper trades are winning, losing, or still developing.")
    trades = dashboard_enrich_outcome_intelligence(load_paper_trades_df())
    summary = dashboard_outcome_summary(trades)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open Trades", summary["open"])
    c2.metric("Closed Trades", summary["closed"])
    c3.metric("Win Rate", f"{summary['win_rate']}%")
    c4.metric("Profit Factor", summary["profit_factor"])
    driver_df = dashboard_group_outcome_performance(trades, "primary_driver", BOT_OUTCOME_ATTRIBUTION_MIN_SAMPLE)
    st.subheader("Primary Driver Performance")
    if driver_df.empty:
        st.info("No closed trades yet. Attribution is active and will rank drivers after outcomes close.")
    else:
        st.dataframe(arrow_safe_df(driver_df), width="stretch")
    st.subheader("Recent Attribution Events")
    cols = [c for c in ["ticker", "signal", "status", "outcome", "outcome_bucket", "primary_driver", "secondary_driver", "weakness_driver", "confidence_bucket", "regime_bucket", "attribution_score", "pnl_percent", "pnl_dollars", "attribution_notes"] if c in trades.columns]
    if trades.empty:
        st.info("No paper trades yet.")
    else:
        st.dataframe(arrow_safe_df(trades.tail(25)[cols]), width="stretch")


# ======================================================
# v32.15 SETUP PERFORMANCE DATABASE TAB
# ======================================================

with setup_db_tab:
    st.header("v32.15 Setup Performance Database")
    st.caption("Ranks each setup by closed paper-trade performance and attribution quality.")
    trades = dashboard_enrich_outcome_intelligence(load_paper_trades_df())
    setup_df = dashboard_group_outcome_performance(trades, "setup_name", BOT_SETUP_ANALYTICS_MIN_SAMPLE)
    if setup_df.empty:
        st.info("No closed setup outcomes yet. Open trades are already being tagged for this database.")
    else:
        st.dataframe(arrow_safe_df(setup_df), width="stretch")
        st.download_button("Download Setup Performance Database CSV", setup_df.to_csv(index=False), "setup_performance_database.csv", "text/csv")
    st.warning(f"Minimum reliable setup sample: {BOT_SETUP_ANALYTICS_MIN_SAMPLE} closed trades per setup.")


# ======================================================
# v32.16 MARKET REGIME PERFORMANCE TAB
# ======================================================

with regime_performance_tab:
    st.header("v32.16 Market Regime Performance Analytics")
    st.caption("Shows which market regimes and risk modes your strategy performs best in.")
    trades = dashboard_enrich_outcome_intelligence(load_paper_trades_df())
    regime_df = dashboard_group_outcome_performance(trades, "regime_bucket", BOT_REGIME_PERFORMANCE_MIN_SAMPLE)
    if regime_df.empty:
        st.info("No closed regime outcomes yet.")
    else:
        st.dataframe(arrow_safe_df(regime_df), width="stretch")
    st.info(f"Minimum reliable regime sample: {BOT_REGIME_PERFORMANCE_MIN_SAMPLE} closed trades per regime.")


# ======================================================
# v32.17 CONFIDENCE CALIBRATION TAB
# ======================================================

with confidence_calibration_tab:
    st.header("v32.17 Confidence Calibration Engine")
    st.caption("Checks whether higher confidence scores are actually producing better outcomes.")
    trades = dashboard_enrich_outcome_intelligence(load_paper_trades_df())
    conf_df = dashboard_group_outcome_performance(trades, "confidence_bucket", BOT_CONFIDENCE_CALIBRATION_MIN_SAMPLE)
    if conf_df.empty:
        st.info("No closed confidence-bucket outcomes yet.")
    else:
        st.dataframe(arrow_safe_df(conf_df), width="stretch")
        if "Group" in conf_df.columns:
            st.bar_chart(conf_df.set_index("Group")[["Win Rate %", "Profit Factor"]])
    st.warning("Do not raise BOT_SIGNAL_MIN_CONFIDENCE until confidence buckets have enough closed trade evidence.")


# ======================================================
# v32.18 SIGNAL INTELLIGENCE DASHBOARD TAB
# ======================================================

with signal_intelligence_tab:
    st.header("v32.18 Signal Intelligence Dashboard")
    st.caption("Combines ticker, signal, setup, regime, and confidence evidence into one pre-v33 decision center.")
    trades = dashboard_enrich_outcome_intelligence(load_paper_trades_df())
    summary = dashboard_outcome_summary(trades)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tracked Trades", summary["total"])
    c2.metric("Closed Trades", summary["closed"])
    c3.metric("Total Closed P/L", f"${summary['total_pnl']}")
    c4.metric("PF", summary["profit_factor"])
    signal_df = dashboard_group_outcome_performance(trades, "signal", BOT_SIGNAL_INTELLIGENCE_MIN_SAMPLE)
    ticker_df = dashboard_group_outcome_performance(trades, "ticker", BOT_SIGNAL_INTELLIGENCE_MIN_SAMPLE)
    st.subheader("Signal Type Performance")
    if signal_df.empty: st.info("No closed signal outcomes yet.")
    else: st.dataframe(arrow_safe_df(signal_df), width="stretch")
    st.subheader("Ticker Performance")
    if ticker_df.empty: st.info("No closed ticker outcomes yet.")
    else: st.dataframe(arrow_safe_df(ticker_df), width="stretch")
    st.subheader("v33 Automation Rule")
    st.info("Only automate strategies after 100+ closed paper trades, PF >= 1.5, WR >= 50%, positive equity curve, and no confirmed weak setup/regime/confidence bucket.")


# ======================================================
# v32.10.1 SETUP INTELLIGENCE TAB
# ======================================================

with setup_intelligence_tab:
    st.header("v32.10.1 Setup Intelligence")
    st.caption("This shows which setup types are working, which setups should not be automated, and whether confidence buckets support raising thresholds.")

    paper_trades_df = load_paper_trades_df()
    tables = build_setup_performance_tables(paper_trades_df)
    setup_perf = tables["setup_perf"]
    best_setups = tables["best_setups"]
    worst_setups = tables["worst_setups"]
    closed_count = len(tables["closed"])
    unique_setups = setup_perf["Group"].nunique() if not setup_perf.empty and "Group" in setup_perf.columns else 0
    confidence_df, confidence_recommendation = build_dynamic_confidence_dashboard(paper_trades_df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Closed Trades", closed_count)
    col2.metric("Unique Setups", unique_setups)
    col3.metric("Strong Setups", len(best_setups))
    col4.metric("Weak Setups", len(worst_setups))

    st.subheader("Setup Intelligence Recommendations")
    for recommendation in build_setup_intelligence_recommendations(tables):
        st.write(recommendation)
    st.write("📌", confidence_recommendation)

    if closed_count < BOT_PERFORMANCE_GATE_MIN_TRADES:
        st.warning(
            f"Do not move to v33 automation yet. Current closed trades: {closed_count}. "
            f"Target: {BOT_PERFORMANCE_GATE_MIN_TRADES}+ closed paper trades."
        )

    st.subheader("Best Setups")
    if best_setups.empty:
        st.info(f"No setup has met the strong criteria yet: PF >= {BOT_SETUP_ANALYTICS_STRONG_PF}, WR >= {BOT_SETUP_ANALYTICS_STRONG_WR}%, and {BOT_SETUP_ANALYTICS_MIN_SAMPLE}+ trades.")
    else:
        st.dataframe(best_setups, width="stretch")

    st.subheader("Weak / Do-Not-Automate Setups")
    if worst_setups.empty:
        st.success("No reliable weak setup has been confirmed yet.")
    else:
        st.error("These setups should not be automated until performance improves.")
        st.dataframe(worst_setups, width="stretch")

    st.subheader("All Setup Performance")
    if setup_perf.empty:
        st.info("No setup performance data yet. Deploy/run the v32.10 bot so new paper trades include setup_name and setup_tags.")
    else:
        st.dataframe(setup_perf, width="stretch")
        chart_cols = [col for col in ["Win Rate %", "Profit Factor"] if col in setup_perf.columns]
        if "Group" in setup_perf.columns and chart_cols:
            st.bar_chart(setup_perf.set_index("Group")[chart_cols])
        st.download_button(
            "Download Setup Performance CSV",
            setup_perf.to_csv(index=False),
            "setup_performance_dashboard.csv",
            "text/csv"
        )

    st.subheader("Setup Tag Performance")
    tag_perf = tables["tag_perf"]
    if tag_perf.empty:
        st.info("No setup tag performance data yet.")
    else:
        st.dataframe(tag_perf, width="stretch")

    st.subheader("Dynamic Confidence Recommendation")
    if confidence_df.empty:
        st.info("No confidence-bucket data yet.")
    else:
        st.dataframe(confidence_df, width="stretch")
        if "Group" in confidence_df.columns:
            st.bar_chart(confidence_df.set_index("Group")[["Win Rate %", "Profit Factor"]])

    st.subheader("Recent Closed Setup Scorecards")
    recent_closed = tables["recent_closed"]
    if recent_closed.empty:
        st.info("Closed paper trades will appear here once trades hit TP2, stop loss, or close.")
    else:
        st.dataframe(recent_closed, width="stretch")

    st.subheader("v33 Automation Guardrail")
    guardrails = pd.DataFrame([
        {"Rule": "Minimum closed paper trades", "Current": closed_count, "Required": BOT_PERFORMANCE_GATE_MIN_TRADES},
        {"Rule": "Strong setup PF", "Current": f">= {BOT_SETUP_ANALYTICS_STRONG_PF}", "Required": "Before favoring setup"},
        {"Rule": "Strong setup WR", "Current": f">= {BOT_SETUP_ANALYTICS_STRONG_WR}%", "Required": "Before favoring setup"},
        {"Rule": "Weak setup block", "Current": f"PF <= {BOT_ADAPTIVE_AVOID_MAX_PF} or WR <= {BOT_ADAPTIVE_AVOID_MAX_WR}%", "Required": "Do not automate"},
        {"Rule": "Confidence sample", "Current": BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE, "Required": "Per bucket before threshold change"},
    ])
    st.dataframe(arrow_safe_df(guardrails), width="stretch")



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
                for _, row in signal_rows.iterrows():
                    alert_key = f"{row['Ticker']}_{row['AI Signal']}_{now_dt().strftime('%Y-%m-%d')}"

                    if alert_key not in st.session_state.sent_signal_alerts:
                        sent = send_signal_embed(row)
                        if sent:
                            st.session_state.sent_signal_alerts.add(alert_key)
                        save_sent_signals(st.session_state.sent_signal_alerts)

                st.success("Signal alerts sent.")

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
            "No local bot_last_status.json or Google Sheets Shared Bot Status data found yet. "
            "For separate Railway projects, add GOOGLE_SHEET_ID, GOOGLE_SERVICE_ACCOUNT_JSON, "
            "GOOGLE_SHEETS_ENABLED=true, and DASHBOARD_SHARED_STATUS_SYNC_ENABLED=true to the dashboard service."
        )
    else:
        age_minutes = status_age_minutes(bot_status)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Bot Version", bot_status.get("bot_version", "Unknown"))
        col2.metric("Last Status", bot_status.get("timestamp", "Unknown"))
        col3.metric("Status Age", f"{age_minutes} min" if age_minutes is not None else "Unknown")
        col4.metric("Scanned", bot_status.get("scanned", 0))
        st.caption(f"Status source: {bot_status.get('_shared_status_source', 'Local File')}")

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

        st.subheader("Paper Trade Data Flow Diagnostics")
        diag_summary, diag_df, dashboard_diag, bot_diag, diag_warnings = build_paper_trade_data_flow_diagnostics(bot_status)

        health = diag_summary.get("Health", "UNKNOWN")
        if health == "HEALTHY - DATA FLOW CONNECTED":
            st.success(health)
        elif health == "CONNECTED - WAITING FOR PAPER TRADES":
            st.info(health)
        elif health == "WAITING FOR BOT DIAGNOSTICS":
            st.warning(health)
        else:
            st.error(health)

        dcol1, dcol2, dcol3, dcol4 = st.columns(4)
        dcol1.metric("Status Source", diag_summary.get("Status Source", "Unknown"))
        dcol2.metric("Rows Match", diag_summary.get("Rows Match", "NO"))
        dcol3.metric("Bot Rows", diag_summary.get("Bot Rows", 0))
        dcol4.metric("Dashboard Rows", diag_summary.get("Dashboard Rows", 0))

        dcol5, dcol6, dcol7, dcol8 = st.columns(4)
        dcol5.metric("Open Trades", diag_summary.get("Bot Open Rows", 0))
        dcol6.metric("Closed Trades", diag_summary.get("Bot Closed Rows", 0))
        dcol7.metric("TP1 Trades", diag_summary.get("Bot TP1 Rows", 0))
        dcol8.metric("Open Tickers", diag_summary.get("Bot Open Tickers", "None"))

        if diag_warnings:
            for warning in diag_warnings:
                st.warning(warning)
        else:
            st.success("Bot and dashboard paper-trade data flow looks connected.")

        st.dataframe(diag_df, width="stretch")

        with st.expander("Dashboard file diagnostics"):
            st.json(dashboard_diag)

        with st.expander("Bot paper-trade file diagnostics"):
            st.json(bot_diag)

        with st.expander("Raw bot status JSON"):
            st.json(bot_status)

    st.subheader("v32.4 Feature Compatibility Checklist")
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
        "Your v32.2+ background bot remains the source of truth for ranked alerts, exposure controls, trade plans, and advanced backtesting."
    )


# ======================================================
# SETTINGS TAB
# ======================================================

with settings_tab:
    st.header("Settings")
    st.caption(f"Running {APP_VERSION}")
    st.write("Dashboard data dir:", DATA_DIR)
    st.write("Paper trades file:", PAPER_TRADES_FILE)
    st.write("Shared status sync enabled:", DASHBOARD_SHARED_STATUS_SYNC_ENABLED)
    st.write("Shared status source preference:", "Google Sheets first" if DASHBOARD_SHARED_STATUS_PREFER_GOOGLE else "Local files first")
    st.write("Google Sheet ID configured:", bool(GOOGLE_SHEET_ID))
    st.write("Google service account JSON configured:", bool(GOOGLE_SERVICE_ACCOUNT_JSON))
    st.write("Paper trades file exists:", os.path.exists(PAPER_TRADES_FILE))
    st.write("Paper trades visible rows:", len(load_paper_trades_df()))
    st.write("Dashboard timezone:", BOT_TIMEZONE)
    st.write("Discord message limit:", DISCORD_MESSAGE_LIMIT)

    st.subheader("Paper Trade File Diagnostics")
    settings_diag_summary, settings_diag_df, settings_dashboard_diag, settings_bot_diag, settings_warnings = build_paper_trade_data_flow_diagnostics(load_bot_status())
    st.caption(settings_diag_summary.get("Health", "UNKNOWN"))
    st.dataframe(settings_diag_df, width="stretch")

    st.subheader("Active Watchlists")

    col1, col2 = st.columns(2)

    with col1:
        st.write("Crypto Tickers")
        st.write(CRYPTO_TICKERS)

    with col2:
        st.write("Stock Tickers")
        st.write(STOCK_TICKERS)

    st.subheader("Paper Trade Quality Settings")
    st.write("Quality filter enabled:", BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED)
    st.write("Max total open paper trades:", BOT_PAPER_TRADE_MAX_OPEN_TOTAL)
    st.write("Minimum backtest PF:", BOT_PAPER_TRADE_MIN_BACKTEST_PF)
    st.write("Minimum backtest win rate:", f"{BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE}%")
    st.write("Minimum backtest signals:", BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS)
    st.write("Avoid tickers:", BOT_PAPER_TRADE_AVOID_TICKERS if BOT_PAPER_TRADE_AVOID_TICKERS else "None")
    st.write("Paper trade summary enabled:", BOT_SEND_PAPER_TRADE_SUMMARY)
    st.write("Paper trade summary interval hours:", BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS)

    st.subheader("Performance Gate Settings")
    st.write("Minimum closed paper trades:", BOT_PERFORMANCE_GATE_MIN_TRADES)
    st.write("Minimum win rate:", f"{BOT_PERFORMANCE_GATE_MIN_WIN_RATE}%")
    st.write("Minimum profit factor:", BOT_PERFORMANCE_GATE_MIN_PROFIT_FACTOR)
    st.write("Require positive equity curve:", BOT_PERFORMANCE_GATE_REQUIRE_POSITIVE_EQUITY)
    st.write("Maximum drawdown:", f"{BOT_PERFORMANCE_GATE_MAX_DRAWDOWN_PCT}%")
    st.write("Minimum average quality score:", BOT_PERFORMANCE_GATE_MIN_AVG_QUALITY)

    st.subheader("Trade Intelligence Settings")
    st.write("Minimum sample size per ticker:", BOT_TRADE_INTELLIGENCE_MIN_SAMPLE)
    st.write("Strong ticker profit factor:", BOT_TRADE_INTELLIGENCE_STRONG_PF)
    st.write("Strong ticker win rate:", f"{BOT_TRADE_INTELLIGENCE_STRONG_WR}%")

    st.subheader("Adaptive Filter Settings")
    st.write("Adaptive minimum sample:", BOT_ADAPTIVE_FILTERS_MIN_SAMPLE)
    st.write("Auto-avoid max PF:", BOT_ADAPTIVE_AVOID_MAX_PF)
    st.write("Auto-avoid max win rate:", f"{BOT_ADAPTIVE_AVOID_MAX_WR}%")
    st.write("Auto-favorite min PF:", BOT_ADAPTIVE_FAVORITE_MIN_PF)
    st.write("Auto-favorite min win rate:", f"{BOT_ADAPTIVE_FAVORITE_MIN_WR}%")
    st.write("Confidence optimization min sample:", BOT_ADAPTIVE_CONFIDENCE_MIN_SAMPLE)
    st.write("Market regime optimization min sample:", BOT_ADAPTIVE_REGIME_MIN_SAMPLE)

    st.subheader("Setup Intelligence Settings")
    st.write("Setup analytics minimum sample:", BOT_SETUP_ANALYTICS_MIN_SAMPLE)
    st.write("Strong setup profit factor:", BOT_SETUP_ANALYTICS_STRONG_PF)
    st.write("Strong setup win rate:", f"{BOT_SETUP_ANALYTICS_STRONG_WR}%")
    st.write("Max setup report rows:", BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS)
    st.write("Dynamic confidence min sample:", BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE)
    st.write("Dynamic confidence target PF:", BOT_DYNAMIC_CONFIDENCE_TARGET_PF)
    st.write("Dynamic confidence target win rate:", f"{BOT_DYNAMIC_CONFIDENCE_TARGET_WR}%")

    st.subheader("Trade Lifecycle Settings")
    st.write("Lifecycle minimum sample:", BOT_TRADE_LIFECYCLE_MIN_SAMPLE)
    st.write("Fast TP1 threshold:", f"{BOT_TRADE_LIFECYCLE_FAST_TP1_HOURS} hours")
    st.write("Slow hold threshold:", f"{BOT_TRADE_LIFECYCLE_MAX_HOLD_DAYS} days")
    st.write("Strong return per day:", f"{BOT_TRADE_LIFECYCLE_STRONG_RETURN_PER_DAY}%/day")

    st.subheader("Automation Readiness Settings")
    st.write("Minimum closed paper trades:", BOT_AUTOMATION_READINESS_MIN_CLOSED_TRADES)
    st.write("Target win rate:", f"{BOT_AUTOMATION_READINESS_TARGET_WR}%")
    st.write("Target profit factor:", BOT_AUTOMATION_READINESS_TARGET_PF)
    st.write("Target readiness score:", BOT_AUTOMATION_READINESS_TARGET_SCORE)
    st.write("Max drawdown:", f"{BOT_AUTOMATION_READINESS_MAX_DRAWDOWN_PCT}%")
    st.write("Minimum strong strategies:", BOT_AUTOMATION_READINESS_MIN_STRONG_STRATEGIES)

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
