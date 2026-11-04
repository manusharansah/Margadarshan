import numpy as np
import pytest

from gis_backend.models import DeliveryRequest, StorageNode
from gis_backend.source_selector import PlanContext, generate_plans
from gis_backend.uncertainty import draw_parameters, leg_drive_time_samples, plan_eta_samples, reliability

from .helpers import grid_network


@pytest.fixture(scope="module")
def net():
    return grid_network(4)


def one_plan(net, deadline=100.0, qty=100):
    wh = StorageNode("A", "A", 0, 0, 900, 4, 200, road_node=0)
    (p,) = generate_plans(PlanContext(net.graph, {"A": wh}, 15, DeliveryRequest(0, 0, qty, deadline)))
    return p


def test_draws_follow_their_documented_distributions():
    d = draw_parameters(20000, seed=1)
    assert d["speed_scale"].mean() == pytest.approx(1.0, abs=0.01) and 0.6 <= d["speed_scale"].min() and d["speed_scale"].max() <= 1.4
    assert d["power_scale"].min() >= 0.8 and d["power_scale"].max() <= 1.2
    assert d["congestion"].min() >= 1.0 and d["congestion"].max() <= 1.3 and d["congestion"].mean() == pytest.approx(1.15, abs=0.01)


def test_draws_are_reproducible():
    a, b = draw_parameters(50, seed=3), draw_parameters(50, seed=3)
    assert all(np.array_equal(a[k], b[k]) for k in a)


def test_median_is_close_to_the_deterministic_time_and_the_range_is_ordered(net):
    p = one_plan(net)
    r = reliability(net.graph, p, 100.0, draw_parameters(400))
    assert r["eta_p10"] < r["eta_p50"] < r["eta_p90"]
    # congestion only slows trips (1.0-1.3x), so the median sits somewhat above the free-flow estimate
    assert p.total_time_hours * 0.9 < r["eta_p50"] < p.total_time_hours * 1.4
    assert r["p_late"] == 0 and r["draws"] == 400


def test_late_probability_tracks_the_deadline(net):
    p = one_plan(net)
    d = draw_parameters(400)
    t = plan_eta_samples(net.graph, p, d)
    assert reliability(net.graph, p, float(t.max()) + 1, d)["p_late"] == 0
    assert reliability(net.graph, p, float(t.min()) - 1e-3, d)["p_late"] == 1
    median = float(np.median(t))
    assert reliability(net.graph, p, median, d)["p_late"] == pytest.approx(0.5, abs=0.05)


def test_zero_length_route_has_zero_time(net):
    d = draw_parameters(10)
    assert (leg_drive_time_samples(net.graph, [3], d) == 0).all()


def test_parallel_plans_take_the_slowest_leg_and_multistop_sums_trips(net):
    wa = StorageNode("A", "A", 0, 0, 300, 2, 200, road_node=0)
    wb = StorageNode("B", "B", 0, 0, 300, 2, 200, road_node=3)
    plans = generate_plans(PlanContext(net.graph, {"A": wa, "B": wb}, 15, DeliveryRequest(0, 0, 500, 100)))
    multi = next(p for p in plans if p.plan_type == "multi_source_parallel")
    d = draw_parameters(50)
    t = plan_eta_samples(net.graph, multi, d)
    per_leg = [plan_eta_samples(net.graph, type(multi)(**{**multi.__dict__, "legs": [leg]}), d) for leg in multi.legs]
    assert np.allclose(t, np.maximum(*per_leg))
