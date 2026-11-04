"""
Candidate-plan generation, following the fulfilment priority order:

  1. Single warehouse (one or several vehicles in parallel).
  2. Several warehouses shipping in parallel (arrival = slowest leg).
  3. One vehicle collecting from several warehouses (Held-Karp TSP), possibly in
     several trips, when vehicle availability - not stock - is the constraint.

A later tier is only explored when no earlier tier yields a plan that meets the
deadline. Every plan carries ``meets_deadline`` so infeasible requests can still
report the true best case. No LLM is involved here.
"""
from __future__ import annotations

from itertools import combinations
from typing import Dict, List, Optional

import networkx as nx

from .feasibility import can_fulfill_alone, vehicles_needed
from .models import CandidatePlan, DeliveryRequest, RouteLeg, StorageNode
from .routing import best_route, held_karp_tsp, path_geometry, path_hotspots, path_metrics

MAX_MULTI_CANDIDATES = 8   # nearest warehouses considered for combinations
MAX_MULTI_SOURCES = 4
MAX_PICKUP_STOPS = 4
MAX_TRIPS = 10             # one vehicle repeating more trips than this is not a realistic plan
MAX_MULTISTOP_HOMES = 3    # candidate base depots (nearest to the destination that have a vehicle)


class PlanContext:
    def __init__(self, G: nx.Graph, warehouses: Dict[str, StorageNode], dest_node: int,
                 request: DeliveryRequest, loading_hours: float = 0.0, k: int = 3,
                 sssp_cache: Optional[dict] = None):
        self.G, self.W, self.dest = G, warehouses, dest_node
        self.req, self.loading, self.k = request, loading_hours, k
        self._routes: Dict[str, Optional[dict]] = {}
        # Single-source shortest-path trees, shared across plan attempts for the same graph (the weights
        # do not depend on the request), which makes repeated trips and remedy searches cheap.
        self._sssp: dict = sssp_cache if sssp_cache is not None else {}

    def sssp(self, node: int):
        """Cheapest paths FROM ``node`` to every reachable node."""
        if node not in self._sssp:
            self._sssp[node] = nx.single_source_dijkstra(self.G, node, weight="weight")
        return self._sssp[node]

    def sssp_to(self, node: int):
        """Cheapest paths from every node TO ``node`` (roads are one-way, so this is not ``sssp`` reversed).
        ``paths[x]`` lists nodes from ``node`` back to x; reverse it to drive x -> node."""
        key = ("to", node)
        if key not in self._sssp:
            self._sssp[key] = nx.single_source_dijkstra(self.G.reverse(copy=False), node, weight="weight")
        return self._sssp[key]

    def route(self, wid: str) -> Optional[dict]:
        if wid not in self._routes:
            self._routes[wid] = best_route(self.G, self.W[wid].road_node, self.dest, self.k,
                                           self.req.deadline_hours - self.loading)
        return self._routes[wid]

    def leg(self, wid: str, units: int) -> Optional[RouteLeg]:
        r = self.route(wid)
        if r is None:
            return None
        wh = self.W[wid]
        n_vehicles = vehicles_needed(units, wh.vehicle_capacity)
        return RouteLeg(
            source=wid, path=r["path"], time_hours=r["time_hours"] + self.loading, edge_cost=r["edge_cost"],
            units_carried=units, vehicles_used=n_vehicles,
            fuel_litres=r["fuel_litres"] * n_vehicles,   # every vehicle drives the route
            distance_km=r["distance_km"], max_hazard=r["max_hazard"],
            ascent_m=r["ascent_m"], descent_m=r["descent_m"], geometry=path_geometry(self.G, r["path"]),
            pickups=[wid], hotspots=path_hotspots(self.G, r["path"]),
        )


def _plan(legs: List[RouteLeg], plan_type: str, ctx: PlanContext, time_h: float) -> CandidatePlan:
    return CandidatePlan(
        legs=legs, plan_type=plan_type, total_units=ctx.req.quantity, total_time_hours=time_h,
        total_edge_cost=sum(l.edge_cost for l in legs), fuel_litres=sum(l.fuel_litres for l in legs),
        vehicles_used=sum(l.vehicles_used for l in legs), distance_km=sum(l.distance_km for l in legs),
        sources_used=sorted({p for l in legs for p in l.pickups}),
        meets_deadline=time_h <= ctx.req.deadline_hours + 1e-9,
    )


def single_source_plans(ctx: PlanContext) -> List[CandidatePlan]:
    plans = []
    for wid, wh in ctx.W.items():
        if wh.road_node is None or not can_fulfill_alone(wh, ctx.req.quantity):
            continue
        leg = ctx.leg(wid, ctx.req.quantity)
        if leg:
            plans.append(_plan([leg], "single_source", ctx, leg.time_hours))
    return plans


