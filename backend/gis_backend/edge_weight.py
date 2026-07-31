"""
Fixed multi-metric edge cost formula (workflow doc, Section 3.2 / Section 9:
"a single edge cost derived from all metrics above via a fixed multi-metric
formula", not user-tunable per request).

Three things are computed per edge, all precomputed once when the graph is
built -- never recomputed by Gemma:
  - weight: the traversal cost used by the routing algorithms (distance,
    slope, road-type difficulty, hazard exposure)
  - time_hours: expected travel time, derated by road type and slope
  - fuel: a placeholder fuel-consumption proxy (flagged in the workflow doc
    as not yet finalized -- swap `compute_edge_fuel` for a better model
    once one is defined)
"""
from .models import RoadEdge

ROAD_TYPE_COST_PENALTY = {"paved": 1.0, "gravel": 1.3, "rough": 1.8}
ROAD_TYPE_SPEED_FACTOR = {"paved": 1.0, "gravel": 0.75, "rough": 0.5}
ROAD_TYPE_FUEL_FACTOR = {"paved": 1.0, "gravel": 1.2, "rough": 1.5}


def compute_edge_weight(
    edge: RoadEdge,
    w_distance: float = 1.0,
    w_slope: float = 0.05,
    w_hazard: float = 8.0,
    w_roadtype: float = 1.0,
) -> float:
    base = edge.length_km * w_distance
    slope_penalty = edge.slope_pct * w_slope * edge.length_km / 10
    road_penalty = (ROAD_TYPE_COST_PENALTY[edge.road_type] - 1.0) * edge.length_km * w_roadtype
    hazard_penalty = edge.hazard_score * w_hazard
    return round(base + slope_penalty + road_penalty + hazard_penalty, 3)


def compute_edge_time_hours(edge: RoadEdge) -> float:
    speed = edge.max_speed_kmph
    speed *= ROAD_TYPE_SPEED_FACTOR[edge.road_type]
    speed *= max(0.4, 1 - edge.slope_pct / 40)
    speed = max(speed, 5)  # floor so a degraded road never implies zero speed
    return round(edge.length_km / speed, 3)


def compute_edge_fuel(edge: RoadEdge) -> float:
    """Placeholder proxy -- see workflow doc Section 9: formula not finalized."""
    slope_factor = 1 + edge.slope_pct / 20
    road_factor = ROAD_TYPE_FUEL_FACTOR[edge.road_type]
    return round(edge.length_km * slope_factor * road_factor, 2)
