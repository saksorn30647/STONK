import random
from collections import defaultdict
import yfinance as yf
import pandas as pd
# (matplotlib is imported further down, inside the __main__ block -- the web
# server imports this file too, and it never draws plots, so it shouldn't
# have to install/load matplotlib just to run the simulation.)

# MA_WINDOW_DAYS lives here (not inside the __main__ block below) because
# load_ticker_data() needs it even when this file is just IMPORTED for its
# functions (e.g. by significance_test.py), not run directly.
MA_WINDOW_DAYS = 50  # "is the market down" is judged against this trailing average, in trading days


def prompt_value(label, default, cast=str, validate=None, error_msg="Invalid value, try again."):
    """
    Ask the user a question in the terminal, e.g. 'Stock ticker [VOO]: '.
    If they just hit Enter without typing anything, we use `default` instead.
    `cast` converts their typed text into the right type (float, int, ...).
    `validate` is an optional function that takes the value and returns
    True/False; if it returns False (or `cast` fails, e.g. typing letters
    where a number was expected) we print `error_msg` and ask again instead
    of accepting garbage.
    """
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if raw == "":
            value = default
        else:
            try:
                value = cast(raw)
            except ValueError:
                print(f"  -> Couldn't read '{raw}' as the right type. {error_msg}")
                continue
        if validate is not None and not validate(value):
            print(f"  -> {error_msg}")
            continue
        return value


def load_ticker_data(ticker_symbol, start_date, end_date):
    """
    Download daily price history. auto_adjust=False keeps raw Close prices
    (not back-adjusted for dividends), so our own dividend handling in
    run_dca() doesn't double-count them.
    """
    ticker = yf.Ticker(ticker_symbol)
    df = ticker.history(start=start_date, end=end_date, auto_adjust=False)

    if df.empty:
        # A regular exception (not SystemExit) so callers can catch it and
        # let the user retry instead of killing the whole script.
        raise ValueError(
            f"No price data for '{ticker_symbol}' between {start_date} and {end_date}. "
            "Check the ticker symbol and dates and try again."
        )

    # Trailing moving average of Close, used by the "dip" strategy to judge
    # whether today's price counts as "cheap" or "expensive". min_periods=1
    # means the very first days (before we have 50 days of history yet) just
    # average what's available so far, instead of being blank.
    df["MA"] = df["Close"].rolling(window=MA_WINDOW_DAYS, min_periods=1).mean()
    return df


def get_random_cost(lowest, highest):
    """Pick a random price between a day's Low and High (simulates 'bought sometime during the day')."""
    return random.uniform(lowest, highest)


def get_period_key(date, freq):
    """
    Turn a date into a tuple that identifies its 'period' (which week / which month).
    As long as this tuple stays the same, we're still in the same period.
    The moment it changes, that's a new period -> time to buy.
    """
    if freq == "weekly":
        iso_year, iso_week, _ = date.isocalendar()
        return (iso_year, iso_week)  # ISO year, not calendar year -> avoids the New Year's week bug
    elif freq == "monthly":
        return (date.year, date.month)
    else:
        raise ValueError("freq must be 'weekly' or 'monthly'")


