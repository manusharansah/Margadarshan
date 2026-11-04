"""Routing verified against brute force on small graphs."""
import itertools

import networkx as nx
import pytest

from gis_backend.routing import best_route, held_karp_tsp, k_shortest_paths, path_geometry, path_metrics

from .helpers import grid_network


@pytest.fixture(scope="module")
def net():
    return grid_network(4)


def all_simple_costs(G, s, t):
    return sorted((sum(G[u][v]["weight"] for u, v in zip(p, p[1:])), p) for p in nx.all_simple_paths(G, s, t, cutoff=9))


def test_yen_matches_brute_force_top_k(net):
    G = net.graph
    brute = all_simple_costs(G, 0, 15)[:5]
    yen = k_shortest_paths(G, 0, 15, k=5)
    yen_costs = [sum(G[u][v]["weight"] for u, v in zip(p, p[1:])) for p in yen]
    assert yen_costs == pytest.approx([c for c, _ in brute])
    assert len({tuple(p) for p in yen}) == 5  # distinct, loopless


def test_path_metrics_are_edge_sums(net):
    G = net.graph
    path = k_shortest_paths(G, 0, 15, 1)[0]
    m = path_metrics(G, path)
    assert m["distance_km"] == pytest.approx(sum(G[u][v]["length"] for u, v in zip(path, path[1:])))
    assert m["time_hours"] == pytest.approx(sum(G[u][v]["time"] for u, v in zip(path, path[1:])))
    assert m["max_hazard"] == max(G[u][v]["hazard"] for u, v in zip(path, path[1:]))


def test_best_route_prefers_cheapest_meeting_deadline_else_fastest(net):
    G = net.graph
    loose = best_route(G, 0, 15, k=3, deadline_hours=100)
    assert loose["edge_cost"] == pytest.approx(path_metrics(G, k_shortest_paths(G, 0, 15, 1)[0])["edge_cost"])
    fastest_t = nx.dijkstra_path_length(G, 0, 15, weight="time")
    tight = best_route(G, 0, 15, k=3, deadline_hours=fastest_t * 0.5)  # impossible deadline
    assert tight["time_hours"] == pytest.approx(fastest_t)  # reports true best case


def test_best_route_same_node(net):
    r = best_route(net.graph, 3, 3)
    assert r["path"] == [3] and r["distance_km"] == 0


def test_geometry_orientation(net):
    G = net.graph
    fwd = path_geometry(G, [0, 1, 2])
    rev = path_geometry(G, [2, 1, 0])
    assert fwd == rev[::-1]
    assert fwd[0][:2] == (G.nodes[0]["lat"], G.nodes[0]["lng"])


def test_directional_ascent_swaps_when_reversed():
    from gis_backend.network import Network
    from .helpers import make_artifact
    n = Network(make_artifact([(1, 2, dict(ascent_ab_m=50.0, descent_ab_m=10.0))], {1: (0, 0), 2: (1, 0)}))
    up, down = path_metrics(n.graph, [1, 2]), path_metrics(n.graph, [2, 1])
    assert (up["ascent_m"], up["descent_m"]) == (50, 10)
    assert (down["ascent_m"], down["descent_m"]) == (10, 50)
    assert up["fuel_litres"] > down["fuel_litres"]


@pytest.mark.parametrize("stops", [[5, 10], [3, 12, 9], [1, 6, 11, 14]])
def test_held_karp_is_optimal_vs_permutations(net, stops):
    G = net.graph
    start = 0
    res = held_karp_tsp(G, start, stops)
    d = {a: nx.single_source_dijkstra_path_length(G, a, weight="weight") for a in [start] + stops}
    best = min(sum(d[a][b] for a, b in zip((start,) + perm, perm)) for perm in itertools.permutations(stops))
    assert res["edge_cost"] == pytest.approx(best)
    assert sorted(res["visit_order"][1:]) == sorted(stops)
    # returned node path is connected and passes through every stop
    assert all(G.has_edge(u, v) for u, v in zip(res["path"], res["path"][1:]))
    assert set(stops) <= set(res["path"])


def test_held_karp_no_stops(net):
    assert held_karp_tsp(net.graph, 0, [0])["path"] == [0]


