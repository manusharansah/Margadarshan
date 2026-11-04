"""
Reasoning layer (workflow doc, section 6).

Gemma never computes numbers. Units, time, cost, fuel and deadline feasibility
are fixed upstream; the reasoner only (a) chooses among plans that already meet
the deadline and (b) explains the outcome in plain language.

Two implementations, both explicit and both labelled in the API response:

``LiveGemmaReasoner``
    Calls Gemma via Google AI Studio's Generative Language API (one API key).
``DeterministicReasoner``
    Applies the documented selection policy and writes a templated explanation
    from the computed figures. Used when Gemma is not configured or fails; the
    response says ``explanation_source: "deterministic"`` so it is never passed
    off as model output.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, List, Optional

import requests

from .models import CandidatePlan, DeliveryRequest

PLAN_LABEL = {
    "single_source": "a single warehouse",
    "multi_source_parallel": "several warehouses shipping in parallel",
    "single_vehicle_multistop": "one vehicle collecting from several warehouses",
}


@dataclass
class Selection:
    index: Optional[int]
    explanation: str
    source: str  # "gemma" | "deterministic"
    risk_note: str = ""


def compact(plan: CandidatePlan, name: Callable[[str], str]) -> dict:
    """Prompt/UI-sized summary: figures only, no geometry."""
    return {
        "plan_type": plan.plan_type,
        "sources": [name(s) for s in plan.sources_used],
        "vehicles": plan.vehicles_used,
        "units": plan.total_units,
        "arrival_hours": round(plan.total_time_hours, 2),
        "route_cost": round(plan.total_edge_cost, 1),
        "fuel_litres": round(plan.fuel_litres, 1),
        "distance_km": round(plan.distance_km, 1),
        "worst_hazard_0_to_1": round(max((l.max_hazard for l in plan.legs), default=0.0), 2),
        "legs": [{"from": name(l.source), "units": l.units_carried,
                  "hours": round(l.time_hours, 2), "km": round(l.distance_km, 1),
                  "riskiest_stretches": [{k: h[k] for k in ("road", "hazard", "rain_index", "grade_pct", "surface")}
                                         for h in l.hotspots]} for l in plan.legs],
    }


def deterministic_risk_note(plan: CandidatePlan) -> str:
    spots = sorted((h for l in plan.legs for h in l.hotspots), key=lambda h: -h["hazard"])[:2]
    if not spots:
        return "No road stretch on this route was flagged as hazardous."
    parts = [f"{h['road']} (hazard {h['hazard']:.2f}, {h['grade_pct']:g}% grade, {h['surface']} surface"
             + (f", rain index {h['rain_index']:.2f}" if h["rain_index"] else "") + ")" for h in spots]
    return "Watch " + " and ".join(parts) + "; allow extra margin there."


class Reasoner:
    name = "base"

    def explain(self, request: DeliveryRequest, plans: List[CandidatePlan], feasible: bool,
                name: Callable[[str], str]) -> Selection:
        raise NotImplementedError


class DeterministicReasoner(Reasoner):
    name = "deterministic"

    def explain(self, request, plans, feasible, name):
        q, d = request.quantity, request.deadline_hours
        if not feasible:
            if not plans:
                why = "no combination of warehouses has enough stock and vehicles to supply that quantity."
            else:
                best = min(plans, key=lambda p: p.total_time_hours)
                why = (f"the fastest plan found ({PLAN_LABEL[best.plan_type]}) needs {best.total_time_hours:.1f} h, "
                       f"which is {best.total_time_hours - d:.1f} h over the {d:g} h deadline.")
            return Selection(None, f"{q} units cannot reach {request.destination_name} within {d:g} h: {why}", "deterministic")
        p = plans[0]
        srcs = ", ".join(name(s) for s in p.sources_used)
        text = (
            f"Recommended: {PLAN_LABEL[p.plan_type]} ({srcs}) with {p.vehicles_used} vehicle(s). "
            f"It arrives in {p.total_time_hours:.1f} h, {d - p.total_time_hours:.1f} h inside the {d:g} h deadline, "
            f"over {p.distance_km:.0f} km using about {p.fuel_litres:.0f} L of fuel. "
        )
        hz = max((l.max_hazard for l in p.legs), default=0.0)
        text += f"The most exposed stretch has a hazard index of {hz:.2f} (0 = safe, 1 = severe). "
        if len(plans) > 1:
            text += (f"{len(plans) - 1} other plan(s) also meet the deadline; this one was ranked first for needing "
                     "the fewest warehouses and vehicles, then the lowest terrain/hazard-weighted route cost.")
        return Selection(0, text, "deterministic", deterministic_risk_note(p))


class GemmaClient:
    """Thin Google AI Studio client returning schema-constrained JSON. Needs ``GEMMA_API_KEY``."""

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    DEFAULT_MODEL = "gemma-4-26b-a4b-it"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 endpoint: Optional[str] = None, timeout: float = 30.0,
                 session: Optional[requests.Session] = None):
        self.api_key = api_key or os.environ.get("GEMMA_API_KEY")
        self.model = model or os.environ.get("GEMMA_MODEL", self.DEFAULT_MODEL)
        self.endpoint = endpoint or os.environ.get("GEMMA_ENDPOINT", self.ENDPOINT)
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate_json(self, prompt: str, schema: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("GEMMA_API_KEY is not set")
        return self._parse(self._call(prompt, schema))

    def _call(self, prompt: str, schema: dict) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                                 "responseJsonSchema": schema},
        }
        try:
            r = self._session.post(self.endpoint.format(model=self.model), params={"key": self.api_key},
                                   json=body, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.RequestException as exc:
            # Never echo the request URL (it carries the API key).
            raise RuntimeError(f"Gemma API call failed: {type(exc).__name__}") from None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("Unexpected Gemma response shape") from exc

    @staticmethod
    def _parse(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        depth, start, blocks = 0, None, []
        for i, ch in enumerate(text):  # fall back to the last balanced {...} block
            if ch == "{":
                start, depth = (i if depth == 0 else start), depth + 1
            elif ch == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    blocks.append(text[start:i + 1])
        for b in reversed(blocks):
            try:
                return json.loads(b)
            except json.JSONDecodeError:
                continue
        raise RuntimeError("Gemma did not return valid JSON")


class LiveGemmaReasoner(GemmaClient, Reasoner):
    """Selects among deadline-feasible plans and writes the explanation + grounded risk briefing."""

    name = "gemma"
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {"chosen_index": {"type": ["integer", "null"]}, "explanation": {"type": "string"},
                       "risk_note": {"type": "string"}},
        "required": ["chosen_index", "explanation"],
    }

    def explain(self, request, plans, feasible, name):
        data = self.generate_json(self._prompt(request, plans, feasible, name), self.RESPONSE_SCHEMA)
        idx = data.get("chosen_index")
        explanation = str(data.get("explanation", "")).strip()
        if not explanation:
            raise RuntimeError("Gemma returned an empty explanation")
        if feasible and (not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(plans)):
            raise RuntimeError(f"Gemma returned invalid chosen_index {idx!r} for {len(plans)} plans")
        chosen = plans[idx] if feasible else None
        risk = str(data.get("risk_note", "")).strip() or (deterministic_risk_note(chosen) if chosen else "")
        return Selection(idx if feasible else None, explanation, "gemma", risk)

    @staticmethod
    def _prompt(request, plans, feasible, name) -> str:
        body = json.dumps([compact(p, name) for p in plans], indent=1)
        task = (
            "Every plan below already meets the deadline. Choose the best one (index into the list). Prefer fewer "
            "warehouses and vehicles unless another plan is clearly safer (lower hazard) or cheaper. Also write "
            "risk_note: 1-2 sentences for the driver/dealer about the chosen plan's riskiest stretches, naming the roads "
            "from riskiest_stretches and citing their hazard, grade or rain figures. Use ONLY those listed stretches; "
            "if none are listed say no stretch was flagged."
            if feasible else
            "No plan meets the deadline. Set chosen_index to null and explain, using only the figures given, why the "
            "order cannot be fulfilled and what would have to change (deadline, quantity). Leave risk_note empty."
        )
        return (
            "You advise a dealer-delivery planner in Nepal's hills. All numbers are precomputed and verified: do not "
            "recompute or alter them; reason only over what is given. Write the explanation in 2-4 plain sentences "
            "for a non-technical dealer.\n\n"
            f"Order: {request.quantity} units to {request.destination_name} within {request.deadline_hours:g} hours.\n"
            f"Plans (0-indexed): {body}\n\n{task}\n"
            'Respond with exactly one JSON object: {"chosen_index": int|null, "explanation": string, "risk_note": string}'
        )


def get_reasoner() -> Reasoner:
    live = LiveGemmaReasoner()
    return live if live.configured else DeterministicReasoner()


def get_client() -> Optional["LiveGemmaReasoner"]:
    """The one AI object (client + reasoner) when GEMMA_API_KEY is set, else None."""
    c = LiveGemmaReasoner()
    return c if c.configured else None
