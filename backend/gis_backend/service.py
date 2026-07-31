"""
End-to-end orchestration (workflow doc, Steps 2-6). Given the network data
and a delivery request, this builds the weighted graph, runs source
selection in priority order (single source -> multi-source parallel ->
single-vehicle multi-stop), then hands the feasible candidates to the
Gemma reasoning layer for final selection and explanation.
"""
from dataclasses import asdict
from typing import Dict, List, Optional

import networkx as nx

from .gemma_reasoner import GemmaReasoner, get_reasoner
from .models import CandidatePlan, DeliveryRequest, RoadEdge, StorageNode
from .routing import build_graph
from .source_selector import (
    find_multi_source_parallel_plans,
    find_single_source_plans,
    find_single_vehicle_multistop_plan,
)


def _plan_summary(plan: CandidatePlan) -> dict:
    return {
        "plan_type": plan.plan_type,
        "sources_used": plan.sources_used,
        "vehicles_used": plan.vehicles_used,
        "total_units": plan.total_units,
        "total_time_hours": round(plan.total_time_hours, 2),
        "total_edge_cost": round(plan.total_edge_cost, 2),
        "fuel_estimate": round(plan.fuel_estimate, 2),
        "legs": [asdict(leg) for leg in plan.legs],
    }


def process_request(
    nodes: Dict[str, StorageNode],
    edges: List[RoadEdge],
    request: DeliveryRequest,
    reasoner: Optional[GemmaReasoner] = None,
    graph: Optional[nx.Graph] = None,
) -> dict:
    """
    `graph` lets a caller (e.g. the API) pass in a graph that was already
    built once at startup, so edge weights aren't recomputed on every
    request. If omitted (e.g. demo.py, tests), a graph is built fresh here
    as before.
    """
    G = graph if graph is not None else build_graph(nodes, edges)
    reasoner = reasoner or get_reasoner()

    # Step 3/4a -- single source first (fulfillment priority #1/#2)
    plans = find_single_source_plans(G, nodes, request)

    # Step 4b -- multi-source parallel, only if no single source suffices
    if not plans:
        plans = find_multi_source_parallel_plans(G, nodes, request)

    # Vehicle-scarcity fallback -- single vehicle, multi-stop pickup (TSP)
    if not plans:
        vehicle_home = next((nid for nid, n in nodes.items() if n.vehicles_available > 0), None)
        if vehicle_home:
            multistop = find_single_vehicle_multistop_plan(G, nodes, request, vehicle_home)
            if multistop:
                plans = [multistop]

    # Step 5/6 -- deterministic feasibility already baked into each plan's
    # totals; Gemma only selects among candidates and explains the result.
    #
    # Important: if the live reasoning call fails, we do NOT silently
    # substitute a rule-based guess and present it as the answer. Every
    # number here is deterministic and verified (routing, feasibility), but
    # the plan *selection* and *explanation* are Gemma's job specifically --
    # so if Gemma can't be reached, the honest answer is "reasoning
    # unavailable," not a stand-in opinion dressed up as Gemma's.
    try:
        result = reasoner.select_and_explain(request, plans)
    except Exception as exc:
        return {
            "status": "reasoning_unavailable",
            "explanation": (
                "The Gemma reasoning/explanation layer could not be reached, so no plan "
                "was selected. All candidate plans below were computed deterministically "
                "(routing, feasibility, cost) and are verified -- only the final "
                "selection and explanation step is missing."
            ),
            "gemma_error": str(exc),
            "chosen_plan": None,
            "chosen_plan_object": None,
            "candidate_plans": [_plan_summary(p) for p in plans],
            "reasoner_used": "unavailable",
        }

    result["reasoner_used"] = "live_gemma"
    return result
