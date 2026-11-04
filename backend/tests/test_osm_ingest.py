"""OSM parsing + ingestion verified against an analytic DEM (a tilted plane), so
the expected grades are known exactly."""
import math

import pytest

from gis_backend.hazard import IncidentIndex, terrain_susceptibility
from gis_backend.ingest import build_network
from gis_backend.osm import classify_surface, haversine_km, is_drivable, parse_maxspeed, split_ways

LAT0, LNG0 = 27.60, 85.50
KM_LAT = 111.32


def way(wid, pts, **tags):
    tags.setdefault("highway", "secondary")
    return {"type": "way", "id": wid, "nodes": [p[0] for p in pts], "tags": tags,
            "geometry": [{"lat": p[1], "lon": p[2]} for p in pts]}


def pt(i, north_km, east_km):
    return (i, LAT0 + north_km / KM_LAT, LNG0 + east_km / (KM_LAT * math.cos(math.radians(LAT0))))


class PlaneDem:
    """elevation = 1000 + 100 m per km northwards -> exactly 10 % grade going north."""

    def __init__(self, gain_per_km=100.0):
        self.g = gain_per_km

    def elevation(self, lat, lng):
        return 1000 + self.g * (lat - LAT0) * KM_LAT

    def terrain_slope_deg(self, lat, lng, step_m=30):
        return math.degrees(math.atan(self.g / 1000))


def test_split_at_junction_and_ends():
    ns = [pt(1, 0, 0), pt(2, 0, 0.4), pt(3, 0, 0.8)]
    cross = [pt(10, -0.4, 0.4), pt(2, 0, 0.4), pt(11, 0.4, 0.4)]
    pieces, _ = split_ways([way(1, ns), way(2, cross)], max_piece_km=5)
    ends = sorted(frozenset((p.nodes[0], p.nodes[-1])) in [frozenset(x) for x in [(1, 2), (2, 3), (2, 10), (2, 11)]] for p in pieces)
    assert len(pieces) == 4 and all(ends)
    assert {frozenset((p.nodes[0], p.nodes[-1])) for p in pieces} == {frozenset(x) for x in [(1, 2), (2, 3), (2, 10), (2, 11)]}


def test_long_way_subdivided():
    ns = [pt(i, i * 0.2, 0) for i in range(1, 12)]  # 2 km
    pieces, _ = split_ways([way(1, ns)], max_piece_km=0.5)
    assert len(pieces) == 5  # greedy fill: 0.4 km pieces, never above the 0.5 km cap
    assert all(haversine_km(p.coords[0], p.coords[-1]) <= 0.5 + 1e-6 for p in pieces)
    assert sum(haversine_km(p.coords[0], p.coords[-1]) for p in pieces) == pytest.approx(2.0, rel=0.01)
    assert pieces[0].nodes[-1] == pieces[1].nodes[0]  # contiguous


def test_private_and_footways_dropped():
    ns = [pt(1, 0, 0), pt(2, 0, 0.4)]
    pieces, _ = split_ways([way(1, ns, access="private"), way(2, ns, highway="footway"), way(3, ns, area="yes"),
                            way(4, ns, motor_vehicle="no"), way(5, ns)], 5)
    assert len(pieces) == 1 and pieces[0].way_id == 5


def test_surface_and_speed_tags():
    assert classify_surface({"surface": "asphalt"}) == ("paved", False)
    assert classify_surface({"surface": "gravel"}) == ("gravel", False)
    assert classify_surface({"surface": "dirt"}) == ("rough", False)
    assert classify_surface({"highway": "track"}) == ("rough", True)
    assert classify_surface({"highway": "primary"}) == ("paved", True)
    assert parse_maxspeed({"maxspeed": "40"}) == 40
    assert parse_maxspeed({"maxspeed": "30 mph"}) == pytest.approx(48.28, abs=0.01)
    assert parse_maxspeed({"maxspeed": "walk"}) is None
    assert is_drivable({"highway": "track", "access": "no", "motor_vehicle": "yes"})


def test_grade_ascent_and_direction_from_plane_dem():
    north = [pt(1, 0, 0), pt(2, 0.4, 0)]  # uphill going north: +40 m over 400 m = 10 %
    east = [pt(3, 0, 0), pt(4, 0, 0.4)]   # level
    # Ingestion keeps one connected component, so each way is built on its own.
    for ways, key, grade, asc in (([way(1, north)], (1, 2), 10.0, 40.0), ([way(2, east)], (3, 4), 0.0, 0.0)):
        a = build_network({"elements": ways}, PlaneDem())
        e = a["edges"][0]
        assert (e["a"], e["b"]) == key
        assert e["slope_pct"] == pytest.approx(grade, abs=0.3)
        assert e["ascent_ab_m"] == pytest.approx(asc, abs=1.5)
        assert e["descent_ab_m"] == pytest.approx(0.0, abs=1.5)
        assert e["length_km"] == pytest.approx(0.4, rel=0.01)


