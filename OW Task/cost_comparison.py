"""cost_comparison.py — the core comparison chart of the whole project:
static day-0-style estimate vs. actual cumulative cost as repairs complete
over time. The actuarial "reserve development" style chart — everything
else in the final presentation supports this one.

Why the two lines need different logic (not just two columns plotted):
  - Static model: NOT a single flat day-0 number — it's the time-distributed
    curve already computed by static_time.py (20% of each property's
    initial_estimated_cost_aud spent at job_start_day, 80% at job_end_day),
    read directly from static_cost_timeline.csv.
  - Actual cost: only becomes real, dollar-by-dollar, as each individual
    property's repair job actually finishes. Plotted as a cumulative STEP
    function: for day t, sum actual_repair_cost_aud across every property
    whose job_end_day <= t. This climbs gradually over the many months of
    the simulation, not all at once.

Forward compatibility: data loading and plotting are kept in separate
functions specifically so that once the credibility model exists, its
series can be added as one more load_*()/compute_*() function plus one more
ax.plot()/ax.step() call in plot_comparison() — this script should be
EXTENDED, not rebuilt, at that point.

Second chart — REMAINING cost, i.e. "what's still left to pay" at day t
under each model's own view (each converges to $0 by the end, since
eventually every repair is done, under any model):
  - Static remaining(t)      = static's own final total - static_cumulative(t)
  - Actual remaining(t)      = true final actual total - actual_cumulative(t)
    ("actual remaining via the repair model engine" — the true dollar
    amount of still-unrealized cost, known here only because the full
    mechanistic simulation was already run; in reality this wouldn't be
    knowable until each job finishes)
  - Credibility remaining(t) = credibility_cumulative(t) - actual_cumulative(t)
    (exactly "sum of credibility_estimate_total for still-incomplete
    properties at day t" by construction — the model's LIVE, evidence-
    updated view of what's left, which should visibly converge onto the
    actual-remaining line as evidence accumulates over the simulation)

Outputs: Output/cost_comparison.png, Output/cost_remaining.png.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless — this script saves a PNG, it doesn't show a window

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"

STATIC_TIMELINE_CSV = OUTPUT_DIR / "static_cost_timeline.csv"
PROPERTIES_CSV = OUTPUT_DIR / "properties.csv"
MARKET_TIMELINE_CSV = OUTPUT_DIR / "market_timeline.csv"
CREDIBILITY_TIMELINE_CSV = OUTPUT_DIR / "credibility_portfolio_timeline.csv"

FIGURE_PATH = OUTPUT_DIR / "cost_comparison.png"
REMAINING_FIGURE_PATH = OUTPUT_DIR / "cost_remaining.png"


# --------------------------------------------------------------------------
# Data loading (kept separate from plotting — see module docstring)
# --------------------------------------------------------------------------

def load_static_timeline() -> tuple[list[int], list[float]]:
    """(days, cumulative_static_spend_aud) from static_time.py's output."""
    days, cumulative = [], []
    with STATIC_TIMELINE_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            days.append(int(row["day"]))
            cumulative.append(float(row["cumulative_static_spend_aud"]))
    return days, cumulative


def load_repaired_properties() -> list[dict]:
    """Every property with a completed job_end_day + actual_repair_cost_aud."""
    rows = []
    with PROPERTIES_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            if row["job_end_day"] and row["actual_repair_cost_aud"]:
                rows.append({
                    "job_end_day": int(row["job_end_day"]),
                    "actual_repair_cost_aud": float(row["actual_repair_cost_aud"]),
                })
    return rows


def compute_actual_cumulative(repaired: list[dict], max_day: int) -> tuple[list[int], list[float]]:
    """Cumulative STEP function: for each day t, sum actual_repair_cost_aud
    across every property whose job_end_day <= t — dollars only become real
    once that property's job actually finishes, not spread out like the
    static model."""
    cost_by_end_day: dict[int, float] = {}
    for row in repaired:
        cost_by_end_day[row["job_end_day"]] = cost_by_end_day.get(row["job_end_day"], 0.0) + row["actual_repair_cost_aud"]

    days = list(range(0, max_day + 1))
    cumulative = []
    running = 0.0
    for day in days:
        running += cost_by_end_day.get(day, 0.0)
        cumulative.append(running)
    return days, cumulative


