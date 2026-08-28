"""Match each property to its nearest OSM building footprint and populate
real building_area_m2 / building_footprint values.

Must run BEFORE apply_synthetic_attributes.py — that script only fills
building_area_m2 where it is still null, and never overwrites a real value,
so the real OSM-sourced values need to land first.

Input: the Overpass buildings export (an "export*.geojson" file in this
directory) and properties.geojson.

Matching: both properties and OSM building polygons are reprojected into
EPSG:7856 (GDA2020 / MGA Zone 56, metric) so distances/areas are in real
metres/m^2, not degrees. A shapely STRtree gives each property its single
nearest building in one vectorized query; a match is only accepted if that
nearest building is within 15 m of the property point. Where no building is
within 15 m, both fields are left null for apply_synthetic_attributes.py to
fill in later — this script never guesses.

Shared-building area split: when N properties (e.g. individual shop-unit
addresses inside one shopping centre, or units inside one large building)
all match to the SAME OSM building polygon, that building's real footprint
is a shared structure, not N separate buildings — so its total area is
divided by N before being assigned as building_area_m2 to each matched
property. Without this, a downstream per-m^2 cost model would price that
one shared building's full floor area once for EVERY unit address matched
to it (found during static_pricing.py's sanity check: 196 shop/unit
addresses inheriting a shared building's full footprint inflated the
portfolio's total modelled cost by ~$769M). building_footprint (the actual
polygon ring geometry) is NOT split — it still records the true shared
building's full outline, since subdividing a footprint shape into a
per-unit polygon isn't meaningful without real internal unit boundaries;
only the derived building_area_m2 used for cost scaling is divided.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import MultiPolygon, Point, shape
from shapely.ops import transform as shapely_transform

BASE_DIR = Path(__file__).resolve().parent           # Data Generation/Data Filtering
DATA_GENERATION_DIR = BASE_DIR.parent                # Data Generation
ROOT_DIR = DATA_GENERATION_DIR.parent                 # OW Task
DATA_SOURCES_DIR = ROOT_DIR / "Data Sources"

sys.path.insert(0, str(DATA_GENERATION_DIR / "Building Files"))  # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402

SOURCE_CRS = "EPSG:4326"  # property/OSM lon/lat, treated as WGS84-equivalent
METRIC_CRS = "EPSG:7856"  # GDA2020 / MGA Zone 56

MAX_MATCH_DISTANCE_M = 15.0

_to_metric = Transformer.from_crs(SOURCE_CRS, METRIC_CRS, always_xy=True)
_to_geographic = Transformer.from_crs(METRIC_CRS, SOURCE_CRS, always_xy=True)


# --------------------------------------------------------------------------
# Step 1: locate and load the OSM buildings export
# --------------------------------------------------------------------------

def find_osm_export() -> Path:
    candidates = sorted(DATA_SOURCES_DIR.glob("export*.geojson"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No 'export*.geojson' (Overpass buildings export) found in {DATA_SOURCES_DIR}")
    return candidates[0]


def load_osm_buildings(path: Path) -> list:
    data = json.loads(path.read_text())
    polygons = []
    for feature in data["features"]:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if "building" not in props or geometry is None:
            continue
        geom = shape(geometry)
        if isinstance(geom, MultiPolygon):
            # Overpass sometimes exports a single building-with-hole as a
            # multipolygon relation; take the largest part as the footprint.
            geom = max(geom.geoms, key=lambda g: g.area)
        if geom.geom_type != "Polygon" or geom.is_empty:
            continue
        polygons.append(geom)
    return polygons


# --------------------------------------------------------------------------
# Steps 2-4: nearest-building match per property, in the metric CRS
# --------------------------------------------------------------------------

def match_properties_to_buildings(properties_geojson: dict, buildings_wgs84: list) -> dict[str, dict]:
    """Returns {property_id: {"building_area_m2": ..., "building_footprint": [...]}}
    for properties with a valid (<=15 m) match. Properties with no valid
    match are simply absent from the result."""
    buildings_metric = [shapely_transform(_to_metric.transform, geom) for geom in buildings_wgs84]
    tree = STRtree(buildings_metric)

    features = properties_geojson["features"]
    property_ids = [f["properties"]["property_id"] for f in features]
    lons = np.array([f["geometry"]["coordinates"][0] for f in features])
    lats = np.array([f["geometry"]["coordinates"][1] for f in features])
    xs, ys = _to_metric.transform(lons, lats)
    points = np.array([Point(x, y) for x, y in zip(xs, ys)])

    pair_indices, distances = tree.query_nearest(
        points, max_distance=MAX_MATCH_DISTANCE_M, return_distance=True, all_matches=False
    )
    input_idx, building_idx = pair_indices

    # How many properties matched to each building — a shared building's
    # area gets divided by this count (see module docstring).
    shared_by_count = Counter(int(b) for b in building_idx)

    matches: dict[str, dict] = {}
    for i, b, dist in zip(input_idx, building_idx, distances):
        building_metric = buildings_metric[b]
        shared_by = shared_by_count[int(b)]
        area_m2 = round(building_metric.area / shared_by, 1)

        ring_x, ring_y = zip(*building_metric.exterior.coords)
        lon, lat = _to_geographic.transform(list(ring_x), list(ring_y))
        footprint = [(float(lo), float(la)) for lo, la in zip(lon, lat)]

        matches[property_ids[i]] = {
            "building_area_m2": area_m2,
            "building_footprint": footprint,
            "_match_distance_m": float(dist),
            "_shared_by": shared_by,
        }

    return matches


# --------------------------------------------------------------------------
# Steps 5-6: apply matches, validate, write, report
# --------------------------------------------------------------------------

def apply_matches(properties_geojson: dict, matches: dict[str, dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    matched_areas: list[float] = []
    shared_match_count = 0

    for feature in properties_geojson["features"]:
        props = dict(feature["properties"])
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]

        match = matches.get(props["property_id"])
        if match is not None:
            props["building_area_m2"] = match["building_area_m2"]
            props["building_footprint"] = match["building_footprint"]
            matched_areas.append(match["building_area_m2"])
            if match["_shared_by"] > 1:
                shared_match_count += 1
        # no valid match: leave building_area_m2 / building_footprint exactly
        # as they already were (null, if this is a fresh properties.geojson)

        try:
            records.append(PropertyRecord(**props))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={props.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, {"matched_areas": matched_areas, "shared_match_count": shared_match_count}


def print_summary(total: int, matched: int, matched_areas: list[float], shared_match_count: int) -> None:
    print("\n=== Summary ===")
    pct = (matched / total * 100) if total else 0.0
    print(f"Properties matched to a real OSM building footprint: {matched:,}/{total:,} ({pct:.1f}%)")
    print(f"Properties with no building within {MAX_MATCH_DISTANCE_M:.0f} m: {total - matched:,} — left null, pending synthetic fill")
    print(f"Properties sharing a building with >=1 other property (area split): {shared_match_count:,}")

    if matched_areas:
        print("\nMatched building_area_m2 distribution (real OSM data, post-split):")
        print(f"  count : {len(matched_areas):,}")
        print(f"  min   : {min(matched_areas):.1f}")
        print(f"  median: {statistics.median(matched_areas):.1f}")
        print(f"  mean  : {statistics.mean(matched_areas):.1f}")
        print(f"  max   : {max(matched_areas):.1f}")
    else:
        print("\nNo matches found — nothing to report a distribution for.")


def main() -> None:
    osm_path = find_osm_export()
    buildings = load_osm_buildings(osm_path)
    print(f"OSM buildings export: {osm_path}")
    print(f"  {len(buildings):,} building features loaded")

    print(f"\nLoading {GEOJSON_OUT.name} ...")
    properties_geojson = json.loads(GEOJSON_OUT.read_text())
    total = len(properties_geojson["features"])
    print(f"  {total:,} properties loaded")

    print("\nMatching properties to nearest OSM building (<=15 m, metric CRS EPSG:7856) ...")
    matches = match_properties_to_buildings(properties_geojson, buildings)
    print(f"  {len(matches):,} properties matched")

    print("\nValidating PropertyRecord rows ...")
    records, stats = apply_matches(properties_geojson, matches)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    print_summary(total, len(matches), stats["matched_areas"], stats["shared_match_count"])


if __name__ == "__main__":
    main()
