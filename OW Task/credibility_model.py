"""Bühlmann credibility price-adjustment engine.

Produces an updated cost estimate for each not-yet-repaired property,
evaluated at any chosen "now", by blending its static estimate with a
robust, weighted signal drawn from nearby/similar properties' ACTUAL
completed repair costs — per cost component, with spatial/characteristic/
recency weighting and explicit outlier protection.

This is the OBSERVATIONAL/INFERENTIAL counterpart to
Repair Model/Data/Market_price.py (the MECHANISTIC model — surge curves,
stockout logistics, log-normal volatility). This file never looks at those
mechanisms directly, only at their OUTCOME (actual_repair_cost_aud on
completed jobs), and tries to predict costs for still-unrepaired properties
purely from evidence — same as it would need to in reality.

Per-component actual costs (needed for observed_ratio_c(j), but never
persisted by the repair simulation — Market_price.py only kept the
aggregate labour_actual_aud/materials_actual_aud): derived here by
allocating those two aggregates back down to each of the 9 components,
weighted by that component's labour/material STATIC baseline share
(base_pricing.LABOUR_MATERIAL_SPLIT). No new randomness is introduced, and
this guarantees sum(actual_component_cost) == labour_actual_aud +
materials_actual_aud == actual_repair_cost_aud exactly — components still
end up with genuinely different ratios since they differ in how
labour/material-heavy they are, and labour vs materials carry different
surge/stockout uplifts.

Outputs (Output/):
  - credibility_timeline.csv: property_id, day, credibility_estimate_total,
    Z_avg — for every property still incomplete, at each evaluation day
    (every 7 days across the simulation).
  - credibility_portfolio_timeline.csv: day, cumulative_actual_realized,
    cumulative_credibility_estimate — the townwide aggregate, feeding the
    third line on cost_comparison.py's chart (its forward-compatibility
    note anticipated exactly this).

Requires the full repair simulation to already have run (job_start_day,
job_end_day, actual_repair_cost_aud, labour_actual_aud, materials_actual_aud,
and the 9 static cost-breakdown columns already populated).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"
GEOJSON_FILE = OUTPUT_DIR / "properties.geojson"
TIMELINE_CSV = OUTPUT_DIR / "credibility_timeline.csv"
PORTFOLIO_CSV = OUTPUT_DIR / "credibility_portfolio_timeline.csv"
ASSUMPTIONS_MD = BASE_DIR / "SYNTHETIC_ASSUMPTIONS.md"

sys.path.insert(0, str(BASE_DIR))                              # base_pricing.py
sys.path.insert(0, str(BASE_DIR / "Repair Model" / "Data"))    # scope_growth.py

from base_pricing import LABOUR_MATERIAL_SPLIT  # noqa: E402
import scope_growth  # noqa: E402

EVAL_INTERVAL_DAYS = 7
MIN_SAMPLE_FOR_LATE_NOW = 50  # validation-reporting threshold — see main()'s late_now_day comment

DISTANCE_RANGE_KM = 15.0        # ESTIMATED — wide relative to the ~9km real max distance, a gentle tilt not a cliff
RECENCY_HALFLIFE_DAYS = 30.0    # ESTIMATED
BUHLMANN_K = 250.0               # ESTIMATED — calibration constant, higher = more conservative
OUTLIER_Z_THRESHOLD = 3.0       # robust-MAD z-score beyond which outlier_dampener starts reducing weight

COMPONENT_COST_FIELDS = {
    "switchboard": "switchboard_cost_aud",
    "plasterboard": "plasterboard_cost_aud",
    "flooring": "flooring_cost_aud",
    "kitchen_cabinetry": "kitchen_cabinetry_cost_aud",
    "electrical": "electrical_cost_aud",
    "appliances": "appliance_cost_aud",
    "drying_decon": "drying_decon_cost_aud",
    "painting": "painting_cost_aud",
    "demolition": "demolition_cost_aud",
}

SIMILARITY_VARIABLES = {
    "flooring": ["flooring", "construction_type", "suburb"],
    "switchboard": ["switchboard_type", "construction_type", "suburb"],
    "electrical": ["switchboard_type", "construction_type", "suburb"],
    "painting": ["construction_type", "area_bucket", "suburb"],
    "plasterboard": ["construction_type", "area_bucket", "suburb"],
    "kitchen_cabinetry": ["kitchen_spec", "affluence_tercile", "suburb"],
    "appliances": ["kitchen_spec", "affluence_tercile", "suburb"],
    "drying_decon": ["area_bucket", "suburb"],
    "demolition": ["area_bucket", "construction_type", "suburb"],
}

REQUIRED_FIELDS = [
    "job_start_day", "job_end_day", "actual_repair_cost_aud", "labour_actual_aud", "materials_actual_aud",
    "latitude", "longitude", "suburb", "construction_type", "flooring", "switchboard_type", "kitchen_spec",
    "affluence_score", "building_area_m2",
] + list(COMPONENT_COST_FIELDS.values())


# --------------------------------------------------------------------------
# Loading + derived fields
# --------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    lat1r, lon1r, lat2r, lon2r = np.radians(lat1), np.radians(lon1), np.radians(lat2), np.radians(lon2)
    dlat = lat2r - lat1r
    dlon = lon2r - lon1r
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def area_bucket(area_m2: float) -> str:
    if area_m2 < 90:
        return "<90"
    if area_m2 <= 180:
        return "90-180"
    return ">180"


def affluence_tercile(score: float | None) -> str:
    if score is None:
        return "mid"
    if score < 0.33:
        return "low"
    if score <= 0.66:
        return "mid"
    return "high"


def load_flooded_properties() -> list[dict]:
    data = json.loads(GEOJSON_FILE.read_text())
    rows = []
    for feature in data["features"]:
        props = dict(feature["properties"])
        if (props.get("initial_estimated_cost_aud") or 0) <= 0:
            continue
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]
        rows.append(props)
    return rows


def check_required_fields(rows: list[dict]) -> None:
    missing: dict[str, list[str]] = {}
    for field in REQUIRED_FIELDS:
        ids = [r["property_id"] for r in rows if r.get(field) is None]
        if ids:
            missing[field] = ids
    if missing:
        print("ABORTING: required inputs are missing.")
        for field, ids in missing.items():
            print(f"  {len(ids):,} properties have no {field} (e.g. {ids[:5]})")
        sys.exit(1)


def compute_actual_component_costs(row: dict) -> dict[str, float]:
    labour_base, material_base = {}, {}
    for component, field in COMPONENT_COST_FIELDS.items():
        static_cost = row.get(field) or 0.0
        l_frac, m_frac = LABOUR_MATERIAL_SPLIT[component]
        labour_base[component] = static_cost * l_frac
        material_base[component] = static_cost * m_frac
    total_l = sum(labour_base.values())
    total_m = sum(material_base.values())
    labour_actual = row.get("labour_actual_aud") or 0.0
    materials_actual = row.get("materials_actual_aud") or 0.0

    result = {}
    for component in COMPONENT_COST_FIELDS:
        l_share = labour_base[component] / total_l if total_l > 0 else 0.0
        m_share = material_base[component] / total_m if total_m > 0 else 0.0
        result[component] = l_share * labour_actual + m_share * materials_actual
    return result


def attach_derived_fields(rows: list[dict]) -> None:
    for row in rows:
        row["area_bucket"] = area_bucket(row["building_area_m2"])
        row["affluence_tercile"] = affluence_tercile(row.get("affluence_score"))
        row["_actual_component_costs"] = compute_actual_component_costs(row)


# --------------------------------------------------------------------------
# Core per-"now" evaluation (vectorized with numpy — see module docstring)
# --------------------------------------------------------------------------

def compute_self_scope_multiplier(job_start_day: int, now: int) -> float:
    """Step 6 — property i's OWN idle time, reusing scope_growth.py's exact
    tuned constants (mold_threshold_days=18, growth_rate=0.02, cap=0.40) —
    not a second version of that formula."""
    days_idle = job_start_day if job_start_day <= now else now
    return scope_growth.compute_scope_multiplier(days_idle)


def evaluate_now(now: int, completed: list[dict], incomplete: list[dict]) -> tuple[dict[str, float], dict[str, float], dict[str, int]]:
    """Returns (credibility_estimate_total by property_id, Z_avg by
    property_id, outlier_dampened_count by component) for every property in
    `incomplete`, at evaluation time `now`. Handles zero completed evidence
    (per component, or entirely) by falling back to the pure static
    estimate — see module docstring's cold-start handling."""
    n_i = len(incomplete)
    lat_i = np.array([r["latitude"] for r in incomplete])
    lon_i = np.array([r["longitude"] for r in incomplete])

    self_scope = np.array([compute_self_scope_multiplier(r["job_start_day"], now) for r in incomplete])

    estimate_total = np.zeros(n_i)
    z_sum = np.zeros(n_i)
    outlier_dampened_counts: dict[str, int] = {}

    lat_j_all = np.array([r["latitude"] for r in completed]) if completed else np.array([])
    lon_j_all = np.array([r["longitude"] for r in completed]) if completed else np.array([])
    end_day_j_all = np.array([r["job_end_day"] for r in completed]) if completed else np.array([])
    recency_all = np.exp(-(now - end_day_j_all) / RECENCY_HALFLIFE_DAYS) if completed else np.array([])

    if completed:
        dist_all = haversine_km(lat_i[:, None], lon_i[:, None], lat_j_all[None, :], lon_j_all[None, :])
        w_distance_all = np.exp(-dist_all / DISTANCE_RANGE_KM)
    else:
        w_distance_all = np.zeros((n_i, 0))

    for component, field in COMPONENT_COST_FIELDS.items():
        static_i = np.array([r.get(field) or 0.0 for r in incomplete])

        static_j_all = np.array([r.get(field) or 0.0 for r in completed]) if completed else np.array([])
        valid_mask = static_j_all > 0

        if not valid_mask.any():
            # no comparable evidence at all for this component -> Z=0 for
            # everyone -> falls back to the pure static estimate exactly
            estimate_total += static_i * self_scope
            outlier_dampened_counts[component] = 0
            continue

        idx = np.where(valid_mask)[0]
        static_j = static_j_all[idx]
        actual_j = np.array([completed[k]["_actual_component_costs"][component] for k in idx])
        ratio_j = actual_j / static_j

        med = np.median(ratio_j)
        mad = np.median(np.abs(ratio_j - med))
        z_robust = 0.6745 * (ratio_j - med) / max(mad, 1e-6)
        outlier_dampener = 1.0 / (1.0 + np.maximum(0, np.abs(z_robust) - OUTLIER_Z_THRESHOLD))
        outlier_dampened_counts[component] = int((outlier_dampener < 0.999).sum())

        recency = recency_all[idx]
        w_distance = w_distance_all[:, idx]

        sim_vars = SIMILARITY_VARIABLES[component]
        char_sum = np.zeros((n_i, len(idx)))
        for var in sim_vars:
            vals_i = np.array([r[var] for r in incomplete], dtype=object)
            vals_j = np.array([completed[k][var] for k in idx], dtype=object)
            char_sum += (vals_i[:, None] == vals_j[None, :]).astype(float)
        w_characteristic = char_sum / len(sim_vars)

        w = w_distance * w_characteristic * recency[None, :] * outlier_dampener[None, :]
        n_arr = w.sum(axis=1)

        sort_order = np.argsort(ratio_j)
        ratio_sorted = ratio_j[sort_order]
        w_sorted = w[:, sort_order]
        cumsum_w = np.cumsum(w_sorted, axis=1)
        total_w = cumsum_w[:, -1]
        half = np.where(total_w > 0, total_w / 2.0, np.inf)
        median_idx = np.argmax(cumsum_w >= half[:, None], axis=1)
        r_c = ratio_sorted[median_idx]

        z = n_arr / (n_arr + BUHLMANN_K)

        market_adjusted = z * r_c * static_i + (1 - z) * static_i
        estimate_total += market_adjusted * self_scope
        z_sum += z

    z_avg = z_sum / len(COMPONENT_COST_FIELDS)

    estimate_by_id = {r["property_id"]: float(estimate_total[k]) for k, r in enumerate(incomplete)}
    z_avg_by_id = {r["property_id"]: float(z_avg[k]) for k, r in enumerate(incomplete)}
    return estimate_by_id, z_avg_by_id, outlier_dampened_counts


