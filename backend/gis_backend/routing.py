"""
Graph routing: Yen's K-shortest paths and exact Held-Karp TSP.

Pure graph math - nothing here calls an LLM. All metrics are summed from the
per-edge values on the road graph (length, time, cost, direction-aware fuel).
"""
from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional

import networkx as nx

from .models import LatLngElev


def _fuel(data: dict, u: int) -> float:
    return data["fuel_ab"] if u == data["a"] else data["fuel_ba"]


def path_metrics(G: nx.Graph, path: List[int]) -> Dict[str, float]:
    dist = time_h = cost = fuel = ascent = descent = hazard = 0.0
    for u, v in zip(path, path[1:]):
        d = G[u][v]
        e = d["edge"]
        fwd = u == d["a"]
        dist += d["length"]
        time_h += d["time"]
        cost += d["weight"]
        fuel += _fuel(d, u)
        ascent += e.ascent_ab_m if fwd else e.descent_ab_m
        descent += e.descent_ab_m if fwd else e.ascent_ab_m
        hazard = max(hazard, d["hazard"])
    return {"distance_km": dist, "time_hours": time_h, "edge_cost": cost, "fuel_litres": fuel,
            "ascent_m": ascent, "descent_m": descent, "max_hazard": hazard}


def path_geometry(G: nx.Graph, path: List[int]) -> List[LatLngElev]:
    """Road polyline (lat, lng, elevation) for a node path, oriented along travel."""
    if len(path) == 1:
        n = G.nodes[path[0]]
        return [(n["lat"], n["lng"], n["elev"])]
    out: List[LatLngElev] = []
    for u, v in zip(path, path[1:]):
        d = G[u][v]
        geom = list(d["edge"].geometry)
        if u != d["a"]:
            geom.reverse()
        out.extend(geom if not out else geom[1:])
    return out


def k_shortest_paths(G: nx.Graph, source: int, target: int, k: int = 3) -> List[List[int]]:
    """Yen's algorithm (networkx ``shortest_simple_paths``), ranked by routing cost."""
    if source not in G or target not in G:
        return []
    try:
        gen = nx.shortest_simple_paths(G, source, target, weight="weight")
        out: List[List[int]] = []
        for p in gen:
            out.append(p)
            if len(out) >= k:
                break
        return out
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def best_route(G: nx.Graph, source: int, target: int, k: int = 3,
               deadline_hours: Optional[float] = None) -> Optional[dict]:
    """
    The cheapest route that meets the deadline; if none can, the fastest route
    (so callers can report the true best-case time).

    Equivalent to scoring Yen's K cheapest paths plus the minimum-time path, but
    evaluated lazily because Yen's algorithm is the expensive part on a
    country-scale graph:
      1. Yen's 1st path *is* the cheapest path - if it meets the deadline it wins.
      2. If even the minimum-time path misses the deadline, nothing can: stop.
      3. Otherwise search Yen's K cheapest for one that meets the deadline,
         falling back to the minimum-time path.
    """
    if source == target:
        return {"path": [source], **path_metrics(G, [source]), "candidates": 1}
    try:
        cheapest = nx.dijkstra_path(G, source, target, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    m = path_metrics(G, cheapest)
    if deadline_hours is None or m["time_hours"] <= deadline_hours:
        return {"path": cheapest, **m, "candidates": 1}
    fastest = nx.dijkstra_path(G, source, target, weight="time")
    fm = path_metrics(G, fastest)
    if fm["time_hours"] > deadline_hours:
        return {"path": fastest, **fm, "candidates": 2}
    scored = [(p, path_metrics(G, p)) for p in k_shortest_paths(G, source, target, k)]
    ok = [x for x in scored if x[1]["time_hours"] <= deadline_hours]
    path, mm = min(ok, key=lambda x: x[1]["edge_cost"]) if ok else (fastest, fm)
    if ok and fm["edge_cost"] < mm["edge_cost"]:
        path, mm = fastest, fm
    return {"path": path, **mm, "candidates": len(scored) + 1}


def held_karp_tsp(G: nx.Graph, start: int, stops: List[int]) -> Optional[dict]:
    """
    Exact open-path TSP: begin at ``start`` and visit every stop once, minimising
    routing cost. Pairwise distances are shortest-path costs in G.
    O(n^2 2^n); intended for the handful of warehouses a single vehicle visits.
    """
    stops = list(dict.fromkeys(s for s in stops if s != start))
    if not stops:
        return {"visit_order": [start], "path": [start], "edge_cost": 0.0}
    pts = [start] + stops
    n = len(pts)

    pair_cost: Dict[tuple, float] = {}
    pair_path: Dict[tuple, List[int]] = {}
    for i in range(n):
        lengths, paths = nx.single_source_dijkstra(G, pts[i], weight="weight")
        for j in range(n):
            if i == j:
                continue
            if pts[j] not in lengths:
                return None
            pair_cost[(i, j)] = lengths[pts[j]]
            pair_path[(i, j)] = paths[pts[j]]

    idx = list(range(1, n))
    dp: Dict[tuple, tuple] = {(frozenset([j]), j): (pair_cost[(0, j)], 0) for j in idx}
    for size in range(2, len(idx) + 1):
        for subset in combinations(idx, size):
            fs = frozenset(subset)
            for j in subset:
                prev = fs - {j}
                best = min(((dp[(prev, k)][0] + pair_cost[(k, j)], k) for k in prev), default=None)
                if best:
                    dp[(fs, j)] = best
    full = frozenset(idx)
    cost, last = min((dp[(full, j)][0], j) for j in idx)
    order, subset = [last], full
    while len(subset) > 1:
        prev = dp[(subset, last)][1]
        order.append(prev)
        subset, last = subset - {last}, prev
    order.reverse()

    visit = [0] + order
    path = [pts[0]]
    for a, b in zip(visit, visit[1:]):
        path.extend(pair_path[(a, b)][1:])
    return {"visit_order": [pts[i] for i in visit], "path": path, "edge_cost": cost}


def path_hotspots(G: nx.Graph, path: List[int], k: int = 3) -> List[dict]:
    """The k riskiest distinct road stretches on a path (grounding for the risk briefing)."""
    best: Dict[str, dict] = {}
    for u, v in zip(path, path[1:]):
        d = G[u][v]
        e = d["edge"]
        label = e.name or f"unnamed {e.highway} road"
        mid = e.geometry[len(e.geometry) // 2]
        cur = best.get(label)
        if cur is None or d["hazard"] > cur["hazard"]:
            best[label] = {"road": label, "hazard": round(d["hazard"], 2), "rain_index": round(d["rain_index"], 2),
                           "grade_pct": round(e.slope_pct, 1), "surface": e.road_type,
                           "lat": round(mid[0], 5), "lng": round(mid[1], 5)}
    return sorted(best.values(), key=lambda h: -h["hazard"])[:k]
