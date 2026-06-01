import os
import sys
import time
import json
import signal
import traceback
import hashlib
import re
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

# v32.1 dedicated paper-trade lifecycle webhooks.
# These keep normal signal alerts in trade-alert channels while routing
# paper trade opened/TP1/TP2/stop/closed updates to separate tracker channels.
# If either paper webhook is missing, the bot safely falls back to the matching
# trade alert webhook so paper-trade notifications are not lost.
CRYPTO_PAPER_TRADE_WEBHOOK_URL = os.getenv(
    "CRYPTO_PAPER_TRADE_WEBHOOK_URL",
    CRYPTO_TRADE_WEBHOOK_URL
)

STOCK_PAPER_TRADE_WEBHOOK_URL = os.getenv(
    "STOCK_PAPER_TRADE_WEBHOOK_URL",
    STOCK_TRADE_WEBHOOK_URL
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
BOT_VERSION = "google-sheets-100-production-v32.11-strategy-ranking-engine"
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

# Discord terminal upgrades. Existing webhooks are used by default.
BOT_DISCORD_ELITE_ALERTS_ENABLED = get_env_bool("BOT_DISCORD_ELITE_ALERTS_ENABLED", True)
BOT_SEND_TOP_SIGNALS_SUMMARY = get_env_bool("BOT_SEND_TOP_SIGNALS_SUMMARY", True)
BOT_TOP_SIGNALS_COUNT = max(3, get_env_int("BOT_TOP_SIGNALS_COUNT", 5))
BOT_TOP_SIGNALS_MIN_INTERVAL_MINUTES = max(5, get_env_int("BOT_TOP_SIGNALS_MIN_INTERVAL_MINUTES", 60))
BOT_SEND_DAILY_PERFORMANCE_REPORT = get_env_bool("BOT_SEND_DAILY_PERFORMANCE_REPORT", True)
BOT_DAILY_REPORT_HOUR = max(0, min(get_env_int("BOT_DAILY_REPORT_HOUR", 18), 23))
BOT_SEND_BACKTEST_SCORECARD = get_env_bool("BOT_SEND_BACKTEST_SCORECARD", True)
TOP_SIGNALS_WEBHOOK_URL = os.getenv("TOP_SIGNALS_WEBHOOK_URL", "")
DAILY_REPORT_WEBHOOK_URL = os.getenv("DAILY_REPORT_WEBHOOK_URL", "")
BACKTEST_WEBHOOK_URL = os.getenv("BACKTEST_WEBHOOK_URL", "")

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

# Multi-timeframe analysis adds confirmation from short-term and higher-timeframe trend.
# Keep this enabled by default for v22. Set BOT_MULTI_TIMEFRAME_ENABLED=false to go back
# to the original single-timeframe scoring behavior without changing the rest of the bot.
BOT_MULTI_TIMEFRAME_ENABLED = get_env_bool("BOT_MULTI_TIMEFRAME_ENABLED", True)
BOT_SHORT_TIMEFRAME_PERIOD = os.getenv("BOT_SHORT_TIMEFRAME_PERIOD", "60d")
BOT_SHORT_TIMEFRAME_INTERVAL = os.getenv("BOT_SHORT_TIMEFRAME_INTERVAL", "4h")
BOT_PRIMARY_TIMEFRAME_LABEL = os.getenv("BOT_PRIMARY_TIMEFRAME_LABEL", "1D")
BOT_HIGHER_TIMEFRAME_LABEL = os.getenv("BOT_HIGHER_TIMEFRAME_LABEL", "1W")
BOT_MTF_MAX_ADJUSTMENT = max(0, min(get_env_int("BOT_MTF_MAX_ADJUSTMENT", 20), 30))
BOT_MTF_SHORT_CONFIRM_POINTS = max(0, min(get_env_int("BOT_MTF_SHORT_CONFIRM_POINTS", 8), 15))
BOT_MTF_HIGHER_CONFIRM_POINTS = max(0, min(get_env_int("BOT_MTF_HIGHER_CONFIRM_POINTS", 12), 20))
BOT_MTF_REQUIRE_MIN_ROWS = max(35, get_env_int("BOT_MTF_REQUIRE_MIN_ROWS", 35))
BOT_MTF_SHORT_ENABLED = get_env_bool("BOT_MTF_SHORT_ENABLED", True)
BOT_MTF_HIGHER_ENABLED = get_env_bool("BOT_MTF_HIGHER_ENABLED", True)
BOT_MTF_TIME_GUARD_SECONDS = max(10, get_env_int("BOT_MTF_TIME_GUARD_SECONDS", 45))
BOT_MTF_ALIGNMENT_REQUIRED = get_env_bool("BOT_MTF_ALIGNMENT_REQUIRED", True)
BOT_MTF_ALIGNMENT_MIN_CONFIDENCE = max(0, min(get_env_float("BOT_MTF_ALIGNMENT_MIN_CONFIDENCE", MIN_CONFIDENCE), 100))
BOT_MTF_ALIGNMENT_ALLOW_NEUTRAL = get_env_bool("BOT_MTF_ALIGNMENT_ALLOW_NEUTRAL", True)
BOT_MTF_ALIGNMENT_MIN_MATCHES = max(1, min(get_env_int("BOT_MTF_ALIGNMENT_MIN_MATCHES", 2), 3))
BOT_MOMENTUM_TIMEFRAME_PERIOD = os.getenv("BOT_MOMENTUM_TIMEFRAME_PERIOD", "30d")
BOT_MOMENTUM_TIMEFRAME_INTERVAL = os.getenv("BOT_MOMENTUM_TIMEFRAME_INTERVAL", "1h")
BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE = os.getenv("BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE", "W-FRI")

# Volume spike detection helps confirm whether a move has real participation behind it.
# Set BOT_VOLUME_SPIKE_ENABLED=false to disable this adjustment while keeping all columns.
BOT_VOLUME_SPIKE_ENABLED = get_env_bool("BOT_VOLUME_SPIKE_ENABLED", True)
BOT_VOLUME_AVG_WINDOW = max(5, get_env_int("BOT_VOLUME_AVG_WINDOW", 20))
BOT_VOLUME_SPIKE_THRESHOLD = max(1.0, get_env_float("BOT_VOLUME_SPIKE_THRESHOLD", 1.5))
BOT_VOLUME_STRONG_SPIKE_THRESHOLD = max(BOT_VOLUME_SPIKE_THRESHOLD, get_env_float("BOT_VOLUME_STRONG_SPIKE_THRESHOLD", 2.0))
BOT_VOLUME_DRY_UP_THRESHOLD = max(0.1, min(get_env_float("BOT_VOLUME_DRY_UP_THRESHOLD", 0.7), 1.0))
BOT_VOLUME_MAX_ADJUSTMENT = max(0, min(get_env_int("BOT_VOLUME_MAX_ADJUSTMENT", 10), 20))

# Market trend filter checks broad market direction before trusting a signal.
# Crypto uses BTC/ETH by default. Stocks use SPY/QQQ by default.
BOT_MARKET_TREND_FILTER_ENABLED = get_env_bool("BOT_MARKET_TREND_FILTER_ENABLED", True)
BOT_MARKET_TREND_MAX_ADJUSTMENT = max(0, min(get_env_int("BOT_MARKET_TREND_MAX_ADJUSTMENT", 12), 20))
BOT_MARKET_TREND_MIN_ANCHORS = max(1, get_env_int("BOT_MARKET_TREND_MIN_ANCHORS", 1))
BOT_CRYPTO_MARKET_TICKERS = clean_ticker_list(get_env_list("BOT_CRYPTO_MARKET_TICKERS", ["BTC-USD"]))
BOT_STOCK_MARKET_TICKERS = clean_ticker_list(get_env_list("BOT_STOCK_MARKET_TICKERS", ["SPY"]))

# Better confidence scoring combines technicals, MTF, volume, and market context.
# Set BOT_CONFIDENCE_ENGINE_ENABLED=false to fall back to the older final-score confidence.
BOT_CONFIDENCE_ENGINE_ENABLED = get_env_bool("BOT_CONFIDENCE_ENGINE_ENABLED", True)
BOT_CONFIDENCE_BASELINE = max(0, min(get_env_float("BOT_CONFIDENCE_BASELINE", 50), 100))
BOT_CONFIDENCE_TECH_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_TECH_WEIGHT", 0.40))
BOT_CONFIDENCE_MTF_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_MTF_WEIGHT", 0.25))
BOT_CONFIDENCE_VOLUME_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_VOLUME_WEIGHT", 0.15))
BOT_CONFIDENCE_MARKET_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_MARKET_WEIGHT", 0.20))
BOT_CONFIDENCE_SR_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_SR_WEIGHT", 0.10))

# Support/resistance checks recent price structure before trusting a signal.
# Set BOT_SUPPORT_RESISTANCE_ENABLED=false to disable this adjustment while keeping all columns.
BOT_SUPPORT_RESISTANCE_ENABLED = get_env_bool("BOT_SUPPORT_RESISTANCE_ENABLED", True)
BOT_SUPPORT_RESISTANCE_LOOKBACK = max(20, get_env_int("BOT_SUPPORT_RESISTANCE_LOOKBACK", 60))
BOT_SUPPORT_RESISTANCE_NEAR_PCT = max(0.1, get_env_float("BOT_SUPPORT_RESISTANCE_NEAR_PCT", 2.0))
BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT = max(0.0, get_env_float("BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT", 0.25))
BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT = max(0, min(get_env_int("BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT", 10), 20))

# News sentiment weighting scores recent headlines and adds a small directional adjustment.
# This does not replace the existing news digests; it only uses headlines as another
# confirmation layer for trade confidence.
BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED = get_env_bool("BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED", True)
BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT = max(0, min(get_env_int("BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT", 10), 20))
BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER = max(1, get_env_int("BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER", 3))
BOT_NEWS_SENTIMENT_MAX_MARKET_ITEMS = max(1, get_env_int("BOT_NEWS_SENTIMENT_MAX_MARKET_ITEMS", 6))
BOT_NEWS_SENTIMENT_USE_MARKET_NEWS = get_env_bool("BOT_NEWS_SENTIMENT_USE_MARKET_NEWS", True)
BOT_NEWS_SENTIMENT_TIME_GUARD_SECONDS = max(10, get_env_int("BOT_NEWS_SENTIMENT_TIME_GUARD_SECONDS", 60))
BOT_CONFIDENCE_NEWS_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_NEWS_WEIGHT", 0.10))

# Full professional confidence engine weights. These are normalized automatically.
BOT_CONFIDENCE_RSI_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_RSI_WEIGHT", 0.10))
BOT_CONFIDENCE_MACD_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_MACD_WEIGHT", 0.10))
BOT_CONFIDENCE_TREND_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_TREND_WEIGHT", 0.15))
BOT_CONFIDENCE_RISK_REWARD_WEIGHT = max(0, get_env_float("BOT_CONFIDENCE_RISK_REWARD_WEIGHT", 0.10))

# Trade management logic creates an actionable plan for each directional signal.
BOT_TRADE_MANAGEMENT_ENABLED = get_env_bool("BOT_TRADE_MANAGEMENT_ENABLED", True)
BOT_ATR_WINDOW = max(5, get_env_int("BOT_ATR_WINDOW", 14))
BOT_ATR_STOP_MULTIPLIER = max(0.5, get_env_float("BOT_ATR_STOP_MULTIPLIER", 1.5))
BOT_ATR_TARGET1_MULTIPLIER = max(0.5, get_env_float("BOT_ATR_TARGET1_MULTIPLIER", 1.5))
BOT_ATR_TARGET2_MULTIPLIER = max(BOT_ATR_TARGET1_MULTIPLIER, get_env_float("BOT_ATR_TARGET2_MULTIPLIER", 3.0))
BOT_MIN_RISK_REWARD = max(0.1, get_env_float("BOT_MIN_RISK_REWARD", 1.5))
BOT_SR_STOP_BUFFER_PCT = max(0.0, get_env_float("BOT_SR_STOP_BUFFER_PCT", 0.35))
BOT_TRADE_PLAN_MAX_HOLD_DAYS = max(1, get_env_int("BOT_TRADE_PLAN_MAX_HOLD_DAYS", 10))

# Advanced backtesting validates signal quality before paper/live automation.
BOT_BACKTESTING_ENABLED = get_env_bool("BOT_BACKTESTING_ENABLED", True)
BOT_BACKTEST_INTERVAL_HOURS = max(1, get_env_float("BOT_BACKTEST_INTERVAL_HOURS", 24))
BOT_BACKTEST_PERIOD = os.getenv("BOT_BACKTEST_PERIOD", "1y")
BOT_BACKTEST_HOLD_DAYS = max(1, get_env_int("BOT_BACKTEST_HOLD_DAYS", 5))
BOT_BACKTEST_LOOKBACK_DAYS = max(80, get_env_int("BOT_BACKTEST_LOOKBACK_DAYS", 140))
BOT_BACKTEST_MIN_CONFIDENCE = max(0, min(get_env_float("BOT_BACKTEST_MIN_CONFIDENCE", MIN_CONFIDENCE), 100))
BOT_BACKTEST_MAX_TICKERS = max(1, get_env_int("BOT_BACKTEST_MAX_TICKERS", 12))
BOT_BACKTEST_MIN_SIGNALS = max(1, get_env_int("BOT_BACKTEST_MIN_SIGNALS", 3))
BOT_BACKTEST_INITIAL_EQUITY = max(100, get_env_float("BOT_BACKTEST_INITIAL_EQUITY", 10000))
BOT_BACKTEST_RISK_PER_TRADE_PCT = max(0.1, min(get_env_float("BOT_BACKTEST_RISK_PER_TRADE_PCT", 1.0), 10))
BOT_BACKTEST_INCLUDE_TRADE_MANAGEMENT = get_env_bool("BOT_BACKTEST_INCLUDE_TRADE_MANAGEMENT", True)
BOT_BACKTEST_QUALITY_FILTER_ENABLED = get_env_bool("BOT_BACKTEST_QUALITY_FILTER_ENABLED", True)
BOT_BACKTEST_QUALITY_MIN_PF = max(0, get_env_float("BOT_BACKTEST_QUALITY_MIN_PF", 1.0))
BOT_BACKTEST_QUALITY_STRONG_PF = max(BOT_BACKTEST_QUALITY_MIN_PF, get_env_float("BOT_BACKTEST_QUALITY_STRONG_PF", 1.25))
BOT_BACKTEST_QUALITY_MIN_WIN_RATE = max(0, min(get_env_float("BOT_BACKTEST_QUALITY_MIN_WIN_RATE", 50), 100))
BOT_BACKTEST_QUALITY_MIN_SIGNALS = max(1, get_env_int("BOT_BACKTEST_QUALITY_MIN_SIGNALS", 20))
BOT_BACKTEST_QUALITY_CONFIDENCE_PENALTY = max(0, min(get_env_float("BOT_BACKTEST_QUALITY_CONFIDENCE_PENALTY", 12), 50))

# Phase 3 upgrades: regime detection, ranking, sizing, trailing stops, exposure controls,
# walk-forward validation, outcome tracking, and dashboard analytics.
BOT_MARKET_REGIME_DETECTION_ENABLED = get_env_bool("BOT_MARKET_REGIME_DETECTION_ENABLED", True)
BOT_MARKET_REGIME_VOLATILITY_THRESHOLD = max(1.0, get_env_float("BOT_MARKET_REGIME_VOLATILITY_THRESHOLD", 8.0))
BOT_SIGNAL_RANKING_ENABLED = get_env_bool("BOT_SIGNAL_RANKING_ENABLED", True)
BOT_MAX_ALERTS_PER_SCAN = max(0, get_env_int("BOT_MAX_ALERTS_PER_SCAN", 8))
BOT_POSITION_SIZING_ENABLED = get_env_bool("BOT_POSITION_SIZING_ENABLED", True)
BOT_ACCOUNT_SIZE = max(100, get_env_float("BOT_ACCOUNT_SIZE", 10000))
BOT_RISK_PER_TRADE_PCT = max(0.1, min(get_env_float("BOT_RISK_PER_TRADE_PCT", 1.0), 10))
BOT_MAX_POSITION_PCT = max(1, min(get_env_float("BOT_MAX_POSITION_PCT", 20), 100))
BOT_TRAILING_STOP_ENABLED = get_env_bool("BOT_TRAILING_STOP_ENABLED", True)
BOT_TRAILING_ATR_MULTIPLIER = max(0.5, get_env_float("BOT_TRAILING_ATR_MULTIPLIER", 1.2))
BOT_BREAKEVEN_TRIGGER_R = max(0.5, get_env_float("BOT_BREAKEVEN_TRIGGER_R", 1.0))
BOT_EXPOSURE_CONTROLS_ENABLED = get_env_bool("BOT_EXPOSURE_CONTROLS_ENABLED", True)
BOT_MAX_ALERTS_PER_MARKET = max(1, get_env_int("BOT_MAX_ALERTS_PER_MARKET", 4))
BOT_MAX_ALERTS_PER_CATEGORY = max(1, get_env_int("BOT_MAX_ALERTS_PER_CATEGORY", 3))
BOT_WALK_FORWARD_ENABLED = get_env_bool("BOT_WALK_FORWARD_ENABLED", True)
BOT_WALK_FORWARD_WINDOWS = max(2, get_env_int("BOT_WALK_FORWARD_WINDOWS", 4))
BOT_OUTCOME_TRACKING_ENABLED = get_env_bool("BOT_OUTCOME_TRACKING_ENABLED", True)
BOT_DASHBOARD_ANALYTICS_ENABLED = get_env_bool("BOT_DASHBOARD_ANALYTICS_ENABLED", True)

BULLISH_NEWS_KEYWORDS = [
    "approval", "approved", "etf approved", "partnership", "beats", "beat estimates",
    "raises guidance", "raised guidance", "upgrade", "upgraded", "surges", "rallies",
    "record revenue", "profit jumps", "strong demand", "launches", "adoption",
    "inflows", "buy rating", "price target raised", "acquisition", "merger"
]

BEARISH_NEWS_KEYWORDS = [
    "lawsuit", "sued", "sec", "investigation", "probe", "downgrade", "downgraded",
    "misses", "missed estimates", "cuts guidance", "cut guidance", "plunges", "crashes",
    "bankruptcy", "halted", "exploit", "hack", "outflows", "recall", "fraud",
    "warning", "layoffs", "price target cut", "bearish"
]

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
TOP_SIGNALS_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_top_signals_log.txt")
SIGNAL_HISTORY_FILE = os.path.join(BOT_DATA_DIR, "signal_history.csv")
SUMMARY_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_summary_log.txt")
NEWS_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_news_log.txt")
NEWS_SCHEDULE_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_news_schedule_log.txt")
BREAKING_NEWS_SCHEDULE_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_breaking_news_schedule_log.txt")
BACKTEST_SCHEDULE_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_backtest_schedule_log.txt")

BOT_HEARTBEAT_ENABLED = get_env_bool("BOT_HEARTBEAT_ENABLED", True)
BOT_HEARTBEAT_INTERVAL_HOURS = max(1, get_env_float("BOT_HEARTBEAT_INTERVAL_HOURS", 12))
HEARTBEAT_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_heartbeat_log.txt")
DAILY_REPORT_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_daily_report_log.txt")

YFINANCE_TICKER_DELAY_SECONDS = max(0, get_env_float("YFINANCE_TICKER_DELAY_SECONDS", 0.25))
YFINANCE_HISTORY_RETRIES = max(0, get_env_int("YFINANCE_HISTORY_RETRIES", 2))
YFINANCE_TIMEOUT_SECONDS = max(5, get_env_int("YFINANCE_TIMEOUT_SECONDS", 20))
YFINANCE_USE_HISTORY_FALLBACK = get_env_bool("YFINANCE_USE_HISTORY_FALLBACK", False)
BOT_SLEEP_CHUNK_SECONDS = max(5, get_env_int("BOT_SLEEP_CHUNK_SECONDS", 30))
LOG_MAX_ITEMS = max(100, get_env_int("BOT_LOG_MAX_ITEMS", 5000))
BOT_STATUS_FILE = os.path.join(BOT_DATA_DIR, "bot_last_status.json")


# ======================================================
# v32 PAPER TRADE TRACKING SYSTEM
# ======================================================

BOT_PAPER_TRADING_ENABLED = get_env_bool("BOT_PAPER_TRADING_ENABLED", True)
BOT_PAPER_TRADE_MONITOR_ENABLED = get_env_bool("BOT_PAPER_TRADE_MONITOR_ENABLED", True)
BOT_PAPER_TRADE_MAX_OPEN_PER_TICKER = max(1, get_env_int("BOT_PAPER_TRADE_MAX_OPEN_PER_TICKER", 1))
BOT_PAPER_TRADE_MAX_OPEN_TOTAL = max(1, get_env_int("BOT_PAPER_TRADE_MAX_OPEN_TOTAL", 10))
BOT_PAPER_TRADE_STARTING_EQUITY = max(100, get_env_float("BOT_PAPER_TRADE_STARTING_EQUITY", BOT_ACCOUNT_SIZE))

# v32.2 quality gate: paper trades should collect useful data without opening
# unrealistic or historically weak setups. This gate uses the backtest-quality
# values already calculated during alert filtering when available.
BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED = get_env_bool("BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED", True)
BOT_PAPER_TRADE_MIN_BACKTEST_PF = max(0, get_env_float("BOT_PAPER_TRADE_MIN_BACKTEST_PF", 1.0))
BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE = max(0, min(get_env_float("BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE", 50), 100))
BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS = max(1, get_env_int("BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS", 20))
BOT_PAPER_TRADE_AVOID_TICKERS = clean_ticker_list(get_env_list("BOT_PAPER_TRADE_AVOID_TICKERS", []))
BOT_SEND_PAPER_TRADE_SUMMARY = get_env_bool("BOT_SEND_PAPER_TRADE_SUMMARY", True)
BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS = max(1, get_env_float("BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS", 6))

# v32.8 Adaptive Filter Enforcement.
# This uses actual closed paper-trade performance to block weak repeat setups
# and tag strong repeat setups before v33 automation. It does not place real trades.
BOT_ADAPTIVE_FILTERS_ENABLED = get_env_bool("BOT_ADAPTIVE_FILTERS_ENABLED", True)
BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES = max(1, get_env_int("BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES", 20))
BOT_ADAPTIVE_BLOCK_WEAK_TICKERS = get_env_bool("BOT_ADAPTIVE_BLOCK_WEAK_TICKERS", True)
BOT_ADAPTIVE_AVOID_MAX_PF = max(0, get_env_float("BOT_ADAPTIVE_AVOID_MAX_PF", 1.0))
BOT_ADAPTIVE_AVOID_MAX_WR = max(0, min(get_env_float("BOT_ADAPTIVE_AVOID_MAX_WR", 45), 100))
BOT_ADAPTIVE_FAVORITE_MIN_PF = max(0, get_env_float("BOT_ADAPTIVE_FAVORITE_MIN_PF", 1.5))
BOT_ADAPTIVE_FAVORITE_MIN_WR = max(0, min(get_env_float("BOT_ADAPTIVE_FAVORITE_MIN_WR", 55), 100))
BOT_ADAPTIVE_INCLUDE_FAVORITE_NOTE = get_env_bool("BOT_ADAPTIVE_INCLUDE_FAVORITE_NOTE", True)
# v32.8.1 bootstrap fix: until enough closed paper trades exist, optionally use
# backtest quality stats as a temporary adaptive-filter data source. Once a ticker
# reaches BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES, real paper-trade results take over.
BOT_ADAPTIVE_USE_BACKTEST_BOOTSTRAP = get_env_bool("BOT_ADAPTIVE_USE_BACKTEST_BOOTSTRAP", True)
BOT_ADAPTIVE_BOOTSTRAP_MIN_BACKTEST_SIGNALS = max(1, get_env_int("BOT_ADAPTIVE_BOOTSTRAP_MIN_BACKTEST_SIGNALS", BOT_BACKTEST_QUALITY_MIN_SIGNALS))


# v32.9 Dynamic Confidence Optimization.
# Recommendation-first intelligence layer. It does not automatically change
# BOT_SIGNAL_MIN_CONFIDENCE; it analyzes paper/backtest performance by
# confidence bucket and recommends whether the threshold should stay, rise,
# or wait for more data.
BOT_DYNAMIC_CONFIDENCE_ENABLED = get_env_bool("BOT_DYNAMIC_CONFIDENCE_ENABLED", True)
BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE = max(1, get_env_int("BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE", 20))
BOT_DYNAMIC_CONFIDENCE_TARGET_PF = max(0, get_env_float("BOT_DYNAMIC_CONFIDENCE_TARGET_PF", 1.5))
BOT_DYNAMIC_CONFIDENCE_TARGET_WR = max(0, min(get_env_float("BOT_DYNAMIC_CONFIDENCE_TARGET_WR", 50), 100))
BOT_DYNAMIC_CONFIDENCE_BUCKET_SIZE = max(5, min(get_env_int("BOT_DYNAMIC_CONFIDENCE_BUCKET_SIZE", 5), 20))
BOT_DYNAMIC_CONFIDENCE_MIN_RECOMMENDED = max(0, min(get_env_float("BOT_DYNAMIC_CONFIDENCE_MIN_RECOMMENDED", MIN_CONFIDENCE), 100))
BOT_DYNAMIC_CONFIDENCE_MAX_RECOMMENDED = max(BOT_DYNAMIC_CONFIDENCE_MIN_RECOMMENDED, min(get_env_float("BOT_DYNAMIC_CONFIDENCE_MAX_RECOMMENDED", 90), 100))
BOT_DYNAMIC_CONFIDENCE_USE_BACKTEST = get_env_bool("BOT_DYNAMIC_CONFIDENCE_USE_BACKTEST", True)
BOT_DYNAMIC_CONFIDENCE_USE_PAPER = get_env_bool("BOT_DYNAMIC_CONFIDENCE_USE_PAPER", True)

# v32.10 Setup Performance Analytics.
BOT_SETUP_ANALYTICS_ENABLED = get_env_bool("BOT_SETUP_ANALYTICS_ENABLED", True)
BOT_SETUP_ANALYTICS_MIN_SAMPLE = max(1, get_env_int("BOT_SETUP_ANALYTICS_MIN_SAMPLE", 5))
BOT_SETUP_ANALYTICS_STRONG_PF = max(0, get_env_float("BOT_SETUP_ANALYTICS_STRONG_PF", 1.5))
BOT_SETUP_ANALYTICS_STRONG_WR = max(0, min(get_env_float("BOT_SETUP_ANALYTICS_STRONG_WR", 50), 100))
BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS = max(3, get_env_int("BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS", 10))

# v32.11 Strategy Ranking Engine.
# Converts setup performance into strong/neutral/weak strategy labels before v33 automation.
# Optional blocking only triggers after enough closed trades exist for that setup.
BOT_STRATEGY_RANKING_ENABLED = get_env_bool("BOT_STRATEGY_RANKING_ENABLED", True)
BOT_STRATEGY_RANKING_MIN_SAMPLE = max(1, get_env_int("BOT_STRATEGY_RANKING_MIN_SAMPLE", BOT_SETUP_ANALYTICS_MIN_SAMPLE))
BOT_STRATEGY_RANKING_STRONG_PF = max(0, get_env_float("BOT_STRATEGY_RANKING_STRONG_PF", BOT_SETUP_ANALYTICS_STRONG_PF))
BOT_STRATEGY_RANKING_STRONG_WR = max(0, min(get_env_float("BOT_STRATEGY_RANKING_STRONG_WR", BOT_SETUP_ANALYTICS_STRONG_WR), 100))
BOT_STRATEGY_RANKING_WEAK_PF = max(0, get_env_float("BOT_STRATEGY_RANKING_WEAK_PF", 1.0))
BOT_STRATEGY_RANKING_WEAK_WR = max(0, min(get_env_float("BOT_STRATEGY_RANKING_WEAK_WR", 45), 100))
BOT_STRATEGY_RANKING_BLOCK_WEAK_SETUPS = get_env_bool("BOT_STRATEGY_RANKING_BLOCK_WEAK_SETUPS", True)
BOT_STRATEGY_RANKING_INCLUDE_NOTES = get_env_bool("BOT_STRATEGY_RANKING_INCLUDE_NOTES", True)
BOT_STRATEGY_RANKING_MAX_REPORT_ROWS = max(3, get_env_int("BOT_STRATEGY_RANKING_MAX_REPORT_ROWS", 10))

PAPER_TRADES_FILE = os.path.join(BOT_DATA_DIR, "paper_trades.csv")
PAPER_EQUITY_FILE = os.path.join(BOT_DATA_DIR, "paper_trade_equity_curve.csv")
PAPER_TRADE_SUMMARY_LOG_FILE = os.path.join(BOT_DATA_DIR, "bot_sent_paper_trade_summary_log.txt")

PAPER_TRADE_HEADERS = [
    "trade_id", "ticker", "market", "signal", "entry_price", "current_price",
    "stop_loss", "tp1", "tp2", "confidence", "position_size", "position_value",
    "status", "result", "date_opened", "date_closed", "last_updated",
    "pnl_percent", "pnl_dollars", "risk_reward_2", "signal_rank", "quality_score",
    "setup_name", "setup_tags", "setup_score",
    "notes", "tp1_notified", "tp2_notified", "stop_notified", "closed_notified",
]

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
    ticker = str(ticker or "").upper()
    return "Crypto" if ticker.endswith("-USD") else "Stock"


def get_trade_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_TRADE_WEBHOOK_URL


def get_paper_trade_webhook(ticker):
    """
    Paper trade lifecycle alerts route to dedicated paper-trade tracker
    channels when configured. Normal buy/sell trade alerts still use
    get_trade_webhook().
    """
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_PAPER_TRADE_WEBHOOK_URL or CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_PAPER_TRADE_WEBHOOK_URL or STOCK_TRADE_WEBHOOK_URL


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
    fields = [
        {"name": "Status", "value": "Bot is still running unless Railway shows a crash.", "inline": False},
        {"name": "Error", "value": compact_text(message, 1000), "inline": False},
        {"name": "Version", "value": BOT_VERSION, "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    sent = send_discord_embed(webhook_url, "🚨 AI Trading Bot Error Alert", 16711680, fields)
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


def format_money(value):
    try:
        value = float(value)
        if abs(value) >= 100:
            return f"${value:,.2f}"
        return f"${value:,.4f}"
    except Exception:
        return "N/A"


def get_top_signals_webhook(market=None):
    """
    Top Ranked Signals are decision-support/scorecard messages, not market summaries.
    They should go to a dedicated scorecards channel when TOP_SIGNALS_WEBHOOK_URL is set.
    If TOP_SIGNALS_WEBHOOK_URL is missing, use BACKTEST_WEBHOOK_URL only when provided
    because the recommended setup is one shared scorecards channel/webhook for both.
    Do not fall back to stock/crypto daily-summary channels.
    """
    if TOP_SIGNALS_WEBHOOK_URL:
        return TOP_SIGNALS_WEBHOOK_URL

    if BACKTEST_WEBHOOK_URL:
        return BACKTEST_WEBHOOK_URL

    return ""


def get_daily_report_webhook():
    if DAILY_REPORT_WEBHOOK_URL:
        return DAILY_REPORT_WEBHOOK_URL
    return get_summary_webhook("Stock") or get_summary_webhook("Crypto")


def get_backtest_webhook():
    """
    Backtest Scorecard should go to its own scorecards channel/webhook.
    If BACKTEST_WEBHOOK_URL is missing, use TOP_SIGNALS_WEBHOOK_URL only when provided
    because both scorecard-style messages can share the same channel.
    Do not fall back to stock/crypto daily-summary channels.
    """
    if BACKTEST_WEBHOOK_URL:
        return BACKTEST_WEBHOOK_URL

    if TOP_SIGNALS_WEBHOOK_URL:
        return TOP_SIGNALS_WEBHOOK_URL

    return ""


def compact_text(value, limit=900):
    text = str(value or "N/A")
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def to_plain_value(value):
    """Convert pandas/numpy scalars and NaN values into safe Python values."""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass

    return value


def sanitize_sheet_values(values):
    """Normalize Google Sheets payloads to avoid pandas/gspread dtype edge cases."""
    cleaned = []
    for row in values or []:
        if isinstance(row, (list, tuple)):
            cleaned.append([to_plain_value(cell) for cell in row])
        else:
            cleaned.append([to_plain_value(row)])
    return cleaned


def normalize_numeric_ohlcv(data):
    """Force OHLCV columns to float-safe numeric dtypes before indicators are added."""
    if data is None or data.empty:
        return pd.DataFrame()
    out = data.copy()
    for column in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    return out


def normalize_paper_trade_dtypes(df):
    """Keep paper-trade CSV columns compatible with decimal updates during monitoring."""
    if df is None or df.empty:
        return pd.DataFrame(columns=PAPER_TRADE_HEADERS)
    out = df.copy()
    numeric_columns = [
        "entry_price", "current_price", "stop_loss", "tp1", "tp2", "confidence",
        "position_size", "position_value", "pnl_percent", "pnl_dollars",
        "risk_reward_2", "signal_rank", "quality_score", "setup_score",
    ]
    bool_columns = ["tp1_notified", "tp2_notified", "stop_notified", "closed_notified"]
    text_columns = [column for column in PAPER_TRADE_HEADERS if column not in numeric_columns + bool_columns]

    for column in numeric_columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").astype("float64")
    for column in bool_columns:
        if column in out.columns:
            out[column] = out[column].astype(str).str.lower().isin(["true", "1", "yes", "y", "on"])
    for column in text_columns:
        if column in out.columns:
            out[column] = out[column].fillna("").astype(str)
    return out


def signal_emoji(signal):
    signal = str(signal or "")
    if "STRONG BUY" in signal:
        return "🚀"
    if "BUY" in signal:
        return "🟢"
    if "STRONG SELL" in signal:
        return "🔻"
    if "SELL" in signal:
        return "🔴"
    return "🟡"


def build_signal_reason_text(row):
    checks = []
    if safe_float(row.get("MTF Confidence", 0), 0) >= 70:
        checks.append("✅ MTF confirms")
    if safe_float(row.get("Volume Confidence", 0), 0) >= 70:
        checks.append(f"✅ Volume {row.get('Volume Signal', 'confirms')}")
    if safe_float(row.get("Trend Confidence", 0), 0) >= 70:
        checks.append(f"✅ Trend {row.get('Daily Trend', 'confirms')}")
    if safe_float(row.get("Market Confidence", 0), 0) >= 70:
        checks.append(f"✅ Market {row.get('Advanced Market Regime', row.get('Market Regime', 'confirms'))}")
    if safe_float(row.get("S/R Confidence", 0), 0) >= 70:
        checks.append(f"✅ S/R {row.get('S/R Signal', 'confirms')}")
    if safe_float(row.get("Risk/Reward Confidence", 0), 0) >= 70:
        checks.append(f"✅ R/R {row.get('Risk/Reward 2', 'N/A')}:1")
    if safe_float(row.get("News Confidence", 0), 0) >= 70:
        checks.append(f"✅ News {row.get('News Sentiment', 'confirms')}")
    if row.get("Exposure Notes"):
        checks.append(f"📌 {row.get('Exposure Notes')}")
    if not checks:
        checks.append("Signal passed minimum confidence and exposure checks.")
    return "\n".join(checks[:8])


def build_backtest_quality_line(result):
    pf = safe_float(result.get("Profit Factor", 0), 0)
    wr = safe_float(result.get("Win Rate %", 0), 0)
    if pf >= 2 and wr >= 55:
        badge = "🟢 Strong"
    elif pf >= 1 and wr >= 50:
        badge = "🟡 Watch"
    else:
        badge = "🔴 Weak"
    return f"{badge} | {result.get('Ticker', '')} | {result.get('Signals Tested', 0)} signals | WR {result.get('Win Rate %', 0)}% | PF {result.get('Profit Factor', 0)} | DD {result.get('Max Drawdown %', 0)}%"


def get_daily_report_key():
    return f"daily_report_{now_dt().strftime('%Y-%m-%d')}"


def daily_report_already_sent():
    return get_daily_report_key() in load_log(DAILY_REPORT_LOG_FILE)


def mark_daily_report_sent():
    items = load_log(DAILY_REPORT_LOG_FILE)
    items.add(get_daily_report_key())
    save_log(DAILY_REPORT_LOG_FILE, items)


def should_send_daily_report():
    if not BOT_SEND_DAILY_PERFORMANCE_REPORT:
        return False
    if now_dt().hour < BOT_DAILY_REPORT_HOUR:
        return False
    return not daily_report_already_sent()


def build_top_signal_lines(rows, limit=None):
    limit = limit or BOT_TOP_SIGNALS_COUNT
    directional = [row for row in rows if is_directional_signal(row.get("AI Signal", ""))]
    ranked = sorted(directional, key=lambda item: safe_float(item.get("Signal Quality Score", item.get("AI Confidence %", 0)), 0), reverse=True)
    lines = []
    for row in ranked[:limit]:
        lines.append(f"#{row.get('Signal Rank', '-')} {row.get('Ticker', '')} | {row.get('AI Signal', '')} | {row.get('AI Confidence %', 0)}% | QS {row.get('Signal Quality Score', 0)} | R/R {row.get('Risk/Reward 2', 0)} | {row.get('Risk Mode', 'N/A')}")
    return lines

def get_top_signals_key():
    bucket_seconds = BOT_TOP_SIGNALS_MIN_INTERVAL_MINUTES * 60
    bucket = int(time.time() // bucket_seconds)
    return f"top_signals_{now_dt().strftime('%Y-%m-%d')}_{bucket}"


def top_signals_already_sent():
    return get_top_signals_key() in load_log(TOP_SIGNALS_LOG_FILE)


def mark_top_signals_sent():
    items = load_log(TOP_SIGNALS_LOG_FILE)
    items.add(get_top_signals_key())
    save_log(TOP_SIGNALS_LOG_FILE, items)


def append_signal_history(row, alert_status):
    try:
        ensure_data_dir()
        record = {
            "Time": now_text(),
            "Ticker": row.get("Ticker", ""),
            "Market": row.get("Market", ""),
            "Signal": row.get("AI Signal", ""),
            "Confidence %": row.get("AI Confidence %", 0),
            "Quality Score": row.get("Signal Quality Score", 0),
            "Rank": row.get("Signal Rank", ""),
            "Price": row.get("Price", 0),
            "Risk/Reward 2": row.get("Risk/Reward 2", 0),
            "Alert Status": alert_status,
            "Approval Notes": compact_text(row.get("Exposure Notes", ""), 500),
        }
        if os.path.exists(SIGNAL_HISTORY_FILE) and os.path.getsize(SIGNAL_HISTORY_FILE) > 0:
            history = pd.read_csv(SIGNAL_HISTORY_FILE)
            history = pd.concat([history, pd.DataFrame([record])], ignore_index=True)
        else:
            history = pd.DataFrame([record])
        history = history.tail(LOG_MAX_ITEMS)
        temp_path = f"{SIGNAL_HISTORY_FILE}.tmp"
        history.to_csv(temp_path, index=False)
        os.replace(temp_path, SIGNAL_HISTORY_FILE)
    except Exception as error:
        log(f"Signal history append error: {error}")


def send_top_signals_summary(rows, candidates=0, sent_count=0):
    if not BOT_SEND_TOP_SIGNALS_SUMMARY or not rows:
        return False
    if top_signals_already_sent():
        log(f"Top signals summary skipped: anti-spam cooldown active for {BOT_TOP_SIGNALS_MIN_INTERVAL_MINUTES} minutes.")
        return False
    webhook_url = get_top_signals_webhook()
    if not webhook_url:
        log("Top signals summary skipped: TOP_SIGNALS_WEBHOOK_URL or BACKTEST_WEBHOOK_URL is missing.")
        return False
    crypto_context = next((row for row in rows if row.get("Market") == "Crypto"), {})
    stock_context = next((row for row in rows if row.get("Market") == "Stock"), {})
    fields = [
        {"name": "Scan Stats", "value": f"Scanned {len(rows)} | Candidates {candidates} | Sent {sent_count}", "inline": False},
        {"name": "Crypto Market", "value": f"{crypto_context.get('Advanced Market Regime', crypto_context.get('Market Regime', 'N/A'))} | {crypto_context.get('Risk Mode', 'N/A')}", "inline": True},
        {"name": "Stock Market", "value": f"{stock_context.get('Advanced Market Regime', stock_context.get('Market Regime', 'N/A'))} | {stock_context.get('Risk Mode', 'N/A')}", "inline": True},
        {"name": f"Top {BOT_TOP_SIGNALS_COUNT} Signals", "value": compact_text("\n".join(build_top_signal_lines(rows)) or "No directional signals passed ranking.", 1000), "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    sent = send_discord_embed(webhook_url, "🏆 Top Ranked Signals", 15844367, fields)
    if sent:
        mark_top_signals_sent()
    return sent


def send_daily_performance_report(scanned_rows, alerted_rows, candidates=0, sent_count=0, skipped_duplicates=0, ticker_errors=0, post_scan_errors=0, backtest_results=None):
    if not should_send_daily_report():
        return False
    webhook_url = get_daily_report_webhook()
    if not webhook_url:
        log("Daily performance report skipped: no summary/trade webhook available.")
        return False
    directional = [row for row in scanned_rows if is_directional_signal(row.get("AI Signal", ""))]
    avg_conf = round(sum(safe_float(row.get("AI Confidence %", 0), 0) for row in scanned_rows) / len(scanned_rows), 2) if scanned_rows else 0
    best = max(directional, key=lambda row: safe_float(row.get("Signal Quality Score", row.get("AI Confidence %", 0)), 0), default=None)
    weakest = min(directional, key=lambda row: safe_float(row.get("Signal Quality Score", row.get("AI Confidence %", 0)), 0), default=None)
    valid_bt = [r for r in (backtest_results or []) if r.get("Signals Tested", 0) > 0]
    avg_pf = round(sum(safe_float(r.get("Profit Factor", 0), 0) for r in valid_bt) / len(valid_bt), 2) if valid_bt else 0
    avg_wr = round(sum(safe_float(r.get("Win Rate %", 0), 0) for r in valid_bt) / len(valid_bt), 2) if valid_bt else 0
    worst_dd = max([safe_float(r.get("Max Drawdown %", 0), 0) for r in valid_bt], default=0)
    fields = [
        {"name": "Daily Scan", "value": f"Signals {len(directional)} | Candidates {candidates} | Sent {sent_count} | Duplicates {skipped_duplicates}", "inline": False},
        {"name": "Average Confidence", "value": f"{avg_conf}%", "inline": True},
        {"name": "Errors", "value": f"Ticker {ticker_errors} | Post-scan {post_scan_errors}", "inline": True},
        {"name": "Top Setup", "value": f"{best.get('Ticker', 'None')} {best.get('AI Signal', '')} | QS {best.get('Signal Quality Score', 0)} | R/R {best.get('Risk/Reward 2', 0)}" if best else "None", "inline": False},
        {"name": "Weakest Setup", "value": f"{weakest.get('Ticker', 'None')} {weakest.get('AI Signal', '')} | QS {weakest.get('Signal Quality Score', 0)}" if weakest else "None", "inline": False},
        {"name": "Backtest Snapshot", "value": f"Avg WR {avg_wr}% | Avg PF {avg_pf} | Worst DD {worst_dd}%" if valid_bt else "No new backtest results this cycle", "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    sent = send_discord_embed(webhook_url, "📊 Daily Trading Bot Performance Report", 3447003, fields)
    if sent:
        mark_daily_report_sent()
    return sent


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

    if BOT_SEND_TOP_SIGNALS_SUMMARY and not TOP_SIGNALS_WEBHOOK_URL and not BACKTEST_WEBHOOK_URL:
        warnings.append("Top Signals summary is enabled but TOP_SIGNALS_WEBHOOK_URL/BACKTEST_WEBHOOK_URL is missing. It will be skipped instead of posting to daily-summary channels.")

    if BOT_SEND_BACKTEST_SCORECARD and not BACKTEST_WEBHOOK_URL and not TOP_SIGNALS_WEBHOOK_URL:
        warnings.append("Backtest Scorecard is enabled but BACKTEST_WEBHOOK_URL/TOP_SIGNALS_WEBHOOK_URL is missing. It will be skipped instead of posting to daily-summary channels.")

    if BOT_MARKET_TREND_FILTER_ENABLED:
        if not BOT_CRYPTO_MARKET_TICKERS:
            warnings.append("BOT_MARKET_TREND_FILTER_ENABLED is true but BOT_CRYPTO_MARKET_TICKERS is empty.")
        if not BOT_STOCK_MARKET_TICKERS:
            warnings.append("BOT_MARKET_TREND_FILTER_ENABLED is true but BOT_STOCK_MARKET_TICKERS is empty.")

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

    data = data.dropna(subset=["Close"])
    return normalize_numeric_ohlcv(data)


def get_price_data(ticker, period="1y", interval="1d"):
    if not BOT_SCAN_MARKET_DATA_ENABLED:
        return pd.DataFrame()

    # Prefer yf.download because it supports timeout. Ticker.history can hang in
    # some hosted environments, so it is used only as a fallback.
    for attempt in range(YFINANCE_HISTORY_RETRIES + 1):
        try:
            data = yf.download(
                ticker,
                period=period,
                interval=interval,
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
                data = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
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
    data = normalize_numeric_ohlcv(data)

    if data.empty:
        return data

    close = data["Close"]

    if len(data) >= 14:
        data["RSI"] = RSIIndicator(close=close, window=14).rsi()

    if len(data) >= 26:
        macd_indicator = MACD(close=close)
        data["MACD"] = macd_indicator.macd()
        data["MACD Signal"] = macd_indicator.macd_signal()

    if len(data) >= 50:
        data["MA50"] = close.rolling(window=50).mean()

    if len(data) >= 200:
        data["MA200"] = close.rolling(window=200).mean()

    if len(data) >= BOT_ATR_WINDOW + 1 and all(column in data.columns for column in ["High", "Low", "Close"]):
        high = pd.to_numeric(data["High"], errors="coerce")
        low = pd.to_numeric(data["Low"], errors="coerce")
        prev_close = pd.to_numeric(data["Close"], errors="coerce").shift(1)
        true_range = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        data["ATR"] = true_range.rolling(window=BOT_ATR_WINDOW).mean()

    return data


def resample_to_higher_timeframe(data):
    if data is None or data.empty:
        return pd.DataFrame()

    try:
        higher = data.resample(BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })
    except Exception as error:
        log(f"Higher timeframe full OHLCV resample failed: {error}")
        try:
            higher = data.resample(BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE).agg({
                "Close": "last",
            })
        except Exception as fallback_error:
            log(f"Higher timeframe close-only resample failed: {fallback_error}")
            return pd.DataFrame()

    return normalize_price_data(higher)


def mtf_time_guard_allows(scan_started_at):
    if scan_started_at is None:
        return True

    try:
        elapsed = time.time() - scan_started_at
        remaining = BOT_MAX_SCAN_SECONDS - elapsed
        return remaining > BOT_MTF_TIME_GUARD_SECONDS
    except Exception:
        return True


def safe_latest_value(row, column, default=0):
    try:
        value = row.get(column, default)
        return float(value) if pd.notna(value) else default
    except Exception:
        return default


def calculate_volume_context(data, primary_trend):
    default_context = {
        "volume": 0,
        "avg_volume": 0,
        "relative_volume": 0,
        "volume_signal": "Unavailable",
        "volume_score_adj": 0,
    }

    if not BOT_VOLUME_SPIKE_ENABLED:
        default_context["volume_signal"] = "Disabled"
        return default_context

    if data is None or data.empty or "Volume" not in data.columns:
        return default_context

    try:
        volume_series = pd.to_numeric(data["Volume"], errors="coerce").dropna()

        if len(volume_series) < BOT_VOLUME_AVG_WINDOW + 1:
            return default_context

        current_volume = float(volume_series.iloc[-1])
        average_volume = float(volume_series.iloc[-(BOT_VOLUME_AVG_WINDOW + 1):-1].mean())

        if average_volume <= 0:
            return default_context

        relative_volume = current_volume / average_volume
        volume_signal = "Normal"
        volume_score_adj = 0

        if relative_volume >= BOT_VOLUME_STRONG_SPIKE_THRESHOLD:
            volume_signal = "Strong Spike"
            if primary_trend == "Bullish":
                volume_score_adj = BOT_VOLUME_MAX_ADJUSTMENT
            elif primary_trend == "Bearish":
                volume_score_adj = -BOT_VOLUME_MAX_ADJUSTMENT
        elif relative_volume >= BOT_VOLUME_SPIKE_THRESHOLD:
            volume_signal = "Spike"
            confirm_points = max(1, round(BOT_VOLUME_MAX_ADJUSTMENT / 2))
            if primary_trend == "Bullish":
                volume_score_adj = confirm_points
            elif primary_trend == "Bearish":
                volume_score_adj = -confirm_points
        elif relative_volume <= BOT_VOLUME_DRY_UP_THRESHOLD:
            volume_signal = "Low Volume"
            weak_points = max(1, round(BOT_VOLUME_MAX_ADJUSTMENT / 2))
            if primary_trend == "Bullish":
                volume_score_adj = -weak_points
            elif primary_trend == "Bearish":
                volume_score_adj = weak_points

        return {
            "volume": round(current_volume, 2),
            "avg_volume": round(average_volume, 2),
            "relative_volume": round(relative_volume, 2),
            "volume_signal": volume_signal,
            "volume_score_adj": int(volume_score_adj),
        }

    except Exception as error:
        log(f"Volume context calculation error: {error}")
        return default_context




def calculate_support_resistance_context(data, primary_trend):
    default_context = {
        "support_level": 0,
        "resistance_level": 0,
        "distance_to_support_pct": 0,
        "distance_to_resistance_pct": 0,
        "support_resistance_signal": "Unavailable",
        "support_resistance_score_adj": 0,
        "sr_strength": 0,
        "sr_position": "Unavailable",
        "support_touches": 0,
        "resistance_touches": 0,
        "sr_notes": "support/resistance unavailable",
    }

    if not BOT_SUPPORT_RESISTANCE_ENABLED:
        default_context["support_resistance_signal"] = "Disabled"
        default_context["sr_notes"] = "support/resistance disabled"
        return default_context

    if data is None or data.empty or "Close" not in data.columns:
        return default_context

    try:
        frame = data.copy().dropna(subset=["Close"])

        if len(frame) < BOT_SUPPORT_RESISTANCE_LOOKBACK + 1:
            return default_context

        latest = frame.iloc[-1]
        current_price = float(latest["Close"])

        if current_price <= 0:
            return default_context

        lookback_frame = frame.iloc[-(BOT_SUPPORT_RESISTANCE_LOOKBACK + 1):-1].copy()
        low_source = "Low" if "Low" in lookback_frame.columns else "Close"
        high_source = "High" if "High" in lookback_frame.columns else "Close"

        lows = pd.to_numeric(lookback_frame[low_source], errors="coerce").dropna()
        highs = pd.to_numeric(lookback_frame[high_source], errors="coerce").dropna()

        if lows.empty or highs.empty:
            return default_context

        support_level = float(lows.quantile(0.10))
        resistance_level = float(highs.quantile(0.90))
        absolute_low = float(lows.min())
        absolute_high = float(highs.max())

        # Blend raw extremes with percentile levels so one bad wick does not dominate.
        support_level = min(max(support_level, absolute_low), current_price) if current_price > absolute_low else absolute_low
        resistance_level = max(min(resistance_level, absolute_high), current_price) if current_price < absolute_high else absolute_high

        if support_level <= 0 or resistance_level <= 0:
            return default_context

        distance_to_support_pct = ((current_price - support_level) / support_level) * 100
        distance_to_resistance_pct = ((resistance_level - current_price) / resistance_level) * 100

        touch_band = BOT_SUPPORT_RESISTANCE_NEAR_PCT / 100
        support_touches = int(((lows >= support_level * (1 - touch_band)) & (lows <= support_level * (1 + touch_band))).sum())
        resistance_touches = int(((highs >= resistance_level * (1 - touch_band)) & (highs <= resistance_level * (1 + touch_band))).sum())

        range_width = max(resistance_level - support_level, current_price * 0.001)
        range_position = (current_price - support_level) / range_width
        range_position = max(0, min(range_position, 1))

        near_support = 0 <= distance_to_support_pct <= BOT_SUPPORT_RESISTANCE_NEAR_PCT
        near_resistance = 0 <= distance_to_resistance_pct <= BOT_SUPPORT_RESISTANCE_NEAR_PCT

        breakout_threshold = resistance_level * (1 + BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT / 100)
        breakdown_threshold = support_level * (1 - BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT / 100)

        is_breakout = current_price > breakout_threshold
        is_breakdown = current_price < breakdown_threshold

        signal = "Range"
        adjustment = 0
        notes = []
        half_adjustment = max(1, round(BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT / 2))

        sr_strength = min(5, max(1, support_touches + resistance_touches))

        if is_breakout:
            signal = "Breakout"
            notes.append("price cleared resistance")
            adjustment = BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT if primary_trend == "Bullish" else half_adjustment
        elif is_breakdown:
            signal = "Breakdown"
            notes.append("price lost support")
            adjustment = -BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT if primary_trend == "Bearish" else -half_adjustment
        elif near_support:
            signal = "Near Support"
            notes.append("price is near support")
            adjustment = half_adjustment if primary_trend in ["Bullish", "Bearish"] else 0
        elif near_resistance:
            signal = "Near Resistance"
            notes.append("price is near resistance")
            adjustment = -half_adjustment if primary_trend in ["Bullish", "Bearish"] else 0
        elif range_position <= 0.35:
            signal = "Lower Range"
            notes.append("price is in lower third of range")
            adjustment = max(1, round(half_adjustment / 2)) if primary_trend == "Bullish" else 0
        elif range_position >= 0.65:
            signal = "Upper Range"
            notes.append("price is in upper third of range")
            adjustment = -max(1, round(half_adjustment / 2)) if primary_trend == "Bullish" else 0
        else:
            notes.append("price is mid-range")

        if support_touches >= 3:
            notes.append(f"{support_touches} support touches")
        if resistance_touches >= 3:
            notes.append(f"{resistance_touches} resistance touches")

        sr_position = (
            "Above Resistance" if is_breakout else
            "Below Support" if is_breakdown else
            "Near Support" if near_support else
            "Near Resistance" if near_resistance else
            "Upper Range" if range_position >= 0.65 else
            "Lower Range" if range_position <= 0.35 else
            "Mid Range"
        )

        return {
            "support_level": round(support_level, 4),
            "resistance_level": round(resistance_level, 4),
            "distance_to_support_pct": round(distance_to_support_pct, 2),
            "distance_to_resistance_pct": round(distance_to_resistance_pct, 2),
            "support_resistance_signal": signal,
            "support_resistance_score_adj": int(adjustment),
            "sr_strength": int(sr_strength),
            "sr_position": sr_position,
            "support_touches": support_touches,
            "resistance_touches": resistance_touches,
            "sr_notes": ", ".join(notes),
        }

    except Exception as error:
        log(f"Support/resistance context calculation error: {error}")
        return default_context


def calculate_trade_management_context(data, signal, current_price, support_resistance_context=None):
    default_context = {
        "entry_price": round(float(current_price or 0), 4) if current_price else 0,
        "stop_loss": 0,
        "take_profit_1": 0,
        "take_profit_2": 0,
        "risk_per_share": 0,
        "reward_1": 0,
        "reward_2": 0,
        "risk_reward_1": 0,
        "risk_reward_2": 0,
        "trade_plan": "Unavailable",
        "trade_management_notes": "trade management unavailable",
    }

    if not BOT_TRADE_MANAGEMENT_ENABLED:
        default_context["trade_plan"] = "Disabled"
        default_context["trade_management_notes"] = "trade management disabled"
        return default_context

    try:
        current_price = float(current_price)
        if current_price <= 0:
            return default_context

        if not is_directional_signal(signal):
            default_context["trade_plan"] = "No Trade"
            default_context["trade_management_notes"] = "hold signal has no trade plan"
            return default_context

        frame = calculate_indicators(data.copy()) if data is not None and not data.empty else pd.DataFrame()
        latest = frame.iloc[-1] if not frame.empty else {}
        atr = safe_latest_value(latest, "ATR", 0)

        if atr <= 0:
            atr = max(current_price * 0.02, 0.01)

        sr = support_resistance_context or {}
        support_level = safe_float(sr.get("support_level", 0))
        resistance_level = safe_float(sr.get("resistance_level", 0))
        buffer = current_price * (BOT_SR_STOP_BUFFER_PCT / 100)

        entry = current_price

        if "BUY" in signal:
            atr_stop = entry - atr * BOT_ATR_STOP_MULTIPLIER
            sr_stop = support_level - buffer if support_level > 0 and support_level < entry else atr_stop
            stop_loss = max(0.01, max(atr_stop, sr_stop))
            take_profit_1 = entry + atr * BOT_ATR_TARGET1_MULTIPLIER
            sr_target = resistance_level if resistance_level > entry * (1 + BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT / 100) else 0
            if sr_target and sr_target > entry:
                capped_tp1 = min(take_profit_1, sr_target)
                if capped_tp1 > entry:
                    take_profit_1 = capped_tp1
            take_profit_2 = entry + atr * BOT_ATR_TARGET2_MULTIPLIER
            if resistance_level > entry:
                take_profit_2 = max(take_profit_2, resistance_level)
            risk = entry - stop_loss
            reward_1 = take_profit_1 - entry
            reward_2 = take_profit_2 - entry
        else:
            atr_stop = entry + atr * BOT_ATR_STOP_MULTIPLIER
            sr_stop = resistance_level + buffer if resistance_level > entry else atr_stop
            stop_loss = min(atr_stop, sr_stop) if sr_stop > entry else atr_stop
            take_profit_1 = entry - atr * BOT_ATR_TARGET1_MULTIPLIER
            sr_target = support_level if 0 < support_level < entry * (1 - BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT / 100) else 0
            if sr_target and sr_target < entry:
                capped_tp1 = max(take_profit_1, sr_target)
                if capped_tp1 < entry:
                    take_profit_1 = capped_tp1
            take_profit_2 = entry - atr * BOT_ATR_TARGET2_MULTIPLIER
            if 0 < support_level < entry:
                take_profit_2 = min(take_profit_2, support_level)
            take_profit_1 = max(0.01, take_profit_1)
            take_profit_2 = max(0.01, take_profit_2)
            risk = stop_loss - entry
            reward_1 = entry - take_profit_1
            reward_2 = entry - take_profit_2

        if risk <= 0:
            return default_context

        rr1 = reward_1 / risk if risk else 0
        rr2 = reward_2 / risk if risk else 0
        plan_quality = "Good R/R" if rr2 >= BOT_MIN_RISK_REWARD else "Weak R/R"

        return {
            "entry_price": round(entry, 4),
            "stop_loss": round(stop_loss, 4),
            "take_profit_1": round(take_profit_1, 4),
            "take_profit_2": round(take_profit_2, 4),
            "risk_per_share": round(risk, 4),
            "reward_1": round(reward_1, 4),
            "reward_2": round(reward_2, 4),
            "risk_reward_1": round(rr1, 2),
            "risk_reward_2": round(rr2, 2),
            "trade_plan": plan_quality,
            "trade_management_notes": f"ATR {round(atr, 4)} | max hold {BOT_TRADE_PLAN_MAX_HOLD_DAYS} days",
        }

    except Exception as error:
        log(f"Trade management calculation error: {error}")
        return default_context

def calculate_market_regime_from_anchor_scores(anchor_scores):
    if not anchor_scores:
        return "Unavailable", 0

    try:
        average_score = sum(anchor_scores) / len(anchor_scores)
    except Exception:
        return "Unavailable", 0

    if average_score >= 75:
        return "Bullish", round(average_score, 2)

    if average_score < 50:
        return "Bearish", round(average_score, 2)

    return "Neutral", round(average_score, 2)


def build_market_context(anchor_tickers, scan_started_at=None):
    default_context = {
        "market_regime": "Unavailable",
        "market_score": 0,
        "market_anchors": "None",
        "market_notes": "market trend unavailable",
        "advanced_market_regime": "Unavailable",
        "regime_strength": 0,
        "risk_mode": "Unavailable",
        "regime_notes": "market trend unavailable",
    }

    if not BOT_MARKET_TREND_FILTER_ENABLED:
        default_context["market_regime"] = "Disabled"
        default_context["advanced_market_regime"] = "Disabled"
        default_context["risk_mode"] = "Disabled"
        default_context["market_notes"] = "market trend filter disabled"
        default_context["regime_notes"] = "market trend filter disabled"
        return default_context

    cleaned_anchors = clean_ticker_list(anchor_tickers or [])
    if not cleaned_anchors:
        return default_context

    anchor_scores = []
    anchor_labels = []

    for anchor in cleaned_anchors:
        if SHUTDOWN_REQUESTED:
            break

        if not mtf_time_guard_allows(scan_started_at):
            default_context["market_regime"] = "Skipped"
            default_context["market_notes"] = "market trend skipped by time guard"
            return default_context

        try:
            anchor_data = get_price_data(anchor, "1y", "1d")
            anchor_frame = score_price_frame(anchor_data)

            if not anchor_frame:
                continue

            anchor_scores.append(anchor_frame["score"])
            anchor_labels.append(f"{anchor}:{anchor_frame['trend']}")

        except Exception as error:
            log(f"Market trend anchor error for {anchor}: {error}")

    if len(anchor_scores) < BOT_MARKET_TREND_MIN_ANCHORS:
        return default_context

    market_regime, market_score = calculate_market_regime_from_anchor_scores(anchor_scores)

    advanced = calculate_advanced_market_regime(anchor_scores, anchor_labels)

    return {
        "market_regime": market_regime,
        "market_score": market_score,
        "market_anchors": ", ".join(anchor_labels) if anchor_labels else "None",
        "market_notes": f"{market_regime} market from {len(anchor_scores)} anchor(s)",
        "advanced_market_regime": advanced.get("advanced_market_regime", market_regime),
        "regime_strength": advanced.get("regime_strength", 0),
        "risk_mode": advanced.get("risk_mode", "Unavailable"),
        "regime_notes": advanced.get("regime_notes", "advanced regime unavailable"),
    }


def build_market_contexts(scan_started_at=None):
    if not BOT_MARKET_TREND_FILTER_ENABLED:
        disabled_context = {
            "market_regime": "Disabled",
            "market_score": 0,
            "market_anchors": "None",
            "market_notes": "market trend filter disabled",
        }
        return {"Crypto": disabled_context, "Stock": disabled_context}

    return {
        "Crypto": build_market_context(BOT_CRYPTO_MARKET_TICKERS, scan_started_at),
        "Stock": build_market_context(BOT_STOCK_MARKET_TICKERS, scan_started_at),
    }


def unavailable_market_context(reason="market trend unavailable"):
    return {
        "market_regime": "Unavailable",
        "market_score": 0,
        "market_anchors": "None",
        "market_notes": reason,
    }


def safe_build_market_contexts(scan_started_at=None):
    try:
        contexts = build_market_contexts(scan_started_at)

        if not isinstance(contexts, dict):
            raise ValueError("market context builder returned a non-dict value")

        return {
            "Crypto": contexts.get("Crypto") or unavailable_market_context(),
            "Stock": contexts.get("Stock") or unavailable_market_context(),
        }

    except Exception as error:
        log(f"Market context build error: {error}")
        fallback = unavailable_market_context("market trend unavailable after context error")
        return {"Crypto": fallback, "Stock": fallback}


def calculate_market_trend_adjustment(primary_trend, market_context):
    if not BOT_MARKET_TREND_FILTER_ENABLED:
        return 0, "Disabled"

    market_context = market_context or {}
    market_regime = market_context.get("market_regime", "Unavailable")
    notes = market_context.get("market_notes", "market trend unavailable")
    adjustment = 0

    if market_regime not in ["Bullish", "Bearish", "Neutral"]:
        return 0, notes

    if primary_trend == "Bullish":
        if market_regime == "Bullish":
            adjustment = BOT_MARKET_TREND_MAX_ADJUSTMENT
            notes = f"market confirms bullish ({notes})"
        elif market_regime == "Bearish":
            adjustment = -BOT_MARKET_TREND_MAX_ADJUSTMENT
            notes = f"market conflicts bullish ({notes})"
        else:
            notes = f"neutral market for bullish setup ({notes})"

    elif primary_trend == "Bearish":
        if market_regime == "Bearish":
            adjustment = -BOT_MARKET_TREND_MAX_ADJUSTMENT
            notes = f"market confirms bearish ({notes})"
        elif market_regime == "Bullish":
            adjustment = BOT_MARKET_TREND_MAX_ADJUSTMENT
            notes = f"market conflicts bearish ({notes})"
        else:
            notes = f"neutral market for bearish setup ({notes})"

    else:
        notes = f"market checked but primary trend is neutral ({notes})"

    return int(adjustment), notes


def calculate_technical_score_from_latest(latest, current_price):
    rsi = latest.get("RSI")
    macd = latest.get("MACD")
    macd_signal = latest.get("MACD Signal")
    ma50 = latest.get("MA50")
    ma200 = latest.get("MA200")

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

    return technical_score


def timeframe_trend_from_score(score):
    try:
        score = float(score)
    except Exception:
        return "Unknown"

    if score >= 75:
        return "Bullish"

    if score < 50:
        return "Bearish"

    return "Neutral"


def is_known_trend(trend):
    return trend in ["Bullish", "Bearish", "Neutral"]


def trend_pair_text(short_trend, higher_trend):
    return f"{BOT_SHORT_TIMEFRAME_INTERVAL.upper()} {short_trend} / {BOT_MOMENTUM_TIMEFRAME_INTERVAL.upper()} momentum / {BOT_HIGHER_TIMEFRAME_LABEL} {higher_trend}"


def score_price_frame(data):
    if data.empty or len(data) < BOT_MTF_REQUIRE_MIN_ROWS:
        return None

    data = calculate_indicators(data)
    latest = data.iloc[-1]
    current_price = float(latest["Close"])

    technical_score = calculate_technical_score_from_latest(latest, current_price)

    return {
        "score": technical_score,
        "trend": timeframe_trend_from_score(technical_score),
        "rsi": safe_latest_value(latest, "RSI", 0),
        "macd": safe_latest_value(latest, "MACD", 0),
        "macd_signal": safe_latest_value(latest, "MACD Signal", 0),
    }


def calculate_mtf_adjustment(primary_trend, short_trend, higher_trend):
    if not BOT_MULTI_TIMEFRAME_ENABLED:
        return 0, "Disabled"

    adjustment = 0
    notes = []
    short_points = BOT_MTF_SHORT_CONFIRM_POINTS
    higher_points = BOT_MTF_HIGHER_CONFIRM_POINTS

    short_label = BOT_SHORT_TIMEFRAME_INTERVAL.upper()
    higher_label = BOT_HIGHER_TIMEFRAME_LABEL

    if not BOT_MTF_SHORT_ENABLED:
        notes.append(f"{short_label} disabled")
    elif short_trend == "Skipped":
        notes.append(f"{short_label} skipped by time guard")
    elif not is_known_trend(short_trend):
        notes.append(f"{short_label} unavailable")

    if not BOT_MTF_HIGHER_ENABLED:
        notes.append(f"{higher_label} disabled")
    elif higher_trend == "Skipped":
        notes.append(f"{higher_label} skipped by time guard")
    elif not is_known_trend(higher_trend):
        notes.append(f"{higher_label} unavailable")

    if primary_trend == "Bullish":
        if short_trend == "Bullish":
            adjustment += short_points
            notes.append(f"{short_label} confirms bullish")
        elif short_trend == "Bearish":
            adjustment -= short_points
            notes.append(f"{short_label} conflicts bullish")

        if higher_trend == "Bullish":
            adjustment += higher_points
            notes.append(f"{higher_label} confirms bullish")
        elif higher_trend == "Bearish":
            adjustment -= higher_points
            notes.append(f"{higher_label} conflicts bullish")

    elif primary_trend == "Bearish":
        if short_trend == "Bearish":
            adjustment -= short_points
            notes.append(f"{short_label} confirms bearish")
        elif short_trend == "Bullish":
            adjustment += short_points
            notes.append(f"{short_label} conflicts bearish")

        if higher_trend == "Bearish":
            adjustment -= higher_points
            notes.append(f"{higher_label} confirms bearish")
        elif higher_trend == "Bullish":
            adjustment += higher_points
            notes.append(f"{higher_label} conflicts bearish")

    else:
        if short_trend == "Bullish" and higher_trend == "Bullish":
            adjustment += min(short_points + higher_points, BOT_MTF_MAX_ADJUSTMENT)
            notes.append(f"{short_label}/{higher_label} leaning bullish")
        elif short_trend == "Bearish" and higher_trend == "Bearish":
            adjustment -= min(short_points + higher_points, BOT_MTF_MAX_ADJUSTMENT)
            notes.append(f"{short_label}/{higher_label} leaning bearish")
        elif is_known_trend(short_trend) or is_known_trend(higher_trend):
            notes.append("mixed confirmation")

    adjustment = max(-BOT_MTF_MAX_ADJUSTMENT, min(adjustment, BOT_MTF_MAX_ADJUSTMENT))

    if not notes:
        notes.append("not enough confirmation")

    return adjustment, ", ".join(notes)



def confidence_grade(confidence):
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0

    if confidence >= 95:
        return "A+"
    if confidence >= 90:
        return "A"
    if confidence >= 80:
        return "B"
    if confidence >= 70:
        return "C"
    if confidence >= 60:
        return "D"
    return "F"



def normalize_confidence_weights():
    weights = {
        "rsi": BOT_CONFIDENCE_RSI_WEIGHT,
        "macd": BOT_CONFIDENCE_MACD_WEIGHT,
        "trend": BOT_CONFIDENCE_TREND_WEIGHT,
        "mtf": BOT_CONFIDENCE_MTF_WEIGHT,
        "volume": BOT_CONFIDENCE_VOLUME_WEIGHT,
        "market": BOT_CONFIDENCE_MARKET_WEIGHT,
        "support_resistance": BOT_CONFIDENCE_SR_WEIGHT,
        "news": BOT_CONFIDENCE_NEWS_WEIGHT,
        "risk_reward": BOT_CONFIDENCE_RISK_REWARD_WEIGHT,
    }

    total = sum(value for value in weights.values() if value > 0)

    if total <= 0:
        return {
            "rsi": 0.10,
            "macd": 0.10,
            "trend": 0.15,
            "mtf": 0.20,
            "volume": 0.12,
            "market": 0.15,
            "support_resistance": 0.10,
            "news": 0.08,
            "risk_reward": 0.10,
        }

    return {
        key: value / total
        for key, value in weights.items()
    }


def component_confidence_from_rsi(rsi_value, signal_direction):
    rsi = safe_float(rsi_value, 50)

    if signal_direction == "BUY":
        if 45 <= rsi <= 65:
            return 85
        if 35 <= rsi < 45:
            return 75
        if rsi < 35:
            return 65
        if 65 < rsi <= 72:
            return 55
        return 30

    if signal_direction == "SELL":
        if 35 <= rsi <= 55:
            return 80
        if 55 < rsi <= 70:
            return 65
        if rsi > 70:
            return 75
        if 25 <= rsi < 35:
            return 55
        return 30

    return BOT_CONFIDENCE_BASELINE


def component_confidence_from_macd(macd_value, macd_signal_value, signal_direction):
    macd = safe_float(macd_value, 0)
    macd_signal = safe_float(macd_signal_value, 0)

    if signal_direction == "BUY":
        return 85 if macd > macd_signal else 40

    if signal_direction == "SELL":
        return 85 if macd < macd_signal else 40

    return BOT_CONFIDENCE_BASELINE


def component_confidence_from_risk_reward(trade_context, signal_direction):
    if not BOT_TRADE_MANAGEMENT_ENABLED or signal_direction not in ["BUY", "SELL"]:
        return BOT_CONFIDENCE_BASELINE

    trade_context = trade_context or {}
    rr2 = safe_float(trade_context.get("risk_reward_2", 0), 0)

    if rr2 >= 3:
        return 95
    if rr2 >= 2:
        return 85
    if rr2 >= BOT_MIN_RISK_REWARD:
        return 75
    if rr2 >= 1:
        return 55
    return 35

def bounded_percent(value):
    try:
        value = float(value)
    except Exception:
        value = 0

    return round(max(0, min(value, 100)), 2)


def component_confidence_from_adjustment(adjustment, max_adjustment, signal_direction):
    try:
        adjustment = float(adjustment)
        max_adjustment = float(max_adjustment)
    except Exception:
        return BOT_CONFIDENCE_BASELINE

    if max_adjustment <= 0:
        return BOT_CONFIDENCE_BASELINE

    if signal_direction == "BUY":
        directional_strength = adjustment / max_adjustment
    elif signal_direction == "SELL":
        directional_strength = -adjustment / max_adjustment
    else:
        directional_strength = 0

    directional_strength = max(-1, min(directional_strength, 1))
    return bounded_percent(BOT_CONFIDENCE_BASELINE + directional_strength * 50)


def component_confidence_from_trend(primary_trend, signal_direction):
    if signal_direction == "BUY":
        if primary_trend == "Bullish":
            return 100
        if primary_trend == "Neutral":
            return 60
        if primary_trend == "Bearish":
            return 20

    if signal_direction == "SELL":
        if primary_trend == "Bearish":
            return 100
        if primary_trend == "Neutral":
            return 60
        if primary_trend == "Bullish":
            return 20

    return BOT_CONFIDENCE_BASELINE


def component_confidence_from_volume(volume_context, signal_direction):
    if not BOT_VOLUME_SPIKE_ENABLED:
        return BOT_CONFIDENCE_BASELINE

    volume_context = volume_context or {}
    volume_signal = str(volume_context.get("volume_signal", "Unavailable"))
    adjustment = volume_context.get("volume_score_adj", 0)

    if volume_signal in ["Unavailable", "Disabled"]:
        return BOT_CONFIDENCE_BASELINE

    return component_confidence_from_adjustment(
        adjustment,
        max(1, BOT_VOLUME_MAX_ADJUSTMENT),
        signal_direction
    )


def component_confidence_from_market(market_adjustment, market_context, signal_direction):
    if not BOT_MARKET_TREND_FILTER_ENABLED:
        return BOT_CONFIDENCE_BASELINE

    market_context = market_context or {}
    market_regime = market_context.get("market_regime", "Unavailable")

    if market_regime not in ["Bullish", "Bearish", "Neutral"]:
        return BOT_CONFIDENCE_BASELINE

    return component_confidence_from_adjustment(
        market_adjustment,
        max(1, BOT_MARKET_TREND_MAX_ADJUSTMENT),
        signal_direction
    )


def component_confidence_from_support_resistance(support_resistance_context, signal_direction):
    if not BOT_SUPPORT_RESISTANCE_ENABLED:
        return BOT_CONFIDENCE_BASELINE

    support_resistance_context = support_resistance_context or {}
    sr_signal = str(support_resistance_context.get("support_resistance_signal", "Unavailable"))
    sr_adjustment = support_resistance_context.get("support_resistance_score_adj", 0)

    if sr_signal in ["Unavailable", "Disabled"]:
        return BOT_CONFIDENCE_BASELINE

    return component_confidence_from_adjustment(
        sr_adjustment,
        max(1, BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT),
        signal_direction
    )


def component_confidence_from_news(news_sentiment_context, signal_direction):
    if not BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED:
        return BOT_CONFIDENCE_BASELINE

    news_sentiment_context = news_sentiment_context or {}
    news_signal = str(news_sentiment_context.get("news_sentiment_label", "Unavailable"))
    news_adjustment = news_sentiment_context.get("news_score_adj", 0)

    if news_signal in ["Unavailable", "Disabled"]:
        return BOT_CONFIDENCE_BASELINE

    return component_confidence_from_adjustment(
        news_adjustment,
        max(1, BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT),
        signal_direction
    )


def component_confidence_from_mtf(mtf_adjustment, short_trend, higher_trend, signal_direction, momentum_trend="Unknown"):
    if not BOT_MULTI_TIMEFRAME_ENABLED:
        return BOT_CONFIDENCE_BASELINE

    known_count = len([
        trend for trend in [short_trend, momentum_trend, higher_trend]
        if is_known_trend(trend)
    ])

    if known_count == 0:
        return BOT_CONFIDENCE_BASELINE

    return component_confidence_from_adjustment(
        mtf_adjustment,
        max(1, BOT_MTF_MAX_ADJUSTMENT),
        signal_direction
    )


def signal_direction_from_score(final_score):
    try:
        final_score = float(final_score)
    except Exception:
        return "HOLD"

    if final_score >= 75:
        return "BUY"

    if final_score < 50:
        return "SELL"

    return "HOLD"



def calculate_confidence_breakdown(
    final_score,
    primary_trend,
    mtf_adjustment,
    short_trend,
    higher_trend,
    volume_context,
    market_adjustment,
    market_context,
    support_resistance_context=None,
    news_sentiment_context=None,
    trade_context=None,
    rsi_value=50,
    macd_value=0,
    macd_signal_value=0,
    momentum_trend="Unknown",
):
    legacy_signal, legacy_confidence = calculate_signal_and_confidence(final_score)
    signal_direction = signal_direction_from_score(final_score)

    if not BOT_CONFIDENCE_ENGINE_ENABLED or signal_direction == "HOLD":
        legacy_confidence = bounded_percent(legacy_confidence)
        return {
            "confidence_percent": legacy_confidence,
            "confidence_grade": confidence_grade(legacy_confidence),
            "rsi_confidence": legacy_confidence,
            "macd_confidence": legacy_confidence,
            "trend_confidence": legacy_confidence,
            "technical_confidence": legacy_confidence,
            "mtf_confidence": BOT_CONFIDENCE_BASELINE,
            "volume_confidence": BOT_CONFIDENCE_BASELINE,
            "market_confidence": BOT_CONFIDENCE_BASELINE,
            "sr_confidence": BOT_CONFIDENCE_BASELINE,
            "news_confidence": BOT_CONFIDENCE_BASELINE,
            "risk_reward_confidence": BOT_CONFIDENCE_BASELINE,
            "confidence_engine": "Legacy" if not BOT_CONFIDENCE_ENGINE_ENABLED else "Hold Score",
            "confidence_notes": "confidence based on final score",
        }

    rsi_confidence = component_confidence_from_rsi(rsi_value, signal_direction)
    macd_confidence = component_confidence_from_macd(macd_value, macd_signal_value, signal_direction)
    trend_confidence = component_confidence_from_trend(primary_trend, signal_direction)
    technical_confidence = bounded_percent((rsi_confidence + macd_confidence + trend_confidence) / 3)

    mtf_confidence = component_confidence_from_mtf(
        mtf_adjustment,
        short_trend,
        higher_trend,
        signal_direction,
        momentum_trend
    )
    volume_confidence = component_confidence_from_volume(volume_context, signal_direction)
    market_confidence = component_confidence_from_market(
        market_adjustment,
        market_context,
        signal_direction
    )
    sr_confidence = component_confidence_from_support_resistance(
        support_resistance_context,
        signal_direction
    )
    news_confidence = component_confidence_from_news(
        news_sentiment_context,
        signal_direction
    )
    risk_reward_confidence = component_confidence_from_risk_reward(
        trade_context,
        signal_direction
    )

    weights = normalize_confidence_weights()
    confidence = (
        rsi_confidence * weights["rsi"]
        + macd_confidence * weights["macd"]
        + trend_confidence * weights["trend"]
        + mtf_confidence * weights["mtf"]
        + volume_confidence * weights["volume"]
        + market_confidence * weights["market"]
        + sr_confidence * weights["support_resistance"]
        + news_confidence * weights["news"]
        + risk_reward_confidence * weights["risk_reward"]
    )

    # Keep confidence realistic: components can improve confidence, but not wildly
    # beyond the actual directional score.
    score_cap = max(legacy_confidence, min(100, legacy_confidence + 22))
    confidence = min(confidence, score_cap)
    confidence = bounded_percent(confidence)

    agreement_parts = []
    if trend_confidence >= 70:
        agreement_parts.append("trend")
    if rsi_confidence >= 70:
        agreement_parts.append("RSI")
    if macd_confidence >= 70:
        agreement_parts.append("MACD")
    if mtf_confidence >= 70:
        agreement_parts.append("MTF")
    if volume_confidence >= 70:
        agreement_parts.append("volume")
    if market_confidence >= 70:
        agreement_parts.append("market")
    if sr_confidence >= 70:
        agreement_parts.append("S/R")
    if news_confidence >= 70:
        agreement_parts.append("news")
    if risk_reward_confidence >= 70:
        agreement_parts.append("R/R")

    confidence_notes = (
        f"{len(agreement_parts)}/9 confirmations: "
        + (", ".join(agreement_parts) if agreement_parts else "none")
    )

    return {
        "confidence_percent": confidence,
        "confidence_grade": confidence_grade(confidence),
        "rsi_confidence": bounded_percent(rsi_confidence),
        "macd_confidence": bounded_percent(macd_confidence),
        "trend_confidence": bounded_percent(trend_confidence),
        "technical_confidence": bounded_percent(technical_confidence),
        "mtf_confidence": bounded_percent(mtf_confidence),
        "volume_confidence": bounded_percent(volume_confidence),
        "market_confidence": bounded_percent(market_confidence),
        "sr_confidence": bounded_percent(sr_confidence),
        "news_confidence": bounded_percent(news_confidence),
        "risk_reward_confidence": bounded_percent(risk_reward_confidence),
        "confidence_engine": "v31.1 Quality Weighted",
        "confidence_notes": confidence_notes,
    }


def calculate_signal_and_confidence(final_score):
    final_score = max(0, min(final_score, 120))

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
    hold_confidence = max(0, min(hold_confidence, 50))
    return "HOLD", hold_confidence


def score_ticker(ticker, scan_started_at=None, market_contexts=None, news_sentiment_contexts=None):
    daily_data = get_price_data(ticker, "1y", "1d")

    if daily_data.empty or len(daily_data) < 50:
        return None

    daily_data = calculate_indicators(daily_data)
    latest = daily_data.iloc[-1]

    current_price = float(latest["Close"])
    previous_price = float(daily_data["Close"].iloc[-2])

    if previous_price == 0:
        price_change_percent = 0
    else:
        price_change_percent = ((current_price - previous_price) / previous_price) * 100

    rsi_value = safe_latest_value(latest, "RSI", 0)
    macd_value = safe_latest_value(latest, "MACD", 0)
    macd_signal_value = safe_latest_value(latest, "MACD Signal", 0)

    technical_score = calculate_technical_score_from_latest(latest, current_price)
    daily_trend = timeframe_trend_from_score(technical_score)

    short_score = 0
    short_trend = "Unknown"
    momentum_score = 0
    momentum_trend = "Unknown"

    if BOT_MULTI_TIMEFRAME_ENABLED and BOT_MTF_SHORT_ENABLED:
        if mtf_time_guard_allows(scan_started_at):
            short_data = get_price_data(ticker, BOT_SHORT_TIMEFRAME_PERIOD, BOT_SHORT_TIMEFRAME_INTERVAL)
            short_frame = score_price_frame(short_data)
            if short_frame:
                short_score = short_frame["score"]
                short_trend = short_frame["trend"]
            momentum_data = get_price_data(ticker, BOT_MOMENTUM_TIMEFRAME_PERIOD, BOT_MOMENTUM_TIMEFRAME_INTERVAL)
            momentum_frame = score_price_frame(momentum_data)
            if momentum_frame:
                momentum_score = momentum_frame["score"]
                momentum_trend = momentum_frame["trend"]
        else:
            short_trend = "Skipped"
            momentum_trend = "Skipped"

    weekly_score = 0
    weekly_trend = "Unknown"

    if BOT_MULTI_TIMEFRAME_ENABLED and BOT_MTF_HIGHER_ENABLED:
        if mtf_time_guard_allows(scan_started_at):
            higher_data = resample_to_higher_timeframe(daily_data)
            higher_frame = score_price_frame(higher_data)

            if higher_frame:
                weekly_score = higher_frame["score"]
                weekly_trend = higher_frame["trend"]
        else:
            weekly_trend = "Skipped"

    # v31.1 MTF stack: 1D trend + 4H trend + 1H momentum.
    combined_short_trend = short_trend
    if momentum_trend in ["Bullish", "Bearish"] and short_trend in ["Bullish", "Bearish"] and momentum_trend != short_trend:
        combined_short_trend = "Neutral"
    elif momentum_trend in ["Bullish", "Bearish"] and short_trend not in ["Bullish", "Bearish"]:
        combined_short_trend = momentum_trend

    mtf_adjustment, mtf_alignment = calculate_mtf_adjustment(
        daily_trend,
        combined_short_trend,
        weekly_trend
    )
    mtf_alignment = f"1D {daily_trend} | 4H {short_trend} | 1H {momentum_trend} | Higher {weekly_trend} | {mtf_alignment}"

    volume_context = calculate_volume_context(daily_data, daily_trend)
    volume_adjustment = volume_context["volume_score_adj"]

    market_contexts = market_contexts or {}
    market_context = market_contexts.get(get_asset_type(ticker), {})
    market_adjustment, market_alignment = calculate_market_trend_adjustment(daily_trend, market_context)

    support_resistance_context = calculate_support_resistance_context(daily_data, daily_trend)
    support_resistance_adjustment = support_resistance_context["support_resistance_score_adj"]

    news_sentiment_contexts = news_sentiment_contexts or {}
    news_sentiment_context = news_sentiment_contexts.get(ticker, summarize_news_sentiment([]))
    news_adjustment = news_sentiment_context.get("news_score_adj", 0)

    final_score = max(0, min(technical_score + mtf_adjustment + volume_adjustment + market_adjustment + support_resistance_adjustment + news_adjustment, 120))
    ai_signal, legacy_confidence_percent = calculate_signal_and_confidence(final_score)

    trade_context = calculate_trade_management_context(
        daily_data,
        ai_signal,
        current_price,
        support_resistance_context
    )

    position_context = calculate_position_sizing_context(ai_signal, current_price, trade_context)
    trailing_context = calculate_trailing_stop_context(ai_signal, current_price, trade_context)

    confidence_context = calculate_confidence_breakdown(
        final_score,
        daily_trend,
        mtf_adjustment,
        short_trend,
        weekly_trend,
        volume_context,
        market_adjustment,
        market_context,
        support_resistance_context,
        news_sentiment_context,
        trade_context,
        rsi_value,
        macd_value,
        macd_signal_value,
        momentum_trend
    )

    confidence_percent = confidence_context["confidence_percent"]

    return {
        "Ticker": ticker,
        "Market": get_asset_type(ticker),
        "Price": round(current_price, 2),
        "Daily Change %": round(price_change_percent, 2),
        "Volume": volume_context["volume"],
        "Avg Volume": volume_context["avg_volume"],
        "Relative Volume": volume_context["relative_volume"],
        "Volume Signal": volume_context["volume_signal"],
        "Volume Score Adj": volume_adjustment,
        "Market Regime": market_context.get("market_regime", "Unavailable"),
        "Market Score": market_context.get("market_score", 0),
        "Market Anchors": market_context.get("market_anchors", "None"),
        "Advanced Market Regime": market_context.get("advanced_market_regime", market_context.get("market_regime", "Unavailable")),
        "Regime Strength": market_context.get("regime_strength", 0),
        "Risk Mode": market_context.get("risk_mode", "Unavailable"),
        "Regime Notes": market_context.get("regime_notes", market_context.get("market_notes", "")),
        "Market Trend Adj": market_adjustment,
        "Market Alignment": market_alignment,
        "Support Level": support_resistance_context["support_level"],
        "Resistance Level": support_resistance_context["resistance_level"],
        "Distance To Support %": support_resistance_context["distance_to_support_pct"],
        "Distance To Resistance %": support_resistance_context["distance_to_resistance_pct"],
        "S/R Signal": support_resistance_context["support_resistance_signal"],
        "S/R Score Adj": support_resistance_adjustment,
        "S/R Strength": support_resistance_context.get("sr_strength", 0),
        "S/R Position": support_resistance_context.get("sr_position", "Unavailable"),
        "Support Touches": support_resistance_context.get("support_touches", 0),
        "Resistance Touches": support_resistance_context.get("resistance_touches", 0),
        "S/R Notes": support_resistance_context.get("sr_notes", ""),
        "Trade Entry": trade_context.get("entry_price", 0),
        "Stop Loss": trade_context.get("stop_loss", 0),
        "Take Profit 1": trade_context.get("take_profit_1", 0),
        "Take Profit 2": trade_context.get("take_profit_2", 0),
        "Risk/Reward 1": trade_context.get("risk_reward_1", 0),
        "Risk/Reward 2": trade_context.get("risk_reward_2", 0),
        "Trade Plan": trade_context.get("trade_plan", "Unavailable"),
        "Trade Notes": trade_context.get("trade_management_notes", ""),
        "Account Size": position_context.get("account_size", 0),
        "Risk %": position_context.get("risk_pct", 0),
        "Risk Dollars": position_context.get("risk_dollars", 0),
        "Position Size": position_context.get("position_size", 0),
        "Position Value": position_context.get("position_value", 0),
        "Position Notes": position_context.get("position_notes", ""),
        "Trailing Stop": trailing_context.get("trailing_stop", 0),
        "Breakeven Trigger": trailing_context.get("breakeven_trigger", 0),
        "Trail Distance": trailing_context.get("trail_distance", 0),
        "Trailing Notes": trailing_context.get("trailing_notes", ""),
        "Asset Category": asset_category(ticker),
        "Signal Quality Score": 0,
        "Signal Rank": "",
        "Alert Approved": "NO",
        "Exposure Notes": "not evaluated",
        "News Sentiment": news_sentiment_context.get("news_sentiment_label", "Unavailable"),
        "News Sentiment Score": news_sentiment_context.get("news_sentiment_score", 0),
        "News Strength": news_sentiment_context.get("news_strength", 0),
        "News Score Adj": news_adjustment,
        "News Headlines": news_sentiment_context.get("news_headlines", "None"),
        "News Notes": news_sentiment_context.get("news_notes", "news sentiment unavailable"),
        "RSI Confidence": confidence_context.get("rsi_confidence", 0),
        "MACD Confidence": confidence_context.get("macd_confidence", 0),
        "Trend Confidence": confidence_context.get("trend_confidence", 0),
        "Technical Confidence": confidence_context["technical_confidence"],
        "MTF Confidence": confidence_context["mtf_confidence"],
        "Volume Confidence": confidence_context["volume_confidence"],
        "Market Confidence": confidence_context["market_confidence"],
        "S/R Confidence": confidence_context["sr_confidence"],
        "News Confidence": confidence_context.get("news_confidence", BOT_CONFIDENCE_BASELINE),
        "Risk/Reward Confidence": confidence_context.get("risk_reward_confidence", BOT_CONFIDENCE_BASELINE),
        "Confidence Grade": confidence_context["confidence_grade"],
        "Confidence Engine": confidence_context["confidence_engine"],
        "Confidence Notes": confidence_context["confidence_notes"],
        "Legacy Confidence %": round(bounded_percent(legacy_confidence_percent), 2),
        "RSI": round(rsi_value, 2),
        "MACD": round(macd_value, 2),
        "MACD Signal": round(macd_signal_value, 2),
        "Technical Score": technical_score,
        "Short TF Score": short_score,
        "Short TF Trend": short_trend,
        "Momentum TF Score": momentum_score,
        "Momentum TF Trend": momentum_trend,
        "Daily Trend": daily_trend,
        "Higher TF Score": weekly_score,
        "Higher TF Trend": weekly_trend,
        "MTF Alignment": mtf_alignment,
        "MTF Score Adj": mtf_adjustment,
        "Final Score": final_score,
        "AI Confidence %": round(confidence_percent, 2),
        "AI Signal": ai_signal
    }


# ======================================================
# PHASE 3 PROFESSIONAL RISK / RANKING HELPERS
# ======================================================

def asset_category(ticker):
    ticker = str(ticker or "").upper()
    if ticker.endswith("-USD"):
        majors = {"BTC-USD", "ETH-USD"}
        return "Crypto Major" if ticker in majors else "Crypto Alt"
    technology = {"AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "AMZN", "PLTR", "TSLA"}
    indices = {"SPY", "QQQ"}
    if ticker in indices:
        return "Index ETF"
    if ticker in technology:
        return "Technology/Growth"
    return "Other Stock"


def calculate_advanced_market_regime(anchor_scores, anchor_labels=None):
    if not BOT_MARKET_REGIME_DETECTION_ENABLED:
        return {
            "advanced_market_regime": "Disabled",
            "regime_strength": 0,
            "risk_mode": "Disabled",
            "regime_notes": "advanced regime detection disabled",
        }

    if not anchor_scores:
        return {
            "advanced_market_regime": "Unavailable",
            "regime_strength": 0,
            "risk_mode": "Unavailable",
            "regime_notes": "not enough anchor data",
        }

    scores = [safe_float(value, 0) for value in anchor_scores]
    average_score = sum(scores) / len(scores)
    dispersion = max(scores) - min(scores) if len(scores) > 1 else 0
    bullish_count = len([score for score in scores if score >= 75])
    bearish_count = len([score for score in scores if score < 50])

    if average_score >= 80 and bearish_count == 0:
        regime = "Bull Trend"
        risk_mode = "Risk-On"
    elif average_score >= 68 and bearish_count == 0:
        regime = "Bullish/Constructive"
        risk_mode = "Risk-On"
    elif average_score < 45 and bullish_count == 0:
        regime = "Bear Trend"
        risk_mode = "Risk-Off"
    elif average_score < 55 and bullish_count == 0:
        regime = "Bearish/Defensive"
        risk_mode = "Risk-Off"
    elif dispersion >= BOT_MARKET_REGIME_VOLATILITY_THRESHOLD * 5:
        regime = "Mixed/Volatile"
        risk_mode = "Caution"
    else:
        regime = "Sideways/Neutral"
        risk_mode = "Neutral"

    strength = min(100, max(0, round(abs(average_score - 50) * 2, 2)))
    labels = ", ".join(anchor_labels or []) if anchor_labels else "anchors scored"

    return {
        "advanced_market_regime": regime,
        "regime_strength": strength,
        "risk_mode": risk_mode,
        "regime_notes": f"avg {round(average_score, 2)} | spread {round(dispersion, 2)} | {labels}",
    }


def calculate_position_sizing_context(signal, current_price, trade_context):
    default = {
        "account_size": BOT_ACCOUNT_SIZE,
        "risk_pct": BOT_RISK_PER_TRADE_PCT,
        "risk_dollars": 0,
        "position_size": 0,
        "position_value": 0,
        "max_position_value": round(BOT_ACCOUNT_SIZE * BOT_MAX_POSITION_PCT / 100, 2),
        "position_notes": "position sizing unavailable",
    }

    if not BOT_POSITION_SIZING_ENABLED:
        default["position_notes"] = "position sizing disabled"
        return default

    if not is_directional_signal(signal):
        default["position_notes"] = "hold signal has no position size"
        return default

    try:
        price = safe_float(current_price, 0)
        risk_per_share = safe_float((trade_context or {}).get("risk_per_share", 0), 0)
        if price <= 0 or risk_per_share <= 0:
            return default

        risk_dollars = BOT_ACCOUNT_SIZE * BOT_RISK_PER_TRADE_PCT / 100
        max_position_value = BOT_ACCOUNT_SIZE * BOT_MAX_POSITION_PCT / 100
        raw_size = risk_dollars / risk_per_share
        max_size = max_position_value / price
        position_size = max(0, min(raw_size, max_size))
        position_value = position_size * price
        capped = raw_size > max_size

        return {
            "account_size": round(BOT_ACCOUNT_SIZE, 2),
            "risk_pct": round(BOT_RISK_PER_TRADE_PCT, 2),
            "risk_dollars": round(risk_dollars, 2),
            "position_size": round(position_size, 6),
            "position_value": round(position_value, 2),
            "max_position_value": round(max_position_value, 2),
            "position_notes": "capped by max position" if capped else "sized by risk per trade",
        }
    except Exception as error:
        log(f"Position sizing error: {error}")
        return default


def calculate_trailing_stop_context(signal, current_price, trade_context):
    default = {
        "trailing_stop": 0,
        "breakeven_trigger": 0,
        "trail_distance": 0,
        "trailing_notes": "trailing stop unavailable",
    }

    if not BOT_TRAILING_STOP_ENABLED:
        default["trailing_notes"] = "trailing stop disabled"
        return default

    if not is_directional_signal(signal):
        default["trailing_notes"] = "hold signal has no trailing stop"
        return default

    try:
        price = safe_float(current_price, 0)
        trade_context = trade_context or {}
        risk_per_share = safe_float(trade_context.get("risk_per_share", 0), 0)
        stop_loss = safe_float(trade_context.get("stop_loss", 0), 0)
        notes = str(trade_context.get("trade_management_notes", ""))
        atr = 0
        match = re.search(r"ATR\s+([0-9.]+)", notes)
        if match:
            atr = safe_float(match.group(1), 0)
        if atr <= 0:
            atr = max(price * 0.02, 0.01)

        trail_distance = atr * BOT_TRAILING_ATR_MULTIPLIER
        if "BUY" in str(signal):
            trailing_stop = max(stop_loss, price - trail_distance)
            breakeven_trigger = price + risk_per_share * BOT_BREAKEVEN_TRIGGER_R
        else:
            trailing_stop = min(stop_loss, price + trail_distance) if stop_loss > price else price + trail_distance
            breakeven_trigger = price - risk_per_share * BOT_BREAKEVEN_TRIGGER_R

        return {
            "trailing_stop": round(max(0.01, trailing_stop), 4),
            "breakeven_trigger": round(max(0.01, breakeven_trigger), 4),
            "trail_distance": round(trail_distance, 4),
            "trailing_notes": f"ATR trail x{BOT_TRAILING_ATR_MULTIPLIER} | BE at {BOT_BREAKEVEN_TRIGGER_R}R",
        }
    except Exception as error:
        log(f"Trailing stop error: {error}")
        return default


def calculate_signal_quality_score(row):
    try:
        confidence = safe_float(row.get("AI Confidence %", 0), 0)
        rr = safe_float(row.get("Risk/Reward 2", 0), 0)
        rank_score = confidence
        rank_score += min(10, rr * 3)
        rank_score += max(-8, min(8, safe_float(row.get("MTF Score Adj", 0), 0) / 2))
        rank_score += max(-6, min(6, safe_float(row.get("Volume Score Adj", 0), 0) / 2))
        rank_score += max(-6, min(6, safe_float(row.get("S/R Score Adj", 0), 0) / 2))
        rank_score += max(-5, min(5, safe_float(row.get("News Score Adj", 0), 0) / 2))
        if str(row.get("Risk Mode", "")) == "Risk-Off" and "BUY" in str(row.get("AI Signal", "")):
            rank_score -= 8
        if str(row.get("Risk Mode", "")) == "Risk-On" and "SELL" in str(row.get("AI Signal", "")):
            rank_score -= 6
        return round(max(0, min(rank_score, 120)), 2)
    except Exception:
        return safe_float(row.get("AI Confidence %", 0), 0)


def mtf_alignment_passes(row):
    if not BOT_MTF_ALIGNMENT_REQUIRED:
        return True, "MTF alignment filter disabled"

    signal = str(row.get("AI Signal", ""))
    if not is_directional_signal(signal):
        return True, "non-directional signal"

    confidence = safe_float(row.get("AI Confidence %", 0), 0)
    if confidence < BOT_MTF_ALIGNMENT_MIN_CONFIDENCE:
        return True, "below MTF alignment confidence gate"

    direction = "Bullish" if "BUY" in signal else "Bearish"
    trends = [
        row.get("Daily Trend", "Unknown"),
        row.get("Short TF Trend", "Unknown"),
        row.get("Momentum TF Trend", "Unknown"),
        row.get("Higher TF Trend", "Unknown"),
    ]
    labels = ["1D", BOT_SHORT_TIMEFRAME_INTERVAL.upper(), BOT_MOMENTUM_TIMEFRAME_INTERVAL.upper(), BOT_HIGHER_TIMEFRAME_LABEL]

    if BOT_MTF_ALIGNMENT_ALLOW_NEUTRAL:
        supportive = len([trend for trend in trends if trend in [direction, "Neutral"]])
        conflicting = len([trend for trend in trends if trend in ["Bullish", "Bearish"] and trend != direction])
    else:
        supportive = len([trend for trend in trends if trend == direction])
        conflicting = len([trend for trend in trends if trend != direction])

    minimum_matches = max(BOT_MTF_ALIGNMENT_MIN_MATCHES, 3)
    known_directional = len([trend for trend in trends if trend in ["Bullish", "Bearish", "Neutral"]])
    alignment_text = "/".join(f"{label}:{trend}" for label, trend in zip(labels, trends))

    if known_directional < minimum_matches:
        return False, f"blocked: not enough MTF data ({alignment_text})"

    if supportive >= minimum_matches and conflicting == 0:
        return True, f"MTF aligned: {alignment_text}"

    return False, f"blocked: MTF not aligned ({alignment_text})"

def backtest_quality_for_ticker(ticker):
    try:
        if not BOT_BACKTEST_QUALITY_FILTER_ENABLED:
            return {"approved": True, "notes": "backtest quality filter disabled", "pf": 0, "wr": 0, "signals": 0}
        result = backtest_ticker(ticker)
        pf = safe_float(result.get("Profit Factor", 0), 0)
        wr = safe_float(result.get("Win Rate %", 0), 0)
        signals = int(safe_float(result.get("Signals Tested", 0), 0))
        if signals < BOT_BACKTEST_QUALITY_MIN_SIGNALS:
            return {"approved": False, "notes": f"blocked: low backtest sample ({signals})", "pf": pf, "wr": wr, "signals": signals}
        if pf < BOT_BACKTEST_QUALITY_MIN_PF or wr < BOT_BACKTEST_QUALITY_MIN_WIN_RATE:
            return {"approved": False, "notes": f"blocked: weak backtest PF {pf} WR {wr}%", "pf": pf, "wr": wr, "signals": signals}
        if pf < BOT_BACKTEST_QUALITY_STRONG_PF:
            return {"approved": True, "notes": f"watch: modest backtest PF {pf} WR {wr}%", "pf": pf, "wr": wr, "signals": signals, "penalty": BOT_BACKTEST_QUALITY_CONFIDENCE_PENALTY}
        return {"approved": True, "notes": f"backtest passed PF {pf} WR {wr}%", "pf": pf, "wr": wr, "signals": signals, "penalty": 0}
    except Exception as error:
        log(f"Backtest quality filter error for {ticker}: {error}")
        return {"approved": True, "notes": "backtest quality unavailable; allowed", "pf": 0, "wr": 0, "signals": 0, "penalty": 0}


def apply_quality_filters(candidate_rows):
    filtered = []
    for row in candidate_rows:
        mtf_ok, mtf_note = mtf_alignment_passes(row)
        notes = [str(row.get("Exposure Notes", "not evaluated")), mtf_note]
        if not mtf_ok:
            row["Alert Approved"] = "NO"
            row["Exposure Notes"] = " | ".join(notes)
            continue
        bt = backtest_quality_for_ticker(row.get("Ticker", ""))
        row["Backtest Quality PF"] = bt.get("pf", 0)
        row["Backtest Quality WR"] = bt.get("wr", 0)
        row["Backtest Quality Signals"] = bt.get("signals", 0)
        notes.append(bt.get("notes", "backtest quality checked"))
        if not bt.get("approved", True):
            row["Alert Approved"] = "NO"
            row["Exposure Notes"] = " | ".join(notes)
            continue
        penalty = safe_float(bt.get("penalty", 0), 0)
        if penalty > 0:
            row["AI Confidence %"] = round(max(0, safe_float(row.get("AI Confidence %", 0), 0) - penalty), 2)
            row["Signal Quality Score"] = calculate_signal_quality_score(row)
            notes.append(f"confidence reduced {penalty} by backtest filter")
        row["Exposure Notes"] = " | ".join(notes)
        filtered.append(row)
    return filtered

def assign_signal_rankings(rows):
    if not rows:
        return rows
    for row in rows:
        row["Asset Category"] = row.get("Asset Category") or asset_category(row.get("Ticker"))
        row["Signal Quality Score"] = calculate_signal_quality_score(row)
        row["Signal Rank"] = ""
        row["Alert Approved"] = "NO"
        row["Exposure Notes"] = "ranking disabled" if not BOT_SIGNAL_RANKING_ENABLED else "not evaluated"

    if not BOT_SIGNAL_RANKING_ENABLED:
        return rows

    directional = [row for row in rows if is_directional_signal(row.get("AI Signal", ""))]
    directional.sort(key=lambda item: item.get("Signal Quality Score", 0), reverse=True)
    for index, row in enumerate(directional, start=1):
        row["Signal Rank"] = index
    return rows


def apply_exposure_controls(candidate_rows):
    if not BOT_EXPOSURE_CONTROLS_ENABLED:
        for row in candidate_rows:
            row["Alert Approved"] = "YES"
            row["Exposure Notes"] = "exposure controls disabled"
        return candidate_rows

    approved = []
    market_counts = {}
    category_counts = {}
    max_alerts = BOT_MAX_ALERTS_PER_SCAN if BOT_MAX_ALERTS_PER_SCAN > 0 else len(candidate_rows)

    for row in sorted(candidate_rows, key=lambda item: item.get("Signal Quality Score", 0), reverse=True):
        market = row.get("Market", "Unknown")
        category = row.get("Asset Category", asset_category(row.get("Ticker")))
        row["Asset Category"] = category

        if len(approved) >= max_alerts:
            row["Alert Approved"] = "NO"
            row["Exposure Notes"] = "blocked: max alerts per scan reached"
            continue
        if market_counts.get(market, 0) >= BOT_MAX_ALERTS_PER_MARKET:
            row["Alert Approved"] = "NO"
            row["Exposure Notes"] = f"blocked: max {market} alerts reached"
            continue
        if category_counts.get(category, 0) >= BOT_MAX_ALERTS_PER_CATEGORY:
            row["Alert Approved"] = "NO"
            row["Exposure Notes"] = f"blocked: max {category} exposure reached"
            continue

        row["Alert Approved"] = "YES"
        row["Exposure Notes"] = "approved"
        approved.append(row)
        market_counts[market] = market_counts.get(market, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

    return approved


def determine_signal_outcome(row, current_price=None):
    if not BOT_OUTCOME_TRACKING_ENABLED:
        return "TRACKING DISABLED"
    signal = str(row.get("AI Signal", row.get("Signal", "")))
    price = safe_float(current_price if current_price is not None else row.get("Price", row.get("Current Price", 0)), 0)
    stop = safe_float(row.get("Stop Loss", 0), 0)
    tp1 = safe_float(row.get("Take Profit 1", 0), 0)
    tp2 = safe_float(row.get("Take Profit 2", 0), 0)
    if not is_directional_signal(signal) or price <= 0:
        return "NO TRADE"
    if "BUY" in signal:
        if stop > 0 and price <= stop:
            return "STOP HIT"
        if tp2 > 0 and price >= tp2:
            return "TP2 HIT"
        if tp1 > 0 and price >= tp1:
            return "TP1 HIT"
    if "SELL" in signal:
        if stop > 0 and price >= stop:
            return "STOP HIT"
        if tp2 > 0 and price <= tp2:
            return "TP2 HIT"
        if tp1 > 0 and price <= tp1:
            return "TP1 HIT"
    return "OPEN"


def build_dashboard_analytics_rows(scanned_rows, backtest_results=None):
    rows = []
    scanned_rows = scanned_rows or []
    backtest_results = backtest_results or []
    directional = [row for row in scanned_rows if is_directional_signal(row.get("AI Signal", ""))]
    ranked_directional = sorted(
        directional,
        key=lambda item: safe_float(item.get("Signal Quality Score", item.get("AI Confidence %", 0)), 0),
        reverse=True
    )
    approved = [row for row in directional if str(row.get("Alert Approved", "")).upper() == "YES"]
    blocked = [row for row in directional if str(row.get("Alert Approved", "")).upper() == "NO"]

    rows.append(["Last Updated", now_text()])
    rows.append(["Tickers Scanned", len(scanned_rows)])
    rows.append(["Directional Signals", len(directional)])
    rows.append(["Approved Alerts", len(approved)])
    rows.append(["Blocked / Filtered Alerts", len(blocked)])
    rows.append(["Average Confidence", round(sum(safe_float(row.get("AI Confidence %", 0)) for row in scanned_rows) / len(scanned_rows), 2) if scanned_rows else 0])
    rows.append(["Average Risk/Reward", round(sum(safe_float(row.get("Risk/Reward 2", 0)) for row in directional) / len(directional), 2) if directional else 0])
    rows.append(["Top Ranked Signal", ranked_directional[0].get("Ticker", "None") if ranked_directional else "None"])
    rows.append(["Top Ranked Setup", f"{ranked_directional[0].get('Ticker', 'None')} {ranked_directional[0].get('AI Signal', '')} | QS {ranked_directional[0].get('Signal Quality Score', 0)} | R/R {ranked_directional[0].get('Risk/Reward 2', 0)}" if ranked_directional else "None"])
    rows.append(["Weakest Current Setup", f"{ranked_directional[-1].get('Ticker', 'None')} {ranked_directional[-1].get('AI Signal', '')} | QS {ranked_directional[-1].get('Signal Quality Score', 0)}" if ranked_directional else "None"])
    rows.append(["Risk-On Count", len([row for row in scanned_rows if row.get("Risk Mode") == "Risk-On"])])
    rows.append(["Risk-Off Count", len([row for row in scanned_rows if row.get("Risk Mode") == "Risk-Off"])])
    rows.append(["Sideways / Neutral Count", len([row for row in scanned_rows if str(row.get("Advanced Market Regime", "")).lower().startswith("sideways") or row.get("Risk Mode") == "Neutral"])])

    valid = [r for r in backtest_results if r.get("Signals Tested", 0) > 0]
    rows.append(["Backtests With Signals", len(valid)])
    if valid:
        best = sorted(valid, key=lambda r: (safe_float(r.get("Profit Factor", 0)), safe_float(r.get("Win Rate %", 0)), safe_float(r.get("Average Return %", 0))), reverse=True)[0]
        worst = sorted(valid, key=lambda r: (safe_float(r.get("Profit Factor", 0)), safe_float(r.get("Win Rate %", 0)), safe_float(r.get("Average Return %", 0))))[0]
        rows.append(["Average Backtest Win Rate", round(sum(safe_float(r.get("Win Rate %", 0)) for r in valid) / len(valid), 2)])
        rows.append(["Average Backtest Profit Factor", round(sum(safe_float(r.get("Profit Factor", 0)) for r in valid) / len(valid), 2)])
        rows.append(["Average Backtest Drawdown", round(sum(safe_float(r.get("Max Drawdown %", 0)) for r in valid) / len(valid), 2)])
        rows.append(["Worst Backtest Drawdown", max(safe_float(r.get("Max Drawdown %", 0)) for r in valid)])
        rows.append(["Best Backtest Ticker", f"{best.get('Ticker', '')} | WR {best.get('Win Rate %', 0)}% | PF {best.get('Profit Factor', 0)} | DD {best.get('Max Drawdown %', 0)}%"] )
        rows.append(["Worst Backtest Ticker", f"{worst.get('Ticker', '')} | WR {worst.get('Win Rate %', 0)}% | PF {worst.get('Profit Factor', 0)} | DD {worst.get('Max Drawdown %', 0)}%"] )
        rows.append(["Best Setup By Backtest", f"{best.get('Ticker', '')} | Avg Return {best.get('Average Return %', 0)}% | Expectancy {best.get('Expectancy %', 0)}%"] )
        rows.append(["Worst Setup By Backtest", f"{worst.get('Ticker', '')} | Avg Return {worst.get('Average Return %', 0)}% | Expectancy {worst.get('Expectancy %', 0)}%"] )
    else:
        rows.append(["Average Backtest Win Rate", 0])
        rows.append(["Average Backtest Profit Factor", 0])
        rows.append(["Average Backtest Drawdown", 0])
        rows.append(["Worst Backtest Drawdown", 0])
        rows.append(["Best Backtest Ticker", "None"])
        rows.append(["Worst Backtest Ticker", "None"])
        rows.append(["Best Setup By Backtest", "None"])
        rows.append(["Worst Setup By Backtest", "None"])

    confidence_report = build_dynamic_confidence_report(backtest_results)
    rows.append(["Dynamic Confidence Source", confidence_report.get("source", "none")])
    rows.append(["Current Minimum Confidence", MIN_CONFIDENCE])
    rows.append(["Recommended Minimum Confidence", confidence_report.get("recommended_min_confidence", MIN_CONFIDENCE)])
    rows.append(["Dynamic Confidence Recommendation", confidence_report.get("recommendation", "N/A")])
    rows.append(["Best Confidence Bucket", confidence_report.get("best_bucket", "N/A")])
    rows.append(["Confidence Bucket Summary", confidence_report.get("bucket_summary", "No confidence data")])
    setup_report = build_setup_performance_report() if BOT_SETUP_ANALYTICS_ENABLED else {}
    rows.append(["Best Setup", setup_report.get("best_setup", "N/A")])
    rows.append(["Worst Setup", setup_report.get("worst_setup", "N/A")])
    rows.append(["Setup Recommendation", setup_report.get("recommendation", "N/A")])
    strategy_report = build_strategy_ranking_report() if BOT_STRATEGY_RANKING_ENABLED else {}
    rows.append(["Best Strategy", strategy_report.get("best_strategy", "N/A")])
    rows.append(["Weakest Strategy", strategy_report.get("weak_strategy", "N/A")])
    rows.append(["Strategy Ranking Recommendation", strategy_report.get("recommendation", "N/A")])
    return rows

def sync_dashboard_analytics_to_google_sheets(scanned_rows, backtest_results=None):
    if not GOOGLE_SHEETS_ENABLED or not BOT_DASHBOARD_ANALYTICS_ENABLED:
        return False
    spreadsheet = get_google_spreadsheet()
    if spreadsheet is None:
        return False
    try:
        worksheet = get_or_create_worksheet(spreadsheet, "Dashboard Analytics", DASHBOARD_ANALYTICS_HEADERS)
        rows = build_dashboard_analytics_rows(scanned_rows, backtest_results)
        worksheet.clear()
        safe_sheet_update(worksheet, "A1", [DASHBOARD_ANALYTICS_HEADERS] + rows)
        return True
    except Exception as error:
        log(f"Dashboard analytics sync error: {error}")
        return False


def calculate_walk_forward_summary(results):
    if not BOT_WALK_FORWARD_ENABLED or not results:
        return {
            "Walk Forward Windows": 0,
            "Walk Forward Passed": "NO DATA",
            "Walk Forward Notes": "no backtest results",
        }
    valid = [result for result in results if result.get("Signals Tested", 0) > 0]
    if not valid:
        return {
            "Walk Forward Windows": 0,
            "Walk Forward Passed": "NO DATA",
            "Walk Forward Notes": "no qualifying historical signals",
        }
    avg_win_rate = sum(safe_float(r.get("Win Rate %", 0)) for r in valid) / len(valid)
    avg_pf = sum(safe_float(r.get("Profit Factor", 0)) for r in valid) / len(valid)
    wf_rates = [safe_float(r.get("Walk Forward Pass Rate %", 0)) for r in valid if r.get("Walk Forward Windows", 0)]
    avg_wf_rate = sum(wf_rates) / len(wf_rates) if wf_rates else 0
    passed = avg_win_rate >= 50 and avg_pf >= 1 and (avg_wf_rate >= 50 if wf_rates else True)
    return {
        "Walk Forward Windows": BOT_WALK_FORWARD_WINDOWS,
        "Walk Forward Passed": "YES" if passed else "NO",
        "Walk Forward Notes": f"avg WR {round(avg_win_rate, 2)}% | avg PF {round(avg_pf, 2)} | avg WF pass {round(avg_wf_rate, 2)}% across {len(valid)} ticker(s)",
    }



# ======================================================
# v32.9 DYNAMIC CONFIDENCE OPTIMIZATION HELPERS
# ======================================================

def confidence_bucket_floor(confidence):
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0
    confidence = max(0, min(confidence, 100))
    bucket = int(confidence // BOT_DYNAMIC_CONFIDENCE_BUCKET_SIZE) * BOT_DYNAMIC_CONFIDENCE_BUCKET_SIZE
    return min(bucket, 100)


def confidence_bucket_label(confidence):
    floor = confidence_bucket_floor(confidence)
    if floor >= 100:
        return "100%"
    upper = min(100, floor + BOT_DYNAMIC_CONFIDENCE_BUCKET_SIZE - 1)
    return f"{floor}-{upper}%"


def summarize_returns_for_confidence_bucket(values):
    returns = [safe_float(value, 0) for value in values or []]
    if not returns:
        return {"trades": 0, "wr": 0, "pf": 0, "avg_return": 0}
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 0)
    wr = round((len(wins) / len(returns)) * 100, 2) if returns else 0
    avg_return = round(sum(returns) / len(returns), 2) if returns else 0
    return {"trades": len(returns), "wr": wr, "pf": pf, "avg_return": avg_return}


def summarize_confidence_bucket_returns(bucket_returns):
    rows = []
    for bucket_floor, returns in sorted((bucket_returns or {}).items()):
        stats = summarize_returns_for_confidence_bucket(returns)
        rows.append({
            "bucket_floor": int(bucket_floor),
            "bucket": confidence_bucket_label(bucket_floor),
            **stats,
        })
    return rows


def choose_dynamic_confidence_recommendation(bucket_rows):
    if not BOT_DYNAMIC_CONFIDENCE_ENABLED:
        return {
            "recommended_min_confidence": MIN_CONFIDENCE,
            "recommendation": "Dynamic confidence optimization disabled.",
            "best_bucket": "N/A",
            "bucket_summary": "Disabled",
        }

    reliable = [
        row for row in (bucket_rows or [])
        if int(row.get("trades", 0)) >= BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE
    ]
    if not reliable:
        total = sum(int(row.get("trades", 0)) for row in (bucket_rows or []))
        return {
            "recommended_min_confidence": MIN_CONFIDENCE,
            "recommendation": f"WAIT: need {BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE}+ trades per confidence bucket before changing threshold. Current bucketed trades: {total}.",
            "best_bucket": "Needs more data",
            "bucket_summary": compact_text(format_confidence_bucket_summary(bucket_rows), 900),
        }

    strong = [
        row for row in reliable
        if safe_float(row.get("pf", 0), 0) >= BOT_DYNAMIC_CONFIDENCE_TARGET_PF
        and safe_float(row.get("wr", 0), 0) >= BOT_DYNAMIC_CONFIDENCE_TARGET_WR
    ]
    if strong:
        best = sorted(strong, key=lambda row: (safe_float(row.get("pf", 0), 0), safe_float(row.get("wr", 0), 0), safe_float(row.get("avg_return", 0), 0)), reverse=True)[0]
        recommended = max(BOT_DYNAMIC_CONFIDENCE_MIN_RECOMMENDED, min(float(best.get("bucket_floor", MIN_CONFIDENCE)), BOT_DYNAMIC_CONFIDENCE_MAX_RECOMMENDED))
        action = "KEEP" if recommended <= MIN_CONFIDENCE else "RAISE"
        return {
            "recommended_min_confidence": round(recommended, 2),
            "recommendation": f"{action}: recommended minimum confidence {round(recommended, 2)} based on strongest reliable bucket {best.get('bucket')} | PF {best.get('pf')} | WR {best.get('wr')}%.",
            "best_bucket": f"{best.get('bucket')} | PF {best.get('pf')} | WR {best.get('wr')}% | Trades {best.get('trades')}",
            "bucket_summary": compact_text(format_confidence_bucket_summary(bucket_rows), 900),
        }

    best = sorted(reliable, key=lambda row: (safe_float(row.get("pf", 0), 0), safe_float(row.get("wr", 0), 0), safe_float(row.get("avg_return", 0), 0)), reverse=True)[0]
    recommended = max(MIN_CONFIDENCE, min(float(best.get("bucket_floor", MIN_CONFIDENCE)), BOT_DYNAMIC_CONFIDENCE_MAX_RECOMMENDED))
    return {
        "recommended_min_confidence": round(recommended, 2),
        "recommendation": f"CAUTION: no bucket met PF {BOT_DYNAMIC_CONFIDENCE_TARGET_PF} and WR {BOT_DYNAMIC_CONFIDENCE_TARGET_WR}%. Best available bucket is {best.get('bucket')} | PF {best.get('pf')} | WR {best.get('wr')}%. Keep collecting data before enforcing.",
        "best_bucket": f"{best.get('bucket')} | PF {best.get('pf')} | WR {best.get('wr')}% | Trades {best.get('trades')}",
        "bucket_summary": compact_text(format_confidence_bucket_summary(bucket_rows), 900),
    }


def format_confidence_bucket_summary(bucket_rows):
    if not bucket_rows:
        return "No confidence bucket data yet."
    lines = []
    for row in sorted(bucket_rows, key=lambda item: int(item.get("bucket_floor", 0))):
        lines.append(
            f"{row.get('bucket')}: {row.get('trades', 0)} trades | WR {row.get('wr', 0)}% | PF {row.get('pf', 0)} | Avg {row.get('avg_return', 0)}%"
        )
    return "\n".join(lines)


def build_paper_confidence_bucket_rows():
    if not BOT_DYNAMIC_CONFIDENCE_USE_PAPER:
        return []
    try:
        df = load_paper_trades_df()
        if df is None or df.empty:
            return []
        closed = df[df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])].copy()
        if closed.empty or "confidence" not in closed.columns:
            return []
        bucket_returns = {}
        for _, trade in closed.iterrows():
            bucket = confidence_bucket_floor(trade.get("confidence", 0))
            pnl_pct = safe_float(trade.get("pnl_percent", 0), 0)
            bucket_returns.setdefault(bucket, []).append(pnl_pct)
        return summarize_confidence_bucket_returns(bucket_returns)
    except Exception as error:
        log(f"Paper confidence optimization error: {error}")
        return []


def merge_confidence_bucket_rows(rows_list):
    merged_returns = {}
    for rows in rows_list or []:
        for row in rows or []:
            # Reconstruct weighted pseudo-returns from aggregate stats without inventing wins/losses.
            # This merge is used for display/recommendation only; paper rows are preferred when available.
            bucket = int(row.get("bucket_floor", 0))
            trades = int(row.get("trades", 0) or 0)
            avg_return = safe_float(row.get("avg_return", 0), 0)
            if trades <= 0:
                continue
            merged_returns.setdefault(bucket, []).extend([avg_return] * trades)
    return summarize_confidence_bucket_returns(merged_returns)


def build_dynamic_confidence_report(backtest_results=None):
    paper_rows = build_paper_confidence_bucket_rows()
    backtest_rows = []
    if BOT_DYNAMIC_CONFIDENCE_USE_BACKTEST:
        for result in backtest_results or []:
            for row in result.get("Confidence Bucket Rows", []) or []:
                backtest_rows.append(row)

    source = "paper" if paper_rows else "backtest" if backtest_rows else "none"
    rows = paper_rows if paper_rows else merge_confidence_bucket_rows([backtest_rows]) if backtest_rows else []
    recommendation = choose_dynamic_confidence_recommendation(rows)
    return {
        "source": source,
        "bucket_rows": rows,
        **recommendation,
    }


def log_dynamic_confidence_report(backtest_results=None):
    report = build_dynamic_confidence_report(backtest_results)
    if not BOT_DYNAMIC_CONFIDENCE_ENABLED:
        return report
    log(f"Dynamic confidence optimization source: {report.get('source')}")
    log(f"Dynamic confidence recommendation: {report.get('recommendation')}")
    return report


# ======================================================
# v32.10 SETUP PERFORMANCE ANALYTICS
# ======================================================

def setup_tag_enabled(row, column, threshold=70):
    return safe_float(row.get(column, 0), 0) >= threshold


def build_setup_profile(row):
    try:
        signal = str(row.get("AI Signal", ""))
        direction = "Long" if "BUY" in signal else "Short" if "SELL" in signal else "Neutral"
        tags = []
        if setup_tag_enabled(row, "MTF Confidence"):
            tags.append("MTF")
        if setup_tag_enabled(row, "Volume Confidence"):
            tags.append("Volume")
        if setup_tag_enabled(row, "Market Confidence"):
            tags.append("Market")
        if setup_tag_enabled(row, "S/R Confidence"):
            tags.append("S/R")
        if setup_tag_enabled(row, "Risk/Reward Confidence"):
            tags.append("R/R")
        if setup_tag_enabled(row, "News Confidence"):
            tags.append("News")
        if not tags:
            tags = ["Base"]
        rr = safe_float(row.get("Risk/Reward 2", 0), 0)
        confidence = safe_float(row.get("AI Confidence %", 0), 0)
        quality = safe_float(row.get("Signal Quality Score", 0), 0)
        setup_score = round(min(100, max(0, confidence * 0.55 + quality * 0.30 + min(rr, 3) * 5)), 2)
        setup_name = f"{direction}: " + "+".join(tags[:4])
        if len(tags) > 4:
            setup_name += f" +{len(tags) - 4}"
        return {"setup_name": setup_name, "setup_tags": ", ".join(tags), "setup_score": setup_score}
    except Exception as error:
        log(f"Setup profile build error: {error}")
        return {"setup_name": "Unknown Setup", "setup_tags": "Unknown", "setup_score": 0}


def calculate_setup_performance_rows(df=None):
    if not BOT_SETUP_ANALYTICS_ENABLED:
        return []
    try:
        df = load_paper_trades_df() if df is None else df
        if df is None or df.empty or "setup_name" not in df.columns:
            return []
        closed = df[df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])].copy()
        if closed.empty:
            return []
        rows = []
        for setup_name, group in closed.groupby(closed["setup_name"].fillna("Unknown Setup").astype(str)):
            pnl = pd.to_numeric(group.get("pnl_dollars", 0), errors="coerce").fillna(0)
            pnl_pct = pd.to_numeric(group.get("pnl_percent", 0), errors="coerce").fillna(0)
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            trades = int(len(group))
            gross_wins = float(wins.sum())
            gross_losses = abs(float(losses.sum()))
            pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 0)
            wr = round((len(wins) / trades) * 100, 2) if trades else 0
            tags_blob = ", ".join(group.get("setup_tags", pd.Series(dtype=str)).fillna("").astype(str).tolist())
            tags = ", ".join(sorted({item.strip() for item in tags_blob.split(",") if item.strip()})) or "N/A"
            status = "STRONG" if trades >= BOT_SETUP_ANALYTICS_MIN_SAMPLE and pf >= BOT_SETUP_ANALYTICS_STRONG_PF and wr >= BOT_SETUP_ANALYTICS_STRONG_WR else "WATCH" if trades >= BOT_SETUP_ANALYTICS_MIN_SAMPLE else "COLLECTING"
            rows.append({
                "setup_name": setup_name or "Unknown Setup",
                "setup_tags": tags,
                "trades": trades,
                "wins": int(len(wins)),
                "losses": int(len(losses)),
                "wr": wr,
                "pf": pf,
                "avg_return": round(float(pnl_pct.mean()), 2) if trades else 0,
                "total_pnl": round(float(pnl.sum()), 2),
                "status": status,
            })
        return sorted(rows, key=lambda row: (safe_float(row.get("pf", 0), 0), safe_float(row.get("wr", 0), 0), safe_float(row.get("total_pnl", 0), 0)), reverse=True)
    except Exception as error:
        log(f"Setup performance calculation error: {error}")
        return []


def build_setup_performance_report(df=None):
    rows = calculate_setup_performance_rows(df)
    if not rows:
        return {"rows": [], "best_setup": "N/A", "worst_setup": "N/A", "recommendation": "Collect more closed paper trades before ranking setups."}
    reliable = [row for row in rows if int(row.get("trades", 0)) >= BOT_SETUP_ANALYTICS_MIN_SAMPLE]
    source_rows = reliable if reliable else rows
    best = source_rows[0]
    worst = sorted(source_rows, key=lambda row: (safe_float(row.get("pf", 0), 0), safe_float(row.get("wr", 0), 0), safe_float(row.get("total_pnl", 0), 0)))[0]
    strong = [row for row in reliable if row.get("status") == "STRONG"]
    if strong:
        recommendation = f"Prioritize {strong[0].get('setup_name')} when it appears. PF {strong[0].get('pf')} | WR {strong[0].get('wr')}%."
    elif reliable:
        recommendation = f"No setup has met PF {BOT_SETUP_ANALYTICS_STRONG_PF} and WR {BOT_SETUP_ANALYTICS_STRONG_WR}% yet. Best reliable setup: {best.get('setup_name')}."
    else:
        recommendation = f"Collect more data. Minimum sample is {BOT_SETUP_ANALYTICS_MIN_SAMPLE} closed trades per setup."
    return {
        "rows": rows,
        "best_setup": f"{best.get('setup_name')} | PF {best.get('pf')} | WR {best.get('wr')}% | Trades {best.get('trades')}",
        "worst_setup": f"{worst.get('setup_name')} | PF {worst.get('pf')} | WR {worst.get('wr')}% | Trades {worst.get('trades')}",
        "recommendation": recommendation,
    }


def log_setup_performance_report():
    report = build_setup_performance_report()
    if BOT_SETUP_ANALYTICS_ENABLED:
        log(f"Setup performance analytics: {report.get('recommendation')}")
        if report.get("best_setup") != "N/A":
            log(f"Best setup: {report.get('best_setup')}")
            log(f"Worst setup: {report.get('worst_setup')}")
    return report


# ======================================================
# v32.11 STRATEGY RANKING ENGINE
# ======================================================

def calculate_strategy_score(row):
    """Score a setup/strategy using performance, sample size, and realized P/L."""
    try:
        trades = int(row.get("trades", 0) or 0)
        wr = safe_float(row.get("wr", 0), 0)
        pf = safe_float(row.get("pf", 0), 0)
        avg_return = safe_float(row.get("avg_return", 0), 0)
        total_pnl = safe_float(row.get("total_pnl", 0), 0)

        sample_score = min(25, (trades / max(1, BOT_STRATEGY_RANKING_MIN_SAMPLE)) * 25)
        wr_score = min(25, max(0, wr / 100 * 25))
        pf_score = min(30, max(0, pf / max(0.01, BOT_STRATEGY_RANKING_STRONG_PF) * 30))
        return_score = min(10, max(0, (avg_return + 5) / 10 * 10))
        pnl_score = 10 if total_pnl > 0 else 0

        return round(min(100, sample_score + wr_score + pf_score + return_score + pnl_score), 2)
    except Exception:
        return 0


def classify_strategy_row(row):
    trades = int(row.get("trades", 0) or 0)
    pf = safe_float(row.get("pf", 0), 0)
    wr = safe_float(row.get("wr", 0), 0)

    if trades < BOT_STRATEGY_RANKING_MIN_SAMPLE:
        return "COLLECTING"
    if pf <= BOT_STRATEGY_RANKING_WEAK_PF or wr <= BOT_STRATEGY_RANKING_WEAK_WR:
        return "WEAK"
    if pf >= BOT_STRATEGY_RANKING_STRONG_PF and wr >= BOT_STRATEGY_RANKING_STRONG_WR:
        return "STRONG"
    return "NEUTRAL"


def strategy_action_from_label(label):
    if label == "STRONG":
        return "Favor this setup when normal filters agree."
    if label == "WEAK":
        return "Do not automate; block new paper trades if enforcement is enabled."
    if label == "NEUTRAL":
        return "Allow but do not prioritize."
    return "Collect more closed trades before enforcing."


def calculate_strategy_ranking_rows(df=None):
    if not BOT_STRATEGY_RANKING_ENABLED:
        return []

    setup_rows = calculate_setup_performance_rows(df)
    ranked = []

    for row in setup_rows:
        label = classify_strategy_row(row)
        score = calculate_strategy_score(row)
        ranked.append({
            **row,
            "strategy_label": label,
            "strategy_score": score,
            "do_not_automate": label == "WEAK",
            "recommended_action": strategy_action_from_label(label),
        })

    ranked = sorted(
        ranked,
        key=lambda item: (
            safe_float(item.get("strategy_score", 0), 0),
            safe_float(item.get("pf", 0), 0),
            safe_float(item.get("wr", 0), 0),
            safe_float(item.get("total_pnl", 0), 0),
        ),
        reverse=True
    )

    for index, row in enumerate(ranked, start=1):
        row["strategy_rank"] = index

    return ranked


def build_strategy_ranking_report(df=None):
    rows = calculate_strategy_ranking_rows(df)
    if not BOT_STRATEGY_RANKING_ENABLED:
        return {"rows": [], "recommendation": "Strategy ranking disabled.", "best_strategy": "N/A", "weak_strategy": "N/A"}

    if not rows:
        return {
            "rows": [],
            "recommendation": "Collect more setup-tagged closed paper trades before ranking strategies.",
            "best_strategy": "N/A",
            "weak_strategy": "N/A",
        }

    strong = [row for row in rows if row.get("strategy_label") == "STRONG"]
    weak = [row for row in rows if row.get("strategy_label") == "WEAK"]
    best = strong[0] if strong else rows[0]
    weakest = sorted(rows, key=lambda item: (
        safe_float(item.get("pf", 0), 0),
        safe_float(item.get("wr", 0), 0),
        safe_float(item.get("total_pnl", 0), 0),
    ))[0]

    if strong:
        recommendation = f"Prioritize strategy '{best.get('setup_name')}' for future automation testing. PF {best.get('pf')} | WR {best.get('wr')}%."
    elif weak:
        recommendation = f"Do not automate weak setup '{weak[0].get('setup_name')}'. PF {weak[0].get('pf')} | WR {weak[0].get('wr')}%."
    else:
        recommendation = "No strategy has earned STRONG status yet. Keep collecting paper trades."

    return {
        "rows": rows,
        "recommendation": recommendation,
        "best_strategy": f"{best.get('setup_name')} | {best.get('strategy_label')} | Score {best.get('strategy_score')} | PF {best.get('pf')} | WR {best.get('wr')}% | Trades {best.get('trades')}",
        "weak_strategy": f"{weakest.get('setup_name')} | {weakest.get('strategy_label')} | Score {weakest.get('strategy_score')} | PF {weakest.get('pf')} | WR {weakest.get('wr')}% | Trades {weakest.get('trades')}",
    }


def log_strategy_ranking_report():
    report = build_strategy_ranking_report()
    if BOT_STRATEGY_RANKING_ENABLED:
        log(f"Strategy ranking engine: {report.get('recommendation')}")
        if report.get("best_strategy") != "N/A":
            log(f"Best strategy: {report.get('best_strategy')}")
            log(f"Weakest strategy: {report.get('weak_strategy')}")
    return report


def strategy_ranking_check(row):
    """
    Enforce v32.11 strategy-level learning.

    This checks the setup generated by the current signal against historical closed
    paper trades for the same setup_name. It only blocks when enough setup-level
    data exists and the strategy is labeled WEAK.
    """
    if not BOT_STRATEGY_RANKING_ENABLED:
        return True, "strategy ranking disabled"

    try:
        setup = build_setup_profile(row)
        setup_name = str(setup.get("setup_name", "Unknown Setup"))
        ranking_rows = calculate_strategy_ranking_rows()
        match = next((item for item in ranking_rows if str(item.get("setup_name", "")) == setup_name), None)

        if not match:
            return True, f"strategy collecting: {setup_name}"

        label = str(match.get("strategy_label", "COLLECTING"))
        trades = int(match.get("trades", 0) or 0)
        pf = safe_float(match.get("pf", 0), 0)
        wr = safe_float(match.get("wr", 0), 0)
        score = safe_float(match.get("strategy_score", 0), 0)

        note = f"strategy {label}: {setup_name} | trades {trades} | PF {pf} | WR {wr}% | score {score}"

        if label == "WEAK" and BOT_STRATEGY_RANKING_BLOCK_WEAK_SETUPS:
            return False, "blocked: " + note

        return True, note if BOT_STRATEGY_RANKING_INCLUDE_NOTES else f"strategy {label}"
    except Exception as error:
        log(f"Strategy ranking check error: {error}")
        return True, "strategy ranking unavailable; allowed"


# ======================================================
# v32 PAPER TRADE TRACKING HELPERS
# ======================================================

def load_paper_trades_df():
    try:
        ensure_data_dir()
        if os.path.exists(PAPER_TRADES_FILE) and os.path.getsize(PAPER_TRADES_FILE) > 0:
            df = pd.read_csv(PAPER_TRADES_FILE)
        else:
            df = pd.DataFrame(columns=PAPER_TRADE_HEADERS)
        for column in PAPER_TRADE_HEADERS:
            if column not in df.columns:
                if column in ["tp1_notified", "tp2_notified", "stop_notified", "closed_notified"]:
                    df[column] = False
                else:
                    df[column] = ""
        return normalize_paper_trade_dtypes(df[PAPER_TRADE_HEADERS])
    except Exception as error:
        log(f"Paper trades load error: {error}")
        return pd.DataFrame(columns=PAPER_TRADE_HEADERS)


def save_paper_trades_df(df):
    try:
        ensure_data_dir()
        for column in PAPER_TRADE_HEADERS:
            if column not in df.columns:
                df[column] = ""
        df = normalize_paper_trade_dtypes(df[PAPER_TRADE_HEADERS])
        temp_path = f"{PAPER_TRADES_FILE}.tmp"
        df[PAPER_TRADE_HEADERS].to_csv(temp_path, index=False)
        os.replace(temp_path, PAPER_TRADES_FILE)
        return True
    except Exception as error:
        log(f"Paper trades save error: {error}")
        return False


def paper_trade_id(row):
    raw = "|".join([
        str(row.get("Ticker", "")),
        str(row.get("AI Signal", "")),
        str(row.get("Trade Entry", row.get("Price", ""))),
        now_dt().strftime("%Y-%m-%d"),
    ])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def paper_bool(value):
    return str(value).strip().lower() in ["1", "true", "yes", "y", "on"]


def has_open_paper_trade(df, ticker, signal):
    if df.empty:
        return False
    open_df = df[
        (df["ticker"].astype(str) == str(ticker))
        & (df["signal"].astype(str) == str(signal))
        & (df["status"].astype(str).isin(["OPEN", "TP1_HIT"]))
    ]
    return len(open_df) >= BOT_PAPER_TRADE_MAX_OPEN_PER_TICKER


def count_open_paper_trades(df):
    if df is None or df.empty or "status" not in df.columns:
        return 0
    return len(df[df["status"].astype(str).isin(["OPEN", "TP1_HIT"])])


def calculate_closed_paper_trade_performance(df, ticker):
    """Return closed paper-trade stats for one ticker using realized P/L."""
    empty = {"trades": 0, "wr": 0, "pf": 0, "total_pnl": 0}
    try:
        if df is None or df.empty or not ticker:
            return empty
        if "ticker" not in df.columns or "status" not in df.columns:
            return empty
        closed_statuses = ["TP2_HIT", "STOPPED", "CLOSED"]
        clean = df.copy()
        ticker = str(ticker).upper().strip()
        closed = clean[
            (clean["ticker"].astype(str).str.upper().str.strip() == ticker)
            & (clean["status"].astype(str).isin(closed_statuses))
        ].copy()
        if closed.empty:
            return empty
        pnl = pd.to_numeric(closed.get("pnl_dollars", 0), errors="coerce").fillna(0)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_wins = float(wins.sum())
        gross_losses = abs(float(losses.sum()))
        profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 0)
        win_rate = round((len(wins) / len(closed)) * 100, 2) if len(closed) else 0
        return {
            "trades": int(len(closed)),
            "wr": win_rate,
            "pf": profit_factor,
            "total_pnl": round(float(pnl.sum()), 2),
        }
    except Exception as error:
        log(f"Adaptive filter performance calculation error for {ticker}: {error}")
        return empty


def adaptive_filter_check(row):
    """
    Enforce v32.8 adaptive filters.

    v32.8.1 behavior:
    - Use real closed paper-trade performance once enough closed trades exist.
    - Before enough paper trades exist, optionally bootstrap from backtest stats so
      obvious weak tickers can be blocked immediately instead of waiting weeks.
    """
    if not BOT_ADAPTIVE_FILTERS_ENABLED:
        return True, "adaptive filters disabled"

    ticker = str(row.get("Ticker", "")).upper().strip()
    if not ticker:
        return True, "adaptive filter skipped: missing ticker"

    df = load_paper_trades_df()
    stats = calculate_closed_paper_trade_performance(df, ticker)
    trades = int(stats.get("trades", 0))
    wr = safe_float(stats.get("wr", 0), 0)
    pf = safe_float(stats.get("pf", 0), 0)

    source = "paper"
    sample_text = f"{trades} closed"

    if trades < BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES:
        if not BOT_ADAPTIVE_USE_BACKTEST_BOOTSTRAP:
            return True, f"adaptive: needs more data ({trades}/{BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES} closed)"

        # Prefer the backtest fields already attached to the row. If they are
        # missing, run the same backtest-quality lookup used by the paper gate.
        bt_pf = safe_float(row.get("Backtest Quality PF", 0), 0)
        bt_wr = safe_float(row.get("Backtest Quality WR", 0), 0)
        bt_signals = int(safe_float(row.get("Backtest Quality Signals", 0), 0))

        if bt_signals <= 0:
            quality = backtest_quality_for_ticker(ticker)
            bt_pf = safe_float(quality.get("pf", bt_pf), bt_pf)
            bt_wr = safe_float(quality.get("wr", bt_wr), bt_wr)
            bt_signals = int(safe_float(quality.get("signals", bt_signals), bt_signals))

        if bt_signals < BOT_ADAPTIVE_BOOTSTRAP_MIN_BACKTEST_SIGNALS:
            return True, (
                f"adaptive bootstrap: not enough backtest data "
                f"({bt_signals}/{BOT_ADAPTIVE_BOOTSTRAP_MIN_BACKTEST_SIGNALS} signals); "
                f"paper sample {trades}/{BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES}"
            )

        pf = bt_pf
        wr = bt_wr
        source = "backtest bootstrap"
        sample_text = f"{bt_signals} backtest signals | {trades}/{BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES} paper closed"

    if pf <= BOT_ADAPTIVE_AVOID_MAX_PF or wr <= BOT_ADAPTIVE_AVOID_MAX_WR:
        note = f"adaptive weak ticker ({source}): {ticker} | {sample_text} | PF {pf} | WR {wr}%"
        if BOT_ADAPTIVE_BLOCK_WEAK_TICKERS:
            return False, "blocked: " + note
        return True, "warning: " + note

    if pf >= BOT_ADAPTIVE_FAVORITE_MIN_PF and wr >= BOT_ADAPTIVE_FAVORITE_MIN_WR:
        if BOT_ADAPTIVE_INCLUDE_FAVORITE_NOTE:
            return True, f"adaptive favorite ({source}): {ticker} | {sample_text} | PF {pf} | WR {wr}%"
        return True, f"adaptive favorite ({source})"

    return True, f"adaptive neutral ({source}): {ticker} | {sample_text} | PF {pf} | WR {wr}%"


def paper_trade_quality_check(row):
    if not BOT_PAPER_TRADE_QUALITY_FILTER_ENABLED:
        return True, "paper trade quality filter disabled"

    ticker = str(row.get("Ticker", "")).upper().strip()
    if ticker in BOT_PAPER_TRADE_AVOID_TICKERS:
        return False, f"blocked: {ticker} is on BOT_PAPER_TRADE_AVOID_TICKERS"

    adaptive_ok, adaptive_note = adaptive_filter_check(row)
    if not adaptive_ok:
        return False, adaptive_note

    strategy_ok, strategy_note = strategy_ranking_check(row)
    if not strategy_ok:
        return False, strategy_note

    pf = safe_float(row.get("Backtest Quality PF", 0), 0)
    wr = safe_float(row.get("Backtest Quality WR", 0), 0)
    signals = int(safe_float(row.get("Backtest Quality Signals", 0), 0))

    # If the row was created before backtest-quality fields were added, do a
    # best-effort check here rather than opening a low-quality paper trade.
    if signals <= 0 and ticker:
        quality = backtest_quality_for_ticker(ticker)
        pf = safe_float(quality.get("pf", pf), pf)
        wr = safe_float(quality.get("wr", wr), wr)
        signals = int(safe_float(quality.get("signals", signals), signals))

    if signals < BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS:
        return False, f"blocked: paper trade low backtest sample ({signals})"
    if pf < BOT_PAPER_TRADE_MIN_BACKTEST_PF:
        return False, f"blocked: paper trade PF {pf} below {BOT_PAPER_TRADE_MIN_BACKTEST_PF}"
    if wr < BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE:
        return False, f"blocked: paper trade WR {wr}% below {BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE}%"

    return True, f"paper trade quality passed PF {pf} WR {wr}% signals {signals} | {adaptive_note} | {strategy_note}"


def create_paper_trade_from_signal(row):
    if not BOT_PAPER_TRADING_ENABLED or not is_directional_signal(row.get("AI Signal", "")):
        return False
    try:
        df = load_paper_trades_df()
        ticker = str(row.get("Ticker", ""))
        signal = str(row.get("AI Signal", ""))
        if count_open_paper_trades(df) >= BOT_PAPER_TRADE_MAX_OPEN_TOTAL:
            log(f"Paper trade not opened for {ticker}: max total open paper trades reached ({BOT_PAPER_TRADE_MAX_OPEN_TOTAL}).")
            return False
        if has_open_paper_trade(df, ticker, signal):
            log(f"Paper trade not opened for {ticker}: open {signal} trade already exists.")
            return False
        quality_ok, quality_note = paper_trade_quality_check(row)
        if not quality_ok:
            log(f"Paper trade not opened for {ticker}: {quality_note}")
            return False
        entry = safe_float(row.get("Trade Entry", row.get("Price", 0)), 0)
        if entry <= 0:
            return False
        trade = {
            "trade_id": paper_trade_id(row),
            "ticker": ticker,
            "market": row.get("Market", ""),
            "signal": signal,
            "entry_price": entry,
            "current_price": safe_float(row.get("Price", entry), entry),
            "stop_loss": safe_float(row.get("Stop Loss", 0), 0),
            "tp1": safe_float(row.get("Take Profit 1", 0), 0),
            "tp2": safe_float(row.get("Take Profit 2", 0), 0),
            "confidence": safe_float(row.get("AI Confidence %", 0), 0),
            "position_size": safe_float(row.get("Position Size", 0), 0),
            "position_value": safe_float(row.get("Position Value", 0), 0),
            "status": "OPEN",
            "result": "OPEN",
            "date_opened": now_text(),
            "date_closed": "",
            "last_updated": now_text(),
            "pnl_percent": 0,
            "pnl_dollars": 0,
            "risk_reward_2": safe_float(row.get("Risk/Reward 2", 0), 0),
            "signal_rank": row.get("Signal Rank", ""),
            "quality_score": safe_float(row.get("Signal Quality Score", 0), 0),
            **build_setup_profile(row),
            "notes": compact_text(f"{row.get('Exposure Notes', '')} | {quality_note}", 500),
            "tp1_notified": False,
            "tp2_notified": False,
            "stop_notified": False,
            "closed_notified": False,
        }
        df = pd.concat([df, pd.DataFrame([trade])], ignore_index=True)
        saved = save_paper_trades_df(df)
        if saved:
            send_paper_trade_event(trade, "opened")
        return saved
    except Exception as error:
        log(f"Create paper trade error: {error}")
        return False


def paper_trade_pnl(signal, entry, current, position_size):
    entry = safe_float(entry, 0)
    current = safe_float(current, 0)
    position_size = safe_float(position_size, 0)
    if entry <= 0 or current <= 0:
        return 0, 0
    if "SELL" in str(signal):
        pnl_percent = ((entry - current) / entry) * 100
    else:
        pnl_percent = ((current - entry) / entry) * 100
    pnl_dollars = (current - entry) * position_size
    if "SELL" in str(signal):
        pnl_dollars = (entry - current) * position_size
    return round(pnl_percent, 2), round(pnl_dollars, 2)


def classify_paper_trade_status(trade, current_price):
    signal = str(trade.get("signal", ""))
    stop = safe_float(trade.get("stop_loss", 0), 0)
    tp1 = safe_float(trade.get("tp1", 0), 0)
    tp2 = safe_float(trade.get("tp2", 0), 0)
    current = safe_float(current_price, 0)
    if current <= 0:
        return str(trade.get("status", "OPEN")), str(trade.get("result", "OPEN"))
    if "SELL" in signal:
        if stop > 0 and current >= stop:
            return "STOPPED", "LOSS"
        if tp2 > 0 and current <= tp2:
            return "TP2_HIT", "WIN"
        if tp1 > 0 and current <= tp1:
            return "TP1_HIT", "PARTIAL WIN"
    else:
        if stop > 0 and current <= stop:
            return "STOPPED", "LOSS"
        if tp2 > 0 and current >= tp2:
            return "TP2_HIT", "WIN"
        if tp1 > 0 and current >= tp1:
            return "TP1_HIT", "PARTIAL WIN"
    return str(trade.get("status", "OPEN")) if str(trade.get("status", "OPEN")) == "TP1_HIT" else "OPEN", "OPEN"


def send_paper_trade_event(trade, event_type):
    ticker = str(trade.get("ticker", ""))
    webhook_url = get_paper_trade_webhook(ticker)
    titles = {
        "opened": "📈 PAPER TRADE OPENED",
        "tp1": "🎯 TP1 HIT",
        "tp2": "🚀 TP2 HIT",
        "stop": "🛑 STOP LOSS HIT",
        "closed": "🏁 TRADE CLOSED",
    }
    colors = {"opened": 3447003, "tp1": 15844367, "tp2": 5763719, "stop": 15548997, "closed": 10181046}
    fields = [
        {"name": "Ticker / Signal", "value": f"{ticker} | {trade.get('signal', '')}", "inline": True},
        {"name": "Status", "value": str(trade.get("status", "")), "inline": True},
        {"name": "Confidence", "value": f"{trade.get('confidence', 0)}%", "inline": True},
        {"name": "Entry", "value": format_money(trade.get("entry_price", 0)), "inline": True},
        {"name": "Current", "value": format_money(trade.get("current_price", 0)), "inline": True},
        {"name": "P/L", "value": f"{trade.get('pnl_percent', 0)}% / {format_money(trade.get('pnl_dollars', 0))}", "inline": True},
        {"name": "Plan", "value": f"SL {format_money(trade.get('stop_loss', 0))} | TP1 {format_money(trade.get('tp1', 0))} | TP2 {format_money(trade.get('tp2', 0))}", "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    return send_discord_embed(webhook_url, titles.get(event_type, "📌 PAPER TRADE UPDATE"), colors.get(event_type, 3447003), fields)


def monitor_open_paper_trades():
    if not BOT_PAPER_TRADING_ENABLED or not BOT_PAPER_TRADE_MONITOR_ENABLED:
        return {"checked": 0, "updated": 0, "closed": 0}
    df = load_paper_trades_df()
    if df.empty:
        return {"checked": 0, "updated": 0, "closed": 0}
    checked = updated = closed = 0
    for index, trade in df.iterrows():
        if str(trade.get("status", "")) not in ["OPEN", "TP1_HIT"]:
            continue
        ticker = str(trade.get("ticker", ""))
        data = get_price_data(ticker, "5d", "1d")
        if data.empty:
            continue
        current = float(data["Close"].iloc[-1])
        checked += 1
        status, result = classify_paper_trade_status(trade, current)
        pnl_percent, pnl_dollars = paper_trade_pnl(trade.get("signal", ""), trade.get("entry_price", 0), current, trade.get("position_size", 0))
        df.at[index, "current_price"] = round(current, 4)
        df.at[index, "pnl_percent"] = pnl_percent
        df.at[index, "pnl_dollars"] = pnl_dollars
        df.at[index, "last_updated"] = now_text()
        old_status = str(trade.get("status", "OPEN"))
        if status != old_status:
            updated += 1
            df.at[index, "status"] = status
            df.at[index, "result"] = result
            event_trade = df.loc[index].to_dict()
            if status == "TP1_HIT" and not paper_bool(trade.get("tp1_notified", False)):
                send_paper_trade_event(event_trade, "tp1")
                df.at[index, "tp1_notified"] = True
            elif status == "TP2_HIT" and not paper_bool(trade.get("tp2_notified", False)):
                send_paper_trade_event(event_trade, "tp2")
                df.at[index, "tp2_notified"] = True
                df.at[index, "date_closed"] = now_text()
                closed += 1
            elif status == "STOPPED" and not paper_bool(trade.get("stop_notified", False)):
                send_paper_trade_event(event_trade, "stop")
                df.at[index, "stop_notified"] = True
                df.at[index, "date_closed"] = now_text()
                closed += 1
    save_paper_trades_df(df)
    update_paper_equity_curve(df)
    return {"checked": checked, "updated": updated, "closed": closed}


def update_paper_equity_curve(df=None):
    try:
        df = load_paper_trades_df() if df is None else df
        realized = 0
        if not df.empty:
            closed_df = df[df["status"].astype(str).isin(["TP2_HIT", "STOPPED", "CLOSED"])]
            realized = pd.to_numeric(closed_df["pnl_dollars"], errors="coerce").fillna(0).sum()
        equity = round(BOT_PAPER_TRADE_STARTING_EQUITY + realized, 2)
        row = {"timestamp": now_text(), "equity": equity, "realized_pnl": round(realized, 2)}
        if os.path.exists(PAPER_EQUITY_FILE) and os.path.getsize(PAPER_EQUITY_FILE) > 0:
            eq = pd.read_csv(PAPER_EQUITY_FILE)
            eq = pd.concat([eq, pd.DataFrame([row])], ignore_index=True)
        else:
            eq = pd.DataFrame([row])
        eq = eq.tail(LOG_MAX_ITEMS)
        temp_path = f"{PAPER_EQUITY_FILE}.tmp"
        eq.to_csv(temp_path, index=False)
        os.replace(temp_path, PAPER_EQUITY_FILE)
        return True
    except Exception as error:
        log(f"Paper equity update error: {error}")
        return False


def get_paper_trade_summary_key():
    current_bucket = int(time.time() // (BOT_PAPER_TRADE_SUMMARY_INTERVAL_HOURS * 3600))
    return f"paper_trade_summary_{current_bucket}"


def paper_trade_summary_already_sent():
    return get_paper_trade_summary_key() in load_log(PAPER_TRADE_SUMMARY_LOG_FILE)


def mark_paper_trade_summary_sent():
    items = load_log(PAPER_TRADE_SUMMARY_LOG_FILE)
    items.add(get_paper_trade_summary_key())
    save_log(PAPER_TRADE_SUMMARY_LOG_FILE, items)


def calculate_paper_trade_metrics(df, market=None):
    if df is None or df.empty:
        return {
            "open": 0, "closed": 0, "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0, "best_ticker": "N/A", "worst_ticker": "N/A",
            "tp1_open": 0,
        }
    working = df.copy()
    if market and "market" in working.columns:
        working = working[working["market"].astype(str) == str(market)]
    if working.empty:
        return {
            "open": 0, "closed": 0, "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0, "best_ticker": "N/A", "worst_ticker": "N/A",
            "tp1_open": 0,
        }
    status = working.get("status", pd.Series(dtype=str)).astype(str)
    open_df = working[status.isin(["OPEN", "TP1_HIT"])]
    closed_df = working[status.isin(["TP2_HIT", "STOPPED", "CLOSED"])]
    pnl = pd.to_numeric(closed_df.get("pnl_dollars", 0), errors="coerce").fillna(0) if not closed_df.empty else pd.Series(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_wins = wins.sum()
    gross_losses = abs(losses.sum())
    by_ticker = closed_df.assign(_pnl=pnl).groupby("ticker")["_pnl"].sum() if not closed_df.empty and "ticker" in closed_df.columns else pd.Series(dtype=float)
    return {
        "open": len(open_df),
        "closed": len(closed_df),
        "win_rate": round((len(wins) / len(closed_df)) * 100, 2) if len(closed_df) else 0,
        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else (round(gross_wins, 2) if gross_wins > 0 else 0),
        "total_pnl": round(pnl.sum(), 2) if len(pnl) else 0,
        "best_ticker": by_ticker.idxmax() if not by_ticker.empty else "N/A",
        "worst_ticker": by_ticker.idxmin() if not by_ticker.empty else "N/A",
        "tp1_open": len(open_df[open_df["status"].astype(str) == "TP1_HIT"]) if not open_df.empty and "status" in open_df.columns else 0,
    }


def send_paper_trade_summary_if_due():
    if not BOT_SEND_PAPER_TRADE_SUMMARY:
        return False
    if paper_trade_summary_already_sent():
        return False
    df = load_paper_trades_df()
    overall = calculate_paper_trade_metrics(df)
    if overall["open"] == 0 and overall["closed"] == 0:
        return False

    sent_any = False
    for market, sample_ticker in [("Crypto", "BTC-USD"), ("Stock", "AAPL")]:
        metrics = calculate_paper_trade_metrics(df, market)
        if metrics["open"] == 0 and metrics["closed"] == 0:
            continue
        fields = [
            {"name": "Open Trades", "value": f"{metrics['open']} open | {metrics['tp1_open']} at TP1", "inline": True},
            {"name": "Closed Trades", "value": str(metrics["closed"]), "inline": True},
            {"name": "Win Rate", "value": f"{metrics['win_rate']}%", "inline": True},
            {"name": "Profit Factor", "value": str(metrics["profit_factor"]), "inline": True},
            {"name": "Total P/L", "value": format_money(metrics["total_pnl"]), "inline": True},
            {"name": "Best / Worst", "value": f"Best: {metrics['best_ticker']} | Worst: {metrics['worst_ticker']}", "inline": False},
            {"name": "Quality Gate", "value": f"Max open total {BOT_PAPER_TRADE_MAX_OPEN_TOTAL} | Min PF {BOT_PAPER_TRADE_MIN_BACKTEST_PF} | Min WR {BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE}% | Avoid: {', '.join(BOT_PAPER_TRADE_AVOID_TICKERS) if BOT_PAPER_TRADE_AVOID_TICKERS else 'None'}", "inline": False},
            {"name": "Time", "value": now_text(), "inline": False},
        ]
        sent = send_discord_embed(
            get_paper_trade_webhook(sample_ticker),
            f"📊 {market} Paper Trade Summary",
            10181046,
            fields,
        )
        sent_any = sent_any or sent
        interruptible_sleep(1)
    if sent_any:
        mark_paper_trade_summary_sent()
    return sent_any


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
    """Return True only when a breaking-news keyword appears as a real word/phrase.

    This uses the same safe boundary matching as the v27 sentiment engine so words
    like "sector" do not accidentally trigger the "SEC" breaking-news keyword.
    """
    return any(keyword_matches_text(keyword, title) for keyword in BREAKING_KEYWORDS)


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
# NEWS SENTIMENT WEIGHTING
# ======================================================

def keyword_matches_text(keyword, text):
    """
    Match news keywords safely.

    The first v27 draft used simple substring checks. That worked, but it could
    misread words like "sector" as "SEC" or "disapproval" as "approval".
    This helper keeps phrase matching simple while requiring word boundaries.
    """
    try:
        keyword = str(keyword or "").strip().lower()
        text = str(text or "").lower()
        if not keyword or not text:
            return False

        escaped = re.escape(keyword)
        escaped = escaped.replace("\\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None
    except Exception:
        return False


def score_headline_sentiment(title):
    text = str(title or "").lower()
    bullish_hits = [
        keyword for keyword in BULLISH_NEWS_KEYWORDS
        if keyword_matches_text(keyword, text)
    ]
    bearish_hits = [
        keyword for keyword in BEARISH_NEWS_KEYWORDS
        if keyword_matches_text(keyword, text)
    ]

    # Context cleanup: some words are bullish in product/business headlines but
    # should not offset legal/regulatory bearish headlines.
    # Example: "SEC launches investigation" should be bearish, not partly bullish
    # because of the word "launches".
    if bearish_hits and "launches" in bullish_hits:
        bullish_hits = [keyword for keyword in bullish_hits if keyword != "launches"]

    # Approval can appear inside bearish/uncertain phrases.
    # Example: "FDA rejects approval" or "SEC delays ETF approval".
    negative_approval_context = (
        keyword_matches_text("rejects", text)
        or keyword_matches_text("rejected", text)
        or keyword_matches_text("denies", text)
        or keyword_matches_text("denied", text)
        or keyword_matches_text("delays", text)
        or keyword_matches_text("delayed", text)
    )
    if negative_approval_context:
        bullish_hits = [
            keyword for keyword in bullish_hits
            if keyword not in ["approval", "approved", "etf approved"]
        ]
        if not any(keyword in bearish_hits for keyword in ["warning", "investigation"]):
            bearish_hits.append("negative approval context")

    raw_score = len(bullish_hits) - len(bearish_hits)

    if raw_score > 0:
        label = "Bullish"
    elif raw_score < 0:
        label = "Bearish"
    else:
        label = "Neutral"

    strength = min(5, max(1, abs(raw_score))) if raw_score else 0

    return {
        "label": label,
        "score": raw_score,
        "strength": strength,
        "bullish_hits": bullish_hits[:5],
        "bearish_hits": bearish_hits[:5],
    }


def summarize_news_sentiment(items):
    if not BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED:
        return {
            "news_sentiment_label": "Disabled",
            "news_sentiment_score": 0,
            "news_strength": 0,
            "news_score_adj": 0,
            "news_headlines": "None",
            "news_notes": "news sentiment weighting disabled",
        }

    if not items:
        return {
            "news_sentiment_label": "Unavailable",
            "news_sentiment_score": 0,
            "news_strength": 0,
            "news_score_adj": 0,
            "news_headlines": "None",
            "news_notes": "no usable recent headlines",
        }

    total_score = 0
    total_strength = 0
    selected_titles = []

    for item in items[:BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER]:
        title = item.get("title", "")
        result = score_headline_sentiment(title)
        total_score += result["score"]
        total_strength += result["strength"]
        if title:
            selected_titles.append(str(title)[:120])

    if total_score > 0:
        label = "Bullish"
    elif total_score < 0:
        label = "Bearish"
    else:
        label = "Neutral"

    capped_score = max(-5, min(total_score, 5))
    if capped_score == 0:
        adjustment = 0
    else:
        adjustment = round((capped_score / 5) * BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT)

    return {
        "news_sentiment_label": label,
        "news_sentiment_score": int(total_score),
        "news_strength": int(total_strength),
        "news_score_adj": int(adjustment),
        "news_headlines": " | ".join(selected_titles[:2]) if selected_titles else "None",
        "news_notes": f"{label} sentiment from {len(items[:BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER])} headline(s)",
    }


def build_news_sentiment_contexts(scan_started_at=None):
    default = summarize_news_sentiment([])

    if not BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED:
        return {ticker: default for ticker in ALL_TICKERS}

    contexts = {ticker: default for ticker in ALL_TICKERS}

    try:
        market_items = {"Crypto": [], "Stock": []}

        if BOT_NEWS_SENTIMENT_USE_MARKET_NEWS:
            if NEWSAPI_KEY:
                market_items["Crypto"] = fetch_newsapi_news("Crypto")[:BOT_NEWS_SENTIMENT_MAX_MARKET_ITEMS]
                market_items["Stock"] = fetch_newsapi_news("Stock")[:BOT_NEWS_SENTIMENT_MAX_MARKET_ITEMS]

        for ticker in ALL_TICKERS:
            if SHUTDOWN_REQUESTED:
                break

            if scan_started_at is not None:
                elapsed = time.time() - scan_started_at
                remaining = BOT_MAX_SCAN_SECONDS - elapsed
                if remaining <= BOT_NEWS_SENTIMENT_TIME_GUARD_SECONDS:
                    contexts[ticker] = {
                        **default,
                        "news_notes": "news sentiment skipped by time guard",
                    }
                    continue

            market = get_asset_type(ticker)
            items = []

            if market == "Stock" and FINNHUB_API_KEY:
                items.extend(fetch_finnhub_company_news(ticker))

            if BOT_NEWS_YFINANCE_ENABLED:
                items.extend(fetch_yfinance_news_for_ticker(ticker, market))

            if BOT_NEWS_SENTIMENT_USE_MARKET_NEWS:
                items.extend(market_items.get(market, []))

            contexts[ticker] = summarize_news_sentiment(items)

            if FINNHUB_NEWS_DELAY_SECONDS > 0 and market == "Stock" and FINNHUB_API_KEY:
                interruptible_sleep(FINNHUB_NEWS_DELAY_SECONDS)

        return contexts

    except Exception as error:
        log(f"News sentiment context build error: {error}")
        return contexts


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
        return False

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
        {"name": "Routing", "value": "Startup/status messages route to System Check only.", "inline": False},
        {"name": "Scan Interval", "value": f"{SCAN_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "Minimum Confidence", "value": f"{MIN_CONFIDENCE}%", "inline": True},
        {"name": "Multi-Timeframe", "value": "On" if BOT_MULTI_TIMEFRAME_ENABLED else "Off", "inline": True},
        {"name": "MTF Frames", "value": f"{BOT_PRIMARY_TIMEFRAME_LABEL} / {BOT_SHORT_TIMEFRAME_INTERVAL.upper()} / {BOT_MOMENTUM_TIMEFRAME_INTERVAL.upper()} / {BOT_HIGHER_TIMEFRAME_LABEL}", "inline": True},
        {"name": "Volume Spike Detection", "value": "On" if BOT_VOLUME_SPIKE_ENABLED else "Off", "inline": True},
        {"name": "Market Trend Filter", "value": "On" if BOT_MARKET_TREND_FILTER_ENABLED else "Off", "inline": True},
        {"name": "Confidence Engine", "value": "On" if BOT_CONFIDENCE_ENGINE_ENABLED else "Legacy", "inline": True},
        {"name": "Support/Resistance", "value": "On" if BOT_SUPPORT_RESISTANCE_ENABLED else "Off", "inline": True},
        {"name": "Trade Management", "value": "On" if BOT_TRADE_MANAGEMENT_ENABLED else "Off", "inline": True},
        {"name": "Advanced Backtesting", "value": "On" if BOT_BACKTESTING_ENABLED else "Off", "inline": True},
        {"name": "Phase 3 Risk Suite", "value": f"Regime {'On' if BOT_MARKET_REGIME_DETECTION_ENABLED else 'Off'} | Ranking {'On' if BOT_SIGNAL_RANKING_ENABLED else 'Off'} | Sizing {'On' if BOT_POSITION_SIZING_ENABLED else 'Off'} | Trailing {'On' if BOT_TRAILING_STOP_ENABLED else 'Off'} | Exposure {'On' if BOT_EXPOSURE_CONTROLS_ENABLED else 'Off'} | WalkFwd {'On' if BOT_WALK_FORWARD_ENABLED else 'Off'} | Outcomes {'On' if BOT_OUTCOME_TRACKING_ENABLED else 'Off'} | Analytics {'On' if BOT_DASHBOARD_ANALYTICS_ENABLED else 'Off'}", "inline": False},
        {"name": "Discord Terminal", "value": f"Elite Alerts {'On' if BOT_DISCORD_ELITE_ALERTS_ENABLED else 'Off'} | Top Signals {'On' if BOT_SEND_TOP_SIGNALS_SUMMARY else 'Off'} | Daily Report {'On' if BOT_SEND_DAILY_PERFORMANCE_REPORT else 'Off'} | Backtest Scorecard {'On' if BOT_SEND_BACKTEST_SCORECARD else 'Off'}", "inline": False},
        {"name": "Paper Trade Routing", "value": f"Crypto {'Dedicated' if CRYPTO_PAPER_TRADE_WEBHOOK_URL != CRYPTO_TRADE_WEBHOOK_URL else 'Trade fallback'} | Stock {'Dedicated' if STOCK_PAPER_TRADE_WEBHOOK_URL != STOCK_TRADE_WEBHOOK_URL else 'Trade fallback'}", "inline": False},
        {"name": "Paper Trade Quality Gate", "value": f"Max Open {BOT_PAPER_TRADE_MAX_OPEN_TOTAL} | Min PF {BOT_PAPER_TRADE_MIN_BACKTEST_PF} | Min WR {BOT_PAPER_TRADE_MIN_BACKTEST_WIN_RATE}% | Min Signals {BOT_PAPER_TRADE_MIN_BACKTEST_SIGNALS} | Avoid {', '.join(BOT_PAPER_TRADE_AVOID_TICKERS) if BOT_PAPER_TRADE_AVOID_TICKERS else 'None'}", "inline": False},
        {"name": "Dynamic Confidence", "value": f"{'On' if BOT_DYNAMIC_CONFIDENCE_ENABLED else 'Off'} | Current Min {MIN_CONFIDENCE}% | Target PF {BOT_DYNAMIC_CONFIDENCE_TARGET_PF} | Target WR {BOT_DYNAMIC_CONFIDENCE_TARGET_WR}% | Mode Recommendation-Only", "inline": False},
        {"name": "Setup Analytics", "value": f"{'On' if BOT_SETUP_ANALYTICS_ENABLED else 'Off'} | Min Sample {BOT_SETUP_ANALYTICS_MIN_SAMPLE} | Strong PF {BOT_SETUP_ANALYTICS_STRONG_PF} | Strong WR {BOT_SETUP_ANALYTICS_STRONG_WR}% | Mode Recommendation-Only", "inline": False},
        {"name": "Strategy Ranking", "value": f"{'On' if BOT_STRATEGY_RANKING_ENABLED else 'Off'} | Min Sample {BOT_STRATEGY_RANKING_MIN_SAMPLE} | Strong PF {BOT_STRATEGY_RANKING_STRONG_PF} | Weak PF {BOT_STRATEGY_RANKING_WEAK_PF} | Block Weak {'On' if BOT_STRATEGY_RANKING_BLOCK_WEAK_SETUPS else 'Off'}", "inline": False},
        {"name": "News Sentiment Weighting", "value": "On" if BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED else "Off", "inline": True},
        {"name": "Summaries", "value": "On" if SEND_SUMMARIES else "Off", "inline": True},
        {"name": "News", "value": "On" if SEND_NEWS else "Off", "inline": True},
        {"name": "News Interval", "value": f"{NEWS_INTERVAL_HOURS} hours", "inline": True},
        {"name": "Breaking News", "value": "On" if SEND_BREAKING_NEWS else "Off", "inline": True},
        {"name": "Breaking Interval", "value": f"{BREAKING_NEWS_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "News Sources", "value": ", ".join(enabled_sources) if enabled_sources else "None configured", "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]

    webhook_url = get_heartbeat_webhook()

    if not webhook_url:
        log("Startup message skipped: no heartbeat/error/trade webhook available.")
        return False

    return send_discord_embed(
        webhook_url,
        "🤖 AI Trading Bot Started",
        3447003,
        fields
    )

def send_signal_alert(row):
    if not BOT_DISCORD_ELITE_ALERTS_ENABLED:
        fields = [
            {"name": "Ticker", "value": str(row["Ticker"]), "inline": True},
            {"name": "Market", "value": str(row["Market"]), "inline": True},
            {"name": "Price", "value": f"${row['Price']}", "inline": True},
            {"name": "Signal", "value": str(row["AI Signal"]), "inline": True},
            {"name": "Confidence", "value": f"{row['AI Confidence %']}%", "inline": True},
            {"name": "Time", "value": now_text(), "inline": False},
        ]
        sent = send_discord_embed(get_trade_webhook(row["Ticker"]), f"{row['Market']} Market | {row['AI Signal']}", signal_embed_color(row["AI Signal"]), fields)
        if sent:
            create_paper_trade_from_signal(row)
        return sent

    signal = str(row.get("AI Signal", ""))
    title = f"{signal_emoji(signal)} {signal} | {row.get('Ticker', '')} | Rank #{row.get('Signal Rank', 'N/A')}"
    trade_plan = (
        f"Entry: {format_money(row.get('Trade Entry'))}\n"
        f"Stop: {format_money(row.get('Stop Loss'))}\n"
        f"TP1: {format_money(row.get('Take Profit 1'))}\n"
        f"TP2: {format_money(row.get('Take Profit 2'))}\n"
        f"Trailing Stop: {format_money(row.get('Trailing Stop'))}\n"
        f"Risk/Reward: {row.get('Risk/Reward 2', 'N/A')}:1"
    )
    position_text = (
        f"Account: {format_money(row.get('Account Size'))}\n"
        f"Risk: {row.get('Risk %', 'N/A')}% / {format_money(row.get('Risk Dollars'))}\n"
        f"Qty: {row.get('Position Size', 'N/A')}\n"
        f"Position Value: {format_money(row.get('Position Value'))}"
    )
    market_text = (
        f"Regime: {row.get('Advanced Market Regime', row.get('Market Regime', 'N/A'))}\n"
        f"Risk Mode: {row.get('Risk Mode', 'N/A')}\n"
        f"Market Adj: {row.get('Market Trend Adj', 0)}\n"
        f"Anchors: {compact_text(row.get('Market Anchors', 'N/A'), 180)}"
    )
    technical_text = (
        f"RSI: {row.get('RSI', 'N/A')} | MACD: {row.get('MACD', 'N/A')}\n"
        f"Daily: {row.get('Daily Trend', 'N/A')} | 4H: {row.get('Short TF Trend', 'N/A')} | 1H: {row.get('Momentum TF Trend', 'N/A')} | Higher: {row.get('Higher TF Trend', 'N/A')}\n"
        f"Volume: {row.get('Volume Signal', 'N/A')} ({row.get('Relative Volume', 'N/A')}x)\n"
        f"S/R: {row.get('S/R Signal', 'N/A')} | S {row.get('Support Level', 'N/A')} / R {row.get('Resistance Level', 'N/A')}"
    )
    confidence_text = (
        f"Grade: {row.get('Confidence Grade', 'N/A')}\n"
        f"Quality Score: {row.get('Signal Quality Score', 0)}\n"
        f"RSI {row.get('RSI Confidence', 'N/A')}% | MACD {row.get('MACD Confidence', 'N/A')}% | Trend {row.get('Trend Confidence', 'N/A')}%\n"
        f"MTF {row.get('MTF Confidence', 'N/A')}% | Vol {row.get('Volume Confidence', 'N/A')}% | Market {row.get('Market Confidence', 'N/A')}% | S/R {row.get('S/R Confidence', 'N/A')}% | R/R {row.get('Risk/Reward Confidence', 'N/A')}%"
    )
    fields = [
        {"name": "Ticker / Market", "value": f"{row.get('Ticker', '')} | {row.get('Market', '')}", "inline": True},
        {"name": "Price", "value": format_money(row.get("Price")), "inline": True},
        {"name": "Confidence", "value": f"{row.get('AI Confidence %', 0)}%", "inline": True},
        {"name": "Market Regime", "value": market_text, "inline": False},
        {"name": "Trade Plan", "value": trade_plan, "inline": False},
        {"name": "Position Sizing", "value": position_text, "inline": False},
        {"name": "Why This Alert", "value": compact_text(build_signal_reason_text(row), 1000), "inline": False},
        {"name": "Technical Snapshot", "value": compact_text(technical_text, 1000), "inline": False},
        {"name": "Confidence Breakdown", "value": compact_text(confidence_text, 1000), "inline": False},
        {"name": "News Sentiment", "value": compact_text(f"{row.get('News Sentiment', 'N/A')} | Adj {row.get('News Score Adj', 0)} | {row.get('News Headlines', 'None')}", 1000), "inline": False},
        {"name": "Approval", "value": f"{row.get('Alert Approved', 'N/A')} | {row.get('Exposure Notes', 'N/A')}", "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    sent = send_discord_embed(get_trade_webhook(row["Ticker"]), title, signal_embed_color(row["AI Signal"]), fields)
    if sent:
        create_paper_trade_from_signal(row)
    return sent


def build_summary_fields(rows, market):
    market_rows = [row for row in rows if row["Market"] == market]

    if not market_rows:
        return []

    def short_trend_label(value):
        value = str(value or "N/A")
        if value == "Bullish":
            return "Bull"
        if value == "Bearish":
            return "Bear"
        if value == "Neutral":
            return "Neut"
        if value == "Unknown":
            return "Unk"
        return value[:8]

    def compact_number(value, digits=1):
        number = safe_float(value, 0)
        if number == 0:
            return "0"
        if abs(number) >= 100:
            return str(round(number))
        return str(round(number, digits))

    def rank_text(row):
        rank = row.get("Signal Rank", "-")
        return f"#{rank}" if str(rank).strip() else "#-"

    def compact_summary_line(row):
        confidence = compact_number(row.get("AI Confidence %", 0), 1)
        grade = row.get("Confidence Grade", "N/A")
        quality = compact_number(row.get("Signal Quality Score", 0), 1)
        rsi = compact_number(row.get("RSI", 0), 0)
        rvol = compact_number(row.get("Relative Volume", 0), 2)
        regime = str(row.get("Advanced Market Regime", row.get("Market Regime", "N/A"))).replace("/Constructive", "").replace("/Defensive", "")
        short_tf = short_trend_label(row.get("Short TF Trend", "N/A"))
        momentum_tf = short_trend_label(row.get("Momentum TF Trend", "N/A"))
        higher_tf = short_trend_label(row.get("Higher TF Trend", "N/A"))
        sr_signal = str(row.get("S/R Signal", "N/A")).replace("Near ", "Near ")
        news = str(row.get("News Sentiment", "N/A"))

        return (
            f"{rank_text(row)} {row.get('Ticker', '')} | {confidence}% {grade} | "
            f"QS {quality} | RSI {rsi} | RVOL {rvol}x | "
            f"{regime} | {sr_signal} | News {news} | MTF {short_tf}/{momentum_tf}/{higher_tf}"
        )

    buy_lines = []
    hold_lines = []
    sell_lines = []

    sorted_rows = sorted(
        market_rows,
        key=lambda item: safe_float(item.get("Signal Quality Score", item.get("AI Confidence %", 0)), 0),
        reverse=True
    )

    for row in sorted_rows:
        line = compact_summary_line(row)

        if "BUY" in row["AI Signal"]:
            buy_lines.append(line)
        elif "SELL" in row["AI Signal"]:
            sell_lines.append(line)
        else:
            hold_lines.append(line)

    def section_value(lines):
        if not lines:
            return "None"

        selected = lines[:SUMMARY_MAX_LINES_PER_SECTION]
        value = "\n".join(selected)

        if len(lines) > len(selected):
            value += f"\n+{len(lines) - len(selected)} more"

        return compact_text(value, 1000)

    fields = [
        {"name": "🟢 BUY SIGNALS", "value": section_value(buy_lines), "inline": False},
        {"name": "🟡 HOLD SIGNALS", "value": section_value(hold_lines), "inline": False},
        {"name": "🔴 SELL SIGNALS", "value": section_value(sell_lines), "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]

    return fields

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
# ADVANCED BACKTESTING ENGINE
# ======================================================

def get_backtest_key():
    current_bucket = int(time.time() // (BOT_BACKTEST_INTERVAL_HOURS * 3600))
    return f"backtest_{current_bucket}"


def backtest_already_sent():
    sent_backtests = load_log(BACKTEST_SCHEDULE_LOG_FILE)
    return get_backtest_key() in sent_backtests


def mark_backtest_sent():
    sent_backtests = load_log(BACKTEST_SCHEDULE_LOG_FILE)
    sent_backtests.add(get_backtest_key())
    save_log(BACKTEST_SCHEDULE_LOG_FILE, sent_backtests)


def calculate_backtest_signal_from_window(window_data):
    if window_data is None or window_data.empty or len(window_data) < BOT_BACKTEST_LOOKBACK_DAYS:
        return None

    try:
        scored_data = calculate_indicators(window_data.copy())
        latest = scored_data.iloc[-1]
        current_price = float(latest["Close"])
        rsi_value = safe_latest_value(latest, "RSI", 50)
        macd_value = safe_latest_value(latest, "MACD", 0)
        macd_signal_value = safe_latest_value(latest, "MACD Signal", 0)

        technical_score = calculate_technical_score_from_latest(latest, current_price)
        daily_trend = timeframe_trend_from_score(technical_score)

        short_trend = "Unavailable"
        higher_trend = "Unknown"
        mtf_adjustment = 0

        if BOT_MULTI_TIMEFRAME_ENABLED:
            higher_data = resample_to_higher_timeframe(scored_data)
            higher_frame = score_price_frame(higher_data)
            if higher_frame:
                higher_trend = higher_frame["trend"]

            mtf_adjustment, _ = calculate_mtf_adjustment(daily_trend, short_trend, higher_trend)

        volume_context = calculate_volume_context(scored_data, daily_trend)
        support_resistance_context = calculate_support_resistance_context(scored_data, daily_trend)
        market_context = unavailable_market_context("historical market context excluded from backtest")
        market_adjustment = 0
        news_sentiment_context = summarize_news_sentiment([])

        final_score = max(
            0,
            min(
                technical_score
                + mtf_adjustment
                + volume_context.get("volume_score_adj", 0)
                + support_resistance_context.get("support_resistance_score_adj", 0),
                120
            )
        )

        signal, legacy_confidence = calculate_signal_and_confidence(final_score)
        trade_context = calculate_trade_management_context(scored_data, signal, current_price, support_resistance_context)

        confidence_context = calculate_confidence_breakdown(
            final_score,
            daily_trend,
            mtf_adjustment,
            short_trend,
            higher_trend,
            volume_context,
            market_adjustment,
            market_context,
            support_resistance_context,
            news_sentiment_context,
            trade_context,
            rsi_value,
            macd_value,
            macd_signal_value
        )

        weighted_confidence = confidence_context.get("confidence_percent", legacy_confidence)
        backtest_confidence = max(bounded_percent(weighted_confidence), bounded_percent(legacy_confidence))

        return {
            "signal": signal,
            "confidence": backtest_confidence,
            "final_score": final_score,
            "daily_trend": daily_trend,
            "higher_trend": higher_trend,
            "trade_context": trade_context,
        }

    except Exception as error:
        log(f"Backtest window scoring error: {error}")
        return None


def calculate_backtest_exit(data, index, hold_days, signal, trade_context):
    entry_price = float(data["Close"].iloc[index])
    default_exit_index = min(index + hold_days, len(data) - 1)
    default_exit_price = float(data["Close"].iloc[default_exit_index])
    stop_loss = safe_float(trade_context.get("stop_loss", 0), 0)
    take_profit_1 = safe_float(trade_context.get("take_profit_1", 0), 0)
    take_profit_2 = safe_float(trade_context.get("take_profit_2", 0), 0)

    exit_price = default_exit_price
    exit_reason = "time exit"

    future = data.iloc[index + 1: default_exit_index + 1]

    for _, candle in future.iterrows():
        high = safe_float(candle.get("High", candle.get("Close", 0)), 0)
        low = safe_float(candle.get("Low", candle.get("Close", 0)), 0)
        close = safe_float(candle.get("Close", 0), 0)

        if "BUY" in signal:
            if stop_loss > 0 and low <= stop_loss:
                return stop_loss, "stop loss"
            if take_profit_2 > 0 and high >= take_profit_2:
                return take_profit_2, "take profit 2"
            if take_profit_1 > 0 and high >= take_profit_1:
                exit_price = take_profit_1
                exit_reason = "take profit 1"
        elif "SELL" in signal:
            if stop_loss > 0 and high >= stop_loss:
                return stop_loss, "stop loss"
            if take_profit_2 > 0 and low <= take_profit_2:
                return take_profit_2, "take profit 2"
            if take_profit_1 > 0 and low <= take_profit_1:
                exit_price = take_profit_1
                exit_reason = "take profit 1"

        if close > 0:
            default_exit_price = close

    return exit_price, exit_reason


def backtest_ticker(ticker):
    data = get_price_data(ticker, BOT_BACKTEST_PERIOD, "1d")

    default_result = {
        "Ticker": ticker,
        "Market": get_asset_type(ticker),
        "Signals Tested": 0,
        "Wins": 0,
        "Losses": 0,
        "Win Rate %": 0,
        "Average Return %": 0,
        "Best Return %": 0,
        "Worst Return %": 0,
        "Average Confidence %": 0,
        "Buy Signals": 0,
        "Sell Signals": 0,
        "Profit Factor": 0,
        "Max Drawdown %": 0,
        "Expectancy %": 0,
        "Average R/R": 0,
        "Hold Days": BOT_BACKTEST_HOLD_DAYS,
        "Period": BOT_BACKTEST_PERIOD,
        "Walk Forward Windows": BOT_WALK_FORWARD_WINDOWS,
        "Walk Forward Passed Windows": 0,
        "Walk Forward Pass Rate %": 0,
        "Walk Forward Notes": "not enough historical data",
        "Notes": "not enough historical data",
    }

    if data is None or data.empty:
        return default_result

    data = normalize_price_data(data)
    required_rows = BOT_BACKTEST_LOOKBACK_DAYS + BOT_BACKTEST_HOLD_DAYS + 5
    if len(data) < required_rows:
        return default_result

    trade_returns = []
    confidence_values = []
    rr_values = []
    buy_signals = 0
    sell_signals = 0
    equity = BOT_BACKTEST_INITIAL_EQUITY
    peak_equity = equity
    max_drawdown = 0
    returns_by_window = [[] for _ in range(BOT_WALK_FORWARD_WINDOWS)]
    confidence_bucket_returns = {}

    start_index = BOT_BACKTEST_LOOKBACK_DAYS
    end_index = len(data) - BOT_BACKTEST_HOLD_DAYS

    for index in range(start_index, end_index):
        window = data.iloc[: index + 1]
        signal_context = calculate_backtest_signal_from_window(window)

        if not signal_context:
            continue

        signal = signal_context["signal"]
        confidence = signal_context["confidence"]
        trade_context = signal_context.get("trade_context", {})

        if signal not in ["STRONG BUY", "BUY", "STRONG SELL", "SELL"]:
            continue

        if confidence < BOT_BACKTEST_MIN_CONFIDENCE:
            continue

        entry_price = float(data["Close"].iloc[index])
        if entry_price <= 0:
            continue

        if BOT_BACKTEST_INCLUDE_TRADE_MANAGEMENT:
            exit_price, exit_reason = calculate_backtest_exit(data, index, BOT_BACKTEST_HOLD_DAYS, signal, trade_context)
        else:
            exit_price = float(data["Close"].iloc[index + BOT_BACKTEST_HOLD_DAYS])
            exit_reason = "time exit"

        raw_return = ((exit_price - entry_price) / entry_price) * 100

        if "SELL" in signal:
            strategy_return = raw_return * -1
            sell_signals += 1
        else:
            strategy_return = raw_return
            buy_signals += 1

        risk_fraction = BOT_BACKTEST_RISK_PER_TRADE_PCT / 100
        equity *= (1 + (strategy_return / 100) * min(1, risk_fraction * 10))
        peak_equity = max(peak_equity, equity)
        drawdown = ((peak_equity - equity) / peak_equity) * 100 if peak_equity else 0
        max_drawdown = max(max_drawdown, drawdown)

        trade_returns.append(strategy_return)
        if BOT_WALK_FORWARD_ENABLED and BOT_WALK_FORWARD_WINDOWS > 0:
            window_span = max(1, end_index - start_index)
            window_index = min(BOT_WALK_FORWARD_WINDOWS - 1, int(((index - start_index) / window_span) * BOT_WALK_FORWARD_WINDOWS))
            returns_by_window[window_index].append(strategy_return)
        confidence_values.append(confidence)
        confidence_bucket_returns.setdefault(confidence_bucket_floor(confidence), []).append(strategy_return)
        rr_values.append(safe_float(trade_context.get("risk_reward_2", 0), 0))

    if not trade_returns:
        return {**default_result, "Notes": "no qualifying historical signals"}

    wins = [value for value in trade_returns if value > 0]
    losses = [value for value in trade_returns if value < 0]
    total = len(trade_returns)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss else round(gross_win, 2)
    wf_window_results = []
    for window_returns in returns_by_window:
        if not window_returns:
            continue
        window_win_rate = len([value for value in window_returns if value > 0]) / len(window_returns) * 100
        window_average = sum(window_returns) / len(window_returns)
        wf_window_results.append(window_win_rate >= 50 and window_average >= 0)
    wf_passed_windows = len([value for value in wf_window_results if value])
    wf_total_windows = len(wf_window_results)
    wf_pass_rate = round((wf_passed_windows / wf_total_windows) * 100, 2) if wf_total_windows else 0
    wf_notes = f"{wf_passed_windows}/{wf_total_windows} profitable windows" if wf_total_windows else "no walk-forward windows with trades"

    return {
        "Ticker": ticker,
        "Market": get_asset_type(ticker),
        "Signals Tested": total,
        "Wins": len(wins),
        "Losses": len(losses),
        "Win Rate %": round((len(wins) / total) * 100, 2) if total else 0,
        "Average Return %": round(sum(trade_returns) / total, 2) if total else 0,
        "Best Return %": round(max(trade_returns), 2),
        "Worst Return %": round(min(trade_returns), 2),
        "Average Confidence %": round(sum(confidence_values) / len(confidence_values), 2) if confidence_values else 0,
        "Buy Signals": buy_signals,
        "Sell Signals": sell_signals,
        "Profit Factor": profit_factor,
        "Max Drawdown %": round(max_drawdown, 2),
        "Expectancy %": round(sum(trade_returns) / total, 2) if total else 0,
        "Average R/R": round(sum(rr_values) / len(rr_values), 2) if rr_values else 0,
        "Hold Days": BOT_BACKTEST_HOLD_DAYS,
        "Period": BOT_BACKTEST_PERIOD,
        "Walk Forward Windows": wf_total_windows,
        "Walk Forward Passed Windows": wf_passed_windows,
        "Walk Forward Pass Rate %": wf_pass_rate,
        "Walk Forward Notes": wf_notes,
        "Confidence Bucket Rows": summarize_confidence_bucket_returns(confidence_bucket_returns),
        "Confidence Bucket Summary": format_confidence_bucket_summary(summarize_confidence_bucket_returns(confidence_bucket_returns)),
        "Dynamic Confidence Recommendation": choose_dynamic_confidence_recommendation(summarize_confidence_bucket_returns(confidence_bucket_returns)).get("recommendation", "N/A"),
        "Recommended Min Confidence": choose_dynamic_confidence_recommendation(summarize_confidence_bucket_returns(confidence_bucket_returns)).get("recommended_min_confidence", MIN_CONFIDENCE),
        "Notes": "OK" if total >= BOT_BACKTEST_MIN_SIGNALS else "low sample size",
    }


def row_from_backtest_result(result):
    return [
        now_text(),
        result.get("Ticker", ""),
        result.get("Market", ""),
        result.get("Signals Tested", 0),
        result.get("Wins", 0),
        result.get("Losses", 0),
        result.get("Win Rate %", 0),
        result.get("Average Return %", 0),
        result.get("Best Return %", 0),
        result.get("Worst Return %", 0),
        result.get("Average Confidence %", 0),
        result.get("Buy Signals", 0),
        result.get("Sell Signals", 0),
        result.get("Profit Factor", 0),
        result.get("Max Drawdown %", 0),
        result.get("Expectancy %", 0),
        result.get("Average R/R", 0),
        result.get("Hold Days", BOT_BACKTEST_HOLD_DAYS),
        result.get("Period", BOT_BACKTEST_PERIOD),
        result.get("Walk Forward Windows", 0),
        result.get("Walk Forward Passed Windows", 0),
        result.get("Walk Forward Pass Rate %", 0),
        result.get("Walk Forward Notes", ""),
        result.get("Notes", ""),
    ]


def sync_backtest_results_to_google_sheets(results):
    if not GOOGLE_SHEETS_ENABLED or not results:
        return False

    spreadsheet = get_google_spreadsheet()
    if spreadsheet is None:
        return False

    try:
        worksheet = get_or_create_worksheet(spreadsheet, "Backtesting Results", BACKTEST_RESULTS_HEADERS)
        rows = [row_from_backtest_result(result) for result in results]
        safe_append_rows(worksheet, rows)
        prune_worksheet_rows(worksheet, GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS)
        return True
    except Exception as error:
        log(f"Google Sheets backtest sync error: {error}")
        return False


def sync_walk_forward_to_google_sheets(results):
    if not GOOGLE_SHEETS_ENABLED or not BOT_WALK_FORWARD_ENABLED:
        return False
    spreadsheet = get_google_spreadsheet()
    if spreadsheet is None:
        return False
    try:
        summary = calculate_walk_forward_summary(results)
        worksheet = get_or_create_worksheet(spreadsheet, "Walk Forward", WALK_FORWARD_HEADERS)
        safe_append_rows(worksheet, [[
            now_text(),
            summary.get("Walk Forward Windows", 0),
            summary.get("Walk Forward Passed", "NO DATA"),
            summary.get("Walk Forward Notes", ""),
        ]])
        prune_worksheet_rows(worksheet, GOOGLE_SHEETS_MAX_SCAN_HISTORY_ROWS)
        return True
    except Exception as error:
        log(f"Walk-forward sync error: {error}")
        return False


def send_backtest_summary(results):
    if not results or not BOT_SEND_BACKTEST_SCORECARD:
        return False
    webhook_url = get_backtest_webhook()
    if not webhook_url:
        log("Backtest scorecard skipped: BACKTEST_WEBHOOK_URL or TOP_SIGNALS_WEBHOOK_URL is missing.")
        return False
    valid_results = [result for result in results if result.get("Signals Tested", 0) > 0]
    if not valid_results:
        fields = [
            {"name": "Status", "value": "No qualifying historical signals found at the current confidence threshold.", "inline": False},
            {"name": "Period", "value": f"{BOT_BACKTEST_PERIOD} | Hold {BOT_BACKTEST_HOLD_DAYS} days", "inline": True},
            {"name": "Time", "value": now_text(), "inline": False},
        ]
        return send_discord_embed(webhook_url, "📈 Backtest Scorecard", 10181046, fields)
    ranked = sorted(valid_results, key=lambda item: (item.get("Profit Factor", 0), item.get("Win Rate %", 0), item.get("Average Return %", 0)), reverse=True)
    weakest = sorted(valid_results, key=lambda item: (item.get("Profit Factor", 0), item.get("Win Rate %", 0), item.get("Average Return %", 0)))
    total_signals = sum(result.get("Signals Tested", 0) for result in valid_results)
    total_wins = sum(result.get("Wins", 0) for result in valid_results)
    overall_win_rate = round((total_wins / total_signals) * 100, 2) if total_signals else 0
    avg_return = round(sum(result.get("Average Return %", 0) for result in valid_results) / len(valid_results), 2)
    avg_drawdown = round(sum(result.get("Max Drawdown %", 0) for result in valid_results) / len(valid_results), 2)
    avg_pf = round(sum(safe_float(result.get("Profit Factor", 0), 0) for result in valid_results) / len(valid_results), 2)
    wf_summary = calculate_walk_forward_summary(results)
    fields = [
        {"name": "Overall", "value": f"{total_signals} signals | WR {overall_win_rate}% | Avg Return {avg_return}% | Avg PF {avg_pf} | Avg DD {avg_drawdown}%", "inline": False},
        {"name": "Walk Forward", "value": f"{wf_summary.get('Walk Forward Passed', 'NO DATA')} | {wf_summary.get('Walk Forward Notes', '')}", "inline": False},
        {"name": "Top Backtests", "value": compact_text("\n".join(build_backtest_quality_line(result) for result in ranked[:8]), 1000), "inline": False},
        {"name": "Weak / Avoid List", "value": compact_text("\n".join(build_backtest_quality_line(result) for result in weakest[:5]), 1000), "inline": False},
        {"name": "Settings", "value": f"Period {BOT_BACKTEST_PERIOD} | Hold {BOT_BACKTEST_HOLD_DAYS} days | Min Conf {BOT_BACKTEST_MIN_CONFIDENCE}%", "inline": False},
        {"name": "Time", "value": now_text(), "inline": False},
    ]
    return send_discord_embed(webhook_url, "📈 Backtest Scorecard", 10181046, fields)


def run_backtest_review():
    if not BOT_BACKTESTING_ENABLED:
        return []

    tickers = clean_ticker_list(ALL_TICKERS)[:BOT_BACKTEST_MAX_TICKERS]
    results = []

    for ticker in tickers:
        if SHUTDOWN_REQUESTED:
            break

        try:
            result = backtest_ticker(ticker)
            results.append(result)
            log(
                f"Backtest {ticker}: {result.get('Signals Tested', 0)} signals | "
                f"{result.get('Win Rate %', 0)}% WR | PF {result.get('Profit Factor', 0)}"
            )
        except Exception as error:
            log(f"Backtest error for {ticker}: {error}")

        if YFINANCE_TICKER_DELAY_SECONDS > 0:
            interruptible_sleep(YFINANCE_TICKER_DELAY_SECONDS)

    sync_backtest_results_to_google_sheets(results)
    sync_walk_forward_to_google_sheets(results)
    sync_dashboard_analytics_to_google_sheets([], results)
    send_backtest_summary(results)
    return results


def maybe_run_backtest_review():
    if not BOT_BACKTESTING_ENABLED:
        return []

    if backtest_already_sent():
        log("Backtest review skipped by interval setting.")
        return []

    results = run_backtest_review()
    mark_backtest_sent()
    return results


# ======================================================
# GOOGLE SHEETS TRACKING
# ======================================================

LIVE_SCANNER_HEADERS = [
    "Timestamp", "Ticker", "Market", "Price", "Daily Change %",
    "Volume", "Avg Volume", "Relative Volume", "Volume Signal", "Volume Score Adj",
    "Market Regime", "Market Score", "Market Anchors", "Advanced Market Regime", "Regime Strength", "Risk Mode", "Regime Notes", "Market Trend Adj", "Market Alignment",
    "Support Level", "Resistance Level", "Distance To Support %", "Distance To Resistance %",
    "S/R Signal", "S/R Score Adj", "S/R Strength", "S/R Position", "Support Touches", "Resistance Touches", "S/R Notes",
    "Trade Entry", "Stop Loss", "Take Profit 1", "Take Profit 2", "Risk/Reward 1", "Risk/Reward 2", "Trade Plan", "Trade Notes",
    "Account Size", "Risk %", "Risk Dollars", "Position Size", "Position Value", "Position Notes", "Trailing Stop", "Breakeven Trigger", "Trail Distance", "Trailing Notes",
    "Asset Category", "Signal Quality Score", "Signal Rank", "Alert Approved", "Exposure Notes",
    "News Sentiment", "News Sentiment Score", "News Strength", "News Score Adj", "News Headlines", "News Notes",
    "RSI Confidence", "MACD Confidence", "Trend Confidence", "Technical Confidence", "MTF Confidence",
    "Volume Confidence", "Market Confidence", "S/R Confidence", "News Confidence", "Risk/Reward Confidence",
    "Confidence Grade", "Confidence Engine", "Confidence Notes", "Legacy Confidence %",
    "RSI", "MACD", "MACD Signal", "Technical Score",
    "Short TF Score", "Short TF Trend", "Momentum TF Score", "Momentum TF Trend", "Daily Trend", "Higher TF Score", "Higher TF Trend",
    "MTF Alignment", "MTF Score Adj", "Final Score", "Confidence %", "Signal"
]

SCAN_HISTORY_HEADERS = LIVE_SCANNER_HEADERS

TRADE_ALERT_HEADERS = [
    "Timestamp", "Ticker", "Market", "Price", "Daily Change %",
    "Volume", "Avg Volume", "Relative Volume", "Volume Signal", "Volume Score Adj",
    "Market Regime", "Market Score", "Market Anchors", "Advanced Market Regime", "Regime Strength", "Risk Mode", "Regime Notes", "Market Trend Adj", "Market Alignment",
    "Support Level", "Resistance Level", "Distance To Support %", "Distance To Resistance %",
    "S/R Signal", "S/R Score Adj", "S/R Strength", "S/R Position", "Support Touches", "Resistance Touches", "S/R Notes",
    "Trade Entry", "Stop Loss", "Take Profit 1", "Take Profit 2", "Risk/Reward 1", "Risk/Reward 2", "Trade Plan", "Trade Notes",
    "Account Size", "Risk %", "Risk Dollars", "Position Size", "Position Value", "Position Notes", "Trailing Stop", "Breakeven Trigger", "Trail Distance", "Trailing Notes",
    "Asset Category", "Signal Quality Score", "Signal Rank", "Alert Approved", "Exposure Notes",
    "News Sentiment", "News Sentiment Score", "News Strength", "News Score Adj", "News Headlines", "News Notes",
    "RSI Confidence", "MACD Confidence", "Trend Confidence", "Technical Confidence", "MTF Confidence",
    "Volume Confidence", "Market Confidence", "S/R Confidence", "News Confidence", "Risk/Reward Confidence",
    "Confidence Grade", "Confidence Engine", "Confidence Notes", "Legacy Confidence %",
    "Signal", "Confidence %", "RSI", "MACD",
    "Short TF Trend", "Momentum TF Trend", "Daily Trend", "Higher TF Trend", "MTF Alignment", "MTF Score Adj",
    "Final Score", "Alert Sent"
]

SIGNAL_TRACKER_HEADERS = [
    "Signal ID", "Opened At", "Last Updated", "Ticker", "Market",
    "Signal", "Entry Price", "Current Price", "Stop Loss", "Take Profit 1", "Take Profit 2",
    "Raw Change %", "Signal Performance %", "Confidence %", "Risk/Reward 2", "RSI", "Status", "Outcome", "Signal Rank", "Asset Category"
]

BOT_PERFORMANCE_HEADERS = [
    "Signal", "Count", "Average Signal Performance %", "Wins", "Losses", "Win Rate %"
]

DASHBOARD_ANALYTICS_HEADERS = [
    "Metric", "Value"
]

SETUP_PERFORMANCE_HEADERS = [
    "Timestamp", "Setup Name", "Setup Tags", "Trades", "Wins", "Losses",
    "Win Rate %", "Profit Factor", "Average Return %", "Total P/L", "Status"
]

STRATEGY_RANKING_HEADERS = [
    "Timestamp", "Rank", "Setup Name", "Strategy Label", "Trades", "Wins", "Losses",
    "Win Rate %", "Profit Factor", "Average Return %", "Total P/L",
    "Strategy Score", "Do Not Automate", "Recommended Action"
]

WALK_FORWARD_HEADERS = [
    "Timestamp", "Windows", "Passed", "Notes"
]

BEST_TICKERS_HEADERS = [
    "Ticker", "Market", "Signal", "Count", "Average Signal Performance %",
    "Wins", "Losses", "Win Rate %", "Last Updated"
]

BACKTEST_RESULTS_HEADERS = [
    "Timestamp", "Ticker", "Market", "Signals Tested", "Wins", "Losses",
    "Win Rate %", "Average Return %", "Best Return %", "Worst Return %",
    "Average Confidence %", "Buy Signals", "Sell Signals", "Profit Factor",
    "Max Drawdown %", "Expectancy %", "Average R/R", "Hold Days", "Period", "Walk Forward Windows", "Walk Forward Passed Windows", "Walk Forward Pass Rate %", "Walk Forward Notes", "Notes"
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
    "Backtesting Results": "#00A6A6",
    "Walk Forward": "#00A6A6",
    "Dashboard Analytics": "#34A853",
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
    Also sanitizes pandas/numpy scalar values before sending.
    """
    values = sanitize_sheet_values(values)
    try:
        return worksheet.update(range_name=range_name, values=values)
    except TypeError:
        return worksheet.update(range_name, values)


def safe_append_rows(worksheet, rows):
    if not rows:
        return None

    rows = sanitize_sheet_values(rows)
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


def google_column_letter(column_number):
    """Convert a 1-based column number to a Google Sheets column letter."""
    try:
        column_number = int(column_number)
    except Exception:
        return "Z"

    if column_number <= 0:
        return "Z"

    letters = []
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        letters.append(chr(65 + remainder))

    return "".join(reversed(letters))


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
        last_column = google_column_letter(max(len(headers), 1))
        worksheet.format(
            f"A:{last_column}",
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
            ["Multi-Timeframe Enabled", str(BOT_MULTI_TIMEFRAME_ENABLED)],
            ["Short Timeframe", f"{BOT_SHORT_TIMEFRAME_PERIOD} {BOT_SHORT_TIMEFRAME_INTERVAL}"],
            ["Higher Timeframe", BOT_HIGHER_TIMEFRAME_LABEL],
            ["MTF Alignment Required", str(BOT_MTF_ALIGNMENT_REQUIRED)],
            ["MTF Alignment Min Matches", BOT_MTF_ALIGNMENT_MIN_MATCHES],
            ["Momentum Timeframe", f"{BOT_MOMENTUM_TIMEFRAME_PERIOD} {BOT_MOMENTUM_TIMEFRAME_INTERVAL}"],
            ["MTF Max Adjustment", BOT_MTF_MAX_ADJUSTMENT],
            ["MTF Short Enabled", str(BOT_MTF_SHORT_ENABLED)],
            ["MTF Higher Enabled", str(BOT_MTF_HIGHER_ENABLED)],
            ["MTF Short Confirm Points", BOT_MTF_SHORT_CONFIRM_POINTS],
            ["MTF Higher Confirm Points", BOT_MTF_HIGHER_CONFIRM_POINTS],
            ["MTF Minimum Rows", BOT_MTF_REQUIRE_MIN_ROWS],
            ["MTF Time Guard Seconds", BOT_MTF_TIME_GUARD_SECONDS],
            ["Higher Timeframe Resample Rule", BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE],
            ["Volume Spike Enabled", str(BOT_VOLUME_SPIKE_ENABLED)],
            ["Volume Average Window", BOT_VOLUME_AVG_WINDOW],
            ["Volume Spike Threshold", BOT_VOLUME_SPIKE_THRESHOLD],
            ["Volume Strong Spike Threshold", BOT_VOLUME_STRONG_SPIKE_THRESHOLD],
            ["Volume Dry Up Threshold", BOT_VOLUME_DRY_UP_THRESHOLD],
            ["Volume Max Adjustment", BOT_VOLUME_MAX_ADJUSTMENT],
            ["Market Trend Filter Enabled", str(BOT_MARKET_TREND_FILTER_ENABLED)],
            ["Market Trend Max Adjustment", BOT_MARKET_TREND_MAX_ADJUSTMENT],
            ["Market Trend Min Anchors", BOT_MARKET_TREND_MIN_ANCHORS],
            ["Crypto Market Anchors", ", ".join(clean_ticker_list(BOT_CRYPTO_MARKET_TICKERS))],
            ["Stock Market Anchors", ", ".join(clean_ticker_list(BOT_STOCK_MARKET_TICKERS))],
            ["Confidence Engine Enabled", str(BOT_CONFIDENCE_ENGINE_ENABLED)],
            ["Confidence Baseline", BOT_CONFIDENCE_BASELINE],
            ["Confidence Tech Weight", BOT_CONFIDENCE_TECH_WEIGHT],
            ["Confidence MTF Weight", BOT_CONFIDENCE_MTF_WEIGHT],
            ["Confidence Volume Weight", BOT_CONFIDENCE_VOLUME_WEIGHT],
            ["Confidence Market Weight", BOT_CONFIDENCE_MARKET_WEIGHT],
            ["Confidence S/R Weight", BOT_CONFIDENCE_SR_WEIGHT],
            ["Support/Resistance Enabled", str(BOT_SUPPORT_RESISTANCE_ENABLED)],
            ["Support/Resistance Lookback", BOT_SUPPORT_RESISTANCE_LOOKBACK],
            ["Support/Resistance Near %", BOT_SUPPORT_RESISTANCE_NEAR_PCT],
            ["Support/Resistance Breakout %", BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT],
            ["Support/Resistance Max Adjustment", BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT],
            ["Trade Management Enabled", str(BOT_TRADE_MANAGEMENT_ENABLED)],
            ["ATR Window", BOT_ATR_WINDOW],
            ["ATR Stop Multiplier", BOT_ATR_STOP_MULTIPLIER],
            ["ATR Target 1 Multiplier", BOT_ATR_TARGET1_MULTIPLIER],
            ["ATR Target 2 Multiplier", BOT_ATR_TARGET2_MULTIPLIER],
            ["Minimum Risk/Reward", BOT_MIN_RISK_REWARD],
            ["Backtesting Enabled", str(BOT_BACKTESTING_ENABLED)],
            ["Backtesting Period", BOT_BACKTEST_PERIOD],
            ["Backtesting Hold Days", BOT_BACKTEST_HOLD_DAYS],
            ["Backtesting Lookback Days", BOT_BACKTEST_LOOKBACK_DAYS],
            ["Backtesting Min Confidence", BOT_BACKTEST_MIN_CONFIDENCE],
            ["Backtesting Max Tickers", BOT_BACKTEST_MAX_TICKERS],
            ["Backtest Quality Filter Enabled", str(BOT_BACKTEST_QUALITY_FILTER_ENABLED)],
            ["Backtest Quality Min PF", BOT_BACKTEST_QUALITY_MIN_PF],
            ["Backtest Quality Min Win Rate", BOT_BACKTEST_QUALITY_MIN_WIN_RATE],
            ["Market Regime Detection Enabled", str(BOT_MARKET_REGIME_DETECTION_ENABLED)],
            ["Signal Ranking Enabled", str(BOT_SIGNAL_RANKING_ENABLED)],
            ["Max Alerts Per Scan", BOT_MAX_ALERTS_PER_SCAN],
            ["Position Sizing Enabled", str(BOT_POSITION_SIZING_ENABLED)],
            ["Account Size", BOT_ACCOUNT_SIZE],
            ["Risk Per Trade %", BOT_RISK_PER_TRADE_PCT],
            ["Max Position %", BOT_MAX_POSITION_PCT],
            ["Trailing Stop Enabled", str(BOT_TRAILING_STOP_ENABLED)],
            ["Trailing ATR Multiplier", BOT_TRAILING_ATR_MULTIPLIER],
            ["Exposure Controls Enabled", str(BOT_EXPOSURE_CONTROLS_ENABLED)],
            ["Max Alerts Per Market", BOT_MAX_ALERTS_PER_MARKET],
            ["Max Alerts Per Category", BOT_MAX_ALERTS_PER_CATEGORY],
            ["Walk Forward Enabled", str(BOT_WALK_FORWARD_ENABLED)],
            ["Walk Forward Windows", BOT_WALK_FORWARD_WINDOWS],
            ["Outcome Tracking Enabled", str(BOT_OUTCOME_TRACKING_ENABLED)],
            ["Dashboard Analytics Enabled", str(BOT_DASHBOARD_ANALYTICS_ENABLED)],
            ["News Sentiment Weighting Enabled", str(BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED)],
            ["News Sentiment Max Adjustment", BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT],
            ["News Sentiment Max Items Per Ticker", BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER],
            ["News Sentiment Use Market News", str(BOT_NEWS_SENTIMENT_USE_MARKET_NEWS)],
            ["Confidence News Weight", BOT_CONFIDENCE_NEWS_WEIGHT],
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
        row.get("Volume", ""),
        row.get("Avg Volume", ""),
        row.get("Relative Volume", ""),
        row.get("Volume Signal", ""),
        row.get("Volume Score Adj", ""),
        row.get("Market Regime", ""),
        row.get("Market Score", ""),
        row.get("Market Anchors", ""),
        row.get("Advanced Market Regime", ""),
        row.get("Regime Strength", ""),
        row.get("Risk Mode", ""),
        row.get("Regime Notes", ""),
        row.get("Market Trend Adj", ""),
        row.get("Market Alignment", ""),
        row.get("Support Level", ""),
        row.get("Resistance Level", ""),
        row.get("Distance To Support %", ""),
        row.get("Distance To Resistance %", ""),
        row.get("S/R Signal", ""),
        row.get("S/R Score Adj", ""),
        row.get("S/R Strength", ""),
        row.get("S/R Position", ""),
        row.get("Support Touches", ""),
        row.get("Resistance Touches", ""),
        row.get("S/R Notes", ""),
        row.get("Trade Entry", ""),
        row.get("Stop Loss", ""),
        row.get("Take Profit 1", ""),
        row.get("Take Profit 2", ""),
        row.get("Risk/Reward 1", ""),
        row.get("Risk/Reward 2", ""),
        row.get("Trade Plan", ""),
        row.get("Trade Notes", ""),
        row.get("Account Size", ""),
        row.get("Risk %", ""),
        row.get("Risk Dollars", ""),
        row.get("Position Size", ""),
        row.get("Position Value", ""),
        row.get("Position Notes", ""),
        row.get("Trailing Stop", ""),
        row.get("Breakeven Trigger", ""),
        row.get("Trail Distance", ""),
        row.get("Trailing Notes", ""),
        row.get("Asset Category", ""),
        row.get("Signal Quality Score", ""),
        row.get("Signal Rank", ""),
        row.get("Alert Approved", ""),
        row.get("Exposure Notes", ""),
        row.get("News Sentiment", ""),
        row.get("News Sentiment Score", ""),
        row.get("News Strength", ""),
        row.get("News Score Adj", ""),
        row.get("News Headlines", ""),
        row.get("News Notes", ""),
        row.get("RSI Confidence", ""),
        row.get("MACD Confidence", ""),
        row.get("Trend Confidence", ""),
        row.get("Technical Confidence", ""),
        row.get("MTF Confidence", ""),
        row.get("Volume Confidence", ""),
        row.get("Market Confidence", ""),
        row.get("S/R Confidence", ""),
        row.get("News Confidence", ""),
        row.get("Risk/Reward Confidence", ""),
        row.get("Confidence Grade", ""),
        row.get("Confidence Engine", ""),
        row.get("Confidence Notes", ""),
        row.get("Legacy Confidence %", ""),
        row.get("RSI", ""),
        row.get("MACD", ""),
        row.get("MACD Signal", ""),
        row.get("Technical Score", ""),
        row.get("Short TF Score", ""),
        row.get("Short TF Trend", ""),
        row.get("Momentum TF Score", ""),
        row.get("Momentum TF Trend", ""),
        row.get("Daily Trend", ""),
        row.get("Higher TF Score", ""),
        row.get("Higher TF Trend", ""),
        row.get("MTF Alignment", ""),
        row.get("MTF Score Adj", ""),
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
        row.get("Volume", ""),
        row.get("Avg Volume", ""),
        row.get("Relative Volume", ""),
        row.get("Volume Signal", ""),
        row.get("Volume Score Adj", ""),
        row.get("Market Regime", ""),
        row.get("Market Score", ""),
        row.get("Market Anchors", ""),
        row.get("Advanced Market Regime", ""),
        row.get("Regime Strength", ""),
        row.get("Risk Mode", ""),
        row.get("Regime Notes", ""),
        row.get("Market Trend Adj", ""),
        row.get("Market Alignment", ""),
        row.get("Support Level", ""),
        row.get("Resistance Level", ""),
        row.get("Distance To Support %", ""),
        row.get("Distance To Resistance %", ""),
        row.get("S/R Signal", ""),
        row.get("S/R Score Adj", ""),
        row.get("S/R Strength", ""),
        row.get("S/R Position", ""),
        row.get("Support Touches", ""),
        row.get("Resistance Touches", ""),
        row.get("S/R Notes", ""),
        row.get("Trade Entry", ""),
        row.get("Stop Loss", ""),
        row.get("Take Profit 1", ""),
        row.get("Take Profit 2", ""),
        row.get("Risk/Reward 1", ""),
        row.get("Risk/Reward 2", ""),
        row.get("Trade Plan", ""),
        row.get("Trade Notes", ""),
        row.get("Account Size", ""),
        row.get("Risk %", ""),
        row.get("Risk Dollars", ""),
        row.get("Position Size", ""),
        row.get("Position Value", ""),
        row.get("Position Notes", ""),
        row.get("Trailing Stop", ""),
        row.get("Breakeven Trigger", ""),
        row.get("Trail Distance", ""),
        row.get("Trailing Notes", ""),
        row.get("Asset Category", ""),
        row.get("Signal Quality Score", ""),
        row.get("Signal Rank", ""),
        row.get("Alert Approved", ""),
        row.get("Exposure Notes", ""),
        row.get("News Sentiment", ""),
        row.get("News Sentiment Score", ""),
        row.get("News Strength", ""),
        row.get("News Score Adj", ""),
        row.get("News Headlines", ""),
        row.get("News Notes", ""),
        row.get("RSI Confidence", ""),
        row.get("MACD Confidence", ""),
        row.get("Trend Confidence", ""),
        row.get("Technical Confidence", ""),
        row.get("MTF Confidence", ""),
        row.get("Volume Confidence", ""),
        row.get("Market Confidence", ""),
        row.get("S/R Confidence", ""),
        row.get("News Confidence", ""),
        row.get("Risk/Reward Confidence", ""),
        row.get("Confidence Grade", ""),
        row.get("Confidence Engine", ""),
        row.get("Confidence Notes", ""),
        row.get("Legacy Confidence %", ""),
        row.get("AI Signal", ""),
        row.get("AI Confidence %", ""),
        row.get("RSI", ""),
        row.get("MACD", ""),
        row.get("Short TF Trend", ""),
        row.get("Momentum TF Trend", ""),
        row.get("Daily Trend", ""),
        row.get("Higher TF Trend", ""),
        row.get("MTF Alignment", ""),
        row.get("MTF Score Adj", ""),
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
                row.get("Stop Loss", ""),
                row.get("Take Profit 1", ""),
                row.get("Take Profit 2", ""),
                round(raw_change_percent, 2),
                round(signal_performance_percent, 2),
                row.get("AI Confidence %", ""),
                row.get("Risk/Reward 2", ""),
                row.get("RSI", ""),
                status,
                determine_signal_outcome(row, current_price),
                row.get("Signal Rank", ""),
                row.get("Asset Category", asset_category(row.get("Ticker", "")))
            ]

            if sheet_row_number:
                updates.append({
                    "range": f"A{sheet_row_number}:T{sheet_row_number}",
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


def sync_setup_performance_to_google_sheets(spreadsheet):
    if not GOOGLE_SHEETS_ENABLED or not BOT_SETUP_ANALYTICS_ENABLED:
        return False
    try:
        worksheet = get_or_create_worksheet(spreadsheet, "Setup Performance", SETUP_PERFORMANCE_HEADERS)
        rows = []
        for row in calculate_setup_performance_rows()[:BOT_SETUP_ANALYTICS_MAX_REPORT_ROWS]:
            rows.append([
                now_text(), row.get("setup_name", ""), row.get("setup_tags", ""),
                row.get("trades", 0), row.get("wins", 0), row.get("losses", 0),
                row.get("wr", 0), row.get("pf", 0), row.get("avg_return", 0),
                row.get("total_pnl", 0), row.get("status", ""),
            ])
        worksheet.clear()
        safe_sheet_update(worksheet, "A1", [SETUP_PERFORMANCE_HEADERS] + rows)
        return True
    except Exception as error:
        log(f"Setup performance sync error: {error}")
        return False


def sync_strategy_ranking_to_google_sheets(spreadsheet):
    if not GOOGLE_SHEETS_ENABLED or not BOT_STRATEGY_RANKING_ENABLED:
        return False
    try:
        worksheet = get_or_create_worksheet(spreadsheet, "Strategy Ranking", STRATEGY_RANKING_HEADERS)
        rows = []
        for row in calculate_strategy_ranking_rows()[:BOT_STRATEGY_RANKING_MAX_REPORT_ROWS]:
            rows.append([
                now_text(), row.get("strategy_rank", ""), row.get("setup_name", ""),
                row.get("strategy_label", ""), row.get("trades", 0), row.get("wins", 0),
                row.get("losses", 0), row.get("wr", 0), row.get("pf", 0),
                row.get("avg_return", 0), row.get("total_pnl", 0),
                row.get("strategy_score", 0), "YES" if row.get("do_not_automate") else "NO",
                row.get("recommended_action", ""),
            ])
        worksheet.clear()
        safe_sheet_update(worksheet, "A1", [STRATEGY_RANKING_HEADERS] + rows)
        return True
    except Exception as error:
        log(f"Strategy ranking sync error: {error}")
        return False


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
        get_or_create_worksheet(spreadsheet, "Backtesting Results", BACKTEST_RESULTS_HEADERS)
        get_or_create_worksheet(spreadsheet, "Walk Forward", WALK_FORWARD_HEADERS)
        get_or_create_worksheet(spreadsheet, "Dashboard Analytics", DASHBOARD_ANALYTICS_HEADERS)
        get_or_create_worksheet(spreadsheet, "Setup Performance", SETUP_PERFORMANCE_HEADERS)
        get_or_create_worksheet(spreadsheet, "Strategy Ranking", STRATEGY_RANKING_HEADERS)
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
        sync_dashboard_analytics_to_google_sheets(scanned_rows)
        sync_setup_performance_to_google_sheets(spreadsheet)
        sync_strategy_ranking_to_google_sheets(spreadsheet)
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
            "name": "Multi-Timeframe",
            "value": "On" if BOT_MULTI_TIMEFRAME_ENABLED else "Off",
            "inline": True
        },
        {
            "name": "MTF Frames",
            "value": f"{BOT_PRIMARY_TIMEFRAME_LABEL} / {BOT_SHORT_TIMEFRAME_INTERVAL.upper()} / {BOT_MOMENTUM_TIMEFRAME_INTERVAL.upper()} / {BOT_HIGHER_TIMEFRAME_LABEL}",
            "inline": True
        },
        {
            "name": "Volume Spike Detection",
            "value": "On" if BOT_VOLUME_SPIKE_ENABLED else "Off",
            "inline": True
        },
        {
            "name": "Market Trend Filter",
            "value": "On" if BOT_MARKET_TREND_FILTER_ENABLED else "Off",
            "inline": True
        },
        {
            "name": "Confidence Engine",
            "value": "On" if BOT_CONFIDENCE_ENGINE_ENABLED else "Legacy",
            "inline": True
        },
        {
            "name": "Support/Resistance",
            "value": "On" if BOT_SUPPORT_RESISTANCE_ENABLED else "Off",
            "inline": True
        },
        {
            "name": "Trade Management",
            "value": "On" if BOT_TRADE_MANAGEMENT_ENABLED else "Off",
            "inline": True
        },
        {
            "name": "Backtesting / Phase 3",
            "value": f"BT {'On' if BOT_BACKTESTING_ENABLED else 'Off'} | Rank {'On' if BOT_SIGNAL_RANKING_ENABLED else 'Off'} | Size {'On' if BOT_POSITION_SIZING_ENABLED else 'Off'} | Trail {'On' if BOT_TRAILING_STOP_ENABLED else 'Off'} | Exposure {'On' if BOT_EXPOSURE_CONTROLS_ENABLED else 'Off'} | WF {'On' if BOT_WALK_FORWARD_ENABLED else 'Off'} | Outcomes {'On' if BOT_OUTCOME_TRACKING_ENABLED else 'Off'} | Analytics {'On' if BOT_DASHBOARD_ANALYTICS_ENABLED else 'Off'}",
            "inline": True
        },
        {
            "name": "News Sentiment",
            "value": "On" if BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED else "Off",
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

    market_contexts = safe_build_market_contexts(scan_started_at)
    log(f"Crypto market context: {market_contexts.get('Crypto', {})}")
    log(f"Stock market context: {market_contexts.get('Stock', {})}")

    news_sentiment_contexts = build_news_sentiment_contexts(scan_started_at)
    log(f"News sentiment weighting contexts built: {len(news_sentiment_contexts)} ticker(s)")

    paper_monitor_result = monitor_open_paper_trades()
    log(f"Paper trade monitor: {paper_monitor_result}")
    run_safe_step("Paper trade summary", send_paper_trade_summary_if_due)

    for ticker in ALL_TICKERS:
        if time.time() - scan_started_at > BOT_MAX_SCAN_SECONDS:
            ticker_errors += 1
            log(f"Max scan time reached after {round(time.time() - scan_started_at, 2)} seconds. Ending ticker loop early.")
            break

        if SHUTDOWN_REQUESTED:
            log("Scan interrupted by shutdown request before all tickers completed.")
            break

        try:
            row = score_ticker(ticker, scan_started_at, market_contexts, news_sentiment_contexts)

            if row is None:
                continue

            scanned_rows.append(row)
            scanned += 1

            signal = row["AI Signal"]
            confidence = row["AI Confidence %"]
            log(f"{ticker} | {signal} | {confidence}%")

        except Exception as error:
            ticker_errors += 1
            log(f"{ticker}: unexpected scoring error: {error}")

        finally:
            if YFINANCE_TICKER_DELAY_SECONDS > 0:
                interruptible_sleep(YFINANCE_TICKER_DELAY_SECONDS)


    assign_signal_rankings(scanned_rows)
    raw_candidates = [
        row for row in scanned_rows
        if row.get("AI Signal") in ["STRONG BUY", "BUY", "STRONG SELL", "SELL"]
        and safe_float(row.get("AI Confidence %", 0), 0) >= MIN_CONFIDENCE
    ]
    candidates = len(raw_candidates)
    quality_candidates = apply_quality_filters(raw_candidates)
    approved_candidates = apply_exposure_controls(quality_candidates)

    for row in approved_candidates:
        signal = row.get("AI Signal", "")
        ticker = row.get("Ticker", "")
        alert_key = f"{ticker}_{signal}_{today}"

        if alert_key in sent_signals:
            skipped_duplicates += 1
            row["Exposure Notes"] = "skipped duplicate alert"
            append_signal_history(row, "DUPLICATE_SKIPPED")
            continue

        sent = send_signal_alert(row)

        if sent:
            sent_count += 1
            alerted_rows.append(row)
            sent_signals.add(alert_key)
            append_signal_history(row, "SENT")
            save_log(SIGNAL_LOG_FILE, sent_signals)
            interruptible_sleep(1)
        else:
            append_signal_history(row, "SEND_FAILED")

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
    interruptible_sleep(1)

    backtest_results, step_error = run_safe_step("Advanced backtest review", maybe_run_backtest_review)
    post_scan_errors += int(step_error)
    backtest_results = backtest_results or []

    _, step_error = run_safe_step("Dynamic confidence optimization", log_dynamic_confidence_report, backtest_results)
    post_scan_errors += int(step_error)
    _, step_error = run_safe_step("Setup performance analytics", log_setup_performance_report)
    post_scan_errors += int(step_error)

    _, step_error = run_safe_step("Top signals Discord summary", send_top_signals_summary, scanned_rows, candidates, sent_count)
    post_scan_errors += int(step_error)
    interruptible_sleep(1)

    _, step_error = run_safe_step(
        "Daily performance Discord report",
        send_daily_performance_report,
        scanned_rows,
        alerted_rows,
        candidates,
        sent_count,
        skipped_duplicates,
        ticker_errors,
        post_scan_errors,
        backtest_results,
    )
    post_scan_errors += int(step_error)
    interruptible_sleep(1)

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
    log(f"Multi-timeframe enabled: {BOT_MULTI_TIMEFRAME_ENABLED}")
    log(f"MTF frames: {BOT_PRIMARY_TIMEFRAME_LABEL} / {BOT_SHORT_TIMEFRAME_INTERVAL.upper()} / {BOT_MOMENTUM_TIMEFRAME_INTERVAL.upper()} / {BOT_HIGHER_TIMEFRAME_LABEL}")
    log(f"MTF enabled flags: short={BOT_MTF_SHORT_ENABLED}, momentum=True, higher={BOT_MTF_HIGHER_ENABLED}")
    log(f"MTF points: short={BOT_MTF_SHORT_CONFIRM_POINTS}, higher={BOT_MTF_HIGHER_CONFIRM_POINTS}, max={BOT_MTF_MAX_ADJUSTMENT}")
    log(f"MTF minimum rows: {BOT_MTF_REQUIRE_MIN_ROWS}")
    log(f"MTF time guard seconds: {BOT_MTF_TIME_GUARD_SECONDS}")
    log(f"Higher timeframe resample rule: {BOT_HIGHER_TIMEFRAME_RESAMPLE_RULE}")
    log(f"Volume spike enabled: {BOT_VOLUME_SPIKE_ENABLED}")
    log(f"Volume avg window: {BOT_VOLUME_AVG_WINDOW}")
    log(f"Volume thresholds: spike={BOT_VOLUME_SPIKE_THRESHOLD}, strong={BOT_VOLUME_STRONG_SPIKE_THRESHOLD}, dry_up={BOT_VOLUME_DRY_UP_THRESHOLD}")
    log(f"Volume max adjustment: {BOT_VOLUME_MAX_ADJUSTMENT}")
    log(f"Market trend filter enabled: {BOT_MARKET_TREND_FILTER_ENABLED}")
    log(f"Market trend max adjustment: {BOT_MARKET_TREND_MAX_ADJUSTMENT}")
    log(f"Market trend min anchors: {BOT_MARKET_TREND_MIN_ANCHORS}")
    log(f"Crypto market anchors: {', '.join(clean_ticker_list(BOT_CRYPTO_MARKET_TICKERS))}")
    log(f"Stock market anchors: {', '.join(clean_ticker_list(BOT_STOCK_MARKET_TICKERS))}")
    log(f"Confidence engine enabled: {BOT_CONFIDENCE_ENGINE_ENABLED}")
    log(f"Confidence weights: tech={BOT_CONFIDENCE_TECH_WEIGHT}, mtf={BOT_CONFIDENCE_MTF_WEIGHT}, volume={BOT_CONFIDENCE_VOLUME_WEIGHT}, market={BOT_CONFIDENCE_MARKET_WEIGHT}, support_resistance={BOT_CONFIDENCE_SR_WEIGHT}, news={BOT_CONFIDENCE_NEWS_WEIGHT}")
    log(f"Confidence baseline: {BOT_CONFIDENCE_BASELINE}")
    log(f"Support/resistance enabled: {BOT_SUPPORT_RESISTANCE_ENABLED}")
    log(f"Support/resistance lookback: {BOT_SUPPORT_RESISTANCE_LOOKBACK}")
    log(f"Support/resistance near pct: {BOT_SUPPORT_RESISTANCE_NEAR_PCT}")
    log(f"Support/resistance breakout pct: {BOT_SUPPORT_RESISTANCE_BREAKOUT_PCT}")
    log(f"Support/resistance max adjustment: {BOT_SUPPORT_RESISTANCE_MAX_ADJUSTMENT}")
    log(f"News sentiment weighting enabled: {BOT_NEWS_SENTIMENT_WEIGHTING_ENABLED}")
    log(f"News sentiment max adjustment: {BOT_NEWS_SENTIMENT_MAX_ADJUSTMENT}")
    log(f"News sentiment max items per ticker: {BOT_NEWS_SENTIMENT_MAX_ITEMS_PER_TICKER}")
    log(f"News sentiment use market news: {BOT_NEWS_SENTIMENT_USE_MARKET_NEWS}")
    log(f"Trade management enabled: {BOT_TRADE_MANAGEMENT_ENABLED}")
    log(f"ATR settings: window={BOT_ATR_WINDOW}, stop={BOT_ATR_STOP_MULTIPLIER}, tp1={BOT_ATR_TARGET1_MULTIPLIER}, tp2={BOT_ATR_TARGET2_MULTIPLIER}")
    log(f"Backtesting enabled: {BOT_BACKTESTING_ENABLED}")
    log(f"Backtesting settings: period={BOT_BACKTEST_PERIOD}, hold={BOT_BACKTEST_HOLD_DAYS}, lookback={BOT_BACKTEST_LOOKBACK_DAYS}, min_conf={BOT_BACKTEST_MIN_CONFIDENCE}, max_tickers={BOT_BACKTEST_MAX_TICKERS}")
    log(f"Phase 3: regime={BOT_MARKET_REGIME_DETECTION_ENABLED}, ranking={BOT_SIGNAL_RANKING_ENABLED}, sizing={BOT_POSITION_SIZING_ENABLED}, trailing={BOT_TRAILING_STOP_ENABLED}, exposure={BOT_EXPOSURE_CONTROLS_ENABLED}, walk_forward={BOT_WALK_FORWARD_ENABLED}, outcomes={BOT_OUTCOME_TRACKING_ENABLED}, dashboard_analytics={BOT_DASHBOARD_ANALYTICS_ENABLED}")
    log(f"Adaptive filters enabled: {BOT_ADAPTIVE_FILTERS_ENABLED} | block weak={BOT_ADAPTIVE_BLOCK_WEAK_TICKERS} | min closed={BOT_ADAPTIVE_FILTERS_MIN_CLOSED_TRADES}")
    log(f"Adaptive bootstrap enabled: {BOT_ADAPTIVE_USE_BACKTEST_BOOTSTRAP} | min backtest signals={BOT_ADAPTIVE_BOOTSTRAP_MIN_BACKTEST_SIGNALS}")
    log(f"Adaptive thresholds: avoid PF<={BOT_ADAPTIVE_AVOID_MAX_PF} or WR<={BOT_ADAPTIVE_AVOID_MAX_WR}% | favorite PF>={BOT_ADAPTIVE_FAVORITE_MIN_PF} and WR>={BOT_ADAPTIVE_FAVORITE_MIN_WR}%")
    log(f"Dynamic confidence enabled: {BOT_DYNAMIC_CONFIDENCE_ENABLED} | min sample={BOT_DYNAMIC_CONFIDENCE_MIN_SAMPLE} | target PF={BOT_DYNAMIC_CONFIDENCE_TARGET_PF} | target WR={BOT_DYNAMIC_CONFIDENCE_TARGET_WR}% | mode=recommendation-only")
    log(f"Setup analytics enabled: {BOT_SETUP_ANALYTICS_ENABLED} | min sample={BOT_SETUP_ANALYTICS_MIN_SAMPLE} | strong PF={BOT_SETUP_ANALYTICS_STRONG_PF} | strong WR={BOT_SETUP_ANALYTICS_STRONG_WR}% | mode=recommendation-only")
    log(f"Strategy ranking enabled: {BOT_STRATEGY_RANKING_ENABLED} | min sample={BOT_STRATEGY_RANKING_MIN_SAMPLE} | strong PF={BOT_STRATEGY_RANKING_STRONG_PF} | strong WR={BOT_STRATEGY_RANKING_STRONG_WR}% | weak PF={BOT_STRATEGY_RANKING_WEAK_PF} | weak WR={BOT_STRATEGY_RANKING_WEAK_WR}% | block weak={BOT_STRATEGY_RANKING_BLOCK_WEAK_SETUPS}")
    log(f"Yahoo/yfinance news enabled: {BOT_NEWS_YFINANCE_ENABLED}")
    log(f"Max scan seconds: {BOT_MAX_SCAN_SECONDS}")
    log(f"Discord message limit: {DISCORD_MESSAGE_LIMIT}")
    log(f"Discord elite alerts enabled: {BOT_DISCORD_ELITE_ALERTS_ENABLED}")
    log(f"Top signals summary enabled: {BOT_SEND_TOP_SIGNALS_SUMMARY} | count={BOT_TOP_SIGNALS_COUNT} | cooldown={BOT_TOP_SIGNALS_MIN_INTERVAL_MINUTES} min")
    log(f"Daily performance report enabled: {BOT_SEND_DAILY_PERFORMANCE_REPORT} | hour={BOT_DAILY_REPORT_HOUR}")
    log(f"Backtest scorecard enabled: {BOT_SEND_BACKTEST_SCORECARD}")
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
