"""
Loaded road network: the routable graph plus spatial snapping.

The graph is undirected (hill roads are two-way for trucks; one-way tags are
not modelled - listed in docs/DATA_SOURCES.md). Edge cost depends on live
rainfall, so ``apply_rain`` can re-weight the graph in place.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np

from .edge_weight import compute_edge_time_hours, compute_edge_weight, effective_hazard, fuel_litres
from .models import RoadEdge, RoadNode

MAX_SNAP_KM = 5.0


class NetworkUnavailable(RuntimeError):
    pass


class Network:
    def __init__(self, artifact: dict):
        self.meta: dict = artifact["meta"]
        self.places: List[dict] = artifact.get("places", [])
        self.nodes: Dict[int, RoadNode] = {
            int(n[0]): RoadNode(int(n[0]), n[1], n[2], n[3]) for n in artifact["nodes"]
        }
        self.edges: List[RoadEdge] = [RoadEdge(
            node_a=e["a"], node_b=e["b"], length_km=e["length_km"], slope_pct=e["slope_pct"],
            max_slope_pct=e["max_slope_pct"], ascent_ab_m=e["ascent_ab_m"], descent_ab_m=e["descent_ab_m"],
            road_type=e["road_type"], highway=e["highway"], max_speed_kmph=e["max_speed_kmph"],
            hazard_terrain=e["hazard_terrain"], hazard_incident=e["hazard_incident"],
            surface_inferred=e.get("surface_inferred", False), name=e.get("name", ""),
            geometry=[tuple(p) for p in e["geometry"]],
        ) for e in artifact["edges"]]
        self._ids = np.array(list(self.nodes.keys()), dtype=np.int64)
        self._lat = np.array([self.nodes[i].lat for i in self._ids])
        self._lng = np.array([self.nodes[i].lng for i in self._ids])
        self.graph = nx.Graph()
        self._lock = threading.RLock()
        self.rain_index_at: Callable[[float, float], float] = lambda lat, lng: 0.0
        self.rebuild_graph()

    # ------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: Path) -> "Network":
        if not Path(path).exists():
            raise NetworkUnavailable(
                f"Road network artifact not found at {path}. Build it with "
                "`python -m gis_backend.ingest` (see docs/DATA_SOURCES.md)."
            )
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # -------------------------------------------------------------- graph
    def rebuild_graph(self) -> None:
        """(Re)compute edge weights from static attributes and current rainfall."""
        with self._lock:
            g = nx.Graph()
            for nid, n in self.nodes.items():
                g.add_node(nid, lat=n.lat, lng=n.lng, elev=n.elevation_m)
            for e in self.edges:
                mid = e.geometry[len(e.geometry) // 2]
                rain = self.rain_index_at(mid[0], mid[1])
                existing = g.get_edge_data(e.node_a, e.node_b)
                data = dict(
                    weight=compute_edge_weight(e, rain), time=compute_edge_time_hours(e),
                    length=e.length_km, fuel_ab=fuel_litres(e, True), fuel_ba=fuel_litres(e, False),
                    hazard=effective_hazard(e, rain), rain_index=rain, a=e.node_a, edge=e,
                )
                # Parallel roads between the same two nodes: keep the cheaper one.
                if existing is None or data["weight"] < existing["weight"]:
                    g.add_edge(e.node_a, e.node_b, **data)
            self.graph = g

    def apply_rain(self, rain_index_at: Callable[[float, float], float]) -> None:
        self.rain_index_at = rain_index_at
        self.rebuild_graph()

    # ------------------------------------------------------------ snapping
    def snap(self, lat: float, lng: float) -> Tuple[int, float]:
        """Nearest graph node and distance in metres."""
        k = math.cos(math.radians(lat))
        d2 = ((self._lat - lat) * 111.32) ** 2 + ((self._lng - lng) * 111.32 * k) ** 2
        i = int(np.argmin(d2))
        dist_km = math.sqrt(float(d2[i]))
        if dist_km > MAX_SNAP_KM:
            raise ValueError(
                f"({lat:.4f}, {lng:.4f}) is {dist_km:.1f} km from the nearest mapped road "
                f"(limit {MAX_SNAP_KM:.0f} km); it lies outside the loaded network."
            )
        return int(self._ids[i]), dist_km * 1000

    def bbox(self) -> Tuple[float, float, float, float]:
        return float(self._lat.min()), float(self._lng.min()), float(self._lat.max()), float(self._lng.max())

    KIND_RANK = {"city": 0, "municipality": 0, "town": 1, "county": 2, "village": 3, "hamlet": 4}

    def search_places(self, q: str, limit: int = 8) -> List[dict]:
        """Case-insensitive substring match on the English or local name; towns rank above neighbourhoods."""
        q = q.strip().lower()
        if not q:
            return []

        def score(p: dict):
            names = [p["name"].lower(), (p.get("name_local") or "").lower()]
            starts = any(n.startswith(q) for n in names)
            return (0 if starts else 1, self.KIND_RANK.get(p.get("kind", ""), 5), len(p["name"]), p["name"])

        hits = [p for p in self.places if q in p["name"].lower() or q in (p.get("name_local") or "").lower()]
        return sorted(hits, key=score)[:limit]
