"""
Deterministic feasibility checks (workflow doc, Step 5). Everything here
is plain arithmetic, computed in code before any plan is handed to Gemma.
"""
import math

from .models import StorageNode


def can_fulfill_alone(node: StorageNode, quantity: int) -> bool:
    return node.units_in_stock >= quantity and node.max_deliverable_units >= quantity


def vehicles_needed(quantity: int, vehicle_capacity: int = 200) -> int:
    return math.ceil(quantity / vehicle_capacity)
