"""credibility_map.py — self-contained interactive HTML map: "who's informing
this estimate?" Click any still-incomplete (as of a representative snapshot
day) flooded property and see exactly which completed nearby properties feed
its credibility-blended estimate, with each connecting line's thickness/
opacity set by that property's REAL combined weight — the same
distance x characteristic-similarity x recency mechanics credibility_model.py
itself uses (same constants: DISTANCE_RANGE_KM, RECENCY_HALFLIFE_DAYS, the
same area_bucket()/affluence_tercile() bucketing, haversine_km()), just
collapsed into one overall weight per property pair for a single visual
instead of credibility_model.py's real per-component blend.

Snapshot day: the median job_end_day across all flooded properties, so the
map shows a realistic, roughly half-complete portfolio — some jobs already
delivering evidence, plenty still waiting on it.

Output: Output/credibility_map.html — self-contained (all data embedded
inline as JSON, map drawn with Leaflet via CDN, no server needed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"
OUTPUT_HTML = OUTPUT_DIR / "credibility_map.html"

sys.path.insert(0, str(BASE_DIR))
from credibility_model import (  # noqa: E402
    DISTANCE_RANGE_KM,
    RECENCY_HALFLIFE_DAYS,
    affluence_tercile,
    area_bucket,
    haversine_km,
)

# Overall similarity variables for this visualization's single combined
# weight (credibility_model.py's real per-component blend uses a slightly
# different subset per cost component — this is the union of all of them,
# averaged, so one line can represent "how similar are these two
# properties overall" rather than nine separate per-component figures).
SIMILARITY_VARS = ["suburb", "construction_type", "flooring", "switchboard_type", "kitchen_spec", "area_bucket", "affluence_tercile"]

TOP_K_NEIGHBORS = 8
MIN_WEIGHT = 0.02  # drop near-zero connections so the visual stays legible
BOUNDS_PAD_DEG = 0.006


def build_dataset() -> dict:
    props = pd.read_csv(OUTPUT_DIR / "properties.csv")
    affected = props[props["initial_estimated_cost_aud"] > 0].copy()
    affected["area_bucket"] = affected["building_area_m2"].apply(area_bucket)
    affected["affluence_tercile"] = affected["affluence_score"].apply(affluence_tercile)

    now_day = int(affected["job_end_day"].median())
    completed = affected[affected["job_end_day"] <= now_day].reset_index(drop=True)
    incomplete = affected[affected["job_end_day"] > now_day].reset_index(drop=True)

    lat_i = incomplete["latitude"].to_numpy()
    lon_i = incomplete["longitude"].to_numpy()
    lat_j = completed["latitude"].to_numpy()
    lon_j = completed["longitude"].to_numpy()
    end_day_j = completed["job_end_day"].to_numpy()

    dist_km = haversine_km(lat_i[:, None], lon_i[:, None], lat_j[None, :], lon_j[None, :])
    w_distance = np.exp(-dist_km / DISTANCE_RANGE_KM)
    w_recency = np.exp(-(now_day - end_day_j) / RECENCY_HALFLIFE_DAYS)[None, :]

    char_sum = np.zeros((len(incomplete), len(completed)))
    for var in SIMILARITY_VARS:
        vi = incomplete[var].to_numpy()
        vj = completed[var].to_numpy()
        char_sum += (vi[:, None] == vj[None, :]).astype(float)
    w_characteristic = char_sum / len(SIMILARITY_VARS)

    weight = w_distance * w_characteristic * w_recency

    completed_points = [
        {"id": str(r.property_id), "lat": round(float(r.latitude), 6), "lon": round(float(r.longitude), 6), "suburb": str(r.suburb)}
        for r in completed.itertuples()
    ]

    incomplete_points = []
    for i, r in enumerate(incomplete.itertuples()):
        row_w = weight[i]
        order = np.argsort(row_w)[::-1][:TOP_K_NEIGHBORS]
        order = order[row_w[order] > MIN_WEIGHT]
        neighbors = [{"j": int(j), "w": round(float(row_w[j]), 4)} for j in order]
        incomplete_points.append({
            "id": str(r.property_id), "lat": round(float(r.latitude), 6), "lon": round(float(r.longitude), 6),
            "suburb": str(r.suburb), "neighbors": neighbors,
        })

    all_lat = affected["latitude"].to_numpy()
    all_lon = affected["longitude"].to_numpy()
    bounds = {
        "south": float(all_lat.min()) - BOUNDS_PAD_DEG, "north": float(all_lat.max()) + BOUNDS_PAD_DEG,
        "west": float(all_lon.min()) - BOUNDS_PAD_DEG, "east": float(all_lon.max()) + BOUNDS_PAD_DEG,
    }

    return {
        "nowDay": now_day,
        "bounds": bounds,
        "completed": completed_points,
        "incomplete": incomplete_points,
        "totalAffected": int(len(affected)),
        "completedCount": int(len(completed)),
        "incompleteCount": int(len(incomplete)),
    }


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lismore 2022 Flood, Who's Informing This Estimate</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  @font-face {
    font-family: 'Saira';
    src: url('../UI%20Design/presentation/assets/fonts/Saira-Variable.ttf') format('truetype');
    font-weight: 100 900; font-stretch: 50% 125%; font-style: normal;
  }
  html, body { margin: 0; padding: 0; height: 100%; font-family: 'Saira', -apple-system, sans-serif; background: #f2f3f5; }
  #map { position: absolute; inset: 0; }
  #hint {
    position: absolute; z-index: 1000; top: 12px; left: 12px; max-width: 260px;
    background: rgba(255,255,255,0.96); border: 1px solid rgba(16,20,27,0.08); border-radius: 14px; padding: 12px 14px;
    box-shadow: 0 12px 30px -8px rgba(16,20,27,0.22); font-size: 11.5px; color: #6b7280; backdrop-filter: blur(6px);
  }
  #hint b { color: #14161a; }
  #panel {
    position: absolute; z-index: 1000; top: 12px; right: 12px; width: 230px;
    background: rgba(255,255,255,0.97); border: 1px solid rgba(16,20,27,0.08); border-radius: 14px; padding: 14px 16px;
    box-shadow: 0 12px 30px -8px rgba(16,20,27,0.22); font-size: 12px; color: #14161a; backdrop-filter: blur(6px);
    opacity: 0; pointer-events: none; transform: translateY(-6px);
    transition: opacity 0.2s ease, transform 0.2s ease;
  }
  #panel.visible { opacity: 1; pointer-events: auto; transform: translateY(0); }
  #panel h1 { font-size: 12.5px; margin: 0 0 8px 0; font-weight: 600; }
  #panel .stat { display: flex; justify-content: space-between; margin: 5px 0; font-family: 'Saira', monospace; font-size: 11px; color: #6b7280; }
  #panel .stat b { color: #14161a; font-weight: 600; }
  #panel .strength-bar {
    margin-top: 10px; height: 8px; border-radius: 999px;
    background: linear-gradient(to right, #c7ccd3, #d64545);
  }
  #panel .strength-labels { display: flex; justify-content: space-between; font-size: 9.5px; color: #9aa1ac; margin-top: 4px; font-family: 'Saira', monospace; }
  #legend {
    position: absolute; z-index: 1000; bottom: 14px; left: 12px; background: rgba(255,255,255,0.96);
    border: 1px solid rgba(16,20,27,0.08); border-radius: 14px; padding: 9px 12px; box-shadow: 0 12px 30px -8px rgba(16,20,27,0.22);
    font-size: 11px; color: #14161a; backdrop-filter: blur(6px);
  }
  #legend .row { display: flex; align-items: center; gap: 6px; margin: 3px 0; font-family: 'Saira', monospace; }
  #legend .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
  #legend .grad-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(16,20,27,0.08); }
  #legend .grad-bar { width: 60px; height: 7px; border-radius: 999px; background: linear-gradient(to right, #c7ccd3, #d64545); }
</style>
</head>
<body>
<div id="map"></div>
<div id="hint">
  Click any <b>incomplete</b> property to see which nearby completed repairs are informing its
  credibility-blended estimate: connection colour and thickness both encode the combined
  distance &times; similarity &times; recency weight.
</div>
<div id="panel">
  <h1 id="panelTitle">-</h1>
  <div class="stat">Weighted neighbors <b id="panelCount">-</b></div>
  <div class="stat">Strongest weight <b id="panelMaxWeight">-</b></div>
  <div class="stat">Weakest weight <b id="panelMinWeight">-</b></div>
  <div class="stat">Approx. credibility (Z) <b id="panelZ">-</b></div>
  <div class="strength-bar"></div>
  <div class="strength-labels"><span>Weak</span><span>Strong</span></div>
</div>
<div id="legend">
  <div class="row"><span class="dot" style="background:#9aa1ac;"></span> Completed (evidence)</div>
  <div class="row"><span class="dot" style="background:#3b6fd6;"></span> Incomplete (click me)</div>
  <div class="grad-row"><span class="grad-bar"></span> Connection strength</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  if (new URLSearchParams(location.search).has('embedded')) {
    document.documentElement.classList.add('embedded');
  }
</script>
<style>.embedded #hint { display: none; }</style>
<script>
const DATA = __DATA_JSON__;
const BUHLMANN_K = 250.0; // matches credibility_model.py's own calibration constant

const bounds = DATA.bounds;
const leafletBounds = [[bounds.south, bounds.west], [bounds.north, bounds.east]];

const map = L.map('map', { preferCanvas: true, zoomSnap: 0.05, zoomControl: false });
map.fitBounds(leafletBounds, { animate: false });
map.setMaxBounds(leafletBounds);
map.options.maxBoundsViscosity = 1.0;
map.setMinZoom(map.getZoom());
L.control.zoom({ position: 'bottomright' }).addTo(map);
window.addEventListener('resize', () => {
  map.invalidateSize();
  map.fitBounds(leafletBounds, { animate: false });
  map.setMinZoom(map.getZoom());
});

L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
  maxZoom: 20, subdomains: 'abcd',
}).addTo(map);

const completedMarkers = DATA.completed.map((p) =>
  L.circleMarker([p.lat, p.lon], { radius: 2.6, weight: 0, fillColor: '#9aa1ac', fillOpacity: 0.55 }).addTo(map)
);

let activeLines = [];
let activePoint = null;
const panel = document.getElementById('panel');

function clearSelection() {
  activeLines.forEach((l) => map.removeLayer(l));
  activeLines = [];
  completedMarkers.forEach((m) => m.setStyle({ radius: 2.6, fillColor: '#9aa1ac', fillOpacity: 0.55 }));
  if (activePoint) activePoint.marker.setStyle({ fillColor: '#3b6fd6', radius: 4 });
  activePoint = null;
  panel.classList.remove('visible');
}

// Weak -> strong colour spectrum for connection strength — grey reads as
// "barely contributing", red as "dominates this property's estimate", so
// which nearby jobs matter most is visible at a glance, not just from line
// thickness.
const WEAK_RGB = [199, 204, 211];   // #c7ccd3
const STRONG_RGB = [214, 69, 69];   // #d64545
function strengthColor(t) {
  const r = Math.round(WEAK_RGB[0] + (STRONG_RGB[0] - WEAK_RGB[0]) * t);
  const g = Math.round(WEAK_RGB[1] + (STRONG_RGB[1] - WEAK_RGB[1]) * t);
  const b = Math.round(WEAK_RGB[2] + (STRONG_RGB[2] - WEAK_RGB[2]) * t);
  return `rgb(${r},${g},${b})`;
}

function selectPoint(point, marker) {
  clearSelection();
  activePoint = { point, marker };
  marker.setStyle({ fillColor: '#1d4ed8', radius: 5.5 });

  const weights = point.neighbors.map((n) => n.w);
  const maxW = Math.max(...weights);
  const minW = Math.min(...weights);
  const range = maxW - minW || 1;

  point.neighbors.forEach(({ j, w }) => {
    const cp = DATA.completed[j];
    const t = (w - minW) / range; // this property's OWN weakest-to-strongest range, so the
                                   // spectrum is always meaningfully spread across what's shown
    const color = strengthColor(t);
    const line = L.polyline([[point.lat, point.lon], [cp.lat, cp.lon]], {
      color,
      weight: Math.max(0.8, w * 10),
      opacity: Math.min(0.9, 0.25 + w * 1.5),
    }).addTo(map);
    activeLines.push(line);
    completedMarkers[j].setStyle({ radius: 4.5, fillColor: color, fillOpacity: 0.95 });
  });

  const sumW = weights.reduce((a, w) => a + w, 0);
  const z = sumW / (sumW + BUHLMANN_K);

  document.getElementById('panelTitle').textContent = point.suburb + ' property';
  document.getElementById('panelCount').textContent = point.neighbors.length;
  document.getElementById('panelMaxWeight').textContent = maxW.toFixed(3);
  document.getElementById('panelMinWeight').textContent = minW.toFixed(3);
  document.getElementById('panelZ').textContent = z.toFixed(4);
  panel.classList.add('visible');
}

DATA.incomplete.forEach((point) => {
  const marker = L.circleMarker([point.lat, point.lon], {
    radius: 4, weight: 1, color: 'rgba(255,255,255,0.7)', fillColor: '#3b6fd6', fillOpacity: 0.9,
  }).addTo(map);
  marker.on('click', (e) => {
    L.DomEvent.stopPropagation(e);
    if (activePoint && activePoint.point.id === point.id) { clearSelection(); return; }
    selectPoint(point, marker);
  });
});

map.on('click', clearSelection);
</script>
</body>
</html>
"""


def write_html(dataset: dict) -> None:
    html = HTML_TEMPLATE.replace("__DATA_JSON__", json.dumps(dataset))
    OUTPUT_HTML.write_text(html)


def main() -> None:
    print("Computing distance x similarity x recency weights (credibility_model.py's own constants) ...")
    dataset = build_dataset()
    print(f"  snapshot day (median job_end_day): {dataset['nowDay']}")
    print(f"  completed: {dataset['completedCount']:,} / incomplete: {dataset['incompleteCount']:,} "
          f"(of {dataset['totalAffected']:,} flooded properties)")
    n_with_neighbors = sum(1 for p in dataset["incomplete"] if p["neighbors"])
    print(f"  {n_with_neighbors:,} incomplete properties have >=1 neighbor above the {MIN_WEIGHT} weight threshold")

    print(f"Writing {OUTPUT_HTML.name} ...")
    write_html(dataset)
    print(f"\nWrote {OUTPUT_HTML.relative_to(BASE_DIR)} ({OUTPUT_HTML.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
