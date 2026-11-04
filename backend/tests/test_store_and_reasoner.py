import json

import pytest
import requests

from gis_backend.gemma_reasoner import DeterministicReasoner, LiveGemmaReasoner, compact
from gis_backend.models import CandidatePlan, DeliveryRequest, RouteLeg, StorageNode
from gis_backend.store import WarehouseStore, validate
from gis_backend.weather import WeatherService


def plan(time_h=2.0, sources=("A",), vehicles=1):
    leg = RouteLeg(source=sources[0], path=[1, 2], time_hours=time_h, edge_cost=10, units_carried=100,
                   vehicles_used=vehicles, fuel_litres=12, distance_km=30, max_hazard=0.3)
    return CandidatePlan([leg], "single_source", 100, time_h, 10, 12, vehicles, 30, list(sources))


REQ = DeliveryRequest(27.6, 85.5, 100, 4.0, "Dhulikhel")
NAME = lambda s: {"A": "Alpha", "B": "Bravo"}.get(s, s)  # noqa: E731


# ------------------------------------------------------------------ store
def test_store_crud_and_csv(tmp_path):
    st = WarehouseStore(f"sqlite:///{tmp_path}/t.db")
    assert st.list() == []
    n = st.import_csv("id,name,lat,lng,units_in_stock,vehicles_available,vehicle_capacity\nW1,Banepa,27.63,85.52,500,3,\nW2,Panauti,27.58,85.52,0,0,150\n")
    assert n == 2
    w1, w2 = st.get("W1"), st.get("W2")
    assert (w1.units_in_stock, w1.vehicle_capacity) == (500, 200) and w2.vehicle_capacity == 150
    st.upsert(StorageNode("W1", "Banepa", 27.63, 85.52, 450, 3))
    assert st.get("W1").units_in_stock == 450 and len(st.list()) == 2
    assert st.delete("W1") and not st.delete("W1")


@pytest.mark.parametrize("csv_text, msg", [
    ("id,name,lat\nx,y,1\n", "missing columns"),
    ("id,name,lat,lng,units_in_stock,vehicles_available\nW,n,abc,85,1,1\n", "line 2"),
    ("id,name,lat,lng,units_in_stock,vehicles_available\nW,n,27,85,-5,1\n", "line 2"),
])
def test_csv_import_is_all_or_nothing(tmp_path, csv_text, msg):
    st = WarehouseStore(f"sqlite:///{tmp_path}/t.db")
    with pytest.raises(ValueError, match=msg):
        st.import_csv(csv_text)
    assert st.list() == []


def test_validate_bounds():
    with pytest.raises(ValueError):
        validate(StorageNode("x", "n", 95, 0, 1, 1))


# --------------------------------------------------------------- reasoners
def test_deterministic_feasible_text_uses_real_numbers():
    sel = DeterministicReasoner().explain(REQ, [plan(2.0), plan(3.0, ("B",))], True, NAME)
    assert sel.index == 0 and sel.source == "deterministic"
    assert "Alpha" in sel.explanation and "2.0 h" in sel.explanation and "1 other plan" in sel.explanation


def test_deterministic_infeasible_text_reports_overrun():
    sel = DeterministicReasoner().explain(REQ, [plan(6.5)], False, NAME)
    assert sel.index is None and "2.5 h over" in sel.explanation
    assert "enough stock" in DeterministicReasoner().explain(REQ, [], False, NAME).explanation


class FakeSession:
    def __init__(self, text=None, exc=None, status=200):
        self.text, self.exc, self.status, self.calls = text, exc, status, []

    def post(self, url, params=None, json=None, timeout=None):
        self.calls.append((url, params, json))
        if self.exc:
            raise self.exc
        r = requests.Response()
        r.status_code = self.status
        r._content = __import__("json").dumps({"candidates": [{"content": {"parts": [{"text": self.text}]}}]}).encode()
        return r


