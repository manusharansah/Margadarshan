# MargaAI Nepal

MargaAI (मार्ग, "route") helps a dealer in Nepal's hills answer one question: for this destination, quantity and
deadline, which warehouses should ship, along which roads, and will it arrive in time? It routes over real road,
elevation and rainfall data and explains each recommendation in plain language.

| Part | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, NetworkX (Yen's K-shortest paths, Held-Karp TSP), SQLAlchemy |
| Frontend | React 19, TanStack Start, Tailwind v4, Leaflet |
| Real data | Overture Maps roads (or OpenStreetMap via Overpass), NASA SRTM elevation, Open-Meteo rainfall |
| AI | Gemma through Google AI Studio |
| Deployment | Docker, GitHub Actions, AWS ECS Fargate with RDS and S3, described in CloudFormation |

## Current status

The software is finished and tested. It runs on a real road network of the Kathmandu valley and Kavre district
(27,018 junctions, 28,549 road pieces, 4,806 km) with live rainfall. Three things are still open:

1. Gemma has never been called live. Tests use a stub, and the browser test uses a labelled stand-in server. Add
   `GEMMA_API_KEY` and run the first live call.
2. Warehouse stock and fleet are demo data. Only you have the real figures. See [docs/INVENTORY_FORMAT.md](docs/INVENTORY_FORMAT.md).
3. Nothing is deployed to AWS yet, and the Docker images have only been built by CI definitions, not run here.

## How a recommendation is made

1. The dealer enters a destination, a quantity and a deadline. Typing the order in plain language also works.
2. The destination and each warehouse snap to the nearest mapped road.
3. Each road piece (0.5 km or less) gets a cost from its length, surface, grade from elevation data, and a hazard
   score that rises with recent and forecast rain.
4. The planner builds candidate plans in priority order: one warehouse, then several warehouses in parallel, then one
   vehicle visiting several warehouses in the best order. It tries a later option only when an earlier one misses the
   deadline.
5. Code discards plans that miss the deadline and ranks the rest by fewest warehouses, then fewest vehicles, then lowest cost.
6. Gemma does the language work and never the arithmetic. It reads typed orders, picks among plans that already
   meet the deadline, writes a risk briefing about the named riskiest road stretches, and proposes alternatives when
   delivery is impossible. The planner re-checks every proposal and the page shows only the feasible ones.

With `MARGA_REQUIRE_GEMMA=true` the service refuses to start without a Gemma key. Without that setting, a missing
key degrades to rule-based text that the response labels `explanation_source: "deterministic"`.

More detail: [architecture](docs/ARCHITECTURE.md), [data sources](docs/DATA_SOURCES.md), [deployment](docs/DEPLOYMENT.md),
[engineering case study](docs/STAR.md).

## Run it locally

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-ingest.txt
python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz
python -m gis_backend.seed_warehouses examples/warehouses.demo.csv
export MARGA_ADMIN_API_KEY=change-me
export GEMMA_API_KEY=your-key          # optional for development
uvicorn gis_backend.api:app --port 8000
```

In a second terminal:

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

The ingestion step downloads roughly 50 MB of road data and elevation tiles and takes two to three minutes. The
first run needs outbound HTTPS to `overturemaps-us-west-2.s3.amazonaws.com` and `elevation-tiles-prod.s3.amazonaws.com`.
To run everything in containers, use `docker compose up --build`.

## Repository layout

```
backend/gis_backend/   overture.py osm.py dem.py hazard.py ingest.py   data pipeline, run once per map update
                       network.py weather.py                           graph, snapping, live rainfall
                       edge_weight.py routing.py                       cost model, Yen, Held-Karp
                       source_selector.py service.py                   plan generation, ranking, remedies
                       gemma_reasoner.py intake.py store.py api.py     AI layer, order intake, inventory, HTTP API
backend/tests/         114 tests
backend/examples/      sample and demo warehouse CSVs
frontend/src/          pages, components, and the /api gateway
frontend/e2e/          browser smoke test
infra/                 CloudFormation stack
docs/                  architecture, data, deployment, inventory format, case study
```

## What was verified

| Claim | Evidence |
|---|---|
| Routing is optimal | Yen's paths match brute-force enumeration. Held-Karp matches every permutation. |
| Road distances are right | Kathmandu to Banepa routes at 26.4 km, to Dhulikhel at 29.3 km, to Bhaktapur at 15.1 km, which match the known road distances. |
| Elevation is real | SRTM samples for Kathmandu, Dhulikhel and Banepa land within 100 m of published altitudes. |
| Planner arithmetic | Tests check that units sum to the order, vehicles equal the rounded-up units per capacity, and arrival equals the slowest leg. |
| Speed on the real network | Ordinary requests take 0.05 to 0.35 s. Impossible orders take 1.3 to 1.8 s. |
| Rainfall is live | Open-Meteo returned 24 grid points for the study area. |
| Whole site works | The Playwright smoke test passes on the real network, including the AI flows against the stand-in, with no browser errors. |
| AI safety checks | Stub-model tests show that bad dates, malformed output and infeasible proposals are rejected. |
| Gemma live | Not tested. Needs a key. |
| AWS deployment | Not done. The template passes `cfn-lint`. |

## Limits

The hazard score is a screening index built from terrain steepness, optional incident history and rainfall. It is not a
calibrated landslide probability. Grades come from 30 m SRTM data. About 76% of road pieces have no surface tag in the
source data, so their surface comes from the road class. Speeds are planning defaults by road class. One-way roads,
live traffic and closures are not modelled. Fuel is an engineering estimate that needs calibrating against your fleet logs.
The default network leaves out residential lanes and tracks to keep it fast.

The original workflow specification is in [GIS_Route_Optimization_Workflow.docx](GIS_Route_Optimization_Workflow.docx).
