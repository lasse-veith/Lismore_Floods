"""Build the shapely Polygon that outlines the Lismore project area from
coords.txt. Shared by gnaf_append.py (address filtering) and
elevation_append.py (DEM tile filtering) so every stage filters against the
exact same polygon.
"""

from __future__ import annotations

import re
from pathlib import Path

from shapely.geometry import Polygon

BASE_DIR = Path(__file__).resolve().parent
COORDS_FILE = BASE_DIR / "coords.txt"

NUMBER_RE = re.compile(r"-?\d+\.?\d*")


def parse_coords(path: Path = COORDS_FILE) -> Polygon:
    """Build a shapely Polygon from a coords file.

    Handles:
      - a KML <coordinates> block (lon,lat[,alt] tokens, space separated)
      - one "lat lon" (or "lon lat") pair per line
      - a single space-separated string of numbers ("poly string")
    Coordinate order is auto-detected/normalized to (lon, lat) since that's
    what shapely.Polygon expects.
    """
    text = path.read_text()

    kml_match = re.search(r"<coordinates>(.*?)</coordinates>", text, re.DOTALL)
    if kml_match:
        points = []
        for token in kml_match.group(1).split():
            parts = token.split(",")
            if len(parts) >= 2:
                lon, lat = float(parts[0]), float(parts[1])
                points.append((lon, lat))
        if len(points) >= 3:
            return _finalize_polygon(points)

    # Fallback: strip markup/comment lines, keep lines that contain digits.
    candidate_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith("<")
        and not line.strip().startswith("=")
        and re.search(r"\d", line)
    ]

    # Try "one pair per line" first.
    points = []
    one_pair_per_line = True
    for line in candidate_lines:
        nums = [float(n) for n in NUMBER_RE.findall(line)]
        if len(nums) < 2:
            one_pair_per_line = False
            break
        points.append((nums[0], nums[1]))

    if not (one_pair_per_line and len(points) >= 3):
        # Fall back to: everything is one big space-separated poly string.
        all_nums = [float(n) for n in NUMBER_RE.findall(" ".join(candidate_lines))]
        points = [(all_nums[i], all_nums[i + 1]) for i in range(0, len(all_nums) - 1, 2)]

    if len(points) < 3:
        raise ValueError(f"Could not parse a polygon (need >=3 points) from {path}")

    return _finalize_polygon(points)


def _finalize_polygon(points: list[tuple[float, float]]) -> Polygon:
    """Normalize point order to (lon, lat) and build a valid Polygon."""

    def looks_like_lat(v: float) -> bool:
        return -90 <= v <= 90

    first_vals = [p[0] for p in points]
    second_vals = [p[1] for p in points]
    # If the first coordinate of every point could be a latitude, but the
    # second coordinate has values a latitude could never hold, the pairs
    # are (lat, lon) and need swapping to (lon, lat).
    if all(looks_like_lat(v) for v in first_vals) and any(abs(v) > 90 for v in second_vals):
        points = [(lon, lat) for lat, lon in points]

    poly = Polygon(points)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def get_polygon(path: Path = COORDS_FILE) -> Polygon:
    """Convenience entry point used by the rest of the pipeline."""
    return parse_coords(path)
