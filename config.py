"""Zentrale Konfiguration fuer CineMatch.

Alle Werte lassen sich ueber Umgebungsvariablen oder eine `.env`-Datei setzen
(pydantic-settings, V2). Die Namen entsprechen den Feldnamen in Grossbuchstaben,
z. B. ``TMDB_API_KEY`` oder ``ADMIN_PASSWORD``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

#: Streaming-Anbieter inklusive der TMDB-Provider-IDs fuer die Region DE.
#: Reihenfolge = Anzeigereihenfolge in Onboarding und Admin-Dashboard.
PROVIDERS: tuple[dict[str, Any], ...] = (
    {"slug": "netflix", "name": "Netflix", "tmdb_id": 8, "accent": "#e50914"},
    {"slug": "prime", "name": "Amazon Prime Video", "tmdb_id": 119, "accent": "#00a8e1"},
    {"slug": "disney", "name": "Disney+", "tmdb_id": 337, "accent": "#113ccf"},
    {"slug": "appletv", "name": "Apple TV+", "tmdb_id": 350, "accent": "#c7c7cc"},
    {"slug": "paramount", "name": "Paramount+", "tmdb_id": 531, "accent": "#0064ff"},
    {"slug": "crunchyroll", "name": "Crunchyroll", "tmdb_id": 283, "accent": "#f47521"},
)

PROVIDER_BY_SLUG: dict[str, dict[str, Any]] = {p["slug"]: p for p in PROVIDERS}
PROVIDER_BY_TMDB_ID: dict[int, dict[str, Any]] = {p["tmdb_id"]: p for p in PROVIDERS}
PROVIDER_SLUGS: tuple[str, ...] = tuple(PROVIDER_BY_SLUG)


def provider_public_list() -> list[dict[str, Any]]:
    """Serialisierbare Anbieterliste fuer API und Frontend."""
    return [dict(provider) for provider in PROVIDERS]


class Settings(BaseSettings):
    """Laufzeitkonfiguration. Leere Zugangsdaten deaktivieren das jeweilige Feature."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Listen aus ENV kommen als Komma-String an (siehe Validatoren unten)
        # statt vorab als JSON interpretiert zu werden.
        enable_decoding=False,
    )

    app_name: str = "CineMatch"
    app_version: str = "1.0.0"
    debug: bool = False

    # Dateisystem
    data_dir: Path = BASE_DIR / "data"
    static_dir: Path = BASE_DIR / "static"

    # Admin (HTTP Basic Auth). Ohne Passwort bleibt /admin komplett gesperrt.
    admin_user: str = "admin"
    admin_password: str = ""

    # TMDB (v3 API-Key oder v4 Read Access Token, beides wird unterstuetzt)
    tmdb_api_key: str = ""
    tmdb_read_access_token: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p"
    tmdb_region: str = "DE"
    tmdb_language: str = "de-DE"
    tmdb_timeout_seconds: float = 12.0
    tmdb_cache_ttl_seconds: int = 900

    # Reverse-Proxy / IP-Erkennung
    trust_forwarded_for: bool = True

    # CORS (fuer Produktionsumgebung mit HTTPS/Reverse Proxy)
    cors_origins: list[str] = ["*"]

    # Haushalt / Quiz
    household_default_providers: list[str] = ["netflix", "prime"]
    max_avatar_bytes: int = 3 * 1024 * 1024
    quiz_max_answers: int = 20
    quiz_benchmark_movies: list[int] = [27205, 120467, 129, 419430, 313369]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if value is None:
            return ["*"]
        return value

    @field_validator("household_default_providers", mode="before")
    @classmethod
    def _parse_provider_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if value is None:
            return []
        return value

    @field_validator("household_default_providers")
    @classmethod
    def _validate_providers(cls, value: list[str]) -> list[str]:
        unknown = [slug for slug in value if slug not in PROVIDER_BY_SLUG]
        if unknown:
            raise ValueError(
                f"Unbekannte Streaming-Anbieter: {', '.join(unknown)}. "
                f"Erlaubt: {', '.join(PROVIDER_SLUGS)}"
            )
        return list(dict.fromkeys(value))

    @field_validator("quiz_benchmark_movies", mode="before")
    @classmethod
    def _parse_benchmarks(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if value is None:
            return []
        return value

    @field_validator("quiz_benchmark_movies")
    @classmethod
    def _validate_benchmarks(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("QUIZ_BENCHMARK_MOVIES darf nicht leer sein.")
        return list(dict.fromkeys(int(item) for item in value))

    @field_validator("tmdb_region")
    @classmethod
    def _upper_region(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 2:
            raise ValueError("TMDB_REGION muss ein zweistelliger ISO-Laendercode sein (z. B. DE).")
        return value

    @field_validator("tmdb_timeout_seconds")
    @classmethod
    def _positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Timeouts muessen groesser als 0 sein.")
        return value

    @property
    def database_file(self) -> Path:
        return self.data_dir / "cinematch.db"

    @property
    def avatar_dir(self) -> Path:
        return self.static_dir / "avatars"

    @property
    def avatar_upload_dir(self) -> Path:
        return self.avatar_dir / "uploads"

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_api_key or self.tmdb_read_access_token)

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_password)

    def ensure_directories(self) -> None:
        """Legt Daten- und Avatarverzeichnisse an (idempotent)."""
        for path in (self.data_dir, self.avatar_dir, self.avatar_upload_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Gecachte Standardkonfiguration fuer den Modul-Import."""
    return Settings()
