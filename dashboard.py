import streamlit as st
import yfinance as yf
import pandas as pd
import os
from ta.momentum import RSIIndicator
from ta.trend import MACD
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
import plotly.graph_objects as go
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PORTFOLIO_FILE = os.path.join(BASE_DIR, "portfolio.csv")
TRADE_HISTORY_FILE = os.path.join(BASE_DIR, "trade_history.csv")
BALANCE_FILE = os.path.join(BASE_DIR, "balance.txt")
EQUITY_FILE = os.path.join(BASE_DIR, "equity_history.csv")


@st.cache_data(ttl=60)
def get_price_data(ticker, period="6mo"):
    try:
        data = yf.Ticker(ticker).history(period=period)
        return data
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_news(ticker):
    try:
        return yf.Ticker(ticker).news
    except Exception:
        return []


st.title("AI Trading Dashboard")

st_autorefresh(
    interval=60000,
    key="market_refresh"
)

current_time = datetime.now()
market_hour = current_time.hour

if 6 <= market_hour < 13:
    market_status = "OPEN"
else:
    market_status = "CLOSED"

col1, col2, col3 = st.columns(3)

col1.metric("Current Time", current_time.strftime("%Y-%m-%d %H:%M:%S"))
col2.metric("Market Status", market_status)

btc_data = get_price_data("BTC-USD", "2d")
eth_data = get_price_data("ETH-USD", "2d")

if not btc_data.empty and not eth_data.empty:
    btc_price = btc_data["Close"].iloc[-1]
    eth_price = eth_data["Close"].iloc[-1]
    btc_dominance = (btc_price / (btc_price + eth_price)) * 100
    col3.metric("BTC Dominance", f"{btc_dominance:.2f}%")
else:
    col3.metric("BTC Dominance", "N/A")

starting_balance = 10000

STOP_LOSS_PERCENT = 5
TAKE_PROFIT_PERCENT = 10

TRADE_WEBHOOK_URL = os.getenv("TRADE_WEBHOOK_URL")
NEWS_WEBHOOK_URL = os.getenv("NEWS_WEBHOOK_URL")
SUMMARY_WEBHOOK_URL = os.getenv("SUMMARY_WEBHOOK_URL")

BULLISH_WORDS = ["beat", "growth", "upgrade", "surge", "profit", "strong", "bullish", "record"]
BEARISH_WORDS = ["miss", "downgrade", "fall", "drop", "loss", "weak", "bearish", "lawsuit"]

if os.path.exists(BALANCE_FILE):
    with open(BALANCE_FILE, "r") as file:
        st.session_state.balance = float(file.read())
else:
    st.session_state.balance = starting_balance

try:
    if os.path.exists(EQUITY_FILE) and os.path.getsize(EQUITY_FILE) > 0:
        equity_load = pd.read_csv(EQUITY_FILE)
        st.session_state.equity_history = equity_load["Equity"].tolist()
    else:
        st.session_state.equity_history = []
except pd.errors.EmptyDataError:
    st.session_state.equity_history = []

try:
    if os.path.exists(PORTFOLIO_FILE) and os.path.getsize(PORTFOLIO_FILE) > 0:
        portfolio_df_load = pd.read_csv(PORTFOLIO_FILE)
        st.session_state.portfolio = portfolio_df_load.to_dict("records")
    else:
        st.session_state.portfolio = []
except pd.errors.EmptyDataError:
    st.session_state.portfolio = []

try:
    if os.path.exists(TRADE_HISTORY_FILE) and os.path.getsize(TRADE_HISTORY_FILE) > 0:
        trade_history_load = pd.read_csv(TRADE_HISTORY_FILE)
        st.session_state.trade_history = trade_history_load.to_dict("records")
    else:
        st.session_state.trade_history = []
except pd.errors.EmptyDataError:
    st.session_state.trade_history = []

st.subheader("Paper Trading Account")

portfolio_value = 0

for position in st.session_state.portfolio:
    current_data = get_price_data(position["Ticker"], "1d")

    if current_data.empty:
        continue

    current_price = current_data["Close"].iloc[-1]
    portfolio_value += position["Shares"] * current_price

total_equity = st.session_state.balance + portfolio_value

col1, col2, col3 = st.columns(3)

