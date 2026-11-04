# Engineering case study

Each section follows the Situation, Task, Action, Result format. Results are measured in this repository. Anything not
yet verified says so.

## 1. Replace simulated data with real data

Situation. The prototype generated roads, slopes, hazards, stock and vehicles with `random`. The frontend carried a
second copy of the engine and fell back to synthetic data whenever the backend URL was unset.

Task. Every calculation had to rest on real data, or say clearly that it could not.

Action. I built an ingestion pipeline. It reads real road segments, splits them at junctions into pieces of 0.5 km or
less, samples NASA SRTM elevation every 30 m, and derives grade, climb and a terrain hazard score. Rainfall comes from
Open-Meteo at request time. Overpass was unreachable from the build environment, so I added Overture Maps as a source
and read it directly from public S3 with range requests. I deleted `simulate.py`, the 756-line TypeScript engine copy and
the fake road drawing. Warehouse stock moved into an operator-owned database, because no public source exists.

Result. The Kathmandu and Kavre network has 27,018 junctions, 28,549 road pieces and 4,806 km in one connected
component. Routed distances match the known roads: Kathmandu to Banepa is 26.4 km, to Dhulikhel 29.3 km, and to
Bhaktapur 15.1 km. Open-Meteo returned live rainfall for 24 grid points. Warehouse figures are still demo data.

## 2. Make calculations correct

Situation. The README said infeasible plans were filtered before Gemma, but the code never checked the deadline. Hazard
cost was a constant per road, so a 3 km and a 40 km road looked equally risky. The fuel figure had no real unit.

Task. Make every number mean what its label says.

Action. I added the deadline filter in code, scaled hazard by length and rain, and computed fuel in litres from base
consumption plus climb energy, depending on direction. The ingestion step now inserts virtual nodes into long straight
segments, and the drivable check validates the road class itself.

Result. The 114 backend tests include oracles. Yen's paths match brute-force enumeration, Held-Karp matches every
permutation, and plan totals follow the arithmetic. The tests caught two real defects: footpaths slipping through saved
data, and sparse roads staying as multi-kilometre edges.

## 3. Make it fast on real networks

Situation. On a 14,400-node test graph the first benchmark took 5.8 s for a single-source order and 11.8 s for an
infeasible one. On the real network, an impossible 40,000-unit order ran past two minutes.

Task. Answer in about a second without changing results.

Action. Yen's first path is the cheapest path, so `best_route` now runs Yen's algorithm only when that path misses the
deadline and a faster one exists. The planner returns at once when total stock is below the order. It caches
shortest-path trees, rejects plans needing more than 10 trips, and starts the one-vehicle tier from only the three
nearest depots. The search for a smaller order that fits has an 8-second budget.

Result. The test graph now answers in 0.06 to 0.47 s. On the real network, ordinary requests take 0.05 to 0.35 s and
impossible orders 1.3 to 1.8 s. A test checks the lazy search is never worse than the eager definition on 30
source, target and deadline combinations.

## 4. Connect the frontend and backend

Situation. The frontend used a TypeScript port with an optional proxy, and a Gemma outage became an HTTP 503 with no answer.

Task. One source of truth, a clear failure behaviour, and no CORS or secret exposure.

Action. The browser talks only to a same-origin `/api` gateway with an allow-list. Admin writes cannot be reached from the
site. If Gemma is missing or fails, the plan the code already computed is returned with `explanation_source` and
`gemma_error`. Gemma's chosen index is validated, and API keys never appear in errors.

Result. PUT, DELETE, PATCH, import and unknown paths all return 404 at the gateway. The browser smoke test passes with zero errors.

## 5. Keep your design

Situation. I first replaced your interface with a generic white dashboard, without rendering the original.

Task. Restore the original look and keep the real-data backend.

Action. I rendered the old design, restored its dark panel, outlined fields, "New Shipment", "Top Recommendation", "Map
Layers", footer and map styling, then reconnected it to the real API. The Inventory and Data pages use the same shell.

Result. The browser test found and I fixed three bugs. The quantity field's step blocked valid input, the header
overflowed a 390 px screen, and Leaflet threw an error when the map was removed during a zoom animation.

## 6. Make the AI contribute

Situation. Gemma only reworded a result that code had already fixed, so the product behaved the same without it.

Task. Give the model work only a language model can do, without letting it touch the numbers.

Action. Four features sit behind strict checks. Plain-language order intake leaves date arithmetic and place lookup to
code. Alternatives for impossible orders are re-run through the planner. The risk briefing uses the named riskiest road
stretches. `MARGA_REQUIRE_GEMMA` lets production refuse to start without the model. A labelled stand-in server lets the real
HTTP client and interface run without a key.

Result. Seventeen new tests cover bad dates, malformed output, out-of-range and infeasible proposals, and invalid
choices. The browser test covers the flows against the stand-in. Real-model quality is untested until a key is added.

## 7. Production readiness

Situation. There were no tests, CI, containers or deployment path. The repository tracked `__pycache__`, an `.env`
file and a 272 MB `frontend.zip`, and the lockfile was out of sync.

Task. A reproducible build and a route to AWS.

Action. I pinned the requirements, wrote Dockerfiles and a compose file, added CI and an OIDC deploy workflow, wrote a
CloudFormation stack, and cleaned the repository.

Result. The template passes `cfn-lint`. The Docker images and the AWS deployment have not been run, because this
environment has no Docker daemon and no AWS access.
