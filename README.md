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

The software is finished and tested end to end. It runs on a real road network of the Kathmandu valley and Kavre district
(27,018 junctions, 28,549 road pieces, 4,806 km) with live rainfall, signed-in users, and separate organisations. Open items:

1. Sign-in is tested against a test identity provider. The real Google or Cognito flow needs a client registered in your
   account. See [docs/SECURITY.md](docs/SECURITY.md).
2. Warehouse stock and fleet are demo data. Only you have the real figures. See [docs/INVENTORY_FORMAT.md](docs/INVENTORY_FORMAT.md).
3. Nothing is deployed to AWS, and the Docker images have only been built by CI definitions, not run here.
4. The travel-time, fuel and hazard models rest on published formulas and physics, not on Nepali field data. See
   [docs/MODEL.md](docs/MODEL.md) for what is verified and what would validate the rest.

Gemma has been called live, with real results described in the architecture notes. Calls take 5 to 90 seconds on the free
tier, so the plan appears at once and the AI text follows.

## How a recommendation is made

1. The dealer enters a destination, a quantity and a deadline. Typing the order in plain language also works.
2. The destination and each warehouse snap to the nearest mapped road.
3. Each road piece (0.5 km or less) gets a cost from its length, surface, grade from elevation data, and a hazard
   score that rises when recent and forecast rain approaches the published landslide threshold for the Nepal Himalaya.
   Time and fuel come from a truck physics model that knows about climbing, curves and one-way roads.
4. The planner builds candidate plans in priority order: one warehouse, then several warehouses in parallel, then one
   vehicle visiting several warehouses in the best order. It tries a later option only when an earlier one misses the
   deadline.
5. Code discards plans that miss the deadline and ranks the rest by fewest warehouses, then fewest vehicles, then lowest cost.
6. Gemma does the language work and never the arithmetic. It reads typed orders, picks among plans that already
   meet the deadline, writes a risk briefing about the named riskiest road stretches, and proposes alternatives when
   delivery is impossible. It may not type a number: it writes placeholders such as `{arrival}` and code fills them in, and
   text with any digit of its own is rejected. The planner re-checks every proposal and the page shows only the feasible ones.
7. Each plan shows a likely arrival range and the chance of missing the deadline, from a simulation over the uncertain inputs.

With `MARGA_REQUIRE_GEMMA=true` the service refuses to start without a Gemma key. Without that setting, a missing
key degrades to rule-based text that the response labels `explanation_source: "deterministic"`.

More detail: [architecture](docs/ARCHITECTURE.md), [data sources](docs/DATA_SOURCES.md), [deployment](docs/DEPLOYMENT.md),
[engineering case study](docs/STAR.md).

## Users, organisations and data

People sign in with their existing work account through OpenID Connect, with no passwords stored. Each organisation
has its own warehouses, orders, road closures, team and audit log, and nothing crosses between them. Roles are viewer,
member, admin and owner. Anyone can download their data or delete their account. See [docs/SECURITY.md](docs/SECURITY.md).

## Run it locally

The quickest start uses the bundled test identity provider and demo organisation. It needs no Google account.

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-ingest.txt
python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz
export DATABASE_URL=sqlite:///data/dev.db
python scripts/dev_bootstrap.py                 # organisation "Acme Demo", invitations, 14 demo warehouses
python scripts/fake_oidc_server.py &            # test identity provider on :9200
export OIDC_ISSUER=http://localhost:9200 OIDC_CLIENT_ID=test-client OIDC_CLIENT_SECRET=test-secret
export MARGA_PUBLIC_URL=http://localhost:3000 MARGA_COOKIE_SECURE=false
export GEMMA_API_KEY=your-key                   # optional for development
uvicorn gis_backend.api:app --port 8000
```

Sign in as `alice@acme.test` (owner) or `bob@acme.test` (member). For real use, create an OpenID Connect client at your
provider, set `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` and `MARGA_BOOTSTRAP_OWNER_EMAIL`, and see
[docs/SECURITY.md](docs/SECURITY.md).

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
                       truck_model.py uncertainty.py                   speed and fuel physics, ETA simulation
                       gemma_reasoner.py intake.py                     AI layer, order intake
                       oidc.py accounts.py db.py store.py api.py       sign-in, sessions, organisations, HTTP API
backend/tests/         263 tests
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
| Road distances are right | Kathmandu to Banepa routes at 26.4 km, to Dhulikhel at 29.3 km, to Bhaktapur at 15.1 km, matching the known roads. |
| Elevation is real | SRTM samples for Kathmandu, Dhulikhel and Banepa land within 100 m of published altitudes. |
| Direction matters | The climb from Dolalghat to Dhulikhel burns 13.9 L and the descent 6.0 L. The old model could not tell them apart. |
| Untagged road surfaces | A learned prior predicts held-out surfaces at 83.6%, against 72.8% for my earlier defaults. |
| Rainfall threshold | The published Nepal Himalaya threshold reproduces the quoted 144 mm per 24 h. |
| Planner arithmetic | Tests check that units sum to the order, vehicles equal the rounded-up units per capacity, fuel counts every vehicle, and arrival equals the slowest leg. |
| Speed on the real network | Ordinary requests take 0.05 to 0.35 s. Impossible orders take 1.3 to 1.8 s. |
| Gemma works live | Real calls read typed orders and wrote explanations whose every figure came from code. |
| AI cannot inject numbers | Tests show that model-typed digits, unknown placeholders, bad dates and infeasible proposals are rejected. |
| Sign-in is strict | Forged, expired, wrong-audience, `alg: none` and key-confusion tokens are refused. State and codes are single use. |
| Organisations are separate | Tests try to read and change another organisation's data through every endpoint and fail. Deliberately breaking each protection makes tests fail. |
| Whole site works | The Playwright smoke test signs in through the real flow as an owner and a member, covers every page, the admin tools, sign-out and the phone layout, with no browser errors. |
| Real Google or Cognito sign-in | Not tested. Needs a client in your account. |
| AWS deployment | Not done. The template passes `cfn-lint`. |

## Limits

The hazard score is a screening index built from terrain steepness, optional incident history and rainfall. It is not a
calibrated landslide probability. Grades come from 30 m SRTM data. About 76% of road pieces have no surface tag, so their surface is estimated from tagged roads of the same class. Free-flow speeds are planning defaults by road class. Live traffic is not modelled, and
closures are entered by hand per organisation. Fuel is an engineering estimate that needs calibrating against your fleet logs.
The default network leaves out residential lanes and tracks to keep it fast.

The original workflow specification is in [GIS_Route_Optimization_Workflow.docx](GIS_Route_Optimization_Workflow.docx).
