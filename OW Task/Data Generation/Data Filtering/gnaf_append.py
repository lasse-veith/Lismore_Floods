"""Extract and clean NSW G-NAF address data inside a given polygon.

Streams NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv and NSW_ADDRESS_DETAIL_psv.psv in
chunks (never loaded fully into memory), joins in the small
NSW_STREET_LOCALITY_psv.psv / NSW_LOCALITY_psv.psv lookup tables, and
assembles one row per property: property_id, address, suburb, postcode,
latitude, longitude. Called from assembly.py — the returned DataFrame is not
yet validated against PropertyRecord (that happens in assembly.py, after
elevation_append.py has had a chance to add ground_elevation_m_ahd).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from shapely import contains_xy as points_in_polygon
from shapely.geometry import Polygon

BASE_DIR = Path(__file__).resolve().parent           # Data Generation/Data Filtering
ROOT_DIR = BASE_DIR.parent.parent                     # OW Task
STANDARD_DIR = (
    ROOT_DIR
    / "Data Sources"
    / "2021_GCP_all_for_NSW_short-header"
    / "g-naf_aug26_allstates_gda2020_psv_110"
    / "G-NAF"
    / "G-NAF AUGUST 2026"
    / "Standard"
)

GEOCODE_FILE = STANDARD_DIR / "NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv"
DETAIL_FILE = STANDARD_DIR / "NSW_ADDRESS_DETAIL_psv.psv"
STREET_FILE = STANDARD_DIR / "NSW_STREET_LOCALITY_psv.psv"
LOCALITY_FILE = STANDARD_DIR / "NSW_LOCALITY_psv.psv"

CHUNK_SIZE = 250_000

DETAIL_COLS = [
    "ADDRESS_DETAIL_PID",
    "DATE_RETIRED",
    "BUILDING_NAME",
    "LOT_NUMBER_PREFIX",
    "LOT_NUMBER",
    "LOT_NUMBER_SUFFIX",
    "FLAT_TYPE_CODE",
    "FLAT_NUMBER_PREFIX",
    "FLAT_NUMBER",
    "FLAT_NUMBER_SUFFIX",
    "NUMBER_FIRST_PREFIX",
    "NUMBER_FIRST",
    "NUMBER_FIRST_SUFFIX",
    "NUMBER_LAST_PREFIX",
    "NUMBER_LAST",
    "NUMBER_LAST_SUFFIX",
    "STREET_LOCALITY_PID",
    "LOCALITY_PID",
    "POSTCODE",
]

PROPERTY_COLUMNS = ["property_id", "address", "suburb", "postcode", "latitude", "longitude"]


# --------------------------------------------------------------------------
# Chunked scan of NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv for polygon hits
# --------------------------------------------------------------------------

def find_matching_geocodes(polygon: Polygon) -> tuple[dict[str, tuple[float, float]], int]:
    """Stream the geocode file in chunks, keep rows whose point falls inside
    the polygon. Returns {ADDRESS_DETAIL_PID: (longitude, latitude)}."""
    matches: dict[str, tuple[float, float]] = {}
    total_rows = 0

    reader = pd.read_csv(
        GEOCODE_FILE,
        sep="|",
        dtype=str,
        chunksize=CHUNK_SIZE,
        usecols=["ADDRESS_DETAIL_PID", "DATE_RETIRED", "LONGITUDE", "LATITUDE"],
    )
    for chunk in reader:
        total_rows += len(chunk)
        chunk = chunk[chunk["DATE_RETIRED"].isna()]
        if chunk.empty:
            continue

        lon = pd.to_numeric(chunk["LONGITUDE"], errors="coerce")
        lat = pd.to_numeric(chunk["LATITUDE"], errors="coerce")
        valid = lon.notna() & lat.notna()
        if not valid.any():
            continue

        pids = chunk.loc[valid, "ADDRESS_DETAIL_PID"].to_numpy()
        lon_vals = lon[valid].to_numpy()
        lat_vals = lat[valid].to_numpy()

        inside = points_in_polygon(polygon, lon_vals, lat_vals)
        for pid, lo, la in zip(pids[inside], lon_vals[inside], lat_vals[inside]):
            matches[pid] = (float(lo), float(la))

    return matches, total_rows


# --------------------------------------------------------------------------
# Chunked join against NSW_ADDRESS_DETAIL_psv.psv, plus small lookup tables
# NSW_STREET_LOCALITY_psv.psv and NSW_LOCALITY_psv.psv
# --------------------------------------------------------------------------

def load_matched_details(pid_set: set[str]) -> pd.DataFrame:
    """Stream the (large) address detail file in chunks, keeping only rows
    whose ADDRESS_DETAIL_PID matched the polygon filter — never materializes
    the full file in memory."""
    frames = []
    reader = pd.read_csv(
        DETAIL_FILE,
        sep="|",
        dtype=str,
        chunksize=CHUNK_SIZE,
        usecols=DETAIL_COLS,
    )
    for chunk in reader:
        hit = chunk[chunk["ADDRESS_DETAIL_PID"].isin(pid_set) & chunk["DATE_RETIRED"].isna()]
        if not hit.empty:
            frames.append(hit)

    if not frames:
        return pd.DataFrame(columns=DETAIL_COLS)
    details = pd.concat(frames, ignore_index=True)
    return details.drop_duplicates(subset="ADDRESS_DETAIL_PID", keep="first")


def load_street_locality() -> pd.DataFrame:
    df = pd.read_csv(
        STREET_FILE,
        sep="|",
        dtype=str,
        usecols=["STREET_LOCALITY_PID", "DATE_RETIRED", "STREET_NAME", "STREET_TYPE_CODE", "STREET_SUFFIX_CODE"],
    )
    df = df[df["DATE_RETIRED"].isna()]
    return df.drop_duplicates(subset="STREET_LOCALITY_PID", keep="first").set_index("STREET_LOCALITY_PID")


def load_locality() -> pd.DataFrame:
    df = pd.read_csv(
        LOCALITY_FILE,
        sep="|",
        dtype=str,
        usecols=["LOCALITY_PID", "DATE_RETIRED", "LOCALITY_NAME", "PRIMARY_POSTCODE"],
    )
    df = df[df["DATE_RETIRED"].isna()]
    return df.drop_duplicates(subset="LOCALITY_PID", keep="first").set_index("LOCALITY_PID")


# --------------------------------------------------------------------------
# Assemble address/suburb/postcode, generate property_id
# --------------------------------------------------------------------------

def _clean(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text == "" or text.lower() in ("nan", "none"):
        return ""
    return text


def make_property_id(address_detail_pid: str) -> str:
    """Canonical, deterministic property_id derived from the stable G-NAF
    ADDRESS_DETAIL_PID, so the same address always gets the same ID across
    re-runs of the pipeline."""
    return f"GNAF-{address_detail_pid}"


def build_number_part(row: pd.Series) -> str:
    first = _clean(row.get("NUMBER_FIRST_PREFIX")) + _clean(row.get("NUMBER_FIRST")) + _clean(row.get("NUMBER_FIRST_SUFFIX"))
    last = _clean(row.get("NUMBER_LAST_PREFIX")) + _clean(row.get("NUMBER_LAST")) + _clean(row.get("NUMBER_LAST_SUFFIX"))
    if first and last:
        return f"{first}-{last}"
    if first:
        return first
    lot = _clean(row.get("LOT_NUMBER_PREFIX")) + _clean(row.get("LOT_NUMBER")) + _clean(row.get("LOT_NUMBER_SUFFIX"))
    if lot:
        return f"LOT {lot}"
    return ""


def build_flat_part(row: pd.Series) -> str:
    flat_type = _clean(row.get("FLAT_TYPE_CODE"))
    flat_number = _clean(row.get("FLAT_NUMBER_PREFIX")) + _clean(row.get("FLAT_NUMBER")) + _clean(row.get("FLAT_NUMBER_SUFFIX"))
    if flat_type and flat_number:
        return f"{flat_type} {flat_number}"
    return flat_type or flat_number


def build_street_part(row: pd.Series, street_lookup: pd.DataFrame) -> str:
    pid = row.get("STREET_LOCALITY_PID")
    if pid not in street_lookup.index:
        return ""
    street = street_lookup.loc[pid]
    return " ".join(
        part
        for part in (_clean(street.get("STREET_NAME")), _clean(street.get("STREET_TYPE_CODE")), _clean(street.get("STREET_SUFFIX_CODE")))
        if part
    )


def assemble_dataframe(
    details: pd.DataFrame,
    geocodes: dict[str, tuple[float, float]],
    street_lookup: pd.DataFrame,
    locality_lookup: pd.DataFrame,
) -> pd.DataFrame:
    """Build one row per matched address: property_id, address, suburb,
    postcode, latitude, longitude. Not yet validated against PropertyRecord —
    that happens in assembly.py, after elevation has been appended."""
    rows = []

    for _, row in details.iterrows():
        pid = row["ADDRESS_DETAIL_PID"]
        if pid not in geocodes:
            continue
        longitude, latitude = geocodes[pid]

        locality_pid = row.get("LOCALITY_PID")
        locality_row = locality_lookup.loc[locality_pid] if locality_pid in locality_lookup.index else None
        suburb = _clean(locality_row.get("LOCALITY_NAME")) if locality_row is not None else ""

        postcode = _clean(row.get("POSTCODE"))
        if not postcode and locality_row is not None:
            postcode = _clean(locality_row.get("PRIMARY_POSTCODE"))

        address = " ".join(
            part
            for part in (
                _clean(row.get("BUILDING_NAME")),
                build_flat_part(row),
                build_number_part(row),
                build_street_part(row, street_lookup),
            )
            if part
        )

        rows.append(
            {
                "property_id": make_property_id(pid),
                "address": address or "UNKNOWN",
                "suburb": suburb or "UNKNOWN",
                "postcode": postcode or None,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    return pd.DataFrame(rows, columns=PROPERTY_COLUMNS)


# --------------------------------------------------------------------------
# Top-level entry point used by assembly.py
# --------------------------------------------------------------------------

def build_gnaf_properties(polygon: Polygon) -> pd.DataFrame:
    """Run the full G-NAF extraction/cleaning sub-pipeline for `polygon` and
    return the raw (unvalidated) properties DataFrame."""
    print(f"Scanning {GEOCODE_FILE.name} in chunks of {CHUNK_SIZE:,} rows ...")
    geocodes, total_geocode_rows = find_matching_geocodes(polygon)
    print(f"  scanned {total_geocode_rows:,} rows, {len(geocodes):,} fell inside the polygon")

    print(f"Joining matches against {DETAIL_FILE.name} in chunks ...")
    details = load_matched_details(set(geocodes.keys()))
    print(f"  matched {len(details):,} address detail rows")

    print(f"Loading lookup tables ({STREET_FILE.name}, {LOCALITY_FILE.name}) ...")
    street_lookup = load_street_locality()
    locality_lookup = load_locality()

    print("Assembling address/suburb/postcode ...")
    properties_df = assemble_dataframe(details, geocodes, street_lookup, locality_lookup)
    print(f"  {len(properties_df):,} property rows assembled")

    return properties_df
