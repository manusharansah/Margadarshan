"""
OpenID Connect sign-in (authorization-code flow with PKCE). Works with Google, AWS Cognito, Microsoft Entra, Auth0 and
any other standards-compliant provider, selected by ``OIDC_ISSUER``.

What is checked, and why:
* state       random, single use, bound to the attempt          -> blocks login CSRF
* PKCE S256   code_verifier held server-side                    -> blocks authorization-code interception
* nonce       inside the ID token must equal the attempt's      -> blocks token replay
* signature   RS256/ES256 only, keys from the provider's JWKS   -> rejects forged tokens, ``alg: none`` and HS256 confusion
* iss / aud   must equal the configured issuer and client id    -> rejects tokens minted for another app
* exp         enforced with 60 s leeway
* email       must be present and ``email_verified``            -> invitations match proven addresses only
Provider access tokens and refresh tokens are discarded immediately. We only need to know who signed in.
"""
from __future__ import annotations

import base64
import hashlib
import threading
import time
from typing import Dict, Optional
from urllib.parse import urlencode, urlparse

import requests
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

ALLOWED_ALGS = ["RS256", "ES256"]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OidcError(Exception):
    """Any failure to complete or verify a sign-in. The message is safe to show to the user."""


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class OidcClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, redirect_uri: str,
                 http: Optional[requests.Session] = None, timeout: float = 10.0):
        host = urlparse(issuer).hostname or ""
        if not issuer.startswith("https://") and host not in LOCAL_HOSTS:
            raise ValueError("OIDC_ISSUER must use https (plain http is allowed only for localhost development)")
        self.issuer, self.client_id, self.client_secret = issuer.rstrip("/"), client_id, client_secret
        self.redirect_uri, self.timeout = redirect_uri, timeout
        self._http = http or requests.Session()
        self._meta: Optional[dict] = None
        self._jwks: Optional[dict] = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    # ------------------------------------------------------------- discovery
    def metadata(self) -> dict:
        with self._lock:
            if self._meta is None:
                try:
                    r = self._http.get(f"{self.issuer}/.well-known/openid-configuration", timeout=self.timeout)
                    r.raise_for_status()
                    meta = r.json()
                except (requests.RequestException, ValueError) as exc:
                    raise OidcError("The sign-in provider is unreachable. Try again shortly.") from exc
                if meta.get("issuer", "").rstrip("/") != self.issuer:
                    raise OidcError("The sign-in provider's metadata does not match the configured issuer.")
                for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if k not in meta:
                        raise OidcError(f"The sign-in provider's metadata is missing {k}.")
                self._meta = meta
            return self._meta

    def _keys(self, refresh: bool = False):
        with self._lock:
            if self._jwks is None or refresh:
                try:
                    r = self._http.get(self.metadata_unlocked()["jwks_uri"], timeout=self.timeout)
                    r.raise_for_status()
                    self._jwks = r.json()
                except (requests.RequestException, ValueError) as exc:
                    raise OidcError("Could not fetch the provider's signing keys.") from exc
            return KeySet.import_key_set(self._jwks)

    def metadata_unlocked(self) -> dict:
        if self._meta is None:       # called with the lock held by _keys; metadata() would deadlock
            self._lock.release()
            try:
                self.metadata()
            finally:
                self._lock.acquire()
        return self._meta  # type: ignore[return-value]

    # ------------------------------------------------------------------ flow
    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        q = {"response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
             "scope": "openid email profile", "state": state, "nonce": nonce,
             "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256"}
        return f"{self.metadata()['authorization_endpoint']}?{urlencode(q)}"

    def exchange_code(self, code: str, verifier: str) -> str:
        """Trade the authorization code for an ID token. Other tokens in the response are ignored."""
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                "client_id": self.client_id, "code_verifier": verifier}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        try:
            r = self._http.post(self.metadata()["token_endpoint"], data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise OidcError("The sign-in provider is unreachable. Try again shortly.") from exc
        if r.status_code != 200:
            raise OidcError("The sign-in provider rejected the sign-in code.")
        try:
            return r.json()["id_token"]
        except (KeyError, ValueError) as exc:
            raise OidcError("The sign-in provider did not return an identity token.") from exc

    def verify_id_token(self, id_token: str, nonce: str, now: Optional[float] = None) -> Dict[str, object]:
        registry = jwt.JWTClaimsRegistry(
            now=int(now) if now is not None else None, leeway=60,
            iss={"essential": True, "value": self.issuer}, aud={"essential": True}, sub={"essential": True},
            exp={"essential": True}, nonce={"essential": True, "value": nonce})
        try:
            try:
                token = jwt.decode(id_token, self._keys(), algorithms=ALLOWED_ALGS)
            except (JoseError, ValueError):
                token = jwt.decode(id_token, self._keys(refresh=True), algorithms=ALLOWED_ALGS)  # keys may have rotated
            claims = dict(token.claims)
            registry.validate(claims)
            aud = claims.get("aud")
            auds = aud if isinstance(aud, list) else [aud]
            if self.client_id not in auds or (len(auds) > 1 and claims.get("azp") != self.client_id):
                raise OidcError("The identity token was issued for a different application.")
        except OidcError:
            raise
        except (JoseError, ValueError, KeyError, TypeError) as exc:
            raise OidcError("The identity token could not be verified.") from exc
        if claims.get("email_verified") not in (True, "true"):
            raise OidcError("Your email address is not verified with the sign-in provider.")
        if not claims.get("email"):
            raise OidcError("The sign-in provider did not share an email address.")
        return claims
