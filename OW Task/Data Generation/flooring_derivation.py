"""Derive a synthetic flooring category per property.

Categories: carpet, vinyl, timber_floorboards, tile, laminate, polished_concrete

STEP 0 — affluence tercile: read directly from PropertyRecord.affluence_score,
the canonical per-suburb affluence rating persisted by
derive_affluence_and_construction_type.py. This script originally computed
its own affluence_percentile here (a rank-normalized composite of
building_area_m2/bedroom_count/vehicle_count), since nothing upstream
computed one at the time — that has been replaced with the grounded field
now that one exists, so this script agrees with switch_board.py and
kitchen_derivation.py rather than each having its own definition.

STEP 1 — base distribution by affluence tercile (low <0.33, mid 0.33-0.66,
high >0.66).

STEP 2 — construction_type nudge: raised timber homes (short_stumps /
high_stumps) get +0.15 to timber_floorboards.

STEP 3 — switchboard_type as a renovation proxy (same logic used for
circuits elsewhere in this pipeline): ceramic_fuse (unrenovated) skews toward
carpet/timber_floorboards; smart_ev_ready (recently renovated) skews toward
tile/laminate.

STEP 4 — dwelling_structure_census nudge: flats/apartments never get
polished_concrete or timber_floorboards; that weight is redistributed
proportionally to carpet/vinyl/tile/laminate.

Renormalize once after all adjustments, then sample (seeded on property_id).

NOTE: construction_type and switchboard_type are both still entirely null in
properties.csv/geojson as of writing this script — neither has its own
dedicated derivation step yet. STEP 2 and STEP 3 are implemented per spec and
will activate automatically (via a re-run) once those fields are populated;
until then they are no-ops and flooring is driven by STEP 1 (affluence) and
STEP 4 (dwelling structure) only. See SYNTHETIC_ASSUMPTIONS.md.

Only fills flooring where still null. Re-validates against PropertyRecord and
overwrites properties.csv/properties.geojson in place.
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

CATEGORIES = ["carpet", "vinyl", "timber_floorboards", "tile", "laminate", "polished_concrete"]

BASE_DIST_BY_TERCILE = {
    "low": {"carpet": 0.35, "vinyl": 0.30, "timber_floorboards": 0.25, "tile": 0.10, "laminate": 0.0, "polished_concrete": 0.0},
    "mid": {"carpet": 0.25, "vinyl": 0.05, "timber_floorboards": 0.30, "tile": 0.25, "laminate": 0.15, "polished_concrete": 0.0},
    "high": {"carpet": 0.10, "vinyl": 0.0, "timber_floorboards": 0.30, "tile": 0.25, "laminate": 0.20, "polished_concrete": 0.15},
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


def compute_flooring_weights(row: dict) -> dict[str, float]:
    weights = dict(BASE_DIST_BY_TERCILE[affluence_tercile(row["affluence_score"])])

    # STEP 2: construction_type nudge
    if row.get("construction_type") in ("short_stumps", "high_stumps"):
        weights["timber_floorboards"] += 0.15

    # STEP 3: switchboard_type as a renovation proxy
    if row.get("switchboard_type") == "ceramic_fuse":
        weights["carpet"] += 0.10
        weights["timber_floorboards"] += 0.05
        for key in ("tile", "laminate", "polished_concrete"):
            weights[key] = max(0.0, weights[key] - 0.05)
    elif row.get("switchboard_type") == "smart_ev_ready":
        weights["tile"] += 0.10
        weights["laminate"] += 0.05
        weights["carpet"] = max(0.0, weights["carpet"] - 0.15)

    # STEP 4: dwelling structure nudge
    if row.get("dwelling_structure_census") == "flat_or_apartment":
        removed = weights["polished_concrete"] + weights["timber_floorboards"]
        weights["polished_concrete"] = 0.0
        weights["timber_floorboards"] = 0.0
        remaining_keys = ["carpet", "vinyl", "tile", "laminate"]
        remaining_total = sum(weights[k] for k in remaining_keys)
        if remaining_total > 0:
            for key in remaining_keys:
                weights[key] += removed * (weights[key] / remaining_total)
        else:
            for key in remaining_keys:
                weights[key] += removed / len(remaining_keys)

    total = sum(weights.values())
    return {k: v / total for k, v in weights.items()}


def sample_flooring(row: dict, rng: random.Random) -> str:
    weights = compute_flooring_weights(row)
    return rng.choices(CATEGORIES, weights=[weights[c] for c in CATEGORIES], k=1)[0]


def enrich_properties(rows: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": len(rows),
        "sampled": 0,
        "flooring_counts": {},
        "flooring_by_tercile": {"low": {}, "mid": {}, "high": {}},
        "construction_type_nudge_applied": 0,
        "switchboard_nudge_applied": 0,
        "flat_nudge_applied": 0,
    }

    for row in rows:
        if row.get("flooring") is None:
            rng = row_rng(row["property_id"])
            row["flooring"] = sample_flooring(row, rng)
            stats["sampled"] += 1

        stats["flooring_counts"][row["flooring"]] = stats["flooring_counts"].get(row["flooring"], 0) + 1
        tercile = affluence_tercile(row["affluence_score"])
        stats["flooring_by_tercile"][tercile][row["flooring"]] = (
            stats["flooring_by_tercile"][tercile].get(row["flooring"], 0) + 1
        )
        if row.get("construction_type") in ("short_stumps", "high_stumps"):
            stats["construction_type_nudge_applied"] += 1
        if row.get("switchboard_type") in ("ceramic_fuse", "smart_ev_ready"):
            stats["switchboard_nudge_applied"] += 1
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
        "# Flooring (flooring_derivation.py)",
        "",
        "Appended by flooring_derivation.py — this section is regenerated (replaced) on every run.",
        "",
        "## affluence tercile: grounded in derive_affluence_and_construction_type.py",
        "",
        "This script originally computed its own affluence_percentile (a rank-normalized",
        "composite of building_area_m2/bedroom_count/vehicle_count), since nothing upstream",
        "computed one at the time. It now reads `PropertyRecord.affluence_score` directly —",
        "the canonical per-suburb affluence rating persisted by",
        "`derive_affluence_and_construction_type.py` — and cuts it into terciles the same",
        "way (`low` <0.33, `mid` 0.33-0.66, `high` >0.66), so this script agrees with",
        "switch_board.py and kitchen_derivation.py rather than each having its own",
        "affluence definition.",
        "",
        "## Category logic",
        "",
        "Base distribution by affluence tercile, then three nudges applied in order",
        "(construction_type, switchboard_type-as-renovation-proxy, dwelling structure),",
        "renormalized once at the end, then sampled (seeded on property_id):",
        "",
        f"- STEP 1 base distributions: {BASE_DIST_BY_TERCILE}",
        "- STEP 2: `construction_type in [short_stumps, high_stumps]` -> +0.15 timber_floorboards",
        "  (raised timber homes structurally retain original timber floors far more often",
        "  than slab homes get them).",
        "- STEP 3: `switchboard_type == \"ceramic_fuse\"` (unrenovated proxy) -> +0.10 carpet,",
        "  +0.05 timber_floorboards, -0.05 each from tile/laminate/polished_concrete.",
        "  `switchboard_type == \"smart_ev_ready\"` (renovated/new proxy) -> +0.10 tile,",
        "  +0.05 laminate, -0.15 carpet.",
        "- STEP 4: `dwelling_structure_census == \"flat_or_apartment\"` -> zero out",
        "  polished_concrete and timber_floorboards, redistribute proportionally to",
        "  carpet/vinyl/tile/laminate.",
        "",
        "`construction_type` and `switchboard_type` are both now populated (by",
        "derive_affluence_and_construction_type.py and switch_board.py respectively), so",
        "STEP 2 and STEP 3 are both active as of this run.",
        "",
        "## Result (this run)",
        "",
        f"- Sampled this run: {stats['sampled']:,} / {stats['total']:,}",
        f"- construction_type nudge applied to: {stats['construction_type_nudge_applied']:,} properties",
        f"- switchboard_type nudge applied to: {stats['switchboard_nudge_applied']:,} properties",
        f"- flat/apartment nudge applied to: {stats['flat_nudge_applied']:,} properties",
        "",
        "Overall flooring distribution:",
    ]
    total = sum(stats["flooring_counts"].values())
    for cat in CATEGORIES:
        n = stats["flooring_counts"].get(cat, 0)
        lines.append(f"- {cat}: {n:,} ({n/total:.1%})" if total else f"- {cat}: 0")
    lines.append("")

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Flooring (flooring_derivation.py)"
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
    print(f"construction_type nudge applied to: {stats['construction_type_nudge_applied']:,} properties"
          + ("  (0 as expected — construction_type not yet populated)" if stats["construction_type_nudge_applied"] == 0 else ""))
    print(f"switchboard_type nudge applied to: {stats['switchboard_nudge_applied']:,} properties"
          + ("  (0 as expected — switchboard_type not yet populated)" if stats["switchboard_nudge_applied"] == 0 else ""))
    print(f"flat/apartment nudge applied to: {stats['flat_nudge_applied']:,} properties")

    print("\nOverall flooring distribution:")
    total = sum(stats["flooring_counts"].values())
    for cat in CATEGORIES:
        n = stats["flooring_counts"].get(cat, 0)
        print(f"  {cat:20s} {n:>6,}  ({n/total:.1%})" if total else f"  {cat:20s} 0")

    print("\nFlooring distribution by affluence tercile:")
    for tercile in ("low", "mid", "high"):
        counts = stats["flooring_by_tercile"][tercile]
        t_total = sum(counts.values())
        print(f"  {tercile}:")
        for cat in CATEGORIES:
            n = counts.get(cat, 0)
            if n:
                print(f"    {cat:20s} {n:>6,}  ({n/t_total:.1%})")


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

    print("Deriving flooring (affluence tercile + construction/switchboard/structure nudges) ...")
    records, stats = enrich_properties(rows)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended flooring documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
