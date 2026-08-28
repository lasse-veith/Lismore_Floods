"""Determines exactly two numbers per flooded property — job_start_day and
job_end_day — from the property's real component-level cost breakdown
(already in properties.csv from static_pricing.py), not a single dollar
proxy.

Day 0 = flood peak (28 Feb 2022) — a DIFFERENT epoch from
flood_hydrograph.py's interval_hour (hours since 24 Feb, the first
minor-level exceedance). The two "day"/"interval_hour" axes serve different
stages of this project and are not meant to be compared directly.

Step 1 — insurance eligibility: insurance_delay.py's log-normal draw
(already floored at 0, since log-normal can't go negative), giving
insurance_eligible_day.

Step 2 — queue wait: inverse-transform sampling from a Weibull CDF. Rather
than simulating a day-by-day capacity-constrained queue (which only
approximates a target rollout shape on average), each property's queue
position is first reduced to a single priority PERCENTILE, then mapped
through the inverse Weibull CDF — this guarantees the whole portfolio's
cumulative rollout follows the target S-curve shape BY CONSTRUCTION, not
just on average.

    priority_score(i) = SEVERITY_WEIGHT * severity_percentile(i)
                       + AFFLUENCE_WEIGHT * affluence_score(i)
                       + NOISE_WEIGHT * Uniform(0, 1)
    severity_percentile = rank-normalized peak_depth_above_floor_m across
                           flooded properties (0=shallowest, 1=deepest)
    p(i) = percentile_rank(priority_score(i))   # 0 = highest priority
                                                 # (done first), 1 = lowest
                                                 # (done last)

    queue_day(i) = ETA_ROLLOUT_DAYS * (-ln(1 - p(i))) ** (1 / BETA_ROLLOUT)

The 0.5/0.3/0.2 weighting and the beta/eta rollout-curve constants are this
file's own ESTIMATE, not sourced — the weighting reflects that trades
plausibly prioritize both the worst-damaged properties and (realistically,
if not equitably) more affluent customers, plus some irreducible randomness
in real-world scheduling; flagged transparently rather than asserted as
fair. p(i) is computed via a (rank + 0.5) / n fractional-rank formula (not
the plain 0..1 rank-normalize used for severity_percentile) so it always
lands strictly inside (0, 1) — the inverse Weibull CDF is singular at
p = 1 (ln(1 - p) -> -inf), so even the single lowest-priority property must
never receive exactly p = 1.

Step 3 — combine with insurance delay:

    job_start_day(i) = insurance_eligible_day(i) + queue_day(i)

Step 4 — job duration: six sequential construction stages (one crew works a
property through them in order, not multiple trades in parallel), each
included only if its cost component is > 0, each sized off real physical
quantities already computed by static_pricing.py, each with its own
Uniform(-1, 1) noise term:

    1. Demolition & drying/decon (always first): 1 + building_area_m2/80 + 3
    2. Electrical + switchboard rough-in:        max(circuit_count/6, 1)
    3. Plasterboard reinstatement:                wetted_wall_area_m2/40 + 2
    4. Painting (follows plaster cure):           wetted_wall_area_m2/60
    5. Flooring:                                  building_area_m2/50
    6. Kitchen cabinetry + appliances:             kitchen_linear_m/3 + 1

job_end_day = job_start_day + job_duration_days.

Dependencies: insurance_delay.py, plus the component-level columns already
present in properties.csv from static_pricing.py — no new upstream files
required, just reading columns that already exist.
"""

from __future__ import annotations

import math
import random
import sys
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Repair Model/Data
ROOT_DIR = BASE_DIR.parent.parent                       # OW Task
sys.path.insert(0, str(ROOT_DIR))                        # base_pricing.py (unused directly, kept for path parity)
sys.path.insert(0, str(BASE_DIR))                         # insurance_delay.py (sibling)

from insurance_delay import sample_insurance_delay_days  # noqa: E402

SEVERITY_WEIGHT = 0.5
AFFLUENCE_WEIGHT = 0.3
NOISE_WEIGHT = 0.2

BETA_ROLLOUT = 2.0     # Weibull shape — S-curve steepness, tuned 1.8-2.5
ETA_ROLLOUT_DAYS = 230  # Weibull scale — how far out the bulk of the rollout stretches


def _row_rng(property_id: str, tag: str) -> random.Random:
    seed_int = int(sha256(f"{property_id}:{tag}".encode()).hexdigest()[:16], 16)
    return random.Random(seed_int)


def _rank_normalize(values: list[float]) -> list[float]:
    """0-1 scale where the lowest value maps to exactly 0 and the highest to
    exactly 1 (fractional rank, ties averaged)."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return [r / (n - 1) for r in ranks]


def _percentile_rank_desc(values: list[float]) -> list[float]:
    """Fractional percentile rank strictly inside the OPEN interval (0, 1):
    the HIGHEST value maps to the SMALLEST p (~0, highest priority, done
    first) and the LOWEST value maps to the LARGEST p (~1, lowest priority,
    done last). Ties share the average rank. The (rank + 0.5) / n offset
    keeps every p strictly inside (0, 1) — including the single best/worst
    property — since queue_day()'s inverse Weibull CDF is singular at
    p = 1."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: -values[i])  # order[0] = highest value
    desc_rank = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2
        for k in range(i, j + 1):
            desc_rank[order[k]] = avg_rank
        i = j + 1
    return [(r + 0.5) / n for r in desc_rank]


