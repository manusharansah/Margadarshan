"""
Warehouse persistence (SQLAlchemy; SQLite locally, PostgreSQL on AWS RDS).

Warehouse locations, stock and fleet size are the operator's own business data -
there is no public source for them - so they live in a database that the
operator maintains through the API or ``python -m gis_backend.seed_warehouses``.
Nothing is generated or pre-filled.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List, Optional

from sqlalchemy import Float, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .models import StorageNode


class Base(DeclarativeBase):
    pass


class WarehouseRow(Base):
    __tablename__ = "warehouses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    units_in_stock: Mapped[int] = mapped_column(Integer)
    vehicles_available: Mapped[int] = mapped_column(Integer)
    vehicle_capacity: Mapped[int] = mapped_column(Integer, default=200)

    def to_node(self) -> StorageNode:
        return StorageNode(node_id=self.id, name=self.name, lat=self.lat, lng=self.lng,
                           units_in_stock=self.units_in_stock, vehicles_available=self.vehicles_available,
                           vehicle_capacity=self.vehicle_capacity)


CSV_COLUMNS = ["id", "name", "lat", "lng", "units_in_stock", "vehicles_available", "vehicle_capacity"]


class WarehouseStore:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def list(self) -> List[StorageNode]:
        with self._session() as s:
            return [r.to_node() for r in s.scalars(select(WarehouseRow).order_by(WarehouseRow.id))]

    def get(self, wid: str) -> Optional[StorageNode]:
        with self._session() as s:
            row = s.get(WarehouseRow, wid)
            return row.to_node() if row else None

    def upsert(self, w: StorageNode) -> StorageNode:
        with self._session() as s, s.begin():
            row = s.get(WarehouseRow, w.node_id) or WarehouseRow(id=w.node_id)
            row.name, row.lat, row.lng = w.name, w.lat, w.lng
            row.units_in_stock, row.vehicles_available = w.units_in_stock, w.vehicles_available
            row.vehicle_capacity = w.vehicle_capacity
            s.add(row)
        return w

    def delete(self, wid: str) -> bool:
        with self._session() as s, s.begin():
            row = s.get(WarehouseRow, wid)
            if not row:
                return False
            s.delete(row)
            return True

    def import_csv(self, text: str) -> int:
        """All-or-nothing CSV import (header: %s)."""
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
        for w in rows:
            self.upsert(w)
        return len(rows)


import_csv_doc = ", ".join(CSV_COLUMNS)
WarehouseStore.import_csv.__doc__ = WarehouseStore.import_csv.__doc__ % import_csv_doc  # type: ignore[operator]


def validate(w: StorageNode, where: str = "warehouse") -> None:
    if not w.node_id or not w.name:
        raise ValueError(f"{where}: id and name are required")
    if not (-90 <= w.lat <= 90 and -180 <= w.lng <= 180):
        raise ValueError(f"{where}: lat/lng out of range")
    if w.units_in_stock < 0 or w.vehicles_available < 0 or w.vehicle_capacity <= 0:
        raise ValueError(f"{where}: stock/vehicles must be >= 0 and vehicle_capacity > 0")
