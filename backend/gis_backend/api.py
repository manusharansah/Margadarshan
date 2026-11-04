"""
FastAPI service.

Public:    GET /health, GET /auth/login, GET /auth/callback
Signed in: everything else. The organisation always comes from the session cookie, never from the request.

    viewer   read the map, inventory, order history, closures
    member   + plan deliveries (POST /optimize-route, /parse-order)
    admin    + manage warehouses, closures, settings, invitations, read the audit log
    owner    + change roles, delete the organisation

State-changing requests must carry ``X-Requested-With: marga`` (and, if present, a matching Origin), on top of the
SameSite=Lax session cookie.

Run:  uvicorn gis_backend.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .accounts import Accounts, Forbidden, LoginDenied, NotFound, SessionInfo, safe_next
from .config import Settings, get_settings
from .db import make_session_factory, utcnow
from .gemma_reasoner import GemmaClient, get_client
from .intake import MAX_TEXT, parse_order
from .models import DeliveryRequest, StorageNode
from .network import Network, NetworkUnavailable
from .oidc import OidcClient, OidcError
from .ratelimit import RateLimiter
from .service import enrich_with_ai, process_request
from .store import WarehouseStore, validate
from .weather import WeatherService

log = logging.getLogger("marga")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


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
    units_in_stock: int = Field(ge=0, le=100_000_000)
    vehicles_available: int = Field(ge=0, le=100_000)
    vehicle_capacity: int = Field(200, gt=0, le=1_000_000)


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    role: str = Field("member", max_length=10)


class RoleIn(BaseModel):
    role: str = Field(max_length=10)


class ClosureIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(gt=0, le=5000)
    reason: str = Field("", max_length=200)
    until_hours: Optional[float] = Field(None, gt=0, le=24 * 365)


class SettingsIn(BaseModel):
    loading_minutes: Optional[float] = Field(None, ge=0, le=480)
    name: Optional[str] = Field(None, min_length=1, max_length=200)


class ConfirmDeleteIn(BaseModel):
    confirm: str = Field(max_length=200)


def create_app(settings: Optional[Settings] = None, network: Optional[Network] = None, session_factory=None,
               weather: Optional[WeatherService] = None, client: Optional[GemmaClient] = None,
               oidc: Optional[OidcClient] = None) -> FastAPI:
    cfg = settings or get_settings()
    cookie_name = "__Host-marga_session" if cfg.cookie_secure else "marga_session"
    origins = {cfg.public_url, *cfg.cors_origins}
    limits: Dict[str, RateLimiter] = {"optimize": RateLimiter(30, 60), "intake": RateLimiter(10, 60),
                                       "ai": RateLimiter(20, 60), "login": RateLimiter(20, 60)}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = client if client is not None else get_client()
        if cfg.require_gemma and app.state.client is None:
            raise RuntimeError("MARGA_REQUIRE_GEMMA is set but GEMMA_API_KEY is missing - refusing to start "
                               "without the AI layer.")
        sf = session_factory or make_session_factory(cfg.database_url)[0]
        app.state.accounts = Accounts(sf, cfg)
        app.state.store = WarehouseStore(sf)
        app.state.oidc = oidc if oidc is not None else (
            OidcClient(cfg.oidc_issuer, cfg.oidc_client_id, cfg.oidc_client_secret, cfg.oidc_redirect_uri)
            if cfg.oidc_client_id else None)
        app.state.results = ResultCache()
        app.state.last_purge = 0.0
        app.state.network, app.state.network_error = network, None
        if app.state.network is None:
            try:
                app.state.network = Network.load(cfg.network_path)
            except NetworkUnavailable as exc:
                app.state.network_error = str(exc)
                log.error("%s", exc)
        app.state.weather = weather
        net: Optional[Network] = app.state.network
        if net and app.state.weather is None and cfg.weather_enabled:
            app.state.weather = WeatherService(net.bbox(), cfg.weather_ttl_seconds)
        if net and app.state.weather and app.state.weather.is_stale():
            if app.state.weather.refresh():
                net.apply_rain(app.state.weather.rain_index_fn())
        app.state.accounts.purge()
        yield

    app = FastAPI(title="MargaAI Nepal - route optimisation API", version="3.0.0", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(origins), allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-Requested-With"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "private, max-age=300" if request.url.path == "/network" else "no-store")
        return resp

    # ---------------------------------------------------------------- helpers
    def client_ip(request: Request) -> str:
        if cfg.trust_proxy:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                # The load balancer appends the address it saw, so the LAST entry is trustworthy; earlier ones are
                # whatever the client sent and can be spoofed to dodge rate limits.
                return fwd.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

    def check_csrf(request: Request) -> None:
        if request.method not in UNSAFE:
            return
        if request.headers.get("x-requested-with") != "marga":
            raise HTTPException(403, "Missing X-Requested-With header")
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            raise HTTPException(403, "Cross-site request refused")

    def current(request: Request) -> Optional[SessionInfo]:
        return request.app.state.accounts.get_session(request.cookies.get(cookie_name, ""))

    def require(role: str) -> Callable:
        def dep(request: Request) -> SessionInfo:
            info = current(request)
            if info is None:
                raise HTTPException(401, "Sign in required")
            if not info.at_least(role):
                raise HTTPException(403, f"This needs the {role} role")
            check_csrf(request)
            return info
        return dep

    def limit(name: str, key: str) -> None:
        if not limits[name].allow(key):
            raise HTTPException(429, "Too many requests. Wait a minute and try again.")

    def net_of(request: Request) -> Network:
        n = request.app.state.network
        if n is None:
            raise HTTPException(503, request.app.state.network_error or "Road network not loaded")
        return n

    def refresh_weather_if_stale(request: Request) -> Optional[dict]:
        w: Optional[WeatherService] = request.app.state.weather
        if w is None:
            return None
        if w.is_stale() and w.refresh():
            net_of(request).apply_rain(w.rain_index_fn())
        return w.status()

    def guard(fn):
        """Map account-layer errors to HTTP statuses."""
        def run(*a, **k):
            try:
                return fn(*a, **k)
            except Forbidden as exc:
                raise HTTPException(403, str(exc)) from exc
            except NotFound as exc:
                raise HTTPException(404, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return run

    # ------------------------------------------------------------------ public
    @app.get("/health")
    def health(request: Request):
        return {"status": "ok" if request.app.state.network else "degraded",
                "network_loaded": request.app.state.network is not None,
                "sign_in_configured": request.app.state.oidc is not None}

    # -------------------------------------------------------------------- auth
    @app.get("/auth/login")
    def login(request: Request, next: str = "/"):
        o: Optional[OidcClient] = request.app.state.oidc
        if o is None:
            raise HTTPException(503, "Sign-in is not configured (OIDC_CLIENT_ID missing).")
        limit("login", client_ip(request))
        state, nonce, verifier = request.app.state.accounts.start_attempt(safe_next(next))
        try:
            return RedirectResponse(o.authorization_url(state, nonce, verifier), status_code=302)
        except OidcError:
            return RedirectResponse("/login?error=provider", status_code=302)

    @app.get("/auth/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        acc: Accounts = request.app.state.accounts
        o: Optional[OidcClient] = request.app.state.oidc
        if o is None:
            raise HTTPException(503, "Sign-in is not configured.")
        limit("login", client_ip(request))
        attempt = acc.consume_attempt(state) if state else None
        if error or not code or attempt is None:
            return RedirectResponse("/login?error=" + ("cancelled" if error else "expired"), status_code=302)
        try:
            claims = o.verify_id_token(o.exchange_code(code, attempt.code_verifier), attempt.nonce)
            user_id, _ = acc.provision_login(o.issuer, str(claims["sub"]), str(claims["email"]), str(claims.get("name") or ""))
        except LoginDenied as exc:
            return RedirectResponse(f"/login?error={exc.code}", status_code=302)
        except OidcError as exc:
            acc.audit(None, None, "login_failed", "", {"reason": str(exc)[:80]})
            return RedirectResponse("/login?error=failed", status_code=302)
        if time.time() - request.app.state.last_purge > 3600:
            request.app.state.last_purge = time.time()
            acc.purge()
        resp = RedirectResponse(attempt.next_path, status_code=302)
        resp.set_cookie(cookie_name, acc.create_session(user_id), httponly=True, secure=cfg.cookie_secure,
                        samesite="lax", path="/", max_age=cfg.session_max_hours * 3600)
        return resp

    @app.post("/auth/logout")
    def logout(request: Request, response: Response):
        check_csrf(request)
        token = request.cookies.get(cookie_name, "")
        info = current(request)
        if info:
            request.app.state.accounts.audit(info.org_id, info.user_id, "logout")
        request.app.state.accounts.end_session(token) if token else None
        response.delete_cookie(cookie_name, path="/", secure=cfg.cookie_secure, httponly=True, samesite="lax")
        return {"signed_out": True}

    @app.get("/auth/me")
    def me(request: Request):
        info = current(request)
        if info is None:
            raise HTTPException(401, "Not signed in")
        return {"user": {"id": info.user_id, "email": info.email, "name": info.name},
                "organisation": {"id": info.org_id, "name": info.org_name}, "role": info.role}

    # --------------------------------------------------------------- read data
    @app.get("/meta")
    def meta(request: Request, info: SessionInfo = Depends(require("viewer"))):
        n = net_of(request)
        w = request.app.state.weather
        ai = request.app.state.client
        return {"network": n.meta, "weather": w.status() if w else {"available": False, "error": "disabled"},
                "warehouses": len(request.app.state.store.list(info.org_id)),
                "reasoner": "gemma" if ai else "deterministic",
                "ai_features": {"explanations": bool(ai), "order_intake": bool(ai), "remedies": bool(ai),
                                "required": cfg.require_gemma},
                "truck_model": {"mass_kg": 8000, "weight_to_power_kg_per_kw": 120}}

    @app.get("/network")
    def network_geojson(request: Request, info: SessionInfo = Depends(require("viewer"))):
        n = net_of(request)
        feats = [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[p[1], p[0]] for p in e.geometry]},
            "properties": {"road_type": e.road_type, "highway": e.highway, "name": e.name, "length_km": e.length_km,
                           "slope_pct": e.slope_pct, "oneway": bool(e.oneway),
                           "hazard": round((n.graph.get_edge_data(e.node_a, e.node_b) or n.graph.get_edge_data(e.node_b, e.node_a)
                                            or {"hazard": e.hazard_terrain})["hazard"], 3)},
        } for e in n.edges]
        return {"type": "FeatureCollection", "features": feats, "bbox": n.bbox()}

    @app.get("/places")
    def places(request: Request, q: str = "", info: SessionInfo = Depends(require("viewer"))):
        return {"places": net_of(request).search_places(q[:80])}

    # --------------------------------------------------------------- inventory
    @app.get("/warehouses")
    def warehouses(request: Request, info: SessionInfo = Depends(require("viewer"))):
        return {"warehouses": [asdict(w) for w in request.app.state.store.list(info.org_id)]}

    @app.put("/warehouses/{wid}")
    def put_warehouse(wid: str, body: WarehouseIn, request: Request, info: SessionInfo = Depends(require("admin"))):
        node = StorageNode(node_id=wid, **body.model_dump())
        try:
            validate(node)
            net_of(request).snap(node.lat, node.lng)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        request.app.state.store.upsert(info.org_id, node)
        request.app.state.accounts.audit(info.org_id, info.user_id, "warehouse_upsert", wid[:64])
        return asdict(node)

    @app.delete("/warehouses/{wid}")
    def delete_warehouse(wid: str, request: Request, info: SessionInfo = Depends(require("admin"))):
        if not request.app.state.store.delete(info.org_id, wid):
            raise HTTPException(404, f"Unknown warehouse '{wid}'")
        request.app.state.accounts.audit(info.org_id, info.user_id, "warehouse_delete", wid[:64])
        return {"deleted": wid}

    @app.post("/warehouses/import")
    async def import_warehouses(file: UploadFile, request: Request, info: SessionInfo = Depends(require("admin"))):
        raw = await file.read(2_000_001)
        if len(raw) > 2_000_000:
            raise HTTPException(413, "File too large (2 MB limit)")
        try:
            count = request.app.state.store.import_csv(info.org_id, raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        request.app.state.accounts.audit(info.org_id, info.user_id, "warehouse_import", "", {"rows": count})
        return {"imported": count}

    # ---------------------------------------------------------------- planning
    @app.post("/optimize-route")
    def optimize_route(req: RouteRequestIn, request: Request, info: SessionInfo = Depends(require("member"))):
        limit("optimize", info.user_id)
        n = net_of(request)
        acc: Accounts = request.app.state.accounts
        weather_status = refresh_weather_if_stale(request)
        settings_ = acc.get_settings(info.org_id)
        dreq = DeliveryRequest(destination_lat=req.destination_lat, destination_lng=req.destination_lng,
                               quantity=req.quantity, deadline_hours=req.deadline_hours,
                               destination_name=req.destination_name)
        try:
            out = process_request(n, request.app.state.store.list(info.org_id), dreq, weather=weather_status,
                                  client=request.app.state.client, defer_ai=True,
                                  graph=n.graph_for(acc.list_closures(info.org_id)),
                                  loading_hours=settings_["loading_minutes"] / 60.0)
        except ValueError as exc:  # destination outside the loaded network
            raise HTTPException(422, str(exc)) from exc
        ctx = out.pop("_ctx", None)
        if ctx is not None:
            ctx["org_id"] = info.org_id
            out["result_id"] = request.app.state.results.put(ctx)
        acc.record_order(info, req.model_dump(), out)
        return out

    @app.post("/optimize-route/{result_id}/ai")
    def optimize_route_ai(result_id: str, request: Request, info: SessionInfo = Depends(require("member"))):
        """Model-written explanation, risk briefing and proposals for an answer already returned by /optimize-route."""
        limit("ai", info.user_id)
        ctx = request.app.state.results.get(result_id)
        client: Optional[GemmaClient] = request.app.state.client
        if ctx is None or ctx.get("org_id") != info.org_id:     # another organisation's answer looks like a missing one
            raise HTTPException(404, "That answer expired. Run the order again.")
        if client is None:
            raise HTTPException(503, "AI is not configured (GEMMA_API_KEY missing).")
        if "patch" not in ctx:
            ctx["patch"] = enrich_with_ai(ctx, client)
        return ctx["patch"]

    @app.post("/parse-order")
    def parse_order_endpoint(body: ParseOrderIn, request: Request, info: SessionInfo = Depends(require("member"))):
        limit("intake", info.user_id)
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

    # --------------------------------------------------------- history, rules
    @app.get("/orders")
    def orders(request: Request, info: SessionInfo = Depends(require("viewer"))):
        return {"orders": request.app.state.accounts.list_orders(info.org_id)}

    @app.get("/closures")
    def closures(request: Request, info: SessionInfo = Depends(require("viewer"))):
        return {"closures": request.app.state.accounts.list_closures(info.org_id)}

    @app.post("/closures")
    def add_closure(body: ClosureIn, request: Request, info: SessionInfo = Depends(require("admin"))):
        until = utcnow() + dt.timedelta(hours=body.until_hours) if body.until_hours else None
        return guard(request.app.state.accounts.add_closure)(info, body.lat, body.lng, body.radius_m, body.reason, until)

    @app.delete("/closures/{cid}")
    def remove_closure(cid: str, request: Request, info: SessionInfo = Depends(require("admin"))):
        guard(request.app.state.accounts.remove_closure)(info, cid)
        return {"deleted": cid}

    @app.get("/settings")
    def get_settings_(request: Request, info: SessionInfo = Depends(require("viewer"))):
        return request.app.state.accounts.get_settings(info.org_id)

    @app.put("/settings")
    def put_settings(body: SettingsIn, request: Request, info: SessionInfo = Depends(require("admin"))):
        return guard(request.app.state.accounts.update_settings)(info, body.loading_minutes, body.name)

    # -------------------------------------------------------------------- team
    @app.get("/team")
    def team(request: Request, info: SessionInfo = Depends(require("viewer"))):
        acc: Accounts = request.app.state.accounts
        return {"members": acc.list_members(info.org_id),
                "invites": acc.list_invites(info.org_id) if info.at_least("admin") else []}

    @app.post("/team/invites")
    def invite(body: InviteIn, request: Request, info: SessionInfo = Depends(require("admin"))):
        return guard(request.app.state.accounts.invite)(info, body.email, body.role)

    @app.delete("/team/invites/{iid}")
    def revoke(iid: str, request: Request, info: SessionInfo = Depends(require("admin"))):
        guard(request.app.state.accounts.revoke_invite)(info, iid)
        return {"revoked": iid}

    @app.put("/team/members/{uid}/role")
    def change_role(uid: str, body: RoleIn, request: Request, info: SessionInfo = Depends(require("owner"))):
        guard(request.app.state.accounts.change_role)(info, uid, body.role)
        return {"user_id": uid, "role": body.role}

    @app.delete("/team/members/{uid}")
    def remove_member(uid: str, request: Request, info: SessionInfo = Depends(require("admin"))):
        guard(request.app.state.accounts.remove_member)(info, uid)
        return {"removed": uid}

    @app.get("/audit")
    def audit(request: Request, info: SessionInfo = Depends(require("admin"))):
        return {"entries": request.app.state.accounts.list_audit(info.org_id)}

    # ------------------------------------------------------- your data, deletion
    @app.get("/me/export")
    def export_me(request: Request, info: SessionInfo = Depends(require("viewer"))):
        request.app.state.accounts.audit(info.org_id, info.user_id, "data_export")
        return request.app.state.accounts.export_user(info)

    @app.delete("/me")
    def delete_me(body: ConfirmDeleteIn, request: Request, response: Response,
                  info: SessionInfo = Depends(require("viewer"))):
        if body.confirm != "DELETE":
            raise HTTPException(422, 'Type DELETE to confirm.')
        guard(request.app.state.accounts.delete_user)(info)
        response.delete_cookie(cookie_name, path="/", secure=cfg.cookie_secure, httponly=True, samesite="lax")
        return {"deleted": True}

    @app.delete("/org")
    def delete_org(body: ConfirmDeleteIn, request: Request, response: Response,
                   info: SessionInfo = Depends(require("owner"))):
        guard(request.app.state.accounts.delete_org)(info, body.confirm)
        response.delete_cookie(cookie_name, path="/", secure=cfg.cookie_secure, httponly=True, samesite="lax")
        return {"deleted": True}

    return app


app = create_app()