def load_credibility_timeline() -> tuple[list[int], list[float]]:
    """(days, cumulative_credibility_estimate) from credibility_model.py's
    portfolio-level output — realized actuals for completed properties plus
    the Bühlmann-blended estimate for everything still incomplete, at each
    evaluation day."""
    days, cumulative = [], []
    with CREDIBILITY_TIMELINE_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            days.append(int(row["day"]))
            cumulative.append(float(row["cumulative_credibility_estimate"]))
    return days, cumulative


def load_market_timeline() -> tuple[list[int], list[bool]] | None:
    """Optional overlay: is_stocked_out, shaded for context. Not required
    for the core chart — skipped gracefully if the file isn't there."""
    if not MARKET_TIMELINE_CSV.exists():
        return None
    days, stocked_out = [], []
    with MARKET_TIMELINE_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            days.append(int(row["day"]))
            stocked_out.append(row["is_stocked_out"] == "True")
    return days, stocked_out


def compute_remaining_series(
    static_cumulative: list[float],
    actual_days: list[int], actual_cumulative: list[float],
    credibility_days: list[int], credibility_cumulative: list[float],
) -> tuple[list[float], list[float], list[float]]:
    """(remaining_static, remaining_actual, remaining_credibility), aligned
    to static_days/actual_days/credibility_days respectively — see module
    docstring for the definition of each. credibility_days is coarser
    (every EVAL_INTERVAL_DAYS from credibility_model.py, not daily), so its
    remaining series is looked up against the daily actual series by day,
    not zipped positionally."""
    static_total = static_cumulative[-1]
    actual_total = actual_cumulative[-1]

    remaining_static = [static_total - v for v in static_cumulative]
    remaining_actual = [actual_total - v for v in actual_cumulative]

    actual_by_day = dict(zip(actual_days, actual_cumulative))
    remaining_credibility = [c - actual_by_day[d] for d, c in zip(credibility_days, credibility_cumulative)]

    return remaining_static, remaining_actual, remaining_credibility


# --------------------------------------------------------------------------
# Plotting
# --------------------------------------------------------------------------

