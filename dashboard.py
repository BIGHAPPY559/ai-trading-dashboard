import os
from datetime import datetime

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

# ======================================================
# SETTINGS
# ======================================================

STARTING_BALANCE = 10000
STOP_LOSS_PERCENT = 5
TAKE_PROFIT_PERCENT = 10

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
# CRYPTO_SUMMARY_WEBHOOK_URL = crypto daily AI market summaryP
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


def send_discord_alert(webhook_url, message):
    if not webhook_url:
        return False

    try:
        response = requests.post(webhook_url, json={"content": message}, timeout=10)
        return response.status_code in [200, 204]
    except Exception:
        return False


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
    if os.path.exists(NEWS_LOG_FILE):
        with open(NEWS_LOG_FILE, "r") as file:
            return set(file.read().splitlines())
    return set()


def save_sent_news(news_set):
    with open(NEWS_LOG_FILE, "w") as file:
        file.write("\n".join(news_set))

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


def build_market_summary(watchlist_df, market):
    market_df = watchlist_df[watchlist_df["Market"] == market].copy()

    if market_df.empty:
        return ""

    top_bullish = market_df.head(3)
    top_bearish = market_df.tail(3)

    summary_message = f"AI DAILY {market.upper()} MARKET SUMMARY\n\n"
    summary_message += "TOP BULLISH\n"

    for _, row in top_bullish.iterrows():
        summary_message += (
            f"{row['Ticker']} | Score: {row['Final Score']} | "
            f"Signal: {row['AI Signal']} | Confidence: {row['AI Confidence %']}%\n"
        )

    summary_message += "\nTOP BEARISH\n"

    for _, row in top_bearish.iterrows():
        summary_message += (
            f"{row['Ticker']} | Score: {row['Final Score']} | "
            f"Signal: {row['AI Signal']} | Confidence: {row['AI Confidence %']}%\n"
        )

    return summary_message


def get_article_url(article):
    content = article.get("content", {})
    canonical_url = content.get("canonicalUrl", {})

    if isinstance(canonical_url, dict):
        return canonical_url.get("url", "")

    if isinstance(article.get("link"), str):
        return article.get("link", "")

    return ""


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
            content = article.get("content", {})
            headline = content.get("title", "") or article.get("title", "")
            title = headline.lower()
            article_url = get_article_url(article)

            for word in BULLISH_WORDS:
                if word in title:
                    news_score += 5

            for word in BEARISH_WORDS:
                if word in title:
                    news_score -= 5

            if headline:
                news_key = f"{ticker}_{headline}_{datetime.now().strftime('%Y-%m-%d')}"

                if news_key not in st.session_state.sent_news:
                    webhook_url = get_news_webhook(ticker)

                    if webhook_url:
                        market = get_asset_type(ticker)

                        message = (
                            f"📰 NEWS ALERT\n"
                            f"Market: {market}\n"
                            f"Ticker: {ticker}\n"
                            f"Headline: {headline}"
                        )

                        if article_url:
                            message += f"\nLink: {article_url}"

                        sent = send_discord_alert(webhook_url, message)

                        if sent:
                            st.session_state.sent_news.add(news_key)
                            save_sent_news(st.session_state.sent_news)

    except Exception:
        news_score = 0

    return news_score


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

