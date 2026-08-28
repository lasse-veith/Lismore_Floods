"""Elevation-based ("bathtub") flood inundation heat map.

For each 12-hour gauge reading in flood_hydrograph.csv, flood_depth at every
DEM cell (and at every property) is:

    flood_depth = max(0, water_level - ground_elevation)

A cell/property is "inundated" wherever flood_depth > 0. This is a pure
elevation/bathtub model: no hydraulic flow, drainage, barriers, levees,
bridges or velocity are modelled (unlike flood_exposure.py elsewhere in this
pipeline, which layers a hazard-zone/levee-overtop damping model on top for
cost-engine purposes — this script deliberately does NOT reuse that, since
the brief here explicitly asks for the naive elevation-only scenario). It
also uses each property's raw ground_elevation_m_ahd, not the floor-height-
offset elevation flood_exposure.py uses for repair-cost purposes — a
property can show "inundated" here (ground flooded) while never being
"flooded" in the cost-engine sense (floor stayed dry). Treat this as a
scenario-based elevation inundation assessment, not an engineering-grade
hydraulic flood model.

Data reused from elsewhere in this pipeline (not recomputed):
  - Data Sources/flood_hydrograph.csv: interval_hour (12h steps, 0-240h —
    see flood_hydrograph.py; the brief calls this "two weeks" but the real
    generated series covers 10 days/240h, the project's established
    12-hour-timescale convention) and gauge_water_level_m_ahd.
  - Output/properties.csv: property_id, address, suburb, latitude,
    longitude, ground_elevation_m_ahd (already DEM-sampled at 1m by
    elevation_append.py) for the 20,500 properties.
  - Data Sources/NSW Government - Spatial Services/DEM/1 Metre/*.tif (35
    tiles, 1m LiDAR, EPSG:28356) + Data Generation/Data Filtering/polygon.py
    for the project-area DEM tile filter — same convention as
    elevation_append.py.

Raster resolution (ESTIMATED, visualization-only): the native DEM is 1m
across a ~12km x 8km area (~96M cells) intersecting the project polygon —
far more resolution than a browser/PNG needs and too heavy to hold ~21
copies of in memory. The mosaic is built directly at VIZ_RESOLUTION_M via
rasterio.merge's own resampling (average), never materializing the full 1m
array. This only affects the *background raster layer's* visual
granularity — every property's own flood_depth is still computed from its
precise, individually DEM-sampled ground_elevation_m_ahd in properties.csv,
completely unaffected by this downsampling.

Smooth visual timeline (cubic spline, visualization only): the real gauge
data only has a reading every 12h (flood_hydrograph.csv). A natural cubic
spline (hand-rolled, no scipy dependency — see natural_cubic_spline())
fitted through those real points is resampled every INTERP_STEP_HOURS to
give the map's raster/markers smooth motion instead of hard 12h jumps. The
real 12h readings themselves are NOT discarded or altered by this — every
one lands exactly on a fine-grid frame (frames_meta's is_reading flag marks
which frames are real vs interpolated, shown in the UI) — only the frames
*between* them are new, smoothed, presentation-only interpolation. The
spline is clipped to [0, 1.05x max observed] as a safeguard against cubic
overshoot swinging below zero or past the real peak.

Total property destroyed (static pricing): a second, independent timeline
tracked alongside the smooth visual one. Timing comes from
Output/flood_exposure.csv's real floor-level, hazard-damping-aware model
(is_flooded per property per REAL interval_hour) — NOT this script's own
simpler ground-level bathtub, and NOT the multi-month Repair Model
timeline. Dollars come ONLY from static_pricing.py's initial_estimated_cost_aud
— never actual_repair_cost_aud. So this is "the static-pricing dollar value
of every property the instant it first floods above its floor, accumulated
over the same 22 real readings" — a clean step function that only actually
changes value at real 12h marks (unaffected by the spline smoothing above,
since money is a real, discrete event, not a visual curve).

Outputs (Output/):
  - flood_heat_map.html: self-contained interactive Leaflet map — basemap +
    a per-timestamp flood-depth raster overlay + all 20,500 properties
    (colour = flood status/depth, click for details) + a smooth, fine-
    grained time slider (with play/pause) + a live summary-stats panel
    (including Total property destroyed (static pricing)). All raster
    frames and the full property/depth data are embedded inline (base64
    PNGs + JSON) so the file works standalone, no server or extra assets.
  - flood_heat_map_summary.png: static companion chart — gauge hydrograph
    (top, real 12h readings) and flooded-property count/percentage
    (bottom) over the same timeline.
  - river_level_over_time.png: river/gauge level (m AHD) vs time — real
    12h readings plus the smooth cubic-spline curve used for the map.
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge
from pyproj import Transformer

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
DATA_SOURCES = BASE_DIR / "Data Sources"
OUTPUT_DIR = BASE_DIR / "Output"
DEM_DIR = DATA_SOURCES / "NSW Government - Spatial Services" / "DEM" / "1 Metre"
HYDROGRAPH_CSV = DATA_SOURCES / "flood_hydrograph.csv"
PROPERTIES_CSV = OUTPUT_DIR / "properties.csv"
FLOOD_EXPOSURE_CSV = OUTPUT_DIR / "flood_exposure.csv"
ASSUMPTIONS_MD = BASE_DIR / "SYNTHETIC_ASSUMPTIONS.md"

OUTPUT_HTML = OUTPUT_DIR / "flood_heat_map.html"
OUTPUT_PNG = OUTPUT_DIR / "flood_heat_map_summary.png"
RIVER_LEVEL_PNG = OUTPUT_DIR / "river_level_over_time.png"

sys.path.insert(0, str(BASE_DIR / "Data Generation" / "Data Filtering"))  # polygon.py
from polygon import get_polygon  # noqa: E402

DEM_CRS = "EPSG:28356"   # GDA94 / MGA Zone 56, as reported by the DEM tiles — same as elevation_append.py
WGS84 = "EPSG:4326"      # property lon/lat, treated as WGS84-equivalent per project convention
DEM_NODATA = -9999.0
DEM_FILL_OUTSIDE = 1.0e6  # effectively "never flooded" — for cells outside DEM tile coverage

VIZ_RESOLUTION_M = 16.0    # ESTIMATED — see module docstring. Coarser than the original 8m: with
                           # INTERP_STEP_HOURS now producing ~6x more raster frames for smooth
                           # animation, per-frame resolution was traded down to keep total HTML
                           # size reasonable (raster pixel count, not visual smoothness, was the
                           # actual size driver — still a town-scale-appropriate 750x500px mosaic).
DEPTH_COLOR_CAP_M = 3.0    # ESTIMATED — depth colour scale saturates at this many metres of inundation
INTERP_STEP_HOURS = 2.0    # ESTIMATED, visualization only — see module docstring's cubic-spline note.
                           # Divides evenly into the real 12h reading spacing (6 sub-frames/interval).

# interval_hour=0 real-world anchor, user-specified: 23/02/2022 00:00. Note this is 24h
# earlier than flood_hydrograph.py's own (rougher) "Thu 24 Feb 2022 morning" phrasing for
# hour 0 — the user's timestamp is more precise and is what this script's display uses;
# it does not change any modelling/hydrograph values, only how timestamps are labelled.
# +1h per interval_hour unit, so interval_hour=240 (the last row) displays as 05/03/2022 00:00.
EVENT_START_ISO = "2022-02-23T00:00:00Z"
MARKER_DEPTH_CAP_M = 2.0   # ESTIMATED — same idea, for the property-marker colour scale


# --------------------------------------------------------------------------
# DEM mosaic (downsampled directly during merge — see module docstring)
# --------------------------------------------------------------------------

def build_dem_mosaic() -> tuple[np.ndarray, dict[str, float], int, int]:
    poly = get_polygon()
    minx, miny, maxx, maxy = poly.bounds
    to_dem = Transformer.from_crs(WGS84, DEM_CRS, always_xy=True)
    xs, ys = to_dem.transform([minx, maxx, minx, maxx], [miny, miny, maxy, maxy])
    poly_minx, poly_maxx = min(xs), max(xs)
    poly_miny, poly_maxy = min(ys), max(ys)

    all_tiles = sorted(DEM_DIR.glob("*.tif"))
    tile_paths = []
    for p in all_tiles:
        ds = rasterio.open(p)
        b = ds.bounds
        if not (b.right < poly_minx or b.left > poly_maxx or b.top < poly_miny or b.bottom > poly_maxy):
            tile_paths.append(p)
        ds.close()
    print(f"  {len(tile_paths)} DEM tile(s) intersect the polygon (of {len(all_tiles)} total)")

    datasets = [rasterio.open(p) for p in tile_paths]
    mosaic, transform = merge(
        datasets,
        res=(VIZ_RESOLUTION_M, VIZ_RESOLUTION_M),
        resampling=Resampling.average,
        nodata=DEM_NODATA,
        masked=True,
    )
    for ds in datasets:
        ds.close()

    dem = np.ma.filled(mosaic[0], DEM_FILL_OUTSIDE).astype(np.float32)
    height, width = dem.shape
    print(f"  mosaic: {width} x {height} px at {VIZ_RESOLUTION_M:.0f}m/px "
          f"({width * height:,} cells, downsampled from ~1m native)")

    to_wgs84 = Transformer.from_crs(DEM_CRS, WGS84, always_xy=True)
    corner_x = [transform.c, transform.c + width * transform.a]
    corner_y = [transform.f, transform.f + height * transform.e]
    lons, lats = to_wgs84.transform(corner_x, corner_y)
    bounds_wgs84 = {"west": min(lons), "east": max(lons), "south": min(lats), "north": max(lats)}

    return dem, bounds_wgs84, len(tile_paths), len(all_tiles)


# --------------------------------------------------------------------------
# Per-timestamp raster frame -> base64 PNG (transparent where dry)
# --------------------------------------------------------------------------

def render_frame_png_base64(depth: np.ndarray) -> str:
    flooded = depth > 0
    norm = np.clip(depth / DEPTH_COLOR_CAP_M, 0.0, 1.0)
    rgba = plt.get_cmap("Blues")(norm).astype(np.float32)
    rgba[..., 3] = np.where(flooded, 0.35 + 0.5 * norm, 0.0)
    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------
# Natural cubic spline (hand-rolled — no scipy in this environment)
# --------------------------------------------------------------------------

def natural_cubic_spline(x: np.ndarray, y: np.ndarray, xq: np.ndarray) -> np.ndarray:
    """Evaluate a natural cubic spline (zero second derivative at both
    endpoints) through (x, y) at query points xq. Standard textbook
    tridiagonal-solve algorithm (Burden & Faires) — used only to smooth the
    map's visual timeline between real 12h gauge readings, see module
    docstring."""
    n = len(x)
    h = np.diff(x).astype(float)

    alpha = np.zeros(n)
    for i in range(1, n - 1):
        alpha[i] = (3.0 / h[i]) * (y[i + 1] - y[i]) - (3.0 / h[i - 1]) * (y[i] - y[i - 1])

    ell = np.ones(n)
    mu = np.zeros(n)
    z = np.zeros(n)
    for i in range(1, n - 1):
        ell[i] = 2.0 * (x[i + 1] - x[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / ell[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / ell[i]

    b = np.zeros(n)
    c = np.zeros(n)
    d = np.zeros(n)
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (y[j + 1] - y[j]) / h[j] - h[j] * (c[j + 1] + 2.0 * c[j]) / 3.0
        d[j] = (c[j + 1] - c[j]) / (3.0 * h[j])

    idx = np.clip(np.searchsorted(x, xq, side="right") - 1, 0, n - 2)
    dx = xq - x[idx]
    return y[idx] + b[idx] * dx + c[idx] * dx**2 + d[idx] * dx**3


def step_lookup(real_hours: np.ndarray, real_values: np.ndarray, query_hours: np.ndarray) -> np.ndarray:
    """value at the latest real_hours[i] <= each query_hour — a step
    function, used for the destroyed-cost timeline (real, discrete money
    events), never smoothed like the water-level spline above."""
    idx = np.clip(np.searchsorted(real_hours, query_hours, side="right") - 1, 0, len(real_hours) - 1)
    return real_values[idx]


# --------------------------------------------------------------------------
# Hydrograph + properties
# --------------------------------------------------------------------------

def load_hydrograph() -> pd.DataFrame:
    return pd.read_csv(HYDROGRAPH_CSV)


def load_properties() -> pd.DataFrame:
    cols = ["property_id", "address", "suburb", "latitude", "longitude", "ground_elevation_m_ahd"]
    df = pd.read_csv(PROPERTIES_CSV, usecols=cols)
    before = len(df)
    df = df.dropna(subset=cols).reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        print(f"  ! dropped {dropped:,} of {before:,} properties with missing lat/lon/elevation")
    return df


def load_destroyed_timing_and_cost() -> pd.DataFrame:
    """Per property_id: first REAL interval_hour at which
    flood_exposure.py's floor-level, hazard-damping-aware model marks it
    is_flooded (NaN if never), paired with its static_pricing.py
    initial_estimated_cost_aud. Drives "Total property destroyed (static
    pricing)" — see module docstring for why this uses flood_exposure.csv's
    timing, not this script's own ground-level bathtub or the Repair
    Model's multi-month timeline."""
    exposure = pd.read_csv(FLOOD_EXPOSURE_CSV, usecols=["property_id", "interval_hour", "is_flooded"])
    first_flooded_hour = (
        exposure[exposure["is_flooded"]].groupby("property_id")["interval_hour"].min()
    )

    costs = pd.read_csv(PROPERTIES_CSV, usecols=["property_id", "initial_estimated_cost_aud"])
    costs = costs.set_index("property_id")["initial_estimated_cost_aud"].fillna(0.0)

    return costs.to_frame("cost").join(first_flooded_hour.rename("first_flooded_hour"), how="left")


