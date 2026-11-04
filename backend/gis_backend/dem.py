"""
Digital elevation model access (real SRTM data).

Elevation comes from the public "Terrain Tiles" dataset on AWS Open Data
(``s3://elevation-tiles-prod/skadi``), which redistributes NASA SRTM 1-arc-second
(~30 m) height tiles as gzip-compressed ``.hgt`` files:

* one tile per 1 deg x 1 deg cell, named by its south-west corner (``N27E085``);
* 3601 x 3601 big-endian signed 16-bit integers (metres above sea level);
* row 0 is the northern edge, column 0 the western edge, ``-32768`` marks a void.

Tiles are downloaded on first use and cached on disk, so ingestion is
repeatable and works offline once the tiles for a study area are present.
"""
from __future__ import annotations

import gzip
import math
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import requests

TILE_URL = "https://elevation-tiles-prod.s3.amazonaws.com/skadi/{ns}{lat:02d}/{ns}{lat:02d}{ew}{lng:03d}.hgt.gz"
TILE_SIZE = 3601  # samples per side (1 arc-second, edges shared with neighbours)
VOID = -32768


class DemError(RuntimeError):
    """Raised when elevation data cannot be obtained for a coordinate."""


def tile_name(lat: float, lng: float) -> Tuple[str, int, int]:
    lat0, lng0 = math.floor(lat), math.floor(lng)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lng0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lng0):03d}", lat0, lng0


class Dem:
    """Lazy, cached, bilinear-interpolating SRTM reader."""

    def __init__(self, cache_dir: os.PathLike | str, session: Optional[requests.Session] = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._tiles: Dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        self._session = session or requests.Session()

    # ------------------------------------------------------------------ tiles
    def _load_tile(self, lat0: int, lng0: int) -> np.ndarray:
        name, _, _ = tile_name(lat0 + 0.5, lng0 + 0.5)
        with self._lock:
            if name in self._tiles:
                return self._tiles[name]
            raw_path = self.cache_dir / f"{name}.hgt"
            if not raw_path.exists():
                self._download(name, lat0, lng0, raw_path)
            data = np.fromfile(raw_path, dtype=">i2")
            if data.size != TILE_SIZE * TILE_SIZE:
                raise DemError(f"{raw_path} has {data.size} samples, expected {TILE_SIZE ** 2}")
            arr = data.reshape(TILE_SIZE, TILE_SIZE).astype(np.float32)
            arr[arr == VOID] = np.nan
            self._tiles[name] = arr
            return arr

    def _download(self, name: str, lat0: int, lng0: int, dest: Path) -> None:
        url = TILE_URL.format(
            ns="N" if lat0 >= 0 else "S", lat=abs(lat0),
            ew="E" if lng0 >= 0 else "W", lng=abs(lng0),
        )
        try:
            resp = self._session.get(url, timeout=120)
            resp.raise_for_status()
            payload = gzip.decompress(resp.content)
        except (requests.RequestException, OSError) as exc:
            raise DemError(f"Could not download elevation tile {name} from {url}: {exc}") from exc
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(payload)
        tmp.replace(dest)

    # --------------------------------------------------------------- sampling
    def elevation(self, lat: float, lng: float) -> float:
        """Bilinear-interpolated elevation in metres; NaN if over a data void."""
        _, lat0, lng0 = tile_name(lat, lng)
        tile = self._load_tile(lat0, lng0)
        row = (lat0 + 1 - lat) * (TILE_SIZE - 1)
        col = (lng - lng0) * (TILE_SIZE - 1)
        r0 = min(int(math.floor(row)), TILE_SIZE - 2)
        c0 = min(int(math.floor(col)), TILE_SIZE - 2)
        fr, fc = row - r0, col - c0
        z = tile[r0:r0 + 2, c0:c0 + 2]
        top = z[0, 0] * (1 - fc) + z[0, 1] * fc
        bot = z[1, 0] * (1 - fc) + z[1, 1] * fc
        return float(top * (1 - fr) + bot * fr)

    def terrain_slope_deg(self, lat: float, lng: float, step_m: float = 30.0) -> float:
        """Hillslope angle (degrees) from central differences around a point."""
        dlat = step_m / 111_320.0
        dlng = step_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
        zn = self.elevation(lat + dlat, lng)
        zs = self.elevation(lat - dlat, lng)
        ze = self.elevation(lat, lng + dlng)
        zw = self.elevation(lat, lng - dlng)
        if any(math.isnan(v) for v in (zn, zs, ze, zw)):
            return float("nan")
        gy = (zn - zs) / (2 * step_m)
        gx = (ze - zw) / (2 * step_m)
        return math.degrees(math.atan(math.hypot(gx, gy)))
