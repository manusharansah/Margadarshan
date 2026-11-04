"""Plan generation + feasibility arithmetic on a deterministic lattice network."""
import math

import pytest

from gis_backend.models import DeliveryRequest, StorageNode
from gis_backend.source_selector import PlanContext, generate_plans, multistop_plan, single_source_plans

from .helpers import grid_network


@pytest.fixture(scope="module")
def net():
    return grid_network(4)


def wh(wid, node, stock, vehicles, cap=200):
    return StorageNode(wid, wid, 0, 0, stock, vehicles, cap, road_node=node)


def ctx(net, warehouses, qty, deadline, dest=15):
    req = DeliveryRequest(0, 0, qty, deadline)
    return PlanContext(net.graph, {w.node_id: w for w in warehouses}, dest, req)


def test_single_source_vehicle_count_and_totals(net):
    c = ctx(net, [wh("A", 0, 900, 4)], 450, 100)
    (p,) = generate_plans(c)
    assert p.plan_type == "single_source" and p.total_units == 450
    assert p.vehicles_used == math.ceil(450 / 200) == 3
    assert p.legs[0].units_carried == 450 and p.meets_deadline


def test_single_source_blocked_by_vehicles_not_stock(net):
    # stock 900 but 1 vehicle (200 cap) -> cannot ship 450 alone
    assert single_source_plans(ctx(net, [wh("A", 0, 900, 1)], 450, 100)) == []


def test_multi_source_units_sum_and_time_is_slowest_leg(net):
    c = ctx(net, [wh("A", 0, 300, 2), wh("B", 3, 300, 2), wh("C", 12, 10, 1)], 500, 100)
    plans = generate_plans(c)
    assert plans and all(p.plan_type == "multi_source_parallel" for p in plans)
    for p in plans:
        assert sum(l.units_carried for l in p.legs) == 500
        assert p.total_time_hours == pytest.approx(max(l.time_hours for l in p.legs))
        assert all(l.units_carried <= c.W[l.source].units_in_stock for l in p.legs)
        assert all(l.vehicles_used <= c.W[l.source].vehicles_available for l in p.legs)


def test_tier_priority_single_beats_multi_when_deadline_ok(net):
    plans = generate_plans(ctx(net, [wh("A", 0, 900, 4), wh("B", 3, 400, 2)], 300, 100))
    assert {p.plan_type for p in plans} == {"single_source"}


def test_falls_through_to_multi_when_single_too_slow(net):
    c = ctx(net, [wh("FAR", 0, 900, 4), wh("NEAR1", 14, 150, 1), wh("NEAR2", 11, 150, 1)], 300, 100)
    far = c.route("FAR")["time_hours"]
    near = max(c.route("NEAR1")["time_hours"], c.route("NEAR2")["time_hours"])
    assert near < far
    deadline = (near + far) / 2
    plans = generate_plans(ctx(net, list(c.W.values()), 300, deadline))
    ok = [p for p in plans if p.meets_deadline]
    assert ok and all(p.plan_type == "multi_source_parallel" for p in ok)
    assert any(p.plan_type == "single_source" and not p.meets_deadline for p in plans)


def test_multistop_when_vehicles_are_the_constraint(net):
    # plenty of stock in total but only one vehicle in the whole network
    c = ctx(net, [wh("A", 0, 120, 1), wh("B", 5, 120, 0), wh("C", 10, 120, 0)], 300, 1000)
    plans = generate_plans(c)
    assert [p.plan_type for p in plans] == ["single_vehicle_multistop"]
    p = plans[0]
    assert p.vehicles_used == 1 and sum(l.units_carried for l in p.legs) == 300
    assert len(p.legs) == 2  # 300 units over a 200-unit vehicle -> two trips
    assert set(p.sources_used) <= {"A", "B", "C"}


def test_multistop_second_trip_includes_return_leg(net):
    c = ctx(net, [wh("A", 0, 500, 1)], 400, 1000)
    p = multistop_plan(c, "A")
    assert len(p.legs) == 2
    assert p.legs[1].time_hours > p.legs[0].time_hours  # trip 2 = return home + collect + deliver


def test_infeasible_by_stock(net):
    assert generate_plans(ctx(net, [wh("A", 0, 100, 2), wh("B", 3, 100, 2)], 1000, 100)) == []


def test_infeasible_by_deadline_still_reports_best_case(net):
    plans = generate_plans(ctx(net, [wh("A", 0, 900, 4)], 100, 0.01))
    assert plans and not any(p.meets_deadline for p in plans)
    assert min(p.total_time_hours for p in plans) > 0.01


def test_warehouse_at_destination_has_zero_travel(net):
    (p,) = generate_plans(ctx(net, [wh("HERE", 15, 500, 3)], 100, 1))
    assert p.total_time_hours == 0 and p.distance_km == 0
