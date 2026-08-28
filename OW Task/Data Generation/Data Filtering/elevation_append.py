"""Append ground elevation (m AHD) to each property from the 1 m LiDAR DEM.

Inputs
 - NSW Government - Spatial Services/DEM/1 Metre/*.tif  (35 GeoTIFF tiles,
   GDA94 / MGA Zone 56, EPSG:28356 — confirmed via each tile's CRS. Property
   lon/lat are GDA2020, treated as WGS84-equivalent per project convention;
   reprojected into the DEM's CRS only at the moment of sampling, per
   steps.md, so nothing is ever stored in two coordinate systems at once.)
 - polygon.py, which outlines the project area from coords.txt — used here
   only to pick which DEM tiles are worth opening (skip tiles that don't
   intersect the project polygon at all).

Outputs
 - Called from assembly.py, after gnaf_append.py has produced the properties
   DataFrame. Looks up each property's longitude/latitude in the DEM and
   appends a ground_elevation_m_ahd column, returning the same DataFrame with
   that column added (None where a point falls outside DEM tile coverage).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.geometry import Polygon

BASE_DIR = Path(__file__).resolve().parent           # Data Generation/Data Filtering
ROOT_DIR = BASE_DIR.parent.parent                     # OW Task
DEM_DIR = ROOT_DIR / "Data Sources" / "NSW Government - Spatial Services" / "DEM" / "1 Metre"

DEM_CRS = "EPSG:28356"  # GDA94 / MGA Zone 56, as reported by the tiles themselves
SOURCE_CRS = "EPSG:4326"  # property lon/lat, treated as WGS84-equivalent

_to_dem_crs = Transformer.from_crs(SOURCE_CRS, DEM_CRS, always_xy=True)


def _list_intersecting_tiles(polygon: Polygon) -> list[rasterio.DatasetReader]:
    """Open (header-only) every DEM tile whose bounds intersect the
    polygon's bounding box, reprojected into the DEM's CRS."""
    minx, miny, maxx, maxy = polygon.bounds
    proj_x, proj_y = _to_dem_crs.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
    poly_minx, poly_maxx = min(proj_x), max(proj_x)
    poly_miny, poly_maxy = min(proj_y), max(proj_y)

    tiles = []
    for tif_path in sorted(DEM_DIR.glob("*.tif")):
        ds = rasterio.open(tif_path)
        b = ds.bounds
        intersects = not (b.right < poly_minx or b.left > poly_maxx or b.top < poly_miny or b.bottom > poly_maxy)
        if intersects:
            tiles.append(ds)
        else:
            ds.close()
    return tiles


def append_elevation(properties: pd.DataFrame, polygon: Polygon) -> pd.DataFrame:
    """Sample the 1 m DEM at each property's lon/lat and append
    ground_elevation_m_ahd. Only DEM tiles that intersect `polygon` are
    opened, and each is read via a windowed 1-pixel sample rather than being
    loaded fully into memory."""
    tiles = _list_intersecting_tiles(polygon)
    print(f"  {len(tiles)} DEM tile(s) intersect the polygon (of {len(list(DEM_DIR.glob('*.tif')))} total)")

    lons = properties["longitude"].to_numpy()
    lats = properties["latitude"].to_numpy()
    xs, ys = _to_dem_crs.transform(lons, lats)

    elevations: list[float | None] = [None] * len(properties)
    sampled = 0
    outside = 0

    for i, (x, y) in enumerate(zip(xs, ys)):
        value = None
        for ds in tiles:
            b = ds.bounds
            if b.left <= x <= b.right and b.bottom <= y <= b.top:
                raw = next(ds.sample([(x, y)]))[0]
                v = float(raw)
                if ds.nodata is None or v != ds.nodata:
                    value = v
                break
        if value is None:
            outside += 1
        else:
            sampled += 1
        elevations[i] = value

    for ds in tiles:
        ds.close()

    print(f"  sampled {sampled:,} point(s) from the DEM, {outside:,} fell outside DEM coverage")

    result = properties.copy()
    result["ground_elevation_m_ahd"] = elevations
    return result
