# Lismore 2022 Flood Cost Model — Project Steps

## Project goal
Model flood impact and repair costs for a synthetic property portfolio placed on
**real Lismore addresses and elevation**, for the 28 February 2022 flood event.
Compare a static up-front cost estimate against a geospatial credibility model
that updates as simulated "actual" repairs come in over time.

Ground truth vs synthetic is tracked explicitly throughout — see `PropertyRecord`
field types in `models.py` (or wherever the two Pydantic files live) for exactly
which fields are real data vs fabricated.

---

## Data inventory

| Data | Source | Status |
|---|---|---|
| Property addresses (lat/lon) | Geoscape G-NAF (filtered to Lismore/South Lismore/East Lismore/Goonellabah/Girards Hill/Loftville via `NSW_LOCALITY_psv.psv`) | Real |
| Ground elevation | NSW Spatial Services 1m DEM, LiDAR, AHD datum, Zone 56 (35 tiles covering the project polygon) | Real |
| Building footprint/area | OpenStreetMap (Overpass export) — **known to be sparse/incomplete for Lismore** | Real, partial coverage |
| Flood hydrograph (water level over time) | Engeny/DCCEEW "Lismore 2022 Post Flood Event Analysis" report (uploaded PDF) — real dated/timed rise, levee overtopping, peak, recession | Real |
| Floor height methodology | Same report, Section 8.1: slab-on-ground +0.15m, short stumps +0.5m, high stumps +1.5m above ground level | Real methodology |
| Suburb hazard zoning | Same report: South Lismore = H2, CBD/North Lismore = H3/H4 | Real |
| Aggregate damage benchmark | Same report, Table 8.1: 2,067 properties flooded above floor, $587M residential damages ($309M structural / $242M internal / $36.1M external) | Real (used to sanity-check/calibrate synthetic unit costs, not to reverse-engineer exact figures) |
| Construction type, switchboard, circuits, kitchen spec, building age (where not from OSM) | — | **Synthetic**, plausibly calibrated |
| Unit costs (per m², per point, per item) | — | **Synthetic**, plausibly calibrated to AU trade rates |
| Actual repair timing/cost | — | **Synthetic** — this is the simulated "ground truth" the credibility model is tested against |

---

## Conventions (apply in every script)

- **CRS**: property lat/lon in GDA2020 decimal degrees (treat as WGS84-equivalent).
  DEM mosaic in its native projected CRS (MGA Zone 56 — confirm EPSG:7856 vs
  EPSG:28356 when the mosaic is built); reproject points into DEM CRS only at
  the moment of sampling, never store two coordinate systems on one record.
- **Column names**: always `latitude` / `longitude`, never `lat`/`lon`/`x`/`y`.
- **Property ID**: generate one canonical `property_id` the first time the
  address list is finalized. Every downstream file (elevation join, flood
  exposure, cost estimate, repair simulation, credibility output) joins back
  to this ID. Never regenerate or re-derive IDs later in the pipeline.
- **Time**: all time-series data indexed by `interval_hour` (integer, hours
  since flood event start = hour 0 = first minor-flood-level exceedance,
  Thu 24 Feb 2022 morning per the report). Store a `timestamp` alongside for
  display purposes, but join/filter on `interval_hour`.
- **Elevation/water levels**: always metres AHD. Never mix AHD with ellipsoidal
  height — if a new elevation source is added later, confirm its datum before
  using it.
- **Real vs synthetic**: keep synthetic attribute generation in a separate
  script/step from real-data cleaning and joining, so it's always obvious
  which columns came from a real source and which were fabricated.

---

## Pipeline steps

### Step 1 — Clean and mosaic the DEM
- Input: 35 GeoTIFF tiles in `/data/dem/`
- Merge into a single `lismore_dem_mosaic.tif` (rasterio.merge)
- Confirm/report: CRS, resolution, bounding box, nodata gaps
- Output: `lismore_dem_mosaic.tif`

