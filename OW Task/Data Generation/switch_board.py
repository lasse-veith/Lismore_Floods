"""Sample a synthetic switchboard_type per property.

Categories: ceramic_fuse, circuit_breaker_basic, circuit_breaker_rcd,
smart_ev_ready.

STEP 0 — affluence_score: read directly from PropertyRecord.affluence_score,
the canonical per-suburb affluence rating persisted by
derive_affluence_and_construction_type.py (a min-max-scaled suburb median
house-price table). This script previously computed its own independent
affluence proxy here (from real 2021 Census GCP G02 median household
income) — that produced a different, unreconciled affluence figure from
every other script needing one (flooring_derivation.py and
kitchen_derivation.py each had their own building-area/bedroom/vehicle-based
proxy too). All of them now read the same persisted field instead, per the
project convention that affluence_score is computed once and reused
everywhere. The G02-income approach was arguably a "more real" signal in
isolation, but consistency across the whole pipeline was judged more
important than any single script's affluence definition — see
SYNTHETIC_ASSUMPTIONS.md for the full reasoning and the price table used.

STEP 1 — has_ev (compute first, drives everything else):
  base_ev_rate = 0.005   # regional NSW estimate, not Lismore-specific — flagged
  p_ev = base_ev_rate * (0.5 + 1.5 * affluence_score)
  p_ev = 0 if vehicle_count == 0 else min(p_ev, 0.03)
  sample has_ev ~ Bernoulli(p_ev)

  NOTE: this has_ev draw is independent of EV_append.py's ev_count (a
  different assumed rate, 1.67% per-vehicle vs. this 0.5% base-household
  rate) — the two are not reconciled and may disagree on any given
  property. has_ev exists solely to drive switchboard_type here; ev_count
  remains the authoritative per-property EV count for every other purpose.

STEP 2 — if has_ev:
  smart_ev_ready: 0.90, circuit_breaker_rcd: 0.10
  (an EV owner without a formal upgrade still needs at minimum RCD protection —
  10% represents charging via a standard outlet without a dedicated upgrade)

STEP 3 — if NOT has_ev, base distribution conditional on building_age_years:
  age > 60:        ceramic_fuse 0.60, basic 0.30, rcd 0.10
  age 30-60:        ceramic_fuse 0.20, basic 0.40, rcd 0.40
  age 10-30:        ceramic_fuse 0.03, basic 0.22, rcd 0.75
  age < 10:         ceramic_fuse 0.00, basic 0.05, rcd 0.95

STEP 4 — affluence adjustment (wealthier suburbs renovate/upgrade more often
regardless of original build age):
  shift weight of (0.15 * affluence_score) from ceramic_fuse into rcd,
  and (0.05 * affluence_score) from basic into rcd
  renormalize

STEP 5 — construction_type nudge:
  if construction_type == "high_stumps": +0.05 to ceramic_fuse
  (older elevated Queenslander-style homes are statistically more likely
  to retain original unrenovated wiring, since underfloor rewiring on
  stumped homes is more disruptive/expensive than slab homes)
  renormalize

Only fills switchboard_type where it is still null (re-runnable without
changing already-sampled values). Sampling is seeded per property_id.
Does not touch circuit_count (a separate, not-yet-written step).

Requires vehicle_count, building_age_years, affluence_score, and
construction_type to already be populated — i.e. run this after
apply_synthetic_attributes.py, derive_building_age.py, and
derive_affluence_and_construction_type.py.
"""

from __future__ import annotations

import json
import sys
import re
import random
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Data Generation
sys.path.insert(0, str(BASE_DIR / "Building Files"))   # assembly.py, Pydantic.py

from assembly import CSV_OUT, GEOJSON_OUT, write_csv, write_geojson  # noqa: E402
from Pydantic import PropertyRecord  # noqa: E402
ASSUMPTIONS_MD = BASE_DIR.parent / "SYNTHETIC_ASSUMPTIONS.md"