def compute_destroyed_cost_by_hour(timing_df: pd.DataFrame, real_hours: np.ndarray) -> np.ndarray:
    """Cumulative static-pricing $ of properties destroyed by each real
    interval_hour — a step function over the real 12h grid only."""
    destroyed = timing_df.dropna(subset=["first_flooded_hour"])
    per_hour_cost = destroyed.groupby("first_flooded_hour")["cost"].sum().to_dict()

    cumulative = np.zeros(len(real_hours))
    running = 0.0
    for i, hour in enumerate(real_hours):
        running += per_hour_cost.get(float(hour), 0.0)
        cumulative[i] = running
    return cumulative


def compute_property_depth_matrix(elevations: np.ndarray, water_levels: np.ndarray) -> np.ndarray:
    """Shape (n_timestamps, n_properties) — depth[t, i] = max(0, water_level[t] - elevation[i])."""
    return np.maximum(0.0, water_levels[:, None] - elevations[None, :])


def compute_timestamp_stats(
    depth_matrix: np.ndarray, hours: np.ndarray, water_levels: np.ndarray, destroyed_cost: np.ndarray | None = None,
) -> list[dict]:
    stats = []
    for t, (hour, wl) in enumerate(zip(hours, water_levels)):
        col = depth_matrix[t]
        flooded = col > 0
        depths = col[flooded]
        entry = {
            "interval_hour": int(round(hour)),
            "water_level_m_ahd": float(wl),
            "flooded_count": int(flooded.sum()),
            "flooded_pct": float(flooded.mean() * 100.0),
            "mean_depth_m": float(depths.mean()) if depths.size else 0.0,
            "median_depth_m": float(np.median(depths)) if depths.size else 0.0,
            "p90_depth_m": float(np.percentile(depths, 90)) if depths.size else 0.0,
            "max_depth_m": float(depths.max()) if depths.size else 0.0,
        }
        if destroyed_cost is not None:
            entry["total_destroyed_cost_aud"] = round(float(destroyed_cost[t]), 2)
        stats.append(entry)
    return stats


