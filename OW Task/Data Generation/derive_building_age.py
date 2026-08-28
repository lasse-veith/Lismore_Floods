"""Derive a synthetic building_age_years per property, calibrated against a
real NSW-wide monthly dwelling-approvals series plus spatial covariates.

Pipeline:
  1. Inspect "NSW DWELLING APPROVAL.csv" (real, state-level, monthly).
  2. Aggregate into yearly_weights, a probability distribution over 1983-2022.
  3. Compute elevation_percentile (within-suburb) and distance_percentile
     (from CBD, whole-dataset) per property.
  4. Combine into a pre_1983_score -> p_pre_1983.
  5. Sample era (pre/post 1983) per property, seeded on property_id.
  6. Sample an exact year: from the real yearly_weights (spatially tilted)
     if post-1983, or from a synthetic bracket distribution if pre-1983.
  7. building_age_years = 2022 - sampled_year. Re-validate, overwrite
     properties.csv/properties.geojson.
  8. Report suburb means/medians, correlations, and pre/post-1983 splits.

Appends documentation to SYNTHETIC_ASSUMPTIONS.md (does not touch any other
pipeline step or script).
"""

from __future__ import annotations

import csv
import json
import sys
import re
import random
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
APPROVALS_CSV = BASE_DIR.parent / "Data Sources" / "NSW DWELLING APPROVAL.csv"
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

FIRST_YEAR = 1983
LAST_YEAR = 2022  # the flood-event year; building_age_years is measured as of this year

CBD_LAT, CBD_LON = -28.8077738, 153.2793420

# Bracket boundaries for the pre-1983 era. 1856 is used as a floor (roughly
# Lismore's township founding era) since the real approvals series only
# starts in 1983 and gives no data to anchor an earlier floor — see
# SYNTHETIC_ASSUMPTIONS.md.
PRE_1946_START = 1856
BRACKETS = {
    "pre_1946": (PRE_1946_START, 1945),
    "y1946_1970": (1946, 1969),
    "y1970_1983": (1970, 1982),
}
BASE_BRACKET_WEIGHTS = {"pre_1946": 0.35, "y1946_1970": 0.40, "y1970_1983": 0.25}

OLDER_SUBURBS = {"GOONELLABAH", "LISMORE HEIGHTS"}
NEWER_RURAL_SUBURBS = {"CHILCOTTS GRASS", "LOFTVILLE", "HOWARDS GRASS", "MONALTRIE", "LINDENDALE", "TREGEAGLE"}


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


# --------------------------------------------------------------------------
# STEP 1: inspect the NSW dwelling approvals file
# --------------------------------------------------------------------------

def inspect_approvals_file() -> list[tuple[int, int, int]]:
    """Prints the STEP 1 inspection, and returns [(year, month, count), ...]."""
    print("=== STEP 1: inspecting NSW DWELLING APPROVAL.csv ===")
    with APPROVALS_CSV.open(encoding="utf-8-sig") as f:
        raw_lines = [line.rstrip("\n") for line in f]

    print("head -5:")
    for line in raw_lines[:5]:
        print(" ", line)

    rows = list(csv.reader(raw_lines))
    print(f"\nTotal rows: {len(rows)}")
    print(f"Columns per row: {sorted(set(len(r) for r in rows))} (no header row present)")
    print("Column 0: date, format 'Mon-YYYY' (e.g. 'Jul-1983')")
    print("Column 1: a SINGLE total dwelling-approval count — this file is NOT split by")
    print("          dwelling type (no separate houses vs other-residential columns)")

    parsed = []
    for date_str, count_str in rows:
        d = datetime.strptime(date_str, "%b-%Y")
        parsed.append((d.year, d.month, int(count_str)))

    months_seen = sorted({(y, m) for y, m, _ in parsed})
    year_month_counts = defaultdict(int)
    for y, _m in months_seen:
        year_month_counts[y] += 1
    all_12 = all(c == 12 for y, c in year_month_counts.items() if FIRST_YEAR < y < max(year_month_counts))
    print(f"\nCadence: monthly, one row per calendar month "
          f"({'confirmed 12 rows/year for all interior years' if all_12 else 'gaps present — see below'})")
    print(f"Date range in file: {parsed[0][0]}-{parsed[0][1]:02d} to {parsed[-1][0]}-{parsed[-1][1]:02d}")
    print(f"  {FIRST_YEAR} has only {year_month_counts[FIRST_YEAR]} months of data (partial year — file starts mid-{FIRST_YEAR})")
    print(f"  {LAST_YEAR} has {year_month_counts[LAST_YEAR]} months of data")

    return parsed


