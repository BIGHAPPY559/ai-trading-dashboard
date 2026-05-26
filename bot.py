import os
import sys
import time
import json
import signal
import traceback
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import requests

try:
    import yfinance as yf
except Exception as error:
    print(f"Missing or failed dependency: yfinance ({error}). Install requirements.txt before running this bot.", flush=True)
    sys.exit(1)

try:
    from ta.momentum import RSIIndicator
    from ta.trend import MACD
except Exception as error:
    print(f"Missing or failed dependency: ta ({error}). Install requirements.txt before running this bot.", flush=True)
    sys.exit(1)

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

CRYPTO_NEWS_KEYWORDS = [
    "bitcoin", "ethereum", "crypto", "cryptocurrency", "blockchain",
    "solana", "xrp", "cardano", "avalanche", "altcoin"
]

STOCK_NEWS_KEYWORDS = [
    "stock market", "nasdaq", "s&p 500", "dow jones", "earnings",
    "federal reserve", "interest rates", "inflation", "ai stocks"
]

BREAKING_KEYWORDS = [
    "breaking", "urgent", "surges", "plunges", "crashes", "rallies",
    "lawsuit", "sec", "fed", "rate cut", "rate hike", "earnings",
    "guidance", "acquisition", "merger", "bankruptcy", "approval",
    "etf", "hack", "exploit", "halted", "investigation", "beats",
    "misses", "downgrade", "upgrade"
]


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


def get_env_list(name, default_items):
    value = os.getenv(name, "")

    if not value.strip():
        return list(default_items)

    items = []
    for item in value.split(","):
        cleaned = item.strip().upper()
        if cleaned and cleaned not in items:
            items.append(cleaned)

    return items if items else list(default_items)


# Optional Railway/custom watchlist overrides. Use comma-separated tickers, for example:
# BOT_CRYPTO_TICKERS=BTC-USD,ETH-USD,SOL-USD
# BOT_STOCK_TICKERS=AAPL,MSFT,NVDA,SPY,QQQ
CRYPTO_TICKERS = get_env_list("BOT_CRYPTO_TICKERS", CRYPTO_TICKERS)
STOCK_TICKERS = get_env_list("BOT_STOCK_TICKERS", STOCK_TICKERS)
ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS


def clean_ticker_list(tickers):
    cleaned = []
    for ticker in tickers:
        ticker = str(ticker or "").strip().upper()
        if not ticker:
            continue
        if any(char.isspace() for char in ticker):
            continue
        if ticker not in cleaned:
            cleaned.append(ticker)
    return cleaned


CRYPTO_TICKERS = clean_ticker_list(CRYPTO_TICKERS)
STOCK_TICKERS = clean_ticker_list(STOCK_TICKERS)
ALL_TICKERS = CRYPTO_TICKERS + STOCK_TICKERS


# ======================================================
# ENVIRONMENT VARIABLES
# ======================================================

TRADE_WEBHOOK_URL = os.getenv("TRADE_WEBHOOK_URL", "")

CRYPTO_TRADE_WEBHOOK_URL = os.getenv(
    "CRYPTO_TRADE_WEBHOOK_URL",
    os.getenv("CRYPTO_WEBHOOK_URL", TRADE_WEBHOOK_URL)
)

STOCK_TRADE_WEBHOOK_URL = os.getenv(
    "STOCK_TRADE_WEBHOOK_URL",
    os.getenv("STOCK_WEBHOOK_URL", TRADE_WEBHOOK_URL)
)

CRYPTO_SUMMARY_WEBHOOK_URL = os.getenv(
    "CRYPTO_SUMMARY_WEBHOOK_URL",
    os.getenv("SUMMARY_WEBHOOK_URL", "")
)

STOCK_SUMMARY_WEBHOOK_URL = os.getenv(
    "STOCK_SUMMARY_WEBHOOK_URL",
    os.getenv("SUMMARY_WEBHOOK_URL", "")
)

CRYPTO_NEWS_WEBHOOK_URL = os.getenv(
    "CRYPTO_NEWS_WEBHOOK_URL",
    os.getenv("NEWS_WEBHOOK_URL", "")
)

STOCK_NEWS_WEBHOOK_URL = os.getenv(
    "STOCK_NEWS_WEBHOOK_URL",
    os.getenv("NEWS_WEBHOOK_URL", "")
)

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

GOOGLE_SHEETS_ENABLED = get_env_bool("GOOGLE_SHEETS_ENABLED", False)
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEETS_LOG_SCAN_HISTORY = get_env_bool("GOOGLE_SHEETS_LOG_SCAN_HISTORY", True)
GOOGLE_SHEETS_LOG_ONLY_ALERTS_TO_TRACKER = get_env_bool("GOOGLE_SHEETS_LOG_ONLY_ALERTS_TO_TRACKER", False)
GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER = get_env_bool("GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER", False)
GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS = max(100, get_env_int("GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS", 5000))
GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES = max(1, get_env_int("GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES", 15))
GOOGLE_SHEETS_RETRY_INTERVAL_MINUTES = max(1, get_env_int("GOOGLE_SHEETS_RETRY_INTERVAL_MINUTES", 15))
GOOGLE_SHEETS_MAX_TRACKER_ROWS = max(100, get_env_int("GOOGLE_SHEETS_MAX_TRACKER_ROWS", 2500))
GOOGLE_SHEETS_FORMATTING_ENABLED = get_env_bool("GOOGLE_SHEETS_FORMATTING_ENABLED", True)
GOOGLE_SHEETS_FORMAT_EVERY_SYNC = get_env_bool("GOOGLE_SHEETS_FORMAT_EVERY_SYNC", False)
YFINANCE_NEWS_MAX_TICKERS_PER_MARKET = max(1, get_env_int("YFINANCE_NEWS_MAX_TICKERS_PER_MARKET", 6))
YFINANCE_NEWS_DELAY_SECONDS = max(0, get_env_float("YFINANCE_NEWS_DELAY_SECONDS", 0.25))
FINNHUB_NEWS_MAX_TICKERS_PER_SCAN = max(1, get_env_int("FINNHUB_NEWS_MAX_TICKERS_PER_SCAN", 8))
FINNHUB_NEWS_DELAY_SECONDS = max(0, get_env_float("FINNHUB_NEWS_DELAY_SECONDS", 0.25))
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "America/Los_Angeles")
BOT_SEND_ERROR_ALERTS = get_env_bool("BOT_SEND_ERROR_ALERTS", True)
BOT_ERROR_ALERT_COOLDOWN_MINUTES = max(5, get_env_int("BOT_ERROR_ALERT_COOLDOWN_MINUTES", 30))
ERROR_WEBHOOK_URL = os.getenv("ERROR_WEBHOOK_URL", "")
HEARTBEAT_WEBHOOK_URL = os.getenv("HEARTBEAT_WEBHOOK_URL", "")
BOT_VERSION = "google-sheets-100-production-v21-heartbeat-polished"
BOT_START_TIME = time.time()

BOT_RUN_ONCE = get_env_bool("BOT_RUN_ONCE", False)
BOT_DISCORD_DRY_RUN = get_env_bool("BOT_DISCORD_DRY_RUN", False)
BOT_SKIP_STARTUP_SCAN = get_env_bool("BOT_SKIP_STARTUP_SCAN", False)
BOT_SEND_NO_DATA_ALERTS = get_env_bool("BOT_SEND_NO_DATA_ALERTS", True)
BOT_STRICT_CONFIG = get_env_bool("BOT_STRICT_CONFIG", False)
BOT_SCAN_MARKET_DATA_ENABLED = get_env_bool("BOT_SCAN_MARKET_DATA_ENABLED", True)
BOT_NEWS_YFINANCE_ENABLED = get_env_bool("BOT_NEWS_YFINANCE_ENABLED", False)
BOT_MAX_SCAN_SECONDS = max(60, get_env_int("BOT_MAX_SCAN_SECONDS", 600))
DISCORD_MESSAGE_LIMIT = max(500, min(get_env_int("DISCORD_MESSAGE_LIMIT", 1900), 2000))
SUMMARY_MAX_LINES_PER_SECTION = max(3, get_env_int("SUMMARY_MAX_LINES_PER_SECTION", 15))

SCAN_INTERVAL_MINUTES = max(1, get_env_int("BOT_SCAN_INTERVAL_MINUTES", 15))
MIN_CONFIDENCE = max(
    0,
    min(
        get_env_float(
            "BOT_SIGNAL_MIN_CONFIDENCE",
            get_env_float("AUTO_SIGNAL_MIN_CONFIDENCE", 75)
        ),
        100
    )
)

SEND_STARTUP_MESSAGE = get_env_bool("BOT_SEND_STARTUP_MESSAGE", True)

SEND_SUMMARIES = get_env_bool("BOT_SEND_SUMMARIES", True)
SUMMARY_INTERVAL_HOURS = max(1, get_env_float("BOT_SUMMARY_INTERVAL_HOURS", 6))

SEND_NEWS = get_env_bool("BOT_SEND_NEWS", True)
NEWS_INTERVAL_HOURS = max(1, get_env_float("BOT_NEWS_INTERVAL_HOURS", 4))
NEWS_MAX_ARTICLES_PER_MARKET = max(1, get_env_int("BOT_NEWS_MAX_ARTICLES_PER_MARKET", 6))
NEWS_ARTICLES_PER_TICKER = max(1, get_env_int("BOT_NEWS_ARTICLES_PER_TICKER", 2))
NEWS_BREAKING_ONLY = get_env_bool("BOT_NEWS_BREAKING_ONLY", False)