# --------------------------------------------------------------------------
# Static summary PNG
# --------------------------------------------------------------------------

def write_summary_png(hydrograph: pd.DataFrame, stats: list[dict]) -> None:
    hours = hydrograph["interval_hour"].to_numpy()
    levels = hydrograph["gauge_water_level_m_ahd"].to_numpy()
    flooded_counts = [s["flooded_count"] for s in stats]
    flooded_pcts = [s["flooded_pct"] for s in stats]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

    ax1.plot(hours, levels, color="#2563eb", marker="o", markersize=3)
    peak_idx = int(np.argmax(levels))
    ax1.annotate(f"peak {levels[peak_idx]:.2f}m @ h{hours[peak_idx]}",
                 (hours[peak_idx], levels[peak_idx]), textcoords="offset points",
                 xytext=(0, 8), ha="center", fontsize=9)
    ax1.set_ylabel("Gauge water level (m AHD)")
    ax1.set_title("Lismore bathtub flood model — gauge level and property impact over time")
    ax1.grid(alpha=0.3)

    ax2.plot(hours, flooded_counts, color="#dc2626", marker="o", markersize=3, label="Flooded properties (count)")
    ax2.set_ylabel("Flooded properties (count)", color="#dc2626")
    ax2.set_xlabel("interval_hour (0 = first minor-level exceedance, 12h steps)")
    ax2.tick_params(axis="y", labelcolor="#dc2626")
    ax2.grid(alpha=0.3)

    ax2b = ax2.twinx()
    ax2b.plot(hours, flooded_pcts, color="#16a34a", marker="s", markersize=3, linestyle="--", label="Flooded (%)")
    ax2b.set_ylabel("Flooded properties (%)", color="#16a34a")
    ax2b.tick_params(axis="y", labelcolor="#16a34a")

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG, dpi=150)
    plt.close(fig)


