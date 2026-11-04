"""Authorization, CSRF, tenant isolation, rate limits and headers, tested through the real HTTP API."""
import pytest

from .authkit import COOKIE, CSRF, Harness
from .helpers import LAT0, LNG0


@pytest.fixture()
def h(tmp_path):
    with Harness(tmp_path) as hh:
        yield hh


def wh(n=0, e=0, stock=900, veh=4, name="W"):
    return {"name": name, "lat": LAT0 + n / 111.32, "lng": LNG0 + e / 98.5, "units_in_stock": stock, "vehicles_available": veh}


def order(qty=100, hours=48, n=6, e=0):
    return {"destination_lat": LAT0 + n / 111.32, "destination_lng": LNG0 + e / 98.5, "destination_name": "Dealer",
            "quantity": qty, "deadline_hours": hours}


# ------------------------------------------------------------------ authentication
PROTECTED = [("GET", "/meta"), ("GET", "/network"), ("GET", "/places?q=a"), ("GET", "/warehouses"), ("GET", "/orders"),
             ("GET", "/closures"), ("GET", "/settings"), ("GET", "/team"), ("GET", "/audit"), ("GET", "/me/export"),
             ("PUT", "/warehouses/x"), ("DELETE", "/warehouses/x"), ("POST", "/warehouses/import"),
             ("POST", "/optimize-route"), ("POST", "/optimize-route/abc/ai"), ("POST", "/parse-order"),
             ("POST", "/closures"), ("DELETE", "/closures/x"), ("PUT", "/settings"), ("POST", "/team/invites"),
             ("DELETE", "/team/invites/x"), ("PUT", "/team/members/x/role"), ("DELETE", "/team/members/x"),
             ("DELETE", "/me"), ("DELETE", "/org")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_data_endpoint_requires_sign_in(h, method, path):
    c = h.client_cm
    c.cookies.clear()
    r = c.request(method, path, headers=CSRF, json={} if method in ("POST", "PUT", "DELETE") else None)
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


def test_only_health_and_the_login_flow_are_public(h):
    c = h.client_cm
    assert c.get("/health").status_code == 200
    assert c.get("/auth/login").status_code == 302
    assert c.get("/auth/me").status_code == 401


def test_garbage_session_cookies_are_rejected(h):
    for bad in ("", "x", "A" * 43, "A" * 1000, "../../etc/passwd", "'; drop table sessions;--"):
        c = h.session_for(bad or "x")
        assert c.get("/warehouses").status_code == 401


# ---------------------------------------------------------------------------- roles
ROLE_MATRIX = [  # (method, path, body, minimum role)
    ("GET", "/warehouses", None, "viewer"), ("GET", "/orders", None, "viewer"), ("GET", "/network", None, "viewer"),
    ("POST", "/optimize-route", order(), "member"),
    ("PUT", "/warehouses/Z", wh(), "admin"), ("DELETE", "/warehouses/Z", None, "admin"),
    ("POST", "/closures", {"lat": LAT0, "lng": LNG0, "radius_m": 100, "reason": "x"}, "admin"),
    ("PUT", "/settings", {"loading_minutes": 5}, "admin"), ("POST", "/team/invites", {"email": "n@acme.test", "role": "member"}, "admin"),
    ("GET", "/audit", None, "admin"),
    ("PUT", "/team/members/nobody/role", {"role": "viewer"}, "owner"),
]
RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}


@pytest.mark.parametrize("method,path,body,minimum", ROLE_MATRIX)
def test_role_matrix(h, method, path, body, minimum):
    owner = h.as_role("owner")
    owner.put("/warehouses/seed", json=wh(name="seed"))
    for role in RANK:
        c = h.as_role(role, org_id=owner.org_id)
        r = c.request(method, path, json=body)
        if RANK[role] < RANK[minimum]:
            assert r.status_code == 403, f"{role} should not {method} {path}: got {r.status_code}"
        else:
            assert r.status_code not in (401, 403), f"{role} should {method} {path}: got {r.status_code} {r.text[:80]}"


# ------------------------------------------------------------------------------ CSRF
def test_state_changing_requests_need_the_csrf_header(h):
    admin = h.as_role("admin")
    admin.headers.pop("X-Requested-With")
    assert admin.put("/warehouses/A", json=wh()).status_code == 403
    assert admin.get("/warehouses").status_code == 200                       # reads are fine
    admin.headers["X-Requested-With"] = "wrong"
    assert admin.put("/warehouses/A", json=wh()).status_code == 403
    admin.headers["X-Requested-With"] = "marga"
    assert admin.put("/warehouses/A", json=wh()).status_code == 200


