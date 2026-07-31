"""
Routing engine (workflow doc, Section 7):
  - Yen's K-shortest paths for source -> destination routing (via
    networkx.shortest_simple_paths, which implements Yen's algorithm).
  - Held-Karp exact TSP (open path) for the case where a single vehicle
    must visit several warehouses in sequence before delivering.

Nothing here calls an LLM -- this module only does graph math.
"""
from itertools import combinations
from typing import Dict, List, Optional

import networkx as nx

from .edge_weight import compute_edge_weight, compute_edge_time_hours, compute_edge_fuel
from .models import StorageNode, RoadEdge


def build_graph(nodes: Dict[str, StorageNode], edges: List[RoadEdge]) -> nx.Graph:
    G = nx.Graph()
    for node_id, node in nodes.items():
        G.add_node(node_id, data=node)
    for edge in edges:
        w = compute_edge_weight(edge)
        t = compute_edge_time_hours(edge)
        f = compute_edge_fuel(edge)
        edge.weight = w
        edge.time_hours = t
        edge.fuel_estimate = f
        G.add_edge(edge.node_a, edge.node_b, weight=w, time=t, fuel=f, length=edge.length_km, data=edge)
    return G


def k_shortest_paths(G: nx.Graph, source: str, target: str, k: int = 3) -> List[list]:
    """Yen's K-shortest paths, ranked by edge_weight cost."""
    if source not in G or target not in G:
        return []
    try:
        gen = nx.shortest_simple_paths(G, source, target, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    paths = []
    for path in gen:
        paths.append(path)
        if len(paths) >= k:
            break
    return paths


def path_metrics(G: nx.Graph, path: list):
    distance = sum(G[u][v]["length"] for u, v in zip(path, path[1:]))
    time_hours = sum(G[u][v]["time"] for u, v in zip(path, path[1:]))
    cost = sum(G[u][v]["weight"] for u, v in zip(path, path[1:]))
    fuel = sum(G[u][v]["fuel"] for u, v in zip(path, path[1:]))
    return distance, time_hours, cost, fuel


def best_route(G: nx.Graph, source: str, target: str, k: int = 3) -> Optional[dict]:
    """Lowest-cost route among the k Yen candidates, with its metrics."""
    if source == target:
        return {"path": [source], "distance_km": 0.0, "time_hours": 0.0, "edge_cost": 0.0, "fuel": 0.0}
    candidates = k_shortest_paths(G, source, target, k=k)
    if not candidates:
        return None
    best = min(candidates, key=lambda p: path_metrics(G, p)[2])
    distance, time_hours, cost, fuel = path_metrics(G, best)
    return {"path": best, "distance_km": distance, "time_hours": time_hours, "edge_cost": cost, "fuel": fuel}


def held_karp_tsp(G: nx.Graph, start: str, stops: List[str]) -> Optional[dict]:
    """
    Exact TSP over an open path: start at `start`, visit every node in
    `stops` exactly once, minimizing total edge-weight cost. Distance
    between any two points is their shortest-path cost in G (stops need
    not be directly connected). Exact and fine for the small number of
    candidate warehouses expected in a demo (roughly <= 12 stops).
    """
    stops = [s for s in stops if s != start]
    if not stops:
        return {"visit_order": [start], "path": [start], "distance_km": 0.0,
                "time_hours": 0.0, "edge_cost": 0.0, "fuel": 0.0}

    all_points = [start] + stops
    n = len(all_points)

    pair_cost, pair_path = {}, {}
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            a, b = all_points[i], all_points[j]
            try:
                path = nx.shortest_path(G, a, b, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None
            pair_path[(i, j)] = path
            pair_cost[(i, j)] = path_metrics(G, path)[2]

    stop_idx = list(range(1, n))
    dp = {}
    for j in stop_idx:
        dp[(frozenset([j]), j)] = (pair_cost[(0, j)], 0)

    for size in range(2, len(stop_idx) + 1):
        for subset in combinations(stop_idx, size):
            subset_fs = frozenset(subset)
            for j in subset:
                prev_subset = subset_fs - {j}
                best = None
                for k_ in prev_subset:
                    if (prev_subset, k_) not in dp:
                        continue
                    prev_cost, _ = dp[(prev_subset, k_)]
                    cand = prev_cost + pair_cost[(k_, j)]
                    if best is None or cand < best[0]:
                        best = (cand, k_)
                if best:
                    dp[(subset_fs, j)] = best

    full_set = frozenset(stop_idx)
    best_end = None
    for j in stop_idx:
        if (full_set, j) in dp:
            cost, _ = dp[(full_set, j)]
            if best_end is None or cost < best_end[0]:
                best_end = (cost, j)
    if best_end is None:
        return None

    cost, last = best_end
    order, subset = [last], full_set
    while len(subset) > 1:
        _, prev = dp[(subset, last)]
        order.append(prev)
        subset = subset - {last}
        last = prev
    order.reverse()

    visit_order = [all_points[0]] + [all_points[i] for i in order]
    full_path = [visit_order[0]]
    total_distance = total_time = total_fuel = 0.0
    for a, b in zip(visit_order, visit_order[1:]):
        ia, ib = all_points.index(a), all_points.index(b)
        seg = pair_path[(ia, ib)]
        full_path.extend(seg[1:])
        d, t, c, f = path_metrics(G, seg)
        total_distance += d
        total_time += t
        total_fuel += f

    return {
        "visit_order": visit_order,
        "path": full_path,
        "distance_km": total_distance,
        "time_hours": total_time,
        "edge_cost": cost,
        "fuel": total_fuel,
    }
