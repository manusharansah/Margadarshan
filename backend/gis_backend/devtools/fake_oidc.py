"""
TEST DOUBLE of an OpenID Connect provider. Never use it in production.

It implements just enough of the protocol (discovery, JWKS, authorize with PKCE, token) and signs real RS256 ID
tokens, so the real ``OidcClient`` verification code runs against it in tests and in the browser smoke test.
It can also mint deliberately broken tokens (wrong audience, expired, unsigned, forged key) to prove they are rejected.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Dict, Optional
from urllib.parse import urlencode

import requests
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

USERS = {
    "alice@acme.test": {"sub": "u-alice", "name": "Alice Owner", "email_verified": True},
    "bob@acme.test": {"sub": "u-bob", "name": "Bob Member", "email_verified": True},
    "carol@other.test": {"sub": "u-carol", "name": "Carol Other", "email_verified": True},
    "mallory@evil.test": {"sub": "u-mallory", "name": "Mallory Unverified", "email_verified": False},
}


class FakeProvider:
    def __init__(self, issuer: str = "http://localhost:9200", client_id: str = "test-client", client_secret: str = "test-secret"):
        self.issuer, self.client_id, self.client_secret = issuer, client_id, client_secret
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        self.other_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)  # same kid, wrong key
        self.codes: Dict[str, dict] = {}
        self.tamper: Optional[str] = None        # "aud" | "expired" | "nonce" | "iss" | "forged" | "none" | "hs256"

    # --- protocol
    def metadata(self) -> dict:
        return {"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token", "jwks_uri": f"{self.issuer}/jwks"}

    def jwks(self) -> dict:
        return KeySet([self.key]).as_dict(private=False)

    def authorize(self, params: dict, email: str) -> str:
        assert params["client_id"] == self.client_id and params["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"email": email, "nonce": params["nonce"], "challenge": params["code_challenge"],
                            "redirect_uri": params["redirect_uri"]}
        return f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params['state']})}"

    def token(self, form: dict) -> tuple[int, dict]:
        entry = self.codes.pop(form.get("code", ""), None)          # codes are single use
        if entry is None or form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return 400, {"error": "invalid_grant"}
        verifier = form.get("code_verifier", "")
        if base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() != entry["challenge"]:
            return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
        if form.get("redirect_uri") != entry["redirect_uri"]:
            return 400, {"error": "invalid_grant"}
        return 200, {"access_token": "not-used", "token_type": "Bearer", "id_token": self.id_token(entry["email"], entry["nonce"])}

    def id_token(self, email: str, nonce: str) -> str:
        u = USERS.get(email, {"sub": f"u-{email}", "name": email, "email_verified": True})
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": self.client_id, "sub": u["sub"], "email": email,
                  "email_verified": u["email_verified"], "name": u["name"], "nonce": nonce, "iat": now, "exp": now + 300}
        key, header = self.key, {"alg": "RS256", "kid": "k1"}
        if self.tamper == "aud":
            claims["aud"] = "someone-else"
        elif self.tamper == "expired":
            claims["exp"] = now - 3600
        elif self.tamper == "nonce":
            claims["nonce"] = "wrong"
        elif self.tamper == "iss":
            claims["iss"] = "https://evil.example"
        elif self.tamper == "forged":
            key = self.other_key
        elif self.tamper == "none":
            return base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode() + "." + \
                base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + "."
        elif self.tamper == "hs256":      # algorithm confusion: sign with the public key as an HMAC secret
            import hmac
            h = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "kid": "k1"}).encode()).rstrip(b"=")
            b = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
            sig = hmac.new(json.dumps(self.jwks()).encode(), h + b"." + b, hashlib.sha256).digest()
            return (h + b"." + b + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode()
        return jwt.encode(header, claims, key)


class _Resp(requests.Response):
    def __init__(self, status: int, body: dict):
        super().__init__()
        self.status_code = status
        self._content = json.dumps(body).encode()
        self.headers["content-type"] = "application/json"


class FakeSession:
    """Drop-in for ``requests.Session`` that talks to a FakeProvider in-process."""

    def __init__(self, provider: FakeProvider):
        self.p = provider
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url.endswith("/.well-known/openid-configuration"):
            return _Resp(200, self.p.metadata())
        if url.endswith("/jwks"):
            return _Resp(200, self.p.jwks())
        return _Resp(404, {})

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/token"):
            status, body = self.p.token(data or {})
            return _Resp(status, body)
        return _Resp(404, {})