col1.metric("Account Balance", f"${st.session_state.balance:.2f}")
col2.metric("Open Positions", len(st.session_state.portfolio))
col3.metric("Total Equity", f"${total_equity:.2f}")

if st.button("Reset Paper Account"):
    st.session_state.balance = starting_balance
    st.session_state.portfolio = []
    st.session_state.trade_history = []
    st.session_state.equity_history = []

    pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
    pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

    with open(BALANCE_FILE, "w") as file:
        file.write(str(st.session_state.balance))

    st.success("Paper account reset!")
    st.rerun()

if (
    len(st.session_state.equity_history) == 0
    or st.session_state.equity_history[-1] != total_equity
):
    st.session_state.equity_history.append(total_equity)

equity_df = pd.DataFrame({"Equity": st.session_state.equity_history})
equity_df.to_csv(EQUITY_FILE, index=False)

st.subheader("Portfolio Performance Over Time")
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

    for index, row in portfolio_df.iterrows():
        ticker = row["Ticker"]
        shares = row["Shares"]
        buy_price = row["Buy Price"]

        current_data = get_price_data(ticker, "1d")

        if current_data.empty:
            current_values.append(0)
            profits.append(0)
            profit_percents.append(0)
            continue

        current_price = current_data["Close"].iloc[-1]

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

    total_risk = 0

    for index, row in portfolio_df.iterrows():
        entry_price = row["Buy Price"]
        stop_loss = row["Stop Loss"]
        shares = row["Shares"]

        risk_per_position = (entry_price - stop_loss) * shares
        total_risk += risk_per_position

    st.metric("Open Risk Exposure", f"${total_risk:.2f}")

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

    st.subheader("Portfolio Allocation Heatmap")

    allocation_heatmap = go.Figure(
        data=go.Heatmap(
            z=[allocation_df["Current Value"]],
            x=allocation_df["Ticker"],
            y=["Allocation Value"],
            text=[allocation_df["Current Value"].round(2)],
            texttemplate="$%{text}",
            colorscale="Blues"
        )
    )

    allocation_heatmap.update_layout(
        xaxis_title="Ticker",
        yaxis_title="Portfolio"
    )

    st.plotly_chart(allocation_heatmap, use_container_width=True)

    st.subheader("Portfolio Allocation Breakdown")

    st.dataframe(
        allocation_df[["Ticker", "Current Value", "Allocation %"]],
        use_container_width=True
    )

    max_allocation = allocation_df["Allocation %"].max()
    largest_position = allocation_df.loc[allocation_df["Allocation %"].idxmax()]

    if max_allocation > 50:
        st.warning(
            f"⚠️ High concentration risk: "
            f"{largest_position['Ticker']} is "
            f"{max_allocation:.2f}% of your portfolio."
        )

    positions_to_sell = []

    for index, row in portfolio_df.iterrows():
        if row["Shares"] == 0:
            continue

        current_price = row["Current Value"] / row["Shares"]

        stop_loss = row["Stop Loss"]
        take_profit = row["Take Profit"]

        if current_price <= stop_loss:
            st.error(f"🚨 {row['Ticker']} hit STOP LOSS level")
            positions_to_sell.append((index, row, current_price, "STOP LOSS"))

        elif current_price >= take_profit:
            st.success(f"🎯 {row['Ticker']} hit TAKE PROFIT level")
            positions_to_sell.append((index, row, current_price, "TAKE PROFIT"))

        else:
            st.info(f"✅ {row['Ticker']} still within trade range")

    for position_data in positions_to_sell:
        index, row, sell_price, reason = position_data

        proceeds = row["Shares"] * sell_price
        st.session_state.balance += proceeds

        with open(BALANCE_FILE, "w") as file:
            file.write(str(st.session_state.balance))

        buy_price = row["Buy Price"]
        profit = proceeds - (row["Shares"] * buy_price)
        profit_percent = (profit / (row["Shares"] * buy_price)) * 100

        st.session_state.trade_history.append({
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Action": f"AUTO SELL ({reason})",
            "Ticker": row["Ticker"],
            "Shares": row["Shares"],
            "Buy Price": buy_price,
            "Sell Price": sell_price,
            "Profit/Loss $": profit,
            "Profit/Loss %": profit_percent
        })

        if TRADE_WEBHOOK_URL:
            requests.post(
                TRADE_WEBHOOK_URL,
                json={
                    "content":
                    f"🚨 AUTO SELL EXECUTED\n"
                    f"Ticker: {row['Ticker']}\n"
                    f"Reason: {reason}\n"
                    f"Sell Price: ${sell_price:.2f}\n"
                    f"Profit/Loss: ${profit:.2f}"
                }
            )
        else:
            st.warning("Trade webhook not found. Skipping Discord alert.")

    for position_data in reversed(positions_to_sell):
        index = position_data[0]
        st.session_state.portfolio.pop(index)

    pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
    pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

    for index, row in portfolio_df.iterrows():
        if st.button(
            f"Sell {row['Ticker']} #{index}",
            key=f"sell_{row['Ticker']}_{index}_{row['Buy Price']}_{row['Shares']}"
        ):
            current_data = get_price_data(row["Ticker"], "1d")

            if current_data.empty:
                st.error("Could not get current price.")
                st.stop()

            sell_price = current_data["Close"].iloc[-1]
            proceeds = row["Shares"] * sell_price

            st.session_state.balance += proceeds

            with open(BALANCE_FILE, "w") as file:
                file.write(str(st.session_state.balance))

            buy_price = row["Buy Price"]
            profit = proceeds - (row["Shares"] * buy_price)
            profit_percent = (profit / (row["Shares"] * buy_price)) * 100

            st.session_state.trade_history.append({
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Action": "SELL",
                "Ticker": row["Ticker"],
                "Shares": row["Shares"],
                "Buy Price": buy_price,
                "Sell Price": sell_price,
                "Profit/Loss $": profit,
                "Profit/Loss %": profit_percent
            })

            st.session_state.portfolio.pop(index)

            pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
            pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

            st.success(f"Sold {row['Ticker']}")
            st.rerun()

    if st.button("Sell All Positions"):
        portfolio_copy = portfolio_df.copy()

        for index, row in portfolio_copy.iterrows():
            current_data = get_price_data(row["Ticker"], "1d")

            if current_data.empty:
                continue

            sell_price = current_data["Close"].iloc[-1]
            proceeds = row["Shares"] * sell_price

            st.session_state.balance += proceeds

            buy_price = row["Buy Price"]
            profit = proceeds - (row["Shares"] * buy_price)
            profit_percent = (profit / (row["Shares"] * buy_price)) * 100

            st.session_state.trade_history.append({
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Action": "BULK SELL",
                "Ticker": row["Ticker"],
                "Shares": row["Shares"],
                "Buy Price": buy_price,
                "Sell Price": sell_price,
                "Profit/Loss $": profit,
                "Profit/Loss %": profit_percent
            })

        st.session_state.portfolio = []

        pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)
        pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)

        with open(BALANCE_FILE, "w") as file:
            file.write(str(st.session_state.balance))

        st.success("All positions sold successfully.")
        st.rerun()

