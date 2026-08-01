# MargaAI Nepal

**MargaAI Nepal** (मार्ग — "route"/"path") is a GIS-powered decision support system that recommends the best delivery route(s) for an order and explains that recommendation in plain language — built for Nepal's hilly, disaster-prone terrain.

> Hackathon project. All inventory, vehicle, and road/hazard data in this phase is **simulated** — no live APIs or real-world map data are used yet.

## Table of Contents

- [MargaAI Nepal](#margaai-nepal)
  - [Table of Contents](#table-of-contents)
  - [Problem Statement](#problem-statement)
  - [Proposed Solution](#proposed-solution)
  - [How It Works](#how-it-works)
  - [Tech Stack](#tech-stack)
  - [Repository Structure](#repository-structure)
  - [Getting Started](#getting-started)
    - [Backend Setup](#backend-setup)
    - [Frontend Setup](#frontend-setup)
    - [Connecting Frontend to Backend](#connecting-frontend-to-backend)
  - [Data Model](#data-model)
  - [Fulfillment Priority Order](#fulfillment-priority-order)
  - [Known Placeholders / Current-Phase Assumptions](#known-placeholders--current-phase-assumptions)
  - [Future Scope](#future-scope)
  - [Reference](#reference)

## Problem Statement

Nepal's hilly and mountainous terrain creates significant transportation and logistics challenges:

- Delivery routes are often chosen without properly accounting for real terrain difficulty (slope, road surface quality, road length).
- Natural disasters — particularly landslides and floods — disrupt transport lines without warning, and this risk is rarely factored into route selection.
- There's no coordinated visibility into which storage units actually hold enough stock **and** enough available vehicles to fulfill a given order on time.

## Proposed Solution

The system recommends the best delivery route(s) for an order and explains that recommendation to the requesting dealer. It:

- Maintains data on multiple storage units — location, inventory, and vehicles currently available.
- Maintains a road network graph carrying terrain and hazard attributes for every road segment.
- Computes optimized routes using classical routing algorithms — **Yen's K-shortest paths**, extended with a **Held-Karp TSP solver** for multi-stop pickup scenarios.
- Uses **Gemma 4** as an LLM reasoning layer for selecting among multiple valid warehouses, combining multi-source results into one coherent plan, and explaining in plain language why that plan was chosen over the alternatives.

Gemma is deliberately scoped to reasoning and language — **not** arithmetic. All costs, totals, and feasibility checks are computed deterministically in code before Gemma ever sees a candidate plan.

## How It Works

The dealer submits exactly three inputs — no priority sliders:

1. **Destination** — the delivery location
2. **Quantity required (Q)** — number of units needed
3. **Delivery deadline** — the system's only hard time constraint

The backend then runs the request through a fixed pipeline:

1. **Edge weight computation** — every road segment's cost is precomputed from a fixed multi-metric formula (slope, road type, hazard score, distance, max speed).
2. **Source feasibility scan** — checks every storage node for `units_in_stock ≥ Q` and `vehicles_available × 200 ≥ Q`.
3. **Single-source routing** (if a qualifying node exists) — Yen's K-shortest paths from that node to the destination.
4. **Multi-source combination** (if no single node qualifies) — combines nodes' stock/vehicle capacity in parallel, or falls back to a single vehicle doing a sequential multi-stop (TSP) pickup if vehicle count, not stock, is the binding constraint.
5. **Deterministic feasibility check** — total units fulfilled, delivery time, and a fuel-efficiency estimate are computed in code; infeasible plans are discarded before Gemma sees them.
6. **Gemma reasoning** — Gemma selects the best remaining plan (preferring the fewest sources/vehicles) and explains the choice in plain language.
7. **Output** — recommended route(s) on the map, source warehouse(s), vehicle count, expected arrival time, and Gemma's explanation.

If nothing in the network can satisfy the order in time, the system reports that directly, along with the reason.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, NetworkX (routing/graph), Pydantic |
| Routing algorithms | Yen's K-shortest paths, Held-Karp TSP (exact) |
| LLM reasoning | Gemma 4 via Google AI Studio (Generative Language API) |
| Frontend | React 19, TanStack Start / Router, Vite, Tailwind CSS, shadcn/ui (Radix), Leaflet (map) |
| Package managers | `pip` (backend), `npm` or `bun` (frontend) |

## Repository Structure

```
.
├── backend/
│   └── gis_backend/
│       ├── models.py           Data classes: StorageNode, RoadEdge, DeliveryRequest, CandidatePlan...
│       ├── simulate.py         Random but connected network generator (simulated data only)
│       ├── edge_weight.py      Fixed multi-metric edge cost / time / fuel formulas
│       ├── routing.py          Graph builder, Yen's K-shortest paths, Held-Karp TSP
│       ├── feasibility.py      Deterministic single-source / vehicle-capacity checks
│       ├── source_selector.py  Single-source, multi-source parallel, multi-stop fallback logic
│       ├── gemma_reasoner.py   Gemma 4 interface (LiveGemmaReasoner + offline MockGemmaReasoner)
│       ├── service.py          Orchestrates the full pipeline end to end
│       ├── api.py              FastAPI app: GET /network, POST /optimize-route
│       └── demo.py             Offline CLI demo, no server needed
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/gis/     Map + workflow UI components
│   │   ├── components/ui/      shadcn/ui components
│   │   ├── lib/gis/            Client-side engine + types + API bridge to the backend
│   │   └── routes/             TanStack Router pages
│   └── package.json
│
└── GIS_Route_Optimization_Workflow.docx   Full workflow specification
```

## Getting Started

### Backend Setup

Requires Python 3.12+.

```bash
cd backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Run the offline CLI demo** (no server needed) — simulates a 12-node network and runs three example requests (comfortably feasible, needs multiple sources, infeasible-by-deadline):

```bash
python -m gis_backend.demo
```

**Run the API server:**

```bash
uvicorn gis_backend.api:app --reload
```

- `GET /network` — returns the simulated nodes and edges (for the map).
- `POST /optimize-route` — body `{"destination": "W3", "quantity": 500, "deadline_hours": 6}`, returns the chosen plan (or an infeasibility explanation) plus rejected alternatives.

**Wiring in real Gemma 4** (optional — the API works without it but returns `reasoning_unavailable` for the selection/explanation step):

1. Get a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Set environment variables before starting the server:

   ```bash
   export GEMMA_API_KEY="your-key-here"
   export GEMMA_MODEL="gemma-4-26b-a4b-it"   # default
   ```

   Other model IDs available on AI Studio (confirm access with your key): `gemma-4-31b-it` (stronger reasoning), `gemma-4-12b-it`, `gemma-4-e4b-it`, `gemma-4-e2b-it` (smallest/fastest).

There is no silent mock fallback — if `GEMMA_API_KEY` isn't set or the live call fails, `POST /optimize-route` returns HTTP `503` with a `reasoning_unavailable` status, the routing/feasibility math (always deterministic), and all candidate plans — never a disguised guess presented as Gemma's answer. `MockGemmaReasoner` exists purely as an offline dev tool and must be passed in explicitly (e.g. `python -m gis_backend.demo --mock`); it never runs automatically.

### Frontend Setup

Requires Node.js (or Bun) and npm.

```bash
cd frontend
npm install
npm run dev
```

Other scripts:

```bash
npm run build       # production build
npm run preview     # preview the production build
npm run lint         # eslint
npm run format       # prettier
```

### Connecting Frontend to Backend

The frontend can run two ways, controlled by a single environment variable:

- **`GIS_API_BASE` set** (e.g. `http://localhost:8000`, or an ngrok URL) → every call is proxied to the real FastAPI backend (`GET /network`, `POST /optimize-route`) — same response shape either way.
- **`GIS_API_BASE` unset** → an in-process TypeScript port of the same engine (`src/lib/gis/engine.ts`) runs standalone, so the UI works without the Python backend running.

To point the frontend at your local backend:

```bash
export GIS_API_BASE="http://localhost:8000"
npm run dev
```

## Data Model

**Storage node** — `node_id`, `location`, `units_in_stock`, `vehicles_available`, `vehicle_capacity` (fixed at 200 units/vehicle this phase).

**Road (graph edge)** — `edge_id`, `endpoints`, `length`, `slope`, `road_type` (paved / gravelled / rough), `max_speed`, `intersection_points`, `hazard_score` (static simulated flood/landslide risk), `computed_weight` (derived from all metrics via a fixed formula).

**Vehicle** — `vehicle_id`, `home_node`, `capacity` (200 units, fixed), `status` (available / dispatched).

## Fulfillment Priority Order

When more than one fulfillment approach is feasible, the system prefers the simplest sufficient option, in this order:

1. Single warehouse, single vehicle.
2. Single warehouse, multiple vehicles running in parallel.
3. Multiple warehouses, parallel shipments (no single warehouse can satisfy the order alone).
4. Multiple warehouses, single vehicle visiting them sequentially — TSP-style pickup (used when vehicle availability, not stock, is the binding constraint).
5. If none of the above meets Q within the deadline, the system reports the demand cannot be fulfilled, with the reason.

## Known Placeholders / Current-Phase Assumptions

- Vehicle capacity is fixed at 200 units/vehicle; per-vehicle customization is deferred to a later iteration.
- Hazard and terrain data are static and fully simulated — no live weather or disaster feeds.
- Road network topology, node locations, and all attribute values are synthetic; no real-world map data is fetched.
- There are no user-facing priority weights — the routing objective minimizes the combined multi-metric edge cost, subject to quantity and deadline as hard constraints.
- The fuel-efficiency formula (`edge_weight.compute_edge_fuel`) is a placeholder proxy (distance × slope factor × road-type factor), not yet finalized.
- Backend Gemma integration is unit-tested against a mocked HTTP layer but **not yet tested end-to-end** against the real Google AI Studio API.

## Future Scope

- Dynamic hazard events — simulating a road going down mid-demo with live rerouting.
- Configurable, per-vehicle capacities instead of a fixed 200-unit limit.
- Real-world road network topology (e.g. via OpenStreetMap) with simulated attributes layered on top.
- Live data feeds for weather and disaster alerts, replacing static simulated hazard scores.

## Reference

Full workflow specification: [`GIS_Route_Optimization_Workflow.docx`](./GIS_Route_Optimization_Workflow.docx)