def write_river_level_png(hours_real: np.ndarray, levels_real: np.ndarray, hours_fine: np.ndarray, levels_fine: np.ndarray) -> None:
    fig, ax = plt.subplots(figsize=(11, 6))

    ax.plot(hours_fine, levels_fine, color="#2563eb", linewidth=1.8,
            label=f"Cubic-spline interpolation (every {INTERP_STEP_HOURS:.0f}h, visualization only)")
    ax.scatter(hours_real, levels_real, color="#dc2626", zorder=5, s=35, label="Real 12h gauge reading")

    peak_idx = int(np.argmax(levels_real))
    ax.annotate(f"peak {levels_real[peak_idx]:.2f}m @ h{int(hours_real[peak_idx])}",
                (hours_real[peak_idx], levels_real[peak_idx]), textcoords="offset points",
                xytext=(0, 10), ha="center", fontsize=9)

    ax.set_xlabel("interval_hour (0 = first minor-level exceedance)")
    ax.set_ylabel("River / gauge level (m AHD)")
    ax.set_title("Lismore 2022 Flood — River Level Over Time")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(RIVER_LEVEL_PNG, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------
# Interactive HTML
# --------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lismore Flood Inundation, Bathtub Model</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  @font-face {
    font-family: 'Saira';
    src: url('../UI%20Design/presentation/assets/fonts/Saira-Variable.ttf') format('truetype');
    font-weight: 100 900; font-stretch: 50% 125%; font-style: normal;
  }
  html, body { margin: 0; padding: 0; height: 100%; font-family: 'Saira', -apple-system, sans-serif; background: #f2f3f5; }
  #map { position: absolute; top: 0; bottom: 0; left: 0; right: 0; }
  #panel {
    position: absolute; z-index: 1000; top: 12px; left: 12px; width: 320px;
    background: rgba(255,255,255,0.96); border: 1px solid rgba(16,20,27,0.08); border-radius: 14px; padding: 14px 16px;
    box-shadow: 0 12px 30px -8px rgba(16,20,27,0.22); font-size: 12.5px; color: #14161a; backdrop-filter: blur(6px);
    /* hidden until the viewer zooms in — at the default fitted extent, this
       same information is shown outside the map (dashboard KPI cards) */
    opacity: 0; pointer-events: none; transform: translateY(-6px);
    transition: opacity 0.25s ease, transform 0.25s ease;
  }
  html.zoomed-in #panel { opacity: 1; pointer-events: auto; transform: translateY(0); }
  #panel h1 { font-size: 14px; margin: 0 0 4px 0; font-weight: 600; letter-spacing: -0.01em; }
  #panel .sub { color: #6b7280; margin-bottom: 10px; font-size: 11.5px; }
  #panel .stat { display: flex; justify-content: space-between; margin: 4px 0; font-family: 'Saira', monospace; font-size: 11.5px; color: #6b7280; }
  #panel .stat b { color: #14161a; font-weight: 600; }
  #slider-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
  #slider { flex: 1; accent-color: #3b6fd6; }
  #playBtn { cursor: pointer; border: none; background: #3b6fd6; color: #fff; border-radius: 999px; padding: 6px 14px; font-size: 11.5px; font-family: 'Saira', monospace; font-weight: 600; }
  #legend {
    position: absolute; z-index: 1000; bottom: 24px; left: 12px; background: rgba(255,255,255,0.96);
    border: 1px solid rgba(16,20,27,0.08); border-radius: 14px; padding: 10px 12px; box-shadow: 0 12px 30px -8px rgba(16,20,27,0.22);
    font-size: 11.5px; color: #14161a; backdrop-filter: blur(6px);
  }
  #legend .row { display:flex; align-items:center; gap:6px; margin:3px 0; font-family: 'Saira', monospace; }
  #legend .swatch { width:10px; height:10px; border-radius:50%; display:inline-block; border:1px solid rgba(16,20,27,0.15); }
</style>
</head>
<body>
<div id="map"></div>
<div id="panel">
  <h1>Lismore Flood Inundation</h1>
  <div class="sub">Elevation ("bathtub") scenario model, smooth (cubic-spline) timeline</div>
  <div id="dateLabel" style="font-weight:700; font-size:17px;"></div>
  <div id="timeLabel" style="font-weight:600; font-size:12px; color:#6b7280; margin-bottom:6px;"></div>
  <div class="stat">Gauge level <b id="statLevel"></b></div>
  <div class="stat">Properties flooded <b id="statCount"></b></div>
  <div class="stat">Median depth (flooded) <b id="statMedian"></b></div>
  <div class="stat">Max depth <b id="statMax"></b></div>
  <div class="stat">Total property destroyed (static pricing) <b id="statDestroyed"></b></div>
  <div id="slider-row">
    <button id="playBtn">▶ Play</button>
    <input type="range" id="slider" min="0" max="__MAX_INDEX__" step="1" value="0">
  </div>
</div>
<script>
  // Embedded mode (?embedded=1): a parent page supplies one shared time
  // control for this map + the paired river-level chart, so this map's own
  // play/slider row is redundant and hidden — it still renders frames, just
  // driven by postMessage("ow-seek") from the parent instead of local input.
  if (new URLSearchParams(location.search).has('embedded')) {
    document.documentElement.classList.add('embedded');
  }
</script>
<style>.embedded #slider-row { display: none; }</style>
<div id="legend">
  <div style="font-weight:600; margin-bottom:4px;">Property status</div>
  <div class="row"><span class="swatch" style="background:#34c185"></span> Not flooded</div>
  <div class="row"><span class="swatch" style="background:#93c5fd"></span> Flooded, shallow</div>
  <div class="row"><span class="swatch" style="background:#1d4ed8"></span> Flooded, deep (&ge; __MARKER_CAP__ m)</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const FRAMES = __FRAMES_JSON__;        // [{hour, water_level, is_reading}], image is a parallel base64 array (FRAME_IMAGES)
