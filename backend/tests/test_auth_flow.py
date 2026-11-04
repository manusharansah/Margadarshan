"""Sign-in end to end: the real OidcClient verification code against the fake provider, including tokens it must refuse."""
from urllib.parse import parse_qs, urlparse

import pytest

from gis_backend.devtools.fake_oidc import FakeProvider, USERS

from .authkit import COOKIE, Harness


def sign_in(h, email, next_="/", tamper=None, mutate_params=None):
    """Drive /auth/login -> provider -> /auth/callback. Returns (client, callback_response)."""
    c = h.client_cm
    r = c.get("/auth/login", params={"next": next_})
    assert r.status_code == 302, r.text
    params = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    if mutate_params:
        mutate_params(params)
    h.provider.tamper = tamper
    redirect = h.provider.authorize(params, email)
    q = urlparse(redirect).query
    return params, c.get(f"/auth/callback?{q}")


@pytest.fixture()
def h(tmp_path):
    with Harness(tmp_path, bootstrap_owner_email="alice@acme.test") as hh:
        yield hh


def test_login_redirects_to_the_provider_with_state_nonce_and_pkce(h):
    r = h.client_cm.get("/auth/login")
    assert r.status_code == 302
    u = urlparse(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert u.netloc == "localhost:9200" and u.path == "/authorize"
    assert q["response_type"] == "code" and q["client_id"] == "test-client" and q["code_challenge_method"] == "S256"
    assert q["scope"] == "openid email profile" and q["redirect_uri"] == "http://localhost:3000/api/auth/callback"
    assert len(q["state"]) >= 40 and len(q["nonce"]) >= 40 and len(q["code_challenge"]) >= 40
    assert "client_secret" not in q and "code_verifier" not in q          # secrets never travel via the browser


def test_successful_sign_in_sets_a_hardened_cookie_and_the_session_works(h):
    _, cb = sign_in(h, "alice@acme.test", "/inventory")
    assert cb.status_code == 302 and cb.headers["location"] == "/inventory"
    cookie = cb.headers["set-cookie"]
    assert f"{COOKIE}=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Max-Age=43200" in cookie and "Domain" not in cookie
    me = h.client_cm.get("/auth/me").json()
    assert me["user"]["email"] == "alice@acme.test" and me["role"] == "owner" and me["organisation"]["name"] == "Acme team"


def test_production_cookie_uses_the_host_prefix_and_secure_flag(tmp_path):
    with Harness(tmp_path, bootstrap_owner_email="alice@acme.test", cookie_secure=True) as h:
        _, cb = sign_in(h, "alice@acme.test")
        cookie = cb.headers["set-cookie"]
        assert cookie.startswith("__Host-marga_session=") and "Secure" in cookie and "HttpOnly" in cookie


def test_open_redirects_are_ignored(h):
    _, cb = sign_in(h, "alice@acme.test", "//evil.test/phish")
    assert cb.headers["location"] == "/"


@pytest.mark.parametrize("tamper", ["aud", "expired", "nonce", "iss", "forged", "none", "hs256"])
def test_forged_or_mismatched_identity_tokens_are_refused(h, tamper):
    _, cb = sign_in(h, "alice@acme.test", tamper=tamper)
    assert cb.status_code == 302 and cb.headers["location"] == "/login?error=failed"
    assert "set-cookie" not in cb.headers
    assert h.client_cm.get("/auth/me").status_code == 401
    from sqlalchemy import func, select
    from gis_backend.db import User
    with h.sf() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 0         # no account was created from a bad token


def test_unverified_email_cannot_sign_in(h):
    _, cb = sign_in(h, "mallory@evil.test")
    assert cb.headers["location"] == "/login?error=failed" and "set-cookie" not in cb.headers


def test_state_is_single_use_and_unknown_state_is_refused(h):
    params, cb = sign_in(h, "alice@acme.test")
    assert cb.status_code == 302 and "set-cookie" in cb.headers
    replay = h.client_cm.get(f"/auth/callback?code=whatever&state={params['state']}")
    assert replay.headers["location"] == "/login?error=expired"
    assert h.client_cm.get("/auth/callback?code=x&state=forged").headers["location"] == "/login?error=expired"
    assert h.client_cm.get("/auth/callback").headers["location"] == "/login?error=expired"


def test_authorization_code_cannot_be_used_without_the_pkce_verifier(h):
    def swap_challenge(p):
        p["code_challenge"] = "A" * 43                                   # an attacker's own challenge
    _, cb = sign_in(h, "alice@acme.test", mutate_params=swap_challenge)
    assert cb.headers["location"] == "/login?error=failed" and "set-cookie" not in cb.headers


def test_provider_error_and_cancel(h):
    r = h.client_cm.get("/auth/callback?error=access_denied&state=x")
    assert r.headers["location"] == "/login?error=cancelled"


def test_uninvited_person_is_refused_after_a_valid_sign_in(h):
    sign_in(h, "alice@acme.test")                                         # creates the organisation
    h.client_cm.cookies.clear()
    _, cb = sign_in(h, "carol@other.test")
    assert cb.headers["location"] == "/login?error=not_invited" and "set-cookie" not in cb.headers
    assert h.client_cm.get("/auth/me").status_code == 401


def test_invited_person_joins_the_inviting_organisation(h):
    _, cb = sign_in(h, "alice@acme.test")
    alice = h.session_for(cb.cookies.get(COOKIE) or h.client_cm.cookies.get(COOKIE))
    assert alice.post("/team/invites", json={"email": "bob@acme.test", "role": "member"}).status_code == 200
    h.client_cm.cookies.clear()
    _, cb2 = sign_in(h, "bob@acme.test")
    assert cb2.status_code == 302 and h.client_cm.get("/auth/me").json()["role"] == "member"


def test_signing_in_twice_does_not_duplicate_the_account(h):
    sign_in(h, "alice@acme.test")
    h.client_cm.cookies.clear()
    sign_in(h, "alice@acme.test")
    from sqlalchemy import func, select
    from gis_backend.db import Organization, User
    with h.sf() as s:
        assert s.scalar(select(func.count()).select_from(User)) == 1 and s.scalar(select(func.count()).select_from(Organization)) == 1


def test_logout_ends_the_session_and_needs_the_csrf_header(h):
    sign_in(h, "alice@acme.test")
    assert h.client_cm.post("/auth/logout").status_code == 403           # cross-site form post cannot log you out
    r = h.client_cm.post("/auth/logout", headers={"X-Requested-With": "marga"})
    assert r.status_code == 200 and "Max-Age=0" in r.headers["set-cookie"]
    stolen = h.client_cm.cookies.get(COOKIE)
    h.client_cm.cookies.clear()
    h.client_cm.cookies.set(COOKIE, stolen) if stolen else None
    assert h.client_cm.get("/auth/me").status_code == 401


def test_provider_outage_sends_the_user_to_a_friendly_error_without_details(tmp_path):
    import requests
    with Harness(tmp_path) as h:
        def down(*a, **k):
            raise requests.ConnectionError("https://secret-internal-host/oops")
        h.oidc._http.get = down
        h.oidc._meta = None
        r = h.client_cm.get("/auth/login")
        assert r.status_code == 302 and r.headers["location"] == "/login?error=provider"
        assert "secret-internal-host" not in r.text


def test_sign_in_not_configured_is_a_clear_503(tmp_path):
    from gis_backend.api import create_app
    from gis_backend.db import make_session_factory
    from .authkit import make_settings
    from .helpers import grid_network
    from fastapi.testclient import TestClient
    cfg = make_settings(tmp_path, oidc_client_id="")
    app = create_app(cfg, network=grid_network(3), session_factory=make_session_factory(cfg.database_url)[0])
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/auth/login").status_code == 503 and c.get("/health").json()["sign_in_configured"] is False


def test_login_endpoint_is_rate_limited_per_client(h):
    codes = [h.client_cm.get("/auth/login").status_code for _ in range(25)]
    assert codes[:20] == [302] * 20 and 429 in codes[20:]


def test_issuer_must_be_https_outside_localhost():
    from gis_backend.oidc import OidcClient
    with pytest.raises(ValueError):
        OidcClient("http://accounts.example.com", "c", "s", "https://app/cb")
    OidcClient("https://accounts.example.com", "c", "s", "https://app/cb")
    OidcClient("http://localhost:9200", "c", "s", "http://localhost:3000/cb")


def test_rate_limit_cannot_be_dodged_by_spoofing_forwarded_for(tmp_path):
    with Harness(tmp_path, trust_proxy=True) as h:
        # the load balancer appends the real address last; the client controls everything before it
        codes = [h.client_cm.get("/auth/login", headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"}).status_code
                 for i in range(25)]
        assert codes[:20] == [302] * 20 and 429 in codes[20:]