CATEGORIES = ["ceramic_fuse", "circuit_breaker_basic", "circuit_breaker_rcd", "smart_ev_ready"]

BASE_EV_RATE = 0.005  # regional NSW estimate, not Lismore-specific — flagged
EV_P_CAP = 0.03

BASE_DIST_BY_AGE = {
    "gt60": {"ceramic_fuse": 0.60, "circuit_breaker_basic": 0.30, "circuit_breaker_rcd": 0.10},
    "age30_60": {"ceramic_fuse": 0.20, "circuit_breaker_basic": 0.40, "circuit_breaker_rcd": 0.40},
    "age10_30": {"ceramic_fuse": 0.03, "circuit_breaker_basic": 0.22, "circuit_breaker_rcd": 0.75},
    "lt10": {"ceramic_fuse": 0.00, "circuit_breaker_basic": 0.05, "circuit_breaker_rcd": 0.95},
}


def row_rng(property_id: str) -> random.Random:
    seed = int(sha256(property_id.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def weighted_choice(dist: dict[str, float], rng: random.Random) -> str:
    keys = list(dist.keys())
    weights = [dist[k] for k in keys]
    if sum(weights) <= 0:
        return rng.choice(keys)
    return rng.choices(keys, weights=weights, k=1)[0]


def renormalize(dist: dict[str, float]) -> dict[str, float]:
    total = sum(dist.values())
    if total <= 0:
        return {k: 1.0 / len(dist) for k in dist}
    return {k: v / total for k, v in dist.items()}


def age_bracket(building_age_years: int) -> str:
    if building_age_years > 60:
        return "gt60"
    if building_age_years >= 30:
        return "age30_60"
    if building_age_years >= 10:
        return "age10_30"
    return "lt10"


# --------------------------------------------------------------------------
# STEPS 1-5: sample switchboard_type per property
# --------------------------------------------------------------------------

def apply_affluence_adjustment(dist: dict[str, float], affluence_score: float) -> dict[str, float]:
    dist = dict(dist)
    shift_from_ceramic = min(dist["ceramic_fuse"], 0.15 * affluence_score)
    shift_from_basic = min(dist["circuit_breaker_basic"], 0.05 * affluence_score)
    dist["ceramic_fuse"] -= shift_from_ceramic
    dist["circuit_breaker_basic"] -= shift_from_basic
    dist["circuit_breaker_rcd"] += shift_from_ceramic + shift_from_basic
    return renormalize(dist)


def apply_construction_type_nudge(dist: dict[str, float], construction_type: str | None) -> dict[str, float]:
    if construction_type != "high_stumps":
        return dist
    dist = dict(dist)
    dist["ceramic_fuse"] += 0.05
    return renormalize(dist)


def determine_switchboard_type(
    vehicle_count: int,
    building_age_years: int,
    construction_type: str | None,
    affluence_score: float,
    rng: random.Random,
) -> tuple[str, bool, float]:
    p_ev = BASE_EV_RATE * (0.5 + 1.5 * affluence_score)
    p_ev = 0.0 if vehicle_count == 0 else min(p_ev, EV_P_CAP)
    has_ev = rng.random() < p_ev

    if has_ev:
        dist = {"smart_ev_ready": 0.90, "circuit_breaker_rcd": 0.10}
    else:
        dist = dict(BASE_DIST_BY_AGE[age_bracket(building_age_years)])
        dist = apply_affluence_adjustment(dist, affluence_score)
        dist = apply_construction_type_nudge(dist, construction_type)

    return weighted_choice(dist, rng), has_ev, p_ev


def enrich_properties(features: list[dict]) -> tuple[list[PropertyRecord], dict]:
    records: list[PropertyRecord] = []
    errors = 0
    stats = {
        "total": 0,
        "sampled": 0,
        "already_set": 0,
        "missing_affluence_score": [],
        "missing_vehicle_count": 0,
        "missing_building_age_years": 0,
        "has_ev_count": 0,
        "switchboard_type_counts": {},
        "high_stumps_nudge_applied": 0,
    }

    for feature in features:
        props = dict(feature["properties"])
        props["longitude"] = feature["geometry"]["coordinates"][0]
        props["latitude"] = feature["geometry"]["coordinates"][1]
        stats["total"] += 1

        if props.get("switchboard_type") is None:
            affluence_score = props.get("affluence_score")
            if affluence_score is None:
                stats["missing_affluence_score"].append(props.get("property_id"))
                affluence_score = 0.5

            vehicle_count = props.get("vehicle_count")
            if vehicle_count is None:
                stats["missing_vehicle_count"] += 1
                vehicle_count = 0

            building_age_years = props.get("building_age_years")
            if building_age_years is None:
                stats["missing_building_age_years"] += 1
                building_age_years = 45  # neutral "age30_60" default

            rng = row_rng(props["property_id"])
            switchboard_type, has_ev, _p_ev = determine_switchboard_type(
                vehicle_count,
                building_age_years,
                props.get("construction_type"),
                affluence_score,
                rng,
            )
            props["switchboard_type"] = switchboard_type

            stats["sampled"] += 1
            if has_ev:
                stats["has_ev_count"] += 1
            if not has_ev and props.get("construction_type") == "high_stumps":
                stats["high_stumps_nudge_applied"] += 1
        else:
            stats["already_set"] += 1

        stats["switchboard_type_counts"][props["switchboard_type"]] = (
            stats["switchboard_type_counts"].get(props["switchboard_type"], 0) + 1
        )

        try:
            records.append(PropertyRecord(**props))
        except Exception as exc:  # noqa: BLE001 - report and skip invalid rows
            errors += 1
            print(f"  ! validation failed for property_id={props.get('property_id')}: {exc}")

    if errors:
        print(f"  {errors} row(s) failed PropertyRecord validation and were dropped")

    return records, stats


# --------------------------------------------------------------------------
# Documentation
# --------------------------------------------------------------------------

def append_assumptions_doc(stats: dict) -> None:
    total_sampled = stats["sampled"]
    has_ev_rate = stats["has_ev_count"] / total_sampled if total_sampled else 0.0

    lines = [
        "# Switchboard Type (switch_board.py)",
        "",
        "Appended by switch_board.py — this section is regenerated (replaced) on every run.",
        "",
        "## affluence_score: grounded in derive_affluence_and_construction_type.py",
        "",
        "`affluence_score` is read directly from `PropertyRecord.affluence_score` — the",
        "canonical per-suburb affluence rating persisted by",
        "`derive_affluence_and_construction_type.py` (a min-max-scaled suburb median",
        "house-price table). This script previously computed its own independent affluence",
        "proxy from real 2021 Census GCP G02 median household income; that was a genuine",
        "ABS-sourced signal, but it disagreed with the different proxies",
        "flooring_derivation.py/kitchen_derivation.py each computed for themselves.",
        "Consistency across the whole pipeline was judged more important than any single",
        "script's affluence definition, so all of them now read the same persisted field.",
        (
            f"{len(stats['missing_affluence_score'])} properties had no affluence_score set "
            "(suburb not in the price table) and defaulted to 0.5."
            if stats["missing_affluence_score"]
            else "Every property had an affluence_score already set."
        ),
        "",
        "## has_ev is independent of EV_append.py's ev_count",
        "",
        f"`has_ev` here is a separate Bernoulli draw (`base_ev_rate = {BASE_EV_RATE}`, a",
        "regional NSW estimate, not Lismore-specific, capped at "
        f"{EV_P_CAP:.0%} and zeroed for `vehicle_count == 0`) used only to decide whether a",
        "property lands in the smart_ev_ready/rcd branch of switchboard_type. It is not",
        "reconciled with `ev_count` (EV_append.py's per-vehicle 1.67% draw) — the two may",
        "disagree on any given property.",
        f"This run: {stats['has_ev_count']:,} / {total_sampled:,} sampled properties drew has_ev=True ({has_ev_rate:.2%}).",
        "",
        "## construction_type nudge",
        "",
        "STEP 5 (`+0.05` to ceramic_fuse when `construction_type == \"high_stumps\"`) requires",
        "`construction_type`, populated by `derive_affluence_and_construction_type.py`.",
        f"Applied to {stats['high_stumps_nudge_applied']:,} properties this run.",
        "",
        "## Result (this run)",
        "",
        f"Sampled this run: {stats['sampled']:,}  |  already set (left untouched): {stats['already_set']:,}",
        "",
        "switchboard_type distribution:",
    ]
    for category in CATEGORIES:
        count = stats["switchboard_type_counts"].get(category, 0)
        pct = count / stats["total"] if stats["total"] else 0.0
        lines.append(f"- {category}: {count:,} ({pct:.1%})")
    lines.append("")

    existing = ASSUMPTIONS_MD.read_text() if ASSUMPTIONS_MD.exists() else ""
    marker = "# Switchboard Type (switch_board.py)"
    new_section = "\n".join(lines).strip("\n")
    pattern = re.compile(r"\n\n---\n\n" + re.escape(marker) + r".*?(?=\n\n---\n\n# |\Z)", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub("\n\n---\n\n" + new_section, existing, count=1)
    else:
        updated = existing.rstrip("\n") + "\n\n---\n\n" + new_section
    ASSUMPTIONS_MD.write_text(updated.rstrip("\n") + "\n")


def print_summary(stats: dict) -> None:
    print("\n=== Summary ===")
    print(f"Total properties: {stats['total']:,}")
    print(f"Sampled this run: {stats['sampled']:,}  |  already set: {stats['already_set']:,}")
    if stats["missing_affluence_score"]:
        print(f"  ! {len(stats['missing_affluence_score'])} properties had no suburb affluence_score, defaulted to 0.5")
    if stats["missing_vehicle_count"]:
        print(f"  ! {stats['missing_vehicle_count']:,} properties had no vehicle_count, treated as 0")
    if stats["missing_building_age_years"]:
        print(f"  ! {stats['missing_building_age_years']:,} properties had no building_age_years, defaulted to 45")

    total_sampled = stats["sampled"]
    has_ev_rate = stats["has_ev_count"] / total_sampled if total_sampled else 0.0
    print(f"\nhas_ev drawn True: {stats['has_ev_count']:,} / {total_sampled:,} sampled ({has_ev_rate:.2%})")
    print(f"high_stumps ceramic_fuse nudge applied: {stats['high_stumps_nudge_applied']:,}")

    print("\nswitchboard_type distribution:")
    for category in CATEGORIES:
        count = stats["switchboard_type_counts"].get(category, 0)
        pct = count / stats["total"] if stats["total"] else 0.0
        print(f"  {category:22s} {count:>6,}  ({pct:.1%})")


def main() -> None:
    print(f"Loading {GEOJSON_OUT.name} ...")
    geojson_data = json.loads(GEOJSON_OUT.read_text())

    print("Sampling switchboard_type (affluence_score grounded in derive_affluence_and_construction_type.py) ...")
    records, stats = enrich_properties(geojson_data["features"])
    print(f"  {len(records):,} records validated successfully")

    print(f"\nOverwriting {GEOJSON_OUT.name} and {CSV_OUT.name} ...")
    write_geojson(records, GEOJSON_OUT)
    write_csv(records, CSV_OUT)

    append_assumptions_doc(stats)
    print(f"Appended switchboard documentation to {ASSUMPTIONS_MD.name}")

    print_summary(stats)


if __name__ == "__main__":
    main()