trade_history_df = pd.DataFrame(st.session_state.trade_history)

if st.session_state.trade_history:
    st.subheader("Trade History")
    st.dataframe(trade_history_df, use_container_width=True)

if "Action" in trade_history_df.columns:
    sell_trades = trade_history_df[
        trade_history_df["Action"].astype(str).str.contains("SELL")
    ]
else:
    sell_trades = pd.DataFrame()

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

    trade_chart_df = sell_trades.copy()
    trade_chart_df["Trade #"] = range(1, len(trade_chart_df) + 1)

    st.subheader("Trade Win/Loss Chart")

    st.bar_chart(
        trade_chart_df,
        x="Trade #",
        y="Profit/Loss $"
    )

    best_trade = sell_trades.loc[sell_trades["Profit/Loss $"].idxmax()]
    worst_trade = sell_trades.loc[sell_trades["Profit/Loss $"].idxmin()]

    col1, col2 = st.columns(2)

    col1.metric(
        "Best Trade",
        f"{best_trade['Ticker']} | ${best_trade['Profit/Loss $']:.2f}"
    )

    col2.metric(
        "Worst Trade",
        f"{worst_trade['Ticker']} | ${worst_trade['Profit/Loss $']:.2f}"
    )

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

watchlist_results = []

for ticker in tickers:
    try:
        data = get_price_data(ticker, "6mo")

        if data.empty:
            continue

        current_price = data["Close"].iloc[-1]
        previous_price = data["Close"].iloc[-2]

        price_change_percent = ((current_price - previous_price) / previous_price) * 100

        rsi = RSIIndicator(close=data["Close"]).rsi().iloc[-1]

        macd_indicator = MACD(close=data["Close"])
        macd = macd_indicator.macd().iloc[-1]
        macd_signal = macd_indicator.macd_signal().iloc[-1]

        data["MA50"] = data["Close"].rolling(window=50).mean()
        data["MA200"] = data["Close"].rolling(window=200).mean()

        ma50 = data["MA50"].iloc[-1]
        ma200 = data["MA200"].iloc[-1]

        score = 0

        if current_price > ma50:
            score += 30

        if ma50 > ma200:
            score += 30

        if rsi < 70:
            score += 20

        if rsi > 40:
            score += 20

        if macd > macd_signal:
            score += 20

        news_score = 0

        try:
            news_items = get_news(ticker)[:1]

            for article in news_items:
                content = article.get("content", {})
                headline = content.get("title", "No Title")
                article_link = content.get("canonicalUrl", {}).get("url", "")

                news_key = f"{ticker}_{headline}"

                if "sent_news" not in st.session_state:
                    st.session_state.sent_news = []

                if news_key not in st.session_state.sent_news:
                    if NEWS_WEBHOOK_URL:
                        requests.post(
                            NEWS_WEBHOOK_URL,
                            json={
                                "content":
                                f"📰 MARKET NEWS ALERT\n"
                                f"Ticker: {ticker}\n"
                                f"Headline: {headline}\n"
                                f"Link: {article_link}"
                            }
                        )

                        st.session_state.sent_news.append(news_key)

                title = headline.lower()

                for word in BULLISH_WORDS:
                    if word in title:
                        news_score += 5

                for word in BEARISH_WORDS:
                    if word in title:
                        news_score -= 5

        except Exception:
            news_score = 0

        final_score = score + news_score
        confidence_percent = (final_score / 120) * 100
        confidence_percent = max(0, min(confidence_percent, 100))

        if confidence_percent >= 80:
            confidence_label = "HIGH"
        elif confidence_percent >= 60:
            confidence_label = "MEDIUM"
        else:
            confidence_label = "LOW"

        if final_score >= 90:
            signal = "STRONG BUY"
        elif final_score >= 75:
            signal = "BUY"
        elif final_score >= 50:
            signal = "HOLD"
        else:
            signal = "SELL"

        watchlist_results.append({
            "Ticker": ticker,
            "Price": round(current_price, 2),
            "RSI": round(rsi, 2),
            "MACD": round(macd, 2),
            "MACD Signal": round(macd_signal, 2),
            "Technical Score": score,
            "News Score": news_score,
            "Final Score": final_score,
            "AI Confidence %": round(confidence_percent, 2),
            "Confidence Level": confidence_label,
            "AI Signal": signal
        })

    except Exception:
        continue

