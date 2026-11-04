"""
Live rainfall from Open-Meteo (https://open-meteo.com, free for non-commercial use).

For a coarse grid over the network's bounding box we fetch hourly precipitation
for the previous 3 days and the next 1 day, sum it, and convert it to a 0..1
"rain index" that amplifies landslide/flood hazard (see ``edge_weight``).
Results are cached for ``ttl`` seconds. If the service is unreachable the index
is 0 everywhere and ``status()`` says so - the API never invents weather.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .edge_weight import rain_index_from_mm

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
GRID_STEP_DEG = 0.1  # ~11 km


class WeatherService:
    def __init__(self, bbox: Tuple[float, float, float, float], ttl: int = 1800,
                 session: Optional[requests.Session] = None):
        s, w, n, e = bbox
        self.lats = _axis(s, n)
        self.lngs = _axis(w, e)
        self.ttl = ttl
        self._session = session or requests.Session()
        self._lock = threading.Lock()
        self._mm: Dict[Tuple[int, int], float] = {}
        self._fetched_at: Optional[float] = None
        self._error: Optional[str] = None
        self._failed_at: Optional[float] = None

    def refresh(self) -> bool:
        """Fetch precipitation for every grid point. Returns True on success."""
        pts = [(la, ln) for la in self.lats for ln in self.lngs]
        result: Dict[Tuple[int, int], float] = {}
        try:
            for chunk_start in range(0, len(pts), 50):
                chunk = pts[chunk_start:chunk_start + 50]
                resp = self._session.get(OPEN_METEO, params={
                    "latitude": ",".join(f"{p[0]:.3f}" for p in chunk),
                    "longitude": ",".join(f"{p[1]:.3f}" for p in chunk),
                    "hourly": "precipitation", "past_days": 3, "forecast_days": 1, "timezone": "UTC",
                }, timeout=30)
                resp.raise_for_status()
                body = resp.json()
                body = body if isinstance(body, list) else [body]
                if len(body) != len(chunk):
                    raise ValueError(f"Open-Meteo returned {len(body)} locations for {len(chunk)} requested")
                for (la, ln), loc in zip(chunk, body):
                    vals = [v for v in loc["hourly"]["precipitation"] if v is not None]
                    result[(self._ix(self.lats, la), self._ix(self.lngs, ln))] = float(sum(vals))
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
                self._failed_at = time.time()
            return False
        with self._lock:
            self._mm, self._fetched_at, self._error, self._failed_at = result, time.time(), None, None
        return True

    @staticmethod
    def _ix(axis: List[float], v: float) -> int:
        return min(range(len(axis)), key=lambda i: abs(axis[i] - v))

    RETRY_AFTER_FAILURE = 300  # seconds; avoids stalling requests while the provider is down

    def is_stale(self) -> bool:
        if self._failed_at is not None and time.time() - self._failed_at < self.RETRY_AFTER_FAILURE:
            return False
        return self._fetched_at is None or time.time() - self._fetched_at > self.ttl

    def precipitation_mm(self, lat: float, lng: float) -> Optional[float]:
        with self._lock:
            if not self._mm:
                return None
            return self._mm.get((self._ix(self.lats, lat), self._ix(self.lngs, lng)))

    def rain_index_fn(self) -> Callable[[float, float], float]:
        return lambda lat, lng: rain_index_from_mm(self.precipitation_mm(lat, lng))

    def status(self) -> dict:
        with self._lock:
            vals = list(self._mm.values())
            return {
                "source": "Open-Meteo (past 3 days + next 24 h precipitation)",
                "available": bool(vals),
                "fetched_at": self._fetched_at,
                "grid_points": len(self.lats) * len(self.lngs),
                "max_precip_mm": round(max(vals), 1) if vals else None,
                "mean_precip_mm": round(sum(vals) / len(vals), 1) if vals else None,
                "error": self._error,
            }


def _axis(lo: float, hi: float) -> List[float]:
    n = max(1, int(math.ceil((hi - lo) / GRID_STEP_DEG)))
    return [lo + (hi - lo) * (i + 0.5) / n for i in range(n)]