def multi_source_plans(ctx: PlanContext) -> List[CandidatePlan]:
    pool = [wid for wid, w in ctx.W.items() if w.road_node is not None and w.units_in_stock > 0 and w.vehicles_available > 0]
    dist = ctx.sssp_to(ctx.dest)[0]
    pool = sorted((w for w in pool if ctx.W[w].road_node in dist), key=lambda w: dist[ctx.W[w].road_node])[:MAX_MULTI_CANDIDATES]
    plans: List[CandidatePlan] = []
    for r in range(2, min(MAX_MULTI_SOURCES, len(pool)) + 1):
        for combo in combinations(pool, r):
            ws = [ctx.W[w] for w in combo]
            if sum(w.units_in_stock for w in ws) < ctx.req.quantity or sum(w.max_deliverable_units for w in ws) < ctx.req.quantity:
                continue
            routes = [ctx.route(w) for w in combo]
            if any(x is None for x in routes):
                continue
            remaining, legs = ctx.req.quantity, []
            for wid in sorted(combo, key=lambda w: ctx.route(w)["time_hours"]):  # fastest sources first
                wh = ctx.W[wid]
                take = min(wh.units_in_stock, wh.max_deliverable_units, remaining)
                if take <= 0:
                    break
                legs.append(ctx.leg(wid, take))
                remaining -= take
            if remaining > 0 or len(legs) != r:   # smaller combos already cover unused members
                continue
            plans.append(_plan(legs, "multi_source_parallel", ctx, max(l.time_hours for l in legs)))
    return plans


def multistop_plan(ctx: PlanContext, home: str) -> Optional[CandidatePlan]:
    """One vehicle based at ``home`` collects from several warehouses, repeating trips as needed."""
    G, req = ctx.G, ctx.req
    cap = ctx.W[home].vehicle_capacity
    if -(-req.quantity // cap) > MAX_TRIPS:
        return None
    home_node = ctx.W[home].road_node
    pool = {w: s.units_in_stock for w, s in ctx.W.items() if s.road_node is not None}
    dist_home = ctx.sssp(home_node)[0]
    dest_paths = ctx.sssp_to(ctx.dest)[1]    # paths x -> destination, stored destination-first
    remaining, legs, total_time, prev_end = req.quantity, [], 0.0, None

    while remaining > 0:
        target = min(remaining, cap)
        ranked = sorted((w for w, s in pool.items() if s > 0 and ctx.W[w].road_node in dist_home),
                        key=lambda w: dist_home[ctx.W[w].road_node])
        picked, covered = [], 0
        for w in ranked:
            if covered >= target or len(picked) >= MAX_PICKUP_STOPS:
                break
            take = min(pool[w], target - covered)
            picked.append((w, take))
            covered += take
        if covered < target:
            return None
        tsp = held_karp_tsp(G, home_node, [ctx.W[w].road_node for w, _ in picked], sssp=ctx.sssp)
        if not tsp or tsp["visit_order"][-1] not in dest_paths:
            return None
        path = tsp["path"] + dest_paths[tsp["visit_order"][-1]][::-1][1:]
        m = path_metrics(G, path)
        trip_time = m["time_hours"] + ctx.loading * len(picked)
        fuel, cost = m["fuel_litres"], m["edge_cost"]
        if prev_end is not None:  # return from the previous delivery before starting this trip
            back = ctx.sssp(ctx.dest)[1].get(home_node)       # drive destination -> home
            if back:
                bm = path_metrics(G, back)
                trip_time, fuel, cost = trip_time + bm["time_hours"], fuel + bm["fuel_litres"], cost + bm["edge_cost"]
        order = [next(w for w, _ in picked if ctx.W[w].road_node == n) for n in tsp["visit_order"][1:]]
        legs.append(RouteLeg(
            source="+".join(order), path=path, time_hours=trip_time, edge_cost=cost, units_carried=covered,
            vehicles_used=1, fuel_litres=fuel, distance_km=m["distance_km"], max_hazard=m["max_hazard"],
            ascent_m=m["ascent_m"], descent_m=m["descent_m"], geometry=path_geometry(G, path), pickups=order,
            hotspots=path_hotspots(G, path),
        ))
        total_time += trip_time
        for w, take in picked:
            pool[w] -= take
        remaining -= covered
        prev_end = ctx.dest

    plan = _plan(legs, "single_vehicle_multistop", ctx, total_time)
    plan.vehicles_used = 1
    return plan


def generate_plans(ctx: PlanContext) -> List[CandidatePlan]:
    if sum(w.units_in_stock for w in ctx.W.values() if w.road_node is not None) < ctx.req.quantity:
        return []  # not enough stock anywhere in the network: no plan can exist, skip all routing
    plans = single_source_plans(ctx)
    if any(p.meets_deadline for p in plans):
        return plans
    multi = multi_source_plans(ctx)
    plans += multi
    if any(p.meets_deadline for p in multi):
        return plans
    dest_dist = ctx.sssp_to(ctx.dest)[0]
    homes = sorted((w for w, s in ctx.W.items() if s.road_node in dest_dist and s.vehicles_available > 0),
                   key=lambda w: dest_dist[ctx.W[w].road_node])[:MAX_MULTISTOP_HOMES]
    stops = [p for p in (multistop_plan(ctx, h) for h in homes) if p]
    if stops:
        plans.append(min(stops, key=lambda p: p.total_time_hours))
    return plans