watchlist_df = pd.DataFrame(watchlist_results)

if not watchlist_df.empty:
    watchlist_df = watchlist_df.sort_values(
        by="AI Confidence %",
        ascending=False
    )

if not watchlist_df.empty:
    top_pick = watchlist_df.iloc[0]

    st.subheader("Top AI Pick")

    col1, col2, col3 = st.columns(3)

    col1.metric("Ticker", top_pick["Ticker"])
    col2.metric("AI Confidence", f"{top_pick['AI Confidence %']:.2f}%")
    col3.metric("Signal", top_pick["AI Signal"])

    average_confidence = watchlist_df["AI Confidence %"].mean()

    if average_confidence >= 75:
        market_sentiment = "BULLISH"
    elif average_confidence >= 55:
        market_sentiment = "NEUTRAL"
    else:
        market_sentiment = "BEARISH"

    st.subheader("AI Market Sentiment")

    st.metric("Market Mood", market_sentiment)
    st.metric("Average AI Confidence", f"{average_confidence:.2f}%")
    st.progress(average_confidence / 100)

bulk_buy_amount = st.number_input(
    "Dollar amount per AI pick",
    min_value=1.00,
    value=100.00,
    step=10.00
)

if not watchlist_df.empty:
    top_bulk_picks = watchlist_df.head(3)