SEND_BREAKING_NEWS = get_env_bool("BOT_SEND_BREAKING_NEWS", True)
BREAKING_NEWS_INTERVAL_MINUTES = max(5, get_env_int("BOT_BREAKING_NEWS_INTERVAL_MINUTES", 60))
BREAKING_NEWS_MAX_ARTICLES_PER_MARKET = max(1, get_env_int("BOT_BREAKING_NEWS_MAX_ARTICLES_PER_MARKET", 3))

# Railway containers can restart. If you attach a Railway volume, set
# BOT_DATA_DIR=/data so duplicate-alert logs survive redeploys/restarts.
BOT_DATA_DIR = os.getenv("BOT_DATA_DIR", os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "."))
BOT_DATA_DIR = BOT_DATA_DIR.strip() or "."

SIGNAL_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_signal_log.txt")
SUMMARY_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_summary_log.txt")
NEWS_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_news_log.txt")
NEWS_SCHEDULE_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_news_schedule_log.txt")
BREAKING_NEWS_SCHEDULE_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_breaking_news_schedule_log.txt")

BOT_HEARTBEAT_ENABLED = get_env_bool("BOT_HEARTBEAT_ENABLED", True)
BOT_HEARTBEAT_INTERVAL_HOURS = max(1, get_env_float("BOT_HEARTBEAT_INTERVAL_HOURS", 12))
HEARTBEAT_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_heartbeat_log.txt")

YFINANCE_TICKER_DELAY_SECONDS = max(0, get_env_float("YFINANCE_TICKER_DELAY_SECONDS", 0.25))
YFINANCE_HISTORY_RETRIES = max(0, get_env_int("YFINANCE_HISTORY_RETRIES", 2))
YFINANCE_TIMEOUT_SECONDS = max(5, get_env_int("YFINANCE_TIMEOUT_SECONDS", 20))
YFINANCE_USE_HISTORY_FALLBACK = get_env_bool("YFINANCE_USE_HISTORY_FALLBACK", False)
BOT_SLEEP_CHUNK_SECONDS = max(5, get_env_int("BOT_SLEEP_CHUNK_SECONDS", 30))
LOG_MAX_ITEMS = max(100, get_env_int("BOT_LOG_MAX_ITEMS", 5000))
BOT_STATUS_FILE = os.path.join(BOT_DATA_DIR, "bot_last_status.json")

GOOGLE_SHEETS_CLIENT = None
GOOGLE_SPREADSHEET = None
LAST_GOOGLE_SHEETS_SYNC_TIME = 0
LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME = 0
LAST_ERROR_ALERT_TIME = 0
SHUTDOWN_REQUESTED = False
FORMATTED_WORKSHEETS = set()


def request_shutdown(signum=None, frame=None):
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    log(f"Shutdown requested. Signal: {signum}")


def configure_signal_handlers():
    try:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)
    except Exception as error:
        log(f"Signal handler setup skipped: {error}")


def interruptible_sleep(total_seconds):
    end_time = time.time() + max(0, total_seconds)
    while time.time() < end_time and not SHUTDOWN_REQUESTED:
        time.sleep(min(BOT_SLEEP_CHUNK_SECONDS, max(0, end_time - time.time())))



# ======================================================
# HELPER FUNCTIONS
# ======================================================

def now_dt():
    try:
        return datetime.now(ZoneInfo(BOT_TIMEZONE))
    except Exception:
        return datetime.now()


def now_text():
    return now_dt().strftime("%Y-%m-%d %H:%M")


def format_epoch_time(timestamp):
    try:
        if not timestamp:
            return "None"

        return datetime.fromtimestamp(timestamp, ZoneInfo(BOT_TIMEZONE)).strftime("%Y-%m-%d %H:%M")

    except Exception:
        return str(timestamp)


def bot_uptime_minutes():
    try:
        return round((time.time() - BOT_START_TIME) / 60, 2)
    except Exception:
        return 0


def format_uptime():
    total_minutes = int(max(0, bot_uptime_minutes()))
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours <= 0:
        return f"{minutes} min"

    if minutes == 0:
        return f"{hours} hr"

    return f"{hours} hr {minutes} min"


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
        return CRYPTO_SUMMARY_WEBHOOK_URL or CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_SUMMARY_WEBHOOK_URL or STOCK_TRADE_WEBHOOK_URL


def get_news_webhook(market):
    if market == "Crypto":
        return CRYPTO_NEWS_WEBHOOK_URL or CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_NEWS_WEBHOOK_URL or STOCK_TRADE_WEBHOOK_URL


def get_error_webhook():
    if ERROR_WEBHOOK_URL:
        return ERROR_WEBHOOK_URL

    if STOCK_TRADE_WEBHOOK_URL:
        return STOCK_TRADE_WEBHOOK_URL

    return CRYPTO_TRADE_WEBHOOK_URL


def get_heartbeat_webhook():
    if HEARTBEAT_WEBHOOK_URL:
        return HEARTBEAT_WEBHOOK_URL

    return get_error_webhook()


def send_error_alert(message):
    global LAST_ERROR_ALERT_TIME

    if not BOT_SEND_ERROR_ALERTS:
        return False

    webhook_url = get_error_webhook()

    if not webhook_url:
        log("Error alert skipped: no error webhook available.")
        return False

    elapsed = seconds_since(LAST_ERROR_ALERT_TIME)
    required = BOT_ERROR_ALERT_COOLDOWN_MINUTES * 60

    if LAST_ERROR_ALERT_TIME and elapsed < required:
        log("Error alert skipped by cooldown.")
        return False

    sent = send_discord_message(
        webhook_url,
        f"⚠️ AI Trading Bot Error\nVersion: {BOT_VERSION}\nTime: {now_text()}\n{message}"
    )

    if sent:
        LAST_ERROR_ALERT_TIME = time.time()

    return sent


def run_safe_step(step_name, function, *args, **kwargs):
    try:
        return function(*args, **kwargs), False

    except Exception as error:
        log(f"{step_name} error: {error}")
        log(traceback.format_exc())
        send_error_alert(f"{step_name} error: {error}")
        return None, True


def ensure_data_dir():
    try:
        os.makedirs(BOT_DATA_DIR, exist_ok=True)
    except Exception as error:
        log(f"Could not create BOT_DATA_DIR {BOT_DATA_DIR}: {error}")


def trim_log_items(items, max_items=LOG_MAX_ITEMS):
    try:
        sorted_items = sorted(items)
        if len(sorted_items) <= max_items:
            return set(sorted_items)
        return set(sorted_items[-max_items:])
    except Exception:
        return set(items)


def load_log(file_path):
    try:
        ensure_data_dir()
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as file:
                return set(line.strip() for line in file if line.strip())
    except Exception as error:
        log(f"Could not load {file_path}: {error}")

    return set()


def save_log(file_path, items):
    try:
        ensure_data_dir()
        cleaned_items = trim_log_items(items)
        temp_path = f"{file_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            for item in sorted(cleaned_items):
                file.write(f"{item}\n")
        os.replace(temp_path, file_path)
    except Exception as error:
        log(f"Could not save {file_path}: {error}")


def save_json_atomic(file_path, data):
    try:
        ensure_data_dir()
        temp_path = f"{file_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, sort_keys=True)
        os.replace(temp_path, file_path)
    except Exception as error:
        log(f"Could not save JSON {file_path}: {error}")


def write_status_file(status):
    payload = dict(status or {})
    payload["bot_version"] = BOT_VERSION
    payload["timestamp"] = now_text()
    payload["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    payload["uptime_minutes"] = bot_uptime_minutes()
    payload["shutdown_requested"] = SHUTDOWN_REQUESTED
    save_json_atomic(BOT_STATUS_FILE, payload)


def safe_get_json(url, params=None, headers=None, timeout=10, max_retries=1):
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)

            if response.status_code == 429 and attempt < max_retries:
                try:
                    retry_after = float(response.headers.get("Retry-After", 1))
                except Exception:
                    retry_after = 1

                log(f"GET JSON rate limited. Waiting {retry_after} seconds: {url}")
                interruptible_sleep(retry_after + 0.5)
                continue

            if response.status_code >= 500 and attempt < max_retries:
                log(f"GET JSON temporary server error {response.status_code}. Retrying: {url}")
                interruptible_sleep(1)
                continue

            if response.status_code != 200:
                log(f"GET JSON failed {response.status_code}: {url} {response.text[:200]}")
                return None

            return response.json()

        except Exception as error:
            log(f"GET JSON error for {url}: {error}")

            if attempt < max_retries:
                interruptible_sleep(1)
                continue

            return None

    return None


def send_discord_embed(webhook_url, title, color, fields, max_retries=2):
    if BOT_DISCORD_DRY_RUN:
        log(f"Discord dry run embed: {title}")
        return True

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
                interruptible_sleep(retry_after + 0.5)
                continue

            if response.status_code >= 500 and attempt < max_retries:
                interruptible_sleep(1.5)
                continue

            return False

        except Exception as error:
            log(f"Discord error: {error}")

            if attempt < max_retries:
                interruptible_sleep(1)
                continue

            return False

    return False


def send_discord_message(webhook_url, message, max_retries=2):
    if BOT_DISCORD_DRY_RUN:
        log(f"Discord dry run message: {message[:120]}")
        return True

    if not webhook_url:
        log("Missing webhook for Discord message.")
        return False

    payload = {"content": message[:DISCORD_MESSAGE_LIMIT]}

    for attempt in range(max_retries + 1):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            log(f"Discord message status: {response.status_code} {response.text[:200]}")

            if response.status_code in [200, 204]:
                return True

            if response.status_code == 429 and attempt < max_retries:
                try:
                    retry_after = float(response.json().get("retry_after", 1))
                except Exception:
                    retry_after = 1

                interruptible_sleep(retry_after + 0.5)
                continue

            if response.status_code >= 500 and attempt < max_retries:
                interruptible_sleep(1.5)
                continue

            return False

        except Exception as error:
            log(f"Discord message error: {error}")

            if attempt < max_retries:
                interruptible_sleep(1)
                continue

            return False

    return False


