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
    admin_api_key: str = field(default_factory=lambda: os.environ.get("MARGA_ADMIN_API_KEY", ""))
    weather_enabled: bool = field(default_factory=lambda: _bool("MARGA_WEATHER_ENABLED", True))
    weather_ttl_seconds: int = field(default_factory=lambda: int(os.environ.get("MARGA_WEATHER_TTL", "1800")))
    require_gemma: bool = field(default_factory=lambda: _bool("MARGA_REQUIRE_GEMMA", False))
    loading_hours_per_stop: float = field(default_factory=lambda: float(os.environ.get("MARGA_LOADING_HOURS", "0")))

    @property
    def network_path(self) -> Path:
        return self.data_dir / self.network_file

    @property
    def dem_dir(self) -> Path:
        return self.data_dir / "dem"


def get_settings() -> Settings:
    return Settings()