else:
    top_bulk_picks = pd.DataFrame()

if st.button("Bulk Buy Top 3 AI Picks"):
    if top_bulk_picks.empty:
        st.error("No AI picks available.")
    else:
        for _, row in top_bulk_picks.iterrows():
            ticker = row["Ticker"]
            current_price = row["Price"]

            shares = bulk_buy_amount / current_price
            cost = shares * current_price

            if st.session_state.balance >= cost:
                st.session_state.balance -= cost

                st.session_state.portfolio.append({
                    "Ticker": ticker,
                    "Shares": shares,
                    "Buy Price": current_price,
                    "Stop Loss": current_price * (1 - STOP_LOSS_PERCENT / 100),
                    "Take Profit": current_price * (1 + TAKE_PROFIT_PERCENT / 100)
                })

                st.session_state.trade_history.append({
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Action": "BULK BUY",
                    "Ticker": ticker,
                    "Shares": shares,
                    "Buy Price": current_price,
                    "Sell Price": None,
                    "Profit/Loss $": None,
                    "Profit/Loss %": None
                })

        pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
        pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

        with open(BALANCE_FILE, "w") as file:
            file.write(str(st.session_state.balance))

        st.success("Bulk buy completed!")
        st.rerun()

st.subheader("Top AI Watchlist")

signal_filter = st.selectbox(
    "Filter AI Signals",
    ["ALL", "STRONG BUY", "BUY", "HOLD", "SELL"]
)

filtered_watchlist = watchlist_df.copy()

ticker_search = st.text_input("Search Ticker")

if not filtered_watchlist.empty:
    if ticker_search:
        filtered_watchlist = filtered_watchlist[
            filtered_watchlist["Ticker"].str.contains(ticker_search.upper())
        ]

    if signal_filter != "ALL":
        filtered_watchlist = filtered_watchlist[
            filtered_watchlist["AI Signal"] == signal_filter
        ]

filtered_buy_amount = st.number_input(
    "Dollar amount per filtered ticker",
    min_value=1.00,
    value=50.00,
    step=10.00
)

if st.button("Bulk Buy Filtered Watchlist"):
    if filtered_watchlist.empty:
        st.error("No filtered tickers available.")
    else:
        for _, row in filtered_watchlist.iterrows():
            ticker = row["Ticker"]
            current_price = row["Price"]

            shares = filtered_buy_amount / current_price
            cost = shares * current_price

            if st.session_state.balance >= cost:
                st.session_state.balance -= cost

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

        pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
        pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

        with open(BALANCE_FILE, "w") as file:
            file.write(str(st.session_state.balance))

        st.success("Filtered bulk buy completed!")
        st.rerun()

st.dataframe(filtered_watchlist, use_container_width=True)

if not watchlist_df.empty:
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

    heatmap_fig.update_layout(
        xaxis_title="Ticker",
        yaxis_title="Metric"
    )

    st.plotly_chart(heatmap_fig, use_container_width=True)

watchlist_csv = filtered_watchlist.to_csv(index=False)

st.download_button(
    label="Download Watchlist CSV",
    data=watchlist_csv,
    file_name="ai_watchlist.csv",
    mime="text/csv"
)

if not watchlist_df.empty:
    top_bullish = watchlist_df.head(3)
    top_bearish = watchlist_df.tail(3)

    summary_message = "📊 AI DAILY MARKET SUMMARY\n\n"
    summary_message += "🔥 TOP BULLISH\n"

    for _, row in top_bullish.iterrows():
        summary_message += (
            f"{row['Ticker']} | "
            f"Score: {row['Final Score']} | "
            f"Signal: {row['AI Signal']}\n"
        )

    summary_message += "\n📉 TOP BEARISH\n"

    for _, row in top_bearish.iterrows():
        summary_message += (
            f"{row['Ticker']} | "
            f"Score: {row['Final Score']} | "
            f"Signal: {row['AI Signal']}\n"
        )

    if st.button("Send Daily Market Summary"):
        if SUMMARY_WEBHOOK_URL:
            requests.post(
                SUMMARY_WEBHOOK_URL,
                json={"content": summary_message}
            )

            st.success("Daily market summary sent to Discord!")
        else:
            st.warning("Summary webhook not found. Skipping Discord summary.")

