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
    """Replays queued model outputs (dicts) or raises queued exceptions. Outputs are routed by prompt type
    because the explanation and proposal calls run in parallel threads."""

    def __init__(self, intake=(), explain=(), remedy=()):
        super().__init__(api_key="stub-key")
        self.q = {"intake": list(intake), "explain": list(explain), "remedy": list(remedy)}
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        kind = "intake" if "Extract a delivery order" in prompt else "remedy" if "Propose up to 3" in prompt else "explain"
        out = self.q[kind].pop(0)
        if isinstance(out, Exception):
            raise out
        return out


NOW = dt.datetime(2026, 11, 4, 10, 0, tzinfo=NEPAL_TZ)
PLACES = [{"id": "p1", "name": "Dhulikhel", "lat": 27.6, "lng": 85.5, "kind": "town"},
          {"id": "p2", "name": "Dhulikhel Bazaar", "lat": 27.61, "lng": 85.51, "kind": "village"}]
search = lambda q: [p for p in PLACES if q.lower() in p["name"].lower()]  # noqa: E731


# ------------------------------------------------------------------ intake
def test_intake_absolute_deadline_hours_computed_by_code():
    ai = StubAI(intake=[{"destination_text": "Dhulikhel", "quantity": 500, "deadline_hours": None,
                 "deadline_local_iso": "2026-11-05T17:00", "notes": ""}])
    r = parse_order("500 units to Dhulikhel by tomorrow 5pm", ai, search, now=NOW)
    assert r["deadline_hours"] == 31.0                       # 10:00 -> next day 17:00, done in code
    assert r["quantity"] == 500 and r["destination"]["name"] == "Dhulikhel" and r["missing"] == []
    assert "2026-11-04T10:00" in ai.prompts[0] and "Wednesday" in ai.prompts[0]  # model is told the date, not asked to compute


def test_intake_relative_duration_and_ambiguous_destination():
    ai = StubAI(intake=[{"destination_text": "dhul", "quantity": 80, "deadline_hours": 6, "deadline_local_iso": None}])
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
    r = parse_order("x", StubAI(intake=[out]), search, now=NOW)
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


def seed(c, extra=False):
    spec = [("A", (0, 0), 900, 4), ("B", (6, 6), 120, 1)] + ([("C", (5, 0), 900, 4)] if extra else [])
    for wid, (n, e), stock, veh in spec:
        assert c.put(f"/warehouses/{wid}", headers={"X-API-Key": "k"}, json={
            "name": wid, "lat": LAT0 + n / 111.32, "lng": LNG0 + e / 98.5, "units_in_stock": stock,
            "vehicles_available": veh}).status_code == 200


def order(qty, hours, n=6, e=0):
    return {"destination_lat": LAT0 + n / 111.32, "destination_lng": LNG0 + e / 98.5,
            "destination_name": "Dealer", "quantity": qty, "deadline_hours": hours}


def run(c, qty, hours):
    r = c.post("/optimize-route", json=order(qty, hours))
    assert r.status_code == 200
    return r.json()


def test_parse_order_endpoint_without_ai_is_503(tmp_path):
    with make_client(tmp_path, None) as c:
        assert c.post("/parse-order", json={"text": "hi"}).status_code == 503
        assert c.get("/meta").json()["ai_features"]["order_intake"] is False


def test_parse_order_endpoint_with_ai_and_failures(tmp_path):
    ai = StubAI(intake=[{"destination_text": "Testville", "quantity": 40, "deadline_hours": 3, "deadline_local_iso": None},
                        RuntimeError("Gemma API call failed: HTTP 503: overloaded")])
    with make_client(tmp_path, ai) as c:
        ok = c.post("/parse-order", json={"text": "40 to Testville in 3 hours"}).json()
        assert ok["destination"]["name"] == "Testville" and ok["quantity"] == 40 and ok["deadline_hours"] == 3
        bad = c.post("/parse-order", json={"text": "again"})
        assert bad.status_code == 502 and "HTTP 503" in bad.json()["detail"]
        assert c.post("/parse-order", json={"text": ""}).status_code == 422


