"""
Load warehouse inventory from a CSV you maintain into one organisation.

    python -m gis_backend.seed_warehouses warehouses.csv --org "Acme Ltd"

Columns: id, name, lat, lng, units_in_stock, vehicles_available[, vehicle_capacity]
The import is all-or-nothing and upserts by id. The organisation must already exist (it is created when its first owner
signs in); the name is matched exactly. Use the web app's Inventory page for day-to-day changes.
"""
import argparse
import sys

from sqlalchemy import select

from .config import get_settings
from .db import Organization, make_session_factory
from .store import WarehouseStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--org", required=True, help="exact organisation name")
    args = ap.parse_args()
    sf, _ = make_session_factory(get_settings().database_url)
    with sf() as s:
        orgs = s.scalars(select(Organization).where(Organization.name == args.org)).all()
    if len(orgs) != 1:
        sys.exit(f"Found {len(orgs)} organisations named {args.org!r}; it must match exactly one. "
                 "The organisation is created when its first owner signs in.")
    with open(args.csv, encoding="utf-8") as fh:
        print(f"Imported {WarehouseStore(sf).import_csv(orgs[0].id, fh.read())} warehouse(s) into {args.org!r}.")


if __name__ == "__main__":
    main()
