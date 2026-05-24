import os
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

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
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

SCAN_INTERVAL_MINUTES = max(1, get_env_int("BOT_SCAN_INTERVAL_MINUTES", 15))
MIN_CONFIDENCE = max(0, min(get_env_float("AUTO_SIGNAL_MIN_CONFIDENCE", 75), 100))

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

SIGNAL_LOG_FILE = "bot_sent_signal_log.txt"
SUMMARY_LOG_FILE = "bot_sent_summary_log.txt"
NEWS_LOG_FILE = "bot_sent_news_log.txt"
NEWS_SCHEDULE_LOG_FILE = "bot_sent_news_schedule_log.txt"
BREAKING_NEWS_SCHEDULE_LOG_FILE = "bot_sent_breaking_news_schedule_log.txt"


# ======================================================
# HELPER FUNCTIONS
# ======================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def log(message):
    print(message, flush=True)


def get_asset_type(ticker):
    return "Crypto" if ticker.endswith("-USD") else "Stock"


def clean_crypto_symbol(ticker):
    return ticker.replace("-USD", "").upper()


def get_trade_webhook(ticker):
    if get_asset_type(ticker) == "Crypto":
        return CRYPTO_TRADE_WEBHOOK_URL
    return STOCK_TRADE_WEBHOOK_URL


def get_summary_webhook(market):
    if market == "Crypto":
        return CRYPTO_SUMMARY_WEBHOOK_URL
    return STOCK_SUMMARY_WEBHOOK_URL


def get_news_webhook(market):
    if market == "Crypto":
        return CRYPTO_NEWS_WEBHOOK_URL
    return STOCK_NEWS_WEBHOOK_URL


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


def safe_get_json(url, params=None, headers=None, timeout=10):
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)

        if response.status_code != 200:
            log(f"GET JSON failed {response.status_code}: {url} {response.text[:200]}")
            return None

        return response.json()

    except Exception as error:
        log(f"GET JSON error for {url}: {error}")
        return None


def safe_get_text(url, params=None, headers=None, timeout=10):
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)

        if response.status_code != 200:
            log(f"GET text failed {response.status_code}: {url} {response.text[:200]}")
            return ""

        return response.text

    except Exception as error:
        log(f"GET text error for {url}: {error}")
        return ""


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


def send_discord_message(webhook_url, message, max_retries=2):
    if not webhook_url:
        log("Missing webhook for Discord message.")
        return False

    payload = {"content": message[:1900]}

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

                time.sleep(retry_after + 0.5)
                continue

            return False

        except Exception as error:
            log(f"Discord message error: {error}")

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


# ======================================================
# MARKET DATA FUNCTIONS
# ======================================================

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
    today = datetime.now().strftime("%Y-%m-%d")
    normalized_title = item["title"].lower().strip()
    return f"{item['market']}_{item['ticker']}_{item['source']}_{normalized_title}_{today}"


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

    for ticker in tickers:
        items.extend(fetch_yfinance_news_for_ticker(ticker, market))

    return items


# ======================================================
# YAHOO FINANCE RSS NEWS
# ======================================================

def fetch_yahoo_rss_for_ticker(ticker, market):
    rss_url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
    rss_text = safe_get_text(
        rss_url,
        params={
            "s": ticker,
            "region": "US",
            "lang": "en-US"
        }
    )

    if not rss_text:
        return []

    items = []

    try:
        root = ElementTree.fromstring(rss_text)

        for rss_item in root.findall("./channel/item")[:NEWS_ARTICLES_PER_TICKER]:
            title_node = rss_item.find("title")
            link_node = rss_item.find("link")
            pub_date_node = rss_item.find("pubDate")

            item = make_news_item(
                source="Yahoo RSS",
                market=market,
                ticker=ticker,
                title=title_node.text if title_node is not None else "",
                url=link_node.text if link_node is not None else "",
                publisher="Yahoo Finance RSS",
                published_at=pub_date_node.text if pub_date_node is not None else ""
            )

            if item:
                items.append(item)

    except Exception as error:
        log(f"Yahoo RSS parse error for {ticker}: {error}")

    return items


def fetch_yahoo_rss_news(tickers, market):
    items = []

    for ticker in tickers:
        items.extend(fetch_yahoo_rss_for_ticker(ticker, market))

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

    end_date = datetime.now().date()
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

    for ticker in tickers[:8]:
        items.extend(fetch_finnhub_company_news(ticker))

    return items


# ======================================================
# CRYPTOPANIC NEWS
# ======================================================

def fetch_cryptopanic_news():
    if not CRYPTOPANIC_API_KEY:
        return []

    currencies = ",".join([clean_crypto_symbol(ticker) for ticker in CRYPTO_TICKERS[:8]])

    data = safe_get_json(
        "https://cryptopanic.com/api/v1/posts/",
        params={
            "auth_token": CRYPTOPANIC_API_KEY,
            "public": "true",
            "currencies": currencies
        }
    )

    if not data:
        return []

    items = []

    for article in data.get("results", []):
        currencies_list = article.get("currencies", [])
        ticker = "Crypto"

        if currencies_list and isinstance(currencies_list, list):
            first_currency = currencies_list[0]
            if isinstance(first_currency, dict):
                ticker = first_currency.get("code", "Crypto")

        source_data = article.get("source", {})
        publisher = "CryptoPanic"

        if isinstance(source_data, dict):
            publisher = source_data.get("title", "CryptoPanic")

        item = make_news_item(
            source="CryptoPanic",
            market="Crypto",
            ticker=ticker,
            title=article.get("title", ""),
            url=article.get("url", ""),
            publisher=publisher,
            published_at=article.get("published_at", "")
        )

        if item:
            items.append(item)

        if len(items) >= NEWS_MAX_ARTICLES_PER_MARKET:
            break

    return items