def plot_comparison(
    static_days: list[int],
    static_cumulative: list[float],
    actual_days: list[int],
    actual_cumulative: list[float],
    credibility_days: list[int],
    credibility_cumulative: list[float],
    market_timeline: tuple[list[int], list[bool]] | None,
) -> Figure:
    fig, ax = plt.subplots(figsize=(12, 7))

    ax.plot(static_days, static_cumulative, label="Static estimate (time-distributed)", color="#4C72B0", linewidth=2)
    ax.step(actual_days, actual_cumulative, where="post", label="Actual (as jobs complete)", color="#C44E52", linewidth=2)
    ax.step(credibility_days, credibility_cumulative, where="post", label="Credibility-updated estimate", color="#55A868", linewidth=2)

    if market_timeline is not None:
        days, stocked_out = market_timeline
        stockout_start = next((d for d, s in zip(days, stocked_out) if s), None)
        if stockout_start is not None:
            ax.axvspan(stockout_start, max(days), color="grey", alpha=0.08, label="Stocked out (logistics premium active)")

    ax.set_xlabel("Day since flood peak")
    ax.set_ylabel("Cumulative repair cost (AUD)")
    ax.set_title("Lismore 2022 Flood — Static Estimate vs Actual Repair Cost Over Time")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x/1e6:.0f}M"))
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def plot_remaining(
    static_days: list[int], remaining_static: list[float],
    actual_days: list[int], remaining_actual: list[float],
    credibility_days: list[int], remaining_credibility: list[float],
) -> Figure:
    fig, ax = plt.subplots(figsize=(12, 7))

    ax.plot(static_days, remaining_static, label="Static remaining (time-distributed schedule)", color="#4C72B0", linewidth=2)
    ax.step(actual_days, remaining_actual, where="post", label="Actual remaining (repair model engine, true)", color="#C44E52", linewidth=2)
    ax.step(credibility_days, remaining_credibility, where="post", label="Credibility remaining (dynamic, evidence-updated)", color="#55A868", linewidth=2)

    ax.set_xlabel("Day since flood peak")
    ax.set_ylabel("Remaining repair cost (AUD)")
    ax.set_title("Lismore 2022 Flood — Remaining Repair Cost Over Time")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x/1e6:.0f}M"))
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    ax.axhline(0, color="black", linewidth=0.8)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    print(f"Loading {STATIC_TIMELINE_CSV.name} ...")
    static_days, static_cumulative = load_static_timeline()

    print(f"Loading {PROPERTIES_CSV.name} ...")
    repaired = load_repaired_properties()
    print(f"  {len(repaired):,} completed repairs")

    max_day = max(static_days[-1], max((r["job_end_day"] for r in repaired), default=0))
    actual_days, actual_cumulative = compute_actual_cumulative(repaired, max_day)

    print(f"Loading {CREDIBILITY_TIMELINE_CSV.name} ...")
    credibility_days, credibility_cumulative = load_credibility_timeline()

    print(f"Loading {MARKET_TIMELINE_CSV.name} (optional overlay) ...")
    market_timeline = load_market_timeline()

    print("Plotting ...")
    fig = plot_comparison(
        static_days, static_cumulative,
        actual_days, actual_cumulative,
        credibility_days, credibility_cumulative,
        market_timeline,
    )

    fig.savefig(FIGURE_PATH, dpi=150)
    print(f"Saved {FIGURE_PATH}")

    print("\n=== Summary ===")
    print(f"Final static cumulative (day {static_days[-1]}):        ${static_cumulative[-1]:,.0f}")
    print(f"Final actual cumulative (day {actual_days[-1]}):        ${actual_cumulative[-1]:,.0f}")
    print(f"Final credibility cumulative (day {credibility_days[-1]}): ${credibility_cumulative[-1]:,.0f}")
    print(f"Actual vs static: ${actual_cumulative[-1] - static_cumulative[-1]:,.0f} "
          f"({(actual_cumulative[-1]/static_cumulative[-1] - 1):+.1%})")
    print(f"Credibility vs actual (final, should be ~0 — everyone's completed by then): "
          f"${credibility_cumulative[-1] - actual_cumulative[-1]:,.0f}")

    print("\nComputing remaining-cost series ...")
    remaining_static, remaining_actual, remaining_credibility = compute_remaining_series(
        static_cumulative, actual_days, actual_cumulative, credibility_days, credibility_cumulative
    )

    print("Plotting remaining cost ...")
    remaining_fig = plot_remaining(
        static_days, remaining_static,
        actual_days, remaining_actual,
        credibility_days, remaining_credibility,
    )
    remaining_fig.savefig(REMAINING_FIGURE_PATH, dpi=150)
    print(f"Saved {REMAINING_FIGURE_PATH}")

    print(f"\nDay 0 remaining: static ${remaining_static[0]:,.0f} / actual ${remaining_actual[0]:,.0f} / "
          f"credibility ${remaining_credibility[0]:,.0f}")
    print(f"Final remaining (should be ~$0 for all three): static ${remaining_static[-1]:,.0f} / "
          f"actual ${remaining_actual[-1]:,.0f} / credibility ${remaining_credibility[-1]:,.0f}")
    min_credibility_remaining = min(remaining_credibility)
    print(f"Min credibility remaining observed: ${min_credibility_remaining:,.0f} "
          f"({'OK, never negative' if min_credibility_remaining >= 0 else 'NEGATIVE — investigate'})")


if __name__ == "__main__":
    main()