def reference_best_route(G, s, t, k, deadline):
    """Eager definition: score Yen's K cheapest + the fastest path; cheapest meeting the deadline, else fastest."""
    cands = k_shortest_paths(G, s, t, k)
    fastest = nx.dijkstra_path(G, s, t, weight="time")
    if fastest not in cands:
        cands.append(fastest)
    scored = [path_metrics(G, p) for p in cands]
    ok = [m for m in scored if m["time_hours"] <= deadline]
    return min(ok, key=lambda m: m["edge_cost"]) if ok else min(scored, key=lambda m: m["time_hours"])


@pytest.mark.parametrize("s,t", [(0, 15), (3, 12), (5, 10), (1, 14), (7, 8)])
def test_lazy_best_route_equals_eager_reference(net, s, t):
    G = net.graph
    fastest = nx.dijkstra_path_length(G, s, t, weight="time")
    for deadline in (fastest * f for f in (0.5, 1.0, 1.05, 1.3, 2.0, 10.0)):
        got = best_route(G, s, t, k=3, deadline_hours=deadline)
        ref = reference_best_route(G, s, t, 3, deadline)
        if ref["time_hours"] <= deadline:
            assert got["time_hours"] <= deadline
            assert got["edge_cost"] <= ref["edge_cost"] + 1e-9   # never worse than the eager answer
        else:
            assert got["time_hours"] == pytest.approx(ref["time_hours"])


# ------------------------------------------------------------------ one-way roads
def oneway_net():
    """Square 0-1-3-2 with a short one-way edge 0->1 and a long two-way detour 0-2-3-1."""
    from gis_backend.network import Network

    from .helpers import make_artifact
    xy = {0: (0, 0), 1: (0, 2), 2: (3, 0), 3: (3, 2)}
    edges = [(0, 1, dict(oneway=1)), (0, 2, {}), (2, 3, {}), (3, 1, {})]
    return Network(make_artifact(edges, xy))


def test_one_way_road_is_used_forward_and_avoided_backward():
    n = oneway_net()
    G = n.graph
    assert G.has_edge(0, 1) and not G.has_edge(1, 0)
    fwd, back = best_route(G, 0, 1), best_route(G, 1, 0)
    assert fwd["path"] == [0, 1] and fwd["distance_km"] == pytest.approx(2.0, abs=0.01)
    assert back["path"] == [1, 3, 2, 0] and back["distance_km"] > 6          # forced onto the long way round


def test_reverse_only_flag_swaps_the_allowed_direction():
    from gis_backend.network import Network

    from .helpers import make_artifact
    n = Network(make_artifact([(0, 1, dict(oneway=-1)), (1, 2, {}), (2, 0, {})], {0: (0, 0), 1: (0, 2), 2: (2, 1)}))
    assert n.graph.has_edge(1, 0) and not n.graph.has_edge(0, 1)


def test_nodes_that_one_way_streets_strand_are_dropped_from_the_network():
    from gis_backend.network import Network

    from .helpers import make_artifact
    # node 3 can be entered (2 -> 3, one-way) but never left: it is outside the strongly connected core
    n = Network(make_artifact([(0, 1, {}), (1, 2, {}), (2, 0, {}), (2, 3, dict(oneway=1))],
                              {0: (0, 0), 1: (0, 2), 2: (2, 1), 3: (4, 1)}))
    assert 3 not in n.graph and n.dropped_nodes == 1
    with pytest.raises(ValueError):
        n.snap(LAT0_FAR := 27.6 + 4 / 111.32 + 0.5, 85.5)       # far from every remaining road


def test_uphill_arcs_are_slower_than_downhill_arcs():
    from gis_backend.network import Network

    from .helpers import make_artifact
    n = Network(make_artifact([(0, 1, dict(ascent_ab_m=120.0, descent_ab_m=0.0, slope_pct=6, length_km=2.0)),
                               (1, 2, {}), (2, 0, {})], {0: (0, 0), 1: (0, 2), 2: (2, 1)}))
    G = n.graph
    assert G[0][1]["time"] > G[1][0]["time"] and G[0][1]["fuel"] > G[1][0]["fuel"]
    assert G[0][1]["ascent"] == 120 and G[1][0]["descent"] == 120
