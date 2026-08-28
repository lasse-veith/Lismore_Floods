"""Models the addition of WORK as time progresses — not price per job.

Mold growth after a property has sat wet/idle for too long requires more
repainting/plastering/disposal/demolition/drying-decontamination. This never
changes the unit rate of any job; it only scales how much of that job needs
doing, via scope_multiplier, applied by Market_price.py to the whole
labour+materials total for that property.

MOLD_THRESHOLD_DAYS = 18 and the 2%/day, 40% cap are this file's own assumed
magnitudes (not sourced) — moderate, capped growth reflecting that mold
remediation scope grows with idle time but plateaus rather than compounding
indefinitely.
"""

from __future__ import annotations

MOLD_THRESHOLD_DAYS = 18
SCOPE_GROWTH_RATE_PER_DAY = 0.02
MAX_SCOPE_MULTIPLIER_INCREASE = 0.40


def compute_scope_multiplier(job_start_day: float) -> float:
    """job_start_day: days between the flood and this property's repair job
    actually starting (== days the property sat flooded/idle before work
    began). Jobs starting within MOLD_THRESHOLD_DAYS get no scope penalty;
    beyond that, scope grows 2%/day idle, capped at +40%."""
    days_idle = job_start_day
    if days_idle > MOLD_THRESHOLD_DAYS:
        return 1.0 + min(SCOPE_GROWTH_RATE_PER_DAY * (days_idle - MOLD_THRESHOLD_DAYS), MAX_SCOPE_MULTIPLIER_INCREASE)
    return 1.0