def compute_priority_scores(flooded_rows: list[dict]) -> dict[str, float]:
    """priority_score(i) = SEVERITY_WEIGHT*severity_percentile(i) +
    AFFLUENCE_WEIGHT*affluence_score(i) + NOISE_WEIGHT*Uniform(0,1). Higher
    score = higher priority (done first). The Uniform(0,1) term is
    deterministically seeded per property (tag "priority_noise") so the
    whole pipeline stays reproducible despite the randomness."""
    depths = [row["peak_depth_above_floor_m"] for row in flooded_rows]
    severity_percentiles = _rank_normalize(depths)
    scores = {}
    for row, sev_pct in zip(flooded_rows, severity_percentiles):
        affluence = row.get("affluence_score")
        affluence = affluence if affluence is not None else 0.5
        noise = _row_rng(row["property_id"], "priority_noise").random()
        scores[row["property_id"]] = (
            SEVERITY_WEIGHT * sev_pct + AFFLUENCE_WEIGHT * affluence + NOISE_WEIGHT * noise
        )
    return scores


def compute_queue_percentiles(flooded_rows: list[dict]) -> dict[str, float]:
    """p(i) per property_id — 0 = highest priority (done first), 1 = lowest
    (done last), via a descending percentile rank of priority_score."""
    priority = compute_priority_scores(flooded_rows)
    pids = [row["property_id"] for row in flooded_rows]
    p_values = _percentile_rank_desc([priority[pid] for pid in pids])
    return dict(zip(pids, p_values))


def compute_queue_day(p: float) -> float:
    """Inverse Weibull CDF: maps a queue percentile p in (0, 1) to a queue
    wait in days, so the portfolio's cumulative rollout follows a Weibull
    S-curve (shape BETA_ROLLOUT, scale ETA_ROLLOUT_DAYS) by construction."""
    return ETA_ROLLOUT_DAYS * (-math.log(1 - p)) ** (1 / BETA_ROLLOUT)


def compute_job_duration_days(row: dict) -> float:
    rng = _row_rng(row["property_id"], "job_duration")
    total = 0.0

    if (row.get("drying_decon_cost_aud") or 0) > 0 or (row.get("demolition_cost_aud") or 0) > 0:
        total += 1 + row["building_area_m2"] / 80 + 3 + rng.uniform(-1, 1)

    if (row.get("electrical_cost_aud") or 0) > 0 or (row.get("switchboard_cost_aud") or 0) > 0:
        total += max(row["circuit_count"] / 6, 1) + rng.uniform(-1, 1)

    if (row.get("plasterboard_cost_aud") or 0) > 0:
        total += row["wetted_wall_area_m2"] / 40 + 2 + rng.uniform(-1, 1)

    if (row.get("painting_cost_aud") or 0) > 0:
        total += row["wetted_wall_area_m2"] / 60 + rng.uniform(-1, 1)

    if (row.get("flooring_cost_aud") or 0) > 0:
        total += row["building_area_m2"] / 50 + rng.uniform(-1, 1)

    if (row.get("kitchen_cabinetry_cost_aud") or 0) > 0 or (row.get("appliance_cost_aud") or 0) > 0:
        kitchen_linear_m = 4.0 + 0.5 * row["bedroom_count"]
        total += kitchen_linear_m / 3 + 1 + rng.uniform(-1, 1)

    return max(total, 1.0)  # defensive floor — stage 1 alone already guarantees >=3 days in practice


def assign_job_start_days(flooded_rows: list[dict]) -> tuple[dict[str, int], dict[str, float]]:
    """Inverse-transform sampling from a Weibull CDF: each property's queue
    percentile p(i) is mapped through the inverse Weibull CDF to a
    queue_day, then added to its insurance_eligible_day. This guarantees
    the PORTFOLIO'S cumulative rollout follows a Weibull S-curve shape by
    construction (not just on average, as the old day-by-day
    capacity-matching simulation only approximated). Returns
    (job_start_day, insurance_eligible_day), both keyed by property_id."""
    eligible_day = {
        row["property_id"]: sample_insurance_delay_days(row["property_id"]) for row in flooded_rows
    }
    p_by_pid = compute_queue_percentiles(flooded_rows)

    job_start_day: dict[str, int] = {}
    for row in flooded_rows:
        pid = row["property_id"]
        queue_day = compute_queue_day(p_by_pid[pid])
        job_start_day[pid] = max(0, round(eligible_day[pid] + queue_day))

    return job_start_day, eligible_day


def compute_job_timelines(flooded_rows: list[dict]) -> dict[str, dict]:
    """Top-level entry point: returns, per property_id, a dict with
    insurance_eligible_day, job_start_day, job_duration_days, job_end_day."""
    job_start_day, eligible_day = assign_job_start_days(flooded_rows)

    timelines = {}
    for row in flooded_rows:
        pid = row["property_id"]
        start = job_start_day[pid]
        duration = max(1, round(compute_job_duration_days(row)))
        timelines[pid] = {
            "insurance_eligible_day": round(eligible_day[pid], 2),
            "job_start_day": start,
            "job_duration_days": duration,
            "job_end_day": start + duration,
        }
    return timelines
