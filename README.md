# MargaAI Nepal

**मार्ग (marga) — "route".** A decision-support system that tells a dealer, for a given destination, quantity and
deadline, *which warehouse(s) should ship, by which road, and whether it can arrive in time* — on Nepal's hilly,
landslide-prone road network, using real road, elevation and rainfall data.

| | |
|---|---|
| **Backend** | Python 3.13 · FastAPI · NetworkX (Yen's K-shortest paths, Held-Karp TSP) · SQLAlchemy |
| **Frontend** | React 19 · TanStack Start · Tailwind v4 · Leaflet |
| **Real data** | OpenStreetMap roads · NASA SRTM elevation · Open-Meteo rainfall · optional landslide-incident CSV |
| **Reasoning** | Gemma (Google AI Studio) selects among already-feasible plans and explains; all arithmetic is plain code |
| **Deploy** | Docker · GitHub Actions · AWS ECS Fargate + RDS + S3 (CloudFormation) |

> **Status, stated plainly.** The platform is complete and tested end to end. Two inputs are *yours* and are not
> bundled: (1) the **road network artifact**, built from OpenStreetMap by one command
> ([docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)), and (2) **warehouse inventory** (no public source exists). Until the
> real artifact is built the UI shows a "development fixture" banner. Gemma has **not** yet been exercised against the live
> API (needs your key). See [Verification status](#verification-status).

## How it works

1. The dealer enters **destination**, **quantity** and **deadline** — nothing else.
2. Destination and warehouses are snapped to the nearest mapped road.
3. Every road piece (≤ 0.5 km) gets a cost from its real length, surface, DEM-derived grade and a hazard score that is
   amplified by recent + forecast rainfall.
4. Candidate plans are generated in priority order — one warehouse → several warehouses in parallel → one vehicle visiting
   several (exact TSP) — using shortest-path search. A later tier is explored only if no earlier tier meets the deadline.
5. **Code** discards plans that miss the deadline and ranks the rest (fewest warehouses → fewest vehicles → lowest cost).
6. **Gemma** (if configured) may choose among the feasible plans and writes the explanation. If it is unconfigured or
   fails, a templated explanation of the computed figures is returned, labelled `explanation_source: "deterministic"`.

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (local)

```bash
# Backend
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz   # needs overpass-api.de
python -m gis_backend.seed_warehouses examples/warehouses.sample.csv              # replace with YOUR inventory (format: docs/INVENTORY_FORMAT.md)
export MARGA_ADMIN_API_KEY=change-me GEMMA_API_KEY=...   # GEMMA_API_KEY optional
uvicorn gis_backend.api:app --port 8000

# Frontend (second terminal)
cd frontend && cp .env.example .env && npm ci && npm run dev
```

No internet access to OpenStreetMap yet? `python scripts/make_dev_fixture.py data/dev/network.json.gz` builds a clearly
labelled **fixture** (real elevations, synthetic roads) so the whole stack can be exercised. Never deploy it.

Or everything in containers: `docker compose up --build`.

## Repository map

```
backend/gis_backend/   dem.py osm.py hazard.py ingest.py   real-data pipeline (offline, run once)
                       network.py weather.py                runtime graph, snapping, live rainfall
                       edge_weight.py routing.py            cost model; Yen + Held-Karp
                       source_selector.py service.py        plan generation, ranking, orchestration
                       gemma_reasoner.py store.py api.py    reasoning layer, inventory DB, HTTP API
backend/tests/         91 tests (brute-force oracles, real SRTM, API, Gemma/weather with stubbed HTTP)
frontend/src/          routes/ (Plan, Inventory, Data) · components/ · lib/api.ts · routes/api/$.ts (gateway)
frontend/e2e/smoke.mjs full-site browser smoke test
infra/marga.cfn.yml    AWS stack · .github/workflows  CI + deploy
docs/                  ARCHITECTURE · DATA_SOURCES · DEPLOYMENT · STAR (case study) · API
```

## Verification status

| Claim | Evidence | State |
|---|---|---|
| Routing is optimal | Yen's vs brute-force path enumeration; Held-Karp vs all permutations | ✅ tested |
| Elevation/grade are real | Sampled SRTM vs published town altitudes (±100 m), ridge/valley ordering | ✅ tested against real tiles |
| Planner arithmetic | Units sum to Q, vehicles = ⌈units/capacity⌉, time = slowest leg, tier priority | ✅ tested |
| API contract & security | Admin key on writes, validation, 422 outside network, public gateway blocks writes | ✅ tested |
| Whole website works | Full-site Playwright smoke test (all pages, both infeasible paths, gateway blocking, mobile), 0 browser errors | ✅ passing (on fixture data) |
| Scale | 14,400 nodes / 28,560 edges: 0.06–0.47 s per request | ✅ measured |
| **Real OSM network built** | Needs outbound access to `overpass-api.de` | ⏳ blocked in the build sandbox |
| **Live rainfall** | Needs `api.open-meteo.com`; parsing tested with stubbed HTTP | ⏳ not run live |
| **Gemma end-to-end** | Needs `GEMMA_API_KEY`; parsing/failure paths tested with stubbed HTTP | ⏳ not run live |
| **Docker images / AWS stack** | Template passes `cfn-lint`; Dockerfiles built by CI (no Docker daemon in sandbox) | ⏳ not deployed |

## Limits you should know

Hazard is a screening index (terrain, optional incidents, rainfall), not a calibrated probability. Grades come from
30 m SRTM. Speeds are planning defaults by road class; one-way roads, live traffic and closures are not modelled.
Fuel is an engineering estimate to be calibrated against fleet logs. A single order uses at most one trip per
vehicle outside the multi-stop tier. Details: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

Original workflow specification: [GIS_Route_Optimization_Workflow.docx](GIS_Route_Optimization_Workflow.docx).
