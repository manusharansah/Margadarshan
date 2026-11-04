"""
FastAPI service.

    GET    /health               liveness + data readiness
    GET    /meta                 provenance of every data layer (sources, counts, freshness)
    GET    /network              road network as GeoJSON-like features for the map
    GET    /places?q=            place-name search (OpenStreetMap place nodes)
    GET    /warehouses           inventory (public read)
    PUT    /warehouses/{id}      create/update            [admin key]
    DELETE /warehouses/{id}                                 [admin key]
    POST   /warehouses/import    CSV bulk upsert           [admin key]
    POST   /optimize-route       full workflow

Run:  uvicorn gis_backend.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .gemma_reasoner import GemmaClient, get_client
from .intake import MAX_TEXT, parse_order
from .models import DeliveryRequest, StorageNode
from .network import Network, NetworkUnavailable
from .service import enrich_with_ai, process_request
from .store import WarehouseStore, validate
from .weather import WeatherService

log = logging.getLogger("marga")


class ResultCache:
    """Short-lived store of computed answers so the AI step can reuse their plans (15 min, 200 entries)."""

    def __init__(self, ttl: float = 900.0, size: int = 200):
        self._d: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock, self.ttl, self.size = threading.Lock(), ttl, size

    def put(self, ctx: dict) -> str:
        rid = uuid.uuid4().hex
        with self._lock:
            self._d[rid] = (time.time() + self.ttl, ctx)
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return rid

    def get(self, rid: str) -> Optional[dict]:
        with self._lock:
            hit = self._d.get(rid)
            if not hit or hit[0] < time.time():
                self._d.pop(rid, None)
                return None
            return hit[1]


class RouteRequestIn(BaseModel):
    destination_lat: float = Field(ge=-90, le=90)
    destination_lng: float = Field(ge=-180, le=180)
    destination_name: str = Field("Destination", max_length=120)
    quantity: int = Field(gt=0, le=10_000_000, description="Units required")
    deadline_hours: float = Field(gt=0, le=720, description="Hours until delivery is due")


class ParseOrderIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class WarehouseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    units_in_stock: int = Field(ge=0)
    vehicles_available: int = Field(ge=0)
    vehicle_capacity: int = Field(200, gt=0)


def create_app(settings: Optional[Settings] = None, network: Optional[Network] = None,
               store: Optional[WarehouseStore] = None, weather: Optional[WeatherService] = None,
               client: Optional[GemmaClient] = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = client if client is not None else get_client()
        app.state.results = ResultCache()
        if cfg.require_gemma and app.state.client is None:
            raise RuntimeError("MARGA_REQUIRE_GEMMA is set but GEMMA_API_KEY is missing - refusing to start "
                               "without the AI layer.")
        app.state.network, app.state.network_error = network, None
        if app.state.network is None:
            try:
                app.state.network = Network.load(cfg.network_path)
            except NetworkUnavailable as exc:
                app.state.network_error = str(exc)
                log.error("%s", exc)
        app.state.store = store or WarehouseStore(cfg.database_url)
        app.state.weather = weather
        net: Optional[Network] = app.state.network
        if net and app.state.weather is None and cfg.weather_enabled:
            app.state.weather = WeatherService(net.bbox(), cfg.weather_ttl_seconds)
        if net and app.state.weather and app.state.weather.is_stale():
            if app.state.weather.refresh():
                net.apply_rain(app.state.weather.rain_index_fn())
        yield

    app = FastAPI(title="MargaAI Nepal - route optimisation API", version="2.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["*"])

    def net_of(request: Request) -> Network:
        n = request.app.state.network
        if n is None:
            raise HTTPException(503, request.app.state.network_error or "Road network not loaded")
        return n

    def require_admin(x_api_key: str = Header(default="")) -> None:
        if not cfg.admin_api_key:
            raise HTTPException(503, "Write access is disabled: MARGA_ADMIN_API_KEY is not configured")
        if not hmac.compare_digest(x_api_key, cfg.admin_api_key):
            raise HTTPException(401, "Invalid or missing X-API-Key")

    def refresh_weather_if_stale(request: Request) -> Optional[dict]:
        w: Optional[WeatherService] = request.app.state.weather
        if w is None:
            return None
        if w.is_stale() and w.refresh():
            net_of(request).apply_rain(w.rain_index_fn())
        return w.status()

    @app.get("/health")
    def health(request: Request):
        return {"status": "ok" if request.app.state.network else "degraded",
                "network_loaded": request.app.state.network is not None,
                "network_error": request.app.state.network_error,
                "warehouses": len(request.app.state.store.list())}

    @app.get("/meta")
    def meta(request: Request):
        n = net_of(request)
        w = request.app.state.weather
        return {"network": n.meta, "weather": w.status() if w else {"available": False, "error": "disabled"},
                "warehouses": len(request.app.state.store.list()),
                "reasoner": "gemma" if request.app.state.client else "deterministic",
                "ai_features": {"explanations": bool(request.app.state.client), "order_intake": bool(request.app.state.client),
                                "remedies": bool(request.app.state.client), "required": cfg.require_gemma}}

    @app.get("/network")
    def network_geojson(request: Request):
        n = net_of(request)
        feats = [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[p[1], p[0]] for p in e.geometry]},
            "properties": {"road_type": e.road_type, "highway": e.highway, "name": e.name,
                           "length_km": e.length_km, "slope_pct": e.slope_pct, "hazard": round(
                               n.graph[e.node_a][e.node_b]["hazard"], 3) if n.graph.has_edge(e.node_a, e.node_b) else e.hazard_terrain},
        } for e in n.edges]
        return {"type": "FeatureCollection", "features": feats, "bbox": n.bbox()}

    @app.get("/places")
    def places(request: Request, q: str = ""):
        return {"places": net_of(request).search_places(q)}

    @app.get("/warehouses")
    def warehouses(request: Request):
        return {"warehouses": [asdict(w) for w in request.app.state.store.list()]}

    @app.put("/warehouses/{wid}", dependencies=[Depends(require_admin)])
    def put_warehouse(wid: str, body: WarehouseIn, request: Request):
        node = StorageNode(node_id=wid, **body.model_dump())
        try:
            validate(node)
            net_of(request).snap(node.lat, node.lng)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return asdict(request.app.state.store.upsert(node))

    @app.delete("/warehouses/{wid}", dependencies=[Depends(require_admin)])
    def delete_warehouse(wid: str, request: Request):
        if not request.app.state.store.delete(wid):
            raise HTTPException(404, f"Unknown warehouse '{wid}'")
        return {"deleted": wid}

    @app.post("/warehouses/import", dependencies=[Depends(require_admin)])
    async def import_warehouses(file: UploadFile, request: Request):
        try:
            count = request.app.state.store.import_csv((await file.read()).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"imported": count}

    @app.post("/optimize-route")
    def optimize_route(req: RouteRequestIn, request: Request):
        n = net_of(request)
        weather_status = refresh_weather_if_stale(request)
        dreq = DeliveryRequest(destination_lat=req.destination_lat, destination_lng=req.destination_lng,
                               quantity=req.quantity, deadline_hours=req.deadline_hours,
                               destination_name=req.destination_name)
        try:
            out = process_request(n, request.app.state.store.list(), dreq, weather=weather_status,
                                  client=request.app.state.client, defer_ai=True)
            ctx = out.pop("_ctx", None)
            if ctx is not None:
                out["result_id"] = request.app.state.results.put(ctx)
            return out
        except ValueError as exc:  # destination outside the loaded network
            raise HTTPException(422, str(exc)) from exc

    @app.post("/optimize-route/{result_id}/ai")
    def optimize_route_ai(result_id: str, request: Request):
        """Model-written explanation, risk briefing and proposals for an answer already returned by /optimize-route."""
        ctx = request.app.state.results.get(result_id)
        client: Optional[GemmaClient] = request.app.state.client
        if ctx is None:
            raise HTTPException(404, "That answer expired. Run the order again.")
        if client is None:
            raise HTTPException(503, "AI is not configured (GEMMA_API_KEY missing).")
        if "patch" not in ctx:
            ctx["patch"] = enrich_with_ai(ctx, client)
        return ctx["patch"]

    @app.post("/parse-order")
    def parse_order_endpoint(body: ParseOrderIn, request: Request):
        n = net_of(request)
        c: Optional[GemmaClient] = request.app.state.client
        if c is None:
            raise HTTPException(503, "AI order intake is not configured (GEMMA_API_KEY missing).")
        try:
            return parse_order(body.text, c, n.search_places)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:  # Gemma unreachable / malformed output
            raise HTTPException(502, f"The AI service couldn't read that order: {exc}") from exc

    return app


app = create_app()
