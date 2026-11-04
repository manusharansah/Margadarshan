"""
Domain models shared by the ingestion pipeline, the routing engine and the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

LatLngElev = Tuple[float, float, float]


@dataclass
class RoadNode:
    node_id: int
    lat: float
    lng: float
    elevation_m: float


@dataclass
class RoadEdge:
    """A drivable road piece between two graph nodes (<= ~0.5 km after ingestion)."""

    node_a: int
    node_b: int
    length_km: float
    slope_pct: float            # mean absolute grade from the DEM
    max_slope_pct: float        # steepest ~30 m stretch
    ascent_ab_m: float          # climb when driving a -> b (descent when driving b -> a)
    descent_ab_m: float
    road_type: str              # "paved" | "gravel" | "rough"
    highway: str                # raw OSM highway class
    max_speed_kmph: float
    hazard_terrain: float       # 0..1 hillslope landslide susceptibility
    hazard_incident: float      # 0..1 historical-incident proximity (0 if no incident data)
    surface_inferred: bool = False
    name: str = ""
    geometry: List[LatLngElev] = field(default_factory=list)  # oriented a -> b


@dataclass
class StorageNode:
    """A warehouse. Inventory and fleet are operator-supplied business data."""

    node_id: str
    name: str
    lat: float
    lng: float
    units_in_stock: int
    vehicles_available: int
    vehicle_capacity: int = 200
    road_node: Optional[int] = None
    snap_distance_m: float = 0.0

    @property
    def max_deliverable_units(self) -> int:
        return self.vehicles_available * self.vehicle_capacity


@dataclass
class DeliveryRequest:
    destination_lat: float
    destination_lng: float
    quantity: int
    deadline_hours: float
    destination_name: str = "Destination"


@dataclass
class RouteLeg:
    source: str
    path: List[int]
    time_hours: float
    edge_cost: float
    units_carried: int
    vehicles_used: int
    fuel_litres: float = 0.0
    distance_km: float = 0.0
    max_hazard: float = 0.0
    ascent_m: float = 0.0
    descent_m: float = 0.0
    geometry: List[LatLngElev] = field(default_factory=list)
    pickups: List[str] = field(default_factory=list)  # warehouse ids visited, in order
    hotspots: List[dict] = field(default_factory=list)  # riskiest road stretches on this leg


@dataclass
class CandidatePlan:
    legs: List[RouteLeg]
    plan_type: str  # "single_source" | "multi_source_parallel" | "single_vehicle_multistop"
    total_units: int
    total_time_hours: float
    total_edge_cost: float
    fuel_litres: float
    vehicles_used: int
    distance_km: float = 0.0
    sources_used: List[str] = field(default_factory=list)
    meets_deadline: bool = True
