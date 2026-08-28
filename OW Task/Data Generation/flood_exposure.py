"""Compute per-property flood exposure across the flood_hydrograph.csv
time series, using a simplified "bathtub fill" model.

local_water_level_m_ahd(t) = gauge_water_level_m_ahd(t) - hazard_damping(property)

This is a simplified physical model, NOT a real hydraulic simulation (the
report's actual modelling used TUFLOW or similar) — it has no channel
routing, no floodwave travel time, and no drainage/infrastructure detail.
hazard_damping reflects hydraulic connectivity (levee protection), not
distance from the river:

  - South Lismore (H2, lower hazard per steps.md): damping = 0.0 always —
    well-connected floodplain, follows the gauge directly.
  - CBD / North Lismore (H3/H4, higher hazard, levee-affected): levee-
    protected until the gauge crosses LEVEE_OVERTOP_LEVEL_M_AHD (imported
    from flood_hydrograph.py — the estimated major-level threshold, since
    the levee overtopped at ~major level per steps.md's timeline). Before
    that crossing, local_water_level is pinned to a low, effectively-dry
    baseline regardless of the gauge level. Once crossed, it's a ONE-WAY
    RATCHET: the property follows the gauge directly for every subsequent
    interval, even if the gauge later recedes back below the trigger — a
    levee breach doesn't self-heal on a receding river, the inundated area
    drains at its own pace, which this model doesn't attempt to simulate.
  - Every other suburb: damping = 0.0 (the spec's own "simplest defensible
    default given no finer real data exists").

"CBD" (from steps.md's "South Lismore = H2, CBD/North Lismore = H3/H4") is
mapped to this project's "LISMORE" suburb — that's the G-NAF locality
covering the town centre — since no suburb is literally named "CBD" in the
dataset. Flagged as an inferred mapping, not an explicit one.

Outputs:
  - flood_exposure.csv: property_id, interval_hour, local_water_level_m_ahd,
    depth_above_floor_m, is_flooded — one row per property per interval_hour.
  - peak_water_level_m_ahd / peak_depth_above_floor_m added to
    properties.csv/properties.geojson (max across all intervals per
    property), re-validated against PropertyRecord.

Requires ground_elevation_m_ahd and floor_height_offset_m to already be
populated (run after derive_affluence_and_construction_type.py).
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
ROOT_DIR = BASE_DIR.parent                             # OW Task

sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py
sys.path.insert(0, str(BASE_DIR / "Data Filtering"))    # flood_hydrograph.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from flood_hydrograph import LEVEE_OVERTOP_LEVEL_M_AHD, OUTPUT_CSV as HYDROGRAPH_CSV  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402

EXPOSURE_CSV = ROOT_DIR / "Output" / "flood_exposure.csv"
ASSUMPTIONS_MD = ROOT_DIR / "SYNTHETIC_ASSUMPTIONS.md"

REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK = 2067  # real, from steps.md / the report

# "CBD" -> LISMORE (inferred, see module docstring)
LEVEE_PROTECTED_SUBURBS = {"LISMORE", "NORTH LISMORE"}

# Low, effectively-dry baseline for levee-protected suburbs before the
# breach — matches flood_hydrograph.py's own pre-flood taper baseline, so
# there's a single consistent "normal river level" assumption project-wide.
DRY_BASELINE_M_AHD = 2.0


def load_hydrograph() -> list[tuple[int, float]]:
    with HYDROGRAPH_CSV.open(newline="") as f:
        return [(int(row["interval_hour"]), float(row["gauge_water_level_m_ahd"])) for row in csv.DictReader(f)]


def load_properties() -> list[dict]:
    geojson_data = json.loads(GEOJSON_OUT.read_text())
    rows = []
    for feature in geojson_data["features"]:
        row = dict(feature["properties"])
        row["longitude"] = feature["geometry"]["coordinates"][0]
        row["latitude"] = feature["geometry"]["coordinates"][1]
        rows.append(row)
    return rows


def compute_exposure_for_property(row: dict, hydrograph: list[tuple[int, float]]) -> list[dict]:
    floor_elevation = row["ground_elevation_m_ahd"] + row["floor_height_offset_m"]
    is_levee_protected = row["suburb"] in LEVEE_PROTECTED_SUBURBS
    levee_breached = False

    exposure_rows = []
    for interval_hour, gauge_level in hydrograph:
        if is_levee_protected:
            if not levee_breached and gauge_level >= LEVEE_OVERTOP_LEVEL_M_AHD:
                levee_breached = True
            local_water_level = gauge_level if levee_breached else DRY_BASELINE_M_AHD
        else:
            local_water_level = gauge_level

        depth_above_floor = max(0.0, local_water_level - floor_elevation)
        exposure_rows.append(
            {
                "property_id": row["property_id"],
                "interval_hour": interval_hour,
                "local_water_level_m_ahd": round(local_water_level, 3),
                "depth_above_floor_m": round(depth_above_floor, 3),
                "is_flooded": depth_above_floor > 0,
            }
        )
    return exposure_rows


def write_exposure_csv(all_rows: list[dict]) -> None:
    with EXPOSURE_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["property_id", "interval_hour", "local_water_level_m_ahd", "depth_above_floor_m", "is_flooded"])
        writer.writeheader()
        writer.writerows(all_rows)


def summarize_and_validate(rows: list[dict], exposure_by_property: dict[str, list[dict]]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": len(rows),
        "ever_flooded_above_floor": 0,
        "levee_protected_pre_breach_flooded": 0,
        "levee_protected_post_breach_flooded": 0,
        "levee_protected_total": 0,
    }

    for row in rows:
        exposures = exposure_by_property[row["property_id"]]
        peak_water_level = max(e["local_water_level_m_ahd"] for e in exposures)
        peak_depth = max(e["depth_above_floor_m"] for e in exposures)
        row["peak_water_level_m_ahd"] = peak_water_level
        row["peak_depth_above_floor_m"] = peak_depth

        if peak_depth > 0:
            stats["ever_flooded_above_floor"] += 1

        if row["suburb"] in LEVEE_PROTECTED_SUBURBS:
            stats["levee_protected_total"] += 1
            breached = False
            for e in exposures:
                if not breached and e["local_water_level_m_ahd"] > DRY_BASELINE_M_AHD + 1e-6:
                    breached = True
                if not breached and e["is_flooded"]:
                    stats["levee_protected_pre_breach_flooded"] += 1
                if breached and e["is_flooded"]:
                    stats["levee_protected_post_breach_flooded"] += 1

        record_fields = {k: v for k, v in row.items() if k in PropertyRecord.model_fields}
        try:
            records.append(PropertyRecord(**record_fields))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={row.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


def append_assumptions_doc(hydrograph: list[tuple[int, float]], stats: dict) -> None:
    grid_peak_hour, grid_peak_level = max(hydrograph, key=lambda r: r[1])
    gap = REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK - stats["ever_flooded_above_floor"]

    lines = [
        "# Flood Exposure (flood_exposure.py)",
        "",
        "Appended by flood_exposure.py — this section is regenerated (replaced) on every run.",
        "",
        "## Bathtub-fill model — simplified, not a real hydraulic simulation",
        "",
        "`local_water_level_m_ahd(t) = gauge_water_level_m_ahd(t) - hazard_damping(property)`.",
        "This has no channel routing, no floodwave travel time, and no drainage/infrastructure",
        "detail — it is NOT equivalent to the report's real hydraulic model (e.g. TUFLOW).",
        "",
        "## Levee-damping logic",
        "",
        "South Lismore and every suburb other than LISMORE/NORTH LISMORE always follow the",
        "gauge directly (damping = 0.0 — South Lismore per its real H2/well-connected",
        "designation, everything else as the spec's simplest defensible default).",
        "",
        "\"CBD\" (steps.md: \"South Lismore = H2, CBD/North Lismore = H3/H4\") is mapped to this",
        "project's `LISMORE` suburb, since no suburb is literally named \"CBD\" — an INFERRED",
        "mapping, not an explicit one.",
        "",
        "LISMORE and NORTH LISMORE are levee-protected: pinned to a low, effectively-dry",
        f"baseline ({DRY_BASELINE_M_AHD}m AHD — matching flood_hydrograph.py's own pre-flood",
        f"taper baseline) until the gauge crosses `LEVEE_OVERTOP_LEVEL_M_AHD`",
        f"({LEVEE_OVERTOP_LEVEL_M_AHD}m AHD, imported from flood_hydrograph.py — the estimated",
        "major-level threshold, since the levee overtopped at ~major level per the report's",
        "timeline). Crossing is a ONE-WAY RATCHET — once breached, the property follows the",
        "gauge directly for every subsequent interval even if the gauge recedes back below the",
        "trigger, since a levee breach doesn't self-heal on a receding river.",
        "",
        "## Sanity checks (this run)",
        "",
        f"- Properties ever flooded above floor: {stats['ever_flooded_above_floor']:,} / "
        f"{stats['total']:,}, vs the report's real benchmark of "
        f"{REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK:,} properties "
        f"({'above' if gap < 0 else 'below'} the benchmark by {abs(gap):,} — different property",
        "  count/model, not expected to match exactly).",
        f"- Levee-protected properties ({stats['levee_protected_total']:,} in LISMORE/NORTH",
        f"  LISMORE): {stats['levee_protected_pre_breach_flooded']:,} flooded-interval "
        "occurrences BEFORE the levee breach (should be 0, confirms the dry-baseline gate",
        f"  worked), {stats['levee_protected_post_breach_flooded']:,} AFTER (confirms the",
        "  post-breach jump).",
        f"- Peak gauge level on the 12h output grid: {grid_peak_level}m AHD at interval_hour",
        f"  {grid_peak_hour} (true peak per flood_hydrograph.py's anchors is 14.36m at hour 111 —",
        "  the 12h grid under-samples it; every per-property peak_water_level_m_ahd in this run",
        "  is therefore capped below the true event peak).",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Flood Exposure (flood_exposure.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(hydrograph: list[tuple[int, float]], stats: dict) -> None:
    grid_peak_hour, grid_peak_level = max(hydrograph, key=lambda r: r[1])
    gap = REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK - stats["ever_flooded_above_floor"]

    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    print(f"Properties ever flooded above floor: {stats['ever_flooded_above_floor']:,} "
          f"({stats['ever_flooded_above_floor']/stats['total']:.1%})")
    print(f"Report benchmark: {REPORT_FLOODED_ABOVE_FLOOR_BENCHMARK:,} properties flooded above floor "
          f"({'model is above' if gap < 0 else 'model is below'} benchmark by {abs(gap):,} — not expected to match exactly)")

    print(f"\nLevee-protected properties (LISMORE + NORTH LISMORE): {stats['levee_protected_total']:,}")
    print(f"  flooded-interval occurrences BEFORE breach: {stats['levee_protected_pre_breach_flooded']:,} (expect 0)")
    print(f"  flooded-interval occurrences AFTER breach:  {stats['levee_protected_post_breach_flooded']:,} (expect > 0)")

    print(f"\nPeak gauge level reached (12h grid): {grid_peak_level}m AHD at interval_hour {grid_peak_hour} "
          f"(true event peak per report: 14.36m — under-sampled by the 12h grid)")


def main() -> None:
    print(f"Loading {HYDROGRAPH_CSV.name} ...")
    hydrograph = load_hydrograph()
    print(f"  {len(hydrograph)} intervals loaded")

    print(f"Loading {GEOJSON_OUT.name} ...")
    rows = load_properties()
    print(f"  {len(rows):,} properties loaded")

    print("Computing per-property flood exposure (bathtub-fill model) ...")
    all_exposure_rows: list[dict] = []
    exposure_by_property: dict[str, list[dict]] = {}
    for row in rows:
        exposures = compute_exposure_for_property(row, hydrograph)
        exposure_by_property[row["property_id"]] = exposures
        all_exposure_rows.extend(exposures)

    print(f"Writing {EXPOSURE_CSV.name} ({len(all_exposure_rows):,} rows) ...")
    write_exposure_csv(all_exposure_rows)

    print("Summarizing peak exposure per property and validating ...")
    records, stats = summarize_and_validate(rows, exposure_by_property)
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(hydrograph, stats)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")

    print_summary(hydrograph, stats)


if __name__ == "__main__":
    main()
