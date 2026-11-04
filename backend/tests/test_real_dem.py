"""
Tests against REAL SRTM elevation data (AWS Terrain Tiles). They download the
~15 MB N27E085 tile once (cached under data/dem) and are skipped automatically
if the tile cannot be fetched (offline CI).
"""
import math
import os

import pytest

from gis_backend.dem import Dem, DemError, tile_name
from gis_backend.ingest import build_network

pytestmark = pytest.mark.network
CACHE = os.environ.get("MARGA_TEST_DEM_DIR", "data/dem")


@pytest.fixture(scope="module")
def dem():
    d = Dem(CACHE)
    try:
        d.elevation(27.62, 85.54)
    except DemError as exc:
        pytest.skip(f"SRTM tile unavailable: {exc}")
    return d


def test_tile_naming():
    assert tile_name(27.62, 85.54)[0] == "N27E085"
    assert tile_name(-1.5, -70.2)[0] == "S02W071"


# Published settlement elevations (m, from municipal/Survey Dept. sources). SRTM is a
# ~30 m surface model so we allow +/-100 m: this guards against flipped axes or tile
# mix-ups (which produce errors of hundreds of metres), not centimetre accuracy.
@pytest.mark.parametrize("name,lat,lng,published", [
    ("Kathmandu (Durbar Sq.)", 27.7043, 85.3070, 1350),
    ("Dhulikhel", 27.6186, 85.5395, 1550),
    ("Banepa", 27.6298, 85.5214, 1490),
])
def test_sampled_elevation_matches_published(dem, name, lat, lng, published):
    assert abs(dem.elevation(lat, lng) - published) < 100, name


def test_valley_floor_is_lower_than_ridge(dem):
    sunkoshi_valley = dem.elevation(27.6392, 85.7114)
    ridge = dem.elevation(27.7156, 85.5200)
    assert ridge - sunkoshi_valley > 800


def test_bilinear_is_continuous_across_cell_edges(dem):
    a, b = dem.elevation(27.62, 85.54), dem.elevation(27.62, 85.54 + 1e-7)
    assert abs(a - b) < 0.1


def test_terrain_slope_flat_valley_vs_steep_hillside(dem):
    assert dem.terrain_slope_deg(27.7043, 85.3070) < 5          # Kathmandu valley floor
    assert dem.terrain_slope_deg(27.7156, 85.5200) > 15          # Nagarkot ridge flank


def test_ingestion_with_real_elevation_on_a_hill_road(dem):
    """Road geometry is a fixture; every elevation/grade/hazard value below is derived from real SRTM."""
    # 2 km track climbing from the Sunkoshi valley (~700 m) toward Dhulikhel.
    pts = [(1, 27.6392, 85.7114), (2, 27.6392 - 0.009, 85.7114 - 0.012), (3, 27.6392 - 0.018, 85.7114 - 0.024)]
    elements = [{"type": "way", "id": 1, "nodes": [p[0] for p in pts],
                 "tags": {"highway": "tertiary", "surface": "asphalt"},
                 "geometry": [{"lat": p[1], "lon": p[2]} for p in pts]}]
    art = build_network(elements and {"elements": elements}, dem, max_piece_km=0.5)
    assert art["meta"]["counts"]["edges"] >= 4
    zs = [n[3] for n in art["nodes"]]
    assert all(0 < z < 3000 for z in zs) and max(zs) - min(zs) > 20  # not a flat/constant surface
    assert all(e["slope_pct"] >= 0 and not math.isnan(e["slope_pct"]) for e in art["edges"])
    assert sum(e["ascent_ab_m"] + e["descent_ab_m"] for e in art["edges"]) > 20
    assert art["meta"]["sources"]["elevation"].startswith("NASA SRTM")