const FRAME_IMAGES = __FRAME_IMAGES_JSON__;
const BOUNDS = __BOUNDS_JSON__;        // {west, east, south, north}
const PROPERTIES = __PROPERTIES_JSON__; // [{id, lat, lon, elev, address, suburb}]
const DEPTH_BY_TIME = __DEPTH_BY_TIME_JSON__; // [t][i] rounded depth (m), 0 = not flooded
const STATS = __STATS_JSON__;          // [{interval_hour, water_level_m_ahd, flooded_count, flooded_pct, median_depth_m, max_depth_m, total_destroyed_cost_aud, ...}]
const MARKER_DEPTH_CAP = __MARKER_CAP__;
const EVENT_START_ISO = "__EVENT_START_ISO__"; // interval_hour = 0

function formatDateTime(hour) {
  const d = new Date(EVENT_START_ISO);
  d.setUTCHours(d.getUTCHours() + hour);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

const leafletBounds = [[BOUNDS.south, BOUNDS.west], [BOUNDS.north, BOUNDS.east]];

// Locked to the exact modelled extent — no basemap is ever visible past the
// overlay's own edge, so where the flood raster stops reads as the map's own
// frame rather than an obvious data cutoff. fitBounds() sizes the view so the
// bounds fill the container on its constraining axis; maxBounds + viscosity
// then prevent panning/zooming out to reveal anything beyond it.
const map = L.map('map', { preferCanvas: true, zoomSnap: 0.05, zoomControl: false });
map.fitBounds(leafletBounds, { animate: false });
map.setMaxBounds(leafletBounds);
map.options.maxBoundsViscosity = 1.0;
map.setMinZoom(map.getZoom());
L.control.zoom({ position: 'bottomright' }).addTo(map);

// The detail panel only earns its place once the viewer has zoomed in past
// *this container's own* default fitted extent — not a one-time zoom level
// captured at page load. baseZoom is recomputed every time we (re)fit the
// bounds (including from the resize handler below, which fires when a host
// dashboard expands/collapses this map into a differently-sized card), so
// expanding to a bigger card — which naturally fits at a higher zoom — and
// then collapsing back down both correctly clear the panel instead of it
// getting stuck open after a resize with no intervening 'zoomend'.
let baseZoom = map.getZoom();
function syncZoomedInClass() {
  document.documentElement.classList.toggle('zoomed-in', map.getZoom() > baseZoom + 0.15);
}
map.on('zoomend', syncZoomedInClass);

L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  maxZoom: 20, subdomains: 'abcd',
}).addTo(map);

const overlay = L.imageOverlay('data:image/png;base64,' + FRAME_IMAGES[0], leafletBounds, { opacity: 0.8 }).addTo(map);