def gemma(text=None, **kw):
    return LiveGemmaReasoner(api_key="k", session=FakeSession(text, **kw))


def test_gemma_parses_valid_selection_and_prompt_has_no_geometry():
    g = gemma('{"chosen_index": 1, "explanation": "Bravo is safer."}')
    sel = g.explain(REQ, [plan(2), plan(3, ("B",))], True, NAME)
    assert (sel.index, sel.source, sel.explanation) == (1, "gemma", "Bravo is safer.")
    prompt = g._session.calls[0][2]["contents"][0]["parts"][0]["text"]
    assert "Dhulikhel" in prompt and "geometry" not in prompt and "Bravo" in prompt


@pytest.mark.parametrize("text", ['{"chosen_index": 5, "explanation": "x"}', '{"chosen_index": null, "explanation": "x"}',
                                  '{"chosen_index": 0, "explanation": ""}', "not json"])
def test_gemma_bad_output_raises_instead_of_guessing(text):
    with pytest.raises(RuntimeError):
        gemma(text).explain(REQ, [plan()], True, NAME)


def test_gemma_tolerates_fenced_and_prefixed_json():
    sel = gemma('thinking... {"a":1}\n```json\n{"chosen_index": 0, "explanation": "ok"}\n```').explain(REQ, [plan()], True, NAME)
    assert sel.index == 0
    assert LiveGemmaReasoner._parse('blah {"chosen_index": 0, "explanation": "e"} trailing')["chosen_index"] == 0


def test_gemma_infeasible_forces_null_index():
    sel = gemma('{"chosen_index": 0, "explanation": "Too slow."}').explain(REQ, [plan(9)], False, NAME)
    assert sel.index is None


def test_gemma_http_error_does_not_leak_api_key():
    g = LiveGemmaReasoner(api_key="SECRET123", session=FakeSession(exc=requests.ConnectionError("https://x?key=SECRET123")))
    with pytest.raises(RuntimeError) as ei:
        g.explain(REQ, [plan()], True, NAME)
    assert "SECRET123" not in str(ei.value) and "SECRET123" not in repr(ei.value.__cause__)


def test_gemma_requires_key(monkeypatch):
    monkeypatch.delenv("GEMMA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMMA_API_KEY"):
        LiveGemmaReasoner().explain(REQ, [plan()], True, NAME)


def test_compact_has_no_geometry():
    c = compact(plan(), NAME)
    assert "geometry" not in json.dumps(c) and c["sources"] == ["Alpha"]


# ----------------------------------------------------------------- weather
class WxSession:
    """Mimics Open-Meteo's multi-location response shape (one object per coordinate)."""

    def __init__(self, mm_per_point, fail=False):
        self.mm, self.fail, self.calls = mm_per_point, fail, 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.fail:
            raise requests.ConnectionError("down")
        n = len(params["latitude"].split(","))
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps([{"hourly": {"precipitation": [self.mm / 4, None, self.mm / 4, self.mm / 2]}}] * n).encode()
        return r


def test_weather_sums_hourly_precip_and_maps_to_index():
    w = WeatherService((27.5, 85.4, 27.7, 85.6), session=WxSession(80.0))
    assert w.refresh()
    assert w.precipitation_mm(27.6, 85.5) == pytest.approx(80.0)
    assert w.rain_index_fn()(27.6, 85.5) == pytest.approx(0.4)
    st = w.status()
    assert st["available"] and st["max_precip_mm"] == 80.0


def test_weather_failure_is_reported_not_invented():
    w = WeatherService((27.5, 85.4, 27.7, 85.6), session=WxSession(0, fail=True))
    assert not w.refresh()
    assert w.precipitation_mm(27.6, 85.5) is None and w.rain_index_fn()(27.6, 85.5) == 0
    st = w.status()
    assert not st["available"] and "ConnectionError" in st["error"]
    assert not w.is_stale()  # backs off instead of hammering a down provider
