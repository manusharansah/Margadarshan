# Architecture

```
 Browser ──► Frontend (TanStack Start, Node)         /api/* allow-list gateway
                │   GET /meta /network /places /warehouses   POST /optimize-route
                ▼
            FastAPI ── Network (graph + snapping) ◄── network.json.gz  (built offline from OSM + SRTM)
                │  ├── WeatherService ◄── Open-Meteo   (cached 30 min; failure = reported, never invented)
                │  ├── Planner (Yen, Held-Karp)        (pure code)
                │  ├── WarehouseStore ◄── SQLite | PostgreSQL (operator-maintained inventory)
                │  └── Reasoner ◄── Gemma (optional)   (selects among feasible plans, explains)
```

## Two phases

**Offline ingestion** (`python -m gis_backend.ingest`, minutes, run when the map changes)
OSM ways → drop non-drivable → split at junctions → cut into ≤ 0.5 km pieces (virtual nodes inside long straight
segments) → sample SRTM every ~30 m → smoothed ascent/descent, mean & max grade, hillslope angle → terrain hazard →
optional incident proximity → keep the largest connected component → `network.json.gz` with a provenance block.

**Online serving** (milliseconds)
Load artifact → NetworkX graph → apply rainfall to edge weights → per request: snap, plan, filter, rank, explain.

## Cost model (`edge_weight.py`)

```
weight = length × (1 + surface_penalty + 0.005 × mean_grade%) + hazard × length × 1.5
hazard = blend(terrain, incidents) × (1 + 1.5 × rain_index)        capped at 1
time   = length / max(5, maxspeed × surface_factor × grade_factor)
fuel   = length × 0.30 L/km × surface_factor + climb_energy / (0.30 × 36 MJ/L)      direction-aware
```
All constants sit at the top of the module with their rationale.

## Planner (`source_selector.py`)

| Tier | Plan | Arrival time |
|---|---|---|
| 1 | One warehouse, ⌈Q/capacity⌉ vehicles in parallel | its route time |
| 2 | 2–4 warehouses (8 nearest candidates), parallel | slowest leg |
| 3 | One vehicle, Held-Karp pickup order, repeated trips with return legs | sum of trips |

`best_route` returns the cheapest path meeting the deadline, else the fastest path (true best case for the
"infeasible" message). It is lazy: Yen's K-shortest is run only when the cheapest path misses the deadline *and* a faster
path exists — equivalent results, ~100× faster (see STAR.md).

## Honesty guarantees

* Numbers in a response are sums over road data. Gemma never produces or alters them.
* Gemma only sees plans that already meet the deadline, as figures (no geometry), and its chosen index is validated.
* Any failure to reach Gemma/Open-Meteo is surfaced in the response (`gemma_error`, `weather.error`).
* The UI labels fixture data, rule-based vs Gemma explanations, and missing rainfall.
* The public gateway forwards only 5 read endpoints and the optimiser; inventory writes need `X-API-Key` and are
  unreachable from the website.
