"""
Load warehouse inventory from a CSV you maintain.

    python -m gis_backend.seed_warehouses warehouses.csv

Columns: id, name, lat, lng, units_in_stock, vehicles_available[, vehicle_capacity]
The import is all-or-nothing and upserts by id.
"""
import sys

from .config import get_settings
from .store import WarehouseStore


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    store = WarehouseStore(get_settings().database_url)
    with open(sys.argv[1], encoding="utf-8") as fh:
        print(f"Imported {store.import_csv(fh.read())} warehouse(s).")


if __name__ == "__main__":
    main()
