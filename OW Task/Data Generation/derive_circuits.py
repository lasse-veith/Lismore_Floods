"""Derive a synthetic circuit_count per property.

No real per-property electrical/wiring data exists (see Synthetic_data.txt),
so circuit_count is modeled from three already-real-or-synthetic covariates
that plausibly drive it in the real world:

  - building_age_years: Australian wiring-rule stringency (AS/NZS 3000 and
    its predecessors) has tightened substantially over time — older houses
    were wired with very few, minimally-split circuits; modern houses get
    many dedicated circuits (per-room power, oven, cooktop, dishwasher,
    aircon, etc.). This is modeled as an era base count.
  - building_area_m2: more floor area needs more power circuits, roughly
    linearly.
  - bedroom_count: more rooms means more lighting/power circuit branches.

This assumes the property's ORIGINAL construction-era wiring density still
applies — it does not model rewiring/renovation history, since no such data
exists. See SYNTHETIC_ASSUMPTIONS.md for full justification.

Only fills circuit_count where still null. Re-validates against
PropertyRecord and overwrites properties.csv/properties.geojson in place.
Requires building_age_years, building_area_m2, and bedroom_count to already
be populated (run after derive_building_age.py / apply_synthetic_attributes.py
/ osm_footprint_append.py).
"""

from __future__ import annotations

import json
import sys
import re
import random
import statistics
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

# Era base circuit count, keyed by building_age_years upper bound (as of the
# 2022 flood-event reference year). Reflects progressively stricter
# AS/NZS 3000 wiring-rule circuit splitting over time.
ERA_BASE_CIRCUITS = [
    (17, 14.0),   # built 2005+   (age < 17)
    (32, 10.0),   # built 1990-2004
    (52, 7.0),    # built 1970-1989
    (76, 5.0),    # built 1946-1969
    (float("inf"), 3.0),  # pre-1946
]

BEDROOM_COEF = 0.6       # extra circuits per bedroom
AREA_DIVISOR = 40.0      # +1 circuit per additional 40 sqm of floor area
NOISE_SD_FRACTION = 0.12  # gaussian noise as a fraction of the base estimate
MIN_CIRCUITS = 2
MAX_CIRCUITS = 30


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def era_base_circuits(building_age_years: int) -> float:
    for max_age, base in ERA_BASE_CIRCUITS:
        if building_age_years < max_age:
            return base
    return ERA_BASE_CIRCUITS[-1][1]


def sample_circuit_count(building_age_years: int, bedroom_count: int, building_area_m2: float, rng: random.Random) -> int:
    base = era_base_circuits(building_age_years)
    base += BEDROOM_COEF * bedroom_count
    base += building_area_m2 / AREA_DIVISOR
    noise = rng.gauss(0, base * NOISE_SD_FRACTION)
    value = round(base + noise)
    return max(MIN_CIRCUITS, min(MAX_CIRCUITS, value))


def enrich_properties(features: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": 0,
        "sampled": 0,
        "missing_inputs": 0,
        "circuit_counts": [],
        "by_era": {"pre_1946": [], "1946_1969": [], "1970_1989": [], "1990_2004": [], "2005_plus": []},
    }

    for feature in features:
        props = dict(feature["properties"])
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]
        stats["total"] += 1

        age = props.get("building_age_years")
        bedrooms = props.get("bedroom_count")
        area = props.get("building_area_m2")

        if age is None or bedrooms is None or area is None:
            stats["missing_inputs"] += 1
        elif props.get("circuit_count") is None:
            rng = row_rng(props["property_id"])
            props["circuit_count"] = sample_circuit_count(age, bedrooms, area, rng)
            stats["sampled"] += 1

        if props.get("circuit_count") is not None:
            stats["circuit_counts"].append(props["circuit_count"])
            if age is not None:
                if age < 17:
                    stats["by_era"]["2005_plus"].append(props["circuit_count"])
                elif age < 32:
                    stats["by_era"]["1990_2004"].append(props["circuit_count"])
                elif age < 52:
                    stats["by_era"]["1970_1989"].append(props["circuit_count"])
                elif age < 76:
                    stats["by_era"]["1946_1969"].append(props["circuit_count"])
                else:
                    stats["by_era"]["pre_1946"].append(props["circuit_count"])

        try:
            records.append(PropertyRecord(**props))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={props.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def append_assumptions_doc(stats: dict) -> None:
    era_rows = "\n".join(
        f"  {max_age if max_age != float('inf') else 'pre-1946'}: base {base}"
        for max_age, base in ERA_BASE_CIRCUITS
    )
    lines = [
        "# Circuit Count (derive_circuits.py)",
        "",
        "Appended by derive_circuits.py — this section is regenerated (replaced) on every run.",
        "",
        "No real per-property electrical/wiring data exists (see Synthetic_data.txt), so",
        "`circuit_count` is derived from covariates that plausibly drive it in the real",
        "world, rather than sourced or drawn from an arbitrary flat distribution:",
        "",
        "- **building_age_years (era base)**: Australian wiring rules (AS/NZS 3000 and its",
        "  predecessors) have required progressively more circuit splitting over time —",
        "  older houses were wired with very few, minimally-split circuits; modern houses",
        "  get many dedicated circuits (per-room power, oven, cooktop, dishwasher, aircon,",
        "  etc.). Base circuit counts by era:",
        f"{era_rows}",
        f"- **bedroom_count**: +{BEDROOM_COEF} circuits per bedroom (more rooms -> more",
        "  lighting/power branches).",
        f"- **building_area_m2**: +1 circuit per additional {AREA_DIVISOR:.0f} sqm of floor",
        "  area (more area needs more power circuits).",
        f"- Gaussian noise (sd = {NOISE_SD_FRACTION:.0%} of the base estimate) is added, then",
        f"  the result is rounded and clipped to [{MIN_CIRCUITS}, {MAX_CIRCUITS}].",
        "",
        "**Key limitation**: this assumes each property still has its ORIGINAL",
        "construction-era wiring density. It does NOT model rewiring/renovation history",
        "(a 1950s house that had a full modern rewire would be underestimated) — no",
        "renovation-history data exists to correct for this.",
        "",
        "## Result (this run)",
        "",
    ]
    if stats["circuit_counts"]:
        lines.append(
            f"- Overall: mean {statistics.mean(stats['circuit_counts']):.1f}, "
            f"median {statistics.median(stats['circuit_counts']):.1f}, "
            f"n={len(stats['circuit_counts']):,}"
        )
        for era, values in stats["by_era"].items():
            if values:
                lines.append(f"- {era}: mean {statistics.mean(values):.1f}, n={len(values):,}")
    lines.append("")

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Circuit Count (derive_circuits.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(stats: dict) -> None:
    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    if stats["missing_inputs"]:
        print(f"  ! {stats['missing_inputs']:,} properties missing building_age_years/bedroom_count/building_area_m2, left circuit_count null")
    print(f"Sampled this run: {stats['sampled']:,}")

    if stats["circuit_counts"]:
        values = stats["circuit_counts"]
        print(f"\ncircuit_count overall: mean {statistics.mean(values):.1f}, median {statistics.median(values):.1f}, "
              f"min {min(values)}, max {max(values)}")
        print("\nMean circuit_count by construction era:")
        for era, era_values in stats["by_era"].items():
            if era_values:
                print(f"  {era:10s} n={len(era_values):>6,}  mean={statistics.mean(era_values):5.1f}")


def main() -> None:
    print(f"Loading {GEOJSON_OUT.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())

    print("Deriving circuit_count (era + bedrooms + floor area) ...")
    records, stats = enrich_properties(geojson_data["features"])
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended circuit-count documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