selected_tickers = st.multiselect(
    "Choose tickers",
    tickers,
    default=["ETH-USD", "BTC-USD", "XRP-USD"]
)

for selected_ticker in selected_tickers:
    data = get_price_data(selected_ticker, "6mo")

    if data.empty:
        continue

    current_price = data["Close"].iloc[-1]

    rsi = RSIIndicator(close=data["Close"]).rsi().iloc[-1]

    macd_indicator = MACD(close=data["Close"])
    macd = macd_indicator.macd().iloc[-1]
    macd_signal = macd_indicator.macd_signal().iloc[-1]

    data["MA50"] = data["Close"].rolling(window=50).mean()
    data["MA200"] = data["Close"].rolling(window=200).mean()

    ma50 = data["MA50"].iloc[-1]
    ma200 = data["MA200"].iloc[-1]

    score = 0

    if current_price > ma50:
        score += 30

    if ma50 > ma200:
        score += 30

    if rsi < 70:
        score += 20

    if rsi > 40:
        score += 20

    if macd > macd_signal:
        score += 20

    st.divider()
    st.subheader(selected_ticker)

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Price", f"${current_price:.2f}")
    col2.metric("RSI", f"{rsi:.2f}")
    col3.metric("MACD", f"{macd:.2f}")
    col4.metric("Signal", f"{macd_signal:.2f}")
    col5.metric("Score", f"{score}/120")

    st.progress(score / 120)

    if score >= 90:
        st.success("AI Decision: STRONG BUY")
    elif score >= 70:
        st.info("AI Decision: WATCH / DECENT SETUP")
    elif rsi > 70:
        st.error("AI Decision: SELL / OVERBOUGHT")
    else:
        st.warning("AI Decision: WAIT")

    if st.button(f"Buy {selected_ticker}", key=f"buy_{selected_ticker}"):
        shares = 0.01
        cost = current_price * shares

        if st.session_state.balance >= cost:
            st.session_state.balance -= cost

            with open(BALANCE_FILE, "w") as file:
                file.write(str(st.session_state.balance))

            st.session_state.portfolio.append({
                "Ticker": selected_ticker,
                "Shares": shares,
                "Buy Price": current_price,
                "Stop Loss": current_price * (1 - STOP_LOSS_PERCENT / 100),
                "Take Profit": current_price * (1 + TAKE_PROFIT_PERCENT / 100)
            })

            st.session_state.trade_history.append({
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Action": "BUY",
                "Ticker": selected_ticker,
                "Shares": shares,
                "Buy Price": current_price,
                "Sell Price": None,
                "Profit/Loss $": None,
                "Profit/Loss %": None
            })

            pd.DataFrame(st.session_state.portfolio).to_csv(PORTFOLIO_FILE, index=False)
            pd.DataFrame(st.session_state.trade_history).to_csv(TRADE_HISTORY_FILE, index=False)

            st.success(f"Bought {shares} shares of {selected_ticker}")
            st.rerun()
        else:
            st.error("Not enough balance")

    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=data.index,
            open=data["Open"],
            high=data["High"],
            low=data["Low"],
            close=data["Close"],
            name="Candles"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=data.index,
            y=data["MA50"],
            line=dict(width=2),
            name="50 MA"
        )
    )

    fig.add_trace(
        go.Scatter(
            x=data.index,
            y=data["MA200"],
            line=dict(width=2),
            name="200 MA"
        )
    )

    fig.update_layout(
        title=f"{selected_ticker} Chart",
        xaxis_title="Date",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False
    )

    st.plotly_chart(fig, use_container_width=True)

st.subheader("Strategy Backtesting")

backtest_ticker = st.selectbox(
    "Choose ticker for backtest",
    tickers
)

backtest_strategy = st.selectbox(
    "Choose backtest strategy",
    [
        "RSI Strategy",
        "MACD Strategy",
        "Moving Average Strategy"
    ]
)

