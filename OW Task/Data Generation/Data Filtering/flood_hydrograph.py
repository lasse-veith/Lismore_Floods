"""Encode the Feb 2022 Lismore flood gauge hydrograph as a time series.

interval_hour = 0 at the first minor-level exceedance (Thu 24 Feb 2022
morning), stepping by 12 hours (project convention as of this revision —
originally 24 hours; changed workspace-wide to a 12-hour timescale). Anchor
points are piecewise-linearly interpolated (no report.txt/PDF exists
anywhere in this workspace to justify a fancier curve shape between sparse
anchors, so a piecewise-linear fit is the more defensible choice).

Sourcing:
  - The event TIMELINE (which hour each stage happened) and the PEAK value
    (14.36m AHD) are real, taken from steps.md, which cites them from the
    project's uploaded flood report — that PDF itself isn't present in this
    workspace, so it can't be grepped directly.
  - The minor/moderate/major THRESHOLD AHD VALUES are not stated anywhere in
    this workspace. They are filled in from the commonly-reported BOM river
    height classification levels for the Lismore (Wilsons River) gauge
    (minor 4.2m, moderate 7.2m, major 9.7m) — general public knowledge, NOT
    sourced from any file here. Flagged as ESTIMATED throughout.
  - Two extra anchor points (a recession trough around hour 66, and a
    baseline taper point at hour 240) were added beyond what the timeline
    describes, purely so the piecewise-linear curve can actually show the
    "exceeded, then recedes, then re-exceeded" shape the timeline
    describes — a straight line between two same-height peaks would
    otherwise stay flat. Flagged as ESTIMATED / not from any source.

Output: flood_hydrograph.csv (interval_hour, gauge_water_level_m_ahd), one
row every 12 hours from 0 to 240 inclusive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

BASE_DIR = Path(__file__).resolve().parent            # Data Generation/Data Filtering
ROOT_DIR = BASE_DIR.parent.parent                      # OW Task
OUTPUT_CSV = ROOT_DIR / "Data Sources" / "flood_hydrograph.csv"
ASSUMPTIONS_MD = ROOT_DIR / "SYNTHETIC_ASSUMPTIONS.md"

# --------------------------------------------------------------------------
# Threshold levels (ESTIMATED — see module docstring)
# --------------------------------------------------------------------------

MINOR_LEVEL_M_AHD = 4.2
MODERATE_LEVEL_M_AHD = 7.2
MAJOR_LEVEL_M_AHD = 9.7
PEAK_LEVEL_M_AHD = 14.36  # REAL — steps.md
PEAK_HOUR = 111  # REAL — steps.md, 3pm Mon 28 Feb

# --------------------------------------------------------------------------
# Anchor points: (interval_hour, gauge_water_level_m_ahd, note)
# --------------------------------------------------------------------------

ANCHORS: list[tuple[float, float, str]] = [
    (0, MINOR_LEVEL_M_AHD,
     "REAL timeline (steps.md) / ESTIMATED value — first minor-level exceedance, Thu 24 Feb AM"),
    (48, MODERATE_LEVEL_M_AHD + 0.1,
     "REAL timeline / ESTIMATED value — moderate level exceeded, local peak before first recession, Fri 25 Feb"),
    (66, (MINOR_LEVEL_M_AHD + MODERATE_LEVEL_M_AHD) / 2,
     "ESTIMATED (added, not in the given timeline) — recession trough between the two moderate exceedances, ~Sat 26 Feb"),
    (84, MODERATE_LEVEL_M_AHD + 0.1,
     "REAL timeline / ESTIMATED value — moderate level re-exceeded, Sun 27 Feb evening"),
    (101, MAJOR_LEVEL_M_AHD + 0.1,
     "REAL timeline / ESTIMATED value — major level exceeded (levee overtopped ~2h earlier, 3am), Mon 28 Feb ~5am"),
    (PEAK_HOUR, PEAK_LEVEL_M_AHD,
     "REAL — peak, 3pm Mon 28 Feb (steps.md)"),
    (150, MAJOR_LEVEL_M_AHD + 1.8,
     "ESTIMATED (added) — recession midpoint, still above major, between peak and falling-below-major"),
    (192, MAJOR_LEVEL_M_AHD,
     "REAL timeline / ESTIMATED value — falls below major level, Wed 2 Mar AM"),
    (240, 2.0,
     "ESTIMATED (added) — taper to near-baseline (assumed pre-flood normal river level)"),
]

INTERVAL_STEP = 12
LAST_HOUR = int(ANCHORS[-1][0])

# Levee-overtop trigger, used by flood_exposure.py's CBD/North Lismore
# damping logic — level-based, not hour-based (see that script).
LEVEE_OVERTOP_LEVEL_M_AHD = MAJOR_LEVEL_M_AHD


def interpolate(hour: float, anchors: Sequence[tuple[float, float, str]] = ANCHORS) -> float:
    if hour <= anchors[0][0]:
        return anchors[0][1]
    if hour >= anchors[-1][0]:
        return anchors[-1][1]
    for (h0, v0, _), (h1, v1, _) in zip(anchors, anchors[1:]):
        if h0 <= hour <= h1:
            frac = (hour - h0) / (h1 - h0)
            return v0 + frac * (v1 - v0)
    raise AssertionError("unreachable")  # anchors are sorted and cover [h0, hLast]


def build_hydrograph() -> list[tuple[int, float]]:
    hours = list(range(0, LAST_HOUR + 1, INTERVAL_STEP))
    return [(h, round(interpolate(h), 3)) for h in hours]


def write_csv(rows: list[tuple[int, float]]) -> None:
    with OUTPUT_CSV.open("w") as f:
        f.write("interval_hour,gauge_water_level_m_ahd\n")
        for hour, level in rows:
            f.write(f"{hour},{level}\n")


def grid_bracket(rows: list[tuple[int, float]], target_hour: int) -> tuple[tuple[int, float], tuple[int, float]]:
    """The two grid rows immediately below/above target_hour."""
    lower = max((r for r in rows if r[0] <= target_hour), key=lambda r: r[0])
    upper = min((r for r in rows if r[0] >= target_hour), key=lambda r: r[0])
    return lower, upper


def append_assumptions_doc(rows: list[tuple[int, float]]) -> None:
    grid_peak_hour, grid_peak_level = max(rows, key=lambda r: r[1])
    (lower_hour, lower_level), (upper_hour, upper_level) = grid_bracket(rows, PEAK_HOUR)

    lines = [
        "# Flood Hydrograph (flood_hydrograph.py)",
        "",
        "Appended by flood_hydrograph.py — this section is regenerated (replaced) on every run.",
        "",
        "## No report.txt/PDF exists in this workspace",
        "",
        "steps.md cites real hydrograph figures from an \"uploaded report PDF\", but no such",
        "PDF (or any report.txt) is present anywhere in this workspace to grep for exact",
        "threshold values. What follows uses:",
        "",
        "- **REAL** (from steps.md): the event timeline (which hour each stage happened)",
        f"  and the peak value, {PEAK_LEVEL_M_AHD}m AHD at hour {PEAK_HOUR} (3pm Mon 28 Feb).",
        "- **ESTIMATED** (not from any file in this workspace — commonly-reported BOM river",
        "  height classification levels for the Lismore/Wilsons River gauge, general public",
        f"  knowledge): minor {MINOR_LEVEL_M_AHD}m, moderate {MODERATE_LEVEL_M_AHD}m,",
        f"  major {MAJOR_LEVEL_M_AHD}m AHD.",
        "- **ESTIMATED, added beyond the given timeline**: a recession trough at hour 66 and",
        "  a baseline taper point at hour 240 — needed purely so piecewise-linear",
        "  interpolation can show \"exceeded, then recedes, then re-exceeded\" rather than a",
        "  flat line between two same-height anchors.",
        "",
        "## Anchor points",
        "",
        "| interval_hour | gauge_water_level_m_ahd | Note |",
        "|---|---|---|",
    ]
    for hour, level, note in ANCHORS:
        lines.append(f"| {hour:g} | {level:.2f} | {note} |")
    lines += [
        "",
        "## Output grid vs true peak",
        "",
        f"flood_hydrograph.csv is sampled every {INTERVAL_STEP}h from 0 to {LAST_HOUR} (project",
        f"convention), which does NOT land exactly on the true peak hour ({PEAK_HOUR}). The",
        f"nearest grid points are hour {lower_hour} ({lower_level:.2f}m) and hour {upper_hour}",
        f"({upper_level:.2f}m) — both below the true peak of {PEAK_LEVEL_M_AHD}m.",
        f"The grid's own maximum is {grid_peak_level}m at hour {grid_peak_hour}, understating the",
        f"true peak by {PEAK_LEVEL_M_AHD - grid_peak_level:.2f}m. Flagged rather than silently",
        "presented as matching the real peak.",
        "",
    ]

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Flood Hydrograph (flood_hydrograph.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(rows: list[tuple[int, float]]) -> None:
    print("\n=== Summary ===")
    print(f"Wrote {OUTPUT_CSV.name}: {len(rows)} rows, interval_hour 0-{LAST_HOUR} step {INTERVAL_STEP}")
    print("\ninterval_hour, gauge_water_level_m_ahd:")
    for hour, level in rows:
        print(f"  {hour:>4}  {level:>6.2f}")

    grid_peak_hour, grid_peak_level = max(rows, key=lambda r: r[1])
    print(f"\nTrue peak (from anchors): {PEAK_LEVEL_M_AHD:.2f}m AHD at interval_hour {PEAK_HOUR} "
          f"(expected 14.36m)")
    print(f"Nearest {INTERVAL_STEP}h-grid peak: {grid_peak_level:.2f}m AHD at interval_hour {grid_peak_hour} "
          f"(under-samples the true peak by {PEAK_LEVEL_M_AHD - grid_peak_level:.2f}m)")


def main() -> None:
    rows = build_hydrograph()
    write_csv(rows)
    append_assumptions_doc(rows)
    print(f"Appended documentation to {ASSUMPTIONS_MD.name}")
    print_summary(rows)


if __name__ == "__main__":
    main()
