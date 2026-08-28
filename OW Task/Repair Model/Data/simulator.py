"""Orchestrates everything — combining all other Repair Model files into one
end-to-end "actual repair" simulation, grounded in the same base_pricing.py
numbers static_pricing.py uses.

RUN ORDER (respects dependencies):
  1. For every property with initial_estimated_cost_aud > 0, call
     repair_queue.py (which itself calls insurance_delay.py) to get
     insurance_eligible_day, job_start_day, job_duration_days, job_end_day.
  2. Using every property's job_start_day, run stock_depletion.py to
     determine the single townwide stockout_day.
  3. For every property, call scope_growth.py (using job_start_day) to get
     its scope_multiplier.
  4. For every property, call Market_price.py (using L_base/M_base derived
     from its static cost breakdown, job_end_day, stockout_day, and
     scope_multiplier) to get actual_repair_cost_aud plus the labour/
     materials sub-totals.

Outputs:
  A) repair_status.csv — one row per property per STATUS CHANGE (more
     compact than one row per property per day; every flooded property gets
     4 rows: awaiting_insurance @ day 0, in_queue @ insurance_eligible_day,
     in_progress @ job_start_day, completed @ job_end_day with
     actual_repair_cost_aud populated only on that row). Non-flooded
     properties never entered the repair pipeline, so they get a single
     permanent not_started @ day 0 row — this uses the full
     [not_started/awaiting_insurance/in_queue/in_progress/completed] enum
     as intended rather than only the 4 states that apply to flooded
     properties.
  B) properties.csv/properties.geojson updated with summary columns.
  C) market_timeline.csv — one row per day across the full simulation
     range: day, surge_intensity, cumulative_material_consumed,
     stock_remaining, is_stocked_out.

Re-validates every property against PropertyRecord before writing.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent                       # Repair Model/Data
ROOT_DIR = BASE_DIR.parent.parent                                  # OW Task
DATA_GEN_BUILDING_FILES = ROOT_DIR / "Data Generation" / "Building Files"
OUTPUT_DIR = ROOT_DIR / "Output"
ASSUMPTIONS_MD = ROOT_DIR / "SYNTHETIC_ASSUMPTIONS.md"

sys.path.insert(0, str(ROOT_DIR))                 # base_pricing.py
sys.path.insert(0, str(DATA_GEN_BUILDING_FILES))  # assembly.py, Pydantic.py
sys.path.insert(0, str(BASE_DIR))                 # sibling Repair Model/Data modules

import Market_price  # noqa: E402
import repair_queue  # noqa: E402
import scope_growth  # noqa: E402
import stock_depletion  # noqa: E402
from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402

STATUS_CSV = OUTPUT_DIR / "repair_status.csv"
MARKET_TIMELINE_CSV = OUTPUT_DIR / "market_timeline.csv"

MOLD_PENALTY_THRESHOLD_DAYS = scope_growth.MOLD_THRESHOLD_DAYS

REQUIRED_FLOODED_FIELDS = [
    "peak_depth_above_floor_m", "building_area_m2", "circuit_count", "wetted_wall_area_m2",
    "bedroom_count", "switchboard_cost_aud", "plasterboard_cost_aud", "flooring_cost_aud",
    "kitchen_cabinetry_cost_aud", "electrical_cost_aud", "appliance_cost_aud",
    "drying_decon_cost_aud", "painting_cost_aud", "demolition_cost_aud",
]


def check_required_inputs(flooded_rows: list[dict]) -> None:
    missing: dict[str, list[str]] = {}
    for field in REQUIRED_FLOODED_FIELDS:
        ids = [r["property_id"] for r in flooded_rows if r.get(field) is None]
        if ids:
            missing[field] = ids
    if missing:
        print("ABORTING: required inputs are missing on flooded properties.")
        for field, ids in missing.items():
            print(f"  {len(ids):,} properties have no {field} (e.g. {ids[:5]})")
        sys.exit(1)


# --------------------------------------------------------------------------
# Output A: repair_status.csv
# --------------------------------------------------------------------------

def write_repair_status_csv(all_rows: list[dict], flooded_ids: set[str]) -> None:
    with STATUS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["property_id", "day", "repair_status", "actual_repair_cost_aud"])
        writer.writeheader()
        for row in all_rows:
            pid = row["property_id"]
            if pid not in flooded_ids:
                writer.writerow({"property_id": pid, "day": 0, "repair_status": "not_started", "actual_repair_cost_aud": ""})
                continue

            writer.writerow({"property_id": pid, "day": 0, "repair_status": "awaiting_insurance", "actual_repair_cost_aud": ""})
            writer.writerow({
                "property_id": pid,
                "day": max(0, round(row["insurance_delay_days"])),
                "repair_status": "in_queue",
                "actual_repair_cost_aud": "",
            })
            writer.writerow({"property_id": pid, "day": row["job_start_day"], "repair_status": "in_progress", "actual_repair_cost_aud": ""})
            writer.writerow({
                "property_id": pid,
                "day": row["job_end_day"],
                "repair_status": "completed",
                "actual_repair_cost_aud": row["actual_repair_cost_aud"],
            })


# --------------------------------------------------------------------------
# Output C: market_timeline.csv
# --------------------------------------------------------------------------

def write_market_timeline_csv(stock_timeline: list[dict]) -> None:
    with MARKET_TIMELINE_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["day", "surge_intensity", "cumulative_material_consumed", "stock_remaining", "is_stocked_out"],
        )
        writer.writeheader()
        for row in stock_timeline:
            writer.writerow({
                "day": row["day"],
                "surge_intensity": round(Market_price.surge_intensity(row["day"]), 4),
                "cumulative_material_consumed": row["cumulative_material_consumed"],
                "stock_remaining": row["stock_remaining"],
                "is_stocked_out": row["is_stocked_out"],
            })


# --------------------------------------------------------------------------
# Validation + write B
# --------------------------------------------------------------------------

def validate_rows(rows: list[dict]) -> tuple[list[PropertyRecord], int]:
    records: list[PropertyRecord] = []
    errors = 0
    for row in rows:
        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")
    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")
    return records, errors


# --------------------------------------------------------------------------
# Mechanism-contribution decomposition
# --------------------------------------------------------------------------

def compute_mechanism_contributions(flooded_rows: list[dict], stockout_day: int | None) -> dict[str, float]:
    """Surge, stockout, and scope are each an ADDITIVE uplift fraction on a
    property's (L_base + M_base) baseline (see Market_price.py) — so unlike
    the original multiplicative spec, this decomposition is now EXACT: the
    three contributions below sum to exactly (total_actual - total_baseline),
    not just approximately via one-at-a-time counterfactuals."""
    total_baseline = 0.0
    total_actual = 0.0
    scope_contribution = 0.0
    surge_contribution = 0.0
    stockout_contribution = 0.0

    for row in flooded_rows:
        L_base, M_base = Market_price.compute_baselines(row)
        t = row["job_end_day"]
        intensity = Market_price.surge_intensity(t)
        stock_empty = 1.0 if (stockout_day is not None and t >= stockout_day) else 0.0
        scope_frac = row["scope_multiplier"] - 1.0
        baseline = L_base + M_base

        total_baseline += baseline
        total_actual += row["actual_repair_cost_aud"]
        scope_contribution += baseline * scope_frac
        surge_contribution += L_base * Market_price.M_PEAK_LABOUR * intensity + M_base * Market_price.M_PEAK_MATERIALS * intensity
        stockout_contribution += M_base * stock_empty * Market_price.L_PREMIUM * intensity

    return {
        "total_actual": total_actual,
        "total_baseline": total_baseline,
        "scope_contribution": scope_contribution,
        "stockout_contribution": stockout_contribution,
        "surge_contribution": surge_contribution,
    }


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------

def append_assumptions_doc(flooded_rows: list[dict], stockout_day: int | None, mechanism: dict[str, float]) -> None:
    total_initial = sum(r["initial_estimated_cost_aud"] for r in flooded_rows)
    total_actual = mechanism["total_actual"]

    lines = [
        "# Repair Simulation (Repair Model/Data/simulator.py)",
        "",
        "Appended by simulator.py — this section is regenerated (replaced) on every run.",
        "",
        "## What this is",
        "",
        "The \"actual\" repair simulation — a stochastic, time-aware companion to",
        "static_pricing.py's deterministic-ish point estimate. Both are grounded in the",
        "same base_pricing.py rate tables, but this simulation adds: insurance response",
        "delay, a Weibull-shaped repair queue rollout, six-stage sequential job duration,",
        "mold-driven scope growth for jobs that sit idle past the mold threshold, a townwide",
        "material stockout, and a Weibull-shaped market surge curve applied to labour and",
        "materials independently (a separate Weibull usage from the queue rollout curve —",
        "see repair_queue.py's module docstring vs Market_price.py's).",
        "",
        "Orchestrates: insurance_delay.py, repair_queue.py, stock_depletion.py,",
        "scope_growth.py, Market_price.py, base_pricing.py (all in Repair Model/Data/,",
        "except base_pricing.py at the project root).",
        "",
        "## Day 0 convention",
        "",
        "Day 0 = flood peak (28 Feb 2022) for this simulation's timeline — a DIFFERENT",
        "epoch from flood_hydrograph.py's interval_hour (hours since 24 Feb, the first",
        "minor-level exceedance). Not directly comparable to interval_hour.",
        "",
        "## Repair queue rollout (ESTIMATED weighting + curve constants)",
        "",
        "Queue timing uses inverse-transform sampling from a Weibull CDF, not a day-by-day",
        "capacity-matching simulation: each property gets a priority percentile",
        "`p(i) = percentile_rank(priority_score(i))` (0 = highest priority/done first,",
        "1 = lowest/done last), where",
        "`priority_score = 0.5*severity_percentile + 0.3*affluence_score + 0.2*Uniform(0,1)`,",
        "then `queue_day(i) = 230 * (-ln(1 - p(i))) ** (1/2.0)` (scale 230 days, Weibull shape",
        "2.0), and `job_start_day(i) = insurance_eligible_day(i) + queue_day(i)`. This",
        "guarantees the portfolio's cumulative rollout follows the target S-curve shape BY",
        "CONSTRUCTION rather than only on average. The 0.5/0.3/0.2 weighting and the",
        "shape/scale constants are this project's own estimate, not sourced; flagged as a",
        "modelling choice reflecting that trades plausibly prioritize both worse-damaged and",
        "more-affluent properties (plus irreducible real-world scheduling noise), not",
        "asserted as fair. See repair_queue.py's module docstring for the full derivation.",
        "",
        "## Mechanism contributions (exact additive decomposition)",
        "",
        "Surge, stockout, and scope are each applied as an ADDITIVE uplift fraction on a",
        "property's (L_base + M_base) baseline in Market_price.py — NOT as separate",
        "multiplicative factors stacked on top of each other. (An earlier version of this",
        "formula multiplied scope_multiplier onto the already-surged Labor(t)/Materials(t)",
        "total, which compounds independent effects instead of summing them: two",
        "independent 40% uplifts became 1.4x1.4=+96% instead of the correct +80%. Fixed —",
        "see Market_price.py's module docstring for the full explanation.) Because the",
        "three uplifts now share one additive baseline, this decomposition is EXACT: the",
        "three contributions below sum to exactly (total_actual - total_baseline), not an",
        "approximation.",
        "",
        f"- Total initial_estimated_cost_aud (flooded properties): ${total_initial:,.0f}",
        f"- Total pre-uplift market baseline (L_base + M_base): ${mechanism['total_baseline']:,.0f}",
        f"- Total actual_repair_cost_aud: ${total_actual:,.0f}",
        f"- Scope growth contribution: ${mechanism['scope_contribution']:,.0f}",
        f"- Market surge contribution: ${mechanism['surge_contribution']:,.0f}",
        f"- Stockout logistics contribution: ${mechanism['stockout_contribution']:,.0f}",
        "",
        f"Townwide material stockout day this run: {stockout_day}",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Repair Simulation (Repair Model/Data/simulator.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def _histogram_buckets(values: list[int], bucket_size: int = 30) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for v in values:
        lo = (v // bucket_size) * bucket_size
        key = f"{lo}-{lo + bucket_size - 1}"
        buckets[key] = buckets.get(key, 0) + 1
    return dict(sorted(buckets.items(), key=lambda kv: int(kv[0].split("-")[0])))


def print_summary(flooded_rows: list[dict], stockout_day: int | None, mechanism: dict[str, float]) -> None:
    total = len(flooded_rows)
    starts = [r["job_start_day"] for r in flooded_rows]
    ends = [r["job_end_day"] for r in flooded_rows]

    print("\n=== Summary ===")
    print(f"Flooded properties simulated: {total:,}")

    print("\njob_start_day distribution:")
    print(f"  min {min(starts)} / median {statistics.median(starts):.0f} / max {max(starts)}")
    print("  histogram (30-day buckets):")
    for bucket, count in _histogram_buckets(starts).items():
        print(f"    {bucket:>10s}: {count:>6,}")

    print("\njob_end_day distribution:")
    print(f"  min {min(ends)} / median {statistics.median(ends):.0f} / max {max(ends)}")
    print("  histogram (30-day buckets):")
    for bucket, count in _histogram_buckets(ends).items():
        print(f"    {bucket:>10s}: {count:>6,}")

    print(f"\nTownwide stockout_day: {stockout_day}")
    if stockout_day is not None:
        affected = sum(1 for r in flooded_rows if r["stockout_day_relevant"])
        print(f"  Properties whose job completed after stockout: {affected:,} / {total:,} ({affected/total:.1%})")
    else:
        print("  Stock never ran out within the simulated horizon.")

    mold_hit = [r for r in flooded_rows if r["job_start_day"] > MOLD_PENALTY_THRESHOLD_DAYS]
    print(f"\nProperties triggering mold scope penalty (job_start_day > {MOLD_PENALTY_THRESHOLD_DAYS}): "
          f"{len(mold_hit):,} / {total:,} ({len(mold_hit)/total:.1%})")
    if mold_hit:
        multipliers = [r["scope_multiplier"] for r in mold_hit]
        print(f"  scope_multiplier range among affected: {min(multipliers):.3f} - {max(multipliers):.3f}")

    total_initial = sum(r["initial_estimated_cost_aud"] for r in flooded_rows)
    total_actual = mechanism["total_actual"]
    ratios = [r["actual_repair_cost_aud"] / r["initial_estimated_cost_aud"] for r in flooded_rows if r["initial_estimated_cost_aud"] > 0]
    print(f"\nTotal initial_estimated_cost_aud: ${total_initial:,.0f}")
    print(f"Total actual_repair_cost_aud:     ${total_actual:,.0f}  ({(total_actual/total_initial - 1):+.1%} vs initial)")
    print(f"Per-property actual/initial ratio: mean {statistics.mean(ratios):.3f} / median {statistics.median(ratios):.3f}")

    total_labour = sum(r["labour_actual_aud"] for r in flooded_rows)
    total_materials = sum(r["materials_actual_aud"] for r in flooded_rows)
    print(f"\nTotal labour_actual_aud:    ${total_labour:,.0f} ({total_labour/(total_labour+total_materials):.1%})")
    print(f"Total materials_actual_aud: ${total_materials:,.0f} ({total_materials/(total_labour+total_materials):.1%})")

    # NOTE: the 3 mechanism contributions are exact additive uplifts on
    # total_baseline (L_base+M_base, the market-side pre-uplift baseline),
    # not on total_initial (static_pricing.py's own point estimate, which
    # carries its own independent per-component volatility draw) — so they
    # sum exactly to (total_actual - total_baseline), not to
    # (total_actual - total_initial). Both figures are shown for clarity.
    total_baseline = mechanism["total_baseline"]
    baseline_increase = total_actual - total_baseline
    print(f"\nTotal market baseline (L_base + M_base, pre-uplift): ${total_baseline:,.0f}")
    print(f"Mechanism contributions to the ${baseline_increase:,.0f} increase over that baseline (exact, sums to the total):")
    print(f"  Scope growth:        ${mechanism['scope_contribution']:,.0f} ({mechanism['scope_contribution']/baseline_increase:.1%})")
    print(f"  Market surge:        ${mechanism['surge_contribution']:,.0f} ({mechanism['surge_contribution']/baseline_increase:.1%})")
    print(f"  Stockout logistics:  ${mechanism['stockout_contribution']:,.0f} ({mechanism['stockout_contribution']/baseline_increase:.1%})")

    lower = [r for r in flooded_rows if r["actual_repair_cost_aud"] < r["initial_estimated_cost_aud"]]
    negative_or_nan = [
        r for r in flooded_rows
        if r["actual_repair_cost_aud"] < 0 or r["labour_actual_aud"] < 0 or r["materials_actual_aud"] < 0
    ]
    print(f"\nProperties where actual_repair_cost_aud < initial_estimated_cost_aud: {len(lower):,} / {total:,} "
          f"({len(lower)/total:.1%}) — expected occasionally, since static_pricing.py's per-component volatility "
          "and this simulation's L_base/M_base volatility are independent draws.")
    print(f"Properties with negative/nonsensical cost values: {len(negative_or_nan):,} (should be 0)")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    print(f"Loading {GEOJSON_OUT.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())
    rows = []
    for feature in geojson_data["features"]:
        row = dict(feature["properties"])
        row["longitude"] = feature["geometry"]["coordinates"][0]
        row["latitude"] = feature["geometry"]["coordinates"][1]
        rows.append(row)
    print(f"  {len(rows):,} properties loaded")

    flooded_rows = [r for r in rows if (r.get("initial_estimated_cost_aud") or 0) > 0]
    flooded_ids = {r["property_id"] for r in flooded_rows}
    print(f"  {len(flooded_rows):,} properties flooded above floor (initial_estimated_cost_aud > 0)")

    check_required_inputs(flooded_rows)

    print("\nStep 1: insurance delay + repair queue timelines ...")
    timelines = repair_queue.compute_job_timelines(flooded_rows)
    for row in flooded_rows:
        t = timelines[row["property_id"]]
        row["insurance_delay_days"] = t["insurance_eligible_day"]
        row["job_start_day"] = t["job_start_day"]
        row["job_duration_days"] = t["job_duration_days"]
        row["job_end_day"] = t["job_end_day"]

    print("Step 2: townwide material stockout ...")
    max_day = max(r["job_end_day"] for r in flooded_rows)
    job_start_days = [r["job_start_day"] for r in flooded_rows]
    stock_timeline = stock_depletion.compute_stock_timeline(job_start_days, max_day)
    stockout_day = stock_depletion.find_stockout_day(stock_timeline)
    print(f"  stockout_day = {stockout_day}  (simulation horizon: 0-{max_day})")

    print("Step 3: scope multiplier per property ...")
    for row in flooded_rows:
        row["scope_multiplier"] = round(scope_growth.compute_scope_multiplier(row["job_start_day"]), 4)

    print("Step 4: market-adjusted actual repair cost per property ...")
    for row in flooded_rows:
        L_base, M_base = Market_price.compute_baselines(row)
        actual_cost, labour_actual, materials_actual = Market_price.compute_actual_cost(
            L_base, M_base, row["job_end_day"], stockout_day, row["scope_multiplier"]
        )
        row["labour_actual_aud"] = round(labour_actual, 2)
        row["materials_actual_aud"] = round(materials_actual, 2)
        row["actual_repair_cost_aud"] = round(actual_cost, 2)
        row["stockout_day_relevant"] = bool(stockout_day is not None and row["job_end_day"] > stockout_day)

    print(f"\nWriting {STATUS_CSV.name} ...")
    write_repair_status_csv(rows, flooded_ids)

    print(f"Writing {MARKET_TIMELINE_CSV.name} ({len(stock_timeline)} rows) ...")
    write_market_timeline_csv(stock_timeline)

    print("\nValidating PropertyRecord rows ...")
    records, _errors = validate_rows(rows)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    print("\nComputing mechanism contributions (surge / scope / stockout) ...")
    mechanism = compute_mechanism_contributions(flooded_rows, stockout_day)

    append_assumptions_doc(flooded_rows, stockout_day, mechanism)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(flooded_rows, stockout_day, mechanism)


if __name__ == "__main__":
    main()
