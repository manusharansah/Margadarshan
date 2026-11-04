"""
Monte Carlo ETA uncertainty.

Travel times rest on assumed speeds, engine power and traffic. Rather than show one falsely precise number, each
plan's route is re-timed under random draws of those assumptions and the spread is reported as an ETA range and a
chance of missing the deadline.

Assumed uncertainty (replace with measurements when you have fleet telemetry):
  * free-flow speed scale   lognormal, sigma 0.20, clipped to 0.6-1.4  (class speeds are planning defaults)
  * engine power            uniform +/-20 %                           (weight-to-power is a design value)
  * congestion              uniform 1.00-1.30 on driving time          (live traffic is not modelled)

All legs of one plan share each draw, so parallel legs stay correlated: plan time is the slowest leg (parallel
plans) or the sum of trips (one-vehicle multi-stop). The ranges describe model-parameter uncertainty. They are not
validated against observed travel times.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import networkx as nx
import numpy as np

from . import truck_model as tm
from .edge_weight import surface_mix
from .models import CandidatePlan

SPEED_SIGMA = 0.20
POWER_RANGE = (0.8, 1.2)
CONGESTION_RANGE = (1.0, 1.3)


def draw_parameters(n: int, seed: int = 7) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        "speed_scale": np.clip(rng.lognormal(-SPEED_SIGMA ** 2 / 2, SPEED_SIGMA, n), 0.6, 1.4),
        "power_scale": rng.uniform(*POWER_RANGE, n),
        "congestion": rng.uniform(*CONGESTION_RANGE, n),
    }


def leg_drive_time_samples(G: nx.DiGraph, path: List[int], draws: Dict[str, np.ndarray],
                           truck: tm.TruckParams = tm.DEFAULT_TRUCK) -> np.ndarray:
    """Driving hours along ``path`` for each draw (shape (n,))."""
    n = len(draws["speed_scale"])
    arcs = [G[u][v] for u, v in zip(path, path[1:])]
    if not arcs:
        return np.zeros(n)
    edges = [a["edge"] for a in arcs]
    forward = np.array([u == e.node_a for (u, _), e in zip(zip(path, path[1:]), edges)])
    length = np.array([e.length_km for e in edges])
    free = np.array([e.max_speed_kmph for e in edges])
    curv = np.array([e.curvature_deg_per_km for e in edges])
    rise = np.array([e.ascent_ab_m - e.descent_ab_m for e in edges]) * np.where(forward, 1.0, -1.0)
    grade = rise / np.maximum(length * 1000.0, 1.0)
    mix = surface_mix(edges)
    out = np.empty(n)
    for i in range(n):
        speed = tm.piece_speed_kmph(free, curv, grade, truck, truck.power_w * draws["power_scale"][i],
                                    draws["speed_scale"][i])
        out[i] = tm.expected_time_hours(length, speed, mix).sum() * draws["congestion"][i]
    return out


def plan_eta_samples(G: nx.DiGraph, plan: CandidatePlan, draws: Dict[str, np.ndarray]) -> np.ndarray:
    """Whole-plan arrival time per draw. Each leg's stated time is scaled by its simulated/deterministic ratio, so
    loading time and return legs are scaled with the driving time (a simplification)."""
    leg_times = []
    for leg in plan.legs:
        det = sum(G[u][v]["time"] for u, v in zip(leg.path, leg.path[1:]))
        ratio = leg_drive_time_samples(G, leg.path, draws) / det if det > 0 else np.ones_like(draws["speed_scale"])
        leg_times.append(leg.time_hours * ratio)
    stack = np.vstack(leg_times)
    return stack.sum(axis=0) if plan.plan_type == "single_vehicle_multistop" else stack.max(axis=0)


def reliability(G: nx.DiGraph, plan: CandidatePlan, deadline_hours: float, draws: Dict[str, np.ndarray]) -> dict:
    t = plan_eta_samples(G, plan, draws)
    return {"eta_p10": round(float(np.percentile(t, 10)), 3), "eta_p50": round(float(np.percentile(t, 50)), 3),
            "eta_p90": round(float(np.percentile(t, 90)), 3), "p_late": round(float((t > deadline_hours).mean()), 3),
            "draws": len(t)}
