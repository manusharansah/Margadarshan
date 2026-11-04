"""
Hazard indices for road pieces.

``terrain_susceptibility``
    Landslide susceptibility from the hillslope angle of the DEM around the road.
    Linear ramp: 0 below 15 deg, 1 above 45 deg. Steep slopes above the road are the
    first-order control on slope failures in the Himalaya; this is a transparent
    screening index, **not** a calibrated probability.

``incident_proximity``
    Optional. If a catalogue of past landslide/flood incidents is supplied
    (CSV with latitude/longitude columns, e.g. an export from the BIPAD portal or
    NASA's Global Landslide Catalog), each piece is scored by how many incidents
    fall within ``radius_km``: ``1 - exp(-count / 3)``. With no catalogue this is 0
    and the artifact metadata records that the incident layer is absent.

Rainfall is applied at request time, not here (see ``weather.py``).
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from .osm import haversine_km

TERRAIN_FLOOR_DEG = 15.0
TERRAIN_CEIL_DEG = 45.0


def terrain_susceptibility(slope_deg: float) -> float:
    if math.isnan(slope_deg):
        return 0.0
    return max(0.0, min(1.0, (slope_deg - TERRAIN_FLOOR_DEG) / (TERRAIN_CEIL_DEG - TERRAIN_FLOOR_DEG)))


class IncidentIndex:
    """Grid-bucketed point catalogue for fast neighbourhood counts."""

    CELL = 0.02  # degrees, ~2.2 km

    def __init__(self, points: Iterable[Tuple[float, float]]):
        self.points: List[Tuple[float, float]] = list(points)
        self._grid: Dict[Tuple[int, int], List[Tuple[float, float]]] = defaultdict(list)
        for p in self.points:
            self._grid[self._key(p)].append(p)

    def _key(self, p: Tuple[float, float]) -> Tuple[int, int]:
        return int(math.floor(p[0] / self.CELL)), int(math.floor(p[1] / self.CELL))

    def count_within(self, lat: float, lng: float, radius_km: float = 2.0) -> int:
        ki, kj = self._key((lat, lng))
        total = 0
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for p in self._grid.get((ki + di, kj + dj), ()):
                    if haversine_km((lat, lng), p) <= radius_km:
                        total += 1
        return total

    def proximity(self, lat: float, lng: float, radius_km: float = 2.0) -> float:
        return 1.0 - math.exp(-self.count_within(lat, lng, radius_km) / 3.0)

    @classmethod
    def from_csv(cls, path: str | Path) -> "IncidentIndex":
        lat_keys, lng_keys = ("latitude", "lat", "y"), ("longitude", "lng", "lon", "long", "x")
        pts: List[Tuple[float, float]] = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
            lat_c = next((cols[k] for k in lat_keys if k in cols), None)
            lng_c = next((cols[k] for k in lng_keys if k in cols), None)
            if not lat_c or not lng_c:
                raise ValueError(f"{path}: need latitude/longitude columns, found {list(cols)}")
            for row in reader:
                try:
                    pts.append((float(row[lat_c]), float(row[lng_c])))
                except (TypeError, ValueError):
                    continue
        return cls(pts)
