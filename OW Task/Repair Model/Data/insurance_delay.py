"""Models how long it takes an insurer to even respond to a claim.

Log-normal delay, bounded at 0 (log-normal is naturally >= 0 — exp() of a
normal draw). Deliberately "fairly normal" (bell-shaped, most claims
clustered near the middle) with only a SLIGHT right skew — modelling that
insurers in this event are national companies who can pull in staff from
other states, so the response-time tail isn't as extreme as a purely local,
capacity-constrained service would produce (contrast with repair_queue.py's
trade-capacity queue, which IS locally constrained and produces a much
longer tail).

MEAN_DELAY_DAYS and SIGMA are this file's own assumed magnitudes, not
sourced from the report or any real insurer SLA data. MEAN_DELAY_DAYS is the
distribution's true mean (not its median) — for a log-normal(mu, sigma),
mean = exp(mu + sigma^2/2), so mu is solved backwards from the target mean
rather than just taking log(mean) directly (which would set the median, not
the mean, to that figure).
"""

from __future__ import annotations

import math
import random
from hashlib import sha256

MEAN_DELAY_DAYS = 5.0
SIGMA = 0.45  # moderate spread -> bell-shaped with a slight right skew, not a long tail

_MU = math.log(MEAN_DELAY_DAYS) - SIGMA**2 / 2  # so the log-normal's true mean is MEAN_DELAY_DAYS


def sample_insurance_delay_days(property_id: str) -> float:
    """Reproducible per-property draw (seeded on property_id), so re-running
    the simulation with unchanged inputs reproduces the same delay."""
    seed_int = int(sha256(f"{property_id}:insurance_delay".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    return rng.lognormvariate(_MU, SIGMA)
