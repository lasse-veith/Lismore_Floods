"""export_dashboard_data.py — computes every real number the dashboard shows
from the pipeline's own Output/ CSVs and writes them to dashboard-data.js as
`window.OW_DATA`. Re-run this any time upstream data changes; every section
HTML file reads from the resulting file instead of hardcoding numbers.

No fabricated statistics: every figure here is either read directly from a
CSV or derived from documented model constants (base_pricing.py,
Repair Model/Data/*.py). Where an exact decomposition wasn't available
(e.g. splitting the actual/static gap precisely into surge vs stockout
dollars), a clearly-labelled proxy metric is used instead of a fabricated
dollar split — see the `mechanism` block below.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # OW Task root
OUTPUT_DIR = BASE_DIR / "Output"
OUT_JS = Path(__file__).resolve().parent / "dashboard-data.js"
RIVER_COORD_TXT = BASE_DIR / "UI Design" / "river_coord.txt"

import sys

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "Repair Model" / "Data"))
import Market_price  # noqa: E402 — reuse the project's own baseline/surge formulas exactly


def main() -> None:
    props = pd.read_csv(OUTPUT_DIR / "properties.csv")
    affected = props[props["initial_estimated_cost_aud"] > 0].copy()

    total_properties = int(len(props))
    affected_count = int(len(affected))
    static_total = float(affected["initial_estimated_cost_aud"].sum())
    actual_total = float(affected["actual_repair_cost_aud"].sum())
    gap_pct = (actual_total / static_total - 1.0) * 100.0

    # --- Section 3b: component trigger-count distribution ---
    comp_cols = [
        "switchboard_cost_aud", "plasterboard_cost_aud", "flooring_cost_aud",
        "kitchen_cabinetry_cost_aud", "electrical_cost_aud", "appliance_cost_aud",
        "drying_decon_cost_aud", "painting_cost_aud", "demolition_cost_aud",
    ]
    trig_counts = (affected[comp_cols].fillna(0) > 0).sum(axis=1)
    trigger_distribution = trig_counts.value_counts().sort_index()
    trigger_distribution = {int(k): int(v) for k, v in trigger_distribution.items()}

    # --- Section 3b widget: representative (median, among triggered) cost per component —
    # a real computed figure from the portfolio, not a single cherry-picked property or an
    # invented "unit rate" — used by the depth-cost simulator's component cards.
    comp_label = {
        "flooring_cost_aud": "Flooring", "drying_decon_cost_aud": "Drying / Decon",
        "demolition_cost_aud": "Demolition", "plasterboard_cost_aud": "Plasterboard",
        "painting_cost_aud": "Painting", "kitchen_cabinetry_cost_aud": "Kitchen Cabinetry",
        "appliance_cost_aud": "Appliances", "switchboard_cost_aud": "Switchboard",
        "electrical_cost_aud": "Electrical",
    }
    comp_tier = {
        "flooring_cost_aud": 0, "drying_decon_cost_aud": 0, "demolition_cost_aud": 0,
        "plasterboard_cost_aud": 1, "painting_cost_aud": 1, "kitchen_cabinetry_cost_aud": 1, "appliance_cost_aud": 1,
        "switchboard_cost_aud": 2, "electrical_cost_aud": 2,
    }
    component_medians = []
    for col, label in comp_label.items():
        triggered_vals = affected.loc[affected[col].fillna(0) > 0, col]
        component_medians.append({
            "key": col, "label": label, "tier": comp_tier[col],
            "medianAud": round(float(triggered_vals.median()), 2) if len(triggered_vals) else 0.0,
        })

    # --- Static Model page widget: mean cost per component ACROSS ALL affected
    # properties (zeros included for non-triggered ones), not just the
    # triggered subset. This is the figure the depth-cost simulator widget
    # uses, because summing means-across-everyone is mathematically exactly
    # the portfolio's average cost per impacted house (sum of per-property
    # totals / count == sum over components of that component's mean) — so
    # the widget's running total at max depth lands exactly on the real
    # average cost per house shown elsewhere on this page.
    component_means = []
    for col, label in comp_label.items():
        component_means.append({
            "key": col, "label": label, "tier": comp_tier[col],
            "meanAud": round(float(affected[col].fillna(0).mean()), 2),
        })

    # --- Static Model page: cost distribution by component — these 9 real
    # column sums add up to static_total exactly (verified: they're its own
    # components), used for the bottom-right bar chart.
    component_totals = []
    for col, label in comp_label.items():
        component_totals.append({
            "key": col, "label": label, "tier": comp_tier[col],
            "totalAud": round(float(affected[col].fillna(0).sum()), 2),
        })

    # --- Static Model page: distance-from-river histogram (total vs flooded) ---
    # The river's own path is real (digitized from the actual Wilsons River
    # centreline, UI Design/river_coord.txt, a KML <coordinates> lon,lat,alt
    # list) — approximated as a piecewise-linear polyline through those
    # points, and every property's distance is the true minimum
    # point-to-segment distance to that polyline (not just distance to the
    # nearest vertex), converted from degrees to metres via an
    # equirectangular projection local to this small an area (a few km
    # across), which is accurate enough at this scale.
    coords_text = re.search(r"<coordinates>(.*?)</coordinates>", RIVER_COORD_TXT.read_text(), re.S).group(1)
    river_lonlat = np.array([[float(x) for x in tok.split(",")[:2]] for tok in coords_text.split()])

    lat0 = float(river_lonlat[:, 1].mean())
    lon0 = float(river_lonlat[:, 0].mean())
    M_PER_DEG_LAT = 110_540.0
    M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(lat0))

    def to_xy(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (lon - lon0) * M_PER_DEG_LON, (lat - lat0) * M_PER_DEG_LAT

    river_x, river_y = to_xy(river_lonlat[:, 0], river_lonlat[:, 1])
    prop_x, prop_y = to_xy(props["longitude"].to_numpy(), props["latitude"].to_numpy())

    min_dist_m = np.full(len(props), np.inf)
    for i in range(len(river_x) - 1):
        ax, ay, bx, by = river_x[i], river_y[i], river_x[i + 1], river_y[i + 1]
        abx, aby = bx - ax, by - ay
        ab_len2 = abx ** 2 + aby ** 2
        t = np.clip(((prop_x - ax) * abx + (prop_y - ay) * aby) / ab_len2, 0.0, 1.0)
        cx, cy = ax + t * abx, ay + t * aby
        seg_dist = np.hypot(prop_x - cx, prop_y - cy)
        min_dist_m = np.minimum(min_dist_m, seg_dist)

    props_dist = props.copy()
    props_dist["dist_to_river_m"] = min_dist_m
    is_flooded = (props_dist["initial_estimated_cost_aud"].fillna(0) > 0)

    BUCKET_SIZE_M = 300
    N_BUCKETS = 10  # 0-3000m in 300m steps, plus a "3000m+" overflow bucket
    bucket_idx = np.minimum((min_dist_m // BUCKET_SIZE_M).astype(int), N_BUCKETS)
    bucket_labels = [f"{i * BUCKET_SIZE_M}-{(i + 1) * BUCKET_SIZE_M}m" for i in range(N_BUCKETS)] + [f"{N_BUCKETS * BUCKET_SIZE_M}m+"]

    total_by_bucket = np.bincount(bucket_idx, minlength=N_BUCKETS + 1)
    flooded_by_bucket = np.bincount(bucket_idx[is_flooded.to_numpy()], minlength=N_BUCKETS + 1)

    river_distance_histogram = {
        "bucketLabels": bucket_labels,
        "totalHouses": [int(v) for v in total_by_bucket],
        "floodedHouses": [int(v) for v in flooded_by_bucket],
    }

    # --- Section 5: mechanism attribution (real, computable proxies) ---
    max_scope_pct = float((affected["scope_multiplier"] >= 1.399).mean() * 100.0)
    stockout_pct = float((affected["stockout_day_relevant"] == True).mean() * 100.0)  # noqa: E712

    # mean surge intensity at job completion, using Market_price.py's own
    # analytical surge_intensity(t) formula (real constants, re-evaluated here)
    RAMP_PEAK_DAY, WEIBULL_BETA, WEIBULL_ETA = 6, 2.2, 75.0

    def surge_intensity(t: float) -> float:
        if t <= 0:
            return 0.0
        if t <= RAMP_PEAK_DAY:
            return t / RAMP_PEAK_DAY
        tp = t - RAMP_PEAK_DAY
        return math.exp(-((tp / WEIBULL_ETA) ** WEIBULL_BETA))

    mean_surge_at_completion = float(
        affected["job_end_day"].dropna().apply(surge_intensity).mean() * 100.0
    )

    scope_dollar_approx = float((affected["initial_estimated_cost_aud"] * (affected["scope_multiplier"] - 1)).sum())

    # --- Section 4 donut: exact additive mechanism attribution (surge / scope / stockout) ---
    # Same formula as Repair Model/Data/simulator.py's compute_mechanism_contributions,
    # re-evaluated here from properties.csv so the dashboard never drifts from the pipeline's
    # own numbers. stockout_day is recovered from market_timeline.csv (first stocked-out day).
    mt = pd.read_csv(OUTPUT_DIR / "market_timeline.csv")
    stocked_out_days = mt.loc[mt["is_stocked_out"] == True, "day"]  # noqa: E712
    stockout_day = int(stocked_out_days.min()) if not stocked_out_days.empty else None

    total_baseline = 0.0
    scope_contribution = 0.0
    surge_contribution = 0.0
    stockout_contribution = 0.0
    for row in affected.to_dict("records"):
        L_base, M_base = Market_price.compute_baselines(row)
        t = row["job_end_day"]
        intensity = Market_price.surge_intensity(t)
        stock_empty = 1.0 if (stockout_day is not None and t >= stockout_day) else 0.0
        scope_frac = row["scope_multiplier"] - 1.0
        total_baseline += L_base + M_base
        scope_contribution += (L_base + M_base) * scope_frac
        surge_contribution += L_base * Market_price.M_PEAK_LABOUR * intensity + M_base * Market_price.M_PEAK_MATERIALS * intensity
        stockout_contribution += M_base * stock_empty * Market_price.L_PREMIUM * intensity

    total_uplift = scope_contribution + surge_contribution + stockout_contribution
    mechanism_attribution = {
        "totalUpliftAud": round(total_uplift, 2),
        "totalBaselineAud": round(total_baseline, 2),
        "scope": {"aud": round(scope_contribution, 2), "pct": round(scope_contribution / total_uplift * 100.0, 1)},
        "surge": {"aud": round(surge_contribution, 2), "pct": round(surge_contribution / total_uplift * 100.0, 1)},
        "stockout": {"aud": round(stockout_contribution, 2), "pct": round(stockout_contribution / total_uplift * 100.0, 1)},
    }

    # --- Section 4: real stock-depletion timeline (downsampled every 7 days) ---
    mt_sample = mt[mt["day"] % 7 == 0]
    stock_timeline = {
        "days": mt_sample["day"].tolist(),
        "stockRemaining": mt_sample["stock_remaining"].tolist(),
    }

    # --- Section 6: credibility convergence ---
    cpt = pd.read_csv(OUTPUT_DIR / "credibility_portfolio_timeline.csv")
    final_actual = float(cpt["cumulative_actual_realized"].iloc[-1])
    conv_days = cpt["day"].tolist()
    static_error_pct = float((static_total - final_actual) / final_actual * 100.0)
    credibility_error_pct = [
        float((v - final_actual) / final_actual * 100.0) for v in cpt["cumulative_credibility_estimate"]
    ]

    # --- Section 6: three-line cost-over-time (static / actual / credibility), resampled every 7 days ---
    static_ts = pd.read_csv(OUTPUT_DIR / "static_cost_timeline.csv")
    # actual cumulative from repair_status.csv, same construction as price_over_time.py's compute_actual_cumulative
    rs = pd.read_csv(OUTPUT_DIR / "repair_status.csv")
    completed = rs[rs["repair_status"] == "completed"].dropna(subset=["actual_repair_cost_aud"])
    max_day = int(max(static_ts["day"].max(), cpt["day"].max()))
    by_day_actual = completed.groupby("day")["actual_repair_cost_aud"].sum()
    days_7 = sorted(set(range(0, max_day + 1, 7)) | {max_day})

    actual_cum_by_day = {}
    running = 0.0
    day_ptr = sorted(by_day_actual.index)
    idx = 0
    for d in range(0, max_day + 1):
        while idx < len(day_ptr) and day_ptr[idx] <= d:
            running += float(by_day_actual.loc[day_ptr[idx]])
            idx += 1
        actual_cum_by_day[d] = running

    static_by_day = dict(zip(static_ts["day"], static_ts["cumulative_static_spend_aud"]))
    cred_by_day = dict(zip(cpt["day"], cpt["cumulative_credibility_estimate"]))

    def nearest_le(d_map: dict, d: int):
        keys = [k for k in d_map if k <= d]
        return d_map[max(keys)] if keys else 0.0

    three_line = {
        "days": days_7,
        "static": [round(nearest_le(static_by_day, d), 2) for d in days_7],
        "actual": [round(actual_cum_by_day.get(d, actual_cum_by_day[max(k for k in actual_cum_by_day if k <= d)]), 2) for d in days_7],
        "credibility": [round(nearest_le(cred_by_day, d), 2) for d in days_7],
    }

    # --- Section 6 (bottom chart): remaining repair cost over time, static / actual / credibility,
    # all converging to $0 as repairs complete — same definitions as cost_comparison.py's
    # compute_remaining_series, resampled onto the same days_7 axis as three_line above.
    static_final = nearest_le(static_by_day, max_day)
    actual_final = actual_cum_by_day[max_day]
    remaining = {
        "days": days_7,
        "static": [round(static_final - nearest_le(static_by_day, d), 2) for d in days_7],
        "actual": [round(actual_final - actual_cum_by_day[d], 2) for d in days_7],
        "credibility": [round(nearest_le(cred_by_day, d) - actual_cum_by_day[d], 2) for d in days_7],
    }

    # --- data schema real/synthetic field split (steps.md documented, counted) ---
    real_fields = [
        "property_id", "address", "suburb", "postcode", "latitude", "longitude",
        "ground_elevation_m_ahd", "building_footprint", "dwelling_structure_census",
        "peak_water_level_m_ahd",
    ]
    synthetic_fields = [c for c in props.columns if c not in real_fields]

    data = {
        "hero": {
            "totalProperties": total_properties,
            "affectedProperties": affected_count,
            "staticTotalAud": round(static_total, 2),
            "actualTotalAud": round(actual_total, 2),
            "gapPct": round(gap_pct, 1),
            "gapAud": round(actual_total - static_total, 2),
        },
        "flood": {
            "peakLevelM": 14.4,
            "peakLabel": "28 Feb 2022, ~3pm",
            "leveeOvertopLabel": "3am, 28 Feb 2022",
        },
        "costEngine": {
            "triggerDistribution": trigger_distribution,
            "affectedProperties": affected_count,
            "componentThresholds": [
                {"tier": "Always (any depth above floor)", "components": ["Flooring", "Drying / decontamination", "Demolition"]},
                {"tier": "> 0.05 m — past the baseboard", "components": ["Plasterboard", "Painting", "Kitchen cabinetry", "Appliances"]},
                {"tier": "> 0.25 m — plug/outlet height", "components": ["Switchboard", "Electrical"]},
            ],
            "componentMedians": component_medians,
            "componentMeans": component_means,
            "componentTotals": component_totals,
        },
        "riverDistance": river_distance_histogram,
        "repairModel": {
            "insuranceDelay": {"meanDays": 5.0, "sigma": 0.45},
            "queueRollout": {"betaShape": 2.0, "etaScaleDays": 230},
            "jobStages": [
                "Drying / decon + demolition", "Electrical + switchboard", "Plasterboard",
                "Painting", "Flooring", "Kitchen cabinetry + appliances",
            ],
            "scopeGrowth": {"thresholdDays": 18, "ratePerDay": 0.02, "maxIncrease": 0.40},
            "surge": {
                "rampPeakDay": 6, "weibullBeta": 2.2, "weibullEtaDays": 75.0,
                "peakLabourUplift": 0.50, "peakMaterialsUplift": 0.30, "logisticsPremium": 0.25,
            },
            "stockout": {"totalStockJobEquivalents": 420, "stockoutDay": stockout_day},
            "stockTimeline": stock_timeline,
        },
        "mechanism": {
            "maxScopeCapPct": round(max_scope_pct, 1),
            "stockoutAffectedPct": round(stockout_pct, 1),
            "meanSurgeAtCompletionPct": round(mean_surge_at_completion, 1),
            "scopeDollarApprox": round(scope_dollar_approx, 2),
            "attribution": mechanism_attribution,
        },
        "convergence": {
            "days": conv_days,
            "staticErrorPct": round(static_error_pct, 1),
            "credibilityErrorPct": [round(v, 2) for v in credibility_error_pct],
        },
        "threeLine": three_line,
        "remaining": remaining,
        "dataSchema": {
            "realFieldCount": len(real_fields),
            "syntheticFieldCount": len(synthetic_fields),
        },
    }

    OUT_JS.write_text("window.OW_DATA = " + json.dumps(data, indent=2) + ";\n")
    print(f"Wrote {OUT_JS} ({OUT_JS.stat().st_size / 1024:.1f} KB)")
    print(f"static_total={static_total:,.0f} actual_total={actual_total:,.0f} gap={gap_pct:.1f}%")
    print(f"affected={affected_count:,} of {total_properties:,}")
    print(f"max_scope_cap_pct={max_scope_pct:.1f}% stockout_pct={stockout_pct:.1f}% mean_surge_at_completion={mean_surge_at_completion:.1f}%")
    print(f"mechanism attribution: scope={mechanism_attribution['scope']['pct']}% "
          f"surge={mechanism_attribution['surge']['pct']}% stockout={mechanism_attribution['stockout']['pct']}% "
          f"(total uplift ${mechanism_attribution['totalUpliftAud']:,.0f}, stockout_day={stockout_day})")


if __name__ == "__main__":
    main()