def run_dca(
    df, freq, monthly_budget, buy_mode="random", fee_pct=0.0,
    strategy="flat", sensitivity=5.0, min_mult=0.5, max_mult=2.0, ticker_label="",
    verbose=True,
):
    """
    Simulate a DCA strategy (weekly or monthly) using a per-MONTH budget.

    Step 1: Walk the price history once to find the first trading day of every
             period (every week, or every month, depending on `freq`). Those
             are our buy days.
    Step 2: Group the buy days by the calendar month they fall in, so we know
             "this month had 4 weekly buy-days" or "this month had 1 monthly
             buy-day". Split that month's monthly_budget evenly across its
             buy-days. (For monthly freq this is always 1 buy -> gets the
             whole monthly_budget. For weekly freq it's usually 4 or 5 buys
             -> each gets a slice.) This is the BASE amount before any
             dip-strategy adjustment.
    Step 2.5 (only if strategy="dip"): nudge each buy-date's base amount up or
             down depending on how far that day's price sits below/above its
             own moving average, then clip it to [min_mult, max_mult] x base
             so no single buy can go to zero or blow the budget.
    Step 3: Walk the price history again. Each day:
             - if VOO paid a dividend, reinvest it into more shares (DRIP)
             - if today is a scheduled buy day, spend today's (possibly
               dip-adjusted) slice of the budget (minus the fee) on more shares
             and record portfolio value.

    `verbose=False` suppresses the printed summary -- handy when this gets
    called in a loop (e.g. 100x for the significance test) and 200 print
    blocks would just be noise.
    """
    # --- Step 1: find the first trading day of every period ---
    buy_dates = []
    last_period = None
    for date, row in df.iterrows():
        key = get_period_key(date, freq)
        if key != last_period:
            buy_dates.append(date)
            last_period = key

    # --- Step 2: group those buy-dates by calendar month, split budget ---
    dates_by_month = defaultdict(list)
    for d in buy_dates:
        dates_by_month[(d.year, d.month)].append(d)

    invest_amount = {}  # buy-date -> $ to invest that day
    for dates_in_month in dates_by_month.values():
        per_buy = monthly_budget / len(dates_in_month)
        for d in dates_in_month:
            invest_amount[d] = per_buy

    # --- Step 2.5: scale each buy up/down based on price vs. moving average ---
    if strategy == "dip":
        for d in buy_dates:
            price = df.loc[d, "Close"]
            ma = df.loc[d, "MA"]
            pct_below_ma = (ma - price) / ma  # positive = price below MA (a "dip"), negative = above
            multiplier = 1 + (pct_below_ma * sensitivity)
            multiplier = max(min_mult, min(max_mult, multiplier))
            invest_amount[d] *= multiplier

    # --- Step 3: simulate the actual buying ---
    has_dividends = "Dividends" in df.columns
    shares = 0.0
    total_invested = 0.0
    total_fees = 0.0
    total_dividends = 0.0
    value_plot = []     # (date, portfolio value) for every day
    invested_plot = []  # (date, cash put in so far) for every day

    for date, row in df.iterrows():
        # Dividend reinvestment: pays out per SHARE HELD, so it must use
        # `shares` as of the start of today, before any scheduled buy below.
        dividend_per_share = row["Dividends"] if has_dividends else 0.0
        if dividend_per_share > 0 and shares > 0:
            dividend_cash = shares * dividend_per_share
            shares += dividend_cash / row["Close"]
            total_dividends += dividend_cash

        # Scheduled DCA buy
        if date in invest_amount:
            if buy_mode == "random":
                price = get_random_cost(row["Low"], row["High"])
            else:
                price = row["Low"]
            amount = invest_amount[date]
            fee = amount * fee_pct
            shares += (amount - fee) / price
            total_invested += amount
            total_fees += fee

        stock_value = shares * row["Close"]
        value_plot.append((date, stock_value))
        invested_plot.append((date, total_invested))

    final_value = value_plot[-1][1]
    performance_pct = ((final_value - total_invested) / total_invested) * 100

    if verbose:
        label = "Weekly" if freq == "weekly" else "Monthly"
        strategy_note = f", {strategy} strategy" if strategy != "flat" else ""
        ticker_note = f"{ticker_label} " if ticker_label else ""
        print(f"---------------{ticker_note}{label} DCA{strategy_note}----------------")
        print(f"Number of buys: {len(buy_dates)}")
        print(f"Total invested: {total_invested:.2f} $")
        print(f"Total fees paid: {total_fees:.2f} $")
        print(f"Total dividends reinvested: {total_dividends:.2f} $")
        print(f"Total value: {final_value:.2f} $")
        print(f"Performance: {performance_pct:.2f}%")

    return {
        "num_buys": len(buy_dates),
        "total_invested": total_invested,
        "total_fees": total_fees,
        "total_dividends": total_dividends,
        "final_value": final_value,
        "performance_pct": performance_pct,
        "value_plot": value_plot,
        "invested_plot": invested_plot,
    }


