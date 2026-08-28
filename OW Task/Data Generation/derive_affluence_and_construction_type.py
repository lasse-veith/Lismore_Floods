"""Derive the canonical affluence_score per suburb, plus construction_type /
floor_height_offset_m / flood_planning_level_m_ahd per property.

affluence_score is the SINGLE canonical affluence rating for this whole
pipeline going forward — every other script that needs an affluence rating
(switch_board.py, flooring_derivation.py, kitchen_derivation.py, ...) should
read this persisted field rather than computing its own proxy.

STEP 1 — affluence_score: a hardcoded suburb median house-price table
(6 real values, 7 estimated placements — flagged separately below and in
SYNTHETIC_ASSUMPTIONS.md), min-max normalized to [0, 1] across all 13
suburbs, joined onto every property by its suburb.

STEP 2 — construction_type + floor_height_offset_m: flats/apartments are
forced to slab_on_ground; everything else is sampled conditional on
building_age_years (seeded on property_id). floor_height_offset_m is then
set deterministically from construction_type per the flood report's real
methodology. flood_planning_level_m_ahd = 13.4 is written as a constant on
every row (a real council DCP figure, kept for later comparison only).

Requires building_age_years and dwelling_structure_census to already be
populated on every row — aborts loudly if not (does not default/skip).

Re-validates against PropertyRecord and overwrites properties.csv/
properties.geojson in place. Only fills construction_type /
floor_height_offset_m where still null; affluence_score and
flood_planning_level_m_ahd are always (re-)written, since they are direct,
deterministic joins/constants rather than per-property random draws.
"""

from __future__ import annotations

import json
import re
import random
import sys
from collections import Counter
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

# --------------------------------------------------------------------------
# STEP 1: hardcoded suburb median house-price table -> affluence_score
# --------------------------------------------------------------------------

SUBURB_PRICE_TABLE = {
    "GOONELLABAH": 719000,
    "GIRARDS HILL": 659000,
    "CHILCOTTS GRASS": 711000,
    "LISMORE HEIGHTS": 620000,
    "EAST LISMORE": 565000,
    "NORTH LISMORE": 455000,
    "SOUTH LISMORE": 455000,
    "LISMORE": 500000,
    "MONALTRIE": 738000,
    "LOFTVILLE": 738000,
    "HOWARDS GRASS": 738000,
    "LINDENDALE": 738000,
    "TREGEAGLE": 738000,
}

# Six suburbs each have a distinct price figure — treated as the "6 real
# values". The other seven all duplicate one of two round-number figures
# (738000 x5, 455000 x2) — a classic placeholder signature (real independent
# median prices essentially never land on the exact same dollar figure
# across multiple suburbs) — so those seven are treated as "estimated
# placements". This split was INFERRED from the duplicate-value pattern in
# the table as given, not separately labeled by the source — flagged here so
# it can be corrected if the inference is wrong.
REAL_PRICE_SUBURBS = {"GOONELLABAH", "GIRARDS HILL", "CHILCOTTS GRASS", "LISMORE HEIGHTS", "EAST LISMORE", "LISMORE"}
ESTIMATED_PRICE_SUBURBS = set(SUBURB_PRICE_TABLE) - REAL_PRICE_SUBURBS

# STEP 2: construction_type sampling weights, by building_age_years bracket.
# Justification: raised-floor (stump) construction was the dominant
# residential technique in NSW until concrete slab-on-ground became cheap
# and standard from roughly the 1970s onward; older housing stock is
# therefore weighted heavily toward stumps, newer stock overwhelmingly slab.
CONSTRUCTION_DIST_BY_AGE = {
    "gt50": {"slab_on_ground": 0.15, "short_stumps": 0.40, "high_stumps": 0.45},
    "age20_50": {"slab_on_ground": 0.45, "short_stumps": 0.35, "high_stumps": 0.20},
    "lt20": {"slab_on_ground": 0.80, "short_stumps": 0.15, "high_stumps": 0.05},
}

