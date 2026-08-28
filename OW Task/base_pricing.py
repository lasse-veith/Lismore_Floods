"""Canonical unit-cost tables for flood repair cost modelling.

The single source of truth both static_pricing.py (initial static estimate)
and the future market_pricing.py / Repair Model (the "actual" repair
simulation — not built yet, see Repair Model/Data/) read from, so both share
identical base rates instead of each duplicating its own copy.

Contents:
  - The full unit-cost table: one dict per component (switchboard-by-type,
    flooring-by-type, kitchen-cabinetry-by-spec, appliance-bundle-by-tier),
    plus the flat per-m2/per-circuit/per-linear-metre rates (plasterboard,
    electrical, drying/decon, painting, demolition). Nothing hardcoded
    anywhere else — static_pricing.py imports these directly.
  - The labour/material split per component (fraction of that line's cost
    that is labour vs material) — needed by market_pricing.py to layer
    supply-side effects (stock depletion, labour surge) on the correct half
    of each cost line.
  - The regional loading constant (1.15x) and which components it applies to.
  - sample_volatility(seed, sigma=0.15): the only stochastic thing in this
    file — a log-normal multiplier, median 1.0, representing real trade-
    price volatility around the base rate. Seeded (via a stable hash of
    whatever `seed` is given — typically (property_id, component_name)) for
    reproducibility, matching the row_rng() pattern used everywhere else in
    this pipeline: the same seed always produces the same multiplier, so
    re-running a script doesn't change already-computed costs.

All unit costs and the labour/material splits below are MODELLING
ASSUMPTIONS, not sourced trade quotes — see SYNTHETIC_ASSUMPTIONS.md.
"""

from __future__ import annotations

import random
from hashlib import sha256

# --------------------------------------------------------------------------
# Unit cost table (AUD, regional NSW, GST-inclusive trade rates)
# --------------------------------------------------------------------------

SWITCHBOARD_RATE_AUD = {
    "ceramic_fuse": 1200,
    "circuit_breaker_basic": 1600,
    "circuit_breaker_rcd": 2100,
    "smart_ev_ready": 3800,
}

FLOORING_RATE_AUD_PER_M2 = {
    "carpet": 55,
    "vinyl": 45,
    "laminate": 60,
    "timber_floorboards": 130,  # most expensive — specialist trade, matching existing
    "tile": 95,
    "polished_concrete": 110,  # resurfacing/regrinding, specialist
}

# Union of the original 4-category spec and the categories actually produced
# by kitchen_derivation.py (basic_laminate/standard_laminate/stone_benchtop/
# premium_stone_island).
KITCHEN_CABINETRY_RATE_AUD_PER_LM = {
    "original_dated": 450,
    "basic_laminate": 600,
    "standard_laminate": 650,
    "mid_range_updated": 950,
    "stone_benchtop": 1300,
    "premium_stone_benchtop": 1300,
    "premium_stone_island": 1600,
}

APPLIANCE_RATE_AUD = {
    "original_dated": 3500,
    "basic_laminate": 4200,
    "standard_laminate": 4200,
    "mid_range_updated": 6500,
    "stone_benchtop": 9500,
    "premium_stone_benchtop": 9500,
    "premium_stone_island": 12000,
}

ELECTRICAL_RATE_AUD_PER_CIRCUIT = 180
PLASTERBOARD_RATE_AUD_PER_M2 = 85
DRYING_DECON_RATE_AUD_PER_M2 = 45
PAINTING_RATE_AUD_PER_M2 = 28
DEMOLITION_RATE_AUD_PER_M2 = 22

# --------------------------------------------------------------------------
# Regional loading — applied to labour-heavy lines only
# --------------------------------------------------------------------------

REGIONAL_LOADING_FACTOR = 1.15
REGIONAL_LOADING_APPLIES_TO = {
    "switchboard", "plasterboard", "flooring", "kitchen_cabinetry", "electrical", "painting",
}  # NOT: appliances (retail goods), drying_decon, demolition

# --------------------------------------------------------------------------
# Regional repair-market capacity — shared by static_pricing.py's
# documentation and by Repair Model/Data/{repair_queue,stock_depletion}.py's
# queueing/stock-buffer math.
# --------------------------------------------------------------------------

NUM_BUILDING_SUPPLIERS = 7            # real, from directory search
JOBS_PER_SUPPLIER_PER_WEEK = 20       # ESTIMATED

# --------------------------------------------------------------------------
# Labour / material split per component: (labour_fraction, material_fraction)
# plasterboard/electrical/appliances as given in the original spec; the
# remaining six are this file's own trade-based ESTIMATES, not sourced.
# --------------------------------------------------------------------------

LABOUR_MATERIAL_SPLIT = {
    "switchboard": (0.50, 0.50),        # ESTIMATED — mixed skilled labour + board/breaker parts
    "plasterboard": (0.55, 0.45),       # given
    "flooring": (0.35, 0.65),           # ESTIMATED — material (esp. timber/tile) dominates
    "kitchen_cabinetry": (0.30, 0.70),  # ESTIMATED — cabinetry itself is the dominant material cost
    "electrical": (0.75, 0.25),         # given
    "appliances": (0.0, 1.0),           # given — pure retail goods
    "drying_decon": (0.70, 0.30),       # ESTIMATED — mostly equipment/labour, some consumables
    "painting": (0.65, 0.35),           # ESTIMATED — mostly labour, paint itself relatively cheap per m2
    "demolition": (0.60, 0.40),         # ESTIMATED — labour + disposal/tip fees
}

# --------------------------------------------------------------------------
# Volatility
# --------------------------------------------------------------------------

def sample_volatility(seed, sigma: float = 0.15) -> float:
    """Log-normal multiplier, median 1.0, representing real trade-price
    volatility around a base rate. `seed` may be any value (e.g. a
    property_id, or a (property_id, component) tuple) — it's hashed
    internally, so callers don't need to worry about random.seed()'s
    supported input types."""
    seed_int = int(sha256(str(seed).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    return rng.lognormvariate(0, sigma)