def test_require_gemma_refuses_to_start_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMMA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MARGA_REQUIRE_GEMMA"):
        with make_client(tmp_path, None, require=True):
            pass


def test_without_ai_the_answer_is_complete_and_has_no_pending_step(tmp_path):
    with make_client(tmp_path, None) as c:
        seed(c)
        b = run(c, 100, 48)
        assert b["status"] == "feasible" and "result_id" not in b and "ai" not in b
        assert b["explanation_source"] == "deterministic" and b["risk_note"]


def test_answer_returns_at_once_and_ai_fills_it_in_with_verified_figures(tmp_path):
    ai = StubAI(explain=[{"chosen_index": 0, "risk_note": "Mind {road1}, hazard {hazard1}.",
                          "explanation": "It arrives in {arrival}, {margin} before the {deadline} deadline, using {vehicles} vehicle(s) over {distance}."}])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 450, 48)
        assert first["ai"] == {"status": "pending"} and first["explanation_source"] == "deterministic"
        assert first["chosen_plan"]["vehicles_used"] == 3 and ai.prompts == []        # no model call yet
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        plan = first["chosen_plan"]
        assert patch["ai"]["status"] == "done" and patch["explanation_source"] == "gemma"
        assert "using 3 vehicle(s)" in patch["explanation"]                           # the figure came from code
        assert "48 h deadline" in patch["explanation"] and f"{plan['distance_km']:.0f} km" in patch["explanation"]
        assert patch["risk_note"].startswith("Mind ") and patch["risk_note_source"] == "gemma"
        assert "{" not in patch["explanation"] and "riskiest_stretches" in ai.prompts[0]
        assert c.post(f"/optimize-route/{first['result_id']}/ai").json() == patch      # idempotent, no second model call
        assert len(ai.prompts) == 1


@pytest.mark.parametrize("bad, why", [
    ("It uses 9 vehicles.", "digit"),
    ("It arrives in {arrival_hours}.", "unknown placeholder"),
    ("It arrives in {arrival", "malformed"),
])
def test_model_typed_numbers_and_unknown_placeholders_never_reach_the_dealer(tmp_path, bad, why):
    ai = StubAI(explain=[{"chosen_index": 0, "explanation": bad}, {"chosen_index": 0, "explanation": bad}])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 100, 48)
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["ai"]["status"] == "partial" and "rejected twice" in patch["ai"]["error"]
        assert "explanation" not in patch                       # the rule-based text from the first answer stands
        assert first["explanation_source"] == "deterministic"


def test_a_rejected_explanation_is_retried_once_with_the_reason(tmp_path):
    ai = StubAI(explain=[{"chosen_index": 0, "explanation": "Uses 9 vehicles."},
                         {"chosen_index": 0, "explanation": "Uses {vehicles} vehicle(s)."}])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 100, 48)
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["ai"]["status"] == "done" and patch["explanation"] == f"Uses {first['chosen_plan']['vehicles_used']} vehicle(s)."
        assert "rejected (it contained a digit)" in ai.prompts[1]


def test_invalid_index_and_model_failure_leave_the_computed_answer_intact(tmp_path):
    ai = StubAI(explain=[{"chosen_index": 99, "explanation": "x"}])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 100, 48)
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["ai"]["status"] == "partial" and "invalid chosen_index" in patch["ai"]["error"]
        assert first["status"] == "feasible" and first["chosen_plan"]
    ai = StubAI(explain=[RuntimeError("Gemma API call failed: HTTP 503: This model is currently experiencing high demand.")])
    with make_client(tmp_path, ai) as c:
        seed(c)
        patch = c.post(f"/optimize-route/{run(c, 100, 48)['result_id']}/ai").json()
        assert patch["ai"]["status"] == "partial" and "HTTP 503" in patch["ai"]["error"] and "stub-key" not in str(patch)


