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
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

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
    risk_source: str = "deterministic"


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


def _response_text(r: requests.Response) -> str:
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


def _finish_reason(r: requests.Response) -> str:
    try:
        return str(r.json()["candidates"][0].get("finishReason", "no reason given"))
    except (KeyError, IndexError, TypeError, ValueError):
        return "no candidates"


def _api_error_message(r: requests.Response) -> str:
    try:
        return str((r.json().get("error") or {}).get("message", ""))[:160]
    except ValueError:
        return ""


# ------------------------------------------------------------------ number guard
PLACEHOLDER = re.compile(r"\{([a-z_0-9]+)\}")


# Placeholders already carry their unit ("45 min"); models sometimes add it again ("45 min hours", "31 km km").
_DUP_UNIT = re.compile(r"(\b\d+(?:\.\d+)? (?:min|h|km|L|%))\s+(?:minutes?|hours?|hrs?|kilomet(?:re|er)s?|km|litres?|liters?|percent)\b")


def _placeholder_problem(text: str, values: Dict[str, str]) -> str:
    if re.search(r"\d", PLACEHOLDER.sub("", text)):
        return "it contained a digit"
    unknown = sorted(set(PLACEHOLDER.findall(text)) - set(values))
    return f"unknown placeholder {', '.join(unknown)}" if unknown else "empty or malformed"


def fill_placeholders(text: str, values: Dict[str, str]) -> Optional[str]:
    """Insert verified figures for ``{name}`` placeholders. Returns None if the model wrote any digit of its own
    or used a placeholder we did not offer: a model-typed number can never reach a dealer."""
    if re.search(r"\d", PLACEHOLDER.sub("", text)):
        return None
    names = set(PLACEHOLDER.findall(text))
    if not names <= set(values):
        return None
    out = PLACEHOLDER.sub(lambda m: values[m.group(1)], text)
    out = _DUP_UNIT.sub(r"\1", out)
    return None if "{" in out or "}" in out else out


