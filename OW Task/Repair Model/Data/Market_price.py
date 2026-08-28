"""The shared townwide price curve — one timeline, not per-property.

For any given day t, defines the current labour multiplier and materials
multiplier, combining: the Weibull survival-function backlog decay and —
for materials only — the logistics premium step-function once t is past
the stockout day from stock_depletion.py. Also holds the log-normal
baseline sampling for each property's L_base/M_base (sampled once per
property, representing normal-market volatility independent of the surge).

Purpose: produces the shared townwide price curve — labour and materials
multipliers over time — that simulator.py evaluates at each property's
job_end_day to compute actual_repair_cost_aud. No base prices of its own;
every dollar figure comes from base_pricing.py, so this file and
static_pricing.py are guaranteed to start from identical numbers.

Imports from base_pricing.py:
  - LABOUR_MATERIAL_SPLIT (9 components -> labour/material fractions)
  - sample_volatility(seed, sigma=0.15) — the only source of randomness
    this file uses

What it does, in order:
  1. Build labour/material baselines per property — take the property's
     already-computed static cost breakdown (the 9 component dollar values
     from static_pricing.py, which themselves came from base_pricing.py's
     rate table), apply the labour/material split percentages, sum into
     labour_baseline and material_baseline.
  2. Apply volatility — call sample_volatility() once each for labour and
     materials, seeded on property_id, to get L_base and M_base. This is
     the step that makes the market/repair side stochastic while
     static_pricing.py stays a clean deterministic-plus-its-own-volatility
     point estimate — same base rates, but this file adds a second,
     independent layer of noise specific to the repair-market side.
  3. Define the shared surge intensity curve — one function of elapsed day
     only (not per-property): linear ramp from the flood to the day-6
     demand peak, then Weibull survival-function decay (beta=2.2, eta=75)
     beyond that. Every property reads off the same curve at whatever day
     its own job happens to land on.
  4. Apply the combined pricing formula at each property's job_end_day, with
     every uplift mechanism ADDITIVE relative to its baseline rather than
     stacked as separate multiplicative factors:
       scope_frac   = scope_multiplier - 1.0   (scope_growth.py's fraction)
       Labor(t)     = L_base * (1 + M_PEAK_LABOUR * surge_intensity(t) + scope_frac)
       Materials(t) = M_base * (1 + M_PEAK_MATERIALS * surge_intensity(t)
                                  + stock_empty(t) * L_PREMIUM * surge_intensity(t)
                                  + scope_frac)
       actual_repair_cost_aud = Labor(t) + Materials(t)
     where stock_empty(t) comes from stock_depletion.py's stockout day.

     NOTE — this was originally spec'd as scope_multiplier being multiplied
     onto (Labor(t) + Materials(t)) *after* those already had the surge
     multiplier baked in: `(Labor(t) + Materials(t)) * scope_multiplier`.
     That compounds independent effects instead of summing them — a job
     surged +40% (M_PEAK x surge_intensity = 0.4) that also hit scope's
     +40% cap became 1.4 x 1.4 = 1.96x (+96%), not the +80% two independent
     40% uplifts should give. Reordering where scope_multiplier multiplies
     into the chain does NOT fix this (multiplication commutes — the result
     is identical regardless of position); the actual fix is making scope,
     surge, and the stockout premium three additive terms on a shared
     baseline, exactly like M_PEAK_MATERIALS and the stockout premium
     already summed inside one factor for materials — scope now joins that
     same additive group instead of multiplying the total afterward. The
     same reasoning was applied to the surge/stockout pair on materials,
     which had the identical compounding bug between just those two.

M_PEAK_LABOUR, M_PEAK_MATERIALS, and L_PREMIUM are this file's own assumed
magnitudes — moderate, defensible post-disaster surge premiums, NOT sourced
from the report.

Outputs: no persisted state of its own — a pure function module called by
simulator.py per property at evaluation time.
"""

from __future__ import annotations

import sys
from math import exp
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # Repair Model/Data
ROOT_DIR = BASE_DIR.parent.parent                       # OW Task
sys.path.insert(0, str(ROOT_DIR))                        # base_pricing.py

from base_pricing import LABOUR_MATERIAL_SPLIT, sample_volatility  # noqa: E402

# --- Surge intensity curve ---
RAMP_PEAK_DAY = 6
WEIBULL_BETA = 2.2
WEIBULL_ETA = 75.0

# --- Surge magnitude constants (ESTIMATED, not sourced) ---
M_PEAK_LABOUR = 0.50      # +50% labour cost at peak surge intensity
M_PEAK_MATERIALS = 0.30   # +30% materials cost at peak surge intensity
L_PREMIUM = 0.25          # additional +25% logistics premium on materials once stocked out, scaled by surge

# Maps each static cost-breakdown column (properties.csv) to its
# base_pricing.LABOUR_MATERIAL_SPLIT key.
COMPONENT_COST_FIELDS = {
    "switchboard_cost_aud": "switchboard",
    "plasterboard_cost_aud": "plasterboard",
    "flooring_cost_aud": "flooring",
    "kitchen_cabinetry_cost_aud": "kitchen_cabinetry",
    "electrical_cost_aud": "electrical",
    "appliance_cost_aud": "appliances",
    "drying_decon_cost_aud": "drying_decon",
    "painting_cost_aud": "painting",
    "demolition_cost_aud": "demolition",
}


def surge_intensity(t: float) -> float:
    """0 at/before the flood, linear ramp to 1.0 at day RAMP_PEAK_DAY, then
    Weibull survival-function decay."""
    if t <= 0:
        return 0.0
    if t <= RAMP_PEAK_DAY:
        return t / RAMP_PEAK_DAY
    t_prime = t - RAMP_PEAK_DAY
    return exp(-((t_prime / WEIBULL_ETA) ** WEIBULL_BETA))


def compute_baselines(row: dict) -> tuple[float, float]:
    """Labour/material baselines from the property's static cost
    breakdown, split per base_pricing.LABOUR_MATERIAL_SPLIT, then each
    independently scaled by its own volatility draw -> (L_base, M_base)."""
    labour_baseline = 0.0
    material_baseline = 0.0
    for field, component in COMPONENT_COST_FIELDS.items():
        cost = row.get(field) or 0.0
        labour_frac, material_frac = LABOUR_MATERIAL_SPLIT[component]
        labour_baseline += cost * labour_frac
        material_baseline += cost * material_frac

    property_id = row["property_id"]
    L_base = labour_baseline * sample_volatility((property_id, "market_labour"))
    M_base = material_baseline * sample_volatility((property_id, "market_materials"))
    return L_base, M_base


def compute_actual_cost(
    L_base: float,
    M_base: float,
    job_end_day: float,
    stockout_day: int | None,
    scope_multiplier: float,
) -> tuple[float, float, float]:
    """Returns (actual_repair_cost_aud, labour_actual_aud, materials_actual_aud).

    Surge, stockout, and scope are three ADDITIVE uplift fractions on each
    baseline — NOT separate multiplicative factors stacked on top of each
    other (see module docstring for why that compounds independent effects
    instead of summing them)."""
    intensity = surge_intensity(job_end_day)
    stock_empty = 1.0 if (stockout_day is not None and job_end_day >= stockout_day) else 0.0
    scope_frac = scope_multiplier - 1.0

    labour_actual = L_base * (1 + M_PEAK_LABOUR * intensity + scope_frac)
    materials_actual = M_base * (1 + M_PEAK_MATERIALS * intensity + stock_empty * L_PREMIUM * intensity + scope_frac)

    actual_repair_cost = labour_actual + materials_actual
    return actual_repair_cost, labour_actual, materials_actual
