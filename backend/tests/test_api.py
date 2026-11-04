"""Functional API behaviour (planning, inventory, validation) for a signed-in owner. Authorization, tenancy and
sign-in are covered in test_security.py, test_accounts.py and test_auth_flow.py."""
import pytest

from .authkit import Harness
from .helpers import LAT0, LNG0


@pytest.fixture()
def owner(tmp_path):
    with Harness(tmp_path) as h:
        yield h.as_role("owner")


def wh(n=0, e=0, stock=900, veh=4, name="W"):
    return {"name": name, "lat": LAT0 + n / 111.32, "lng": LNG0 + e / 98.5, "units_in_stock": stock, "vehicles_available": veh}


def dest(n_km, e_km, qty, hours):
    return {"destination_lat": LAT0 + n_km / 111.32, "destination_lng": LNG0 + e_km / 98.5,
            "destination_name": "Dealer", "quantity": qty, "deadline_hours": hours}


def test_health_meta_network_places(owner):
    assert owner.get("/health").json()["network_loaded"] is True
    m = owner.get("/meta").json()
    assert m["network"]["sources"]["roads"] == "test fixture" and m["reasoner"] == "deterministic"
    net = owner.get("/network").json()
    assert net["type"] == "FeatureCollection" and len(net["features"]) == 24
    assert {"hazard", "oneway", "road_type"} <= set(net["features"][0]["properties"])
    assert owner.get("/places?q=test").json()["places"][0]["name"] == "Testville"
    assert owner.get("/places?q=zzz").json()["places"] == []


def test_csv_import_endpoint_is_all_or_nothing(owner):
    ok = f"id,name,lat,lng,units_in_stock,vehicles_available\nA,A,{LAT0},{LNG0},100,2\n"
    assert owner.post("/warehouses/import", files={"file": ("w.csv", ok)}).json() == {"imported": 1}
    assert owner.post("/warehouses/import", files={"file": ("w.csv", "id\n1\n")}).status_code == 422
    assert [w["node_id"] for w in owner.get("/warehouses").json()["warehouses"]] == ["A"]


def test_optimize_feasible_end_to_end_without_ai(owner):
    owner.put("/warehouses/A", json=wh(0, 0))
    owner.put("/warehouses/B", json=wh(6, 6))
    b = owner.post("/optimize-route", json=dest(6, 0, 300, 24)).json()
    assert b["status"] == "feasible" and b["explanation_source"] == "deterministic" and "ai" not in b
    p = b["chosen_plan"]
    assert p["total_units"] == 300 and p["vehicles_used"] == 2 and p["total_time_hours"] <= 24
    assert len(p["legs"][0]["geometry"]) >= 2 and p["legs"][0]["geometry"][0][2] > 0
    assert p["reliability"]["eta_p10"] <= p["reliability"]["eta_p90"] and 0 <= p["reliability"]["p_late"] <= 1
    assert b["candidates_evaluated"] >= 1 and b["chosen_matches_policy"] is True


def test_optimize_infeasible_by_deadline_reports_best_case(owner):
    owner.put("/warehouses/A", json=wh(0, 0))
    b = owner.post("/optimize-route", json=dest(6, 6, 100, 0.05)).json()
    assert b["status"] == "infeasible" and b["chosen_plan"] is None
    assert b["best_case_hours"] > 0.05 and "deadline" in b["explanation"]


def test_optimize_with_no_warehouses_is_infeasible_with_warning(owner):
    b = owner.post("/optimize-route", json=dest(1, 1, 10, 5)).json()
    assert b["status"] == "infeasible" and any("No warehouses" in w for w in b["warnings"])


def test_optimize_destination_outside_network_is_422(owner):
    owner.put("/warehouses/A", json=wh())
    r = owner.post("/optimize-route", json=dest(900, 900, 10, 5))
    assert r.status_code == 422 and "outside the loaded network" in r.json()["detail"]


def test_every_run_lands_in_the_order_history(owner):
    owner.put("/warehouses/A", json=wh())
    owner.post("/optimize-route", json=dest(6, 6, 100, 48))
    owner.post("/optimize-route", json=dest(6, 6, 100, 0.01))
    got = owner.get("/orders").json()["orders"]
    assert [o["status"] for o in got] == ["infeasible", "feasible"] and got[1]["by"] == owner.email
    assert got[1]["p_late"] is not None and got[1]["plan_type"] == "single_source"
