"""
Physics model of a loaded rigid truck on a hill road. Vectorised with numpy so it can price every road
piece quickly and drive the uncertainty simulation.

Speed on a piece is the minimum of three limits, evaluated per direction of travel:

1. Free-flow speed for the road class and surface (OSM/Overture ``maxspeed`` when present, else a class default).
2. Curve limit from the standard lateral-friction relation  V = sqrt(127 R (e + f))  (V in km/h, R in m) with
   e + f = 0.19, using the piece's average radius R = 1000 / (curvature_deg_per_km * pi/180).
3. Grade limit. Uphill, steady speed solves the power balance
       P_wheel = (Crr m g + m g grade + 0.5 rho CdA v^2) v
   with engine power from a weight-to-power ratio (120 kg/kW is the design truck used in highway
   engineering guidance, so an 8 t truck has 66.7 kW). Downhill, speed is capped for braking safety.

Fuel is the mechanical energy needed (rolling, aerodynamic and net climb, never negative) divided by driveline
and engine efficiency, plus idle burn while moving slowly.

Every constant is an engineering assumption with a typical-value basis, listed here so it can be replaced with
fleet data. None of this has been validated against telemetry from Nepali trucks.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict

import numpy as np

SURFACES = ("paved", "gravel", "rough")
G = 9.81


@dataclass(frozen=True)
class TruckParams:
    mass_kg: float = 8000.0                 # loaded rigid truck (assumption)
    weight_to_power_kg_per_kw: float = 120.0  # design-truck value from highway-engineering guidance
    driveline_eff: float = 0.85
    engine_eff: float = 0.35                # diesel brake thermal efficiency at typical part load
    diesel_mj_per_l: float = 36.0
    cda_m2: float = 6.0                     # drag coefficient x frontal area
    air_density: float = 1.0                # kg/m3 at 1,000-2,000 m altitude
    crr_paved: float = 0.010                # typical rolling-resistance coefficients for trucks
    crr_gravel: float = 0.020
    crr_rough: float = 0.035
    idle_l_per_h: float = 2.5               # fuel burned per hour of driving regardless of load
    surface_speed_factor_paved: float = 1.0
    surface_speed_factor_gravel: float = 0.75
    surface_speed_factor_rough: float = 0.55
    curve_e_plus_f: float = 0.19            # superelevation 0.04 + side friction 0.15
    min_speed_kmph: float = 5.0
    steep_downhill_cap_start_pct: float = 5.0  # braking-safety cap starts here (assumption)
    downhill_cap_base_kmph: float = 50.0
    downhill_cap_slope: float = 2.5          # km/h lost per extra percent of grade
    downhill_cap_floor_kmph: float = 15.0

    @property
    def power_w(self) -> float:
        return self.mass_kg / self.weight_to_power_kg_per_kw * 1000.0

    def crr(self) -> np.ndarray:
        return np.array([self.crr_paved, self.crr_gravel, self.crr_rough])

    def surface_factor(self) -> np.ndarray:
        return np.array([self.surface_speed_factor_paved, self.surface_speed_factor_gravel,
                         self.surface_speed_factor_rough])


DEFAULT_TRUCK = TruckParams()


def curve_speed_kmph(curvature_deg_per_km: np.ndarray, p: TruckParams) -> np.ndarray:
    ci = np.maximum(curvature_deg_per_km, 1e-6)
    radius_m = 1000.0 / np.radians(ci)
    return np.sqrt(127.0 * radius_m * p.curve_e_plus_f)


def uphill_speed_kmph(grade: np.ndarray, crr: np.ndarray, p: TruckParams, power_w=None) -> np.ndarray:
    """Steady speed from the power balance; ``grade`` is rise/run (negative = downhill). inf if no resistance."""
    power = (p.power_w if power_w is None else power_w) * p.driveline_eff
    a = 0.5 * p.air_density * p.cda_m2
    b = p.mass_kg * G * (crr + grade)
    v = np.full(np.broadcast(grade, crr).shape, 10.0)
    pos = b > 0
    bb = np.where(pos, b, 1.0)
    for _ in range(40):  # Newton on a v^3 + b v - P = 0, monotone for b > 0
        f = a * v ** 3 + bb * v - power
        v = np.maximum(v - f / (3 * a * v ** 2 + bb), 0.1)
    return np.where(pos, v * 3.6, np.inf)


def downhill_cap_kmph(grade: np.ndarray, p: TruckParams) -> np.ndarray:
    steep = np.maximum(-grade * 100.0 - p.steep_downhill_cap_start_pct, 0.0)
    return np.maximum(p.downhill_cap_base_kmph - p.downhill_cap_slope * steep, p.downhill_cap_floor_kmph)


def piece_speed_kmph(free_kmph: np.ndarray, curvature: np.ndarray, grade: np.ndarray, p: TruckParams,
                     power_w=None, speed_scale=1.0) -> np.ndarray:
    """Speed per piece and surface. Shapes: pieces (n,), result (n, 3) for paved/gravel/rough."""
    sf = p.surface_factor()[None, :]
    free = free_kmph[:, None] * sf * speed_scale
    curve = curve_speed_kmph(curvature, p)[:, None]
    up = uphill_speed_kmph(grade[:, None], p.crr()[None, :], p, power_w)
    down = downhill_cap_kmph(grade, p)[:, None]
    v = np.minimum(np.minimum(free, curve), np.where(grade[:, None] < 0, down, up))
    return np.maximum(v, p.min_speed_kmph)


def expected_time_hours(length_km: np.ndarray, speed: np.ndarray, mix: np.ndarray) -> np.ndarray:
    """Mean travel time over the surface mix (the correct average of times, not of speeds)."""
    return (mix * (length_km[:, None] / speed)).sum(axis=1)


def fuel_litres(length_km: np.ndarray, speed_kmph: np.ndarray, net_rise_m: np.ndarray, mix: np.ndarray,
                p: TruckParams) -> np.ndarray:
    """Expected fuel over the surface mix for travel along each piece in one direction.

    ``speed_kmph`` has shape (n, 3): the speed on each surface, so every surface is priced at its own speed."""
    v_ms = speed_kmph / 3.6
    dist_m = length_km[:, None] * 1000.0
    roll = p.crr()[None, :] * p.mass_kg * G * dist_m
    aero = 0.5 * p.air_density * p.cda_m2 * v_ms ** 2 * dist_m
    climb = p.mass_kg * G * net_rise_m[:, None]
    energy_j = np.maximum(roll + aero + climb, 0.0)  # gravity can carry the truck but never refuels it
    fuel = energy_j / (p.driveline_eff * p.engine_eff * p.diesel_mj_per_l * 1e6)
    idle = p.idle_l_per_h * (length_km[:, None] / speed_kmph)
    return ((fuel + idle) * mix).sum(axis=1)


def with_params(**kw) -> TruckParams:
    return replace(DEFAULT_TRUCK, **kw)


def steady_state_table(p: TruckParams = DEFAULT_TRUCK) -> Dict[int, float]:
    """Uphill crawl speed (km/h) on paved road for grades 0-12 %: handy for review against known truck behaviour."""
    g = np.arange(0, 13) / 100.0
    v = uphill_speed_kmph(g, np.full_like(g, p.crr_paved), p)
    return {int(round(x * 100)): float(s) for x, s in zip(g, v)}
