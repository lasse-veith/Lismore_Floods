from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional


class PropertyRecord(BaseModel):
    """One row per real-world property. Later data is appended to this :
    FloodExposureRecord and the future CostEstimateRecord, which are indexed
    by interval_hour, since a single property has many cost estimates over time.)
    """

    # --- Stage 1: identity + location (G-NAF, real) ---
    property_id: str                        # canonical ID, generated once, reused everywhere
    address: str
    suburb: str
    postcode: Optional[str] = None
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)

    # --- Stage 2: elevation (DEM, real) ---
    ground_elevation_m_ahd: Optional[float] = None

    # --- Stage 3: footprint (OSM, real where available) ---
    building_footprint: Optional[list[tuple[float, float]]] = None  # [(lon, lat), ...] polygon ring
    building_area_m2: Optional[float] = None

    # --- Stage 4a: synthetic attributes, sampled from 2021 ABS Census SAL/LGA
    # distributions (see census_distributions.py / apply_synthetic_attributes.py).
    # dwelling_structure_census is the ABS building-type taxonomy (separate
    # house / semi-detached / flat or apartment / other) — a different axis
    # from construction_type below, which is the flood-report's floor
    # construction/stump-height taxonomy. A property has both, independently.
    dwelling_structure_census: Optional[Literal["separate_house", "semi_detached", "flat_or_apartment", "other"]] = None
    bedroom_count: Optional[int] = Field(default=None, ge=0)
    vehicle_count: Optional[int] = Field(default=None, ge=0)
    ev_count: Optional[int] = Field(default=None, ge=0)  # how many of vehicle_count are EVs (see EV_append.py)
    # canonical per-suburb affluence score (0-1, min-max scaled house-price table) —
    # every script needing an affluence rating reads THIS field, not its own proxy.
    # See derive_affluence_and_construction_type.py.
    affluence_score: Optional[float] = Field(default=None, ge=0, le=1)

    # --- Stage 4b: synthetic attributes (flood-report methodology) ---
    construction_type: Optional[Literal["slab_on_ground", "short_stumps", "high_stumps"]] = None
    floor_height_offset_m: Optional[float] = None   # 0.15 / 0.5 / 1.5 per report methodology
    switchboard_type: Optional[Literal["ceramic_fuse", "circuit_breaker_basic", "circuit_breaker_rcd", "smart_ev_ready"]] = None
    circuit_count: Optional[int] = None
    kitchen_spec: Optional[Literal["basic_laminate", "standard_laminate", "stone_benchtop", "premium_stone_island"]] = None
    flooring: Optional[Literal["carpet", "vinyl", "timber_floorboards", "tile", "laminate", "polished_concrete"]] = None
    building_age_years: Optional[int] = None
    flood_planning_level_m_ahd: Optional[float] = None  # constant, real council DCP figure — comparison only

    # --- Stage 4c: flood exposure summary (bathtub-fill model; see
    # flood_hydrograph.py / flood_exposure.py) — per-property peaks across
    # every interval_hour, computed once and reused rather than re-derived
    # from flood_exposure.csv by every downstream script ---
    peak_water_level_m_ahd: Optional[float] = None
    peak_depth_above_floor_m: Optional[float] = None

    # --- Stage 5: static cost engine output (real formula, synthetic unit
    # costs) — see static_pricing.py for the full breakdown logic ---
    depth_above_floor_m: Optional[float] = None  # equals peak_depth_above_floor_m by construction; kept as an explicit costing input
    wetted_wall_area_m2: Optional[float] = None
    severity_factor: Optional[float] = None
    switchboard_cost_aud: Optional[float] = None
    plasterboard_cost_aud: Optional[float] = None
    flooring_cost_aud: Optional[float] = None
    kitchen_cabinetry_cost_aud: Optional[float] = None
    electrical_cost_aud: Optional[float] = None
    appliance_cost_aud: Optional[float] = None
    drying_decon_cost_aud: Optional[float] = None
    painting_cost_aud: Optional[float] = None
    demolition_cost_aud: Optional[float] = None
    initial_estimated_cost_aud: Optional[float] = None

    # --- Stage 6: repair simulation output (Repair Model/Data/simulator.py) —
    # timeline + market-volatility-adjusted actual cost, null for properties
    # that never flooded above floor (initial_estimated_cost_aud == 0) ---
    insurance_delay_days: Optional[float] = None
    job_start_day: Optional[int] = None
    job_duration_days: Optional[int] = None
    job_end_day: Optional[int] = None
    scope_multiplier: Optional[float] = None
    stockout_day_relevant: Optional[bool] = None  # did this property's job finish after the townwide stockout?
    labour_actual_aud: Optional[float] = None
    materials_actual_aud: Optional[float] = None
    actual_repair_cost_aud: Optional[float] = None

    @field_validator("ground_elevation_m_ahd")
    @classmethod
    def sanity_check_elevation(cls, v):
        if v is not None and not (-5 <= v <= 250):
            raise ValueError(f"Elevation {v} m AHD is outside plausible range for Lismore")
        return v

    @model_validator(mode="after")
    def sanity_check_ev_count(self):
        if self.ev_count is not None and self.vehicle_count is not None and self.ev_count > self.vehicle_count:
            raise ValueError(f"ev_count ({self.ev_count}) cannot exceed vehicle_count ({self.vehicle_count})")
        return self

    @property
    def floor_elevation_m_ahd(self) -> Optional[float]:
        if self.ground_elevation_m_ahd is None or self.floor_height_offset_m is None:
            return None
        return self.ground_elevation_m_ahd + self.floor_height_offset_m


class FloodExposureRecord(BaseModel):
    """One row per property PER 12h interval. Time-series only — this is where
    dynamic values live: flood state now, and later the credibility-updated
    cost estimate and actual repair cost/status per interval."""

    property_id: str                        # foreign key back to PropertyRecord
    interval_hour: int                      # hours since event start (t=0 = first minor-level exceedance)
    water_level_m_ahd: float
    is_flooded: bool
    depth_above_floor_m: Optional[float] = None

    # future stages, added when the repair sim / credibility model are built:
    is_repaired: Optional[bool] = None
    actual_repair_cost_aud: Optional[float] = None
    credibility_updated_estimate_aud: Optional[float] = None