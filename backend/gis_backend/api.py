"""
FastAPI backend. Two endpoints for now:
  GET  /network         -- simulated storage nodes + road edges (for the map)
  POST /optimize-route   -- run the full workflow for a delivery request

Run locally with:
  uvicorn gis_backend.api:app --reload
"""
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .models import DeliveryRequest
from .routing import build_graph
from .service import process_request
from .simulate import simulate_network

app = FastAPI(title="GIS Route Optimization Backend")

# CORS: wide open for hackathon/demo purposes so the (not-yet-built) frontend
# map can call this API from a browser during dev. Tighten allow_origins to
# the real frontend's origin before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simulated network, generated once at startup (Section 9: fully simulated
# data for this phase, no live data fetching). The graph is also built once
# here -- not lazily per-request -- so edge weight/time/fuel are always
# populated for /network, and /optimize-route doesn't recompute them on
# every call.
NODES, EDGES = simulate_network(num_nodes=12, seed=7)
GRAPH = build_graph(NODES, EDGES)


class RouteRequestIn(BaseModel):
    destination: str
    quantity: int = Field(gt=0, description="Units required; must be positive")
    deadline_hours: float = Field(gt=0, description="Delivery deadline in hours; must be positive")

    @field_validator("destination")
    @classmethod
    def destination_must_exist(cls, v: str) -> str:
        if v not in NODES:
            valid = ", ".join(sorted(NODES.keys()))
            raise ValueError(f"Unknown destination '{v}'. Known nodes: {valid}")
        return v


@app.get("/network")
def get_network():
    return {
        "nodes": [asdict(n) for n in NODES.values()],
        "edges": [asdict(e) for e in EDGES],
    }


@app.post("/optimize-route")
def optimize_route(req: RouteRequestIn):
    request = DeliveryRequest(
        destination=req.destination, quantity=req.quantity, deadline_hours=req.deadline_hours
    )
    try:
        result = process_request(NODES, EDGES, request, graph=GRAPH)
    except Exception as exc:  # pragma: no cover - safety net for unexpected pipeline errors
        raise HTTPException(status_code=500, detail=f"Route optimization failed: {exc}") from exc

    result.pop("chosen_plan_object", None)  # internal only, not JSON-serializable

    if result.get("status") == "reasoning_unavailable":
        # Deterministic routing/feasibility math succeeded (see candidate_plans),
        # but the Gemma selection/explanation step could not run. 503, not 200 --
        # this is an upstream dependency being down, not a normal answer.
        return JSONResponse(status_code=503, content=result)

    return result
