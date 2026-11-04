"""
Edge cost, travel-time and fuel models.

Everything here is a transparent engineering model, not a black box. Constants
are collected at the top so an operator can replace them with fleet telemetry.

Routing cost ("weight", km-equivalents)
    weight = length * (1 + surface_penalty + grade_penalty) + hazard_penalty
    * surface_penalty  : paved 0, gravel 0.3, rough 0.8
    * grade_penalty    : 0.05 per 10 % of mean absolute grade   (so 10 % -> +5 %)
    * hazard_penalty   : hazard * length * HAZARD_KM_PER_KM
    Hazard scales with *length* because exposure accumulates with distance
    travelled through a risky stretch (the prototype added a constant per edge,
    which made a 40 km and a 3 km road equally "risky").

Travel time
    speed = max_speed * surface_factor * grade_factor, floored at 5 km/h.

Fuel (litres, direction-aware)
    fuel = length * BASE_L_PER_KM * surface_fuel_factor
         + ascent * LOADED_MASS * g / (ENGINE_EFFICIENCY * DIESEL_ENERGY)
    The second term is the potential energy gained climbing, converted at a
    realistic engine efficiency; descending is treated as free (no regeneration).
    BASE_L_PER_KM is an assumption for a loaded ~8 t light truck on flat paved
    road and should be calibrated against real fleet fuel logs.
"""
from __future__ import annotations

from typing import Optional

from .models import RoadEdge

SURFACE_COST_PENALTY = {"paved": 0.0, "gravel": 0.3, "rough": 0.8}
SURFACE_SPEED_FACTOR = {"paved": 1.0, "gravel": 0.75, "rough": 0.5}
SURFACE_FUEL_FACTOR = {"paved": 1.0, "gravel": 1.2, "rough": 1.5}

GRADE_COST_PER_PCT = 0.005     # per percent of mean absolute grade
HAZARD_KM_PER_KM = 1.5         # extra km-equivalents per km at hazard score 1.0
RAIN_HAZARD_GAIN = 1.5         # hazard multiplier gain at saturated rainfall index

BASE_L_PER_KM = 0.30           # loaded light truck, flat paved road (assumption)
LOADED_MASS_KG = 8000.0
ENGINE_EFFICIENCY = 0.30
DIESEL_MJ_PER_L = 36.0
G = 9.81
MIN_SPEED_KMPH = 5.0


def effective_hazard(edge: RoadEdge, rain_index: float = 0.0) -> float:
    """Static hazard (terrain + incidents) amplified by recent/forecast rainfall."""
    if edge.hazard_incident > 0:
        static = 0.6 * edge.hazard_terrain + 0.4 * edge.hazard_incident
    else:
        static = edge.hazard_terrain
    return min(1.0, static * (1.0 + RAIN_HAZARD_GAIN * max(0.0, min(1.0, rain_index))))


def compute_edge_weight(edge: RoadEdge, rain_index: float = 0.0) -> float:
    length = edge.length_km
    base = length * (1.0 + SURFACE_COST_PENALTY[edge.road_type] + GRADE_COST_PER_PCT * edge.slope_pct)
    hazard = effective_hazard(edge, rain_index) * length * HAZARD_KM_PER_KM
    return round(base + hazard, 5)


def compute_edge_time_hours(edge: RoadEdge) -> float:
    speed = edge.max_speed_kmph * SURFACE_SPEED_FACTOR[edge.road_type]
    speed *= max(0.4, 1 - edge.slope_pct / 40)
    speed = max(speed, MIN_SPEED_KMPH)
    return round(edge.length_km / speed, 5)


def fuel_litres(edge: RoadEdge, forward: bool = True) -> float:
    ascent = edge.ascent_ab_m if forward else edge.descent_ab_m
    rolling = edge.length_km * BASE_L_PER_KM * SURFACE_FUEL_FACTOR[edge.road_type]
    climb = ascent * LOADED_MASS_KG * G / (ENGINE_EFFICIENCY * DIESEL_MJ_PER_L * 1e6)
    return round(rolling + climb, 5)


def rain_index_from_mm(precip_mm: Optional[float], saturation_mm: float = 200.0) -> float:
    """Map accumulated precipitation (past 72 h + next 24 h) to a 0..1 index."""
    if precip_mm is None or precip_mm <= 0:
        return 0.0
    return min(1.0, precip_mm / saturation_mm)