def signal_embed_color(signal):
    if "BUY" in signal:
        return 65280
    if "SELL" in signal:
        return 16711680
    return 16776960


# ======================================================
# RUNTIME VALIDATION
# ======================================================

def validate_runtime_config():
    warnings = []

    if not ALL_TICKERS:
        warnings.append("No tickers are configured. Check BOT_CRYPTO_TICKERS and BOT_STOCK_TICKERS.")

    if not CRYPTO_TRADE_WEBHOOK_URL:
        warnings.append("CRYPTO_TRADE_WEBHOOK_URL is missing. Crypto trade alerts cannot be sent.")

    if not STOCK_TRADE_WEBHOOK_URL:
        warnings.append("STOCK_TRADE_WEBHOOK_URL is missing. Stock trade alerts cannot be sent.")

    if SEND_SUMMARIES and not get_summary_webhook("Crypto"):
        warnings.append("Crypto summaries are enabled but no crypto summary/trade webhook is available.")

    if SEND_SUMMARIES and not get_summary_webhook("Stock"):
        warnings.append("Stock summaries are enabled but no stock summary/trade webhook is available.")

    if SEND_NEWS and not get_news_webhook("Crypto"):
        warnings.append("Crypto news is enabled but no crypto news/trade webhook is available.")

    if SEND_NEWS and not get_news_webhook("Stock"):
        warnings.append("Stock news is enabled but no stock news/trade webhook is available.")

    if SEND_NEWS and not NEWSAPI_KEY and not FINNHUB_API_KEY and not BOT_NEWS_YFINANCE_ENABLED:
        warnings.append("News is enabled, but NewsAPI/Finnhub keys are missing and Yahoo/yfinance news is disabled. News digests will likely be empty.")

    if GOOGLE_SHEETS_ENABLED:
        if not GOOGLE_SHEET_ID:
            warnings.append("GOOGLE_SHEETS_ENABLED is true but GOOGLE_SHEET_ID is missing.")
        if not GOOGLE_SERVICE_ACCOUNT_JSON:
            warnings.append("GOOGLE_SHEETS_ENABLED is true but GOOGLE_SERVICE_ACCOUNT_JSON is missing.")
        if gspread is None or Credentials is None:
            warnings.append("GOOGLE_SHEETS_ENABLED is true but gspread/google-auth is not installed.")

    return warnings


def log_runtime_config_warnings():
    warnings = validate_runtime_config()
    if not warnings:
        log("Runtime config check passed.")
        return

    for warning in warnings:
        log(f"CONFIG WARNING: {warning}")

    if BOT_STRICT_CONFIG:
        raise RuntimeError("BOT_STRICT_CONFIG is enabled and configuration warnings were found: " + "; ".join(warnings))


# ======================================================
# MARKET DATA FUNCTIONS
# ======================================================

def normalize_price_data(data):
    if data is None or data.empty:
        return pd.DataFrame()

    # yfinance.download can return multi-index columns; flatten when one ticker is used.
    if isinstance(data.columns, pd.MultiIndex):
        try:
            data.columns = data.columns.get_level_values(0)
        except Exception:
            data.columns = [str(col[0]) for col in data.columns]

    required_columns = ["Close"]
    for column in required_columns:
        if column not in data.columns:
            return pd.DataFrame()

    return data.dropna(subset=["Close"])


def get_price_data(ticker, period="1y"):
    if not BOT_SCAN_MARKET_DATA_ENABLED:
        return pd.DataFrame()

    # Prefer yf.download because it supports timeout. Ticker.history can hang in
    # some hosted environments, so it is used only as a fallback.
    for attempt in range(YFINANCE_HISTORY_RETRIES + 1):
        try:
            data = yf.download(
                ticker,
                period=period,
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=YFINANCE_TIMEOUT_SECONDS
            )
            data = normalize_price_data(data)

            if not data.empty:
                return data

            log(f"{ticker}: no price data returned on download attempt {attempt + 1}.")

        except Exception as error:
            log(f"Price data download error for {ticker} attempt {attempt + 1}: {error}")

        if YFINANCE_USE_HISTORY_FALLBACK and not SHUTDOWN_REQUESTED:
            try:
                data = yf.Ticker(ticker).history(period=period, auto_adjust=False)
                data = normalize_price_data(data)

                if not data.empty:
                    return data

                log(f"{ticker}: no price data returned on history fallback attempt {attempt + 1}.")

            except Exception as error:
                log(f"Price data history fallback error for {ticker} attempt {attempt + 1}: {error}")

        if attempt < YFINANCE_HISTORY_RETRIES and not SHUTDOWN_REQUESTED:
            interruptible_sleep(1.5)

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
# NEWS NORMALIZATION
# ======================================================

def make_news_item(source, market, ticker, title, url="", publisher="", published_at=""):
    title = str(title or "").strip()
    url = str(url or "").strip()
    publisher = str(publisher or source or "Unknown").strip()

    if not title:
        return None

    return {
        "source": source,
        "market": market,
        "ticker": ticker,
        "title": title,
        "url": url,
        "publisher": publisher,
        "published_at": str(published_at or "").strip()
    }


def is_breaking_news(title):
    lowered = title.lower()
    return any(keyword in lowered for keyword in BREAKING_KEYWORDS)


def article_key(item):
    today = now_dt().strftime("%Y-%m-%d")
    raw_key = "|".join([
        str(item.get("market", "")),
        str(item.get("ticker", "")),
        str(item.get("source", "")),
        str(item.get("title", "")).lower().strip(),
        str(item.get("url", "")).lower().strip(),
        today,
    ])
    return hashlib.sha256(raw_key.encode("utf-8", errors="ignore")).hexdigest()


def dedupe_news_items(items, max_articles, breaking_only=False):
    sent_news = load_log(NEWS_LOG_FILE)
    seen_titles = set()
    deduped = []

    for item in items:
        if not item:
            continue

        key = article_key(item)
        normalized_title = item["title"].lower().strip()

        if key in sent_news:
            continue

        if normalized_title in seen_titles:
            continue

        if breaking_only and not is_breaking_news(item["title"]):
            continue

        seen_titles.add(normalized_title)
        deduped.append(item)

        if len(deduped) >= max_articles:
            break

    return deduped


# ======================================================
# YAHOO FINANCE YFINANCE NEWS
# ======================================================

def get_yahoo_article_title(article):
    content = article.get("content", {})

    if isinstance(content, dict):
        title = content.get("title", "")
        if title:
            return title

    title = article.get("title", "")
    if title:
        return title

    return ""


def get_yahoo_article_url(article):
    content = article.get("content", {})

    if isinstance(content, dict):
        canonical_url = content.get("canonicalUrl", {})
        if isinstance(canonical_url, dict) and canonical_url.get("url"):
            return canonical_url.get("url", "")

        click_url = content.get("clickThroughUrl", {})
        if isinstance(click_url, dict) and click_url.get("url"):
            return click_url.get("url", "")

    link = article.get("link", "")
    if isinstance(link, str):
        return link

    return ""


def get_yahoo_article_publisher(article):
    content = article.get("content", {})

    if isinstance(content, dict):
        provider = content.get("provider", {})
        if isinstance(provider, dict) and provider.get("displayName"):
            return provider.get("displayName", "")

    publisher = article.get("publisher", "")
    if publisher:
        return publisher

    return "Yahoo Finance"


def fetch_yfinance_news_for_ticker(ticker, market):
    items = []

    try:
        news_items = yf.Ticker(ticker).news

        if not news_items:
            return items

        for article in news_items[:NEWS_ARTICLES_PER_TICKER]:
            item = make_news_item(
                source="Yahoo Finance",
                market=market,
                ticker=ticker,
                title=get_yahoo_article_title(article),
                url=get_yahoo_article_url(article),
                publisher=get_yahoo_article_publisher(article)
            )

            if item:
                items.append(item)

    except Exception as error:
        log(f"Yahoo/yfinance news error for {ticker}: {error}")

    return items


def fetch_yfinance_news(tickers, market):
    items = []

    if not BOT_NEWS_YFINANCE_ENABLED:
        return items
    limited_tickers = tickers[:YFINANCE_NEWS_MAX_TICKERS_PER_MARKET]

    for ticker in limited_tickers:
        if SHUTDOWN_REQUESTED:
            break
        items.extend(fetch_yfinance_news_for_ticker(ticker, market))

        if YFINANCE_NEWS_DELAY_SECONDS > 0:
            interruptible_sleep(YFINANCE_NEWS_DELAY_SECONDS)

    return items


# ======================================================
# NEWSAPI NEWS
# ======================================================

def fetch_newsapi_news(market):
    if not NEWSAPI_KEY:
        return []

    query_terms = CRYPTO_NEWS_KEYWORDS if market == "Crypto" else STOCK_NEWS_KEYWORDS
    query = " OR ".join(query_terms[:8])

    data = safe_get_json(
        "https://newsapi.org/v2/everything",
        params={
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": NEWS_MAX_ARTICLES_PER_MARKET,
            "apiKey": NEWSAPI_KEY
        }
    )

    if not data or data.get("status") != "ok":
        return []

    items = []

    for article in data.get("articles", []):
        source = article.get("source", {})
        publisher = source.get("name", "NewsAPI") if isinstance(source, dict) else "NewsAPI"

        item = make_news_item(
            source="NewsAPI",
            market=market,
            ticker="Market",
            title=article.get("title", ""),
            url=article.get("url", ""),
            publisher=publisher,
            published_at=article.get("publishedAt", "")
        )

        if item:
            items.append(item)

    return items