def multi_stock_dca(stock_data, ticker_data, monthly_budget, buy_mode="random", fee_pct=0.01,
    strategy="flat", sensitivity=5.0, min_mult=0.5, max_mult=2.0, freq="monthly", verbose=True):
    """
    Run DCA simulation for multiple stocks in the portfolio based on their allocation percentages,
    then combine them into one portfolio-level result. `ticker_data` is the dict of
    already-downloaded/validated DataFrames built during setup, so this never re-downloads.

    The result also carries a "per_stock" dict (ticker -> that stock's own invested /
    final value / performance), so the website can show each stock's numbers, not
    just the portfolio total. `verbose=False` silences all the printing (the web
    server uses that -- nobody reads a server's terminal).
    """
    total_result = {
        "num_buys": 0,
        "total_invested": 0.0,
        "total_fees": 0.0,
        "total_dividends": 0.0,
        "final_value": 0.0,
        "per_stock": {},
    }
    value_series_list = []     # one pd.Series per stock: date -> that stock's value
    invested_series_list = []  # one pd.Series per stock: date -> that stock's cash invested so far

    for ticker, percentage in stock_data.items():
        df = ticker_data[ticker]
        stock_budget = monthly_budget * (percentage / 100)

        result = run_dca(
            df, freq=freq, monthly_budget=stock_budget, buy_mode=buy_mode, fee_pct=fee_pct,
            strategy=strategy, sensitivity=sensitivity, min_mult=min_mult, max_mult=max_mult,
            ticker_label=ticker, verbose=verbose,
        )

        # Aggregate the single numbers directly
        total_result["num_buys"] += result["num_buys"]
        total_result["total_invested"] += result["total_invested"]
        total_result["total_fees"] += result["total_fees"]
        total_result["total_dividends"] += result["total_dividends"]
        total_result["final_value"] += result["final_value"]

        # This stock's own headline numbers, kept separately for the per-stock breakdown
        total_result["per_stock"][ticker] = {
            "total_invested": result["total_invested"],
            "final_value": result["final_value"],
            "performance_pct": result["performance_pct"],
        }

        # Keep each stock's day-by-day series so we can add them up further down.
        # Different tickers can have slightly different trading calendars, so a
        # plain list concatenation would misalign dates -- pd.Series + concat
        # lines them up by date instead.
        value_series_list.append(pd.Series(dict(result["value_plot"])))
        invested_series_list.append(pd.Series(dict(result["invested_plot"])))

    # Combine into one date-indexed table: columns = stocks, rows = dates.
    # ffill/bfill covers the rare day one ticker is missing a price (e.g. a
    # different exchange holiday) by carrying its last known value instead of
    # treating the missing day as $0, which would fake a portfolio crash.
    value_table = pd.concat(value_series_list, axis=1).sort_index().ffill().bfill()
    invested_table = pd.concat(invested_series_list, axis=1).sort_index().ffill().bfill()

    total_result["value_plot"] = list(value_table.sum(axis=1).items())
    total_result["invested_plot"] = list(invested_table.sum(axis=1).items())

    total_result["performance_pct"] = (
        (total_result["final_value"] - total_result["total_invested"]) / total_result["total_invested"]
    ) * 100

    if verbose:
        f = "Weekly" if freq == "weekly" else "Monthly"
        print(f"{f} Portfolio Total value : {total_result['final_value']:.2f} $")
        print(f"{f} Performance: {total_result['performance_pct']:.2f}%")

    return total_result


