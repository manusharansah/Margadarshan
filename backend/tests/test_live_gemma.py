"""Real-model checks. Skipped unless GEMMA_API_KEY is set. Run with:  pytest -m live
Latency on the free tier is 5 to 90 s per call and 503s happen, so these are for a human to run, not for CI."""
import os

import pytest

from gis_backend.gemma_reasoner import LiveGemmaReasoner
from gis_backend.intake import parse_order
from gis_backend.models import DeliveryRequest

pytestmark = [pytest.mark.live, pytest.mark.skipif(not os.environ.get("GEMMA_API_KEY"), reason="no GEMMA_API_KEY")]
PLACES = [{"id": "p1", "name": "Dhulikhel", "lat": 27.6, "lng": 85.5, "kind": "town"}]


def test_real_model_reads_a_typed_order():
    r = parse_order("send 300 units to Dhulikhel within 6 hours", LiveGemmaReasoner(),
                    lambda q: [p for p in PLACES if q.lower() in p["name"].lower()])
    assert r["quantity"] == 300 and r["deadline_hours"] == 6 and r["destination"]["name"] == "Dhulikhel"


def test_real_model_explanation_contains_only_code_written_figures():
    from tests.test_store_and_reasoner import plan
    ai = LiveGemmaReasoner()
    sel = ai.explain(DeliveryRequest(27.6, 85.5, 100, 4.0, "Dhulikhel"), [plan(2.0)], True, lambda s: s)
    assert sel.source == "gemma" and "{" not in sel.explanation
    assert "2 h" in sel.explanation or "arrive" in sel.explanation.lower()