function depthColor(depth) {
  if (depth <= 0) return '#34c185';
  const t = Math.max(0, Math.min(1, depth / MARKER_DEPTH_CAP));
  // light -> deep blue interpolation for flooded severity (not flooded stays green above)
  const r1 = 147, g1 = 197, b1 = 253;  // #93c5fd shallow
  const r2 = 29,  g2 = 78,  b2 = 216;  // #1d4ed8 deep
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r},${g},${b})`;
}

const markers = new Array(PROPERTIES.length);
const popupOwner = { index: -1 };
let currentT = 0;

function popupHtml(i) {
  const p = PROPERTIES[i];
  const d = DEPTH_BY_TIME[currentT][i];
  const f = FRAMES[currentT];
  return `<div style="font-size:12px; line-height:1.5;">`
    + `<b>${p.address}</b><br>${p.suburb}<br>`
    + `Ground elevation: ${p.elev.toFixed(2)} m AHD<br>`
    + `Date/time: ${formatDateTime(f.hour)} (h${f.hour})<br>`
    + `Water level: ${f.water_level.toFixed(2)} m AHD<br>`
    + `Flood depth: ${d > 0 ? d.toFixed(2) + ' m' : '0 m'}<br>`
    + `Status: <b style="color:${d > 0 ? '#1d4ed8' : '#34c185'}">${d > 0 ? 'INUNDATED' : 'Not flooded'}</b>`
    + `</div>`;
}

for (let i = 0; i < PROPERTIES.length; i++) {
  const p = PROPERTIES[i];
  const m = L.circleMarker([p.lat, p.lon], {
    radius: 3, weight: 0.5, color: 'rgba(5,7,10,0.55)', fillColor: '#34c185', fillOpacity: 0.9,
  }).addTo(map);
  m.on('click', () => { popupOwner.index = i; m.bindPopup(popupHtml(i)).openPopup(); });
  markers[i] = m;
}

function updateFrame(t) {
  currentT = t;
  overlay.setUrl('data:image/png;base64,' + FRAME_IMAGES[t]);
  const depths = DEPTH_BY_TIME[t];
  for (let i = 0; i < markers.length; i++) {
    const d = depths[i];
    markers[i].setStyle({ fillColor: depthColor(d) });
  }
  const f = FRAMES[t];
  const s = STATS[t];
  const readingTag = f.is_reading ? '(real 12h reading)' : '(interpolated, smooth visual only)';
  document.getElementById('dateLabel').textContent = formatDateTime(f.hour);
  document.getElementById('timeLabel').innerHTML = `Hour ${f.hour} (day ${(f.hour / 24).toFixed(1)}) `
    + `<span style="font-style:italic;">${readingTag}</span>`;
  document.getElementById('statLevel').textContent = f.water_level.toFixed(2) + ' m AHD';
  document.getElementById('statCount').textContent = s.flooded_count.toLocaleString() + ' (' + s.flooded_pct.toFixed(1) + '%)';
  document.getElementById('statMedian').textContent = s.median_depth_m.toFixed(2) + ' m';
  document.getElementById('statMax').textContent = s.max_depth_m.toFixed(2) + ' m';
  document.getElementById('statDestroyed').textContent = '$' + Math.round(s.total_destroyed_cost_aud).toLocaleString();
  if (popupOwner.index >= 0 && markers[popupOwner.index].isPopupOpen()) {
    markers[popupOwner.index].setPopupContent(popupHtml(popupOwner.index));
  }

  // broadcast this frame's real stats so a host dashboard can drive its own
  // live KPI cards without re-deriving anything from the raw CSVs itself
  window.parent.postMessage({
    type: 'ow-flood-stats',
    frame: t,
    hour: f.hour,
    dateLabel: formatDateTime(f.hour),
    isReading: f.is_reading,
    waterLevelM: f.water_level,
    floodedCount: s.flooded_count,
    floodedPct: s.flooded_pct,
    medianDepthM: s.median_depth_m,
    maxDepthM: s.max_depth_m,
    destroyedCostAud: s.total_destroyed_cost_aud,
  }, '*');
}

document.getElementById('slider').addEventListener('input', (e) => updateFrame(+e.target.value));

const PLAY_INTERVAL_MS = 150; // fast enough that the fine, spline-interpolated frames read as smooth motion
let playing = false, playTimer = null;
document.getElementById('playBtn').addEventListener('click', () => {
  playing = !playing;
  document.getElementById('playBtn').textContent = playing ? '⏸ Pause' : '▶ Play';
  if (playing) {
    playTimer = setInterval(() => {
      const slider = document.getElementById('slider');
      let next = (+slider.value + 1) % FRAMES.length;
      slider.value = next;
      updateFrame(next);
    }, PLAY_INTERVAL_MS);
  } else {
    clearInterval(playTimer);
  }
});

updateFrame(0);

window.addEventListener('resize', () => {
  map.invalidateSize();
  map.fitBounds(leafletBounds, { animate: false });
  map.setMinZoom(map.getZoom());
  baseZoom = map.getZoom();
  syncZoomedInClass(); // explicit re-check — fitBounds may not fire 'zoomend' if the zoom level happens not to change
});

// --- shared time-control protocol (see 02-flood-model.html) ---
// Tells the parent how many frames exist so it can size one shared slider
// covering both this map and the paired river-level chart, then seeks on
// command instead of running its own independent play/slider loop.
window.parent.postMessage({ type: 'ow-flood-ready', maxIndex: FRAMES.length - 1 }, '*');
window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'ow-seek') updateFrame(e.data.frame);
});
</script>
</body>
</html>
"""


def write_interactive_html(
    frames_meta: list[dict], frame_images: list[str], bounds: dict,
    properties: pd.DataFrame, depth_by_time: np.ndarray, stats: list[dict],
) -> None:
    ids = properties["property_id"].to_numpy()
    lats = properties["latitude"].to_numpy()
    lons = properties["longitude"].to_numpy()
    elevs = properties["ground_elevation_m_ahd"].to_numpy()
    addresses = properties["address"].to_numpy()
    suburbs = properties["suburb"].to_numpy()
    properties_json = [
        {
            "id": str(ids[i]),
            "lat": round(float(lats[i]), 6),
            "lon": round(float(lons[i]), 6),
            "elev": round(float(elevs[i]), 2),
            "address": str(addresses[i]),
            "suburb": str(suburbs[i]),
        }
        for i in range(len(properties))
    ]
    depth_by_time_json = [[round(float(v), 2) for v in row] for row in depth_by_time]

    html = HTML_TEMPLATE
    html = html.replace("__MAX_INDEX__", str(len(frames_meta) - 1))
    html = html.replace("__MARKER_CAP__", json.dumps(MARKER_DEPTH_CAP_M))
    html = html.replace("__EVENT_START_ISO__", EVENT_START_ISO)
    html = html.replace("__FRAMES_JSON__", json.dumps(frames_meta))
    html = html.replace("__FRAME_IMAGES_JSON__", json.dumps(frame_images))
    html = html.replace("__BOUNDS_JSON__", json.dumps(bounds))
    html = html.replace("__PROPERTIES_JSON__", json.dumps(properties_json))
    html = html.replace("__DEPTH_BY_TIME_JSON__", json.dumps(depth_by_time_json))
    html = html.replace("__STATS_JSON__", json.dumps(stats))

    OUTPUT_HTML.write_text(html)


# --------------------------------------------------------------------------
# Documentation + summary
# --------------------------------------------------------------------------

def append_assumptions_doc(
    stats: list[dict], n_properties: int, dem_shape: tuple[int, int], n_tiles_used: int, n_tiles_total: int,
    total_destroyed_final: float, n_real_readings: int,
) -> None:
    peak = max(stats, key=lambda s: s["flooded_count"])
    lines = [
        "# Flood Inundation Heat Map (heat_map.py)",
        "",
        "Appended by heat_map.py — this section is regenerated (replaced) on every run.",
        "",
        "## Model: pure elevation ('bathtub'), not hydraulic",
        "",
        "`flood_depth = max(0, water_level - ground_elevation)`, evaluated at every DEM cell",
        "and at every property's own `ground_elevation_m_ahd`. No flow, drainage, barriers,",
        "levees, bridges or velocity are modelled — this is a scenario-based elevation",
        "inundation assessment, not an engineering-grade hydraulic flood model. It also",
        "deliberately does not reuse flood_exposure.py's hazard-zone/levee-overtop damping or",
        "floor-height-offset elevation (used there for repair-cost purposes) — this script",
        "answers a different, simpler question (is the ground around this property wet?), so",
        "its flooded counts are not directly comparable to flood_exposure.py's.",
        "",
        "## Timeline",
        "",
        f"Reuses Data Sources/flood_hydrograph.csv as-is: {n_real_readings} real gauge readings",
        f"on its 12h-step convention (interval_hour {stats[0]['interval_hour']}-{stats[-1]['interval_hour']}).",
        "",
        f"Displayed dates/times use a real-world anchor: interval_hour=0 = {EVENT_START_ISO}",
        "(user-specified), +1h per interval_hour unit. This is 24h earlier than",
        "flood_hydrograph.py's own rougher \"Thu 24 Feb 2022 morning\" phrasing for hour 0 — the",
        "user's timestamp is more precise and is used here for display only; it does not change",
        "any hydrograph/model values, only how each timestamp is labelled in the UI.",
        "",
        "## Smooth visual timeline (cubic spline, ESTIMATED, visualization only)",
        "",
        f"The real gauge data only has a reading every 12h. A hand-rolled natural cubic spline",
        f"(no scipy in this environment — see natural_cubic_spline(), a standard tridiagonal-",
        f"solve implementation) fitted through those {n_real_readings} real points is resampled",
        f"every {INTERP_STEP_HOURS:.0f}h ({len(stats)} frames total) so the map's raster and",
        "property markers move smoothly instead of jumping hard between readings. The real 12h",
        "readings are not discarded or altered — every one lands exactly on a fine-grid frame",
        "(flagged `is_reading` in the data, shown in the UI as \"(real 12h reading)\" vs",
        "\"(interpolated — smooth visual only)\"); only the frames *between* them are new,",
        "smoothed, presentation-only interpolation. The spline is clipped to",
        "[0, 1.05x max observed level] as a safeguard against cubic overshoot dipping below",
        "zero or swinging past the real peak between anchor points.",
        "",
        "## Total property destroyed (static pricing)",
        "",
        "A second, independent timeline tracked alongside the smooth visual one above. Timing",
        "comes from Output/flood_exposure.csv's real floor-level, hazard-damping-aware model",
        "(first REAL interval_hour at which each property's `is_flooded` goes true) — NOT this",
        "script's own simpler ground-level bathtub, and NOT the multi-month Repair Model",
        "timeline. Dollars come ONLY from static_pricing.py's `initial_estimated_cost_aud` —",
        "never `actual_repair_cost_aud`. The result is a step function that only actually",
        "changes value at the real 12h marks (money is a real, discrete event, unaffected by",
        f"the spline smoothing above). Final total once every property that ever floods above",
        f"its floor has done so: ${total_destroyed_final:,.0f}.",
        "",
        "## DEM raster resolution (ESTIMATED, visualization-only)",
        "",
        f"The native DEM is 1m LiDAR ({n_tiles_used} of {n_tiles_total} tiles intersect the project",
        f"polygon, ~96M cells at 1m). Rebuilding that at 1m for all {len(stats)} smooth-timeline",
        f"frames was unnecessary for a browser/PNG-scale visualization and too heavy to hold",
        f"repeatedly in memory, so `rasterio.merge` builds the mosaic directly at",
        f"{VIZ_RESOLUTION_M:.0f}m/px (average resampling) — final mosaic {dem_shape[1]} x {dem_shape[0]} px",
        f"({dem_shape[0]*dem_shape[1]:,} cells). Coarser than an earlier 8m version: with the",
        "cubic-spline smoothing now producing several times more frames, per-frame resolution",
        "was traded down to keep total HTML size reasonable — this only affects the background",
        "raster layer's visual granularity, every property's own flood_depth still comes from",
        "its precise, individually DEM-sampled `ground_elevation_m_ahd` in properties.csv,",
        "completely unaffected by this downsampling.",
        "",
        "## Colour scales (ESTIMATED, not sourced)",
        "",
        f"- Raster depth colour saturates at {DEPTH_COLOR_CAP_M:.0f}m (Blues colormap, alpha",
        "  scales with depth, fully transparent where dry or outside DEM coverage).",
        f"- Property marker colour saturates at {MARKER_DEPTH_CAP_M:.0f}m (green = dry,",
        "  yellow->red = shallow->deep).",
        "",
        "## CRS handling",
        "",
        "Same convention as elevation_append.py: DEM tiles are GDA94/MGA Zone 56",
        f"({DEM_CRS}), project lon/lat treated as WGS84-equivalent ({WGS84}). The mosaic's",
        "corner coordinates are reprojected to WGS84 to place the Leaflet ImageOverlay — an",
        "axis-aligned bounding-box approximation of a locally near-conformal projection,",
        "consistent with this project's existing GDA2020~WGS84 approximation elsewhere.",
        "",
        "## Outputs",
        "",
        f"- `flood_heat_map.html`: self-contained (all {len(stats)} smooth-timeline raster frames",
        f"  + all {n_properties:,} properties' per-frame depths embedded inline as base64/JSON —",
        "  no server or external assets besides the Leaflet CDN files and OSM basemap tiles).",
        "- `flood_heat_map_summary.png`: static hydrograph + flooded-count/percentage chart",
        "  (real 12h readings only, unaffected by the spline smoothing above).",
        "- `river_level_over_time.png`: river/gauge level (m AHD) vs time — real 12h readings",
        "  plus the smooth cubic-spline curve used for the map.",
        "",
        "Known perf tradeoff: the property layer restyles all markers on every slider tick",
        "via individual `setStyle` calls — fine interactively, but a canvas-batched custom",
        "renderer would be the next optimization if smoother scrubbing at this property count",
        "is needed.",
        "",
        "## Result (this run)",
        "",
        f"Peak impact: {peak['flooded_count']:,} properties flooded ({peak['flooded_pct']:.1f}%)",
        f"at interval_hour {peak['interval_hour']} (water level {peak['water_level_m_ahd']:.2f}m AHD),",
        f"median depth among flooded {peak['median_depth_m']:.2f}m, max {peak['max_depth_m']:.2f}m.",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Flood Inundation Heat Map (heat_map.py)"
    import re
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(
    stats: list[dict], n_properties: int, dem_shape: tuple[int, int], n_tiles_used: int, n_tiles_total: int,
) -> None:
    print("\n=== Summary ===")
    print(f"DEM mosaic: {n_tiles_used}/{n_tiles_total} tiles -> {dem_shape[1]} x {dem_shape[0]} px at {VIZ_RESOLUTION_M:.0f}m/px")
    print(f"Properties: {n_properties:,}")
    print(f"Smooth-timeline frames: {len(stats)} (interval_hour {stats[0]['interval_hour']}-{stats[-1]['interval_hour']}, "
          f"every {INTERP_STEP_HOURS:.0f}h) — table below shows real 12h readings only")
    print("\ninterval_hour  water_level  flooded_count  flooded_pct  median_depth  max_depth  destroyed_$(static)")
    for s in stats:
        if s["interval_hour"] % 12 != 0:
            continue
        print(f"  {s['interval_hour']:>4}          {s['water_level_m_ahd']:>6.2f}      "
              f"{s['flooded_count']:>6,}         {s['flooded_pct']:>5.1f}%      "
              f"{s['median_depth_m']:>5.2f}        {s['max_depth_m']:>5.2f}      "
              f"${s['total_destroyed_cost_aud']:>13,.0f}")
    peak = max(stats, key=lambda s: s["flooded_count"])
    print(f"\nPeak impact: {peak['flooded_count']:,} properties ({peak['flooded_pct']:.1f}%) at "
          f"interval_hour {peak['interval_hour']} (water level {peak['water_level_m_ahd']:.2f}m AHD)")
    print(f"Final total property destroyed (static pricing): ${stats[-1]['total_destroyed_cost_aud']:,.0f}")
    print(f"\nWrote {OUTPUT_HTML.relative_to(BASE_DIR)} ({OUTPUT_HTML.stat().st_size / 1e6:.1f} MB)")
    print(f"Wrote {OUTPUT_PNG.relative_to(BASE_DIR)} ({OUTPUT_PNG.stat().st_size / 1e3:.0f} KB)")
    print(f"Wrote {RIVER_LEVEL_PNG.relative_to(BASE_DIR)} ({RIVER_LEVEL_PNG.stat().st_size / 1e3:.0f} KB)")


def main() -> None:
    print("Building DEM mosaic ...")
    dem, bounds, n_tiles_used, n_tiles_total = build_dem_mosaic()

    print(f"\nLoading {HYDROGRAPH_CSV.name} ...")
    hydrograph = load_hydrograph()
    hours_real = hydrograph["interval_hour"].to_numpy(dtype=float)
    levels_real = hydrograph["gauge_water_level_m_ahd"].to_numpy(dtype=float)
    print(f"  {len(hydrograph)} real 12h readings, interval_hour {hours_real[0]:.0f}-{hours_real[-1]:.0f}")

    print(f"Fitting natural cubic spline for smooth visuals (every {INTERP_STEP_HOURS:.0f}h) ...")
    hours_fine = np.arange(hours_real[0], hours_real[-1] + 1, INTERP_STEP_HOURS)
    levels_fine = natural_cubic_spline(hours_real, levels_real, hours_fine)
    # Clipped to the real recorded peak exactly, not a soft 1.05x margin — a
    # natural cubic spline can overshoot past its anchor points between real
    # readings, which previously let the interpolated level (and therefore
    # per-property flood depth near the peak) run up to 5% above the true
    # 14.4m record. The real peak reading itself is unaffected: the spline
    # passes through every real anchor point exactly, so clipping at that
    # ceiling only trims overshoot *between* readings.
    levels_fine = np.clip(levels_fine, 0.0, levels_real.max())
    is_reading = np.isin(hours_fine, hours_real)
    print(f"  {len(hours_fine)} fine frames ({int(is_reading.sum())} exact real readings, "
          f"{int((~is_reading).sum())} interpolated)")

    print(f"\nLoading {PROPERTIES_CSV.name} ...")
    properties = load_properties()
    print(f"  {len(properties):,} properties")

    print("\nRendering raster frames (smooth timeline) ...")
    frames_meta = []
    frame_images = []
    for hour, wl, real in zip(hours_fine, levels_fine, is_reading):
        depth = np.maximum(0.0, wl - dem)
        frame_images.append(render_frame_png_base64(depth))
        frames_meta.append({"hour": int(round(hour)), "water_level": float(wl), "is_reading": bool(real)})
    print(f"  {len(frame_images)} frames rendered")

    print("\nComputing per-property flood depth for every fine timestamp ...")
    elevations = properties["ground_elevation_m_ahd"].to_numpy()
    depth_by_time = compute_property_depth_matrix(elevations, levels_fine)

    print(f"\nLoading {FLOOD_EXPOSURE_CSV.name} for 'Total property destroyed (static pricing)' timing ...")
    timing_df = load_destroyed_timing_and_cost()
    destroyed_real = compute_destroyed_cost_by_hour(timing_df, hours_real)
    destroyed_fine = step_lookup(hours_real, destroyed_real, hours_fine)
    print(f"  Final total static-pricing cost destroyed: ${destroyed_real[-1]:,.0f}")

    stats_fine = compute_timestamp_stats(depth_by_time, hours_fine, levels_fine, destroyed_cost=destroyed_fine)

    print("\nComputing stats on the real 12h grid (for the static summary PNG) ...")
    depth_by_time_real = compute_property_depth_matrix(elevations, levels_real)
    stats_real = compute_timestamp_stats(depth_by_time_real, hours_real, levels_real)

    print(f"\nWriting {OUTPUT_PNG.name} ...")
    write_summary_png(hydrograph, stats_real)

    print(f"Writing {RIVER_LEVEL_PNG.name} ...")
    write_river_level_png(hours_real, levels_real, hours_fine, levels_fine)

    print(f"Writing {OUTPUT_HTML.name} ...")
    write_interactive_html(frames_meta, frame_images, bounds, properties, depth_by_time, stats_fine)

    append_assumptions_doc(
        stats_fine, len(properties), dem.shape, n_tiles_used, n_tiles_total,
        float(destroyed_real[-1]), len(hydrograph),
    )
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats_fine, len(properties), dem.shape, n_tiles_used, n_tiles_total)


if __name__ == "__main__":
    main()
