# Architecture

```
Browser -> Frontend (TanStack Start, Node) -> /api gateway with an allow-list
                                                |
                                                v
          FastAPI -- Network (graph and snapping) <-- network.json.gz, built offline
             |-- WeatherService  <-- Open-Meteo, cached for 30 minutes
             |-- Planner         (Yen, Held-Karp; plain code)
             |-- Accounts, sessions, orders, audit, warehouses  <-- SQLite or PostgreSQL, one organisation per tenant
             |-- OIDC client     <-- Google, Cognito, Entra or Auth0
             `-- Gemma client    <-- Google AI Studio, optional unless MARGA_REQUIRE_GEMMA is set
```

## Two phases

Offline ingestion runs `python -m gis_backend.ingest` once per map update. It loads road segments, splits them at
junctions, cuts them into pieces of 0.5 km or less, samples elevation every 30 m, derives grade and terrain hazard, keeps
the largest connected component, and writes `network.json.gz` with a metadata block listing every source.

Online serving loads that file, builds a NetworkX graph, applies current rainfall to the edge weights, and answers
each request by snapping, planning, filtering, ranking and explaining.

## Cost, time and fuel

The graph is directed. A two-way road becomes two arcs, a one-way road one arc, and only the largest strongly connected
part is routable, so every node can reach every other. Each arc carries its own time and fuel, because climbing is slower
and thirstier than descending. The full evidence is in [MODEL.md](MODEL.md).

```
weight = length * (1 + E[surface penalty] + 0.005 * mean_grade_percent) + hazard * length * 1.5
hazard = blend(terrain, incidents) * (1 + 1.5 * rain_index), capped at 1
rain_index = worst 24, 48 or 72 hour rainfall divided by the Dahal and Hasegawa landslide threshold, capped at 1
speed  = min(free flow, curve limit, grade limit) per surface; time is the mix-weighted mean of length / speed
fuel   = max(0, rolling + aero + net climb) / (driveline * engine efficiency) + idle, per direction and per vehicle
```

Roads without a surface tag use the learned class prior as probabilities. All constants sit at the top of their modules.

## Planner

| Tier | Plan | Arrival time |
|---|---|---|
| 1 | One warehouse, enough vehicles in parallel | Its route time |
| 2 | Two to four warehouses from the eight nearest, in parallel | The slowest leg |
| 3 | One vehicle collecting from several warehouses, in the best order, over up to 10 trips | Sum of the trips |

`best_route` returns the cheapest path that meets the deadline, or the fastest path when none does, so the "cannot be
delivered" message can state the true best case. It runs Yen's algorithm only when the cheapest path is too slow and a
faster path exists. Roads are one-way, so distances to the destination come from a reversed-graph search, not from the
forward one. Shortest-path trees are cached per request, so repeated trips and remedy searches reuse them.
Three guards keep impossible orders fast: the planner returns at once when total stock is below the order, tier 3
rejects plans needing more than 10 trips, and tier 3 starts from only the three nearest depots with a vehicle.

## Where Gemma contributes

| Feature | What Gemma does | What code does |
|---|---|---|
| Order intake, `POST /parse-order` | Extracts the destination text, quantity and deadline as written | Computes deadline hours from the current Nepal time, checks ranges, looks up the place. The form is pre-filled and the dealer confirms. |
| Plan choice and explanation | Picks one index among deadline-feasible plans and writes 2 to 3 sentences | Decides feasibility, ranks plans, validates the index, and falls back to rule-based text if the output is invalid |
| Risk briefing | Summarises the named riskiest stretches | Finds those stretches from the road graph with their hazard, grade and rain figures |
| Options when infeasible | Proposes alternative quantity and deadline pairs with a reason | Re-runs the planner on each proposal and drops any it cannot prove feasible. It always adds its own options too. |

The model may not type a number. Its prompt lists placeholders such as `{arrival}`, `{vehicles}`, `{distance}` and
`{road1}`. Code fills them from the chosen plan. Any digit the model writes on its own, or any unknown placeholder,
rejects the text. The first live run showed why: an unguarded model wrote "uses 0 vehicles" for a nine-vehicle plan.
A rejected text gets one retry with the reason, then the rule-based text stands.

Model calls took 5 to 90 seconds on the free tier, with occasional 503 and empty `RECITATION` responses. So
`POST /optimize-route` returns the computed plan at once, with `ai.status: "pending"` and a `result_id`, and the page
then calls `POST /optimize-route/{id}/ai`. That call runs the explanation and the option proposals in parallel and
returns what to merge in. The AI step never blocks, changes or removes the computed answer.

Tests in `tests/test_ai_features.py` cover these checks with a stub model. `scripts/fake_gemma_server.py` is a labelled
stand-in that speaks Gemma's wire format, so the browser test runs without a key. It checks wiring only.

## Accounts and tenancy

Sign-in, sessions, roles and data handling are described in [SECURITY.md](SECURITY.md). Two design rules matter for the
code. The organisation always comes from the session cookie, so no endpoint accepts an organisation id. And shared
read-only data (the road graph) is never modified per tenant: closures create a filtered view for one request.

## Safety properties

* Every number in a response is a sum over road data. Gemma never produces or changes one.
* Gemma sees only plans that already meet the deadline, as figures without geometry, and its chosen index is validated.
* A failure to reach Gemma or Open-Meteo appears in the response as `gemma_error` or `weather.error`.
* The interface labels fixture data, rule-based text and missing rainfall.
* The public gateway forwards only listed routes and passes the session cookie and CSRF header through. The backend
  checks sign-in, role and organisation again on every request.
