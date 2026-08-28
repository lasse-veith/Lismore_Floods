"""Print formatted street addresses from properties.geojson, e.g.:

    1 Macquarie St, Sydney NSW 2000

Prints the first NUM_ROWS properties — change NUM_ROWS below (or pass a
number as the first CLI arg) to print more/fewer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

NUM_ROWS = 500  # <-- change this to print a different number of rows

BASE_DIR = Path(__file__).resolve().parent
GEOJSON_FILE = BASE_DIR / "Output" / "properties.geojson"

STATE = "NSW"

# Australia Post style street-type abbreviations.
STREET_TYPE_ABBREVIATIONS = {
    "ALLEY": "All", "ARCADE": "Arc", "AVENUE": "Ave", "BEND": "Bend",
    "BOULEVARD": "Blvd", "BROADWAY": "Bdwy", "CHASE": "Chase", "CIRCLE": "Cir",
    "CIRCUIT": "Cct", "CLOSE": "Cl", "CORNER": "Cnr", "COURT": "Ct",
    "COVE": "Cove", "CRESCENT": "Cres", "CREST": "Crst", "CUL-DE-SAC": "Cds",
    "DRIVE": "Dr", "ESPLANADE": "Esp", "GARDENS": "Gdns", "GLEN": "Gln",
    "GROVE": "Gr", "HEIGHTS": "Hts", "HIGHWAY": "Hwy", "LANE": "Ln",
    "LOOP": "Loop", "MEWS": "Mews", "OUTLOOK": "Outlk", "PARADE": "Pde",
    "PARKWAY": "Pwy", "PLACE": "Pl", "PROMENADE": "Prom", "RESERVE": "Res",
    "RIDGE": "Rdge", "RISE": "Rise", "ROAD": "Rd", "ROW": "Row",
    "SQUARE": "Sq", "STREET": "St", "TERRACE": "Tce", "TRACK": "Trk",
    "TRAIL": "Trl", "VIEW": "Vw", "VISTA": "Vsta", "WALK": "Walk", "WAY": "Way",
}


def format_address(raw_address: str) -> str:
    words = []
    for word in raw_address.split():
        upper = word.upper()
        if upper in STREET_TYPE_ABBREVIATIONS:
            words.append(STREET_TYPE_ABBREVIATIONS[upper])
        elif len(word) <= 2:
            # keep short tokens (e.g. compass suffixes "N"/"S"/"E"/"W", unit "1")
            words.append(word.upper() if word.isalpha() else word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def format_line(properties: dict) -> str:
    address = format_address(properties.get("address", ""))
    suburb = (properties.get("suburb") or "").title()
    postcode = properties.get("postcode") or ""
    return f"{address}, {suburb} {STATE} {postcode}".strip()


def main() -> None:
    num_rows = int(sys.argv[1]) if len(sys.argv) > 1 else NUM_ROWS

    data = json.loads(GEOJSON_FILE.read_text())
    features = data["features"][:num_rows]

    for feature in features:
        print(format_line(feature["properties"]))


if __name__ == "__main__":
    main()
