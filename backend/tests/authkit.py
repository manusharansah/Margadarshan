"""Test harness: an app with a real database, real session handling and real OIDC verification (against a fake provider)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi.testclient import TestClient

from gis_backend.accounts import Accounts
from gis_backend.api import create_app
from gis_backend.config import Settings
from gis_backend.db import Membership, Organization, User, make_session_factory
from gis_backend.devtools.fake_oidc import FakeProvider, FakeSession
from gis_backend.oidc import OidcClient
from gis_backend.store import WarehouseStore

from .helpers import grid_network

COOKIE = "marga_session"
CSRF = {"X-Requested-With": "marga"}


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path}/t.db", weather_enabled=False, cookie_secure=False,
                public_url="http://localhost:3000", cors_origins=["http://localhost:3000"],
                oidc_client_id="test-client", oidc_client_secret="test-secret", oidc_issuer="http://localhost:9200")
    base.update(kw)
    return Settings(**base)


class Harness:
    """Owns the app, the provider double, and helpers to create people directly in the database."""

    def __init__(self, tmp_path, ai=None, network=None, **settings):
        self.cfg = make_settings(tmp_path, **settings)
        self.sf, _ = make_session_factory(self.cfg.database_url)
        self.provider = FakeProvider()
        self.oidc = OidcClient(self.cfg.oidc_issuer, "test-client", "test-secret", self.cfg.oidc_redirect_uri,
                               http=FakeSession(self.provider))
        self.app = create_app(self.cfg, network=network or grid_network(4), session_factory=self.sf, client=ai, oidc=self.oidc)
        self.client_cm = TestClient(self.app, follow_redirects=False)
        self.accounts = Accounts(self.sf, self.cfg)         # same database as the app's own instance

    def __enter__(self):
        self.client_cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self.client_cm.__exit__(*exc)

    # -- direct provisioning (bypasses sign-in; sign-in itself is tested separately)
    def person(self, email: str, role: str = "member", org_id: Optional[str] = None, org_name: str = "Acme"):
        with self.sf() as s, s.begin():
            if org_id is None:
                org = Organization(id=str(uuid.uuid4()), name=org_name)
                s.add(org)
                org_id = org.id
            u = User(id=str(uuid.uuid4()), issuer="http://localhost:9200", subject=f"s-{email}", email=email, name=email.split("@")[0])
            s.add(u)
            s.flush()
            s.add(Membership(user_id=u.id, org_id=org_id, role=role))
        return u.id, org_id, self.accounts.create_session(u.id)

    def session_for(self, token: str) -> TestClient:
        c = TestClient(self.app, follow_redirects=False)
        c.__enter__()
        c.cookies.set(COOKIE, token)
        c.headers.update(CSRF)
        return c

    def as_role(self, role: str, email: Optional[str] = None, org_id: Optional[str] = None, org_name: str = "Acme"):
        email = email or f"{role}-{uuid.uuid4().hex[:6]}@acme.test"
        uid, org, token = self.person(email, role, org_id, org_name)
        c = self.session_for(token)
        c.user_id, c.org_id, c.email = uid, org, email
        return c

    def info(self, token: str):
        return self.accounts.get_session(token)
