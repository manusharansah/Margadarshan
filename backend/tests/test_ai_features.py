"""The three AI features, driven with a stub model so the *guardrails* around it are what's tested:
 - order intake: code (not the model) computes deadlines and resolves places;
 - remedies: model proposals are accepted only if the real planner proves them feasible;
 - explanation/risk note: invalid or failing model output degrades to a labelled deterministic answer."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from gis_backend.api import create_app
from gis_backend.config import Settings
from gis_backend.gemma_reasoner import LiveGemmaReasoner
from gis_backend.intake import NEPAL_TZ, parse_order
from gis_backend.store import WarehouseStore

from .helpers import LAT0, LNG0, grid_network


class StubAI(LiveGemmaReasoner):
    """Replays queued model outputs (dicts) or raises queued exceptions; records prompts."""

    def __init__(self, *outputs):
        super().__init__(api_key="stub-key")
        self.outputs, self.prompts = list(outputs), []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


NOW = dt.datetime(2026, 11, 4, 10, 0, tzinfo=NEPAL_TZ)
PLACES = [{"id": "p1", "name": "Dhulikhel", "lat": 27.6, "lng": 85.5, "kind": "town"},
          {"id": "p2", "name": "Dhulikhel Bazaar", "lat": 27.61, "lng": 85.51, "kind": "village"}]
search = lambda q: [p for p in PLACES if q.lower() in p["name"].lower()]  # noqa: E731


# ------------------------------------------------------------------ intake
def test_intake_absolute_deadline_hours_computed_by_code():
    ai = StubAI({"destination_text": "Dhulikhel", "quantity": 500, "deadline_hours": None,
                 "deadline_local_iso": "2026-11-05T17:00", "notes": ""})
    r = parse_order("500 units to Dhulikhel by tomorrow 5pm", ai, search, now=NOW)
    assert r["deadline_hours"] == 31.0                       # 10:00 -> next day 17:00, done in code
    assert r["quantity"] == 500 and r["destination"]["name"] == "Dhulikhel" and r["missing"] == []
    assert "2026-11-04T10:00" in ai.prompts[0] and "Wednesday" in ai.prompts[0]  # model is told the date, not asked to compute


def test_intake_relative_duration_and_ambiguous_destination():
    ai = StubAI({"destination_text": "dhul", "quantity": 80, "deadline_hours": 6, "deadline_local_iso": None})
    r = parse_order("80 to dhul in 6h", ai, search, now=NOW)
    assert r["deadline_hours"] == 6.0 and r["destination"] is None and len(r["destination_candidates"]) == 2


@pytest.mark.parametrize("out, missing, issue", [
    ({"destination_text": "Nowhere", "quantity": 5, "deadline_hours": 2, "deadline_local_iso": None}, ["destination"], None),
    ({"destination_text": "Dhulikhel", "quantity": 0, "deadline_hours": 2, "deadline_local_iso": None}, ["quantity"], None),
    ({"destination_text": "Dhulikhel", "quantity": 5, "deadline_hours": None, "deadline_local_iso": None}, ["deadline"], None),
    ({"destination_text": "Dhulikhel", "quantity": 5, "deadline_hours": None, "deadline_local_iso": "2026-11-03T09:00"}, ["deadline"], "past"),
    ({"destination_text": "Dhulikhel", "quantity": 5, "deadline_hours": 9999, "deadline_local_iso": None}, ["deadline"], "30 days"),
    ({"destination_text": "Dhulikhel", "quantity": 5, "deadline_hours": None, "deadline_local_iso": "garbage"}, ["deadline"], "read"),
])
def test_intake_never_accepts_bad_model_output(out, missing, issue):
    r = parse_order("x", StubAI(out), search, now=NOW)
    assert r["missing"] == missing
    if issue:
        assert any(issue in i for i in r["issues"])


def test_intake_rejects_empty_and_oversized():
    for bad in ("", "   ", "x" * 501):
        with pytest.raises(ValueError):
            parse_order(bad, StubAI(), search, now=NOW)


# ------------------------------------------------------------- API-level AI
def make_client(tmp_path, ai, require=False):
    cfg = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/t.db", admin_api_key="k",
                   weather_enabled=False, require_gemma=require)
    app = create_app(cfg, network=grid_network(4), store=WarehouseStore(cfg.database_url), client=ai)
    return TestClient(app)


def seed(c):
    for wid, (n, e), stock, veh in (("A", (0, 0), 900, 4), ("B", (6, 6), 120, 1)):
        assert c.put(f"/warehouses/{wid}", headers={"X-API-Key": "k"}, json={
            "name": wid, "lat": LAT0 + n / 111.32, "lng": LNG0 + e / 98.5, "units_in_stock": stock, "vehicles_available": veh}).status_code == 200


def order(qty, hours, n=6, e=0):
    return {"destination_lat": LAT0 + n / 111.32, "destination_lng": LNG0 + e / 98.5,
            "destination_name": "Dealer", "quantity": qty, "deadline_hours": hours}


def test_parse_order_endpoint_without_ai_is_503(tmp_path):
    with make_client(tmp_path, None) as c:
        assert c.post("/parse-order", json={"text": "hi"}).status_code == 503
        assert c.get("/meta").json()["ai_features"]["order_intake"] is False


def test_parse_order_endpoint_with_ai_and_failures(tmp_path):
    ai = StubAI({"destination_text": "Testville", "quantity": 40, "deadline_hours": 3, "deadline_local_iso": None},
                RuntimeError("Gemma API call failed: ReadTimeout"))
    with make_client(tmp_path, ai) as c:
        ok = c.post("/parse-order", json={"text": "40 to Testville in 3 hours"}).json()
        assert ok["destination"]["name"] == "Testville" and ok["quantity"] == 40 and ok["deadline_hours"] == 3
        bad = c.post("/parse-order", json={"text": "again"})
        assert bad.status_code == 502 and "ReadTimeout" in bad.json()["detail"]
        assert c.post("/parse-order", json={"text": ""}).status_code == 422


def test_require_gemma_refuses_to_start_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMMA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MARGA_REQUIRE_GEMMA"):
        with make_client(tmp_path, None, require=True):
            pass


def test_feasible_answer_carries_grounded_risk_note(tmp_path):
    ai = StubAI({"chosen_index": 0, "explanation": "Best option.", "risk_note": "Mind the rough stretch."})
    with make_client(tmp_path, ai) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(100, 48)).json()
        assert b["explanation_source"] == "gemma" and b["risk_note"] == "Mind the rough stretch."
        assert "riskiest_stretches" in ai.prompts[0]            # model is given real, named road stretches
        leg = b["chosen_plan"]["legs"][0]
        assert leg["hotspots"] and {"road", "hazard", "grade_pct"} <= set(leg["hotspots"][0])


def test_failed_ai_degrades_to_labelled_deterministic_answer(tmp_path):
    ai = StubAI(RuntimeError("Gemma API call failed: HTTPError"))
    with make_client(tmp_path, ai) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(100, 48)).json()
        assert b["status"] == "feasible" and b["explanation_source"] == "deterministic"
        assert "HTTPError" in b["gemma_error"] and b["risk_note"].startswith(("Watch", "No road stretch"))


def test_invalid_ai_choice_is_rejected(tmp_path):
    ai = StubAI({"chosen_index": 99, "explanation": "x"})
    with make_client(tmp_path, ai) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(100, 48)).json()
        assert b["explanation_source"] == "deterministic" and "invalid chosen_index" in b["gemma_error"]


def test_deadline_limited_order_gets_a_verified_longer_deadline(tmp_path):
    with make_client(tmp_path, None) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(500, 0.1)).json()
        assert b["status"] == "infeasible"
        assert [r["kind"] for r in b["remedies"]] == ["extend_deadline"]   # no quantity can arrive in 0.1 h
        r = b["remedies"][0]
        assert r["quantity"] == 500 and r["deadline_hours"] >= b["best_case_hours"] and r["verified"]


def test_stock_limited_order_gets_partial_shipment_and_ai_proposals_are_verified(tmp_path):
    proposals = {"proposals": [
        {"quantity": 100, "deadline_hours": 48, "rationale": "Send 100 now and the rest next week."},   # feasible -> kept
        {"quantity": 100, "deadline_hours": 0.001, "rationale": "Impossible deadline."},               # infeasible -> dropped
        {"quantity": 5000, "deadline_hours": 48, "rationale": "More than ordered."},                    # > original -> dropped
        {"quantity": "many", "deadline_hours": 1, "rationale": "garbage"},                              # malformed -> dropped
    ]}
    ai = StubAI({"chosen_index": None, "explanation": "Not enough stock."}, proposals)
    with make_client(tmp_path, ai) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(1500, 48)).json()   # network holds only 1020 units
        assert b["status"] == "infeasible" and b["warehouses_considered"] == 2
        assert all(r["verified"] for r in b["remedies"])
        red = next(r for r in b["remedies"] if r["kind"] == "reduce_quantity")
        assert 0 < red["quantity"] < 1500 and red["eta_hours"] <= 48 and red["source"] == "computed"
        gem = [r for r in b["remedies"] if r["source"] == "gemma"]
        assert len(gem) == 1 and gem[0]["quantity"] == 100 and "rest next week" in gem[0]["rationale"]
        assert "closest_plans" in ai.prompts[1] and "already_verified_options" in ai.prompts[1]


def test_remedies_work_without_ai_and_remedy_ai_failure_is_reported(tmp_path):
    with make_client(tmp_path, None) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(1500, 48)).json()
        assert b["remedies"] and all(r["source"] == "computed" for r in b["remedies"])
    ai = StubAI({"chosen_index": None, "explanation": "x"}, RuntimeError("Gemma API call failed: Timeout"))
    with make_client(tmp_path, ai) as c:
        seed(c)
        b = c.post("/optimize-route", json=order(1500, 48)).json()
        assert b["remedies"] and "Timeout" in b["remedy_error"]