# ======================================================
# FINNHUB NEWS
# ======================================================

def fetch_finnhub_company_news(ticker):
    if not FINNHUB_API_KEY:
        return []

    end_date = now_dt().date()
    start_date = end_date - timedelta(days=7)

    data = safe_get_json(
        "https://finnhub.io/api/v1/company-news",
        params={
            "symbol": ticker,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "token": FINNHUB_API_KEY
        }
    )

    if not isinstance(data, list):
        return []

    items = []

    for article in data[:NEWS_ARTICLES_PER_TICKER]:
        item = make_news_item(
            source="Finnhub",
            market="Stock",
            ticker=ticker,
            title=article.get("headline", ""),
            url=article.get("url", ""),
            publisher=article.get("source", "Finnhub"),
            published_at=article.get("datetime", "")
        )

        if item:
            items.append(item)

    return items


def fetch_finnhub_news(tickers):
    if not FINNHUB_API_KEY:
        return []

    items = []

    for ticker in tickers[:FINNHUB_NEWS_MAX_TICKERS_PER_SCAN]:
        if SHUTDOWN_REQUESTED:
            break
        items.extend(fetch_finnhub_company_news(ticker))

        if FINNHUB_NEWS_DELAY_SECONDS > 0:
            interruptible_sleep(FINNHUB_NEWS_DELAY_SECONDS)

    return items



# ======================================================
# NEWS DIGEST SENDING
# ======================================================

def collect_news_items(tickers, market, digest_type="scheduled"):
    items = []

    if market == "Crypto":
        items.extend(fetch_newsapi_news("Crypto"))
        items.extend(fetch_yfinance_news(tickers, "Crypto"))

    if market == "Stock":
        items.extend(fetch_newsapi_news("Stock"))
        items.extend(fetch_finnhub_news(tickers))
        items.extend(fetch_yfinance_news(tickers, "Stock"))

    breaking_only = digest_type == "breaking" or NEWS_BREAKING_ONLY
    max_articles = BREAKING_NEWS_MAX_ARTICLES_PER_MARKET if digest_type == "breaking" else NEWS_MAX_ARTICLES_PER_MARKET

    return dedupe_news_items(items, max_articles, breaking_only=breaking_only)


def send_news_digest(tickers, market, digest_type="scheduled"):
    webhook_url = get_news_webhook(market)

    if not webhook_url:
        log(f"Missing {market} news webhook.")
        return "failed"

    digest_items = collect_news_items(tickers, market, digest_type=digest_type)

    if not digest_items:
        log(f"No new {market} {digest_type} news articles to send.")
        return "none"

    title_prefix = "🚨 BREAKING" if digest_type == "breaking" else "📰"
    message = f"{title_prefix} {market.upper()} MARKET NEWS DIGEST\n"
    message += f"Time: {now_text()}\n\n"

    included_items = []

    for number, item in enumerate(digest_items, start=1):
        line = (
            f"{number}. {item['ticker']} | {item['title']}\n"
            f"Source: {item['publisher']} via {item['source']}"
        )

        if item["url"]:
            line += f"\n{item['url']}"

        line += "\n\n"

        if len(message) + len(line) > DISCORD_MESSAGE_LIMIT:
            break

        message += line
        included_items.append(item)

    if not included_items:
        log(f"{market} {digest_type} news digest was too long before any article could be added.")
        return "none"

    sent = send_discord_message(webhook_url, message)

    if sent:
        sent_news = load_log(NEWS_LOG_FILE)

        for item in included_items:
            sent_news.add(article_key(item))

        save_log(NEWS_LOG_FILE, sent_news)
        return "sent"

    return "failed"


