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

import json
import math
import re
import time
from dataclasses import asdict, replace
from typing import Callable, Dict, List, Optional

from .gemma_reasoner import (
    DeterministicReasoner, GemmaClient, Reasoner, compact, deterministic_risk_note, get_reasoner,
)
from .models import CandidatePlan, DeliveryRequest, StorageNode
from .network import Network
from .source_selector import PlanContext, generate_plans
from .uncertainty import draw_parameters, reliability

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


REMEDY_BUDGET_SECONDS = 8.0   # cap on the quantity search for "largest order that still fits"

REMEDY_SCHEMA = {
    "type": "object",
    "properties": {"proposals": {"type": "array", "maxItems": 3, "items": {
        "type": "object",
        "properties": {"quantity": {"type": "integer"}, "deadline_hours": {"type": "number"},
                       "rationale": {"type": "string"}},
        "required": ["quantity", "deadline_hours", "rationale"]}}},
    "required": ["proposals"],
}


def _rank(plans: List[CandidatePlan]) -> List[CandidatePlan]:
    return sorted((p for p in plans if p.meets_deadline), key=policy_key)


def verify_remedy(network: Network, usable: Dict[str, StorageNode], dest_node: int, req: DeliveryRequest,
                  nm: Callable[[str], str], source: str, kind: str, rationale: str,
                  cache: Optional[dict] = None, graph=None, loading: float = 0.0) -> Optional[dict]:
    """Run the real planner on an alternative order; return it only if it is genuinely feasible."""
    if req.quantity <= 0 or not (0 < req.deadline_hours <= 720):
        return None
    best = _rank(generate_plans(PlanContext(graph if graph is not None else network.graph, usable, dest_node, req,
                                            loading, sssp_cache=cache)))
    if not best:
        return None
    p = best[0]
    return {"source": source, "kind": kind, "quantity": req.quantity, "deadline_hours": round(req.deadline_hours, 2),
            "eta_hours": round(p.total_time_hours, 2), "plan_type": p.plan_type,
            "sources": [nm(x) for x in p.sources_used], "vehicles": p.vehicles_used, "rationale": rationale,
            "verified": True}


def computed_remedies(network, usable, dest_node, request, plans, nm, cache: Optional[dict] = None, graph=None,
                      loading: float = 0.0) -> List[dict]:
    """Code-derived options (no AI): a longer deadline, or a smaller quantity that fits the deadline.

    The quantity comes from bisection. Whether an order is deliverable is not strictly monotone in its
    size (different warehouse combinations apply at different sizes), so the result is a quantity the
    planner has verified, not a guaranteed maximum."""
    out: List[dict] = []
    if plans:
        h = min(720.0, math.ceil(min(p.total_time_hours for p in plans) * 2) / 2)
        r = verify_remedy(network, usable, dest_node, replace(request, deadline_hours=h), nm, "computed",
                          "extend_deadline", f"Same {request.quantity} units, deadline moved to {h:g} h.", cache, graph, loading)
        if r:
            out.append(r)
    lo, hi, best_q = 0, min(request.quantity - 1, sum(w.units_in_stock for w in usable.values())), 0
    started = time.monotonic()
    for _ in range(8):  # bisection on quantity; every candidate is run through the real planner
        if hi <= lo or time.monotonic() - started > REMEDY_BUDGET_SECONDS:
            break
        mid = (lo + hi + 1) // 2
        if _rank(generate_plans(PlanContext(graph if graph is not None else network.graph, usable, dest_node,
                                            replace(request, quantity=mid), loading, sssp_cache=cache))):
            lo = best_q = mid
        else:
            hi = mid - 1
    if best_q > 0:
        r = verify_remedy(network, usable, dest_node, replace(request, quantity=best_q), nm, "computed",
                          "reduce_quantity",
                          f"Ship {best_q} units now within {request.deadline_hours:g} h; {request.quantity - best_q} remain.", cache, graph, loading)
        if r:
            out.append(r)
    return out


