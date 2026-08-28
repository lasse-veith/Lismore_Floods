"""Compute the initial estimated flood repair cost per property.

Core principle: each component is a TRIGGER, not a dial — once its own
depth threshold is crossed, that component is fully replaced regardless of
exactly how much further the water rose (wall-height-dependent items,
plasterboard and painting, are the exception: they additionally scale
continuously with wetted_wall_area_m2, since higher water genuinely means
more wall area to redo). Components no longer share one all-or-nothing
"any water at all" gate — different building elements fail at different
real-world depths:

    FLOORING, DRYING/DECON, DEMOLITION:      > 0 m      (any water at all)
    PLASTERBOARD, PAINTING:                  > 0.05 m   (past the baseboard)
    KITCHEN CABINETRY, APPLIANCES:           > 0.05 m   (floor-level fixtures, same as baseboard)
    SWITCHBOARD, ELECTRICAL:                 > 0.25 m   (plug/outlet height)

A property can therefore need re-flooring and drying without needing new
plasterboard, a switchboard, or a kitchen — exactly the "not everything
breaks the instant water enters" correction this replaces.

Step 0 inputs come directly from flood_exposure.py's persisted summary
fields (peak_depth_above_floor_m IS "the MAX water level reached across all
interval_hour rows for that property_id" — flood_exposure.py already
computed exactly this), rather than being recomputed here.

Unit costs, the regional loading factor, and the volatility sampler all come
from base_pricing.py — the single source of truth also used by the future
market_pricing.py / Repair Model "actual" repair simulation, so both are
grounded in the same core numbers rather than each keeping its own copy.
Every rate in base_pricing.py is a MODELLING ASSUMPTION, not a sourced trade
quote — see SYNTHETIC_ASSUMPTIONS.md.

Market volatility: each of the 9 cost components is scaled by its own
base_pricing.sample_volatility() draw — a log-normal multiplier (median 1.0)
seeded on (property_id, component), so prices genuinely vary property-to-
property and line-to-line the way a real trade market does, rather than
every property paying an identical fixed unit rate, while still being
reproducible across re-runs (same seed -> same multiplier). Only the unit
RATE is stochastic; the underlying geometry (wetted_wall_area_m2, severity
factor, etc.) stays a deterministic function of the property's real
attributes.

Re-validates against PropertyRecord and overwrites properties.csv/
properties.geojson in place. Requires building_area_m2, bedroom_count,
circuit_count, kitchen_spec, flooring, switchboard_type, and
peak_depth_above_floor_m to already be populated.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from math import sqrt
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
ROOT_DIR = BASE_DIR.parent                              # OW Task
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py
sys.path.insert(0, str(ROOT_DIR))                        # base_pricing.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from base_pricing import (  # noqa: E402
    APPLIANCE_RATE_AUD,
    DEMOLITION_RATE_AUD_PER_M2,
    DRYING_DECON_RATE_AUD_PER_M2,
    ELECTRICAL_RATE_AUD_PER_CIRCUIT,
    FLOORING_RATE_AUD_PER_M2,
    JOBS_PER_SUPPLIER_PER_WEEK,
    KITCHEN_CABINETRY_RATE_AUD_PER_LM,
    NUM_BUILDING_SUPPLIERS,
    PAINTING_RATE_AUD_PER_M2,
    PLASTERBOARD_RATE_AUD_PER_M2,
    REGIONAL_LOADING_APPLIES_TO,
    REGIONAL_LOADING_FACTOR,
    SWITCHBOARD_RATE_AUD,
    sample_volatility,
)
from Pydantic import PropertyRecord  # noqa: E402

ASSUMPTIONS_MD = ROOT_DIR / "SYNTHETIC_ASSUMPTIONS.md"

REPORT_TOTAL_DAMAGES_AUD = 587_000_000  # real, from steps.md / the report
REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK = 2067  # real, from steps.md / the report

# NUM_BUILDING_SUPPLIERS / JOBS_PER_SUPPLIER_PER_WEEK (imported above) are
# flagged in SYNTHETIC_ASSUMPTIONS.md below; not used by this script's cost
# math — they're the supply-side constraint Repair Model/Data/simulator.py's
# repair-timing simulation queues against.

REQUIRED_FIELDS = [
    "building_area_m2", "bedroom_count", "circuit_count",
    "kitchen_spec", "flooring", "switchboard_type", "peak_depth_above_floor_m",
]

# Per-component depth-above-floor trigger thresholds (metres). Each
# component is independently gated — a property can trip the flooring/
# drying/demolition threshold without tripping switchboard/electrical.
DEPTH_THRESHOLD_M = {
    "flooring": 0.0,
    "drying_decon": 0.0,
    "demolition": 0.0,
    "plasterboard": 0.05,
    "painting": 0.05,
    "kitchen_cabinetry": 0.05,
    "appliances": 0.05,
    "switchboard": 0.25,
    "electrical": 0.25,
}

COMPONENT_TO_COST_FIELD = {
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


def check_required_inputs(rows: list[dict]) -> None:
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


def check_category_mappings(rows: list[dict]) -> None:
    """Halt and report rather than defaulting silently if any categorical
    value present in the data has no cost mapping."""
    present_kitchen = {r["kitchen_spec"] for r in rows}
    present_flooring = {r["flooring"] for r in rows}
    present_switchboard = {r["switchboard_type"] for r in rows}

    unmapped_kitchen = present_kitchen - set(KITCHEN_CABINETRY_RATE_AUD_PER_LM)
    unmapped_flooring = present_flooring - set(FLOORING_RATE_AUD_PER_M2)
    unmapped_switchboard = present_switchboard - set(SWITCHBOARD_RATE_AUD)

    if unmapped_kitchen or unmapped_flooring or unmapped_switchboard:
        print("ABORTING: unmapped categorical values found — refusing to default silently.")
        if unmapped_kitchen:
            print(f"  kitchen_spec values with no cost mapping: {sorted(unmapped_kitchen)}")
        if unmapped_flooring:
            print(f"  flooring values with no cost mapping: {sorted(unmapped_flooring)}")
        if unmapped_switchboard:
            print(f"  switchboard_type values with no cost mapping: {sorted(unmapped_switchboard)}")
        sys.exit(1)

    print(f"  kitchen_spec values present & mapped: {sorted(present_kitchen)}")
    print(f"  flooring values present & mapped: {sorted(present_flooring)}")
    print(f"  switchboard_type values present & mapped: {sorted(present_switchboard)}")
    unused_kitchen = set(KITCHEN_CABINETRY_RATE_AUD_PER_LM) - present_kitchen
    if unused_kitchen:
        print(f"  (kitchen_spec cost-table entries not present in the data, unused: {sorted(unused_kitchen)})")


def compute_costs(row: dict) -> dict:
    property_id = row["property_id"]
    depth_above_floor_m = row["peak_depth_above_floor_m"]
    is_flooded_above_floor = depth_above_floor_m > 0

    building_area_m2 = row["building_area_m2"]
    est_perimeter_m = 4 * sqrt(building_area_m2)
    wetted_wall_height_m = min(depth_above_floor_m + 0.3, 2.4)
    wetted_wall_area_m2 = est_perimeter_m * wetted_wall_height_m
    kitchen_linear_m = 4.0 + 0.5 * row["bedroom_count"]
    severity_factor = 1.0 + min(depth_above_floor_m / 2.0, 1.0)

    base_result = {
        "depth_above_floor_m": depth_above_floor_m,
        "wetted_wall_area_m2": round(wetted_wall_area_m2, 2),
        "severity_factor": round(severity_factor, 3),
    }

    if not is_flooded_above_floor:
        return {
            **base_result,
            "switchboard_cost_aud": 0.0,
            "plasterboard_cost_aud": 0.0,
            "flooring_cost_aud": 0.0,
            "kitchen_cabinetry_cost_aud": 0.0,
            "electrical_cost_aud": 0.0,
            "appliance_cost_aud": 0.0,
            "drying_decon_cost_aud": 0.0,
            "painting_cost_aud": 0.0,
            "demolition_cost_aud": 0.0,
            "initial_estimated_cost_aud": 0.0,
        }

    def market_rate(base_rate: float, component: str) -> float:
        """base_rate scaled by a reproducible, per-property/per-component
        log-normal volatility multiplier (median 1.0, from base_pricing.py),
        then by the regional loading factor if this component carries it."""
        rate = base_rate * sample_volatility((property_id, component))
        if component in REGIONAL_LOADING_APPLIES_TO:
            rate *= REGIONAL_LOADING_FACTOR
        return rate

    def triggered(component: str) -> bool:
        return depth_above_floor_m > DEPTH_THRESHOLD_M[component]

    switchboard_cost = market_rate(SWITCHBOARD_RATE_AUD[row["switchboard_type"]], "switchboard") if triggered("switchboard") else 0.0
    plasterboard_cost = market_rate(PLASTERBOARD_RATE_AUD_PER_M2, "plasterboard") * wetted_wall_area_m2 if triggered("plasterboard") else 0.0
    flooring_cost = market_rate(FLOORING_RATE_AUD_PER_M2[row["flooring"]], "flooring") * building_area_m2 if triggered("flooring") else 0.0
    kitchen_cabinetry_cost = market_rate(KITCHEN_CABINETRY_RATE_AUD_PER_LM[row["kitchen_spec"]], "kitchen_cabinetry") * kitchen_linear_m if triggered("kitchen_cabinetry") else 0.0
    electrical_cost = market_rate(ELECTRICAL_RATE_AUD_PER_CIRCUIT, "electrical") * row["circuit_count"] if triggered("electrical") else 0.0
    appliance_cost = market_rate(APPLIANCE_RATE_AUD[row["kitchen_spec"]], "appliances") if triggered("appliances") else 0.0
    drying_decon_cost = market_rate(DRYING_DECON_RATE_AUD_PER_M2, "drying_decon") * building_area_m2 if triggered("drying_decon") else 0.0
    painting_cost = market_rate(PAINTING_RATE_AUD_PER_M2, "painting") * wetted_wall_area_m2 if triggered("painting") else 0.0
    demolition_cost = market_rate(DEMOLITION_RATE_AUD_PER_M2, "demolition") * building_area_m2 * severity_factor if triggered("demolition") else 0.0

    total = (
        switchboard_cost + plasterboard_cost + flooring_cost + kitchen_cabinetry_cost
        + electrical_cost + appliance_cost + drying_decon_cost + painting_cost + demolition_cost
    )

    return {
        **base_result,
        "switchboard_cost_aud": round(switchboard_cost, 2),
        "plasterboard_cost_aud": round(plasterboard_cost, 2),
        "flooring_cost_aud": round(flooring_cost, 2),
        "kitchen_cabinetry_cost_aud": round(kitchen_cabinetry_cost, 2),
        "electrical_cost_aud": round(electrical_cost, 2),
        "appliance_cost_aud": round(appliance_cost, 2),
        "drying_decon_cost_aud": round(drying_decon_cost, 2),
        "painting_cost_aud": round(painting_cost, 2),
        "demolition_cost_aud": round(demolition_cost, 2),
        "initial_estimated_cost_aud": round(total, 2),
    }


def enrich_properties(rows: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    flooded_costs: list[float] = []
    all_costs: list[float] = []
    trigger_counts: dict[str, int] = {component: 0 for component in DEPTH_THRESHOLD_M}

    for row in rows:
        costs = compute_costs(row)
        row.update(costs)
        all_costs.append(costs["initial_estimated_cost_aud"])
        if costs["initial_estimated_cost_aud"] > 0:
            flooded_costs.append(costs["initial_estimated_cost_aud"])
            for component, field in COMPONENT_TO_COST_FIELD.items():
                if costs[field] > 0:
                    trigger_counts[component] += 1

        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    stats = {
        "total": len(rows),
        "flooded_count": len(flooded_costs),
        "flooded_costs": flooded_costs,
        "total_cost_aud": sum(all_costs),
        "trigger_counts": trigger_counts,
    }
    return records, stats


def append_assumptions_doc(stats: dict) -> None:
    avg_cost = statistics.mean(stats["flooded_costs"]) if stats["flooded_costs"] else 0.0
    report_avg = REPORT_TOTAL_DAMAGES_AUD / REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK

    lines = [
        "# Static Pricing (static_pricing.py)",
        "",
        "Appended by static_pricing.py — this section is regenerated (replaced) on every run.",
        "",
        "## Core principle — per-component depth thresholds",
        "",
        "Each component is a TRIGGER, not a dial, but components no longer share one",
        "all-or-nothing \"any water at all\" gate — different building elements fail at",
        "different real-world depths:",
        "",
        "| Threshold | Components |",
        "|---|---|",
        "| > 0 m (any water) | flooring, drying/decon, demolition |",
        "| > 0.05 m (past the baseboard / floor-level fixtures) | plasterboard, painting, kitchen cabinetry, appliances |",
        "| > 0.25 m (plug/outlet height) | switchboard, electrical |",
        "",
        "A property can therefore need re-flooring and drying without needing a new",
        "switchboard or kitchen — this replaced an earlier version where any positive",
        "`peak_depth_above_floor_m` triggered full replacement of all 9 components at once.",
        "Wall-height-dependent items (plasterboard, painting) additionally scale",
        "continuously with `wetted_wall_area_m2` once triggered, since higher water means",
        "more wall area to redo.",
        "",
        "Trigger rate this run (% of flooded properties where each component fired):",
        "",
    ] + [
        f"- {component}: {stats['trigger_counts'][component]:,} / {stats['flooded_count']:,} "
        f"({stats['trigger_counts'][component]/stats['flooded_count']:.1%})" if stats['flooded_count'] else f"- {component}: 0 / 0"
        for component in DEPTH_THRESHOLD_M
    ] + [
        "",
        "## Unit cost table — now sourced from base_pricing.py",
        "",
        "static_pricing.py no longer defines its own rate tables. All 9 component rates,",
        "the regional loading factor, and which components carry it are imported from",
        "`base_pricing.py`, the single shared source of truth also intended for the future",
        "`market_pricing.py` / Repair Model \"actual\" repair simulation (see",
        "`Repair Model/Data/` — not built yet), so both stay grounded in identical numbers",
        "instead of drifting apart via duplicated copies. MODELLING ASSUMPTIONS throughout,",
        "not sourced trade quotes:",
        "",
        "| Component | Rate | Regional loading (1.15x)? |",
        "|---|---|---|",
        f"| Switchboard | {SWITCHBOARD_RATE_AUD} (by type) | yes |",
        f"| Plasterboard | ${PLASTERBOARD_RATE_AUD_PER_M2}/m2 | yes |",
        f"| Flooring | {FLOORING_RATE_AUD_PER_M2} (by type, $/m2) | yes |",
        f"| Kitchen cabinetry | {KITCHEN_CABINETRY_RATE_AUD_PER_LM} (by kitchen_spec, $/lm) | yes |",
        f"| Electrical rewiring | ${ELECTRICAL_RATE_AUD_PER_CIRCUIT}/circuit | yes |",
        f"| Appliances | {APPLIANCE_RATE_AUD} (by kitchen_spec) | no (retail goods) |",
        f"| Drying & decontamination | ${DRYING_DECON_RATE_AUD_PER_M2}/m2 | no |",
        f"| Painting | ${PAINTING_RATE_AUD_PER_M2}/m2 | no |",
        f"| Demolition/waste | ${DEMOLITION_RATE_AUD_PER_M2}/m2 x severity_factor | no |",
        "",
        f"**Regional loading factor ({REGIONAL_LOADING_FACTOR}x)**: applied to labour-heavy",
        "lines only, reflecting regional NSW trade scarcity amplified by post-disaster demand",
        "surge. A single project-wide constant (not per-suburb), since Lismore LGA is one",
        "regional labour market.",
        "",
        "## Market volatility (new — this run is no longer fully deterministic)",
        "",
        "Each of the 9 cost components is scaled by its own",
        "`base_pricing.sample_volatility((property_id, component))` draw — a log-normal",
        "multiplier, median 1.0, sigma 0.15 — before the regional loading factor is applied.",
        "This means unit rates genuinely vary property-to-property and line-to-line, the way",
        "a real trade market does, rather than every property paying an identical fixed rate.",
        "It is still fully reproducible: the multiplier is seeded on (property_id, component),",
        "so re-running this script produces the same costs unless the underlying property data",
        "changed. Only the unit RATE is stochastic — building_area_m2, wetted_wall_area_m2,",
        "severity_factor, etc. remain deterministic functions of the property's real",
        "attributes. (base_pricing.py's own labour/material split table, needed to layer",
        "supply-side effects onto the correct half of each cost line, is not used by this",
        "script — it exists for market_pricing.py.)",
        "",
        "**Repair capacity (flagged for the future repair-timing simulation, not used by this",
        "script's cost math)**: the regional repair market is modelled as",
        f"**{NUM_BUILDING_SUPPLIERS} main building suppliers**, each able to complete",
        f"**{JOBS_PER_SUPPLIER_PER_WEEK} jobs/week** — a combined capacity of",
        f"{NUM_BUILDING_SUPPLIERS * JOBS_PER_SUPPLIER_PER_WEEK} jobs/week across the whole",
        "portfolio. This is the supply-side constraint the repair-timing simulation",
        "(market_pricing.py / Repair Model) will need to queue against; it has no effect on",
        "the cost figures in this script.",
        "",
        "**kitchen_spec category reconciliation**: the cost tables above include both the",
        "originally-specified 4 categories (`original_dated`, `mid_range_updated`,",
        "`premium_stone_benchtop`, plus a shared `basic_laminate`/`stone_benchtop`) and the",
        "4 categories kitchen_derivation.py actually produces",
        "(`basic_laminate`/`standard_laminate`/`stone_benchtop`/`premium_stone_island`) — the",
        "union covers whichever set is actually present in the data, checked at runtime rather",
        "than assumed; the script halts rather than silently defaulting if any value is",
        "unmapped.",
        "",
        "## Geometry helpers",
        "",
        "`est_perimeter_m = 4 * sqrt(building_area_m2)` (square-footprint approximation).",
        "`wetted_wall_height_m = min(depth_above_floor_m + 0.3, 2.4)` — +0.3m industry-practice",
        "margin above the visible waterline, capped at 2.4m standard ceiling height.",
        "`severity_factor = 1.0 + min(depth_above_floor_m / 2.0, 1.0)` — ranges 1.0 (just above",
        "floor) to 2.0 (2m+ above floor); applied only to demolition/waste, since deeper floods",
        "generate more material to remove even though the whole floor is redone regardless.",
        "",
        "## Sanity check against the real benchmark",
        "",
        f"Report benchmark: **${REPORT_TOTAL_DAMAGES_AUD:,} residential damages**,",
        f"**{REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK:,} properties** flooded above floor",
        f"(implied average ${report_avg:,.0f}/property — that figure includes full structural",
        "rebuild costs for severely damaged homes, which this model does not attempt).",
        "",
        f"This model: {stats['flooded_count']:,} properties flooded above floor, average",
        f"${avg_cost:,.0f}/flooded property, total ${sum(stats['flooded_costs']):,.0f}.",
        f"**Gap flagged, not resolved**: this model's per-property average is",
        f"{'higher' if avg_cost > report_avg else 'lower'} than the report's implied average",
        f"by ${abs(avg_cost - report_avg):,.0f} — expected, since the flooded-property COUNT",
        "already differs from the report benchmark (see flood_exposure.py's own sanity check),",
        "this model excludes structural damage, and every unit rate here is an assumption, not",
        "a sourced quote.",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Static Pricing (static_pricing.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(stats: dict) -> None:
    avg_cost = statistics.mean(stats["flooded_costs"]) if stats["flooded_costs"] else 0.0
    report_avg = REPORT_TOTAL_DAMAGES_AUD / REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK

    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    print(f"Properties flooded above floor (cost > 0): {stats['flooded_count']:,}")
    print(f"Total modelled cost (all properties): ${stats['total_cost_aud']:,.0f}")
    if stats["flooded_costs"]:
        print(f"Average cost per flooded property: ${avg_cost:,.0f}")
        print(f"  min ${min(stats['flooded_costs']):,.0f} / median ${statistics.median(stats['flooded_costs']):,.0f} / max ${max(stats['flooded_costs']):,.0f}")

    print(f"\nReport benchmark: ${REPORT_TOTAL_DAMAGES_AUD:,} / {REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK:,} properties "
          f"= ${report_avg:,.0f}/property average (includes structural rebuild, not modelled here)")
    print(f"This model's average (${avg_cost:,.0f}) vs report's implied average (${report_avg:,.0f}): "
          f"gap of ${abs(avg_cost - report_avg):,.0f} — flagged, not resolved.")

    if stats["flooded_count"]:
        print("\nPer-component trigger rate (% of flooded properties where the depth threshold was crossed):")
        for component, threshold in DEPTH_THRESHOLD_M.items():
            count = stats["trigger_counts"][component]
            print(f"  {component:20s} (>{threshold:.2f}m): {count:>6,} / {stats['flooded_count']:,} ({count/stats['flooded_count']:.1%})")


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

    check_required_inputs(rows)

    print("Checking kitchen_spec/flooring/switchboard_type category mappings ...")
    check_category_mappings(rows)

    print("\nComputing static repair cost per property ...")
    records, stats = enrich_properties(rows)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
