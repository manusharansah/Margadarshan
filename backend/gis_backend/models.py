"""
Core data models. These mirror the schemas finalized in the workflow
documentation (Section 3: storage node / road edge / vehicle) plus the
request and plan objects the routing pipeline passes between stages.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StorageNode:
    node_id: str
    x: float
    y: float
    units_in_stock: int
    vehicles_available: int
    vehicle_capacity: int = 200

    @property
    def max_deliverable_units(self) -> int:
        return self.vehicles_available * self.vehicle_capacity


@dataclass
class RoadEdge:
    node_a: str
    node_b: str
    length_km: float
    slope_pct: float
    road_type: str  # "paved" | "gravel" | "rough"
    max_speed_kmph: float
    hazard_score: float  # 0 (safe) .. 1 (severe flood/landslide exposure)
    intersection: bool = False
    weight: Optional[float] = None  # filled in once the edge-weight formula runs
    time_hours: Optional[float] = None  # filled in alongside weight
    fuel_estimate: Optional[float] = None  # filled in alongside weight


@dataclass
class DeliveryRequest:
    destination: str
    quantity: int
    deadline_hours: float


@dataclass
class RouteLeg:
    source: str
    path: List[str]
    time_hours: float
    edge_cost: float
    units_carried: int
    vehicles_used: int
    fuel_estimate: float = 0.0
    distance_km: float = 0.0


@dataclass
class CandidatePlan:
    legs: List[RouteLeg]
    plan_type: str  # "single_source" | "multi_source_parallel" | "single_vehicle_multistop"
    total_units: int
    total_time_hours: float
    total_edge_cost: float
    fuel_estimate: float
    vehicles_used: int
    sources_used: List[str] = field(default_factory=list)
