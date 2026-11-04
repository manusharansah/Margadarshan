import math

import numpy as np
import pytest

from gis_backend import edge_weight as ew
from gis_backend import truck_model as tm
from gis_backend.models import RoadEdge


def edge(**kw):
    base = dict(node_a=1, node_b=2, length_km=10.0, slope_pct=0.0, max_slope_pct=0.0, ascent_ab_m=0.0,
                descent_ab_m=0.0, road_type="paved", highway="primary", max_speed_kmph=50.0,
                hazard_terrain=0.0, hazard_incident=0.0)
    base.update(kw)
    return RoadEdge(**base)


# ---------------------------------------------------------------- routing cost
def test_weight_flat_paved_safe_equals_length():
    assert ew.compute_edge_weight(edge()) == pytest.approx(10.0)


def test_weight_components_add_up():
    e = edge(road_type="gravel", slope_pct=10, hazard_terrain=0.5)
    assert ew.compute_edge_weight(e) == pytest.approx(10 * (1 + 0.3 + 0.005 * 10) + 0.5 * 10 * ew.HAZARD_KM_PER_KM)


def test_untagged_road_cost_is_the_expectation_over_the_surface_prior():
    e = edge(road_type="gravel", surface_mix=(0.4, 0.1, 0.5))
    assert ew.compute_edge_weight(e) == pytest.approx(10 * (1 + 0.1 * 0.3 + 0.5 * 0.8))


def test_hazard_scales_with_length_and_rain_amplifies_it():
    short, long_ = edge(length_km=1, hazard_terrain=1), edge(length_km=10, hazard_terrain=1)
    assert (ew.compute_edge_weight(long_) - 10) == pytest.approx(10 * (ew.compute_edge_weight(short) - 1))
    e = edge(hazard_terrain=0.4)
    assert ew.effective_hazard(e, 1.0) == pytest.approx(0.4 * (1 + ew.RAIN_HAZARD_GAIN))
    assert ew.effective_hazard(edge(hazard_terrain=0.9), 1.0) == 1.0
    assert ew.effective_hazard(edge(hazard_terrain=0.5, hazard_incident=1.0)) == pytest.approx(0.6 * 0.5 + 0.4)


# -------------------------------------------------------- rainfall threshold
def test_rain_threshold_matches_published_nepal_himalaya_values():
    # Dahal and Hasegawa (2008): I = 73.9 D^-0.79 -> about 144 mm in 24 h and 181 mm in 72 h
    assert ew.rain_threshold_mm(24) == pytest.approx(144, abs=1)
    assert ew.rain_threshold_mm(72) == pytest.approx(181, abs=1)


def test_rain_index_uses_worst_window_and_caps_at_one():
    day = [6.0] * 24                                   # 144 mm in 24 h = exactly the threshold
    assert ew.rain_index_from_hourly(day + [0.0] * 72) == pytest.approx(1.0, abs=0.01)
    assert ew.rain_index_from_hourly([3.0] * 24 + [0.0] * 72) == pytest.approx(0.5, abs=0.01)
    assert ew.rain_index_from_hourly([0.0] * 96) == 0
    assert ew.rain_index_from_hourly([20.0] * 96) == 1.0
    assert ew.rain_index_from_hourly([0.0] * 30 + [6.0] * 24 + [0.0] * 42) == pytest.approx(1.0, abs=0.01)  # window anywhere
    assert ew.rain_index_from_hourly([5.0] * 10) == 0                                                      # too short
    assert ew.rain_index_from_hourly([float("nan")] * 96) == 0


# --------------------------------------------------------------- truck physics
def test_uphill_crawl_speed_follows_the_power_balance():
    t = tm.steady_state_table()
    assert t[0] > t[4] > t[8] > t[12]
    assert 25 < t[8] < 32 and 17 < t[12] < 23          # 8 t truck, 66.7 kW: about 28 and 20 km/h


