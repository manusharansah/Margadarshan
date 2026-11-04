"""Test fixtures. The networks here are tiny hand-built graphs used ONLY to verify
algorithms against brute force; they are never shipped or served as real data."""
from __future__ import annotations

import math

from gis_backend.network import Network

LAT0, LNG0 = 27.60, 85.50


def make_artifact(spec_edges, node_xy, elev=None):
    """spec_edges: [(a, b, dict(overrides))]; node_xy: {id: (north_km, east_km)}."""
    elev = elev or {}
    nodes = []
    for nid, (n_km, e_km) in node_xy.items():
        nodes.append([nid, LAT0 + n_km / 111.32, LNG0 + e_km / (111.32 * math.cos(math.radians(LAT0))), elev.get(nid, 1000.0)])
    by = {n[0]: n for n in nodes}
    edges = []
    for a, b, o in spec_edges:
        la, lo = by[a][1:3], by[b][1:3]
        (na, ea), (nb, eb) = node_xy[a], node_xy[b]
        length = o.pop("length_km", math.hypot(na - nb, ea - eb))
        e = dict(a=a, b=b, length_km=round(length, 4), slope_pct=0.0, max_slope_pct=0.0, ascent_ab_m=0.0,
                 descent_ab_m=0.0, road_type="paved", highway="secondary", max_speed_kmph=40.0,
                 hazard_terrain=0.0, hazard_incident=0.0, surface_inferred=False, name="",
                 geometry=[[la[0], la[1], by[a][3]], [by[b][1], by[b][2], by[b][3]]])
        e.update(o)
        edges.append(e)
    meta = {"built_at": "test", "counts": {}, "sources": {"roads": "test fixture"}}
    return {"meta": meta, "nodes": nodes, "edges": edges, "places": [{"id": "t1", "name": "Testville", "lat": LAT0, "lng": LNG0, "kind": "town"}]}


def grid_network(n=4, step_km=2.0):
    """n x n lattice; row/col-dependent surface & hazard make routes non-trivial (deterministic, no RNG)."""
    xy = {r * n + c: (r * step_km, c * step_km) for r in range(n) for c in range(n)}
    edges = []
    for r in range(n):
        for c in range(n):
            i = r * n + c
            for j in ((i + 1) if c < n - 1 else None, (i + n) if r < n - 1 else None):
                if j is None:
                    continue
                k = (i * 7 + j * 3) % 5
                edges.append((i, j, dict(road_type=["paved", "gravel", "rough", "paved", "gravel"][k],
                                         hazard_terrain=[0.0, 0.2, 0.9, 0.1, 0.4][k], slope_pct=[2, 6, 12, 3, 8][k],
                                         max_speed_kmph=[50, 40, 30, 50, 40][k])))
    return Network(make_artifact(edges, xy))
