import pytest

from gis_backend.osm import DEFAULT_SURFACE_BY_CLASS, oneway_flag
from gis_backend.overture import oneway_from_restrictions, motor_traffic_denied, segment_tags
from gis_backend.surface_prior import fit_priors, holdout_accuracy


def test_osm_oneway_tags():
    assert [oneway_flag({"oneway": v}) for v in ("yes", "1", "true", "-1", "no", "")] == [1, 1, 1, -1, 0, 0]
    assert oneway_flag({"junction": "roundabout"}) == 1 and oneway_flag({}) == 0


def test_overture_one_way_is_denied_access_against_one_heading():
    back_denied = {"access_restrictions": [{"access_type": "denied", "when": {"heading": "backward"}}]}
    fwd_denied = {"access_restrictions": [{"access_type": "denied", "when": {"heading": "forward"}}]}
    assert oneway_from_restrictions(back_denied) == 1 and oneway_from_restrictions(fwd_denied) == -1
    assert oneway_from_restrictions({}) == 0
    assert oneway_from_restrictions({"access_restrictions": [{"access_type": "denied", "when": {"heading": "backward", "mode": ["bicycle"]}}]}) == 0
    # a one-way road is still drivable; a full ban for motor vehicles is not
    assert not motor_traffic_denied(back_denied)
    assert motor_traffic_denied({"access_restrictions": [{"access_type": "denied", "when": {"mode": ["motor_vehicle"]}}]})
    assert segment_tags({**back_denied, "class": "tertiary"})["oneway"] == "yes"


def test_prior_probabilities_follow_the_tagged_roads_by_class():
    rows = [("tertiary", "paved", 1)] * 8 + [("tertiary", "rough", 1)] * 2 + [("unclassified", "rough", 1)] * 5 + [("unclassified", "paved", 1)] * 4
    priors, glob = fit_priors(rows, alpha=0)
    assert priors["tertiary"] == pytest.approx((0.8, 0.0, 0.2)) and priors["unclassified"][2] > priors["unclassified"][0]
    assert sum(glob) == pytest.approx(1.0)


def test_prior_uses_length_weights():
    priors, _ = fit_priors([("secondary", "paved", 9.0), ("secondary", "rough", 1.0)], alpha=0)
    assert priors["secondary"][0] == pytest.approx(0.9)


def test_holdout_beats_hand_picked_defaults_when_the_data_say_so():
    # In this area 'unclassified' roads are mostly paved, unlike the hand-picked default (gravel).
    rows = [("unclassified", "paved", 1.0)] * 80 + [("unclassified", "rough", 1.0)] * 20 + [("tertiary", "paved", 1.0)] * 100
    ev = holdout_accuracy(rows, DEFAULT_SURFACE_BY_CLASS)
    assert ev["prior_accuracy"] > ev["static_default_accuracy"] + 0.2 and ev["n_tagged"] == 200


def test_holdout_needs_enough_data():
    assert holdout_accuracy([("a", "paved", 1.0)] * 10, {})["prior_accuracy"] is None