def get_news_key(market):
    current_bucket = int(time.time() // (NEWS_INTERVAL_HOURS * 3600))
    return f"scheduled_{market}_{current_bucket}"


def get_breaking_news_key(market):
    current_bucket = int(time.time() // (BREAKING_NEWS_INTERVAL_MINUTES * 60))
    return f"breaking_{market}_{current_bucket}"


def news_already_checked(market):
    sent_news_schedules = load_log(NEWS_SCHEDULE_LOG_FILE)
    return get_news_key(market) in sent_news_schedules


def breaking_news_already_checked(market):
    sent_breaking_schedules = load_log(BREAKING_NEWS_SCHEDULE_LOG_FILE)
    return get_breaking_news_key(market) in sent_breaking_schedules


def mark_news_checked(market):
    sent_news_schedules = load_log(NEWS_SCHEDULE_LOG_FILE)
    sent_news_schedules.add(get_news_key(market))
    save_log(NEWS_SCHEDULE_LOG_FILE, sent_news_schedules)


def mark_breaking_news_checked(market):
    sent_breaking_schedules = load_log(BREAKING_NEWS_SCHEDULE_LOG_FILE)
    sent_breaking_schedules.add(get_breaking_news_key(market))
    save_log(BREAKING_NEWS_SCHEDULE_LOG_FILE, sent_breaking_schedules)


def maybe_send_breaking_news():
    if not SEND_BREAKING_NEWS:
        return

    if not breaking_news_already_checked("Crypto"):
        crypto_status = send_news_digest(CRYPTO_TICKERS, "Crypto", digest_type="breaking")

        if crypto_status in ["sent", "none"]:
            mark_breaking_news_checked("Crypto")

    interruptible_sleep(1)

    if not breaking_news_already_checked("Stock"):
        stock_status = send_news_digest(STOCK_TICKERS, "Stock", digest_type="breaking")

        if stock_status in ["sent", "none"]:
            mark_breaking_news_checked("Stock")


def maybe_send_scheduled_news():
    if not SEND_NEWS:
        return

    if not news_already_checked("Crypto"):
        crypto_status = send_news_digest(CRYPTO_TICKERS, "Crypto", digest_type="scheduled")

        if crypto_status in ["sent", "none"]:
            mark_news_checked("Crypto")

    interruptible_sleep(1)

    if not news_already_checked("Stock"):
        stock_status = send_news_digest(STOCK_TICKERS, "Stock", digest_type="scheduled")

        if stock_status in ["sent", "none"]:
            mark_news_checked("Stock")


# ======================================================
# DISCORD TRADE ALERTS
# ======================================================

def send_startup_message():
    if not SEND_STARTUP_MESSAGE:
        return

    enabled_sources = []

    if BOT_NEWS_YFINANCE_ENABLED:
        enabled_sources.append("Yahoo Finance")

    if NEWSAPI_KEY:
        enabled_sources.append("NewsAPI")

    if FINNHUB_API_KEY:
        enabled_sources.append("Finnhub")

    fields = [
        {"name": "Status", "value": "Railway worker is online.", "inline": False},
        {"name": "Bot Version", "value": BOT_VERSION, "inline": False},
        {"name": "Scan Interval", "value": f"{SCAN_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "Minimum Confidence", "value": f"{MIN_CONFIDENCE}%", "inline": True},
        {"name": "Summaries", "value": "On" if SEND_SUMMARIES else "Off", "inline": True},
        {"name": "News", "value": "On" if SEND_NEWS else "Off", "inline": True},
        {"name": "News Interval", "value": f"{NEWS_INTERVAL_HOURS} hours", "inline": True},
        {"name": "Breaking News", "value": "On" if SEND_BREAKING_NEWS else "Off", "inline": True},
        {"name": "Breaking Interval", "value": f"{BREAKING_NEWS_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "News Sources", "value": ", ".join(enabled_sources) if enabled_sources else "None configured", "inline": False},
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


# ======================================================
# SUMMARY FUNCTIONS
# ======================================================

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
        f"{chr(10).join(buy_lines[:SUMMARY_MAX_LINES_PER_SECTION]) if buy_lines else 'None'}\n\n"
        "🟡 HOLD SIGNALS\n"
        f"{chr(10).join(hold_lines[:SUMMARY_MAX_LINES_PER_SECTION]) if hold_lines else 'None'}\n\n"
        "🔴 SELL SIGNALS\n"
        f"{chr(10).join(sell_lines[:SUMMARY_MAX_LINES_PER_SECTION]) if sell_lines else 'None'}\n\n"
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
# GOOGLE SHEETS TRACKING
# ======================================================

LIVE_SCANNER_HEADERS = [
    "Timestamp", "Ticker", "Market", "Price", "Daily Change %",
    "RSI", "MACD", "MACD Signal", "Technical Score", "Final Score",
    "Confidence %", "Signal"
]

SCAN_HISTORY_HEADERS = LIVE_SCANNER_HEADERS

TRADE_ALERT_HEADERS = [
    "Timestamp", "Ticker", "Market", "Price", "Daily Change %",
    "Signal", "Confidence %", "RSI", "MACD", "Final Score", "Alert Sent"
]

SIGNAL_TRACKER_HEADERS = [
    "Signal ID", "Opened At", "Last Updated", "Ticker", "Market",
    "Signal", "Entry Price", "Current Price", "Raw Change %",
    "Signal Performance %", "Confidence %", "RSI", "Status"
]

BOT_PERFORMANCE_HEADERS = [
    "Signal", "Count", "Average Signal Performance %", "Wins", "Losses", "Win Rate %"
]

BEST_TICKERS_HEADERS = [
    "Ticker", "Market", "Signal", "Count", "Average Signal Performance %",
    "Wins", "Losses", "Win Rate %", "Last Updated"
]

SYSTEM_STATUS_HEADERS = [
    "Metric", "Value"
]

GOOGLE_SHEETS_TAB_COLORS = {
    "Live Scanner": "#3399FF",
    "Scan History": "#666666",
    "Trade Alerts Log": "#FF8C00",
    "Signal Tracker": "#1AB359",
    "Bot Performance": "#8C59E6",
    "Best Tickers": "#F2BF26",
    "System Status": "#CC3333",
}


def hex_to_google_rgb(hex_color):
    """Convert #RRGGBB to Google Sheets API RGB dict."""
    color = str(hex_color or "").strip().lstrip("#")
    if len(color) != 6:
        return None
    try:
        return {
            "red": int(color[0:2], 16) / 255,
            "green": int(color[2:4], 16) / 255,
            "blue": int(color[4:6], 16) / 255,
        }
    except Exception:
        return None


def safe_update_tab_color(worksheet, color):
    """
    Best-effort tab color update across gspread versions.
    Some versions expect a hex string, while others expect a Google RGB dict.
    """
    if not color:
        return False

    try:
        worksheet.update_tab_color(color)
        return True
    except Exception as first_error:
        rgb_color = hex_to_google_rgb(color)
        if rgb_color:
            try:
                worksheet.update_tab_color(rgb_color)
                return True
            except Exception as second_error:
                log(f"Google Sheets tab color skipped for {worksheet.title}: {second_error}")
                return False

        log(f"Google Sheets tab color skipped for {worksheet.title}: {first_error}")
        return False


def safe_sheet_update(worksheet, range_name, values):
    """
    Keeps Google Sheets writes compatible across gspread versions.
    Some versions prefer update(range_name, values), newer versions also
    accept named arguments. This wrapper prevents deployment surprises.
    """
    try:
        return worksheet.update(range_name=range_name, values=values)
    except TypeError:
        return worksheet.update(range_name, values)


def safe_append_rows(worksheet, rows):
    if not rows:
        return None

    try:
        return worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    except TypeError:
        return worksheet.append_rows(rows)


def safe_batch_update(worksheet, updates):
    if not updates:
        return None

    try:
        return worksheet.batch_update(updates)
    except TypeError:
        # Older gspread versions may not accept the same batch payload options,
        # but the core list-of-updates format is still supported.
        return worksheet.batch_update(updates)


def format_worksheet_for_readability(worksheet, title, headers):
    """
    Best-effort formatting only. If Google Sheets rejects a formatting call,
    the bot still keeps scanning and syncing instead of crashing.

    Formatting is cached per runtime to avoid burning Google API quota every scan.
    Set GOOGLE_SHEETS_FORMAT_EVERY_SYNC=true only if you intentionally want the
    bot to re-apply formatting every sync.
    """
    if not GOOGLE_SHEETS_FORMATTING_ENABLED:
        return

    cache_key = str(title)
    if cache_key in FORMATTED_WORKSHEETS and not GOOGLE_SHEETS_FORMAT_EVERY_SYNC:
        return

    try:
        worksheet.freeze(rows=1)
    except Exception as error:
        log(f"Google Sheets freeze skipped for {title}: {error}")

    color = GOOGLE_SHEETS_TAB_COLORS.get(title)
    if color:
        safe_update_tab_color(worksheet, color)

    try:
        worksheet.format(
            "1:1",
            {
                "horizontalAlignment": "CENTER",
                "backgroundColor": {"red": 0.12, "green": 0.12, "blue": 0.12},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
            }
        )
    except Exception as error:
        log(f"Google Sheets header formatting skipped for {title}: {error}")

    try:
        worksheet.format(
            "A:Z",
            {
                "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP",
            }
        )
    except Exception as error:
        log(f"Google Sheets body formatting skipped for {title}: {error}")

    try:
        worksheet.columns_auto_resize(0, max(len(headers), 2))
    except Exception as error:
        log(f"Google Sheets auto resize skipped for {title}: {error}")

    FORMATTED_WORKSHEETS.add(cache_key)


def get_sheet_health_summary(scanned_count, candidates, sent_count, skipped_duplicates, alerted_count, ticker_errors, post_scan_errors):
    return {
        "Last Sync Result": "OK" if post_scan_errors == 0 and ticker_errors == 0 else "CHECK WARNINGS",
        "Scanned Count": scanned_count,
        "Alert Candidates": candidates,
        "Alerts Sent": sent_count,
        "Alerts Skipped As Duplicates": skipped_duplicates,
        "Alerted Rows Written": alerted_count,
        "Ticker Errors": ticker_errors,
        "Post Scan Errors": post_scan_errors,
    }



def bool_text(value):
    return "YES" if bool(value) else "NO"


def safe_float(value, default=0):
    try:
        if value is None:
            return default

        cleaned = str(value).replace("%", "").replace(",", "").strip()

        if cleaned == "":
            return default

        return float(cleaned)

    except Exception:
        return default


def seconds_since(timestamp):
    return time.time() - timestamp


def should_sync_google_sheets():
    global LAST_GOOGLE_SHEETS_SYNC_TIME

    if not GOOGLE_SHEETS_ENABLED:
        return False

    elapsed = seconds_since(LAST_GOOGLE_SHEETS_SYNC_TIME)
    required = GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES * 60

    return elapsed >= required


def is_directional_signal(signal):
    signal_text = str(signal or "")
    return "BUY" in signal_text or "SELL" in signal_text


def filter_tracker_rows(rows):
    if GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER:
        return rows

    return [
        row for row in rows
        if is_directional_signal(row.get("AI Signal", ""))
    ]


def prune_worksheet_rows(worksheet, max_data_rows):
    try:
        all_values = worksheet.get_all_values()
        current_data_rows = max(0, len(all_values) - 1)

        if current_data_rows <= max_data_rows:
            return

        rows_to_delete = current_data_rows - max_data_rows
        start_index = 2
        end_index = rows_to_delete + 1

        worksheet.delete_rows(start_index, end_index)
        log(f"Pruned {rows_to_delete} old rows from {worksheet.title}.")

    except Exception as error:
        log(f"Google Sheets prune error for {worksheet.title}: {error}")


def update_system_status(spreadsheet, scanned_count, candidates, sent_count, skipped_duplicates, alerted_count, ticker_errors=0, post_scan_errors=0):
    try:
        status_sheet = get_or_create_worksheet(spreadsheet, "System Status", SYSTEM_STATUS_HEADERS)

        sheet_health = get_sheet_health_summary(
            scanned_count,
            candidates,
            sent_count,
            skipped_duplicates,
            alerted_count,
            ticker_errors,
            post_scan_errors
        )

        status_rows = [
            ["Last Sync", now_text()],
            ["Last Sync Result", sheet_health["Last Sync Result"]],
            ["Current UTC Time", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            ["Bot Version", BOT_VERSION],
            ["Bot Timezone", BOT_TIMEZONE],
            ["Bot Run Once", str(BOT_RUN_ONCE)],
            ["Discord Dry Run", str(BOT_DISCORD_DRY_RUN)],
            ["Strict Config", str(BOT_STRICT_CONFIG)],
            ["Market Data Scan Enabled", str(BOT_SCAN_MARKET_DATA_ENABLED)],
            ["YFinance News Enabled", str(BOT_NEWS_YFINANCE_ENABLED)],
            ["Max Scan Seconds", BOT_MAX_SCAN_SECONDS],
            ["Discord Message Limit", DISCORD_MESSAGE_LIMIT],
            ["Summary Max Lines Per Section", SUMMARY_MAX_LINES_PER_SECTION],
            ["Crypto Tickers", ", ".join(CRYPTO_TICKERS)],
            ["Stock Tickers", ", ".join(STOCK_TICKERS)],
            ["Bot Status File", BOT_STATUS_FILE],
            ["Bot Uptime Minutes", bot_uptime_minutes()],
            ["Scanned Count", scanned_count],
            ["Ticker Errors", ticker_errors],
            ["Post Scan Step Errors", post_scan_errors],
            ["Alert Candidates", candidates],
            ["Alerts Sent", sent_count],
            ["Alerts Skipped As Duplicates", skipped_duplicates],
            ["Alerted Rows Written", alerted_count],
            ["Google Sheets Scan History Logging", str(GOOGLE_SHEETS_LOG_SCAN_HISTORY)],
            ["Google Sheets Tracker Only Alerts", str(GOOGLE_SHEETS_LOG_ONLY_ALERTS_TO_TRACKER)],
            ["Google Sheets Include HOLD In Tracker", str(GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER)],
            ["Google Sheets Sync Interval Minutes", GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES],
            ["Google Sheets Formatting Enabled", str(GOOGLE_SHEETS_FORMATTING_ENABLED)],
            ["Google Sheets Format Every Sync", str(GOOGLE_SHEETS_FORMAT_EVERY_SYNC)],
            ["Google Sheets Retry Interval Minutes", GOOGLE_SHEETS_RETRY_INTERVAL_MINUTES],
            ["Max Scan History Rows", GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS],
            ["Max Signal Tracker Rows", GOOGLE_SHEETS_MAX_TRACKER_ROWS],
            ["YFinance News Max Tickers Per Market", YFINANCE_NEWS_MAX_TICKERS_PER_MARKET],
            ["YFinance News Delay Seconds", YFINANCE_NEWS_DELAY_SECONDS],
            ["YFinance Timeout Seconds", YFINANCE_TIMEOUT_SECONDS],
            ["YFinance History Fallback", str(YFINANCE_USE_HISTORY_FALLBACK)],
            ["Finnhub News Max Tickers Per Scan", FINNHUB_NEWS_MAX_TICKERS_PER_SCAN],
            ["Finnhub News Delay Seconds", FINNHUB_NEWS_DELAY_SECONDS],
            ["Error Alerts Enabled", str(BOT_SEND_ERROR_ALERTS)],
            ["Error Alert Cooldown Minutes", BOT_ERROR_ALERT_COOLDOWN_MINUTES],
            ["Heartbeat Enabled", str(BOT_HEARTBEAT_ENABLED)],
            ["Heartbeat Interval Hours", BOT_HEARTBEAT_INTERVAL_HOURS],
            ["Dedicated Heartbeat Webhook", "SET" if HEARTBEAT_WEBHOOK_URL else "MISSING"],
            ["Last Error Alert Time", format_epoch_time(LAST_ERROR_ALERT_TIME)],
            ["Last Google Sheets Connection Error Time", format_epoch_time(LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME)],
            ["NewsAPI Enabled", bool_text(NEWSAPI_KEY)],
            ["Finnhub Enabled", bool_text(FINNHUB_API_KEY)],
            ["Crypto Trade Webhook", "SET" if CRYPTO_TRADE_WEBHOOK_URL else "MISSING"],
            ["Stock Trade Webhook", "SET" if STOCK_TRADE_WEBHOOK_URL else "MISSING"],
            ["Generic Trade Webhook Fallback", "SET" if TRADE_WEBHOOK_URL else "MISSING"],
            ["Crypto News Webhook", "SET" if CRYPTO_NEWS_WEBHOOK_URL else "MISSING"],
            ["Stock News Webhook", "SET" if STOCK_NEWS_WEBHOOK_URL else "MISSING"],
            ["Crypto Summary Webhook", "SET" if CRYPTO_SUMMARY_WEBHOOK_URL else "MISSING"],
            ["Stock Summary Webhook", "SET" if STOCK_SUMMARY_WEBHOOK_URL else "MISSING"],
        ]

        status_sheet.clear()
        safe_sheet_update(status_sheet, "A1", [SYSTEM_STATUS_HEADERS] + status_rows)

    except Exception as error:
        log(f"Google Sheets system status update error: {error}")


def google_sheets_available():
    if not GOOGLE_SHEETS_ENABLED:
        return False

    if not GOOGLE_SHEET_ID:
        log("Google Sheets disabled: GOOGLE_SHEET_ID missing.")
        return False

    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        log("Google Sheets disabled: GOOGLE_SERVICE_ACCOUNT_JSON missing.")
        return False

    if gspread is None or Credentials is None:
        log("Google Sheets disabled: gspread or google-auth is not installed.")
        return False

    return True


def get_google_spreadsheet():
    global GOOGLE_SHEETS_CLIENT
    global GOOGLE_SPREADSHEET
    global LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME

    if not google_sheets_available():
        return None

    if GOOGLE_SPREADSHEET is not None:
        return GOOGLE_SPREADSHEET

    if LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME:
        retry_elapsed = seconds_since(LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME)
        retry_required = GOOGLE_SHEETS_RETRY_INTERVAL_MINUTES * 60

        if retry_elapsed < retry_required:
            log("Google Sheets connection retry skipped until cooldown expires.")
            return None

    try:
        service_account_json = GOOGLE_SERVICE_ACCOUNT_JSON.strip()

        if service_account_json.startswith("'") and service_account_json.endswith("'"):
            service_account_json = service_account_json[1:-1]

        if service_account_json.startswith('"') and service_account_json.endswith('"'):
            service_account_json = service_account_json[1:-1]

        service_account_info = json.loads(service_account_json)

        if "private_key" in service_account_info:
            service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        credentials = Credentials.from_service_account_info(
            service_account_info,
            scopes=scopes
        )

        GOOGLE_SHEETS_CLIENT = gspread.authorize(credentials)
        GOOGLE_SPREADSHEET = GOOGLE_SHEETS_CLIENT.open_by_key(GOOGLE_SHEET_ID)

        log("Google Sheets connection successful.")
        return GOOGLE_SPREADSHEET

    except Exception as error:
        LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME = time.time()
        GOOGLE_SPREADSHEET = None
        GOOGLE_SHEETS_CLIENT = None
        log(f"Google Sheets connection error: {error}")
        return None


def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        worksheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(20, len(headers) + 2))

    try:
        current_headers = worksheet.row_values(1)

        if current_headers != headers:
            safe_sheet_update(worksheet, "A1", [headers])

        try:
            if worksheet.col_count < len(headers):
                worksheet.resize(rows=worksheet.row_count, cols=len(headers))
        except Exception:
            pass

    except Exception as error:
        log(f"Google Sheets header update error for {title}: {error}")

    format_worksheet_for_readability(worksheet, title, headers)
    return worksheet


def row_from_scan(row):
    return [
        now_text(),
        row.get("Ticker", ""),
        row.get("Market", ""),
        row.get("Price", ""),
        row.get("Daily Change %", ""),
        row.get("RSI", ""),
        row.get("MACD", ""),
        row.get("MACD Signal", ""),
        row.get("Technical Score", ""),
        row.get("Final Score", ""),
        row.get("AI Confidence %", ""),
        row.get("AI Signal", "")
    ]


def row_from_alert(row, alert_sent):
    return [
        now_text(),
        row.get("Ticker", ""),
        row.get("Market", ""),
        row.get("Price", ""),
        row.get("Daily Change %", ""),
        row.get("AI Signal", ""),
        row.get("AI Confidence %", ""),
        row.get("RSI", ""),
        row.get("MACD", ""),
        row.get("Final Score", ""),
        "YES" if alert_sent else "NO"
    ]


def signal_tracker_id(row):
    today = now_dt().strftime("%Y-%m-%d")
    return f"{today}_{row.get('Ticker', '')}_{row.get('AI Signal', '')}"


def upsert_signal_tracker(worksheet, rows):
    if not rows:
        return

    try:
        existing_values = worksheet.get_all_values()
        existing_rows = existing_values[1:] if len(existing_values) > 1 else []

        row_index_by_id = {}

        for index, existing_row in enumerate(existing_rows, start=2):
            if existing_row:
                row_index_by_id[existing_row[0]] = index

        updates = []
        appends = []

        for row in rows:
            signal_id = signal_tracker_id(row)
            current_price = safe_float(row.get("Price", 0))
            entry_price = current_price

            if signal_id in row_index_by_id:
                sheet_row_number = row_index_by_id[signal_id]
                existing_row = existing_values[sheet_row_number - 1]

                try:
                    entry_price = safe_float(existing_row[6], current_price)
                except Exception:
                    entry_price = current_price

                opened_at = existing_row[1] if len(existing_row) > 1 else now_text()
            else:
                sheet_row_number = None
                opened_at = now_text()

            if entry_price == 0:
                raw_change_percent = 0
            else:
                raw_change_percent = ((current_price - entry_price) / entry_price) * 100

            signal = str(row.get("AI Signal", ""))

            if "SELL" in signal:
                signal_performance_percent = raw_change_percent * -1
            elif "BUY" in signal:
                signal_performance_percent = raw_change_percent
            else:
                signal_performance_percent = raw_change_percent

            status = (
                "WINNING"
                if signal_performance_percent > 0
                else "LOSING"
                if signal_performance_percent < 0
                else "FLAT"
            )

            tracker_row = [
                signal_id,
                opened_at,
                now_text(),
                row.get("Ticker", ""),
                row.get("Market", ""),
                signal,
                round(entry_price, 4),
                round(current_price, 4),
                round(raw_change_percent, 2),
                round(signal_performance_percent, 2),
                row.get("AI Confidence %", ""),
                row.get("RSI", ""),
                status
            ]

            if sheet_row_number:
                updates.append({
                    "range": f"A{sheet_row_number}:M{sheet_row_number}",
                    "values": [tracker_row]
                })
            else:
                appends.append(tracker_row)

        if updates:
            safe_batch_update(worksheet, updates)

        if appends:
            safe_append_rows(worksheet, appends)

    except Exception as error:
        log(f"Google Sheets signal tracker error: {error}")


def update_bot_performance(spreadsheet):
    try:
        tracker = get_or_create_worksheet(spreadsheet, "Signal Tracker", SIGNAL_TRACKER_HEADERS)
        performance = get_or_create_worksheet(spreadsheet, "Bot Performance", BOT_PERFORMANCE_HEADERS)

        values = tracker.get_all_records()

        grouped = {}

        for row in values:
            signal = row.get("Signal", "")
            if not signal:
                continue

            if not GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER and not is_directional_signal(signal):
                continue

            try:
                performance_percent = safe_float(
                    row.get("Signal Performance %", row.get("Change %", 0))
                )
            except Exception:
                performance_percent = 0

            if signal not in grouped:
                grouped[signal] = []

            grouped[signal].append(performance_percent)

        output_rows = []

        for signal, changes in grouped.items():
            count = len(changes)
            avg_change = sum(changes) / count if count else 0
            wins = len([change for change in changes if change > 0])
            losses = len([change for change in changes if change < 0])
            win_rate = (wins / count) * 100 if count else 0

            output_rows.append([
                signal,
                count,
                round(avg_change, 2),
                wins,
                losses,
                round(win_rate, 2)
            ])

        output_rows = sorted(output_rows, key=lambda item: item[2], reverse=True)

        performance.clear()
        safe_sheet_update(performance, "A1", [BOT_PERFORMANCE_HEADERS] + output_rows)

    except Exception as error:
        log(f"Google Sheets performance update error: {error}")


def update_best_tickers(spreadsheet):
    try:
        tracker = get_or_create_worksheet(spreadsheet, "Signal Tracker", SIGNAL_TRACKER_HEADERS)
        best_tickers = get_or_create_worksheet(spreadsheet, "Best Tickers", BEST_TICKERS_HEADERS)

        values = tracker.get_all_records()
        grouped = {}

        for row in values:
            ticker = row.get("Ticker", "")
            market = row.get("Market", "")
            signal = row.get("Signal", "")

            if not ticker or not signal:
                continue

            if not GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER and not is_directional_signal(signal):
                continue

            try:
                performance_percent = safe_float(
                    row.get("Signal Performance %", row.get("Change %", 0))
                )
            except Exception:
                performance_percent = 0

            key = (ticker, market, signal)

            if key not in grouped:
                grouped[key] = []

            grouped[key].append(performance_percent)

        output_rows = []

        for (ticker, market, signal), changes in grouped.items():
            count = len(changes)
            avg_change = sum(changes) / count if count else 0
            wins = len([change for change in changes if change > 0])
            losses = len([change for change in changes if change < 0])
            win_rate = (wins / count) * 100 if count else 0

            output_rows.append([
                ticker,
                market,
                signal,
                count,
                round(avg_change, 2),
                wins,
                losses,
                round(win_rate, 2),
                now_text()
            ])

        output_rows = sorted(output_rows, key=lambda item: (item[4], item[7]), reverse=True)

        best_tickers.clear()
        safe_sheet_update(best_tickers, "A1", [BEST_TICKERS_HEADERS] + output_rows)

    except Exception as error:
        log(f"Google Sheets best tickers update error: {error}")


def sync_google_sheets(scanned_rows, alerted_rows, candidates=0, sent_count=0, skipped_duplicates=0, ticker_errors=0, post_scan_errors=0):
    global LAST_GOOGLE_SHEETS_SYNC_TIME
    global LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME
    global GOOGLE_SPREADSHEET
    global GOOGLE_SHEETS_CLIENT

    if not should_sync_google_sheets():
        log("Google Sheets sync skipped by interval setting.")
        return

    spreadsheet = get_google_spreadsheet()

    if spreadsheet is None:
        return

    try:
        live_scanner = get_or_create_worksheet(spreadsheet, "Live Scanner", LIVE_SCANNER_HEADERS)
        scan_history = get_or_create_worksheet(spreadsheet, "Scan History", SCAN_HISTORY_HEADERS)
        trade_alerts = get_or_create_worksheet(spreadsheet, "Trade Alerts Log", TRADE_ALERT_HEADERS)
        signal_tracker = get_or_create_worksheet(spreadsheet, "Signal Tracker", SIGNAL_TRACKER_HEADERS)
        get_or_create_worksheet(spreadsheet, "Bot Performance", BOT_PERFORMANCE_HEADERS)
        get_or_create_worksheet(spreadsheet, "Best Tickers", BEST_TICKERS_HEADERS)
        get_or_create_worksheet(spreadsheet, "System Status", SYSTEM_STATUS_HEADERS)

        live_rows = [row_from_scan(row) for row in scanned_rows]

        live_scanner.clear()
        safe_sheet_update(live_scanner, "A1", [LIVE_SCANNER_HEADERS] + live_rows)

        if live_rows and GOOGLE_SHEETS_LOG_SCAN_HISTORY:
            safe_append_rows(scan_history, live_rows)
            prune_worksheet_rows(scan_history, GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS)

        if alerted_rows:
            alert_rows = [row_from_alert(row, True) for row in alerted_rows]
            safe_append_rows(trade_alerts, alert_rows)

        tracker_source_rows = alerted_rows if GOOGLE_SHEETS_LOG_ONLY_ALERTS_TO_TRACKER else scanned_rows
        tracker_rows = filter_tracker_rows(tracker_source_rows)
        upsert_signal_tracker(signal_tracker, tracker_rows)
        prune_worksheet_rows(signal_tracker, GOOGLE_SHEETS_MAX_TRACKER_ROWS)
        update_bot_performance(spreadsheet)
        update_best_tickers(spreadsheet)
        update_system_status(
            spreadsheet,
            len(scanned_rows),
            candidates,
            sent_count,
            skipped_duplicates,
            len(alerted_rows),
            ticker_errors,
            post_scan_errors
        )

        LAST_GOOGLE_SHEETS_SYNC_TIME = time.time()
        log("Google Sheets sync complete.")

    except Exception as error:
        LAST_GOOGLE_SHEETS_CONNECTION_ERROR_TIME = time.time()
        GOOGLE_SPREADSHEET = None
        GOOGLE_SHEETS_CLIENT = None
        log(f"Google Sheets sync error: {error}")


# ======================================================
# HEARTBEAT
# ======================================================

def get_heartbeat_key():
    current_bucket = int(time.time() // (BOT_HEARTBEAT_INTERVAL_HOURS * 3600))
    return f"heartbeat_{current_bucket}"


def heartbeat_already_sent():
    sent_heartbeats = load_log(HEARTBEAT_LOG_FILE)
    return get_heartbeat_key() in sent_heartbeats


def mark_heartbeat_sent():
    sent_heartbeats = load_log(HEARTBEAT_LOG_FILE)
    sent_heartbeats.add(get_heartbeat_key())
    save_log(HEARTBEAT_LOG_FILE, sent_heartbeats)


def send_heartbeat(scanned_count=0, ticker_errors=0, post_scan_errors=0):
    if not BOT_HEARTBEAT_ENABLED:
        return False

    if heartbeat_already_sent():
        return False

    webhook_url = get_heartbeat_webhook()
    if not webhook_url:
        log("Heartbeat skipped: no heartbeat/error/trade webhook available.")
        return False

    total_errors = int(ticker_errors or 0) + int(post_scan_errors or 0)

    if total_errors == 0:
        status_text = "✅ Running Normally"
        heartbeat_color = 5763719  # green
        title = "🤖 AI Trading Bot Heartbeat"
    else:
        status_text = "⚠️ Running With Warnings"
        heartbeat_color = 16776960  # yellow
        title = "⚠️ AI Trading Bot Heartbeat"

    google_sheets_status = "Enabled" if GOOGLE_SHEETS_ENABLED else "Disabled"
    news_sources = []

    if NEWSAPI_KEY:
        news_sources.append("NewsAPI")

    if FINNHUB_API_KEY:
        news_sources.append("Finnhub")

    if BOT_NEWS_YFINANCE_ENABLED:
        news_sources.append("Yahoo Finance")

    fields = [
        {
            "name": "System Status",
            "value": status_text,
            "inline": False
        },
        {
            "name": "Uptime",
            "value": format_uptime(),
            "inline": True
        },
        {
            "name": "Last Scan",
            "value": f"{scanned_count} tickers",
            "inline": True
        },
        {
            "name": "Next Scan",
            "value": f"About {SCAN_INTERVAL_MINUTES} min",
            "inline": True
        },
        {
            "name": "Ticker Errors",
            "value": str(ticker_errors),
            "inline": True
        },
        {
            "name": "Post-Scan Errors",
            "value": str(post_scan_errors),
            "inline": True
        },
        {
            "name": "Alert Threshold",
            "value": f"{MIN_CONFIDENCE}%",
            "inline": True
        },
        {
            "name": "Scanner",
            "value": "Active" if BOT_SCAN_MARKET_DATA_ENABLED else "Disabled",
            "inline": True
        },
        {
            "name": "Google Sheets",
            "value": google_sheets_status,
            "inline": True
        },
        {
            "name": "Discord Alerts",
            "value": "Operational",
            "inline": True
        },
        {
            "name": "News Sources",
            "value": ", ".join(news_sources) if news_sources else "None configured",
            "inline": False
        },
        {
            "name": "Version",
            "value": BOT_VERSION,
            "inline": False
        },
        {
            "name": "Time",
            "value": now_text(),
            "inline": False
        }
    ]

    sent = send_discord_embed(
        webhook_url,
        title,
        heartbeat_color,
        fields
    )

    if sent:
        mark_heartbeat_sent()

    return sent


# ======================================================
# SCAN LOOP
# ======================================================

def run_scan():
    scan_started_at = time.time()
    log("=" * 50)
    log(f"Running bot scan: {now_dt().strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Bot uptime minutes: {bot_uptime_minutes()}")

    sent_signals = load_log(SIGNAL_LOG_FILE)
    today = now_dt().strftime("%Y-%m-%d")

    scanned_rows = []
    scanned = 0
    candidates = 0
    sent_count = 0
    skipped_duplicates = 0
    ticker_errors = 0
    alerted_rows = []

    for ticker in ALL_TICKERS:
        if time.time() - scan_started_at > BOT_MAX_SCAN_SECONDS:
            ticker_errors += 1
            log(f"Max scan time reached after {round(time.time() - scan_started_at, 2)} seconds. Ending ticker loop early.")
            break

        if SHUTDOWN_REQUESTED:
            log("Scan interrupted by shutdown request before all tickers completed.")
            break

        try:
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
                alerted_rows.append(row)
                sent_signals.add(alert_key)
                save_log(SIGNAL_LOG_FILE, sent_signals)
                interruptible_sleep(1)

        except Exception as error:
            ticker_errors += 1
            log(f"{ticker}: unexpected scoring error: {error}")

        finally:
            if YFINANCE_TICKER_DELAY_SECONDS > 0:
                interruptible_sleep(YFINANCE_TICKER_DELAY_SECONDS)

    post_scan_errors = 0

    if BOT_SEND_NO_DATA_ALERTS and scanned == 0 and not SHUTDOWN_REQUESTED:
        send_error_alert("No ticker data was scanned. Check yfinance/network availability and Railway outbound access.")

    if SHUTDOWN_REQUESTED:
        result = {
            "scanned": scanned,
            "ticker_errors": ticker_errors,
            "post_scan_errors": 0,
            "candidates": candidates,
            "sent": sent_count,
            "skipped_duplicates": skipped_duplicates,
            "duration_seconds": round(time.time() - scan_started_at, 2),
            "max_scan_seconds": BOT_MAX_SCAN_SECONDS,
            "interrupted": True,
        }
        write_status_file(result)
        return result

    _, step_error = run_safe_step("Crypto summary", maybe_send_summary, scanned_rows, "Crypto")
    post_scan_errors += int(step_error)
    interruptible_sleep(1)

    _, step_error = run_safe_step("Stock summary", maybe_send_summary, scanned_rows, "Stock")
    post_scan_errors += int(step_error)
    interruptible_sleep(1)

    # Run breaking news before scheduled news so urgent headlines are not swallowed
    # by the regular digest duplicate log.
    _, step_error = run_safe_step("Breaking news", maybe_send_breaking_news)
    post_scan_errors += int(step_error)
    interruptible_sleep(1)

    _, step_error = run_safe_step("Scheduled news", maybe_send_scheduled_news)
    post_scan_errors += int(step_error)

    _, step_error = run_safe_step(
        "Google Sheets sync",
        sync_google_sheets,
        scanned_rows,
        alerted_rows,
        candidates,
        sent_count,
        skipped_duplicates,
        ticker_errors,
        post_scan_errors
    )
    post_scan_errors += int(step_error)

    _, step_error = run_safe_step(
        "Heartbeat",
        send_heartbeat,
        len(scanned_rows),
        ticker_errors,
        post_scan_errors
    )
    post_scan_errors += int(step_error)

    log("Scan complete.")
    log(f"Scanned: {scanned}")
    log(f"Ticker errors: {ticker_errors}")
    log(f"Post scan step errors: {post_scan_errors}")
    log(f"Candidates: {candidates}")
    log(f"Sent: {sent_count}")
    log(f"Skipped duplicates: {skipped_duplicates}")

    result = {
        "scanned": scanned,
        "ticker_errors": ticker_errors,
        "post_scan_errors": post_scan_errors,
        "candidates": candidates,
        "sent": sent_count,
        "skipped_duplicates": skipped_duplicates,
        "duration_seconds": round(time.time() - scan_started_at, 2),
        "max_scan_seconds": BOT_MAX_SCAN_SECONDS,
        "interrupted": False,
    }
    write_status_file(result)
    return result


def main():
    configure_signal_handlers()
    enabled_sources = []

    if BOT_NEWS_YFINANCE_ENABLED:
        enabled_sources.append("Yahoo Finance")

    if NEWSAPI_KEY:
        enabled_sources.append("NewsAPI")

    if FINNHUB_API_KEY:
        enabled_sources.append("Finnhub")

    log("AI Trading Bot started.")
    log(f"Bot version: {BOT_VERSION}")
    log(f"Run once: {BOT_RUN_ONCE}")
    log(f"Discord dry run: {BOT_DISCORD_DRY_RUN}")
    log(f"Skip startup scan: {BOT_SKIP_STARTUP_SCAN}")
    log(f"No data alerts: {BOT_SEND_NO_DATA_ALERTS}")
    log(f"Strict config: {BOT_STRICT_CONFIG}")
    log(f"Market data scan enabled: {BOT_SCAN_MARKET_DATA_ENABLED}")
    log(f"Yahoo/yfinance news enabled: {BOT_NEWS_YFINANCE_ENABLED}")
    log(f"Max scan seconds: {BOT_MAX_SCAN_SECONDS}")
    log(f"Discord message limit: {DISCORD_MESSAGE_LIMIT}")
    log(f"Summary max lines per section: {SUMMARY_MAX_LINES_PER_SECTION}")
    log(f"Crypto tickers: {', '.join(CRYPTO_TICKERS)}")
    log(f"Stock tickers: {', '.join(STOCK_TICKERS)}")
    log(f"Scan interval: {SCAN_INTERVAL_MINUTES} minutes")
    log(f"Minimum confidence: {MIN_CONFIDENCE}%")
    log(f"Bot timezone: {BOT_TIMEZONE}")
    log(f"Summaries enabled: {SEND_SUMMARIES}")
    log(f"Summary interval: {SUMMARY_INTERVAL_HOURS} hours")
    log(f"News enabled: {SEND_NEWS}")
    log(f"News interval: {NEWS_INTERVAL_HOURS} hours")
    log(f"Breaking news enabled: {SEND_BREAKING_NEWS}")
    log(f"Breaking news interval: {BREAKING_NEWS_INTERVAL_MINUTES} minutes")
    log(f"News sources: {', '.join(enabled_sources) if enabled_sources else 'None configured'}")
    log(f"Google Sheets enabled: {GOOGLE_SHEETS_ENABLED}")
    log(f"Google Sheets scan history logging: {GOOGLE_SHEETS_LOG_SCAN_HISTORY}")
    log(f"Google Sheets tracker only alerts: {GOOGLE_SHEETS_LOG_ONLY_ALERTS_TO_TRACKER}")
    log(f"Google Sheets include HOLD in tracker: {GOOGLE_SHEETS_INCLUDE_HOLD_IN_TRACKER}")
    log(f"Google Sheets sync interval minutes: {GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES}")
    log(f"Google Sheets formatting enabled: {GOOGLE_SHEETS_FORMATTING_ENABLED}")
    log(f"Google Sheets format every sync: {GOOGLE_SHEETS_FORMAT_EVERY_SYNC}")
    log(f"Google Sheets retry interval minutes: {GOOGLE_SHEETS_RETRY_INTERVAL_MINUTES}")
    log(f"Google Sheets max scan history rows: {GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS}")
    log(f"Google Sheets max tracker rows: {GOOGLE_SHEETS_MAX_TRACKER_ROWS}")
    log(f"Generic trade webhook fallback configured: {bool_text(TRADE_WEBHOOK_URL)}")
    log(f"YFinance news max tickers per market: {YFINANCE_NEWS_MAX_TICKERS_PER_MARKET}")
    log(f"YFinance news delay seconds: {YFINANCE_NEWS_DELAY_SECONDS}")
    log(f"YFinance ticker delay seconds: {YFINANCE_TICKER_DELAY_SECONDS}")
    log(f"YFinance history retries: {YFINANCE_HISTORY_RETRIES}")
    log(f"YFinance timeout seconds: {YFINANCE_TIMEOUT_SECONDS}")
    log(f"YFinance history fallback: {YFINANCE_USE_HISTORY_FALLBACK}")
    log(f"Finnhub news max tickers per scan: {FINNHUB_NEWS_MAX_TICKERS_PER_SCAN}")
    log(f"Finnhub news delay seconds: {FINNHUB_NEWS_DELAY_SECONDS}")
    log(f"Error alerts enabled: {BOT_SEND_ERROR_ALERTS}")
    log(f"Error alert cooldown minutes: {BOT_ERROR_ALERT_COOLDOWN_MINUTES}")
    log(f"Error webhook configured: {bool_text(get_error_webhook())}")
    log(f"Heartbeat webhook configured: {bool_text(get_heartbeat_webhook())}")
    log(f"Dedicated heartbeat webhook configured: {bool_text(HEARTBEAT_WEBHOOK_URL)}")
    log(f"Bot data dir: {BOT_DATA_DIR}")
    log(f"Bot status file: {BOT_STATUS_FILE}")
    log(f"Heartbeat enabled: {BOT_HEARTBEAT_ENABLED}")
    log(f"Heartbeat interval hours: {BOT_HEARTBEAT_INTERVAL_HOURS}")
    ensure_data_dir()
    log_runtime_config_warnings()

    if not CRYPTO_TRADE_WEBHOOK_URL:
        log("WARNING: CRYPTO_TRADE_WEBHOOK_URL is missing.")

    if not STOCK_TRADE_WEBHOOK_URL:
        log("WARNING: STOCK_TRADE_WEBHOOK_URL is missing.")

    if SEND_NEWS and not get_news_webhook("Crypto"):
        log("WARNING: Crypto news webhook is missing and no crypto trade webhook fallback is available.")

    if SEND_NEWS and not get_news_webhook("Stock"):
        log("WARNING: Stock news webhook is missing and no stock trade webhook fallback is available.")

    if not NEWSAPI_KEY:
        log("INFO: NEWSAPI_KEY missing. NewsAPI source disabled.")

    if not FINNHUB_API_KEY:
        log("INFO: FINNHUB_API_KEY missing. Finnhub source disabled.")

    if not BOT_NEWS_YFINANCE_ENABLED:
        log("INFO: BOT_NEWS_YFINANCE_ENABLED is false. Yahoo/yfinance news source disabled for safer Railway runtime.")

    send_startup_message()

    if BOT_SKIP_STARTUP_SCAN and not BOT_RUN_ONCE:
        log("Startup scan skipped by BOT_SKIP_STARTUP_SCAN. Sleeping until next interval.")
        interruptible_sleep(SCAN_INTERVAL_MINUTES * 60)

    if BOT_RUN_ONCE:
        result = run_scan()
        log(f"BOT_RUN_ONCE complete: {result}")
        return

    while not SHUTDOWN_REQUESTED:
        try:
            run_scan()
        except Exception as error:
            log(f"Unexpected scan error: {error}")
            send_error_alert(f"Unexpected scan error: {error}")

        if SHUTDOWN_REQUESTED:
            break

        log(f"Sleeping for {SCAN_INTERVAL_MINUTES} minutes...")
        interruptible_sleep(SCAN_INTERVAL_MINUTES * 60)

    log("AI Trading Bot stopped cleanly.")


if __name__ == "__main__":
    main()