# --------------------------------------------------------------------------
# Cold-start test (per spec: confirm zero comparable jobs never divides by zero)
# --------------------------------------------------------------------------

def run_cold_start_test(rows: list[dict]) -> None:
    print("Running cold-start test (zero completed jobs) ...")
    sample = rows[:50]
    estimate_by_id, z_avg_by_id, _ = evaluate_now(now=0, completed=[], incomplete=sample)
    for row in sample:
        pid = row["property_id"]
        assert z_avg_by_id[pid] == 0.0, f"expected Z_avg=0 with no evidence, got {z_avg_by_id[pid]} for {pid}"
        expected = row["initial_estimated_cost_aud"] * compute_self_scope_multiplier(row["job_start_day"], 0)
        # tolerance accounts for legitimate per-component 2dp rounding accumulated
        # across 9 summed components (static_pricing.py rounds each component
        # individually, so their sum can differ from initial_estimated_cost_aud
        # by a few cents) — not a bug, just floating-point rounding.
        assert abs(estimate_by_id[pid] - expected) < 0.10, (
            f"cold-start estimate should equal static*self_scope (within rounding) for {pid}: "
            f"got {estimate_by_id[pid]}, expected {expected}"
        )
    print(f"  OK — {len(sample)} properties with zero comparable jobs all fell back to the static estimate, no errors.")


