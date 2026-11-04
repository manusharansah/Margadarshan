"""
OpenStreetMap road-network extraction (Overpass API JSON -> routable pieces).

Pipeline
    1. ``fetch_overpass`` (or a saved Overpass JSON file) -> ways with geometry.
    2. Keep drivable ways; drop private / no-motor-vehicle access.
    3. Split each way at junctions (nodes shared by >1 way) and way ends.
    4. Subdivide long pieces to <= ``max_piece_km`` so that warehouses and
       destinations can be snapped to within a few hundred metres of the road.

Surface and speed are read from OSM tags when present. Nepal's OSM coverage of
``surface`` / ``maxspeed`` is incomplete, so where they are absent we infer them
from the highway class and flag the edge ``surface_inferred`` - the artifact's
metadata reports what fraction of the network is inferred, so users can see how
much of the cost model rests on tags versus defaults.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

DRIVABLE_HIGHWAYS = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|track|"
    "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)

# Typical loaded-truck free-flow speeds (km/h) by OSM class when `maxspeed` is
# untagged. These are planning defaults for hill roads, not legal limits.
DEFAULT_SPEED = {
    "motorway": 60, "motorway_link": 40, "trunk": 50, "trunk_link": 35, "primary": 45,
    "primary_link": 30, "secondary": 40, "secondary_link": 30, "tertiary": 30,
    "tertiary_link": 25, "unclassified": 25, "residential": 20, "living_street": 15, "track": 15,
}
DEFAULT_SURFACE_BY_CLASS = {
    "motorway": "paved", "motorway_link": "paved", "trunk": "paved", "trunk_link": "paved",
    "primary": "paved", "primary_link": "paved", "secondary": "paved", "secondary_link": "paved",
    "tertiary": "paved", "tertiary_link": "paved", "unclassified": "gravel",
    "residential": "gravel", "living_street": "gravel", "track": "rough",
}
PAVED = {"asphalt", "concrete", "paved", "concrete:plates", "concrete:lanes", "paving_stones", "sett", "cobblestone", "metal"}
GRAVEL = {"gravel", "fine_gravel", "compacted", "pebblestone", "unhewn_cobblestone"}
ROUGH = {"dirt", "earth", "ground", "mud", "sand", "grass", "unpaved", "rock", "clay", "dirt/sand"}

Coord = Tuple[float, float]


def haversine_km(a: Coord, b: Coord) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def overpass_query(bbox: Tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return (
        "[out:json][timeout:300];"
        f'(way["highway"~"^({DRIVABLE_HIGHWAYS})$"]({s},{w},{n},{e});'
        f'node["place"~"^(city|town|village|hamlet|suburb|neighbourhood)$"]["name"]({s},{w},{n},{e}););'
        "out geom;"
    )


def fetch_overpass(bbox: Tuple[float, float, float, float], endpoints: Iterable[str] = OVERPASS_ENDPOINTS,
                   retries: int = 3, session: Optional[requests.Session] = None) -> dict:
    sess = session or requests.Session()
    last: Optional[Exception] = None
    for endpoint in endpoints:
        for attempt in range(retries):
            try:
                r = sess.post(endpoint, data={"data": overpass_query(bbox)}, timeout=360,
                              headers={"User-Agent": "MargaAI-ingest/1.0"})
                if r.status_code in (429, 504):
                    time.sleep(10 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Overpass API unreachable after retries: {last}")


# --------------------------------------------------------------- tag handling
def classify_surface(tags: dict) -> Tuple[str, bool]:
    """Return (surface class, inferred?)."""
    surface = (tags.get("surface") or "").lower()
    if surface in PAVED:
        return "paved", False
    if surface in GRAVEL:
        return "gravel", False
    if surface in ROUGH:
        return "rough", False
    if tags.get("tracktype") in {"grade4", "grade5"}:
        return "rough", False
    return DEFAULT_SURFACE_BY_CLASS.get(tags.get("highway", ""), "gravel"), True


def parse_maxspeed(tags: dict) -> Optional[float]:
    raw = tags.get("maxspeed")
    if not raw:
        return None
    try:
        value = float(str(raw).split()[0])
    except ValueError:
        return None
    if "mph" in str(raw):
        value *= 1.609344
    return value if 5 <= value <= 120 else None


def is_drivable(tags: dict) -> bool:
    if tags.get("area") == "yes":
        return False
    if tags.get("access") in {"no", "private"} and tags.get("motor_vehicle") not in {"yes", "permissive", "destination"}:
        return False
    if tags.get("motor_vehicle") in {"no", "private"} or tags.get("motorcar") == "no":
        return False
    return tags.get("highway") in DEFAULT_SPEED


# ---------------------------------------------------------------- chain build
@dataclass
class Piece:
    way_id: int
    tags: dict
    nodes: List[int]            # OSM node ids
    coords: List[Coord]         # (lat, lng) per node


def split_ways(elements: List[dict], max_piece_km: float = 0.5) -> Tuple[List[Piece], List[dict]]:
    """Split OSM ways at junctions and subdivide to <= max_piece_km."""
    ways = [e for e in elements if e.get("type") == "way" and e.get("geometry") and e.get("nodes")
            and len(e["nodes"]) == len(e["geometry"]) and is_drivable(e.get("tags", {}))]
    places = [
        {"id": f"osm:{e['id']}", "name": e["tags"]["name"], "lat": e["lat"], "lng": e["lon"],
         "kind": e["tags"].get("place", "place")}
        for e in elements if e.get("type") == "node" and e.get("tags", {}).get("name") and "lat" in e
    ]

    usage: Dict[int, int] = {}
    for w in ways:
        for nid in set(w["nodes"]):
            usage[nid] = usage.get(nid, 0) + 1

    pieces: List[Piece] = []
    for w in ways:
        counter = [0]
        ids = w["nodes"]
        coords = [(p["lat"], p["lon"]) for p in w["geometry"]]
        start = 0
        for i in range(1, len(ids)):
            if i == len(ids) - 1 or usage[ids[i]] > 1:
                chain_ids, chain_coords = ids[start:i + 1], coords[start:i + 1]
                pieces.extend(_subdivide(w["id"], w.get("tags", {}), chain_ids, chain_coords, max_piece_km, counter))
                start = i
    return pieces, places


def _subdivide(way_id: int, tags: dict, ids: List[int], coords: List[Coord], max_km: float,
               counter: Optional[List[int]] = None) -> List[Piece]:
    """Cut a chain into pieces <= max_km, inserting virtual nodes inside long straight segments."""
    if len(ids) < 2:
        return []
    counter = counter if counter is not None else [0]
    ids2: List[int] = [ids[0]]
    coords2: List[Coord] = [coords[0]]
    for i in range(1, len(ids)):
        seg = haversine_km(coords[i - 1], coords[i])
        extra = int(math.ceil(seg / max_km)) - 1
        for k in range(1, extra + 1):  # virtual node ids are negative: they can never clash with OSM ids
            t = k / (extra + 1)
            counter[0] += 1
            ids2.append(-(way_id * 10_000 + counter[0]))
            coords2.append((coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
                            coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t))
        ids2.append(ids[i])
        coords2.append(coords[i])
    out: List[Piece] = []

    def emit(a: int, b: int) -> None:
        if ids2[a] != ids2[b]:  # ignore degenerate loops collapsed onto one node
            out.append(Piece(way_id, tags, ids2[a:b + 1], coords2[a:b + 1]))

    s, acc = 0, 0.0
    for i in range(1, len(ids2)):
        seg = haversine_km(coords2[i - 1], coords2[i])
        if acc > 0 and acc + seg > max_km * 1.001:  # greedy fill: never exceed the cap
            emit(s, i - 1)
            s, acc = i - 1, 0.0
        acc += seg
    emit(s, len(ids2) - 1)
    return out