def test_more_power_climbs_faster_and_heavier_climbs_slower():
    g, crr = np.array([0.08]), np.array([0.01])
    base = tm.uphill_speed_kmph(g, crr, tm.DEFAULT_TRUCK)[0]
    assert tm.uphill_speed_kmph(g, crr, tm.with_params(weight_to_power_kg_per_kw=80))[0] > base
    assert tm.uphill_speed_kmph(g, crr, tm.with_params(mass_kg=16000, weight_to_power_kg_per_kw=240))[0] < base  # same engine, double the load


def test_curve_speed_matches_the_lateral_friction_formula():
    ci = np.array([500.0])                              # 500 deg/km -> radius about 115 m
    r = 1000 / math.radians(500)
    assert tm.curve_speed_kmph(ci, tm.DEFAULT_TRUCK)[0] == pytest.approx(math.sqrt(127 * r * 0.19))
    assert tm.curve_speed_kmph(np.array([2000.0]), tm.DEFAULT_TRUCK)[0] < tm.curve_speed_kmph(ci, tm.DEFAULT_TRUCK)[0]


def test_time_is_direction_dependent_and_bounded_by_free_flow():
    up = edge(ascent_ab_m=1000.0, descent_ab_m=0.0, slope_pct=10)   # 10 % average grade climbing a -> b
    t_up, t_down = ew.compute_edge_time_hours(up, True), ew.compute_edge_time_hours(up, False)
    assert t_up > t_down                                            # climbing is slower than descending
    assert ew.compute_edge_time_hours(edge()) == pytest.approx(10 / 50)   # flat paved: free-flow speed
    assert ew.compute_edge_time_hours(edge(road_type="rough")) == pytest.approx(10 / (50 * 0.55))


def test_winding_road_is_slower_than_a_straight_one():
    straight, winding = edge(), edge()
    winding.curvature_deg_per_km = 1500.0
    assert ew.compute_edge_time_hours(winding) > ew.compute_edge_time_hours(straight)


def test_expected_time_averages_times_not_speeds():
    e = edge(road_type="paved", surface_mix=(0.5, 0.0, 0.5))
    fast, slow = 10 / 50, 10 / (50 * 0.55)
    assert ew.compute_edge_time_hours(e) == pytest.approx(0.5 * fast + 0.5 * slow)


def test_fuel_flat_road_is_in_a_realistic_range_and_climbing_costs_more():
    flat = ew.fuel_litres(edge(road_type="paved", max_speed_kmph=40.0)) / 10
    assert 0.12 < flat < 0.25                                       # 4 to 8 km/L for an 8 t truck
    climb = edge(ascent_ab_m=500.0, descent_ab_m=0.0)
    assert ew.fuel_litres(climb, True) > 2 * ew.fuel_litres(climb, False)
    assert ew.fuel_litres(climb, False) > 0                         # gravity never refuels the truck, idle still burns


def test_fuel_energy_matches_hand_calculation():
    p = tm.DEFAULT_TRUCK
    v = np.array([[40.0, 40.0, 40.0]])
    got = tm.fuel_litres(np.array([1.0]), v, np.array([100.0]), np.array([[1.0, 0, 0]]), p)[0]
    energy = 0.010 * 8000 * 9.81 * 1000 + 0.5 * 1.0 * 6.0 * (40 / 3.6) ** 2 * 1000 + 8000 * 9.81 * 100
    assert got == pytest.approx(energy / (0.85 * 0.35 * 36e6) + 2.5 * (1 / 40), rel=1e-6)


def test_heading_change_per_km():
    straight = [(27.6, 85.5, 0), (27.601, 85.5, 0), (27.602, 85.5, 0)]
    assert ew.heading_change_deg_per_km(straight, 0.222) == pytest.approx(0, abs=1e-6)
    right_angle = [(27.6, 85.5, 0), (27.601, 85.5, 0), (27.601, 85.5011, 0)]
    assert ew.heading_change_deg_per_km(right_angle, 0.2) == pytest.approx(90 / 0.2, rel=0.02)
    assert ew.heading_change_deg_per_km(straight[:2], 1.0) == 0