# --------------------------------------------------------------------------
# Main loop across evaluation days
# --------------------------------------------------------------------------

def main() -> None:
    print(f"Loading {GEOJSON_FILE.name} ...")
    rows = load_flooded_properties()
    print(f"  {len(rows):,} flooded properties")

    check_required_fields(rows)
    attach_derived_fields(rows)

    run_cold_start_test(rows)

    max_day = max(r["job_end_day"] for r in rows)
    eval_days = list(range(0, max_day + 1, EVAL_INTERVAL_DAYS))
    if eval_days[-1] != max_day:
        eval_days.append(max_day)
    print(f"\nEvaluating credibility estimates every {EVAL_INTERVAL_DAYS} days across 0-{max_day} ({len(eval_days)} evaluation points) ...")

    total_actual_final = sum(r["actual_repair_cost_aud"] for r in rows)
    total_static_final = sum(r["initial_estimated_cost_aud"] for r in rows)

    timeline_rows = []
    portfolio_rows = []
    # "Late now" validation checkpoint: the LATEST evaluation day that still
    # has a reasonably-sized incomplete population — not literally the last
    # non-empty day, since the tail end of this simulation degenerates to a
    # handful of outlier mega-buildings (job_end_day up to 630) once the
    # bulk of the portfolio (~6,436/6,446) has already finished by ~day 350;
    # picking literally the last incomplete day would report a Z/outlier
    # "distribution" over just 1-2 unrepresentative stragglers.
    late_now_day = None
    late_now_z_values: list[float] = []
    late_now_outlier_counts: dict[str, int] = {}
    late_now_incomplete_count = 0

    for now in eval_days:
        completed = [r for r in rows if r["job_end_day"] <= now]
        incomplete = [r for r in rows if r["job_end_day"] > now]
        cumulative_actual = sum(r["actual_repair_cost_aud"] for r in completed)

        if incomplete:
            estimate_by_id, z_avg_by_id, outlier_counts = evaluate_now(now, completed, incomplete)
            cumulative_credibility = cumulative_actual + sum(estimate_by_id.values())
            for row in incomplete:
                pid = row["property_id"]
                timeline_rows.append({
                    "property_id": pid,
                    "day": now,
                    "credibility_estimate_total": round(estimate_by_id[pid], 2),
                    "Z_avg": round(z_avg_by_id[pid], 4),
                })
            if len(incomplete) >= MIN_SAMPLE_FOR_LATE_NOW:
                late_now_day = now
                late_now_z_values = list(z_avg_by_id.values())
                late_now_outlier_counts = outlier_counts
                late_now_incomplete_count = len(incomplete)
        else:
            cumulative_credibility = cumulative_actual

        portfolio_rows.append({
            "day": now,
            "cumulative_actual_realized": round(cumulative_actual, 2),
            "cumulative_credibility_estimate": round(cumulative_credibility, 2),
        })

        if now % 70 == 0 or now == eval_days[-1]:
            print(f"  day {now:>4}: {len(completed):>5,} completed, {len(incomplete):>5,} incomplete, "
                  f"cumulative_credibility_estimate=${cumulative_credibility:,.0f}")

    print(f"\nWriting {TIMELINE_CSV.name} ({len(timeline_rows):,} rows) ...")
    with TIMELINE_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["property_id", "day", "credibility_estimate_total", "Z_avg"])
        writer.writeheader()
        writer.writerows(timeline_rows)

    print(f"Writing {PORTFOLIO_CSV.name} ({len(portfolio_rows):,} rows) ...")
    with PORTFOLIO_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["day", "cumulative_actual_realized", "cumulative_credibility_estimate"])
        writer.writeheader()
        writer.writerows(portfolio_rows)

    append_assumptions_doc(late_now_day, late_now_z_values, late_now_outlier_counts, late_now_incomplete_count, portfolio_rows, total_actual_final, total_static_final)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(late_now_day, late_now_z_values, late_now_outlier_counts, late_now_incomplete_count, portfolio_rows, total_actual_final, total_static_final)