def test_downhill_orientation_swaps_ascent_descent():
    south = [pt(1, 0.4, 0), pt(2, 0, 0)]
    e = build_network({"elements": [way(1, south)]}, PlaneDem())["edges"][0]
    assert e["descent_ab_m"] == pytest.approx(40, abs=1.5) and e["ascent_ab_m"] == pytest.approx(0, abs=1.5)


def test_largest_component_kept_and_meta_honest():
    main = [pt(1, 0, 0), pt(2, 0.3, 0), pt(3, 0.6, 0)]
    island = [pt(10, 0, 5), pt(11, 0.2, 5)]
    art = build_network({"elements": [way(1, main), way(2, island, surface="asphalt"),
                                      {"type": "node", "id": 77, "lat": LAT0, "lon": LNG0, "tags": {"name": "Banepa", "place": "town"}}]},
                        PlaneDem())
    ids = {n[0] for n in art["nodes"]}
    assert 10 not in ids and {1, 3} <= ids
    assert art["meta"]["surface_inferred_fraction"] == 1.0
    assert art["meta"]["sources"]["incidents"] == "not supplied"
    assert art["places"][0]["name"] == "Banepa"


def test_terrain_susceptibility_ramp():
    assert terrain_susceptibility(10) == 0
    assert terrain_susceptibility(30) == pytest.approx(0.5)
    assert terrain_susceptibility(60) == 1
    assert terrain_susceptibility(float("nan")) == 0


def test_incident_proximity(tmp_path):
    f = tmp_path / "inc.csv"
    f.write_text("event_date,latitude,longitude\n2020-01-01,27.6,85.5\n2021-01-01,27.6005,85.5005\n2022-01-01,27.601,85.499\nbad,x,y\n")
    idx = IncidentIndex.from_csv(f)
    assert len(idx.points) == 3
    assert idx.count_within(27.6, 85.5, 2.0) == 3
    assert idx.proximity(27.6, 85.5) == pytest.approx(1 - math.exp(-1))
    assert idx.proximity(28.5, 86.5) == 0
    (tmp_path / "bad.csv").write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        IncidentIndex.from_csv(tmp_path / "bad.csv")


def test_sparse_way_gets_virtual_nodes_so_snapping_stays_precise():
    ns = [pt(1, 0, 0), pt(2, 3.0, 0)]  # one 3 km straight segment, no intermediate OSM nodes
    pieces, _ = split_ways([way(1, ns)], max_piece_km=0.5)
    assert len(pieces) == 6
    assert all(haversine_km(p.coords[0], p.coords[-1]) <= 0.51 for p in pieces)
    ids = [p.nodes[0] for p in pieces] + [pieces[-1].nodes[-1]]
    assert ids[0] == 1 and ids[-1] == 2 and all(i < 0 for i in ids[1:-1]) and len(set(ids)) == len(ids)


def test_division_places_use_english_names_and_drop_wards():
    from shapely import to_wkb
    from shapely.geometry import Point

    from gis_backend.overture import division_places

    def row(i, primary, en, subtype="locality", cls=None, lng=85.5, lat=27.6):
        names = {"primary": primary, "common": [("ne", primary)] + ([("en", en)] if en else [])}
        return {"id": i, "names": names, "subtype": subtype, "class": cls, "geometry": to_wkb(Point(lng, lat))}

    rows = [row("a", "बनेपा नगरपालिका", "Banepa"), row("b", "Kathmandu-16", None, "neighborhood"),
            row("c", "धुलिखेल", None, "locality", "town"), row("d", "x", "Somewhere", "region")]
    out = division_places(rows)
    assert [p["name"] for p in out] == ["Banepa", "धुलिखेल"]            # ward and non-place subtype dropped
    assert out[0]["name_local"] == "बनेपा नगरपालिका" and out[0]["kind"] == "municipality" and out[1]["kind"] == "town"


def test_place_search_matches_both_scripts_and_ranks_towns_first():
    from gis_backend.network import Network

    from .helpers import make_artifact

    art = make_artifact([(1, 2, {})], {1: (0, 0), 2: (1, 0)})
    art["places"] = [
        {"id": "1", "name": "Banepa Tol", "name_local": "", "lat": 27.6, "lng": 85.5, "kind": "hamlet"},
        {"id": "2", "name": "Banepa", "name_local": "बनेपा नगरपालिका", "lat": 27.6, "lng": 85.5, "kind": "municipality"},
        {"id": "3", "name": "Old Banepa Quarter", "name_local": "", "lat": 27.6, "lng": 85.5, "kind": "neighborhood"}]
    n = Network(art)
    assert [p["id"] for p in n.search_places("banepa")] == ["2", "1", "3"]   # prefix + rank, then substring
    assert [p["id"] for p in n.search_places("बनेपा")] == ["2"]              # Devanagari query
