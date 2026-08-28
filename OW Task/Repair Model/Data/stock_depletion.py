"""Models the local building-supplies warehouse stock buffer.

7 real Lismore-area building suppliers (base_pricing.NUM_BUILDING_SUPPLIERS,
"real, from directory search" per this file's original note), each
servicing ~20 jobs/week under normal conditions
(base_pricing.JOBS_PER_SUPPLIER_PER_WEEK, assumption), holding ~3 weeks of
buffer stock (BUFFER_WEEKS_OF_STOCK_HELD, assumption — typical retail
turnover) — giving a one-time initial buffer of 7 * 20 * 3 = 420 "job
equivalents" of local material stock.

This buffer depletes monotonically as properties' jobs START (materials are
ordered/consumed at job_start_day, 1 job-equivalent each) — no
replenishment is modelled during the simulation window, representing the
acute-disaster period before national/interstate supply chains catch up.
Once exhausted, every day from then on is "stocked out": Market_price.py's
logistics premium applies permanently for the rest of the simulation, not
just transiently — a one-way step, matching the levee-breach ratchet used
in flood_exposure.py for the same real-world reason (a shortage doesn't
self-heal mid-simulation).
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Repair Model/Data
ROOT_DIR = BASE_DIR.parent.parent                       # OW Task
sys.path.insert(0, str(ROOT_DIR))                        # base_pricing.py

from base_pricing import JOBS_PER_SUPPLIER_PER_WEEK, NUM_BUILDING_SUPPLIERS  # noqa: E402

BUFFER_WEEKS_OF_STOCK_HELD = 3  # ESTIMATED, typical retail turnover
TOTAL_LOCAL_STOCK_JOB_EQUIVALENTS = NUM_BUILDING_SUPPLIERS * JOBS_PER_SUPPLIER_PER_WEEK * BUFFER_WEEKS_OF_STOCK_HELD  # 420


def compute_stock_timeline(job_start_days: list[int], max_day: int) -> list[dict]:
    """One row per day, 0..max_day inclusive: cumulative_material_consumed,
    stock_remaining, is_stocked_out. Consumption is counted 1 job-equivalent
    per property at its job_start_day (materials are ordered when the job
    begins, not when it finishes)."""
    starts_per_day: dict[int, int] = {}
    for day in job_start_days:
        starts_per_day[day] = starts_per_day.get(day, 0) + 1

    rows = []
    cumulative = 0
    for day in range(0, max_day + 1):
        cumulative += starts_per_day.get(day, 0)
        stock_remaining = max(0, TOTAL_LOCAL_STOCK_JOB_EQUIVALENTS - cumulative)
        is_stocked_out = cumulative >= TOTAL_LOCAL_STOCK_JOB_EQUIVALENTS
        rows.append(
            {
                "day": day,
                "cumulative_material_consumed": cumulative,
                "stock_remaining": stock_remaining,
                "is_stocked_out": is_stocked_out,
            }
        )
    return rows


def find_stockout_day(stock_timeline: list[dict]) -> int | None:
    for row in stock_timeline:
        if row["is_stocked_out"]:
            return row["day"]
    return None
