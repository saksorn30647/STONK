"""
Phase 4.5 -- Is the weekly-vs-monthly DCA gap real, or just noise?

We run the SAME weekly-vs-monthly comparison 100 times. Each run uses a
different random seed (which controls the random buy-price draws inside
get_random_cost), but for a given seed, weekly and monthly are re-seeded
to the SAME starting point before their own run. That's what makes each
row a matched PAIR rather than two unrelated numbers -- pair i's weekly
result and pair i's monthly result share the same source of randomness.

A paired t-test then checks: is the average (monthly - weekly) difference
across all 100 pairs bigger than we'd expect from pure chance? If yes
(p < 0.05), the edge we saw back in Phase 1 is a real, repeatable effect
(most likely the "monthly's money sits in the market slightly longer on
average" timing effect discussed then) -- not a fluke of one lucky run.
"""
import random
import pandas as pd
from scipy import stats

from STKDCA import load_ticker_data, run_dca

# --- Settings for this test (separate from STKDCA.py's interactive prompts) ---
TICKER = "VOO"
START_DATE = "2010-01-01"
END_DATE = "2025-07-29"
MONTHLY_BUDGET = 60.0
FEE_PCT = 0.01
STRATEGY = "flat"      # keep it "flat", not "dip" -- we're isolating the pure
                        # weekly-vs-monthly timing question, not the dip strategy
N_RUNS = 100
OUTPUT_CSV = "significance_test_results.csv"

print(f"Downloading {TICKER} once (reused for all {N_RUNS} paired runs)...")
df = load_ticker_data(TICKER, START_DATE, END_DATE)

rows = []
for seed in range(N_RUNS):
    # Re-seeding before EACH run (not once before the loop) is what pairs
    # them: weekly and monthly both start from the same PRNG state, so
    # seed i's pair isn't influenced by how many random numbers a
    # DIFFERENT freq's run happened to consume.
    random.seed(seed)
    weekly = run_dca(
        df, freq="weekly", monthly_budget=MONTHLY_BUDGET, buy_mode="random",
        fee_pct=FEE_PCT, strategy=STRATEGY, verbose=False,
    )

    random.seed(seed)
    monthly = run_dca(
        df, freq="monthly", monthly_budget=MONTHLY_BUDGET, buy_mode="random",
        fee_pct=FEE_PCT, strategy=STRATEGY, verbose=False,
    )

    rows.append({
        "seed": seed,
        "weekly_final_value": weekly["final_value"],
        "monthly_final_value": monthly["final_value"],
        "weekly_performance_pct": weekly["performance_pct"],
        "monthly_performance_pct": monthly["performance_pct"],
        "diff_monthly_minus_weekly": monthly["final_value"] - weekly["final_value"],
    })

    if (seed + 1) % 20 == 0:
        print(f"  ...{seed + 1}/{N_RUNS} runs done")

results = pd.DataFrame(rows)
results.to_csv(OUTPUT_CSV, index=False)
print(f"\nSaved all {N_RUNS} paired results to {OUTPUT_CSV} -- open it in Excel any time to inspect or double-check by hand.")

# --- Paired t-test ---
# Null hypothesis: the true average difference (monthly - weekly) is 0,
# i.e. any edge we see is just random noise from the 100 samples we happened
# to draw. ttest_rel is built exactly for "same subjects, two conditions"
# comparisons like this -- it looks at the 100 per-seed DIFFERENCES, not the
# raw weekly/monthly numbers separately, which is what makes it more
# sensitive here than treating them as two independent, unpaired samples.
t_stat, p_value = stats.ttest_rel(results["monthly_final_value"], results["weekly_final_value"])
mean_diff = results["diff_monthly_minus_weekly"].mean()
std_diff = results["diff_monthly_minus_weekly"].std()

print("\n=== Paired t-test: Monthly vs Weekly final portfolio value ===")
print(f"Mean difference (Monthly - Weekly): ${mean_diff:.2f}  (std dev across runs: ${std_diff:.2f})")
print(f"t-statistic: {t_stat:.4f}")
print(f"p-value: {p_value:.10f}")

if p_value < 0.05:
    winner = "Monthly" if mean_diff > 0 else "Weekly"
    print(f"-> p < 0.05: statistically significant. {winner} really does tend to come out ahead here -- not just noise.")
else:
    print("-> p >= 0.05: NOT statistically significant. The observed difference is consistent with random noise;")
    print("   we can't confidently say either strategy is actually better based on this data.")