def test_model_may_prefer_another_feasible_plan_but_only_from_the_feasible_set(tmp_path):
    ai = StubAI(explain=[{"chosen_index": 1, "explanation": "Safer: {sources} in {arrival}."}])
    with make_client(tmp_path, ai) as c:
        seed(c, extra=True)
        first = run(c, 100, 48)
        assert first["alternatives_total"] >= 1
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["chosen_matches_policy"] is False
        assert patch["chosen_plan"]["sources_used"] != first["chosen_plan"]["sources_used"]
        assert patch["chosen_plan"]["meets_deadline"] is True and patch["chosen_plan"]["legs"][0]["geometry"]


def test_deadline_limited_order_gets_a_verified_longer_deadline_without_ai(tmp_path):
    with make_client(tmp_path, None) as c:
        seed(c)
        b = run(c, 500, 0.1)
        assert b["status"] == "infeasible" and [r["kind"] for r in b["remedies"]] == ["extend_deadline"]
        r = b["remedies"][0]
        assert r["quantity"] == 500 and r["deadline_hours"] >= b["best_case_hours"] and r["verified"]


def test_ai_proposals_are_verified_and_digit_rationales_dropped(tmp_path):
    proposals = {"proposals": [
        {"quantity": 100, "deadline_hours": 48, "rationale": "Send part now and the rest in a later run."},   # feasible: kept
        {"quantity": 100, "deadline_hours": 0.001, "rationale": "Impossible deadline."},                      # infeasible: dropped
        {"quantity": 5000, "deadline_hours": 48, "rationale": "More than ordered."},                          # above the order: dropped
        {"quantity": 90, "deadline_hours": 48, "rationale": "Send 90 now."},                                  # model-typed number: dropped
        {"quantity": "many", "deadline_hours": 1, "rationale": "garbage"},                                    # malformed: dropped
    ]}
    ai = StubAI(explain=[{"chosen_index": None, "explanation": "Cannot deliver {quantity} within {deadline}."}], remedy=[proposals])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 1500, 48)                                  # the network holds only 1,020 units
        assert first["status"] == "infeasible" and first["ai"]["status"] == "pending"
        assert all(r["source"] == "computed" for r in first["remedies"])
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["explanation"] == "Cannot deliver 1,500 within 48 h."
        gem = [r for r in patch["remedies"] if r["source"] == "gemma"]
        assert len(gem) == 1 and gem[0]["quantity"] == 100 and gem[0]["verified"]
        assert {"computed"} == {r["source"] for r in patch["remedies"] if r["source"] != "gemma"}
        assert any("closest_plans" in p for p in ai.prompts)


def test_remedy_failure_is_reported_and_computed_options_survive(tmp_path):
    ai = StubAI(explain=[{"chosen_index": None, "explanation": "No."}],
                remedy=[RuntimeError("Gemma API call failed: empty response (RECITATION)")])
    with make_client(tmp_path, ai) as c:
        seed(c)
        first = run(c, 1500, 48)
        patch = c.post(f"/optimize-route/{first['result_id']}/ai").json()
        assert patch["ai"]["status"] == "partial" and "RECITATION" in patch["ai"]["error"] and "remedies" not in patch
        assert first["remedies"]


def test_unknown_or_unconfigured_ai_step(tmp_path):
    with make_client(tmp_path, StubAI()) as c:
        assert c.post("/optimize-route/doesnotexist/ai").status_code == 404
    with make_client(tmp_path, None) as c:
        assert c.post("/optimize-route/x/ai").status_code in (404, 503)


def test_fill_placeholders_contract():
    from gis_backend.gemma_reasoner import fill_placeholders
    vals = {"arrival": "45 min", "distance": "31 km", "fuel": "8 L"}
    assert fill_placeholders("In {arrival} over {distance}.", vals) == "In 45 min over 31 km."
    assert fill_placeholders("In {arrival} hours over {distance} km, {fuel} litres.", vals) == "In 45 min over 31 km, 8 L."
    assert fill_placeholders("In 45 min", vals) is None and fill_placeholders("In {unknown}", vals) is None
    assert fill_placeholders("No figures at all.", vals) == "No figures at all."
