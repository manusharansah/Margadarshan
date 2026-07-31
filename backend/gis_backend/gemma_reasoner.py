"""
Gemma 4 reasoning layer (workflow doc, Section 6).

Gemma is never used to compute totals -- units, time, edge cost, and fuel
are always produced deterministically upstream (source_selector.py,
feasibility.py). Gemma's job is limited to:
  (a) selecting the best plan among the already-feasible candidates, and
  (b) explaining that choice in plain language, or explaining why no
      feasible plan exists.

Two implementations:
  - MockGemmaReasoner: deterministic, offline stand-in used when no
    GEMMA_API_KEY is configured -- lets the rest of the backend be built
    and demoed without a Gemma API key.
  - LiveGemmaReasoner: calls Gemma via Google AI Studio's Generative
    Language API (a single API key, no GCP project needed). See its
    docstring below for setup.
"""
import json
import os
from dataclasses import asdict
from typing import List, Optional

import requests

from .models import CandidatePlan, DeliveryRequest


def _plan_summary(plan: CandidatePlan) -> dict:
    return {
        "plan_type": plan.plan_type,
        "sources_used": plan.sources_used,
        "vehicles_used": plan.vehicles_used,
        "total_units": plan.total_units,
        "total_time_hours": round(plan.total_time_hours, 2),
        "total_edge_cost": round(plan.total_edge_cost, 2),
        "fuel_estimate": round(plan.fuel_estimate, 2),
        # Needed by the frontend map to draw the actual route geometry for
        # this plan -- without this the UI can only show source/destination
        # markers, not the path between them.
        "legs": [asdict(leg) for leg in plan.legs],
    }


class GemmaReasoner:
    def select_and_explain(self, request: DeliveryRequest, plans: List[CandidatePlan]) -> dict:
        raise NotImplementedError


class MockGemmaReasoner(GemmaReasoner):
    """
    Applies the same selection policy Gemma is expected to apply: among
    plans that meet the deadline, prefer the fewest sources/vehicles;
    break ties on lower total edge cost. If nothing meets the deadline,
    report infeasibility with the reason, per the finalized workflow.
    """

    def select_and_explain(self, request: DeliveryRequest, plans: List[CandidatePlan]) -> dict:
        feasible = [p for p in plans if p.total_time_hours <= request.deadline_hours]

        if not feasible:
            if plans:
                best_case = min(p.total_time_hours for p in plans)
                reason = (
                    f"The fastest possible plan found still takes {best_case:.1f}h, "
                    f"which exceeds the {request.deadline_hours}h deadline."
                )
            else:
                reason = "No combination of warehouses in the network can supply the requested quantity at all."
            return {
                "status": "infeasible",
                "explanation": (
                    f"Cannot deliver {request.quantity} units to {request.destination} "
                    f"within {request.deadline_hours} hours. {reason}"
                ),
                "chosen_plan": None,
                "chosen_plan_object": None,
                "rejected_plans": [_plan_summary(p) for p in plans],
            }

        chosen = min(feasible, key=lambda p: (len(p.sources_used), p.total_edge_cost))
        rejected = [p for p in feasible if p is not chosen]

        lines = [
            f"Recommended: {chosen.plan_type.replace('_', ' ')} using "
            f"{len(chosen.sources_used)} source(s) ({', '.join(chosen.sources_used)}) "
            f"and {chosen.vehicles_used} vehicle(s).",
            f"Arrives in {chosen.total_time_hours:.1f}h, within the {request.deadline_hours}h deadline.",
        ]
        if rejected:
            fewer = min(rejected, key=lambda p: len(p.sources_used))
            lines.append(
                f"Other feasible plans needed {len(fewer.sources_used)}+ source(s) or more "
                f"vehicles without a meaningful time or cost advantage, so this one was preferred."
            )

        return {
            "status": "feasible",
            "explanation": " ".join(lines),
            "chosen_plan": _plan_summary(chosen),
            "chosen_plan_object": chosen,
            "rejected_plans": [_plan_summary(p) for p in rejected],
        }