### Step 2 — Clean the address data
- Input: filtered G-NAF extract (address detail + geocode + site, joined and
  locality-filtered as already done)
- Dedupe records (G-NAF can have multiple rows per property for sub-addressing)
- Drop rows with null/malformed coordinates
- Confirm every point falls inside the project polygon (`coords.txt`) —
  belt-and-braces check even after locality filtering
- Assign canonical `property_id`
- Output: `addresses_clean.csv`

### Step 3 — Join elevation onto addresses
- For each row in `addresses_clean.csv`, reproject lat/lon into the mosaic's
  CRS, sample `lismore_dem_mosaic.tif`, add `ground_elevation_m_ahd`
- Report how many points sampled successfully vs. fell outside DEM coverage
- Validate against plausible range for Lismore floodplain (see
  `PropertyRecord.sanity_check_elevation` in the Pydantic model)
- Output: `properties_with_elevation.csv`


### Step 4 — Generate synthetic attributes
- For every property, assign: `construction_type` (drives
  `floor_height_offset_m` per the report's real methodology),
  `building_area_m2` (only where Step 4 left it null),
  `switchboard_type`, `circuit_count`, `kitchen_spec`, `building_age_years`
- Draw from plausible distributions for Lismore housing stock — document the
  assumed distributions in a comment block or a `SYNTHETIC_ASSUMPTIONS.md`
- Validate every record against the `PropertyRecord` Pydantic model before
  writing
- Output: `properties_final.csv` **and** `properties.geojson`
  (GeoJSON is the canonical form used for all future mapping/visualization —
  see Pydantic-to-GeoJSON helper already written)

### Step 5 — Build the flood hydrograph model
- Encode the real dated/timed water-level series from the report
  (minor level Thu 24 Feb → moderate Fri 25 Feb → recede → moderate again
  Sun 27 Feb evening → major + levee overtopped 3am Mon 28 Feb → peak 14.36m
  AHD 3pm Mon 28 Feb → falls below major Wed 2 Mar morning) as a time series
  in `interval_hour` steps of 12
- Layer a spatial decay/hazard-zone model on top (South Lismore lower hazard,
  CBD/North Lismore higher, per report) to turn a single river-gauge level
  into a per-property `water_level_m_ahd` at each interval
- Output: `flood_hydrograph.csv` (interval_hour → gauge water level) and the
  spatial decay parameters used

### Step 6 — Compute flood exposure per property per interval
- For each `property_id` × `interval_hour`: compute `floor_elevation_m_ahd`
  (ground + floor height offset), compare to that interval's local water
  level, derive `is_flooded` and `depth_above_floor_m`
- Validate each row against the `FloodExposureRecord` Pydantic model
- Output: `flood_exposure.csv` **and** `flood_exposure.geojson` per interval
  (or one GeoJSON with `interval_hour` as a filterable property — decide based
  on what the mapping library consumes more easily)

### Step 7 — Visualize flood progression (first visualization checkpoint)
- Build a time-slider map (Leaflet or equivalent) stepping through
  `interval_hour`, coloring/filtering properties by `is_flooded` /
  `depth_above_floor_m`
- This is a checkpoint — confirm the flood progression looks sane against the
  report's real timeline before building any cost logic on top of it

### Step 8 onward — Cost engine, repair simulation, credibility model
(Detailed steps to follow once Steps 1–8 are validated. Not started yet.)
- Static cost engine: unit costs × property characteristics × depth exceedance
- Repair simulation: synthetic rollout of actual repair timing/cost with
  realistic noise
- Credibility model: distance + similarity + recency-weighted updating of
  cost estimates as simulated repairs complete
- Final comparison: static vs credibility-updated vs actual, visualized

---

## Files referenced
- `coords.txt` — project area polygon (lat/lon vertices)
- `models.py` (or equivalent) — `PropertyRecord` and `FloodExposureRecord`
  Pydantic models, already written
- Uploaded report PDF — source of all real hydrograph/methodology/benchmark
  figures cited above