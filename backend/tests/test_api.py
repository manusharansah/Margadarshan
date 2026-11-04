import pytest
from fastapi.testclient import TestClient

from gis_backend.api import create_app
from gis_backend.config import Settings
from gis_backend.gemma_reasoner import DeterministicReasoner
from gis_backend.store import WarehouseStore

from .helpers import LAT0, LNG0, grid_network

KEY = "test-admin-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMMA_API_KEY", raising=False)
    cfg = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/t.db", admin_api_key=KEY, weather_enabled=False)
    app = create_app(cfg, network=grid_network(4), store=WarehouseStore(cfg.database_url))
    with TestClient(app) as c:
        yield c


def put(client, wid, node_xy, stock, vehicles, key=KEY):
    n_km, e_km = node_xy
    return client.put(f"/warehouses/{wid}", headers={"X-API-Key": key}, json={
        "name": wid, "lat": LAT0 + n_km / 111.32, "lng": LNG0 + e_km / 98.5, "units_in_stock": stock, "vehicles_available": vehicles})


def dest(n_km, e_km, qty, hours):
    return {"destination_lat": LAT0 + n_km / 111.32, "destination_lng": LNG0 + e_km / 98.5,
            "destination_name": "Dealer", "quantity": qty, "deadline_hours": hours}


def test_health_meta_network_places(client):
    assert client.get("/health").json()["network_loaded"] is True
    m = client.get("/meta").json()
    assert m["network"]["sources"]["roads"] == "test fixture" and m["reasoner"] == "deterministic"
    net = client.get("/network").json()
    assert net["type"] == "FeatureCollection" and len(net["features"]) == 24
    assert client.get("/places?q=test").json()["places"][0]["name"] == "Testville"
    assert client.get("/places?q=zzz").json()["places"] == []


def test_write_endpoints_need_the_admin_key(client):
    assert put(client, "A", (0, 0), 100, 1, key="wrong").status_code == 401
    assert client.put("/warehouses/A", json={}).status_code in (401, 422)
    assert client.delete("/warehouses/A").status_code == 401
    assert put(client, "A", (0, 0), 100, 1).status_code == 200


def test_writes_disabled_without_configured_key(tmp_path):
    cfg = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/t.db", admin_api_key="", weather_enabled=False)
    with TestClient(create_app(cfg, network=grid_network(3), store=WarehouseStore(cfg.database_url))) as c:
        assert put(c, "A", (0, 0), 1, 1, key="").status_code == 503


def test_warehouse_outside_network_rejected(client):
    assert put(client, "FAR", (500, 500), 10, 1).status_code == 422


def test_csv_import_endpoint(client):
    csv_ok = f"id,name,lat,lng,units_in_stock,vehicles_available\nA,A,{LAT0},{LNG0},100,2\n"
    r = client.post("/warehouses/import", headers={"X-API-Key": KEY}, files={"file": ("w.csv", csv_ok)})
    assert r.json() == {"imported": 1}
    bad = client.post("/warehouses/import", headers={"X-API-Key": KEY}, files={"file": ("w.csv", "id\n1\n")})
    assert bad.status_code == 422
    assert [w["node_id"] for w in client.get("/warehouses").json()["warehouses"]] == ["A"]


def test_optimize_feasible_end_to_end(client):
    put(client, "A", (0, 0), 900, 4)
    put(client, "B", (6, 6), 900, 4)
    r = client.post("/optimize-route", json=dest(6, 0, 300, 24))
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "feasible" and b["explanation_source"] == "deterministic"
    p = b["chosen_plan"]
    assert p["total_units"] == 300 and p["vehicles_used"] == 2 and p["total_time_hours"] <= 24
    assert len(p["legs"][0]["geometry"]) >= 2 and p["legs"][0]["geometry"][0][2] > 0
    assert b["alternatives_total"] == len(b["alternatives"]) or b["alternatives_total"] > len(b["alternatives"])
    assert b["candidates_evaluated"] >= 1 and b["chosen_matches_policy"] is True


def test_optimize_infeasible_by_deadline_reports_best_case(client):
    put(client, "A", (0, 0), 900, 4)
    b = client.post("/optimize-route", json=dest(6, 6, 100, 0.05)).json()
    assert b["status"] == "infeasible" and b["chosen_plan"] is None
    assert b["best_case_hours"] > 0.05 and "deadline" in b["explanation"]


def test_optimize_with_no_warehouses_is_infeasible_with_warning(client):
    b = client.post("/optimize-route", json=dest(1, 1, 10, 5)).json()
    assert b["status"] == "infeasible" and any("No warehouses" in w for w in b["warnings"])


def test_optimize_destination_outside_network_is_422(client):
    put(client, "A", (0, 0), 900, 4)
    r = client.post("/optimize-route", json=dest(900, 900, 10, 5))
    assert r.status_code == 422 and "outside the loaded network" in r.json()["detail"]


@pytest.mark.parametrize("patch", [{"quantity": 0}, {"deadline_hours": -1}, {"destination_lat": 123}, {"quantity": "x"}])
def test_input_validation(client, patch):
    assert client.post("/optimize-route", json={**dest(1, 1, 10, 5), **patch}).status_code == 422