if st.button("Run Backtest"):
    backtest_data = get_price_data(backtest_ticker, "1y")

    if backtest_data.empty:
        st.error("No backtest data available.")
        st.stop()

    backtest_data["RSI"] = RSIIndicator(
        close=backtest_data["Close"]
    ).rsi()

    macd_indicator = MACD(
        close=backtest_data["Close"]
    )

    backtest_data["MACD"] = macd_indicator.macd()
    backtest_data["MACD Signal"] = macd_indicator.macd_signal()

    backtest_data["MA50"] = backtest_data["Close"].rolling(window=50).mean()
    backtest_data["MA200"] = backtest_data["Close"].rolling(window=200).mean()

    balance = 10000
    shares = 0
    trade_log = []

    for index, row in backtest_data.iterrows():
        current_price = row["Close"]

        rsi_value = row["RSI"]
        macd = row["MACD"]
        macd_signal = row["MACD Signal"]
        ma50 = row["MA50"]
        ma200 = row["MA200"]

        if (
            backtest_strategy == "RSI Strategy"
            and rsi_value < 35
            and balance > 0
        ):
            shares = balance / current_price
            balance = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "BUY",
                "Price": current_price,
                "Portfolio Value": balance + (shares * current_price)
            })

        elif (
            backtest_strategy == "RSI Strategy"
            and rsi_value > 70
            and shares > 0
        ):
            balance = shares * current_price
            shares = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "SELL",
                "Price": current_price,
                "Portfolio Value": balance
            })

        elif (
            backtest_strategy == "MACD Strategy"
            and macd > macd_signal
            and balance > 0
        ):
            shares = balance / current_price
            balance = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "BUY",
                "Price": current_price,
                "Portfolio Value": balance + (shares * current_price)
            })

        elif (
            backtest_strategy == "MACD Strategy"
            and macd < macd_signal
            and shares > 0
        ):
            balance = shares * current_price
            shares = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "SELL",
                "Price": current_price,
                "Portfolio Value": balance
            })

        if (
            backtest_strategy == "Moving Average Strategy"
            and ma50 > ma200
            and balance > 0
        ):
            shares = balance / current_price
            balance = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "BUY",
                "Price": current_price,
                "Portfolio Value": balance + (shares * current_price)
            })

        elif (
            backtest_strategy == "Moving Average Strategy"
            and ma50 < ma200
            and shares > 0
        ):
            balance = shares * current_price
            shares = 0

            trade_log.append({
                "Date": index.strftime("%Y-%m-%d"),
                "Action": "SELL",
                "Price": current_price,
                "Portfolio Value": balance
            })

    final_value = balance

    if shares > 0:
        final_value += shares * backtest_data["Close"].iloc[-1]

    sell_prices = []
    buy_prices = []

    for trade in trade_log:
        if trade["Action"] == "BUY":
            buy_prices.append(trade["Price"])
        elif trade["Action"] == "SELL":
            sell_prices.append(trade["Price"])

    completed_trades = min(len(buy_prices), len(sell_prices))

    wins = 0
    losses = 0

    for i in range(completed_trades):
        if sell_prices[i] > buy_prices[i]:
            wins += 1
        else:
            losses += 1

    if completed_trades > 0:
        backtest_win_rate = (wins / completed_trades) * 100
    else:
        backtest_win_rate = 0

    trade_results = []

    for i in range(completed_trades):
        trade_result = ((sell_prices[i] - buy_prices[i]) / buy_prices[i]) * 100
        trade_results.append(trade_result)

    winning_results = [
        result for result in trade_results
        if result > 0
    ]

    losing_results = [
        result for result in trade_results
        if result <= 0
    ]

    if winning_results:
        average_win = sum(winning_results) / len(winning_results)
    else:
        average_win = 0

    if losing_results:
        average_loss = sum(losing_results) / len(losing_results)
    else:
        average_loss = 0

    total_return = ((final_value - 10000) / 10000) * 100

    st.success(
        f"Backtest Complete | Final Portfolio Value: "
        f"${final_value:.2f}"
    )

    st.metric("Backtest Return", f"{total_return:.2f}%")

    st.subheader("Backtest Stats")

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Completed Trades", completed_trades)
    col2.metric("Wins", wins)
    col3.metric("Losses", losses)
    col4.metric("Win Rate", f"{backtest_win_rate:.2f}%")

    col5, col6 = st.columns(2)

    col5.metric("Average Win", f"{average_win:.2f}%")
    col6.metric("Average Loss", f"{average_loss:.2f}%")

    trade_log_df = pd.DataFrame(trade_log)

    if not trade_log_df.empty:
        buy_trades = trade_log_df[trade_log_df["Action"] == "BUY"]
        sell_trades_chart = trade_log_df[trade_log_df["Action"] == "SELL"]

        st.dataframe(trade_log_df, use_container_width=True)

        st.subheader("Backtest Equity Curve")

        st.line_chart(
            trade_log_df,
            x="Date",
            y="Portfolio Value"
        )

        st.subheader("Backtest Trade Signals")

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
                marker=dict(
                    size=10,
                    symbol="triangle-up"
                )
            )
        )

        signal_fig.add_trace(
            go.Scatter(
                x=sell_trades_chart["Date"],
                y=sell_trades_chart["Price"],
                mode="markers",
                name="SELL",
                marker=dict(
                    size=10,
                    symbol="triangle-down"
                )
            )
        )

        st.plotly_chart(signal_fig, use_container_width=True)

    st.subheader("Strategy Comparison")

    comparison_results = []

    strategies = [
        "RSI Strategy",
        "MACD Strategy",
        "Moving Average Strategy"
    ]

    for strategy in strategies:
        test_data = get_price_data(backtest_ticker, "1y")

        if test_data.empty:
            continue

        test_data["RSI"] = RSIIndicator(
            close=test_data["Close"]
        ).rsi()

        macd_test = MACD(
            close=test_data["Close"]
        )

        test_data["MACD"] = macd_test.macd()
        test_data["MACD Signal"] = macd_test.macd_signal()

        test_data["MA50"] = test_data["Close"].rolling(window=50).mean()
        test_data["MA200"] = test_data["Close"].rolling(window=200).mean()

        test_balance = 10000
        test_shares = 0
        test_buys = []
        test_sells = []

        for i, row in test_data.iterrows():
            price = row["Close"]
            rsi_test = row["RSI"]
            macd_value = row["MACD"]
            macd_signal_value = row["MACD Signal"]
            ma50_value = row["MA50"]
            ma200_value = row["MA200"]

            if (
                strategy == "RSI Strategy"
                and rsi_test < 35
                and test_balance > 0
            ):
                test_shares = test_balance / price
                test_balance = 0
                test_buys.append(price)

            elif (
                strategy == "RSI Strategy"
                and rsi_test > 70
                and test_shares > 0
            ):
                test_balance = test_shares * price
                test_shares = 0
                test_sells.append(price)

            elif (
                strategy == "MACD Strategy"
                and macd_value > macd_signal_value
                and test_balance > 0
            ):
                test_shares = test_balance / price
                test_balance = 0
                test_buys.append(price)

            elif (
                strategy == "MACD Strategy"
                and macd_value < macd_signal_value
                and test_shares > 0
            ):
                test_balance = test_shares * price
                test_shares = 0
                test_sells.append(price)

            if (
                strategy == "Moving Average Strategy"
                and ma50_value > ma200_value
                and test_balance > 0
            ):
                test_shares = test_balance / price
                test_balance = 0
                test_buys.append(price)

            elif (
                strategy == "Moving Average Strategy"
                and ma50_value < ma200_value
                and test_shares > 0
            ):
                test_balance = test_shares * price
                test_shares = 0
                test_sells.append(price)

        test_final_value = test_balance

        if test_shares > 0:
            test_final_value += test_shares * test_data["Close"].iloc[-1]

        test_return = ((test_final_value - 10000) / 10000) * 100

        completed = min(len(test_buys), len(test_sells))

        wins = 0

        for x in range(completed):
            if test_sells[x] > test_buys[x]:
                wins += 1

        if completed > 0:
            win_rate = (wins / completed) * 100
        else:
            win_rate = 0

        comparison_results.append({
            "Strategy": strategy,
            "Final Value": round(test_final_value, 2),
            "Return %": round(test_return, 2),
            "Completed Trades": completed,
            "Win Rate %": round(win_rate, 2)
        })

    comparison_df = pd.DataFrame(comparison_results)

    if not comparison_df.empty:
        comparison_df = comparison_df.sort_values(
            by="Return %",
            ascending=False
        )

        st.dataframe(comparison_df, use_container_width=True)

        csv = comparison_df.to_csv(index=False)

        st.download_button(
            label="Download Strategy Comparison CSV",
            data=csv,
            file_name="strategy_comparison.csv",
            mime="text/csv"
        )