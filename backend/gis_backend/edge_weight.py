"""
Edge cost, travel time and fuel for every road piece.

Travel time and fuel come from the physics in ``truck_model`` (grade, curvature, surface, direction). The routing
cost ("weight", km-equivalents) stays a transparent formula:

    weight = length * (1 + E[surface penalty] + 0.005 * mean_abs_grade_percent) + hazard * length * 1.5

Hazard scales with length because exposure grows with distance travelled through a risky stretch. Where a road's
surface is inferred, expectations use the class prior (see ``surface_prior``) instead of a single guess.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from . import truck_model as tm
from .models import RoadEdge

SURFACE_COST_PENALTY = np.array([0.0, 0.3, 0.8])   # paved, gravel, rough
GRADE_COST_PER_PCT = 0.005
HAZARD_KM_PER_KM = 1.5
RAIN_HAZARD_GAIN = 1.5

# Rainfall threshold for landslide initiation in the Nepal Himalaya (Dahal and Hasegawa, 2008, Geomorphology 100:429):
#   I = 73.9 * D^-0.79   with I in mm/h and D in hours   ->   accumulated depth  P = 73.9 * D^0.21 mm
# (about 144 mm in 24 h, 181 mm in 72 h). The rain index is the largest ratio of observed accumulation to this
# threshold over 24, 48 and 72 h windows, capped at 1.
RAIN_THRESHOLD_A = 73.9
RAIN_THRESHOLD_B = 0.21
RAIN_WINDOWS_H = (24, 48, 72)


def rain_threshold_mm(duration_h: float) -> float:
    return RAIN_THRESHOLD_A * duration_h ** RAIN_THRESHOLD_B


def rain_index_from_hourly(hourly_mm: List[float]) -> float:
    """Max over windows of (worst rolling accumulation / landslide threshold), in [0, 1]."""
    vals = np.nan_to_num(np.asarray(hourly_mm, dtype=float))
    best = 0.0
    for d in RAIN_WINDOWS_H:
        if len(vals) < d:
            continue
        csum = np.concatenate([[0.0], np.cumsum(vals)])
        worst = float((csum[d:] - csum[:-d]).max())
        best = max(best, worst / rain_threshold_mm(d))
    return min(1.0, best)


def effective_hazard(edge: RoadEdge, rain_index: float = 0.0) -> float:
    """Static hazard (terrain + incidents) amplified by rainfall relative to the landslide threshold."""
    static = (0.6 * edge.hazard_terrain + 0.4 * edge.hazard_incident) if edge.hazard_incident > 0 else edge.hazard_terrain
    return min(1.0, static * (1.0 + RAIN_HAZARD_GAIN * max(0.0, min(1.0, rain_index))))


def surface_mix(edges: List[RoadEdge]) -> np.ndarray:
    out = np.zeros((len(edges), 3))
    idx = {"paved": 0, "gravel": 1, "rough": 2}
    for i, e in enumerate(edges):
        if e.surface_mix:
            out[i] = e.surface_mix
        else:
            out[i, idx[e.road_type]] = 1.0
    return out


def compute_arrays(edges: List[RoadEdge], truck: tm.TruckParams = tm.DEFAULT_TRUCK, power_w=None,
                   speed_scale: float = 1.0) -> Dict[str, np.ndarray]:
    """Per-piece, per-direction travel time and fuel (a -> b and b -> a), plus the static part of the routing cost."""
    n = len(edges)
    length = np.array([e.length_km for e in edges])
    mix = surface_mix(edges)
    free = np.array([e.max_speed_kmph for e in edges])
    curv = np.array([e.curvature_deg_per_km for e in edges])
    rise_ab = np.array([e.ascent_ab_m - e.descent_ab_m for e in edges])
    grade_ab = rise_ab / np.maximum(length * 1000.0, 1.0)
    out: Dict[str, np.ndarray] = {"length": length, "mix": mix}
    for tag, grade, rise in (("ab", grade_ab, rise_ab), ("ba", -grade_ab, -rise_ab)):
        speed = tm.piece_speed_kmph(free, curv, grade, truck, power_w, speed_scale)      # (n, 3)
        out[f"time_{tag}"] = tm.expected_time_hours(length, speed, mix)
        out[f"fuel_{tag}"] = tm.fuel_litres(length, speed, rise, mix, truck)
        out[f"vavg_{tag}"] = length / np.maximum(out[f"time_{tag}"], 1e-9)
    slope = np.array([e.slope_pct for e in edges])
    out["base_cost"] = length * (1.0 + mix @ SURFACE_COST_PENALTY + GRADE_COST_PER_PCT * slope)
    return out


def hazard_array(edges: List[RoadEdge], rain: np.ndarray) -> np.ndarray:
    return np.array([effective_hazard(e, r) for e, r in zip(edges, rain)])


def heading_change_deg_per_km(geometry, length_km: float) -> float:
    """Cumulative absolute heading change along a polyline, per km."""
    if len(geometry) < 3 or length_km <= 0:
        return 0.0
    lat0 = math.radians(geometry[0][0])
    pts = [((p[1]) * math.cos(lat0), p[0]) for p in geometry]
    total, prev = 0.0, None
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 == x2 and y1 == y2:
            continue
        h = math.atan2(y2 - y1, x2 - x1)
        if prev is not None:
            d = abs((h - prev + math.pi) % (2 * math.pi) - math.pi)
            total += d
        prev = h
    return math.degrees(total) / length_km


# ----------------------------------------------------------- scalar helpers (tests, notebooks)
def compute_edge_weight(edge: RoadEdge, rain_index: float = 0.0) -> float:
    a = compute_arrays([edge])
    return float(a["base_cost"][0] + effective_hazard(edge, rain_index) * edge.length_km * HAZARD_KM_PER_KM)


def compute_edge_time_hours(edge: RoadEdge, forward: bool = True) -> float:
    return float(compute_arrays([edge])["time_ab" if forward else "time_ba"][0])


def fuel_litres(edge: RoadEdge, forward: bool = True) -> float:
    return float(compute_arrays([edge])["fuel_ab" if forward else "fuel_ba"][0])
