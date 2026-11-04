"""
Warehouse persistence, scoped to one organisation.

Warehouse locations, stock and fleet size are the operator's own business data. There is no public source for them,
so they live in the database and are maintained through the API or ``python -m gis_backend.seed_warehouses``.
Every method takes ``org_id``; the API passes the one from the authenticated session.
"""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from sqlalchemy import delete, select

from .db import WarehouseRow
from .models import StorageNode

CSV_COLUMNS = ["id", "name", "lat", "lng", "units_in_stock", "vehicles_available", "vehicle_capacity"]


def _node(r: WarehouseRow) -> StorageNode:
    return StorageNode(node_id=r.id, name=r.name, lat=r.lat, lng=r.lng, units_in_stock=r.units_in_stock,
                       vehicles_available=r.vehicles_available, vehicle_capacity=r.vehicle_capacity)


class WarehouseStore:
    def __init__(self, session_factory):
        self._session = session_factory

    def list(self, org_id: str) -> List[StorageNode]:
        with self._session() as s:
            rows = s.scalars(select(WarehouseRow).where(WarehouseRow.org_id == org_id).order_by(WarehouseRow.id))
            return [_node(r) for r in rows]

    def get(self, org_id: str, wid: str) -> Optional[StorageNode]:
        with self._session() as s:
            row = s.get(WarehouseRow, (org_id, wid))
            return _node(row) if row else None

    def upsert(self, org_id: str, w: StorageNode) -> StorageNode:
        with self._session() as s, s.begin():
            row = s.get(WarehouseRow, (org_id, w.node_id)) or WarehouseRow(org_id=org_id, id=w.node_id)
            row.name, row.lat, row.lng = w.name, w.lat, w.lng
            row.units_in_stock, row.vehicles_available = w.units_in_stock, w.vehicles_available
            row.vehicle_capacity = w.vehicle_capacity
            s.add(row)
        return w

    def delete(self, org_id: str, wid: str) -> bool:
        with self._session() as s, s.begin():
            row = s.get(WarehouseRow, (org_id, wid))
            if not row:
                return False
            s.delete(row)
            return True

    def import_csv(self, org_id: str, text: str) -> int:
        """All-or-nothing CSV import. Columns: id, name, lat, lng, units_in_stock, vehicles_available[, vehicle_capacity]."""
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        missing = {"id", "name", "lat", "lng", "units_in_stock", "vehicles_available"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        rows: List[StorageNode] = []
        for n, r in enumerate(reader, start=2):
            try:
                rows.append(StorageNode(
                    node_id=r["id"].strip(), name=r["name"].strip(), lat=float(r["lat"]), lng=float(r["lng"]),
                    units_in_stock=int(r["units_in_stock"]), vehicles_available=int(r["vehicles_available"]),
                    vehicle_capacity=int(r.get("vehicle_capacity") or 200)))
            except (ValueError, KeyError, AttributeError) as exc:
                raise ValueError(f"line {n}: {exc}") from exc
            validate(rows[-1], f"line {n}")
        if len(rows) > 5000:
            raise ValueError("At most 5,000 warehouses per import")
        for w in rows:
            self.upsert(org_id, w)
        return len(rows)

    def delete_all(self, org_id: str) -> int:
        with self._session() as s, s.begin():
            return s.execute(delete(WarehouseRow).where(WarehouseRow.org_id == org_id)).rowcount or 0


def validate(w: StorageNode, where: str = "warehouse") -> None:
    if not w.node_id or not w.name:
        raise ValueError(f"{where}: id and name are required")
    if len(w.node_id) > 64 or len(w.name) > 200:
        raise ValueError(f"{where}: id is limited to 64 characters and name to 200")
    if not (-90 <= w.lat <= 90 and -180 <= w.lng <= 180):
        raise ValueError(f"{where}: lat/lng out of range")
    if w.units_in_stock < 0 or w.vehicles_available < 0 or w.vehicle_capacity <= 0:
        raise ValueError(f"{where}: stock/vehicles must be >= 0 and vehicle_capacity > 0")