# Real flood-report methodology (see Pydantic.py's floor_height_offset_m comment).
FLOOR_HEIGHT_OFFSET_BY_TYPE = {"slab_on_ground": 0.15, "short_stumps": 0.5, "high_stumps": 1.5}

FLOOD_PLANNING_LEVEL_M_AHD = 13.4  # real council DCP figure, comparison only


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def compute_affluence_scores() -> dict[str, float]:
    lo = min(SUBURB_PRICE_TABLE.values())
    hi = max(SUBURB_PRICE_TABLE.values())
    span = hi - lo
    return {
        suburb: (price - lo) / span if span > 0 else 0.5
        for suburb, price in SUBURB_PRICE_TABLE.items()
    }


def age_bracket(building_age_years: int) -> str:
    if building_age_years > 50:
        return "gt50"
    if building_age_years >= 20:
        return "age20_50"
    return "lt20"


def weighted_choice(dist: dict[str, float], rng: random.Random) -> str:
    keys = list(dist.keys())
    weights = [dist[k] for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def sample_construction_type(row: dict, rng: random.Random) -> str:
    if row.get("dwelling_structure_census") == "flat_or_apartment":
        return "slab_on_ground"
    dist = CONSTRUCTION_DIST_BY_AGE[age_bracket(row["building_age_years"])]
    return weighted_choice(dist, rng)


def check_required_inputs(rows: list[dict]) -> None:
    missing_age = [r["property_id"] for r in rows if r.get("building_age_years") is None]
    missing_structure = [r["property_id"] for r in rows if r.get("dwelling_structure_census") is None]
    if missing_age or missing_structure:
        print("ABORTING: required inputs are missing.")
        if missing_age:
            print(f"  {len(missing_age):,} properties have no building_age_years "
                  f"(e.g. {missing_age[:5]}) — run derive_building_age.py first.")
        if missing_structure:
            print(f"  {len(missing_structure):,} properties have no dwelling_structure_census "
                  f"(e.g. {missing_structure[:5]}) — run apply_synthetic_attributes.py first.")
        sys.exit(1)


def enrich_properties(rows: list[dict], affluence_scores: dict[str, float]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": len(rows),
        "construction_sampled": 0,
        "already_set": 0,
        "missing_affluence_score": [],
        "construction_x_structure": Counter(),
        "construction_x_age_bracket": Counter(),
    }

    for row in rows:
        affluence_score = affluence_scores.get(row["suburb"])
        if affluence_score is None:
            stats["missing_affluence_score"].append(row["property_id"])
        row["affluence_score"] = affluence_score

        row["flood_planning_level_m_ahd"] = FLOOD_PLANNING_LEVEL_M_AHD

        if row.get("construction_type") is None:
            rng = row_rng(row["property_id"])
            construction_type = sample_construction_type(row, rng)
            row["construction_type"] = construction_type
            row["floor_height_offset_m"] = FLOOR_HEIGHT_OFFSET_BY_TYPE[construction_type]
            stats["construction_sampled"] += 1
        else:
            stats["already_set"] += 1

        stats["construction_x_structure"][(row["construction_type"], row["dwelling_structure_census"])] += 1
        stats["construction_x_age_bracket"][(row["construction_type"], age_bracket(row["building_age_years"]))] += 1

        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def append_assumptions_doc(affluence_scores: dict[str, float], stats: dict) -> None:
    lines = [
        "# Affluence Score & Construction Type (derive_affluence_and_construction_type.py)",
        "",
        "Appended by derive_affluence_and_construction_type.py — this section is",
        "regenerated (replaced) on every run.",
        "",
        "## affluence_score: THE canonical affluence rating",
        "",
        "`affluence_score` is a hardcoded suburb median house-price table, min-max",
        "normalized to [0, 1] across all 13 suburbs. **Every script needing an affluence",
        "rating should read this persisted field going forward, rather than computing its",
        "own proxy** (flooring_derivation.py and kitchen_derivation.py previously computed",
        "their own building-area/bedroom/vehicle-based composite; switch_board.py",
        "previously computed its own G02-income-based version — both have been updated to",
        "read this field instead; see their own docstrings/SYNTHETIC_ASSUMPTIONS.md sections).",
        "",
        "Suburb price table (6 real values, 7 estimated placements — the estimated ones",
        "were **inferred** from duplicate round-number values in the table as given, not",
        "separately labeled by the source; flag if this inference is wrong):",
        "",
        "| Suburb | Price | Basis | affluence_score |",
        "|---|---|---|---|",
    ]
    for suburb, price in sorted(SUBURB_PRICE_TABLE.items(), key=lambda kv: -kv[1]):
        basis = "real" if suburb in REAL_PRICE_SUBURBS else "estimated"
        lines.append(f"| {suburb} | ${price:,} | {basis} | {affluence_scores[suburb]:.3f} |")
    lines += [
        "",
        "## construction_type sampling",
        "",
        "`dwelling_structure_census == \"flat_or_apartment\"` is always forced to",
        "`slab_on_ground` (flats are built on slab, not stumps). Everything else is sampled",
        "conditional on `building_age_years`:",
        "",
        f"- >50yr: {CONSTRUCTION_DIST_BY_AGE['gt50']}",
        f"- 20-50yr: {CONSTRUCTION_DIST_BY_AGE['age20_50']}",
        f"- <20yr: {CONSTRUCTION_DIST_BY_AGE['lt20']}",
        "",
        "**Justification**: raised-floor (stump) construction was the dominant NSW",
        "residential technique until concrete slab-on-ground became cheap and standard",
        "from roughly the 1970s onward — older housing stock is weighted heavily toward",
        "stumps, newer stock overwhelmingly toward slab.",
        "",
        f"`floor_height_offset_m` is then set deterministically from `construction_type`:",
        f"{FLOOR_HEIGHT_OFFSET_BY_TYPE} (real flood-report methodology).",
        "",
        f"`flood_planning_level_m_ahd = {FLOOD_PLANNING_LEVEL_M_AHD}` is written as a",
        "constant on every row — a real council DCP figure, kept for later comparison only.",
        "",
        "## Result (this run)",
        "",
        f"- construction_type sampled this run: {stats['construction_sampled']:,} / "
        f"already set: {stats['already_set']:,}",
        "",
        "construction_type x dwelling_structure_census:",
    ]
    for (ct, ds), n in sorted(stats["construction_x_structure"].items()):
        lines.append(f"- {ct} x {ds}: {n:,}")
    lines.append("")
    lines.append("construction_type x age bracket:")
    for (ct, bracket), n in sorted(stats["construction_x_age_bracket"].items()):
        lines.append(f"- {ct} x {bracket}: {n:,}")
    lines.append("")

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Affluence Score & Construction Type (derive_affluence_and_construction_type.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(affluence_scores: dict[str, float], stats: dict) -> None:
    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    print(f"construction_type sampled this run: {stats['construction_sampled']:,}  |  already set: {stats['already_set']:,}")
    if stats["missing_affluence_score"]:
        print(f"  ! {len(stats['missing_affluence_score'])} properties had a suburb with no affluence_score entry")

    print("\naffluence_score by suburb:")
    for suburb, score in sorted(affluence_scores.items(), key=lambda kv: -kv[1]):
        basis = "real" if suburb in REAL_PRICE_SUBURBS else "estimated"
        print(f"  {suburb:20s} {score:.3f}  ({basis})")

    print("\nconstruction_type x dwelling_structure_census:")
    for (ct, ds), n in sorted(stats["construction_x_structure"].items()):
        print(f"  {ct:15s} x {ds:20s} {n:>6,}")

    print("\nconstruction_type x age bracket:")
    for (ct, bracket), n in sorted(stats["construction_x_age_bracket"].items()):
        print(f"  {ct:15s} x {bracket:10s} {n:>6,}")


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

    affluence_scores = compute_affluence_scores()

    print("Deriving affluence_score, construction_type, floor_height_offset_m, flood_planning_level_m_ahd ...")
    records, stats = enrich_properties(rows, affluence_scores)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(affluence_scores, stats)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(affluence_scores, stats)


if __name__ == "__main__":
    main()