if "sent_signal_alerts" not in st.session_state:
    st.session_state.sent_signal_alerts = []

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

        st.dataframe(portfolio_df, use_container_width=True)

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
        st.plotly_chart(fig_allocation, use_container_width=True)

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
        st.dataframe(trade_history_df, use_container_width=True)

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
        st.dataframe(crypto_watchlist_df, use_container_width=True)

        selected_crypto = st.selectbox("Choose crypto", CRYPTO_TICKERS, key="selected_crypto")
        crypto_data = get_price_data(selected_crypto, "6mo")

        if not crypto_data.empty:
            st.plotly_chart(create_price_chart(selected_crypto, crypto_data), use_container_width=True)

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
        st.dataframe(stock_watchlist_df, use_container_width=True)

        selected_stock = st.selectbox("Choose stock", STOCK_TICKERS, key="selected_stock")
        stock_data = get_price_data(selected_stock, "6mo")

        if not stock_data.empty:
            st.plotly_chart(create_price_chart(selected_stock, stock_data), use_container_width=True)

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
                use_container_width=True
            )

        with scanner_tab2:
            st.dataframe(
                watchlist_df[watchlist_df["Market"] == "Stock"],
                use_container_width=True
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

            st.dataframe(filtered_watchlist, use_container_width=True)

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
        st.plotly_chart(heatmap_fig, use_container_width=True)

        csv = watchlist_df.to_csv(index=False)
        st.download_button(
            label="Download Watchlist CSV",
            data=csv,
            file_name="ai_watchlist.csv",
            mime="text/csv"
        )

        crypto_summary_message = build_market_summary(watchlist_df, "Crypto")
        stock_summary_message = build_market_summary(watchlist_df, "Stock")

        if st.button("Send Crypto Daily Summary"):
            if not crypto_summary_message:
                st.warning("No crypto summary available.")
            else:
                sent = send_discord_alert(
                    get_summary_webhook("Crypto"),
                    crypto_summary_message
                )
                if sent:
                    st.success("Crypto daily summary sent to Discord.")
                else:
                    st.warning("Crypto summary webhook not found or failed.")

        if st.button("Send Stock Daily Summary"):
            if not stock_summary_message:
                st.warning("No stock summary available.")
            else:
                sent = send_discord_alert(
                    get_summary_webhook("Stock"),
                    stock_summary_message
                )
                if sent:
                    st.success("Stock daily summary sent to Discord.")
                else:
                    st.warning("Stock summary webhook not found or failed.")

        if st.button("Send Both Daily Summaries"):
            crypto_sent = False
            stock_sent = False

            if crypto_summary_message:
                crypto_sent = send_discord_alert(
                    get_summary_webhook("Crypto"),
                    crypto_summary_message
                )

            if stock_summary_message:
                stock_sent = send_discord_alert(
                    get_summary_webhook("Stock"),
                    stock_summary_message
                )

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
                        send_discord_alert(
                            get_trade_webhook(row["Ticker"]),
                            f"AI SIGNAL ALERT\nTicker: {row['Ticker']}\nMarket: {row['Market']}\nSignal: {row['AI Signal']}\nConfidence: {row['AI Confidence %']}%\nPrice: ${row['Price']}"
                        )
                        st.session_state.sent_signal_alerts.append(alert_key)

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

                st.dataframe(trade_log_df, use_container_width=True)

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
                st.plotly_chart(signal_fig, use_container_width=True)

# ======================================================
# SETTINGS TAB
# ======================================================

with settings_tab:
    st.header("Settings")

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

    st.subheader("Test News Webhooks")

    if st.button("Test Crypto News Webhook"):
        sent = send_discord_alert(
            CRYPTO_NEWS_WEBHOOK_URL,
            "📰 TEST CRYPTO NEWS ALERT"
        )

        if sent:
            st.success("Crypto news test sent.")
        else:
            st.error("Crypto news test failed.")

    if st.button("Test Stock News Webhook"):
        sent = send_discord_alert(
            STOCK_NEWS_WEBHOOK_URL,
            "📰 TEST STOCK NEWS ALERT"
        )

        if sent:
            st.success("Stock news test sent.")
        else:
            st.error("Stock news test failed.")

    st.info(
        "Recommended environment variables: CRYPTO_TRADE_WEBHOOK_URL, "
        "STOCK_TRADE_WEBHOOK_URL, CRYPTO_NEWS_WEBHOOK_URL, "
        "STOCK_NEWS_WEBHOOK_URL, CRYPTO_SUMMARY_WEBHOOK_URL, "
        "STOCK_SUMMARY_WEBHOOK_URL. SUMMARY_WEBHOOK_URL still works as a fallback. Older variables like "
        "CRYPTO_WEBHOOK_URL, STOCK_WEBHOOK_URL, TRADE_WEBHOOK_URL, and "
        "NEWS_WEBHOOK_URL still work as fallbacks."
    )

st.divider()
st.caption("AI Trading Dashboard | Stocks and Crypto Only | For education and paper trading, not financial advice.")