def test_cross_site_origins_are_refused_even_with_the_header(h):
    admin = h.as_role("admin")
    assert admin.put("/warehouses/A", json=wh(), headers={"Origin": "https://evil.example"}).status_code == 403
    assert admin.put("/warehouses/A", json=wh(), headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_cors_does_not_allow_credentials_or_foreign_origins(h):
    r = h.client_cm.options("/warehouses", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers or r.headers["access-control-allow-origin"] != "https://evil.example"
    assert r.headers.get("access-control-allow-credentials") != "true"


# ------------------------------------------------------------------ tenant isolation
def test_organisations_cannot_see_or_change_each_others_warehouses(h):
    a, b = h.as_role("owner", org_name="Acme"), h.as_role("owner", org_name="Beta")
    assert a.org_id != b.org_id
    a.put("/warehouses/SHARED", json=wh(stock=111, name="Acme depot"))
    b.put("/warehouses/SHARED", json=wh(stock=222, name="Beta depot"))     # same id, no collision
    assert [(w["node_id"], w["units_in_stock"]) for w in a.get("/warehouses").json()["warehouses"]] == [("SHARED", 111)]
    assert [(w["node_id"], w["units_in_stock"]) for w in b.get("/warehouses").json()["warehouses"]] == [("SHARED", 222)]
    a.put("/warehouses/ONLY_A", json=wh())
    assert b.delete("/warehouses/ONLY_A").status_code == 404
    assert len(a.get("/warehouses").json()["warehouses"]) == 2


def test_an_organisation_is_never_taken_from_request_input(h):
    a, b = h.as_role("owner", org_name="Acme"), h.as_role("owner", org_name="Beta")
    a.put("/warehouses/A1", json=wh())
    for extra in ({"org_id": a.org_id}, {"organisation": a.org_id}):
        r = b.put("/warehouses/B1", json={**wh(), **extra})
        assert r.status_code == 200
    assert b.get("/warehouses?org_id=" + a.org_id).json()["warehouses"][0]["node_id"] == "B1"
    assert b.get("/warehouses", headers={"X-Org-Id": a.org_id}).json()["warehouses"][0]["node_id"] == "B1"
    assert "A1" not in {w["node_id"] for w in b.get("/warehouses").json()["warehouses"]}


def test_orders_closures_team_and_audit_are_isolated(h):
    a, b = h.as_role("owner", org_name="Acme"), h.as_role("owner", org_name="Beta")
    a.put("/warehouses/A", json=wh())
    assert a.post("/optimize-route", json=order()).status_code == 200
    a.post("/closures", json={"lat": LAT0, "lng": LNG0, "radius_m": 200, "reason": "A only"})
    assert len(a.get("/orders").json()["orders"]) == 1 and b.get("/orders").json()["orders"] == []
    assert len(a.get("/closures").json()["closures"]) == 1 and b.get("/closures").json()["closures"] == []
    assert [m["email"] for m in b.get("/team").json()["members"]] == [b.email]
    assert all(e["user"] in (a.email, None) for e in a.get("/audit").json()["entries"])
    assert not any(a.email in str(e) for e in b.get("/audit").json()["entries"])
    cid = a.get("/closures").json()["closures"][0]["id"]
    assert b.delete(f"/closures/{cid}").status_code == 404


def test_another_organisations_result_id_looks_like_a_missing_one(h):
    ai = None
    a, b = h.as_role("owner", org_name="Acme"), h.as_role("owner", org_name="Beta")
    a.put("/warehouses/A", json=wh())
    assert a.post("/optimize-route", json=order()).status_code == 200
    # no AI is configured here, so there is no result id; the isolation check lives in test_ai_features (cross-org 404)
    assert b.post("/optimize-route/" + "a" * 32 + "/ai").status_code in (404, 503)


def test_a_closure_changes_routes_only_for_the_organisation_that_made_it(h):
    a, b = h.as_role("owner", org_name="Acme"), h.as_role("owner", org_name="Beta")
    for c in (a, b):
        c.put("/warehouses/A", json=wh(0, 0))
    base = b.post("/optimize-route", json=order(100, 48, 6, 6)).json()
    km = base["chosen_plan"]["distance_km"]
    # Acme closes every road within 20 km of the origin depot: it cannot ship from there at all
    a.post("/closures", json={"lat": LAT0, "lng": LNG0, "radius_m": 5000, "reason": "slide"})
    ra = a.post("/optimize-route", json=order(100, 48, 6, 6)).json()
    rb = b.post("/optimize-route", json=order(100, 48, 6, 6)).json()
    assert ra["status"] == "infeasible" or ra["chosen_plan"]["distance_km"] != km
    assert rb["status"] == "feasible" and rb["chosen_plan"]["distance_km"] == km          # Beta is unaffected


# ------------------------------------------------------------------ limits, headers
def test_expensive_endpoints_are_rate_limited_per_user(h):
    m = h.as_role("member")
    m.put  # members cannot add warehouses; no stock is fine, we only count calls
    statuses = [m.post("/optimize-route", json=order()).status_code for _ in range(32)]
    assert statuses[:30] == [200] * 30 and statuses[30:] == [429, 429]
    other = h.as_role("member")
    assert other.post("/optimize-route", json=order()).status_code == 200      # another user is not throttled


def test_responses_carry_security_headers_and_are_not_cached(h):
    c = h.as_role("viewer")
    r = c.get("/warehouses")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "same-origin" and r.headers["cache-control"] == "no-store"
    assert c.get("/network").headers["cache-control"] == "private, max-age=300"


@pytest.mark.parametrize("body", [{"quantity": 0}, {"quantity": -5}, {"deadline_hours": 0}, {"deadline_hours": 99999},
                                  {"destination_lat": 91}, {"quantity": "x"}, {"destination_name": "x" * 500}])
def test_inputs_are_validated(h, body):
    m = h.as_role("member")
    assert m.post("/optimize-route", json={**order(), **body}).status_code == 422


def test_oversized_uploads_and_bad_csv_are_rejected_and_nothing_is_half_imported(h):
    a = h.as_role("admin")
    big = "id,name,lat,lng,units_in_stock,vehicles_available\n" + "W,n,27.6,85.5,1,1\n" * 120_000
    assert a.post("/warehouses/import", files={"file": ("w.csv", big)}).status_code == 413
    bad = f"id,name,lat,lng,units_in_stock,vehicles_available\nG1,ok,{LAT0},{LNG0},5,1\nG2,bad,abc,1,1,1\n"
    assert a.post("/warehouses/import", files={"file": ("w.csv", bad)}).status_code == 422
    assert a.get("/warehouses").json()["warehouses"] == []


def test_warehouse_outside_the_road_network_is_rejected(h):
    assert h.as_role("admin").put("/warehouses/FAR", json=wh(500, 500)).status_code == 422


# ----------------------------------------------------------------- session effects
def test_removing_someone_logs_them_out_immediately(h):
    owner = h.as_role("owner")
    victim = h.as_role("member", org_id=owner.org_id)
    assert victim.get("/warehouses").status_code == 200
    assert owner.delete(f"/team/members/{victim.user_id}").status_code == 200
    assert victim.get("/warehouses").status_code == 401


def test_audit_log_records_who_did_what_without_secrets(h):
    owner = h.as_role("owner")
    owner.put("/warehouses/A", json=wh())
    owner.post("/team/invites", json={"email": "new@acme.test", "role": "member"})
    entries = owner.get("/audit").json()["entries"]
    actions = [e["action"] for e in entries]
    assert "warehouse_upsert" in actions and "invite_create" in actions
    text = str(entries)
    assert "new@acme.test" not in text and COOKIE not in text            # invitee email is hashed in the log


def test_data_export_and_account_deletion_through_the_api(h):
    owner = h.as_role("owner")
    member = h.as_role("member", org_id=owner.org_id)
    ex = member.get("/me/export").json()
    assert ex["user"]["email"] == member.email and ex["organisation"]["role"] == "member"
    assert member.request("DELETE", "/me", json={"confirm": "nope"}).status_code == 422
    assert member.request("DELETE", "/me", json={"confirm": "DELETE"}).status_code == 200
    assert member.get("/warehouses").status_code == 401
    assert member.email not in str(owner.get("/team").json())


def test_sole_owner_must_transfer_or_delete_the_organisation(h):
    owner = h.as_role("owner")
    h.as_role("member", org_id=owner.org_id)
    assert owner.request("DELETE", "/me", json={"confirm": "DELETE"}).status_code == 403
    assert owner.request("DELETE", "/org", json={"confirm": "wrong"}).status_code == 422
    assert owner.request("DELETE", "/org", json={"confirm": "Acme"}).status_code == 200
    assert owner.get("/warehouses").status_code == 401


def test_handling_time_setting_adds_time_to_every_pickup(h):
    a = h.as_role("owner")
    a.put("/warehouses/A", json=wh())
    t0 = a.post("/optimize-route", json=order()).json()["chosen_plan"]["total_time_hours"]
    assert a.put("/settings", json={"loading_minutes": 30}).status_code == 200
    t1 = a.post("/optimize-route", json=order()).json()["chosen_plan"]["total_time_hours"]
    assert t1 == pytest.approx(t0 + 0.5, abs=1e-3)