# ======================================================
# NEWS DIGEST SENDING
# ======================================================

def collect_news_items(tickers, market, digest_type="scheduled"):
    items = []

    if market == "Crypto":
        items.extend(fetch_cryptopanic_news())
        items.extend(fetch_newsapi_news("Crypto"))
        items.extend(fetch_yahoo_rss_news(tickers, "Crypto"))
        items.extend(fetch_yfinance_news(tickers, "Crypto"))

    if market == "Stock":
        items.extend(fetch_newsapi_news("Stock"))
        items.extend(fetch_finnhub_news(tickers))
        items.extend(fetch_yahoo_rss_news(tickers, "Stock"))
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

        if len(message) + len(line) > 1900:
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

    time.sleep(1)

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

    time.sleep(1)

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

    enabled_sources = ["Yahoo RSS", "Yahoo Finance"]

    if NEWSAPI_KEY:
        enabled_sources.append("NewsAPI")

    if FINNHUB_API_KEY:
        enabled_sources.append("Finnhub")

    if CRYPTOPANIC_API_KEY:
        enabled_sources.append("CryptoPanic")

    fields = [
        {"name": "Status", "value": "Railway worker is online.", "inline": False},
        {"name": "Scan Interval", "value": f"{SCAN_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "Minimum Confidence", "value": f"{MIN_CONFIDENCE}%", "inline": True},
        {"name": "Summaries", "value": "On" if SEND_SUMMARIES else "Off", "inline": True},
        {"name": "News", "value": "On" if SEND_NEWS else "Off", "inline": True},
        {"name": "News Interval", "value": f"{NEWS_INTERVAL_HOURS} hours", "inline": True},
        {"name": "Breaking News", "value": "On" if SEND_BREAKING_NEWS else "Off", "inline": True},
        {"name": "Breaking Interval", "value": f"{BREAKING_NEWS_INTERVAL_MINUTES} minutes", "inline": True},
        {"name": "News Sources", "value": ", ".join(enabled_sources), "inline": False},
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
    time.sleep(1)

    # Run breaking news before scheduled news so urgent headlines are not swallowed
    # by the regular digest duplicate log.
    maybe_send_breaking_news()
    time.sleep(1)
    maybe_send_scheduled_news()

    log("Scan complete.")
    log(f"Scanned: {scanned}")
    log(f"Candidates: {candidates}")
    log(f"Sent: {sent_count}")
    log(f"Skipped duplicates: {skipped_duplicates}")


def main():
    enabled_sources = ["Yahoo RSS", "Yahoo Finance"]

    if NEWSAPI_KEY:
        enabled_sources.append("NewsAPI")

    if FINNHUB_API_KEY:
        enabled_sources.append("Finnhub")

    if CRYPTOPANIC_API_KEY:
        enabled_sources.append("CryptoPanic")

    log("AI Trading Bot started.")
    log(f"Scan interval: {SCAN_INTERVAL_MINUTES} minutes")
    log(f"Minimum confidence: {MIN_CONFIDENCE}%")
    log(f"Summaries enabled: {SEND_SUMMARIES}")
    log(f"Summary interval: {SUMMARY_INTERVAL_HOURS} hours")
    log(f"News enabled: {SEND_NEWS}")
    log(f"News interval: {NEWS_INTERVAL_HOURS} hours")
    log(f"Breaking news enabled: {SEND_BREAKING_NEWS}")
    log(f"Breaking news interval: {BREAKING_NEWS_INTERVAL_MINUTES} minutes")
    log(f"News sources: {', '.join(enabled_sources)}")

    if not CRYPTO_TRADE_WEBHOOK_URL:
        log("WARNING: CRYPTO_TRADE_WEBHOOK_URL is missing.")

    if not STOCK_TRADE_WEBHOOK_URL:
        log("WARNING: STOCK_TRADE_WEBHOOK_URL is missing.")

    if SEND_NEWS and not CRYPTO_NEWS_WEBHOOK_URL:
        log("WARNING: CRYPTO_NEWS_WEBHOOK_URL is missing.")

    if SEND_NEWS and not STOCK_NEWS_WEBHOOK_URL:
        log("WARNING: STOCK_NEWS_WEBHOOK_URL is missing.")

    if not NEWSAPI_KEY:
        log("INFO: NEWSAPI_KEY missing. NewsAPI source disabled.")

    if not FINNHUB_API_KEY:
        log("INFO: FINNHUB_API_KEY missing. Finnhub source disabled.")

    if not CRYPTOPANIC_API_KEY:
        log("INFO: CRYPTOPANIC_API_KEY missing. CryptoPanic source disabled.")

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
