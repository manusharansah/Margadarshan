"""
Source selection (workflow doc, Steps 3/4a/4b):

  1. Single-source scan: any one warehouse with enough stock AND enough
     vehicle capacity to cover the order alone.
  2. Multi-source parallel: combinations of warehouses (multiple vehicles
     from the same warehouse may run in parallel, per the finalized
     answer) whose combined stock/capacity covers the order. All legs
     run in parallel, so the plan's arrival time is the slowest leg.
  3. Single-vehicle multi-stop (TSP): fallback for when vehicle
     availability, not stock, is the binding constraint -- one vehicle
     visits multiple warehouses in sequence (Held-Karp TSP) to
     accumulate enough units, possibly across multiple trips if the
     order exceeds one vehicle's capacity.

None of this module talks to Gemma -- it only produces candidate plans
for the reasoning layer to choose between.
"""
from itertools import combinations
from typing import Dict, List, Optional

import networkx as nx

from .feasibility import can_fulfill_alone, vehicles_needed
from .models import CandidatePlan, DeliveryRequest, RouteLeg, StorageNode
from .routing import best_route, held_karp_tsp


def find_single_source_plans(
    G: nx.Graph, nodes: Dict[str, StorageNode], request: DeliveryRequest, k: int = 3
) -> List[CandidatePlan]:
    plans = []
    for node_id, node in nodes.items():
        if node_id == request.destination or not can_fulfill_alone(node, request.quantity):
            continue
        route = best_route(G, node_id, request.destination, k=k)
        if not route:
            continue
        n_vehicles = vehicles_needed(request.quantity, node.vehicle_capacity)
        leg = RouteLeg(
            source=node_id, path=route["path"], time_hours=route["time_hours"],
            edge_cost=route["edge_cost"], units_carried=request.quantity,
            vehicles_used=n_vehicles, fuel_estimate=route["fuel"], distance_km=route["distance_km"],
        )
        plans.append(CandidatePlan(
            legs=[leg], plan_type="single_source", total_units=request.quantity,
            total_time_hours=route["time_hours"], total_edge_cost=route["edge_cost"],
            fuel_estimate=route["fuel"], vehicles_used=n_vehicles, sources_used=[node_id],
        ))
    return plans


def find_multi_source_parallel_plans(
    G: nx.Graph, nodes: Dict[str, StorageNode], request: DeliveryRequest,
    k: int = 3, max_sources: int = 4,
) -> List[CandidatePlan]:
    candidates = [
        (nid, n) for nid, n in nodes.items()
        if nid != request.destination and n.units_in_stock > 0 and n.vehicles_available > 0
    ]
    plans = []
    for r in range(2, min(max_sources, len(candidates)) + 1):
        for combo in combinations(candidates, r):
            total_stock = sum(n.units_in_stock for _, n in combo)
            total_capacity = sum(n.max_deliverable_units for _, n in combo)
            if total_stock < request.quantity or total_capacity < request.quantity:
                continue

            legs, remaining, ok = [], request.quantity, True
            for node_id, node in combo:
                if remaining <= 0:
                    break
                take = min(node.units_in_stock, node.max_deliverable_units, remaining)
                if take <= 0:
                    continue
                route = best_route(G, node_id, request.destination, k=k)
                if not route:
                    ok = False
                    break
                n_vehicles = vehicles_needed(take, node.vehicle_capacity)
                legs.append(RouteLeg(
                    source=node_id, path=route["path"], time_hours=route["time_hours"],
                    edge_cost=route["edge_cost"], units_carried=take, vehicles_used=n_vehicles,
                    fuel_estimate=route["fuel"], distance_km=route["distance_km"],
                ))
                remaining -= take

            if not ok or remaining > 0 or not legs:
                continue

            plans.append(CandidatePlan(
                legs=legs, plan_type="multi_source_parallel", total_units=request.quantity,
                total_time_hours=max(leg.time_hours for leg in legs),
                total_edge_cost=sum(leg.edge_cost for leg in legs),
                fuel_estimate=sum(leg.fuel_estimate for leg in legs),
                vehicles_used=sum(leg.vehicles_used for leg in legs),
                sources_used=[leg.source for leg in legs],
            ))
    return plans


def find_single_vehicle_multistop_plan(
    G: nx.Graph, nodes: Dict[str, StorageNode], request: DeliveryRequest,
    vehicle_home: str, vehicle_capacity: int = 200, max_pickup_stops: int = 4, k: int = 3,
) -> Optional[CandidatePlan]:
    """
    Simplifying assumption for this phase (documented in the workflow doc):
    the vehicle returns to its home node between trips if more than one
    trip is needed; the road network is treated as symmetric, so the
    return leg reuses the same edge weights in reverse.
    """
    remaining = request.quantity
    stock_pool = {nid: n.units_in_stock for nid, n in nodes.items() if nid != request.destination}
    trips = []
    total_time = total_cost = total_fuel = 0.0
    first_trip = True

    while remaining > 0:
        take_target = min(remaining, vehicle_capacity)

        def dist_from_home(nid):
            try:
                return nx.shortest_path_length(G, vehicle_home, nid, weight="weight")
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return float("inf")

        ranked = sorted(
            [(nid, s) for nid, s in stock_pool.items() if s > 0],
            key=lambda x: dist_from_home(x[0]),
        )
        picked, covered = [], 0
        for nid, stock in ranked:
            if covered >= take_target or len(picked) >= max_pickup_stops:
                break
            take = min(stock, take_target - covered)
            if take <= 0:
                continue
            picked.append((nid, take))
            covered += take

        if covered < take_target:
            return None  # network-wide stock insufficient regardless of routing

        stops = [nid for nid, _ in picked]
        pickup_leg = held_karp_tsp(G, vehicle_home, stops)
        if pickup_leg is None:
            return None
        last_stop = pickup_leg["visit_order"][-1]
        final_route = best_route(G, last_stop, request.destination, k=k)
        if not final_route:
            return None

        trip_time = pickup_leg["time_hours"] + final_route["time_hours"]
        trip_cost = pickup_leg["edge_cost"] + final_route["edge_cost"]
        trip_fuel = pickup_leg["fuel"] + final_route["fuel"]

        if not first_trip:
            return_route = best_route(G, request.destination, vehicle_home, k=1)
            if return_route:
                trip_time += return_route["time_hours"]
                trip_cost += return_route["edge_cost"]
                trip_fuel += return_route["fuel"]

        trips.append({
            "pickups": picked,
            "path": pickup_leg["visit_order"] + final_route["path"][1:],
            "time_hours": trip_time, "edge_cost": trip_cost, "fuel": trip_fuel,
            "units_carried": covered,
        })
        total_time += trip_time
        total_cost += trip_cost
        total_fuel += trip_fuel

        for nid, take in picked:
            stock_pool[nid] -= take
        remaining -= covered
        first_trip = False

    legs = [
        RouteLeg(
            source="+".join(nid for nid, _ in t["pickups"]), path=t["path"],
            time_hours=t["time_hours"], edge_cost=t["edge_cost"],
            units_carried=t["units_carried"], vehicles_used=1, fuel_estimate=t["fuel"],
        )
        for t in trips
    ]
    sources = sorted({nid for t in trips for nid, _ in t["pickups"]})

    return CandidatePlan(
        legs=legs, plan_type="single_vehicle_multistop", total_units=request.quantity,
        total_time_hours=total_time, total_edge_cost=total_cost, fuel_estimate=total_fuel,
        vehicles_used=1, sources_used=sources,
    )
