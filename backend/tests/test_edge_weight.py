import pytest

from gis_backend import edge_weight as ew
from gis_backend.models import RoadEdge


def edge(**kw):
    base = dict(node_a=1, node_b=2, length_km=10.0, slope_pct=0.0, max_slope_pct=0.0, ascent_ab_m=0.0,
                descent_ab_m=0.0, road_type="paved", highway="primary", max_speed_kmph=50.0,
                hazard_terrain=0.0, hazard_incident=0.0)
    base.update(kw)
    return RoadEdge(**base)


def test_weight_flat_paved_safe_equals_length():
    assert ew.compute_edge_weight(edge()) == pytest.approx(10.0)


def test_weight_components_add_up():
    e = edge(road_type="gravel", slope_pct=10, hazard_terrain=0.5)
    expected = 10 * (1 + 0.3 + 0.005 * 10) + 0.5 * 10 * ew.HAZARD_KM_PER_KM
    assert ew.compute_edge_weight(e) == pytest.approx(expected)


def test_hazard_scales_with_length_not_constant():
    short, long_ = edge(length_km=1, hazard_terrain=1), edge(length_km=10, hazard_terrain=1)
    assert (ew.compute_edge_weight(long_) - 10) == pytest.approx(10 * (ew.compute_edge_weight(short) - 1))


def test_rain_amplifies_hazard_and_is_capped():
    e = edge(hazard_terrain=0.4)
    assert ew.effective_hazard(e, 1.0) == pytest.approx(0.4 * (1 + ew.RAIN_HAZARD_GAIN))
    assert ew.effective_hazard(edge(hazard_terrain=0.9), 1.0) == 1.0
    assert ew.compute_edge_weight(e, 1.0) > ew.compute_edge_weight(e, 0.0)


def test_incident_layer_blends_when_present():
    e = edge(hazard_terrain=0.5, hazard_incident=1.0)
    assert ew.effective_hazard(e) == pytest.approx(0.6 * 0.5 + 0.4 * 1.0)


def test_time_derated_by_surface_and_grade():
    t_paved = ew.compute_edge_time_hours(edge())
    assert t_paved == pytest.approx(10 / 50)
    assert ew.compute_edge_time_hours(edge(road_type="rough")) == pytest.approx(10 / 25)
    assert ew.compute_edge_time_hours(edge(slope_pct=20)) == pytest.approx(10 / (50 * 0.5))


def test_time_speed_floor():
    assert ew.compute_edge_time_hours(edge(road_type="rough", max_speed_kmph=5, slope_pct=30)) == pytest.approx(10 / 5)


def test_fuel_is_direction_aware_physics():
    e = edge(ascent_ab_m=100.0, descent_ab_m=100.0 * 0 + 0.0)
    up, down = ew.fuel_litres(e, True), ew.fuel_litres(e, False)
    climb_l = 100 * 8000 * 9.81 / (0.30 * 36e6)
    assert up - down == pytest.approx(climb_l, rel=1e-3)
    assert down == pytest.approx(10 * ew.BASE_L_PER_KM)


def test_rain_index_mapping():
    assert ew.rain_index_from_mm(None) == 0
    assert ew.rain_index_from_mm(100) == pytest.approx(0.5)
    assert ew.rain_index_from_mm(1000) == 1.0