# --------------------------------------------------------------------------
# STEP 2: yearly_weights, a probability distribution over 1983-2022
# --------------------------------------------------------------------------

def build_yearly_weights(monthly: list[tuple[int, int, int]]) -> dict[int, float]:
    print("\n=== STEP 2: yearly baseline (1983-2022) ===")
    yearly_totals: dict[int, int] = defaultdict(int)
    for year, _month, count in monthly:
        if FIRST_YEAR <= year <= LAST_YEAR:
            yearly_totals[year] += count

    total = sum(yearly_totals.values())
    yearly_weights = {year: count / total for year, count in yearly_totals.items()}

    ranked = sorted(yearly_weights.items(), key=lambda kv: -kv[1])
    print("5 highest-weighted years:")
    for year, w in ranked[:5]:
        flag = "  (partial year, only 6 months of data)" if year == FIRST_YEAR else ""
        print(f"  {year}: {w:.4f}{flag}")
    print("5 lowest-weighted years:")
    for year, w in ranked[-5:]:
        flag = "  (partial year, only 6 months of data)" if year == FIRST_YEAR else ""
        print(f"  {year}: {w:.4f}{flag}")

    return yearly_weights


# --------------------------------------------------------------------------
# STEP 3: spatial covariates
# --------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    r = 6371.0088  # mean Earth radius, km
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def rank_normalize(values: np.ndarray) -> np.ndarray:
    """0-1 scale where the lowest value maps to exactly 0 and the highest to
    exactly 1 (fractional rank, ties averaged). A single-element input maps
    to 0.5 (no basis to rank it)."""
    n = len(values)
    if n <= 1:
        return np.full(n, 0.5)
    order = values.argsort(kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(n, dtype=float)
    # average rank for ties
    sorted_vals = values[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            avg = ranks[order[i:j + 1]].mean()
            ranks[order[i:j + 1]] = avg
        i = j + 1
    return ranks / (n - 1)


def compute_spatial_covariates(rows: list[dict]) -> None:
    """Adds elevation_percentile and distance_percentile (+ distance_km_from_cbd)
    to each row dict in place."""
    print("\n=== STEP 3: spatial covariates ===")

    by_suburb: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_suburb[row["suburb"]].append(i)

    for idxs in by_suburb.values():
        elevations = np.array([rows[i]["ground_elevation_m_ahd"] for i in idxs], dtype=float)
        percentiles = rank_normalize(elevations)
        for i, p in zip(idxs, percentiles):
            rows[i]["elevation_percentile"] = float(p)

    lats = np.array([row["latitude"] for row in rows])
    lons = np.array([row["longitude"] for row in rows])
    distances = haversine_km(lats, lons, CBD_LAT, CBD_LON)
    distance_percentiles = rank_normalize(distances)
    for row, dist, pct in zip(rows, distances, distance_percentiles):
        row["distance_km_from_cbd"] = float(dist)
        row["distance_percentile"] = float(pct)

    print(f"  elevation_percentile computed within each of {len(by_suburb)} suburbs")
    print(f"  distance_km_from_cbd (from {CBD_LAT},{CBD_LON}): "
          f"min {distances.min():.2f} km, max {distances.max():.2f} km, "
          f"then rank-normalized to distance_percentile")


# --------------------------------------------------------------------------
# STEP 4-5: pre_1983_score -> p_pre_1983 -> sampled era
# --------------------------------------------------------------------------

def compute_p_pre_1983(row: dict) -> float:
    score = 0.5
    score += 0.30 * (1 - row["elevation_percentile"])
    score += 0.30 * (1 - row["distance_percentile"])
    if row["suburb"] in OLDER_SUBURBS:
        score -= 0.35
    if row["suburb"] in NEWER_RURAL_SUBURBS:
        score -= 0.10
    if row.get("dwelling_structure_census") == "flat_or_apartment":
        score -= 0.50
    return min(0.85, max(0.02, score))


# --------------------------------------------------------------------------
# STEP 6a: post-1983 exact year, spatially tilted
# --------------------------------------------------------------------------

def sample_post_1983_year(yearly_weights: dict[int, float], distance_percentile: float, rng: random.Random) -> int:
    tilted = {}
    for year, w in yearly_weights.items():
        if year <= 2000:
            tilted[year] = w * (1 + 0.3 * (1 - distance_percentile))
        else:
            tilted[year] = w * (1 + 0.3 * distance_percentile)
    total = sum(tilted.values())
    years = list(tilted.keys())
    weights = [tilted[y] / total for y in years]
    return rng.choices(years, weights=weights, k=1)[0]


# --------------------------------------------------------------------------
# STEP 6b: pre-1983 bracket, then uniform exact year within it
# --------------------------------------------------------------------------

def sample_pre_1983_year(elevation_percentile: float, distance_percentile: float, rng: random.Random) -> int:
    tilt = 0.3 * (1 - elevation_percentile) + 0.3 * (1 - distance_percentile)  # in [0, 0.6]
    weights = {
        "pre_1946": BASE_BRACKET_WEIGHTS["pre_1946"] * (1 + tilt),
        "y1946_1970": BASE_BRACKET_WEIGHTS["y1946_1970"],
        "y1970_1983": BASE_BRACKET_WEIGHTS["y1970_1983"] * (1 - tilt),
    }
    total = sum(weights.values())
    brackets = list(weights.keys())
    probs = [weights[b] / total for b in brackets]
    bracket = rng.choices(brackets, weights=probs, k=1)[0]
    start, end = BRACKETS[bracket]
    return rng.randint(start, end)


# --------------------------------------------------------------------------
# STEP 5-7: sample era + year per property, validate, write
# --------------------------------------------------------------------------

def derive_ages(rows: list[dict], yearly_weights: dict[int, float]) -> tuple[list[PropertyRecord], dict]:
    print("\n=== STEP 5-7: sampling era + exact year per property ===")
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "pre_1983_count": 0,
        "post_1983_count": 0,
        "per_suburb": defaultdict(lambda: {"pre": 0, "post": 0, "ages": []}),
        "ages": [],
        "elevations": [],
        "distances": [],
    }

    for row in rows:
        rng = row_rng(row["property_id"])
        p_pre_1983 = compute_p_pre_1983(row)
        is_pre_1983 = rng.random() < p_pre_1983

        if is_pre_1983:
            year = sample_pre_1983_year(row["elevation_percentile"], row["distance_percentile"], rng)
            stats["pre_1983_count"] += 1
            stats["per_suburb"][row["suburb"]]["pre"] += 1
        else:
            year = sample_post_1983_year(yearly_weights, row["distance_percentile"], rng)
            stats["post_1983_count"] += 1
            stats["per_suburb"][row["suburb"]]["post"] += 1

        building_age_years = LAST_YEAR - year
        row["building_age_years"] = building_age_years
        stats["ages"].append(building_age_years)
        stats["elevations"].append(row["ground_elevation_m_ahd"])
        stats["distances"].append(row["distance_km_from_cbd"])
        stats["per_suburb"][row["suburb"]]["ages"].append(building_age_years)

        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


# --------------------------------------------------------------------------
# STEP 8: report
# --------------------------------------------------------------------------

def pearson_corr(a: list[float], b: list[float]) -> float:
    return float(np.corrcoef(np.array(a), np.array(b))[0, 1])


def print_summary(stats: dict) -> None:
    print("\n=== STEP 8: Summary ===")
    total = stats["pre_1983_count"] + stats["post_1983_count"]
    print(f"Overall: pre-1983 {stats['pre_1983_count']:,} ({stats['pre_1983_count']/total:.1%})  "
          f"post-1983 {stats['post_1983_count']:,} ({stats['post_1983_count']/total:.1%})")

    print("\nbuilding_age_years mean/median per suburb, and pre/post-1983 split:")
    for suburb, d in sorted(stats["per_suburb"].items(), key=lambda kv: -len(kv[1]["ages"])):
        ages = d["ages"]
        n = len(ages)
        mean_age = sum(ages) / n
        median_age = sorted(ages)[n // 2] if n % 2 else (sorted(ages)[n // 2 - 1] + sorted(ages)[n // 2]) / 2
        pre_pct = d["pre"] / n
        print(f"  {suburb:20s} n={n:>6,}  mean={mean_age:6.1f}y  median={median_age:6.1f}y  pre-1983={pre_pct:.1%}")

    corr_elevation = pearson_corr(stats["ages"], stats["elevations"])
    corr_distance = pearson_corr(stats["ages"], stats["distances"])
    print(f"\nCorrelation(building_age_years, ground_elevation_m_ahd): {corr_elevation:+.3f}  (expected negative)")
    print(f"Correlation(building_age_years, distance_km_from_cbd):   {corr_distance:+.3f}  (expected negative)")


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------

def append_assumptions_doc(stats: dict) -> None:
    total = stats["pre_1983_count"] + stats["post_1983_count"]

    lines = [
        "# Building Age Derivation (derive_building_age.py)",
        "",
        "Appended by derive_building_age.py — this section is regenerated (replaced) on every run.",
        "",
        "## Data source",
        "",
        "`NSW DWELLING APPROVAL.csv` is a **real** ABS-style monthly dwelling-approvals",
        "series, but it is **state-level (all of NSW), not Lismore-specific** — there is",
        "no LGA- or suburb-level approvals series available. It has no header row: column",
        "0 is `Mon-YYYY`, column 1 is a single total approval count (not split by dwelling",
        f"type). It covers {FIRST_YEAR}-07 through beyond {LAST_YEAR}, but "
        f"{FIRST_YEAR} itself only has 6 months of data (Jul-Dec) — its yearly weight is",
        "therefore somewhat suppressed relative to a full year and should be read with that",
        "caveat.",
        "",
        "It is used only to shape the **relative** likelihood of a post-1983 building's",
        "exact construction year (NSW-wide building cycles: recessions, booms). It says",
        "nothing about Lismore specifically or about pre-1983 construction, since the",
        "series doesn't go back that far.",
        "",
        "## Spatial covariates",
        "",
        "- `elevation_percentile`: each property's `ground_elevation_m_ahd` rank-normalized",
        "  (0 = lowest, 1 = highest) **within its own suburb only** — so it measures relative",
        "  position in the local terrain, not raw elevation across suburbs.",
        f"- `distance_km_from_cbd`: haversine distance to the fixed point ({CBD_LAT}, {CBD_LON}),",
        "  rank-normalized to `distance_percentile` (0 = closest, 1 = farthest) across the",
        "  whole dataset.",
        "",
        "## Scoring weights and their justification",
        "",
        "`p_pre_1983` starts at a neutral 0.5 and is shifted by:",
        "",
        "- `+0.30 * (1 - elevation_percentile)`: lower-lying land within a suburb was",
        "  typically settled/built on earlier (flatter, more accessible, closer to the",
        "  original river-flat township); higher land was developed later as the town",
        "  expanded uphill.",
        "- `+0.30 * (1 - distance_percentile)`: properties closer to the historical CBD",
        "  are more likely to be older, since Australian regional towns grew outward from",
        "  their centre over time.",
        "- `-0.35` if suburb is Goonellabah or Lismore Heights: these are well-documented",
        "  post-WWII/1960s-onward suburban expansion areas of Lismore, so the base",
        "  likelihood of a pre-1983 building is reduced substantially.",
        "- `-0.10` if suburb is one of the small outlying/rural localities (Chilcotts",
        "  Grass, Loftville, Howards Grass, Monaltrie, Lindendale, Tregeagle): a smaller,",
        "  softer penalty reflecting that rural-residential subdivision in these areas",
        "  tends to be more recent than the historical town core, but with much more",
        "  variance than the dedicated post-war suburbs above.",
        "- `-0.50` if `dwelling_structure_census == \"flat_or_apartment\"`: multi-unit/flat",
        "  development in Lismore is predominantly a later (post-1970s) building form,",
        "  so flats are pushed strongly toward the post-1983 era.",
        "",
        "The result is clipped to `[0.02, 0.85]` so every property retains a non-zero",
        "chance of either era (the scoring is a soft prior, not a hard rule).",
        "",
        "## Year sampling",
        "",
        "**Post-1983** (probability `1 - p_pre_1983`): the exact year is drawn from the",
        "real `yearly_weights` (STEP 2), with a mild spatial tilt applied first — years",
        "1983-2000 are up-weighted by `(1 + 0.3*(1-distance_percentile))` (closer to the",
        "centre skews slightly earlier within this window) and years 2001-2022 by",
        "`(1 + 0.3*distance_percentile)` (farther out skews slightly later, matching",
        "outward suburban growth), then renormalized. (Year 2000 itself is assigned to the",
        "earlier window only, to avoid double-weighting the boundary year.)",
        "",
        "**Pre-1983** (probability `p_pre_1983`): a bracket is drawn from",
        f"`{BASE_BRACKET_WEIGHTS}`, tilted by",
        "`tilt = 0.3*(1-elevation_percentile) + 0.3*(1-distance_percentile)` (range [0, 0.6]):",
        "`pre_1946` weight is multiplied by `(1 + tilt)` and `y1970_1983` weight by",
        "`(1 - tilt)`, `y1946_1970` left as the stable middle anchor, then renormalized.",
        "An exact year is then drawn **uniformly** within the chosen bracket.",
        "",
        f"**This exact-year sampling within pre-1983 brackets is fully structural/synthetic**",
        "— no real approvals (or any other real temporal) data exists for Lismore, or NSW,",
        f"before {FIRST_YEAR}. The bracket boundaries themselves "
        f"({BRACKETS['pre_1946'][0]}-{BRACKETS['pre_1946'][1]}, "
        f"{BRACKETS['y1946_1970'][0]}-{BRACKETS['y1946_1970'][1]}, "
        f"{BRACKETS['y1970_1983'][0]}-{BRACKETS['y1970_1983'][1]}) follow standard",
        "Australian housing-stock eras (pre-WWII, post-war boom, 1970s), and the",
        f"`{PRE_1946_START}` floor is an assumed approximate founding era for the Lismore",
        "township, not a sourced historical fact.",
        "",
        "## Result (this run)",
        "",
        f"- Pre-1983: {stats['pre_1983_count']:,} ({stats['pre_1983_count']/total:.1%})",
        f"- Post-1983: {stats['post_1983_count']:,} ({stats['post_1983_count']/total:.1%})",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Building Age Derivation (derive_building_age.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    monthly = inspect_approvals_file()
    yearly_weights = build_yearly_weights(monthly)

    print(f"\nLoading {GEOJSON_OUT.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())
    rows = []
    for feature in geojson_data["features"]:
        row = dict(feature["properties"])
        row["longitude"] = feature["geometry"]["coordinates"][0]
        row["latitude"] = feature["geometry"]["coordinates"][1]
        rows.append(row)
    print(f"  {len(rows):,} properties loaded")

    compute_spatial_covariates(rows)

    records, stats = derive_ages(rows, yearly_weights)
    print(f"\n{len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended building-age documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
