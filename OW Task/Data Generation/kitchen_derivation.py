"""Derive a synthetic kitchen_spec per property.

kitchen_derivation.py had no instructions written into it (unlike
flooring_derivation.py's spec) — this design mirrors that script's structure
and reasoning, since kitchen finish quality plausibly follows the same
drivers as flooring finish quality.

Categories: basic_laminate, standard_laminate, stone_benchtop, premium_stone_island

STEP 0 — affluence tercile: read directly from PropertyRecord.affluence_score,
the canonical per-suburb affluence rating persisted by
derive_affluence_and_construction_type.py. This script originally recomputed
its own affluence_percentile locally (mirroring flooring_derivation.py's
original approach); both now read the same grounded field instead.

STEP 1 — base distribution by affluence tercile (low <0.33, mid 0.33-0.66,
high >0.66) — nicer kitchens skew toward higher-affluence properties.

STEP 2 — switchboard_type as a renovation proxy (same reasoning used for
flooring elsewhere in this pipeline: a kitchen reno and an electrical
upgrade tend to happen together, or neither happens):
  ceramic_fuse (unrenovated proxy)   -> +0.15 basic_laminate, -0.05 each from
                                         standard_laminate/stone_benchtop/premium_stone_island
  smart_ev_ready (renovated/new proxy) -> +0.10 stone_benchtop,
                                         +0.05 premium_stone_island, -0.15 basic_laminate

STEP 3 — bedroom_count nudge (bigger homes more often get an island-bench
kitchen upgrade when renovated, simply because there's room for one):
  bedroom_count >= 4: +0.05 stone_benchtop
  bedroom_count >= 5: +0.05 premium_stone_island (on top of the above), -0.10 basic_laminate

STEP 4 — dwelling_structure_census nudge (apartments/units are space-
constrained — a premium island-bench kitchen is a house-specific feature):
  if dwelling_structure_census == "flat_or_apartment":
    zero out premium_stone_island, redistribute proportionally to the other
    three categories

Renormalize once after all adjustments, then sample (seeded on property_id).

Only fills kitchen_spec where still null. Re-validates against
PropertyRecord and overwrites properties.csv/properties.geojson in place.
"""

from __future__ import annotations

import json
import sys
import re
import random
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

CATEGORIES = ["basic_laminate", "standard_laminate", "stone_benchtop", "premium_stone_island"]

BASE_DIST_BY_TERCILE = {
    "low": {"basic_laminate": 0.55, "standard_laminate": 0.35, "stone_benchtop": 0.08, "premium_stone_island": 0.02},
    "mid": {"basic_laminate": 0.20, "standard_laminate": 0.45, "stone_benchtop": 0.30, "premium_stone_island": 0.05},
    "high": {"basic_laminate": 0.05, "standard_laminate": 0.20, "stone_benchtop": 0.45, "premium_stone_island": 0.30},
}


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def affluence_tercile(affluence_score: float) -> str:
    if affluence_score < 0.33:
        return "low"
    if affluence_score <= 0.66:
        return "mid"
    return "high"


def compute_kitchen_weights(row: dict) -> dict[str, float]:
    weights = dict(BASE_DIST_BY_TERCILE[affluence_tercile(row["affluence_score"])])

    # STEP 2: switchboard_type as a renovation proxy
    if row.get("switchboard_type") == "ceramic_fuse":
        weights["basic_laminate"] += 0.15
        for key in ("standard_laminate", "stone_benchtop", "premium_stone_island"):
            weights[key] = max(0.0, weights[key] - 0.05)
    elif row.get("switchboard_type") == "smart_ev_ready":
        weights["stone_benchtop"] += 0.10
        weights["premium_stone_island"] += 0.05
        weights["basic_laminate"] = max(0.0, weights["basic_laminate"] - 0.15)

    # STEP 3: bedroom_count nudge
    bedroom_count = row.get("bedroom_count") or 0
    if bedroom_count >= 4:
        weights["stone_benchtop"] += 0.05
    if bedroom_count >= 5:
        weights["premium_stone_island"] += 0.05
        weights["basic_laminate"] = max(0.0, weights["basic_laminate"] - 0.10)

    # STEP 4: dwelling structure nudge
    if row.get("dwelling_structure_census") == "flat_or_apartment":
        removed = weights["premium_stone_island"]
        weights["premium_stone_island"] = 0.0
        remaining_keys = ["basic_laminate", "standard_laminate", "stone_benchtop"]
        remaining_total = sum(weights[k] for k in remaining_keys)
        if remaining_total > 0:
            for key in remaining_keys:
                weights[key] += removed * (weights[key] / remaining_total)
        else:
            for key in remaining_keys:
                weights[key] += removed / len(remaining_keys)

    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def sample_kitchen_spec(row: dict, rng: random.Random) -> str:
    weights = compute_kitchen_weights(row)
    return rng.choices(CATEGORIES, weights=[weights[c] for c in CATEGORIES], k=1)[0]