class LiveGemmaReasoner(GemmaReasoner):
    """
    Wired up for Google AI Studio (the Generative Language API), since that's
    where this project's Gemma access lives. Auth is a single API key -- no
    GCP project/service account needed (that would be the Vertex AI path
    instead, which uses a different endpoint/auth shape).

    Get a key at https://aistudio.google.com/apikey, then:
      export GEMMA_API_KEY="..."
      export GEMMA_MODEL="gemma-4-26b-a4b-it"   # override if you want a
                                                  # different size -- see below

    Gemma 4 model ids available on AI Studio (pick based on latency/quality
    tradeoff; all support generateContent the same way):
      gemma-4-26b-a4b-it   -- default here; lower latency, good general use
      gemma-4-31b-it        -- larger, stronger reasoning
      gemma-4-12b-it
      gemma-4-e4b-it
      gemma-4-e2b-it         -- smallest/fastest
    Confirm which of these your specific key has access to in AI Studio --
    availability can vary by account/region.
    """

    DEFAULT_ENDPOINT_TEMPLATE = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )
    DEFAULT_MODEL = "gemma-4-26b-a4b-it"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        endpoint_template: Optional[str] = None,
        timeout: float = 20.0,
    ):
        self.api_key = api_key or os.environ.get("GEMMA_API_KEY")
        self.model = model or os.environ.get("GEMMA_MODEL", self.DEFAULT_MODEL)
        self.endpoint_template = (
            endpoint_template or os.environ.get("GEMMA_ENDPOINT", self.DEFAULT_ENDPOINT_TEMPLATE)
        )
        self.timeout = timeout

    def select_and_explain(self, request: DeliveryRequest, plans: List[CandidatePlan]) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "No GEMMA_API_KEY configured. Get one from https://aistudio.google.com/apikey, "
                "set it as an env var, or use MockGemmaReasoner for local development."
            )
        prompt = self._build_prompt(request, plans)
        raw = self._call_gemma(prompt)
        return self._parse_response(raw, plans)

    # Gemma (unlike Gemini) ignores a bare responseMimeType="application/json" --
    # it still emits its markdown "thinking" before any JSON, which is exactly
    # what was breaking parsing. Pairing responseMimeType with a full
    # responseSchema (and, for extra safety, responseJsonSchema) is what
    # actually suppresses that and forces clean JSON-only output.
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["feasible", "infeasible"]},
            # responseJsonSchema follows real JSON Schema, not OpenAPI --
            # there's no "nullable" keyword there, so int-or-null is
            # expressed as a type array instead.
            "chosen_index": {"type": ["integer", "null"]},
            "explanation": {"type": "string"},
        },
        "required": ["status", "chosen_index", "explanation"],
    }

    def _build_prompt(self, request: DeliveryRequest, plans: List[CandidatePlan]) -> str:
        return (
            "You are choosing the best delivery plan for a logistics order in Nepal. "
            "You are given only precomputed, verified plan summaries -- do not recompute "
            "or second-guess the numbers, only reason over them.\n\n"
            f"Order: deliver {request.quantity} units to {request.destination} "
            f"within {request.deadline_hours} hours.\n\n"
            f"Candidate plans (0-indexed):\n{json.dumps([_plan_summary(p) for p in plans], indent=2)}\n\n"
            "Pick the best feasible plan (meets the deadline; prefer fewer sources/vehicles "
            "when the time/cost difference is small). If none are feasible, say so and explain "
            "why.\n\n"
            "Output rules (critical): do not think out loud, do not use bullet points or "
            "markdown, do not restate the input, do not write any text before or after the "
            "JSON. Your entire response must be exactly one JSON object, and nothing else, "
            "in this shape: "
            '{"status": "feasible"|"infeasible", "chosen_index": int|null, "explanation": string}'
        )

    def _call_gemma(self, prompt: str) -> str:
        url = self.endpoint_template.format(model=self.model)
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                # responseJsonSchema (not the older responseSchema -- the two
                # are mutually exclusive and sending both risks a 400) is what
                # actually stops Gemma from "thinking" in markdown before
                # producing JSON. responseMimeType alone is not enough for
                # Gemma models, even though it's sufficient for Gemini.
                "responseJsonSchema": self.RESPONSE_SCHEMA,
            },
        }
        try:
            resp = requests.post(url, params={"key": self.api_key}, json=body, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Gemma API call failed: {exc}") from exc

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected Gemma response shape: {data}") from exc

    @staticmethod
    def _try_parse_json(text: str) -> Optional[dict]:
        """
        Direct parse first. If Gemma still prepended markdown "thinking"
        before the JSON despite the schema constraints, fall back to
        pulling out the last balanced {...} block in the text -- Gemma's
        actual answer, when it produces one, comes last.
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Scan for balanced brace blocks and try the last one that parses,
        # since the final block is most likely to be the intended answer
        # rather than a fragment mentioned while reasoning.
        candidates = []
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        candidates.append(text[start:i + 1])

        for candidate in reversed(candidates):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _parse_response(self, raw: str, plans: List[CandidatePlan]) -> dict:
        text = raw.strip()
        # Defensive: strip markdown code fences if the model added them
        # despite responseMimeType=application/json.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()

        data = self._try_parse_json(text)
        if data is None:
            raise RuntimeError(f"Gemma did not return valid JSON: {raw!r}")

        status = data.get("status")
        if status not in ("feasible", "infeasible"):
            raise RuntimeError(f"Gemma returned an unexpected status field: {data!r}")

        idx = data.get("chosen_index")
        chosen = None
        if status == "feasible":
            if not isinstance(idx, int) or not (0 <= idx < len(plans)):
                raise RuntimeError(
                    f"Gemma returned status='feasible' with an invalid chosen_index={idx!r} "
                    f"for {len(plans)} candidate plan(s) -- refusing to guess."
                )
            chosen = plans[idx]

        return {
            "status": status,
            "explanation": data.get("explanation", ""),
            "chosen_plan": _plan_summary(chosen) if chosen else None,
            "chosen_plan_object": chosen,
            "rejected_plans": [_plan_summary(p) for p in plans if p is not chosen],
        }


def get_reasoner() -> GemmaReasoner:
    """
    Always resolves to the live Gemma reasoner -- there is no automatic
    fallback to MockGemmaReasoner here. If GEMMA_API_KEY isn't set,
    LiveGemmaReasoner.select_and_explain will raise a clear error on first
    use, which service.process_request catches and reports as an honest
    "reasoning_unavailable" result rather than silently substituting a
    rule-based guess dressed up as Gemma's answer.

    MockGemmaReasoner still exists and is useful, but only when a caller
    explicitly opts into it (e.g. `process_request(..., reasoner=MockGemmaReasoner())`
    in demo.py or tests, clearly labeled as a stand-in) -- never as an
    implicit default.
    """
    return LiveGemmaReasoner()