class GemmaClient:
    """Thin Google AI Studio client returning schema-constrained JSON. Needs ``GEMMA_API_KEY``."""

    ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    DEFAULT_MODEL = "gemma-4-26b-a4b-it"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 endpoint: Optional[str] = None, timeout: float = 90.0,
                 session: Optional[requests.Session] = None, max_attempts: int = 3, backoff_seconds: float = 2.0):
        self.api_key = api_key or os.environ.get("GEMMA_API_KEY")
        self.model = model or os.environ.get("GEMMA_MODEL", self.DEFAULT_MODEL)
        self.endpoint = endpoint or os.environ.get("GEMMA_ENDPOINT", self.ENDPOINT)
        self.timeout = timeout
        self.max_attempts, self.backoff_seconds = max_attempts, backoff_seconds
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate_json(self, prompt: str, schema: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("GEMMA_API_KEY is not set")
        return self._parse(self._call(prompt, schema))

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def _call(self, prompt: str, schema: dict) -> str:
        last = "no attempt"
        empties = 0
        for attempt in range(self.max_attempts):
            # Retries nudge the temperature: an empty RECITATION/SAFETY response repeats at the same setting.
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1 + 0.4 * empties, "responseMimeType": "application/json",
                                     "responseJsonSchema": schema},
            }
            try:
                r = self._session.post(self.endpoint.format(model=self.model), params={"key": self.api_key},
                                       json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                # Never echo the request URL (it carries the API key).
                last = f"{type(exc).__name__}"
            else:
                if r.status_code == 200:
                    text = _response_text(r)
                    if text:
                        return text
                    last = f"empty response ({_finish_reason(r)})"
                    empties += 1
                    if empties >= 2:
                        break   # the same refusal twice will not change; do not make the dealer wait
                else:
                    last = f"HTTP {r.status_code}: {_api_error_message(r)}"
                    if r.status_code not in self.RETRY_STATUSES:
                        break
            if attempt + 1 < self.max_attempts:
                time.sleep(self.backoff_seconds * (2 ** attempt))
        raise RuntimeError(f"Gemma API call failed: {last}")

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


def format_hours(h: float) -> str:
    total = round(h * 60)
    if total < 1:
        return "under a minute"
    hh, mm = divmod(total, 60)
    return f"{mm} min" if hh == 0 else f"{hh} h" if mm == 0 else f"{hh} h {mm} min"


def plan_values(plan: CandidatePlan, request: DeliveryRequest, name: Callable[[str], str]) -> Dict[str, str]:
    """Verified figures a model may reference by placeholder (it is never allowed to type a number)."""
    spots = sorted((h for l in plan.legs for h in l.hotspots), key=lambda h: -h["hazard"])[:3]
    vals = {
        "arrival": format_hours(plan.total_time_hours), "vehicles": str(plan.vehicles_used),
        "distance": f"{plan.distance_km:.0f} km", "fuel": f"{plan.fuel_litres:.0f} L",
        "deadline": format_hours(request.deadline_hours),
        "margin": format_hours(request.deadline_hours - plan.total_time_hours),
        "quantity": f"{request.quantity:,}", "sources": ", ".join(name(x) for x in plan.sources_used),
        "hazard": f"{max((l.max_hazard for l in plan.legs), default=0.0):.2f}",
    }
    for i, h in enumerate(spots, 1):
        vals.update({f"road{i}": h["road"], f"hazard{i}": f"{h['hazard']:.2f}", f"grade{i}": f"{h['grade_pct']:g}%"})
    return vals


class LiveGemmaReasoner(GemmaClient, Reasoner):
    """Selects among deadline-feasible plans and writes the explanation and a grounded risk briefing.

    The model never types a figure. It writes with placeholders such as ``{arrival}`` and ``{road1}``, and code
    fills them from the verified plan. Text containing any digit of its own is rejected."""

    name = "gemma"
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {"chosen_index": {"type": ["integer", "null"]}, "explanation": {"type": "string"},
                       "risk_note": {"type": "string"}},
        "required": ["chosen_index", "explanation"],
    }

    def explain(self, request, plans, feasible, name):
        prompt = self._prompt(request, plans, feasible, name)
        last_problem = ""
        for attempt in range(2):
            data = self.generate_json(prompt, self.RESPONSE_SCHEMA)
            idx = data.get("chosen_index")
            if feasible and (not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(plans)):
                raise RuntimeError(f"Gemma returned invalid chosen_index {idx!r} for {len(plans)} plans")
            chosen = plans[idx] if feasible else None
            if chosen:
                values = plan_values(chosen, request, name)
            else:
                best = min((p.total_time_hours for p in plans), default=None)
                values = {"quantity": f"{request.quantity:,}", "deadline": format_hours(request.deadline_hours),
                          **({"best_case": format_hours(best)} if best is not None else {})}
            raw = str(data.get("explanation", "")).strip()
            explanation = fill_placeholders(raw, values)
            if explanation:
                risk_raw = str(data.get("risk_note", "")).strip()
                risk = fill_placeholders(risk_raw, values) if risk_raw else None   # unverifiable text is dropped
                sel = Selection(idx if feasible else None, explanation, "gemma",
                                risk or (deterministic_risk_note(chosen) if chosen else ""))
                sel.risk_source = "gemma" if risk else "deterministic"
                return sel
            last_problem = _placeholder_problem(raw, values)
            prompt += (f"\n\nYour previous explanation was rejected ({last_problem}). Write it again with no digits "
                       f"and only these placeholders: {' '.join('{' + k + '}' for k in values)}.")
        raise RuntimeError(f"Gemma's explanation was rejected twice ({last_problem}): {raw[:140]!r}")

    @staticmethod
    def _prompt(request, plans, feasible, name) -> str:
        body = json.dumps([compact(p, name) for p in plans], indent=1)
        if feasible:
            task = (
                "Every plan below already meets the deadline. Choose the best one (chosen_index = its position in the "
                "list). Prefer fewer warehouses and vehicles unless another plan is clearly safer (lower hazard).\n"
                "Write explanation (2-3 plain sentences for a non-technical dealer) and risk_note (1-2 sentences for the "
                "driver about the riskiest stretches of the plan you chose).\n"
                "STRICT RULE: never write a digit or spell out a quantity. Each placeholder already includes its unit, so never add one "
                "(write {arrival}, not {arrival} hours). Refer to figures only with these placeholders, "
                "which refer to the plan you chose: {arrival} {vehicles} {distance} {fuel} {deadline} {margin} {sources} "
                "{hazard}, and for the risk note {road1} {hazard1} {grade1} {road2} {hazard2} {grade2}. Write {road1} "
                "instead of a road name. Use only what the plans show; do not invent causes or events."
            )
        else:
            task = (
                "No plan meets the deadline. Set chosen_index to null. Write explanation (2-3 plain sentences): why it "
                "cannot be delivered and what would have to change. Leave risk_note empty.\n"
                "STRICT RULE: never write a digit. Refer to figures only with these placeholders: {quantity} {deadline} "
                "{best_case}."
            )
        return (
            "You advise a dealer-delivery planner in Nepal's hills. All figures are precomputed and verified.\n\n"
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
