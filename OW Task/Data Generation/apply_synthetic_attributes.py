"""Sample ABS-calibrated synthetic dwelling attributes onto each property.

Reads properties.geojson + lismore_census_distributions.json (produced by
census_distributions.py) and, for every property, draws — from its own
suburb's distribution:
  - dwelling_structure_census (categorical, from dwelling_structure_dist)
  - bedroom_count (categorical, conditional on dwelling_structure_census,
    from bedroom_dist_by_structure)
  - vehicle_count (categorical, from vehicle_count_dist — an EV-ownership
    proxy for a later step; no EV rate is invented here)

Only ADDS these new fields and fills building_area_m2 where it is still
null (never overwrites a real OSM-sourced value or any identity/location
field). Every record is re-validated against PropertyRecord before
properties.csv/properties.geojson are overwritten in place.

Sampling is seeded per property_id, so re-running this script against an
unchanged properties.geojson + census JSON reproduces the same synthetic
values. Does not touch any other pipeline step.
"""

from __future__ import annotations

import json
import sys
import random
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
CENSUS_JSON = BASE_DIR.parent / "Output" / "lismore_census_distributions.json"
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

# Assumed floor area per bedroom (m^2), used only to backfill building_area_m2
# where OSM left it null. Includes a proportional share of living/kitchen/
# bathroom space, not just the bedroom itself — a rough Lismore-housing-stock
# figure, not sourced from OSM or ABS.
SQM_PER_BEDROOM = 30.0
BUILDING_AREA_NOISE_FRACTION = 0.15  # +/- gaussian noise as a fraction of the base estimate
BUILDING_AREA_MIN_M2 = 40.0

BEDROOM_BUCKET_TO_INT = {"1": 1, "2": 2, "3": 3, "4": 4, "5+": 5}
VEHICLE_BUCKET_TO_INT = {"0": 0, "1": 1, "2": 2, "3+": 3}


def row_rng(property_id: str) -> random.Random:
    """Deterministic per-property RNG so re-runs are reproducible."""
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def weighted_choice(dist: dict[str, float], rng: random.Random) -> str:
    keys = list(dist.keys())
    weights = [dist[k] for k in keys]
    if sum(weights) <= 0:
        return rng.choice(keys)
    return rng.choices(keys, weights=weights, k=1)[0]


def synthesize_building_area_m2(bedroom_count: int, rng: random.Random) -> float:
    base = bedroom_count * SQM_PER_BEDROOM
    noise = rng.gauss(0, base * BUILDING_AREA_NOISE_FRACTION)
    return round(max(BUILDING_AREA_MIN_M2, base + noise), 1)