def gemma_remedies(client: GemmaClient, network, usable, dest_node, request, plans, anchors, nm,
                   cache: Optional[dict] = None, graph=None, loading: float = 0.0) -> List[dict]:
    """Gemma proposes business-sensible alternatives; each is accepted only if the planner proves it feasible."""
    facts = {
        "order": {"quantity": request.quantity, "deadline_hours": request.deadline_hours,
                  "destination": request.destination_name},
        "fastest_possible_hours": round(min((p.total_time_hours for p in plans), default=0), 2) or None,
        "network_stock_units": sum(w.units_in_stock for w in usable.values()),
        "network_vehicles": sum(w.vehicles_available for w in usable.values()),
        "already_verified_options": [{k: a[k] for k in ("kind", "quantity", "deadline_hours")} for a in anchors],
        "closest_plans": [compact(p, nm) for p in sorted(plans, key=lambda p: p.total_time_hours)[:3]],
    }
    prompt = (
        "A dealer's order cannot be delivered as requested. Propose up to 3 DIFFERENT practical alternatives a dealer "
        "might accept - e.g. a partial shipment now with the rest later, a modestly later deadline, or a smaller order. "
        "Each has an integer quantity (<= the original), a deadline_hours, and a one-sentence rationale in plain language "
        "that contains NO digits and no numbers written as words (the quantity and deadline are shown separately). "
        "Do not repeat the already-verified options. Do not claim any option works: they will be checked by the "
        f"routing engine.\nFacts: {json.dumps(facts)}\n"
        'Respond with exactly one JSON object: {"proposals": [{"quantity": int, "deadline_hours": number, "rationale": string}]}'
    )
    data = client.generate_json(prompt, REMEDY_SCHEMA)
    seen = {(a["quantity"], a["deadline_hours"]) for a in anchors}
    out: List[dict] = []
    for pr in data.get("proposals", [])[:3]:
        try:
            q, h, why = int(pr["quantity"]), float(pr["deadline_hours"]), str(pr["rationale"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 < q <= request.quantity) or (q, round(h, 2)) in seen or re.search(r"\d", why) or not why:
            continue   # a rationale with a self-typed number is unverifiable, so the proposal is dropped
        r = verify_remedy(network, usable, dest_node, replace(request, quantity=q, deadline_hours=h), nm, "gemma",
                          "split_or_adjust", why, cache, graph, loading)
        if r:
            seen.add((q, r["deadline_hours"]))
            out.append(r)
    return out


_DRAWS = draw_parameters(300)


def _with_reliability(d: dict, network: Network, plan: CandidatePlan, request: DeliveryRequest) -> dict:
    d["reliability"] = reliability(network.graph, plan, request.deadline_hours, _DRAWS)
    return d


def process_request(network: Network, warehouses: List[StorageNode], request: DeliveryRequest,
                    reasoner: Optional[Reasoner] = None, weather: Optional[dict] = None,
                    client: Optional[GemmaClient] = None, defer_ai: bool = False, graph=None,
                    loading_hours: float = 0.0) -> dict:
    """``defer_ai=True`` returns the computed answer at once (rule-based text, computed remedies) with
    ``ai.status == "pending"`` and a private ``_ctx``; ``enrich_with_ai`` then adds the model's contribution.
    Model calls take 15 to 60 s on the free tier, so the dealer's answer never waits for them."""
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

    ctx = PlanContext(graph if graph is not None else network.graph, usable, dest_node, request, loading_hours)
    plans = generate_plans(ctx) if usable else []
    feasible = sorted((p for p in plans if p.meets_deadline), key=policy_key)

    deferred = defer_ai and client is not None
    reasoner = DeterministicReasoner() if deferred else (reasoner or get_reasoner())
    gemma_error: Optional[str] = None
    try:
        sel = reasoner.explain(request, feasible if feasible else plans, bool(feasible), nm)
    except Exception as exc:  # noqa: BLE001 - any reasoner failure degrades to the deterministic explanation
        gemma_error = str(exc)
        sel = DeterministicReasoner().explain(request, feasible if feasible else plans, bool(feasible), nm)

    chosen = feasible[sel.index] if feasible and sel.index is not None else None
    ranked = ([chosen] + [p for p in feasible if p is not chosen]) if chosen else []
    policy_choice = feasible[0] if feasible else None

    remedies: List[dict] = []
    remedy_error: Optional[str] = None
    if not chosen and usable:
        cache: dict = ctx._sssp  # shortest-path trees already computed for this request
        remedies = computed_remedies(network, usable, dest_node, request, plans, nm, cache, ctx.G, loading_hours)
        if client is not None and not deferred:
            try:
                remedies += gemma_remedies(client, network, usable, dest_node, request, plans, remedies, nm, cache, ctx.G,
                                           loading_hours)
            except Exception as exc:  # noqa: BLE001 - AI proposals are a bonus; computed options still stand
                remedy_error = str(exc)

    out = {
        "status": "feasible" if chosen else "infeasible",
        "risk_note": sel.risk_note or (deterministic_risk_note(chosen) if chosen else ""),
        "risk_note_source": "deterministic",
        "remedies": remedies,
        "explanation": sel.explanation,
        "explanation_source": sel.source,
        "destination": {"name": request.destination_name, "lat": request.destination_lat,
                        "lng": request.destination_lng, "snapped_lat": dn.lat, "snapped_lng": dn.lng,
                        "snap_distance_m": round(dest_snap_m)},
        "chosen_plan": _with_reliability(plan_json(chosen), network, chosen, request) if chosen else None,
        "chosen_matches_policy": chosen is policy_choice if chosen else None,
        "alternatives": [_with_reliability(plan_json(p), network, p, request) for p in ranked[1:1 + MAX_ALTERNATIVES]],
        "alternatives_total": max(len(ranked) - 1, 0),
        "best_case_hours": round(min(p.total_time_hours for p in plans), 3) if plans and not chosen else None,
        "candidates_evaluated": len(plans),
        "warehouses_considered": len(usable),
        "warnings": warnings,
        "weather": weather,
        "plan_summaries": [compact(p, nm) for p in (ranked or plans)[:MAX_ALTERNATIVES + 1]],
    }
    if deferred:
        out["ai"] = {"status": "pending"}
        out["_ctx"] = {"network": network, "usable": usable, "dest_node": dest_node, "request": request, "plans": plans,
                       "feasible": feasible, "nm": nm, "cache": ctx._sssp, "computed_remedies": remedies,
                       "graph": ctx.G, "loading_hours": loading_hours}
    if gemma_error:
        out["gemma_error"] = gemma_error
    if remedy_error:
        out["remedy_error"] = remedy_error
    return out


def enrich_with_ai(ctx: dict, client: GemmaClient, reasoner: Optional[Reasoner] = None) -> dict:
    """The model's contribution to an already-computed answer. Never raises: failures come back as ``ai.status``.

    The explanation call and the remedy-proposal call run in parallel. Both are guarded: the chosen index must be a
    deadline-feasible plan, the text may contain no model-typed number, and every proposal is re-run through the planner."""
    from concurrent.futures import ThreadPoolExecutor

    req, feasible, plans, nm = ctx["request"], ctx["feasible"], ctx["plans"], ctx["nm"]
    reasoner = reasoner or client  # LiveGemmaReasoner is both
    pool = [feasible if feasible else plans]
    patch: dict = {"ai": {"status": "done"}}

    def explain():
        return reasoner.explain(req, pool[0], bool(feasible), nm)

    def propose():
        if feasible or not ctx["usable"]:
            return []
        return gemma_remedies(client, ctx["network"], ctx["usable"], ctx["dest_node"], req, plans,
                              ctx["computed_remedies"], nm, ctx["cache"], ctx["graph"], ctx["loading_hours"])

    with ThreadPoolExecutor(2) as ex:
        f_explain, f_remedy = ex.submit(explain), ex.submit(propose)
        try:
            sel = f_explain.result()
            patch.update(explanation=sel.explanation, explanation_source="gemma", risk_note=sel.risk_note,
                         risk_note_source=sel.risk_source)
            if feasible and sel.index is not None and sel.index != 0:
                chosen = feasible[sel.index]
                others = [p for p in feasible if p is not chosen]
                patch.update(chosen_plan=_with_reliability(plan_json(chosen), ctx["network"], chosen, req), chosen_matches_policy=False,
                             alternatives=[_with_reliability(plan_json(p), ctx["network"], p, req) for p in others[:MAX_ALTERNATIVES]],
                             alternatives_total=len(others))
            elif feasible:
                patch["chosen_matches_policy"] = True
        except Exception as exc:  # noqa: BLE001
            patch["ai"] = {"status": "partial", "error": str(exc)}
        try:
            extra = f_remedy.result()
            if extra:
                patch["remedies"] = ctx["computed_remedies"] + extra
        except Exception as exc:  # noqa: BLE001
            patch["ai"] = {"status": "partial", "error": str(exc)}
    return patch
