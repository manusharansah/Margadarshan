"""
Build a DEVELOPMENT FIXTURE network so the stack can be exercised end-to-end
without internet access to OpenStreetMap.

What is real: the SRTM elevations, and therefore every grade/ascent/hazard value,
come from the real DEM through the real ingestion pipeline.
What is NOT real: the road layout is an artificial lattice. The artifact says so
in its metadata (``is_fixture``) and the UI shows a banner. Never deploy it.

    python scripts/make_dev_fixture.py data/dev/network.json.gz
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gis_backend.dem import Dem  # noqa: E402
from gis_backend.ingest import build_network, save  # noqa: E402

S, W, N, E = 27.55, 85.45, 27.72, 85.62
ROWS = COLS = 9
CLASSES = [("primary", "asphalt"), ("secondary", "asphalt"), ("tertiary", None), ("unclassified", "gravel"), ("track", "dirt")]


def main(out: str) -> None:
    pts = {(r, c): (S + (N - S) * r / (ROWS - 1), W + (E - W) * c / (COLS - 1)) for r in range(ROWS) for c in range(COLS)}
    nid = {rc: 1000 + rc[0] * COLS + rc[1] for rc in pts}
    ways, wid = [], 1
    for r in range(ROWS):
        for c in range(COLS):
            for dr, dc in ((0, 1), (1, 0)):
                r2, c2 = r + dr, c + dc
                if r2 < ROWS and c2 < COLS:
                    hw, surf = CLASSES[(r * 3 + c * 5 + dr) % len(CLASSES)]
                    tags = {"highway": hw, **({"surface": surf} if surf else {})}
                    ways.append({"type": "way", "id": wid, "nodes": [nid[(r, c)], nid[(r2, c2)]], "tags": tags,
                                 "geometry": [{"lat": pts[(r, c)][0], "lon": pts[(r, c)][1]},
                                              {"lat": pts[(r2, c2)][0], "lon": pts[(r2, c2)][1]}]})
                    wid += 1
    places = [{"type": "node", "id": 9000 + i, "lat": la, "lon": lo, "tags": {"name": n, "place": "town"}}
              for i, (n, la, lo) in enumerate([("Banepa", 27.6298, 85.5214), ("Dhulikhel", 27.6186, 85.5395), ("Panauti", 27.5847, 85.5225)])]
    art = build_network({"elements": ways + places}, Dem("data/dem"), None, (S, W, N, E), max_piece_km=0.5)
    art["meta"]["is_fixture"] = True
    art["meta"]["sources"]["roads"] = "DEVELOPMENT FIXTURE - synthetic lattice, not real roads"
    save(art, Path(out))
    print(json.dumps(art["meta"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/dev/network.json.gz")
