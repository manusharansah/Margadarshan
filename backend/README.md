# GIS Route Optimization Backend

Implements the finalized workflow (see `GIS_Route_Optimization_Workflow.docx`):
simulated storage-node + road-network data, a fixed multi-metric edge-weight
formula, Yen's K-shortest-paths + Held-Karp TSP routing, deterministic
feasibility checks, and a pluggable Gemma 4 reasoning/explanation layer.
Frontend (map UI) is deliberately not built yet — this is backend-only.

## Project layout

```
gis_backend/
  models.py            Data classes: StorageNode, RoadEdge, Vehicle-adjacent
                        fields, DeliveryRequest, RouteLeg, CandidatePlan
  simulate.py           Random but connected network generator (Section 9:
                        fully simulated, no live data)
  edge_weight.py         Fixed multi-metric edge cost / time / fuel formulas
  routing.py             Graph builder, Yen's K-shortest paths, Held-Karp TSP
  feasibility.py          Deterministic single-source / vehicle-capacity checks
  source_selector.py       Steps 3/4a/4b: single-source, multi-source parallel,
                        single-vehicle multi-stop fallback
  gemma_reasoner.py        Gemma 4 interface: MockGemmaReasoner (offline,
                        used by default) + LiveGemmaReasoner (stub to fill
                        in once a real Gemma 4 endpoint is available)
  service.py              Orchestrates the full pipeline end to end
  api.py                  FastAPI app: GET /network, POST /optimize-route
  demo.py                  Offline CLI demo, no server needed
```

## Run the offline demo

```
python -m gis_backend.demo
```

Simulates a 12-node network and runs three example requests (comfortably
feasible, needs multiple sources, and infeasible-by-deadline) so you can see
all three code paths without standing up a server.

## Run the API server

```
uvicorn gis_backend.api:app --reload
```

- `GET /network` — returns the simulated nodes and edges (for the future map).
- `POST /optimize-route` — body `{"destination": "W3", "quantity": 500, "deadline_hours": 6}`,
  returns the chosen plan (or an infeasibility explanation) plus rejected alternatives.

## Wiring in real Gemma 4

Gemma access for this project is via **Google AI Studio** (the Generative
Language API) -- a single API key, no GCP project or service account setup
needed.

1. Get a key at https://aistudio.google.com/apikey
2. Set env vars before starting the server:
   ```
   export GEMMA_API_KEY="your-key-here"
   export GEMMA_MODEL="gemma-4-26b-a4b-it"   # default; see options below
   ```
   Other Gemma 4 model ids available on AI Studio, if you want a different
   latency/quality tradeoff (confirm which your key has access to):
   `gemma-4-31b-it` (stronger reasoning), `gemma-4-12b-it`, `gemma-4-e4b-it`,
   `gemma-4-e2b-it` (smallest/fastest).

**There is no silent mock fallback.** `get_reasoner()` always resolves to
`LiveGemmaReasoner`. If `GEMMA_API_KEY` isn't set, or the live call fails for
any reason (bad key, rate limit, malformed response, network issue),
`process_request` does **not** substitute a rule-based guess and present it
as Gemma's answer. Instead it returns:

```json
{
  "status": "reasoning_unavailable",
  "explanation": "The Gemma reasoning/explanation layer could not be reached...",
  "gemma_error": "<what actually went wrong>",
  "chosen_plan": null,
  "candidate_plans": [ /* all deterministically computed, verified plans */ ]
}
```

`POST /optimize-route` returns this as HTTP `503`, not a disguised `200` --
the routing/feasibility math (which is always deterministic and verified)
still ran and is visible in `candidate_plans`; only the plan *selection* and
plain-language *explanation*, which are specifically Gemma's job, are
missing.

`MockGemmaReasoner` (a rule-based stand-in: fewest sources/vehicles among
deadline-feasible plans) still exists in `gemma_reasoner.py`, but it is
**never used automatically**. It only runs if a caller explicitly passes it
in, e.g. `process_request(..., reasoner=MockGemmaReasoner())`, or via
`python -m gis_backend.demo --mock`. This is meant purely as an offline dev
tool for testing the routing pipeline without a Gemma key -- it must never
be presented as if it were Gemma's reasoning.

**Not tested end-to-end against the real API** -- prompt building, response
parsing, and error handling were unit-tested with a mocked HTTP layer, but
nobody has actually hit `generativelanguage.googleapis.com` with this code
yet. Run `python -m gis_backend.demo` with `GEMMA_API_KEY` set as your first
real test, and double check the `GEMMA_MODEL` id matches what's available
to your key in AI Studio.

## Known placeholders (see workflow doc Section 9)

- Vehicle capacity is fixed at 200 units/vehicle.
- The fuel-efficiency formula (`edge_weight.compute_edge_fuel`) is a
  placeholder proxy, not yet finalized.
- Network topology and all attribute values are fully synthetic.
