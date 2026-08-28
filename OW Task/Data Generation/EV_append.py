"""Sample how many of each property's vehicles are EVs.

Adds ev_count: for a property with vehicle_count vehicles, each vehicle is
independently an EV with probability EV_RATE (1.67%), so ev_count is a
Binomial(vehicle_count, EV_RATE) draw, seeded per property_id for
reproducibility. Properties with vehicle_count == 0 (or null) get ev_count
0 (or left null, respectively) — you can't own an EV without owning a car.

Only fills ev_count where it is still null (re-runnable without changing
already-sampled values). Re-validates against the updated PropertyRecord
(ev_count, plus the new ev_count <= vehicle_count check) before overwriting
properties.csv/properties.geojson in place.

Requires vehicle_count to already be populated — i.e. run this after
apply_synthetic_attributes.py.
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

EV_RATE = 0.0167  # assumed per-vehicle EV probability


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def sample_ev_count(vehicle_count: int, rng: random.Random) -> int:
    return sum(1 for _ in range(vehicle_count) if rng.random() < EV_RATE)


def enrich_properties(features: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": 0,
        "sampled": 0,
        "missing_vehicle_count": 0,
        "ev_count_counts": {},
        "properties_with_ev": 0,
        "total_vehicles": 0,
        "total_evs": 0,
    }

    for feature in features:
        props = dict(feature["properties"])
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]
        stats["total"] += 1

        vehicle_count = props.get("vehicle_count")
        if vehicle_count is None:
            stats["missing_vehicle_count"] += 1
        elif props.get("ev_count") is None:
            rng = row_rng(props["property_id"])
            ev_count = sample_ev_count(vehicle_count, rng)
            props["ev_count"] = ev_count
            stats["sampled"] += 1

        if props.get("ev_count") is not None:
            stats["ev_count_counts"][props["ev_count"]] = stats["ev_count_counts"].get(props["ev_count"], 0) + 1
            stats["total_evs"] += props["ev_count"]
            if props["ev_count"] > 0:
                stats["properties_with_ev"] += 1
        if vehicle_count is not None:
            stats["total_vehicles"] += vehicle_count

        try:
            records.append(PropertyRecord(**props))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={props.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def append_assumptions_doc(stats: dict) -> None:
    observed_rate = stats["total_evs"] / stats["total_vehicles"] if stats["total_vehicles"] else 0.0
    lines = [
        "# EV Ownership (EV_append.py)",
        "",
        "Appended by EV_append.py — this section is regenerated (replaced) on every run.",
        "",
        f"`ev_count` is sampled per vehicle: each of a property's `vehicle_count` vehicles",
        f"is independently an EV with probability `EV_RATE = {EV_RATE:.4f}` ({EV_RATE:.2%}),",
        "i.e. `ev_count ~ Binomial(vehicle_count, EV_RATE)`, seeded per `property_id`.",
        f"**{EV_RATE:.2%} is an assumed rate, not sourced from real NSW EV registration data**",
        "— no such dataset was provided. `vehicle_count` itself remains capped at \"3+\" -> 3",
        "(see apply_synthetic_attributes.py), so this likely slightly undercounts EVs for the",
        "small number of households with 4+ vehicles.",
        "",
        f"This run: {stats['total_evs']:,} EVs across {stats['total_vehicles']:,} total vehicles",
        f"(observed rate {observed_rate:.3%}, vs the assumed {EV_RATE:.2%} — differs only by",
        "sampling noise).",
        "",
    ]
    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# EV Ownership (EV_append.py)"
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
    if stats["missing_vehicle_count"]:
        print(f"  ! {stats['missing_vehicle_count']:,} properties had no vehicle_count set, left ev_count null")
    print(f"Sampled this run: {stats['sampled']:,}")

    print("\nev_count distribution:")
    for count, n in sorted(stats["ev_count_counts"].items()):
        print(f"  {count} EV(s): {n:>6,}")

    print(f"\nProperties with at least one EV: {stats['properties_with_ev']:,}")
    observed_rate = stats["total_evs"] / stats["total_vehicles"] if stats["total_vehicles"] else 0.0
    print(f"Total EVs: {stats['total_evs']:,} / total vehicles: {stats['total_vehicles']:,} "
          f"(observed rate {observed_rate:.3%}, assumed rate {EV_RATE:.2%})")


def main() -> None:
    print(f"Loading {GEOJSON_OUT.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())

    print("Sampling ev_count (per-vehicle EV rate) ...")
    records, stats = enrich_properties(geojson_data["features"])
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended EV documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