def enrich_properties(features: list[dict], distributions: dict) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": 0,
        "enriched": 0,
        "missing_distribution": [],
        "dwelling_structure_counts": {},
        "bedroom_count_counts": {},
        "vehicle_count_counts": {},
        "building_area_synthesized": 0,
        "building_area_already_set": 0,
    }

    for feature in features:
        props = dict(feature["properties"])
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]
        stats["total"] += 1

        suburb = props.get("suburb")
        dist = distributions.get(suburb)

        if dist is None:
            stats["missing_distribution"].append(props.get("property_id"))
        else:
            rng = row_rng(props["property_id"])

            # a) dwelling structure — only fill if not already set
            if props.get("dwelling_structure_census") is None:
                structure = weighted_choice(dist["dwelling_structure_dist"], rng)
                props["dwelling_structure_census"] = structure
            else:
                structure = props["dwelling_structure_census"]

            # b) bedroom count, conditional on the structure just drawn
            if props.get("bedroom_count") is None:
                bucket = weighted_choice(dist["bedroom_dist_by_structure"][structure], rng)
                props["bedroom_count"] = BEDROOM_BUCKET_TO_INT[bucket]

            # c) vehicle count (EV-ownership proxy for a later step)
            if props.get("vehicle_count") is None:
                bucket = weighted_choice(dist["vehicle_count_dist"], rng)
                props["vehicle_count"] = VEHICLE_BUCKET_TO_INT[bucket]

            stats["enriched"] += 1
            stats["dwelling_structure_counts"][props["dwelling_structure_census"]] = (
                stats["dwelling_structure_counts"].get(props["dwelling_structure_census"], 0) + 1
            )
            stats["bedroom_count_counts"][props["bedroom_count"]] = (
                stats["bedroom_count_counts"].get(props["bedroom_count"], 0) + 1
            )
            stats["vehicle_count_counts"][props["vehicle_count"]] = (
                stats["vehicle_count_counts"].get(props["vehicle_count"], 0) + 1
            )

            # building_area_m2: never overwrite a real OSM-sourced value
            if props.get("building_area_m2") is None:
                props["building_area_m2"] = synthesize_building_area_m2(props["bedroom_count"], rng)
                stats["building_area_synthesized"] += 1
            else:
                stats["building_area_already_set"] += 1

        try:
            records.append(PropertyRecord(**props))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={props.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def write_assumptions_doc(distributions: dict, stats: dict) -> None:
    fallback_suburbs = sorted(s for s, d in distributions.items() if d["source"] == "LGA_fallback")

    lines = [
        "# Synthetic Assumptions",
        "",
        "Generated by apply_synthetic_attributes.py — overwritten on every run.",
        "",
        "## ABS-to-project category mapping",
        "",
        "`dwelling_structure_census` (this script) and `construction_type` (a later,",
        "still-untouched step) are independent taxonomies on the same PropertyRecord:",
        "",
        "- `dwelling_structure_census`: ABS 2021 Census building-type category —",
        "  `separate_house` / `semi_detached` / `flat_or_apartment` / `other` — sampled",
        "  from each property's suburb-level G36/G41 distributions.",
        "- `construction_type`: flood-report floor-construction taxonomy —",
        "  `slab_on_ground` / `short_stumps` / `high_stumps` — drives",
        "  `floor_height_offset_m` per the Lismore 2022 flood report methodology.",
        "  **Not set by this script.**",
        "",
        "A property has both fields, populated by different, independent steps.",
        "",
        "## building_area_m2 assumption",
        "",
        f"Where `building_area_m2` was still null (i.e. not sourced from the OSM join),",
        f"it was synthesized as `bedroom_count * {SQM_PER_BEDROOM:.0f} sqm/bedroom`, with",
        f"+/-{BUILDING_AREA_NOISE_FRACTION:.0%} Gaussian noise and a floor of {BUILDING_AREA_MIN_M2:.0f} sqm.",
        f"Real OSM-sourced values, where present, were never overwritten.",
        f"- Synthesized this run: {stats['building_area_synthesized']:,}",
        f"- Already set (real OSM value or a prior run's synthesis, left untouched): {stats['building_area_already_set']:,}",
        "",
        "## Suburbs using LGA fallback",
        "",
        (
            "None — every suburb had usable SAL-level census data."
            if not fallback_suburbs
            else "The following suburbs' SAL-level data was missing, suppressed, or had fewer"
            " than 50 total dwellings in G36, so the Lismore LGA-level aggregate was used instead:"
        ),
    ]
    for suburb in fallback_suburbs:
        reasons = "; ".join(distributions[suburb]["fallback_reasons"])
        lines.append(f"- **{suburb}**: {reasons}")

    lines += [
        "",
        "## Fields that remain fully synthetic / unset",
        "",
        "Not touched by this script — each awaits its own dedicated step:",
        "",
        "- `construction_type` (and the `floor_height_offset_m` it drives)",
        "- `switchboard_type`",
        "- `circuit_count`",
        "- `kitchen_spec`",
        "- `building_age_years`",
        "- `initial_estimated_cost_aud`",
        "- `building_footprint` (still pending a real OSM join)",
        "",
        "This script only ever sets `dwelling_structure_census`, `bedroom_count`,",
        "`vehicle_count`, and (only when still null) `building_area_m2`.",
        "",
    ]
    ASSUMPTIONS_MD.write_text("\n".join(lines))


def print_summary(distributions: dict, stats: dict) -> None:
    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    print(f"Enriched (suburb had a distribution): {stats['enriched']:,}")
    if stats["missing_distribution"]:
        print(f"  ! {len(stats['missing_distribution'])} properties had no matching suburb distribution, left unenriched")

    print("\ndwelling_structure_census counts:")
    for key, count in sorted(stats["dwelling_structure_counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {key:20s} {count:>6,}")

    print("\nbedroom_count counts:")
    for key, count in sorted(stats["bedroom_count_counts"].items()):
        print(f"  {key:>2} bedroom(s): {count:>6,}")

    print("\nvehicle_count counts:")
    for key, count in sorted(stats["vehicle_count_counts"].items()):
        print(f"  {key} vehicle(s): {count:>6,}")

    print(f"\nbuilding_area_m2: synthesized {stats['building_area_synthesized']:,}, already set {stats['building_area_already_set']:,}")
    print(f"assumed sqm/bedroom used for synthesis: {SQM_PER_BEDROOM:.0f}")

    fallback_suburbs = sorted(s for s, d in distributions.items() if d["source"] == "LGA_fallback")
    print(f"\nSuburbs that used LGA fallback (from {CENSUS_JSON.name}): {fallback_suburbs if fallback_suburbs else 'none'}")


def main() -> None:
    print(f"Loading {GEOJSON_OUT.name} and {CENSUS_JSON.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())
    distributions = json.loads(CENSUS_JSON.read_text())

    print("Sampling synthetic attributes ...")
    records, stats = enrich_properties(geojson_data["features"], distributions)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    write_assumptions_doc(distributions, stats)
    print(f"Wrote {ASSUMPTIONS_MD.name}")

    print_summary(distributions, stats)


if __name__ == "__main__":
    main()
