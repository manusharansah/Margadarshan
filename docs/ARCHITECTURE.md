# Architecture

```
Browser -> Frontend (TanStack Start, Node) -> /api gateway with an allow-list
                                                |
                                                v
          FastAPI -- Network (graph and snapping) <-- network.json.gz, built offline
             |-- WeatherService  <-- Open-Meteo, cached for 30 minutes
             |-- Planner         (Yen, Held-Karp; plain code)
             |-- WarehouseStore  <-- SQLite or PostgreSQL, maintained by the operator
             `-- Gemma client    <-- Google AI Studio, optional unless MARGA_REQUIRE_GEMMA is set
```

## Two phases

Offline ingestion runs `python -m gis_backend.ingest` once per map update. It loads road segments, splits them at
junctions, cuts them into pieces of 0.5 km or less, samples elevation every 30 m, derives grade and terrain hazard, keeps
the largest connected component, and writes `network.json.gz` with a metadata block listing every source.

Online serving loads that file, builds a NetworkX graph, applies current rainfall to the edge weights, and answers
each request by snapping, planning, filtering, ranking and explaining.

## Cost model

All constants sit at the top of `edge_weight.py` with their reasoning.

```
weight = length * (1 + surface_penalty + 0.005 * mean_grade_percent) + hazard * length * 1.5
hazard = blend(terrain, incidents) * (1 + 1.5 * rain_index), capped at 1
time   = length / max(5, max_speed * surface_factor * grade_factor)
fuel   = length * 0.30 L/km * surface_factor + climb_energy / (0.30 * 36 MJ per litre)
```

Fuel depends on direction, because climbing costs more than descending.

## Planner

| Tier | Plan | Arrival time |
|---|---|---|
| 1 | One warehouse, enough vehicles in parallel | Its route time |
| 2 | Two to four warehouses from the eight nearest, in parallel | The slowest leg |
| 3 | One vehicle collecting from several warehouses, in the best order, over up to 10 trips | Sum of the trips |

`best_route` returns the cheapest path that meets the deadline, or the fastest path when none does, so the "cannot be
delivered" message can state the true best case. It runs Yen's algorithm only when the cheapest path is too slow and a
faster path exists. Shortest-path trees are cached per request, so repeated trips and remedy searches reuse them.
Three guards keep impossible orders fast: the planner returns at once when total stock is below the order, tier 3
rejects plans needing more than 10 trips, and tier 3 starts from only the three nearest depots with a vehicle.

## Where Gemma contributes

| Feature | What Gemma does | What code does |
|---|---|---|
| Order intake, `POST /parse-order` | Extracts the destination text, quantity and deadline as written | Computes deadline hours from the current Nepal time, checks ranges, looks up the place. The form is pre-filled and the dealer confirms. |
| Plan choice and explanation | Picks one index among deadline-feasible plans and writes 2 to 4 sentences | Decides feasibility, ranks plans, validates the index, and falls back to rule-based text if the output is invalid |
| Risk briefing | Summarises the named riskiest stretches | Finds those stretches from the road graph with their hazard, grade and rain figures |
| Options when infeasible | Proposes alternative quantity and deadline pairs with a reason | Re-runs the planner on each proposal and drops any it cannot prove feasible. It always adds its own options too. |

Tests in `tests/test_ai_features.py` cover these checks with a stub model. `scripts/fake_gemma_server.py` is a labelled
stand-in that speaks Gemma's wire format, so the real HTTP client and the UI run end to end without a key. It tests
wiring only and says nothing about the real model's wording.

## Safety properties

* Every number in a response is a sum over road data. Gemma never produces or changes one.
* Gemma sees only plans that already meet the deadline, as figures without geometry, and its chosen index is validated.
* A failure to reach Gemma or Open-Meteo appears in the response as `gemma_error` or `weather.error`.
* The interface labels fixture data, rule-based text and missing rainfall.
* The public gateway forwards six endpoints. Inventory writes need `X-API-Key` and the website cannot reach them.
