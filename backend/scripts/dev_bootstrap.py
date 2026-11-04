"""
Prepare a development database: one organisation, invitations for the test-double users, and demo warehouses.
Development and the browser test only. Real organisations are created when their first owner signs in
(MARGA_BOOTSTRAP_OWNER_EMAIL) and invite people from the Team page.

    DATABASE_URL=sqlite:///data/dev.db python scripts/dev_bootstrap.py
"""
import datetime as dt
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gis_backend.config import get_settings  # noqa: E402
from gis_backend.db import Invite, Organization, make_session_factory, utcnow  # noqa: E402
from gis_backend.store import WarehouseStore  # noqa: E402

sf, _ = make_session_factory(get_settings().database_url)
org_id = str(uuid.uuid4())
with sf() as s, s.begin():
    s.add(Organization(id=org_id, name="Acme Demo"))
    s.flush()   # the organisation must exist before rows that reference it
    for email, role in (("alice@acme.test", "owner"), ("bob@acme.test", "member")):
        s.add(Invite(id=str(uuid.uuid4()), org_id=org_id, email=email, role=role,
                     expires_at=utcnow() + dt.timedelta(days=14)))
csv = Path(__file__).resolve().parents[1] / "examples" / "warehouses.demo.csv"
print(f"Organisation 'Acme Demo' ready; imported {WarehouseStore(sf).import_csv(org_id, csv.read_text())} demo warehouses.")