# --------------------------------------------------------------------------
# Documentation + reporting
# --------------------------------------------------------------------------

def _convergence_checkpoints(portfolio_rows: list[dict], total_actual_final: float) -> list[str]:
    lines = []
    n = len(portfolio_rows)
    for frac in (0.25, 0.5, 0.75, 1.0):
        idx = min(n - 1, int(n * frac) - 1) if n else 0
        row = portfolio_rows[idx]
        gap = row["cumulative_credibility_estimate"] - total_actual_final
        lines.append(
            f"  day {row['day']:>4}: credibility estimate ${row['cumulative_credibility_estimate']:,.0f} "
            f"vs final actual ${total_actual_final:,.0f} (gap ${gap:,.0f}, {gap/total_actual_final:+.1%})"
        )
    return lines


def append_assumptions_doc(
    late_now_day: int | None, late_now_z_values: list[float], late_now_outlier_counts: dict[str, int],
    late_now_incomplete_count: int, portfolio_rows: list[dict], total_actual_final: float, total_static_final: float,
) -> None:
    lines = [
        "# Credibility Model (credibility_model.py)",
        "",
        "Appended by credibility_model.py — this section is regenerated (replaced) on every run.",
        "",
        "## What this is",
        "",
        "Bühlmann credibility blend of the static estimate with a weighted signal from nearby/",
        "similar completed properties' actual costs, per cost component, re-evaluated at every",
        f"{EVAL_INTERVAL_DAYS}-day interval across the simulation. This is the OBSERVATIONAL",
        "counterpart to Repair Model/Data/Market_price.py's MECHANISTIC model — it never looks at",
        "surge/stockout/volatility directly, only at completed jobs' realized outcomes.",
        "",
        "## Per-component actual costs (not natively persisted — derived here)",
        "",
        "Market_price.py/simulator.py only persisted the AGGREGATE labour_actual_aud/",
        "materials_actual_aud per property, not a true per-component actual cost. This script",
        "allocates those two aggregates back down to each of the 9 components, weighted by that",
        "component's labour/material STATIC baseline share (base_pricing.LABOUR_MATERIAL_SPLIT).",
        "No new randomness — guarantees sum(actual_component_cost) == actual_repair_cost_aud",
        "exactly, while still giving each component a genuinely different ratio (components differ",
        "in labour/material mix, and labour vs materials carry different surge/stockout uplifts).",
        "",
        "## Similarity variables per component",
        "",
        "| Component | Similarity variables |",
        "|---|---|",
    ] + [f"| {c} | {', '.join(v)} |" for c, v in SIMILARITY_VARIABLES.items()] + [
        "",
        "`area_bucket`: fixed bands <90 / 90-180 / >180 sqm. `affluence_tercile`: the same",
        "low(<0.33)/mid/high(>0.66) cut already used throughout this pipeline (affluence_score).",
        "",
        "## Tuned constants (ESTIMATED, not sourced)",
        "",
        f"- Distance range: {DISTANCE_RANGE_KM} km — deliberately wide relative to the ~9km real",
        "  max distance in the dataset, so distance is a gentle tilt, not a cliff.",
        f"- Recency half-life: {RECENCY_HALFLIFE_DAYS} days — how fast trust in a completed",
        "  property's price signal decays as it ages relative to \"now\".",
        f"- Bühlmann k = {BUHLMANN_K}: calibration constant: Z(i) = n(i)/(n(i)+k). Higher k means",
        "  more evidence mass is needed before the observed signal outweighs the static prior.",
        f"- Outlier threshold: {OUTLIER_Z_THRESHOLD} robust-MAD z-score — untouched within that",
        "  range, smoothly down-weighted beyond it, never a hard cutoff.",
        "",
        f"## Validation (at day {late_now_day}, the latest evaluation point with a reasonably-sized",
        f"incomplete population — >= {MIN_SAMPLE_FOR_LATE_NOW} properties; the true last few days",
        "degenerate to a handful of outlier mega-buildings with few comparables, not a",
        "representative sample)",
        "",
        f"- Incomplete properties evaluated: {late_now_incomplete_count:,}",
    ]
    if late_now_z_values:
        lines.append(
            f"- Z_avg distribution: min {min(late_now_z_values):.3f} / median {statistics.median(late_now_z_values):.3f} / "
            f"max {max(late_now_z_values):.3f} / mean {statistics.mean(late_now_z_values):.3f}"
        )
    lines.append("- Observations meaningfully down-weighted by outlier_dampener, by component:")
    for component, count in late_now_outlier_counts.items():
        lines.append(f"  - {component}: {count:,}")
    lines += [
        "",
        "## Convergence: cumulative_credibility_estimate vs the known final actual total",
        "",
        f"Final actual_repair_cost_aud total: ${total_actual_final:,.0f} (static total never moves from",
        f"${total_static_final:,.0f}).",
        "",
    ] + _convergence_checkpoints(portfolio_rows, total_actual_final) + [""]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Credibility Model (credibility_model.py)"
    import re
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(
    late_now_day: int | None, late_now_z_values: list[float], late_now_outlier_counts: dict[str, int],
    late_now_incomplete_count: int, portfolio_rows: list[dict], total_actual_final: float, total_static_final: float,
) -> None:
    print("\n=== Summary ===")
    print(f"Validation checkpoint: day {late_now_day} (latest evaluation day with >= {MIN_SAMPLE_FOR_LATE_NOW} "
          f"incomplete properties — the true tail end is a handful of outlier mega-buildings, not representative)")
    print(f"Incomplete properties at that checkpoint: {late_now_incomplete_count:,}")
    if late_now_z_values:
        print(f"Z_avg distribution: min {min(late_now_z_values):.3f} / "
              f"median {statistics.median(late_now_z_values):.3f} / max {max(late_now_z_values):.3f} / "
              f"mean {statistics.mean(late_now_z_values):.3f}")

    print("\nObservations meaningfully down-weighted by outlier_dampener at that checkpoint, by component:")
    for component, count in late_now_outlier_counts.items():
        print(f"  {component:20s}: {count:,}")

    print(f"\nFinal actual_repair_cost_aud total: ${total_actual_final:,.0f}")
    print(f"Static total (never moves):          ${total_static_final:,.0f}")
    print("\nConvergence of cumulative_credibility_estimate toward the final actual total:")
    for line in _convergence_checkpoints(portfolio_rows, total_actual_final):
        print(line)


if __name__ == "__main__":
    main()
