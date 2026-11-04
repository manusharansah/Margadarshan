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

from . import truck_model as tm
from .edge_weight import HAZARD_KM_PER_KM, compute_arrays, hazard_array, heading_change_deg_per_km
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
        self.edges: List[RoadEdge] = []
        for e in artifact["edges"]:
            edge = RoadEdge(
                node_a=e["a"], node_b=e["b"], length_km=e["length_km"], slope_pct=e["slope_pct"],
                max_slope_pct=e["max_slope_pct"], ascent_ab_m=e["ascent_ab_m"], descent_ab_m=e["descent_ab_m"],
                road_type=e["road_type"], highway=e["highway"], max_speed_kmph=e["max_speed_kmph"],
                hazard_terrain=e["hazard_terrain"], hazard_incident=e["hazard_incident"],
                surface_inferred=e.get("surface_inferred", False), name=e.get("name", ""),
                oneway=int(e.get("oneway", 0)), surface_mix=tuple(e["surface_mix"]) if e.get("surface_mix") else None,
                geometry=[tuple(p) for p in e["geometry"]],
            )
            edge.curvature_deg_per_km = heading_change_deg_per_km(edge.geometry, edge.length_km)
            self.edges.append(edge)
        self._lock = threading.RLock()
        self.rain_index_at: Callable[[float, float], float] = lambda lat, lng: 0.0
        self.truck = tm.DEFAULT_TRUCK
        self.closures: List[dict] = []   # {"lat","lng","radius_m"}; arcs within radius are removed
        self.arrays = compute_arrays(self.edges, self.truck)
        self._mid = [e.geometry[len(e.geometry) // 2] for e in self.edges]
        self.graph = nx.DiGraph()
        self.rebuild_graph()
        # Only the largest strongly connected component is routable (one-way roads can strand nodes).
        keep = max(nx.strongly_connected_components(self.graph), key=len)
        self.dropped_nodes = self.graph.number_of_nodes() - len(keep)
        self.nodes = {i: n for i, n in self.nodes.items() if i in keep}
        self._ids = np.array(list(self.nodes.keys()), dtype=np.int64)
        self._lat = np.array([self.nodes[i].lat for i in self._ids])
        self._lng = np.array([self.nodes[i].lng for i in self._ids])
        self._keep = keep
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
    def _closed(self, lat: float, lng: float) -> bool:
        k = math.cos(math.radians(lat))
        return any(math.hypot((lat - c["lat"]) * 111_320, (lng - c["lng"]) * 111_320 * k) <= c["radius_m"]
                   for c in self.closures)

    def rebuild_graph(self) -> None:
        """(Re)compute arc weights from static attributes, current rainfall and operator closures."""
        with self._lock:
            keep = getattr(self, "_keep", None)
            rain = np.array([self.rain_index_at(m[0], m[1]) for m in self._mid])
            hazard = hazard_array(self.edges, rain)
            weight = self.arrays["base_cost"] + hazard * self.arrays["length"] * HAZARD_KM_PER_KM
            g = nx.DiGraph()
            for nid, n in self.nodes.items():
                if keep is None or nid in keep:
                    g.add_node(nid, lat=n.lat, lng=n.lng, elev=n.elevation_m)
            for i, e in enumerate(self.edges):
                if keep is not None and (e.node_a not in keep or e.node_b not in keep):
                    continue
                if self.closures and self._closed(*self._mid[i]):
                    continue
                for fwd in (True, False):
                    if (fwd and e.oneway == -1) or (not fwd and e.oneway == 1):
                        continue
                    u, v = (e.node_a, e.node_b) if fwd else (e.node_b, e.node_a)
                    tag = "ab" if fwd else "ba"
                    data = dict(
                        weight=float(weight[i]), time=float(self.arrays[f"time_{tag}"][i]), length=e.length_km,
                        fuel=float(self.arrays[f"fuel_{tag}"][i]), hazard=float(hazard[i]), rain_index=float(rain[i]),
                        ascent=e.ascent_ab_m if fwd else e.descent_ab_m, descent=e.descent_ab_m if fwd else e.ascent_ab_m,
                        geometry=e.geometry if fwd else e.geometry[::-1], edge=e,
                    )
                    old = g.get_edge_data(u, v)
                    if old is None or data["weight"] < old["weight"]:  # parallel roads: keep the cheaper arc
                        g.add_edge(u, v, **data)
            self.graph = g

    def apply_rain(self, rain_index_at: Callable[[float, float], float]) -> None:
        self.rain_index_at = rain_index_at
        self.rebuild_graph()

    def set_closures(self, closures: List[dict]) -> None:
        self.closures = list(closures)
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
