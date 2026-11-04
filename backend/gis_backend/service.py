"""
End-to-end orchestration (workflow doc, steps 2-7).

    snap destination + warehouses to the road graph
      -> generate candidate plans in priority order         (source_selector)
      -> deterministic deadline filter + policy ranking     (here)
      -> reasoner selects among feasible plans and explains (gemma_reasoner)

The recommendation is always computed in code. If Gemma is configured it may
re-select among the *feasible* plans and writes the explanation; if it is not
configured or fails, the deterministic policy choice stands and the response
says so (``explanation_source`` / ``gemma_error``) - it is never HTTP 503,
because nothing the dealer needs is missing.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from .gemma_reasoner import DeterministicReasoner, Reasoner, compact, get_reasoner
from .models import CandidatePlan, DeliveryRequest, StorageNode
from .network import Network
from .source_selector import PlanContext, generate_plans

TYPE_RANK = {"single_source": 0, "multi_source_parallel": 1, "single_vehicle_multistop": 2}
MAX_ALTERNATIVES = 6


def policy_key(p: CandidatePlan):
    """Fewest warehouses, then fewest vehicles, then lowest route cost, then fastest."""
    return (TYPE_RANK[p.plan_type], len(p.sources_used), p.vehicles_used, p.total_edge_cost, p.total_time_hours)


def plan_json(plan: CandidatePlan, with_geometry: bool = True) -> dict:
    d = asdict(plan)
    for leg in d["legs"]:
        leg.pop("path", None)
        if not with_geometry:
            leg["geometry"] = []
    for k in ("total_time_hours", "total_edge_cost", "fuel_litres", "distance_km"):
        d[k] = round(d[k], 3)
    return d


def process_request(network: Network, warehouses: List[StorageNode], request: DeliveryRequest,
                    reasoner: Optional[Reasoner] = None, weather: Optional[dict] = None) -> dict:
    warnings: List[str] = []
    dest_node, dest_snap_m = network.snap(request.destination_lat, request.destination_lng)
    dn = network.nodes[dest_node]

    usable: Dict[str, StorageNode] = {}
    for w in warehouses:
        try:
            node, dist = network.snap(w.lat, w.lng)
        except ValueError as exc:
            warnings.append(f"Warehouse {w.node_id} ({w.name}) excluded: {exc}")
            continue
        usable[w.node_id] = StorageNode(**{**asdict(w), "road_node": node, "snap_distance_m": round(dist)})
    if not usable:
        warnings.append("No warehouses are registered inside the road network. Add inventory via POST /warehouses.")

    names = {w.node_id: w.name for w in warehouses}
    nm = lambda wid: "+".join(names.get(p, p) for p in wid.split("+"))  # noqa: E731

    ctx = PlanContext(network.graph, usable, dest_node, request)
    plans = generate_plans(ctx) if usable else []
    feasible = sorted((p for p in plans if p.meets_deadline), key=policy_key)

    reasoner = reasoner or get_reasoner()
    gemma_error: Optional[str] = None
    try:
        sel = reasoner.explain(request, feasible if feasible else plans, bool(feasible), nm)
    except Exception as exc:  # noqa: BLE001 - any reasoner failure degrades to the deterministic explanation
        gemma_error = str(exc)
        sel = DeterministicReasoner().explain(request, feasible if feasible else plans, bool(feasible), nm)

    chosen = feasible[sel.index] if feasible and sel.index is not None else None
    ranked = ([chosen] + [p for p in feasible if p is not chosen]) if chosen else []
    policy_choice = feasible[0] if feasible else None

    out = {
        "status": "feasible" if chosen else "infeasible",
        "explanation": sel.explanation,
        "explanation_source": sel.source,
        "destination": {"name": request.destination_name, "lat": request.destination_lat,
                        "lng": request.destination_lng, "snapped_lat": dn.lat, "snapped_lng": dn.lng,
                        "snap_distance_m": round(dest_snap_m)},
        "chosen_plan": plan_json(chosen) if chosen else None,
        "chosen_matches_policy": chosen is policy_choice if chosen else None,
        "alternatives": [plan_json(p) for p in ranked[1:1 + MAX_ALTERNATIVES]],
        "alternatives_total": max(len(ranked) - 1, 0),
        "best_case_hours": round(min(p.total_time_hours for p in plans), 3) if plans and not chosen else None,
        "candidates_evaluated": len(plans),
        "warehouses_considered": len(usable),
        "warnings": warnings,
        "weather": weather,
        "plan_summaries": [compact(p, nm) for p in (ranked or plans)[:MAX_ALTERNATIVES + 1]],
    }
    if gemma_error:
        out["gemma_error"] = gemma_error
    return out
