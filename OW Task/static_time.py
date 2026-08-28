"""Distributes the ALREADY-COMPUTED static cost per property
(initial_estimated_cost_aud, from static_pricing.py) across time, using the
SAME job_start_day/job_end_day timeline Repair Model/Data/simulator.py
already computed via repair_queue.py — read directly from properties.csv/
properties.geojson, not recomputed here. This adds no new randomness; it is
a pure time-redistribution of an existing total.

Logic: 20% of a property's initial_estimated_cost_aud is "spent" on
job_start_day, the remaining 80% on job_end_day.

INVARIANT: summing every property's 20%+80% split always equals exactly
initial_estimated_cost_aud — no cost is created or destroyed by spreading it
over time, only reassigned to when it lands. Verified explicitly at the end
(this is the whole point: the final cumulative total must match the
portfolio's static total exactly, or this model is wrong).

Output: static_cost_timeline.csv (Output/) — one row per day across the full
simulation horizon: day, daily_static_spend_aud, cumulative_static_spend_aud.
This is the "static" line for the later cost-accrual visualization (static
vs credibility vs actual over time) — its counterpart is Repair
Model/Data/market_timeline.csv (the "actual"/market side of that chart).

Requires job_start_day and job_end_day to already be populated on every
flooded property — i.e. run this after Repair Model/Data/simulator.py.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"
GEOJSON_FILE = OUTPUT_DIR / "properties.geojson"
TIMELINE_CSV = OUTPUT_DIR / "static_cost_timeline.csv"

JOB_START_SPEND_FRACTION = 0.20
JOB_END_SPEND_FRACTION = 0.80

MILESTONE_DAYS = [30, 90, 180, 365]


def load_flooded_properties() -> list[dict]:
    data = json.loads(GEOJSON_FILE.read_text())
    return [f["properties"] for f in data["features"] if (f["properties"].get("initial_estimated_cost_aud") or 0) > 0]


def check_required_fields(rows: list[dict]) -> None:
    missing = [r["property_id"] for r in rows if r.get("job_start_day") is None or r.get("job_end_day") is None]
    if missing:
        print("ABORTING: required inputs are missing.")
        print(f"  {len(missing):,} flooded properties have no job_start_day/job_end_day "
              f"(e.g. {missing[:5]}) — run Repair Model/Data/simulator.py first.")
        sys.exit(1)


def build_daily_spend(rows: list[dict]) -> dict[int, float]:
    """20% of each property's static cost lands on job_start_day, the
    remaining 80% (computed as the exact remainder, not a second rounded
    fraction) lands on job_end_day — guarantees the two pieces always sum
    to exactly the original cost, cent for cent."""
    daily_spend: dict[int, float] = {}
    for row in rows:
        cost = row["initial_estimated_cost_aud"]
        start_day = row["job_start_day"]
        end_day = row["job_end_day"]

        start_spend = round(cost * JOB_START_SPEND_FRACTION, 2)
        end_spend = round(cost - start_spend, 2)  # exact remainder, not cost*0.8 separately rounded

        daily_spend[start_day] = daily_spend.get(start_day, 0.0) + start_spend
        daily_spend[end_day] = daily_spend.get(end_day, 0.0) + end_spend
    return daily_spend


def write_timeline_csv(daily_spend: dict[int, float]) -> list[dict]:
    max_day = max(daily_spend) if daily_spend else 0
    rows = []
    cumulative = 0.0
    with TIMELINE_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["day", "daily_static_spend_aud", "cumulative_static_spend_aud"])
        writer.writeheader()
        for day in range(0, max_day + 1):
            spend = round(daily_spend.get(day, 0.0), 2)
            cumulative = round(cumulative + spend, 2)
            row = {"day": day, "daily_static_spend_aud": spend, "cumulative_static_spend_aud": cumulative}
            writer.writerow(row)
            rows.append(row)
    return rows


def print_summary(rows: list[dict], timeline_rows: list[dict], total_static_cost: float) -> None:
    final_cumulative = timeline_rows[-1]["cumulative_static_spend_aud"] if timeline_rows else 0.0
    diff = abs(total_static_cost - final_cumulative)

    print("\n=== Summary ===")
    print(f"Flooded properties: {len(rows):,}")
    print(f"Simulation horizon: day 0-{timeline_rows[-1]['day'] if timeline_rows else 0}")
    print(f"Total initial_estimated_cost_aud (sum, unchanged by this script): ${total_static_cost:,.2f}")
    print(f"Final cumulative_static_spend_aud (end of this timeline):        ${final_cumulative:,.2f}")
    print(f"Reconciliation difference: ${diff:,.4f} "
          f"({'OK — exact match' if diff < 0.01 else 'MISMATCH — investigate, this should never happen'})")

    print("\nCumulative static spend at milestones:")
    by_day = {r["day"]: r["cumulative_static_spend_aud"] for r in timeline_rows}
    last_day = timeline_rows[-1]["day"] if timeline_rows else 0
    for milestone in MILESTONE_DAYS:
        if milestone > last_day:
            continue
        print(f"  day {milestone:>4}: ${by_day.get(milestone, 0):,.0f} "
              f"({by_day.get(milestone, 0)/total_static_cost:.1%} of total)")


def main() -> None:
    print(f"Loading {GEOJSON_FILE.name} ...")
    rows = load_flooded_properties()
    print(f"  {len(rows):,} flooded properties (initial_estimated_cost_aud > 0)")

    check_required_fields(rows)

    total_static_cost = sum(r["initial_estimated_cost_aud"] for r in rows)
    print(f"\nDistributing static cost over time: {JOB_START_SPEND_FRACTION:.0%} at job_start_day, "
          f"{JOB_END_SPEND_FRACTION:.0%} at job_end_day ...")
    daily_spend = build_daily_spend(rows)

    print(f"Writing {TIMELINE_CSV.name} ...")
    timeline_rows = write_timeline_csv(daily_spend)

    print_summary(rows, timeline_rows, total_static_cost)


if __name__ == "__main__":
    main()