# ---------------------------------------------------------------------------
# Everything below only runs when you execute `python STKDCA.py` directly --
# not when another script does `from STKDCA import run_dca`. That's what
# lets significance_test.py reuse the simulation functions above without
# being interrupted by these terminal prompts.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import matplotlib.pyplot as plt  # only the terminal version draws charts

    # --- Settings that stay fixed for now (not asked interactively yet) ---
    BUY_MODE = "random"      # "random" = random price between the day's Low and High
                              # "low"    = pretend we always buy at the day's Low (unrealistic, kept for curiosity)
    FEE_PCT = 0.01           # brokerage fee per scheduled buy, e.g. 0.01 = 1%. Comes out of the
                              # budget (a $15 buy at 1% fee spends $0.15 on fees, $14.85 buys shares).
                              # Dividend reinvestment is assumed fee-free, like most real DRIP programs.
    STRATEGY = "flat"        # "flat" = every buy gets an equal slice of the monthly budget (Phase 0-2 behavior)
                              # "dip"  = same base slice, then scaled up/down based on that day's price
                              #          vs. its own moving average (see settings below)
    DIP_SENSITIVITY = 5.0    # for every 1% the price sits below its moving average, buy 5% more that day
                              # (and symmetrically, buy 5% less for every 1% price is ABOVE the average)
    MIN_BUY_MULTIPLIER = 0.5  # floor: never buy less than half the base scheduled amount
    MAX_BUY_MULTIPLIER = 2.0  # ceiling: never buy more than double the base scheduled amount

    # --- Ask the user for the inputs that change what we're simulating ---
    print("=== DCA Simulation Setup (press Enter to accept the default) ===")

    MONTHLY_BUDGET = prompt_value(
        "Monthly budget ($)", 60.0, float,
        validate=lambda v: v > 0,
        error_msg="Budget must be a positive number.",
    )
    START_DATE = prompt_value(
        "Start date (YYYY-MM-DD)", "2010-01-01",
        validate=lambda v: not pd.isna(pd.to_datetime(v, errors="coerce")) and pd.Timestamp(v) <= pd.Timestamp.today(),
        error_msg="The entered date is not compatible, Please recheck and enter a valid date in the format YYYY-MM-DD, and ensure it is correct.",
    )
    YEARS = prompt_value(
        "How many years to simulate", 15, int,
        validate=lambda v: v > 0,
        error_msg="Years must be a positive whole number.",
    )

    # Turn "start date + N years" into an actual end date. pd.DateOffset handles
    # leap years/month-length correctly (unlike just adding years*365 days).
    # If that end date would land in the future, clamp it to today instead --
    # and say so, so a 100-year request doesn't quietly turn into a 15-year run.
    start_dt = pd.Timestamp(START_DATE)
    end_dt = start_dt + pd.DateOffset(years=YEARS)
    today = pd.Timestamp.today().normalize()
    if end_dt > today:
        print(f"  -> {YEARS} years from {START_DATE} goes past today; using today ({today.date()}) as the end date instead.")
        end_dt = today
    END_DATE = end_dt.strftime("%Y-%m-%d")
    print(f"Simulating {YEARS} years of DCA starting from {START_DATE} with a monthly budget of ${MONTHLY_BUDGET:.2f}.")

    # Date range is locked in above, BEFORE we collect tickers, so each ticker can
    # be validated against it immediately -- a bad/delisted-for-this-range ticker
    # only costs you re-typing that one ticker, not restarting the whole setup.
    stock_list = []
    stock_data = {}
    ticker_data = {}  # ticker -> already-downloaded DataFrame, so we don't fetch it twice

    while True:
        print("Enter the stock ticker symbol (e.g. VOO, SPY, AAPL, MSFT, etc.) enter '0000' to complete setting up.")
        TICKER = prompt_value("Stock ticker", "VOO")
        if TICKER == '0000':
            if not stock_list:
                print("  -> Add at least one stock before finishing setup.")
                continue
            break
        if TICKER in stock_list:
            print(f"  -> {TICKER} is already in your portfolio, enter a different ticker.")
            continue
        try:
            ticker_data[TICKER] = load_ticker_data(TICKER, START_DATE, END_DATE)
        except ValueError as e:
            print(f"  -> {e}")
            continue
        stock_list.append(TICKER)

    # Percentages are collected in the order tickers were entered. The LAST stock
    # is identified by its position in the list (not by comparing ticker symbols)
    # so this can't misfire even if ticker names look similar.
    MIN_STOCK_PCT = 0.01  # every stock (including the auto-filled last one) must get at least this much,
                           # so an earlier stock can never eat 100% and leave a later one at a hard 0%
                           # (a 0% stock would crash later: dividing by its $0 total_invested)

    for i, TICKER in enumerate(stock_list):
        is_last = (i == len(stock_list) - 1)
        if is_last:
            PERCENTAGE = 100.0 - sum(stock_data.values())
            print(f"Percentage of {TICKER} in portfolio is automatically set to {PERCENTAGE:.2f}% to make the total 100%.")
        else:
            stocks_after_this = (len(stock_list) - 1) - i  # count of stocks still waiting, incl. the auto-last one
            remaining = 100.0 - sum(stock_data.values())
            max_allowed = remaining - (MIN_STOCK_PCT * stocks_after_this)
            print(f"Please enter percentage of {TICKER} in portfolio (e.g. 50.0, 30.0, 20.02). {remaining:.2f}% left to allocate.")
            PERCENTAGE = prompt_value(
                f"Percentage of {TICKER} in portfolio", min(50.0, max_allowed), float,
                validate=lambda v, cap=max_allowed: 0 < v <= cap,
                error_msg=f"Percentage must be greater than 0, and leave at least {MIN_STOCK_PCT}% for each of the {stocks_after_this} stock(s) still to come (max {max_allowed:.2f}% right now).",
            )
        stock_data[TICKER] = PERCENTAGE

    print(f"Portfolio allocation: {', '.join(f'{k}: {v:.2f}%' for k, v in stock_data.items())}")

    weekly_result_flat = multi_stock_dca(
        stock_data, ticker_data, freq="weekly", monthly_budget=MONTHLY_BUDGET, buy_mode=BUY_MODE, fee_pct=FEE_PCT,
        strategy=STRATEGY, sensitivity=DIP_SENSITIVITY, min_mult=MIN_BUY_MULTIPLIER, max_mult=MAX_BUY_MULTIPLIER,
    )

    monthly_result_flat = multi_stock_dca(
        stock_data, ticker_data, freq="monthly", monthly_budget=MONTHLY_BUDGET, buy_mode=BUY_MODE, fee_pct=FEE_PCT,
        strategy=STRATEGY, sensitivity=DIP_SENSITIVITY, min_mult=MIN_BUY_MULTIPLIER, max_mult=MAX_BUY_MULTIPLIER,
    )

    # -----------------------------------------------------------------------
    # Plot: portfolio value over time for both strategies, plus their cash-invested
    # lines. Under the "dip" strategy these two invested lines can drift apart
    # from each other (weekly and monthly react to the same MA signal on
    # different days), so we draw both instead of assuming they match.
    # -----------------------------------------------------------------------
    plt.figure(figsize=(12, 6))
    plt.plot(
        [x[0] for x in weekly_result_flat["value_plot"]],
        [x[1] for x in weekly_result_flat["value_plot"]],
        label="Weekly DCA",
        color="blue",
    )
    plt.plot(
        [x[0] for x in monthly_result_flat["value_plot"]],
        [x[1] for x in monthly_result_flat["value_plot"]],
        label="Monthly DCA",
        color="orange",
    )
    plt.plot(
        [x[0] for x in weekly_result_flat["invested_plot"]],
        [x[1] for x in weekly_result_flat["invested_plot"]],
        label="Weekly invested (cash in)",
        color="blue",
        linestyle="--",
        alpha=0.5,
    )
    plt.plot(
        [x[0] for x in monthly_result_flat["invested_plot"]],
        [x[1] for x in monthly_result_flat["invested_plot"]],
        label="Monthly invested (cash in)",
        color="orange",
        linestyle="--",
        alpha=0.5,
    )
    strategy_desc = f"{STRATEGY} strategy" if STRATEGY != "flat" else "flat budget"
    portfolio_desc = ", ".join(f"{t} {p:.0f}%" for t, p in stock_data.items())
    plt.title(f"{portfolio_desc}: Weekly vs Monthly DCA (${MONTHLY_BUDGET:.0f}/month base, {strategy_desc})")
    plt.xlabel("Date")
    plt.ylabel("Value ($)")
    plt.legend()
    plt.grid()
    plt.show()
