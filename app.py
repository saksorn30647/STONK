"""
Phase 5 -- the web server.

This is the "bridge" between the website and the Python simulation. It does
two jobs:

  1. Serves the website files (web/index.html, style.css, script.js) so you can
     open the site in a browser at http://127.0.0.1:5000
  2. Answers POST /api/simulate: the browser sends the form inputs as JSON,
     we run the REAL simulation from STKDCA.py (weekly AND monthly), and send
     the results back as JSON for script.js to draw.

No DCA math lives here -- it all comes from STKDCA.py. This file only checks
inputs, calls the engine, and reshapes the results into browser-friendly JSON.

Run locally:   python app.py
On Render:     gunicorn app:app   (gunicorn is a sturdier server for production)
"""
import random
from functools import lru_cache

import pandas as pd
from flask import Flask, jsonify, request

from STKDCA import load_ticker_data, multi_stock_dca

# --- Fixed simulation settings (same meaning as the constants in STKDCA.py) ---
BUY_MODE = "random"
FEE_PCT = 0.01
DIP_SENSITIVITY = 5.0
MIN_BUY_MULTIPLIER = 0.5
MAX_BUY_MULTIPLIER = 2.0
RANDOM_SEED = 42      # weekly and monthly both start from this seed -> a fair, paired
                      # comparison, and the same inputs always give the same answer
MAX_STOCKS = 8        # keeps one request from downloading dozens of tickers

# static_folder="web" + static_url_path="" means the file web/style.css is
# reachable at http://.../style.css -- exactly what index.html already asks for.
app = Flask(__name__, static_folder="web", static_url_path="")


@lru_cache(maxsize=64)
def cached_ticker_data(ticker, start, end):
    """
    Same as load_ticker_data, but remembers the last 64 downloads. Clicking
    Start again with the same tickers/dates skips Yahoo entirely -- faster, and
    fewer requests means less chance of Yahoo rate-limiting the server.
    """
    return load_ticker_data(ticker, start, end)


class BadInput(Exception):
    """Raised when the request is invalid; turned into a friendly 400 error below."""


def parse_request(data):
    """Check every input from the browser and turn it into clean Python values."""
    try:
        budget = float(data.get("budget"))
    except (TypeError, ValueError):
        raise BadInput("Monthly budget must be a number.")
    if budget <= 0:
        raise BadInput("Monthly budget must be more than $0.")

    start = pd.to_datetime(data.get("start"), errors="coerce")
    end = pd.to_datetime(data.get("end"), errors="coerce")
    if pd.isna(start) or pd.isna(end):
        raise BadInput("Start and end must be valid dates.")
    today = pd.Timestamp.today().normalize()
    end = min(end, today)  # can't simulate the future -- clamp the end date to today
    if start >= end:
        raise BadInput("Start date must be before the end date (and before today).")

    strategy = data.get("strategy", "flat")
    if strategy not in ("flat", "dip"):
        raise BadInput("Strategy must be 'flat' or 'dip'.")

    portfolio = data.get("portfolio") or []
    if not portfolio:
        raise BadInput("Add at least one stock to your portfolio.")
    if len(portfolio) > MAX_STOCKS:
        raise BadInput(f"Max {MAX_STOCKS} stocks per portfolio.")

    stock_data = {}  # ticker -> percentage, same shape multi_stock_dca expects
    for item in portfolio:
        ticker = str(item.get("ticker", "")).strip().upper()
        try:
            pct = float(item.get("pct"))
        except (TypeError, ValueError):
            raise BadInput(f"Percentage for {ticker or 'a stock'} must be a number.")
        if not ticker:
            raise BadInput("One of the stocks has an empty ticker.")
        if ticker in stock_data:
            raise BadInput(f"{ticker} is in the portfolio twice.")
        if pct <= 0:
            raise BadInput(f"{ticker} needs a percentage above 0%.")
        stock_data[ticker] = pct

    # Small tolerance because the browser rounds percentages to 2 decimals
    if abs(sum(stock_data.values()) - 100) > 0.05:
        raise BadInput("Portfolio percentages must add up to 100%.")

    return budget, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), strategy, stock_data


def weekly_chart_points(weekly, monthly):
    """
    The engine records a value for EVERY trading day (~250 per year). That's
    more points than a chart needs, so we keep one point per week (the week's
    last trading day). 15 years -> ~780 points instead of ~3,900.
    All four lines share the same dates so the chart can line them up.
    """
    table = pd.DataFrame({
        "weekly_value": pd.Series(dict(weekly["value_plot"])),
        "monthly_value": pd.Series(dict(monthly["value_plot"])),
        "weekly_invested": pd.Series(dict(weekly["invested_plot"])),
        "monthly_invested": pd.Series(dict(monthly["invested_plot"])),
    }).sort_index().ffill().bfill()

    table = table.resample("W-FRI").last().dropna()

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in table.index],
        **{col: [round(float(v), 2) for v in table[col]] for col in table.columns},
    }


def summarize(result):
    """Pick out the headline numbers and convert them to plain floats (JSON can't hold numpy numbers)."""
    return {
        "total_invested": float(result["total_invested"]),
        "total_fees": float(result["total_fees"]),
        "total_dividends": float(result["total_dividends"]),
        "final_value": float(result["final_value"]),
        "performance_pct": float(result["performance_pct"]),
        "num_buys": int(result["num_buys"]),
        "per_stock": {
            t: {k: float(v) for k, v in nums.items()} for t, nums in result["per_stock"].items()
        },
    }


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/simulate")
def simulate():
    try:
        budget, start, end, strategy, stock_data = parse_request(request.get_json(silent=True) or {})
    except BadInput as e:
        return jsonify(error=str(e)), 400

    # Download (or reuse cached) price history for every ticker
    ticker_data = {}
    for ticker in stock_data:
        try:
            ticker_data[ticker] = cached_ticker_data(ticker, start, end)
        except ValueError as e:
            # load_ticker_data's own "no data for this ticker/date range" message
            return jsonify(error=str(e)), 400
        except Exception as e:
            # Anything else is usually Yahoo refusing/rate-limiting the request
            app.logger.exception("Download failed for %s", ticker)
            return jsonify(error=f"Couldn't download {ticker} from Yahoo Finance right now ({type(e).__name__}). Try again in a minute."), 502

    settings = dict(
        monthly_budget=budget, buy_mode=BUY_MODE, fee_pct=FEE_PCT, strategy=strategy,
        sensitivity=DIP_SENSITIVITY, min_mult=MIN_BUY_MULTIPLIER, max_mult=MAX_BUY_MULTIPLIER,
        verbose=False,
    )

    # Same seed before each run -> weekly and monthly draw from the same random
    # sequence, just like the paired runs in significance_test.py
    random.seed(RANDOM_SEED)
    weekly = multi_stock_dca(stock_data, ticker_data, freq="weekly", **settings)
    random.seed(RANDOM_SEED)
    monthly = multi_stock_dca(stock_data, ticker_data, freq="monthly", **settings)

    return jsonify(
        start=start,
        end=end,
        weekly=summarize(weekly),
        monthly=summarize(monthly),
        chart=weekly_chart_points(weekly, monthly),
    )


if __name__ == "__main__":
    # debug=True auto-reloads the server whenever you save a .py file
    app.run(debug=True, port=5000)
