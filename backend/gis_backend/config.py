"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MARGA_DATA_DIR", "data")))
    network_file: str = field(default_factory=lambda: os.environ.get("MARGA_NETWORK_FILE", "network.json.gz"))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///data/marga.db"))
    cors_origins: List[str] = field(
        default_factory=lambda: [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    )
    weather_enabled: bool = field(default_factory=lambda: _bool("MARGA_WEATHER_ENABLED", True))
    weather_ttl_seconds: int = field(default_factory=lambda: int(os.environ.get("MARGA_WEATHER_TTL", "1800")))
    require_gemma: bool = field(default_factory=lambda: _bool("MARGA_REQUIRE_GEMMA", False))
    # --- sign-in (OpenID Connect) -------------------------------------------------------------
    oidc_issuer: str = field(default_factory=lambda: os.environ.get("OIDC_ISSUER", "https://accounts.google.com"))
    oidc_client_id: str = field(default_factory=lambda: os.environ.get("OIDC_CLIENT_ID", ""))
    oidc_client_secret: str = field(default_factory=lambda: os.environ.get("OIDC_CLIENT_SECRET", ""))
    public_url: str = field(default_factory=lambda: os.environ.get("MARGA_PUBLIC_URL", "http://localhost:3000").rstrip("/"))
    cookie_secure: bool = field(default_factory=lambda: _bool("MARGA_COOKIE_SECURE", True))
    session_idle_minutes: int = field(default_factory=lambda: int(os.environ.get("MARGA_SESSION_IDLE_MINUTES", "120")))
    session_max_hours: int = field(default_factory=lambda: int(os.environ.get("MARGA_SESSION_MAX_HOURS", "12")))
    bootstrap_owner_email: str = field(default_factory=lambda: os.environ.get("MARGA_BOOTSTRAP_OWNER_EMAIL", "").strip().lower())
    allow_self_signup: bool = field(default_factory=lambda: _bool("MARGA_ALLOW_SELF_SIGNUP", False))
    allowed_email_domains: List[str] = field(
        default_factory=lambda: [d.strip().lower() for d in os.environ.get("MARGA_ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()])
    order_retention_days: int = field(default_factory=lambda: int(os.environ.get("MARGA_ORDER_RETENTION_DAYS", "365")))
    audit_retention_days: int = field(default_factory=lambda: int(os.environ.get("MARGA_AUDIT_RETENTION_DAYS", "730")))
    trust_proxy: bool = field(default_factory=lambda: _bool("MARGA_TRUST_PROXY", False))
    loading_hours_per_stop: float = field(default_factory=lambda: float(os.environ.get("MARGA_LOADING_HOURS", "0")))

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_url}/api/auth/callback"

    @property
    def network_path(self) -> Path:
        return self.data_dir / self.network_file

    @property
    def dem_dir(self) -> Path:
        return self.data_dir / "dem"


def get_settings() -> Settings:
    return Settings()