def enrich_properties(rows: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": len(rows),
        "sampled": 0,
        "kitchen_counts": {},
        "kitchen_by_tercile": {"low": {}, "mid": {}, "high": {}},
        "switchboard_nudge_applied": 0,
        "bedroom_nudge_applied": 0,
        "flat_nudge_applied": 0,
    }

    for row in rows:
        if row.get("kitchen_spec") is None:
            rng = row_rng(row["property_id"])
            row["kitchen_spec"] = sample_kitchen_spec(row, rng)
            stats["sampled"] += 1

        stats["kitchen_counts"][row["kitchen_spec"]] = stats["kitchen_counts"].get(row["kitchen_spec"], 0) + 1
        tercile = affluence_tercile(row["affluence_score"])
        stats["kitchen_by_tercile"][tercile][row["kitchen_spec"]] = (
            stats["kitchen_by_tercile"][tercile].get(row["kitchen_spec"], 0) + 1
        )
        if row.get("switchboard_type") in ("ceramic_fuse", "smart_ev_ready"):
            stats["switchboard_nudge_applied"] += 1
        if (row.get("bedroom_count") or 0) >= 4:
            stats["bedroom_nudge_applied"] += 1
        if row.get("dwelling_structure_census") == "flat_or_apartment":
            stats["flat_nudge_applied"] += 1

        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def append_assumptions_doc(stats: dict) -> None:
    lines = [
        "# Kitchen Spec (kitchen_derivation.py)",
        "",
        "Appended by kitchen_derivation.py — this section is regenerated (replaced) on every run.",
        "",
        "kitchen_derivation.py had no instructions written into it (unlike",
        "flooring_derivation.py). This design mirrors flooring_derivation.py's structure",
        "and reasoning — affluence_score is read directly from",
        "PropertyRecord.affluence_score (the canonical field persisted by",
        "derive_affluence_and_construction_type.py), same switchboard_type-as-renovation-",
        "proxy logic — since kitchen finish quality plausibly follows the same drivers as",
        "flooring finish quality.",
        "",
        "## Category logic",
        "",
        f"- STEP 1 base distributions by affluence tercile: {BASE_DIST_BY_TERCILE}",
        "- STEP 2: `switchboard_type == \"ceramic_fuse\"` (unrenovated proxy) -> +0.15",
        "  basic_laminate, -0.05 each from standard_laminate/stone_benchtop/premium_stone_island.",
        "  `switchboard_type == \"smart_ev_ready\"` (renovated/new proxy) -> +0.10 stone_benchtop,",
        "  +0.05 premium_stone_island, -0.15 basic_laminate.",
        "- STEP 3: `bedroom_count >= 4` -> +0.05 stone_benchtop; `bedroom_count >= 5` -> additionally",
        "  +0.05 premium_stone_island, -0.10 basic_laminate (bigger homes more often get an",
        "  island-bench kitchen when renovated, simply because there's room for one).",
        "- STEP 4: `dwelling_structure_census == \"flat_or_apartment\"` -> zero out",
        "  premium_stone_island (space-constrained, a house-specific feature), redistribute",
        "  proportionally to the other three categories.",
        "",
        "## Result (this run)",
        "",
        f"- Sampled this run: {stats['sampled']:,} / {stats['total']:,}",
        f"- switchboard_type nudge applied to: {stats['switchboard_nudge_applied']:,} properties",
        f"- bedroom_count nudge applied to: {stats['bedroom_nudge_applied']:,} properties",
        f"- flat/apartment nudge applied to: {stats['flat_nudge_applied']:,} properties",
        "",
        "Overall kitchen_spec distribution:",
    ]
    total = sum(stats["kitchen_counts"].values())
    for cat in CATEGORIES:
        n = stats["kitchen_counts"].get(cat, 0)
        lines.append(f"- {cat}: {n:,} ({n/total:.1%})" if total else f"- {cat}: 0")
    lines.append("")

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Kitchen Spec (kitchen_derivation.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(stats: dict) -> None:
    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}  |  sampled this run: {stats['sampled']:,}")
    print(f"switchboard_type nudge applied to: {stats['switchboard_nudge_applied']:,} properties")
    print(f"bedroom_count nudge applied to: {stats['bedroom_nudge_applied']:,} properties")
    print(f"flat/apartment nudge applied to: {stats['flat_nudge_applied']:,} properties")

    print("\nOverall kitchen_spec distribution:")
    total = sum(stats["kitchen_counts"].values())
    for cat in CATEGORIES:
        n = stats["kitchen_counts"].get(cat, 0)
        print(f"  {cat:22s} {n:>6,}  ({n/total:.1%})" if total else f"  {cat:22s} 0")

    print("\nkitchen_spec distribution by affluence tercile:")
    for tercile in ("low", "mid", "high"):
        counts = stats["kitchen_by_tercile"][tercile]
        t_total = sum(counts.values())
        print(f"  {tercile}:")
        for cat in CATEGORIES:
            n = counts.get(cat, 0)
            if n:
                print(f"    {cat:22s} {n:>6,}  ({n/t_total:.1%})")


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

    print("Deriving kitchen_spec (affluence tercile + switchboard/bedroom/structure nudges) ...")
    records, stats = enrich_properties(rows)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended kitchen-spec documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
