"""
Build the routable network artifact from real data.

    python -m gis_backend.ingest --bbox 27.45 85.25 27.80 85.80 --out data/network.json.gz
    python -m gis_backend.ingest --overpass-json saved.json --incidents landslides.csv ...

Outputs a gzip-JSON artifact holding junction/piece nodes with SRTM elevations,
edges with DEM-derived grade and terrain hazard, OSM place names, and a metadata
block recording exactly which sources were used and how much was inferred.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import networkx as nx

from .dem import Dem
from .hazard import IncidentIndex, terrain_susceptibility
from .osm import (
    Piece, classify_surface, DEFAULT_SPEED, fetch_overpass, haversine_km, parse_maxspeed, split_ways,
)

SAMPLE_SPACING_KM = 0.03  # one DEM sample per ~30 m, the SRTM cell size


def _densify(coords: List[Tuple[float, float]]) -> List[Tuple[float, float, float]]:
    """Interpolate points every ~30 m along a polyline; returns (lat, lng, cum_km)."""
    out: List[Tuple[float, float, float]] = [(coords[0][0], coords[0][1], 0.0)]
    cum = 0.0
    for a, b in zip(coords, coords[1:]):
        seg = haversine_km(a, b)
        n = max(1, int(math.ceil(seg / SAMPLE_SPACING_KM)))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, cum + seg * t))
        cum += seg
    return out


def _smooth(values: List[float], half_window: int = 2) -> List[float]:
    """Centred moving average (5 samples ~ 150 m) with the window shrinking at the ends.

    SRTM has metre-level relative noise; summing raw 30 m height differences would
    inflate climb and grade, so heights are smoothed before differencing."""
    n = len(values)
    out = []
    for i in range(n):
        h = min(half_window, i, n - 1 - i)  # shrink at the ends so endpoint heights stay anchored
        out.append(sum(values[i - h:i + h + 1]) / (2 * h + 1))
    return out


def build_edge(piece: Piece, dem: Dem, incidents: Optional[IncidentIndex]) -> Optional[dict]:
    dense = _densify(piece.coords)
    elev = [dem.elevation(la, ln) for la, ln, _ in dense]
    slope_deg = [dem.terrain_slope_deg(la, ln) for la, ln, _ in dense]
    if sum(1 for z in elev if math.isnan(z)) > len(elev) // 2:
        return None  # over a DEM void
    last = next(z for z in elev if not math.isnan(z))
    for i, z in enumerate(elev):  # forward-fill isolated voids
        if math.isnan(z):
            elev[i] = last
        last = elev[i]
    smooth = _smooth(elev)
    length_km = dense[-1][2]
    if length_km <= 0:
        return None

    ascent = sum(max(0.0, b - a) for a, b in zip(smooth, smooth[1:]))
    descent = sum(max(0.0, a - b) for a, b in zip(smooth, smooth[1:]))
    # steepest stretch measured over ~90 m windows (3 samples), not single 30 m steps
    span = 3
    grades = [abs(smooth[i + span] - smooth[i]) / max((dense[i + span][2] - dense[i][2]) * 1000, 1.0) * 100
              for i in range(len(smooth) - span)] or [abs(smooth[-1] - smooth[0]) / (length_km * 1000) * 100]
    valid_slopes = [s for s in slope_deg if not math.isnan(s)]
    hillslope = statistics.fmean(valid_slopes) if valid_slopes else float("nan")

    tags = piece.tags
    surface, inferred = classify_surface(tags)
    highway = tags.get("highway", "unclassified")
    speed = parse_maxspeed(tags) or float(DEFAULT_SPEED.get(highway, 25))

    mid = dense[len(dense) // 2]
    hazard_incident = incidents.proximity(mid[0], mid[1]) if incidents else 0.0

    # Per-vertex elevations for the polyline shown on the map / elevation profile.
    vert_elev = []
    for la, ln in piece.coords:
        z = dem.elevation(la, ln)
        vert_elev.append(0.0 if math.isnan(z) else z)

    return {
        "a": piece.nodes[0], "b": piece.nodes[-1],
        "length_km": round(length_km, 4),
        "slope_pct": round((ascent + descent) / (length_km * 1000) * 100, 2),
        "max_slope_pct": round(max(grades) if grades else 0.0, 2),
        "ascent_ab_m": round(ascent, 1), "descent_ab_m": round(descent, 1),
        "road_type": surface, "highway": highway, "max_speed_kmph": speed,
        "hazard_terrain": round(terrain_susceptibility(hillslope), 3),
        "hazard_incident": round(hazard_incident, 3),
        "surface_inferred": inferred, "name": tags.get("name", ""),
        "geometry": [[round(la, 5), round(ln, 5), round(z, 1)] for (la, ln), z in zip(piece.coords, vert_elev)],
    }


def build_network(overpass: dict, dem: Dem, incidents: Optional[IncidentIndex] = None,
                  bbox: Optional[Tuple[float, float, float, float]] = None,
                  max_piece_km: float = 0.5, progress: bool = False) -> dict:
    pieces, places = split_ways(overpass.get("elements", []), max_piece_km)
    return build_network_from_pieces(pieces, places, dem, incidents, bbox, max_piece_km, progress,
                                     roads_source="OpenStreetMap contributors (ODbL) via Overpass API")


def build_network_from_pieces(pieces: List[Piece], places: List[dict], dem: Dem,
                              incidents: Optional[IncidentIndex] = None,
                              bbox: Optional[Tuple[float, float, float, float]] = None,
                              max_piece_km: float = 0.5, progress: bool = False,
                              roads_source: str = "") -> dict:
    edges: List[dict] = []
    for i, piece in enumerate(pieces):
        edge = build_edge(piece, dem, incidents)
        if edge:
            edges.append(edge)
        if progress and i % 500 == 0:
            print(f"  edges {i}/{len(pieces)}", flush=True)

    # Keep only the largest connected component so every node can reach every other.
    g = nx.Graph()
    g.add_edges_from((e["a"], e["b"]) for e in edges)
    if g.number_of_nodes() == 0:
        raise ValueError("No drivable road pieces found in the supplied OSM data.")
    keep = max(nx.connected_components(g), key=len)
    edges = [e for e in edges if e["a"] in keep]

    node_xyz: Dict[int, Tuple[float, float, float]] = {}
    for e in edges:
        node_xyz[e["a"]] = tuple(e["geometry"][0])  # type: ignore[assignment]
        node_xyz[e["b"]] = tuple(e["geometry"][-1])  # type: ignore[assignment]

    inferred = sum(1 for e in edges if e["surface_inferred"])
    total_km = sum(e["length_km"] for e in edges)
    meta = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bbox_s_w_n_e": list(bbox) if bbox else None,
        "sources": {
            "roads": roads_source,
            "elevation": "NASA SRTM 1 arc-second via AWS Open Data 'Terrain Tiles' (skadi)",
            "incidents": f"{len(incidents.points)} catalogued incidents" if incidents else "not supplied",
        },
        "counts": {"nodes": len(node_xyz), "edges": len(edges), "places": len(places)},
        "network_km": round(total_km, 1),
        "surface_inferred_fraction": round(inferred / len(edges), 3),
        "max_piece_km": max_piece_km,
    }
    return {
        "meta": meta,
        "nodes": [[nid, *xyz] for nid, xyz in node_xyz.items()],
        "edges": edges,
        "places": places,
    }


def save(artifact: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(artifact, fh, separators=(",", ":"))


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                    default=[27.45, 85.25, 27.80, 85.80], help="study area (default: Kathmandu valley east -> Kavre)")
    ap.add_argument("--source", choices=["overture", "overpass"], default="overture",
                    help="road data source (default: Overture Maps on public S3; no API server needed)")
    ap.add_argument("--all-road-classes", action="store_true",
                    help="(overture) include residential lanes and tracks - much larger network")
    ap.add_argument("--overture-release", help="(overture) release id; default: latest")
    ap.add_argument("--overpass-json", help="use a saved Overpass response instead of querying the API")
    ap.add_argument("--incidents", help="CSV of past landslide/flood incidents (latitude, longitude columns)")
    ap.add_argument("--out", default="data/network.json.gz")
    ap.add_argument("--dem-dir", default="data/dem")
    ap.add_argument("--max-piece-km", type=float, default=0.5)
    args = ap.parse_args(argv)

    incidents = IncidentIndex.from_csv(args.incidents) if args.incidents else None
    bbox = tuple(args.bbox)
    if args.source == "overture" and not args.overpass_json:
        from .overture import ALL_DRIVABLE_CLASSES, TRUCK_CLASSES, load_overture

        pieces, places, release = load_overture(
            bbox, Path(args.dem_dir).parent, ALL_DRIVABLE_CLASSES if args.all_road_classes else TRUCK_CLASSES,
            args.overture_release)  # type: ignore[arg-type]
        print(f"{len(pieces)} road pieces; sampling elevation ...", flush=True)
        artifact = build_network_from_pieces(
            pieces, places, Dem(args.dem_dir), incidents, bbox, args.max_piece_km, progress=True,  # type: ignore[arg-type]
            roads_source=f"Overture Maps Foundation transportation theme, release {release} "
                         "(open data, largely OpenStreetMap-derived; ODbL)")
    else:
        if args.overpass_json:
            overpass = json.loads(Path(args.overpass_json).read_text())
        else:
            print("Querying Overpass API ...", flush=True)
            overpass = fetch_overpass(bbox)  # type: ignore[arg-type]
        artifact = build_network(overpass, Dem(args.dem_dir), incidents, bbox,  # type: ignore[arg-type]
                                 args.max_piece_km, progress=True)
    save(artifact, Path(args.out))
    print(json.dumps(artifact["meta"], indent=2))


if __name__ == "__main__":
    main()
