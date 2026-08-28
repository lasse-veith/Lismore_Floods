"""Assemble the Lismore properties dataset end to end.

Pipeline: polygon.py (project area) -> gnaf_append.py (address extraction +
cleaning, filtered/joined against the polygon) -> elevation_append.py (DEM
elevation lookup, filtered against the same polygon) -> PropertyRecord
validation -> properties.geojson / properties.csv.

Re-run freely: every output file is fully rewritten from scratch each run,
so there is nothing to clean up by hand between runs (e.g. after editing
coords.txt).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent          # Data Generation/Building Files
DATA_GENERATION_DIR = BASE_DIR.parent                # Data Generation
ROOT_DIR = DATA_GENERATION_DIR.parent                # OW Task
DATA_FILTERING_DIR = DATA_GENERATION_DIR / "Data Filtering"
OUTPUT_DIR = ROOT_DIR / "Output"

sys.path.insert(0, str(DATA_FILTERING_DIR))  # polygon.py, gnaf_append.py, elevation_append.py

from elevation_append import append_elevation  # noqa: E402
from gnaf_append import build_gnaf_properties  # noqa: E402
from polygon import get_polygon  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402

GEOJSON_OUT = OUTPUT_DIR / "properties.geojson"
CSV_OUT = OUTPUT_DIR / "properties.csv"

CSV_FIELDS = list(PropertyRecord.model_fields.keys())


# --------------------------------------------------------------------------
# Validate the assembled rows against PropertyRecord
# --------------------------------------------------------------------------

def validate_records(properties: pd.DataFrame) -> list[PropertyRecord]:
    records: list[PropertyRecord] = []
    errors = 0

    for _, row in properties.iterrows():
        try:
            record = PropertyRecord(
                property_id=row["property_id"],
                address=row["address"],
                suburb=row["suburb"],
                postcode=row["postcode"] or None,
                latitude=row["latitude"],
                longitude=row["longitude"],
                ground_elevation_m_ahd=row.get("ground_elevation_m_ahd"),
            )
            records.append(record)
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records


# --------------------------------------------------------------------------
# Write properties.geojson and properties.csv
# --------------------------------------------------------------------------

def write_geojson(records: list[PropertyRecord], path: Path) -> None:
    features = []
    for rec in records:
        data = rec.model_dump()
        lon = data.pop("longitude")
        lat = data.pop("latitude")
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": data,
            }
        )
    collection = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(collection, indent=2))


def write_csv(records: list[PropertyRecord], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for rec in records:
            data = rec.model_dump()
            for key, value in data.items():
                if value is None:
                    data[key] = ""
                elif isinstance(value, (list, tuple, dict)):
                    data[key] = json.dumps(value)
            writer.writerow(data)


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

def print_summary(records: list[PropertyRecord], polygon) -> None:
    print("\n=== Summary ===")
    print(f"Total rows matched: {len(records)}")

    print("\nBreakdown by suburb:")
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.suburb] = counts.get(rec.suburb, 0) + 1
    for suburb, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {suburb}: {count}")

    with_elevation = sum(1 for rec in records if rec.ground_elevation_m_ahd is not None)
    print(f"\nElevation coverage: {with_elevation:,}/{len(records):,} properties have ground_elevation_m_ahd")

    poly_minx, poly_miny, poly_maxx, poly_maxy = polygon.bounds
    print("\nBounding box (lon/lat):")
    print(f"  coords.txt : lon [{poly_minx:.6f}, {poly_maxx:.6f}]  lat [{poly_miny:.6f}, {poly_maxy:.6f}]")

    if records:
        lons = [r.longitude for r in records]
        lats = [r.latitude for r in records]
        print(
            f"  result     : lon [{min(lons):.6f}, {max(lons):.6f}]  lat [{min(lats):.6f}, {max(lats):.6f}]"
        )
    else:
        print("  result     : no matched rows")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    print("Building polygon from coords.txt ...")
    polygon = get_polygon()
    print(f"  polygon bounds (lon/lat): {polygon.bounds}")

    print("\n--- G-NAF address extraction ---")
    properties_df = build_gnaf_properties(polygon)

    print("\n--- DEM elevation append ---")
    properties_df = append_elevation(properties_df, polygon)

    print("\nValidating PropertyRecord rows ...")
    records = validate_records(properties_df)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nWriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    print_summary(records, polygon)


if __name__ == "__main__":
    main()
