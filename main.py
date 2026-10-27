"""CineMatch - FastAPI-Service fuer Gruppen-Filmempfehlungen.

Start:  uvicorn main:app --host 0.0.0.0 --port 8000

Oeffentliche Routen
-------------------
* ``GET  /``                      -> Redirect auf /onboard
* ``GET  /onboard``               -> Mobile Swipe-Onboarding
* ``GET  /admin``                 -> Dashboard (HTTP Basic Auth)
* ``GET  /api/v1/health``         -> Konfigurationsstatus
* ``POST /api/v1/quiz/start``     -> Quiz-Session inkl. Benchmark-Karten
* ``POST /api/v1/quiz/swipe``     -> Swipe speichern, Genre-Vektor aktualisieren
* ``POST /api/v1/quiz/next``      -> naechste Karten adaptiv nachladen
* ``POST /api/v1/quiz/complete``  -> Profil anlegen + HA-Verknuepfung
* ``POST /api/v1/recommendations``-> Gruppenempfehlungen
* ``GET  /api/v1/presence``       -> anwesende, registrierte Personen (HA)
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from config import (
    PROVIDER_BY_SLUG,
    PROVIDER_SLUGS,
    Settings,
    get_settings,
    provider_public_list,
)
from ha_client import HAClient, HAError, extract_client_ip
from recommender import (
    MEDIA_TYPES,
    QuizError,
    QuizService,
    RecommendationEngine,
    RecommendationError,
    TMDBClient,
    TMDBError,
)
from store import Store, utc_now

logger = logging.getLogger("cinematch")

#: Vorlagen-Avatare (liegen als SVG unter static/avatars/).
PRESET_AVATARS: list[dict[str, str]] = [
    {"id": "crimson", "label": "Crimson", "url": "/static/avatars/preset-crimson.svg"},
    {"id": "indigo", "label": "Indigo", "url": "/static/avatars/preset-indigo.svg"},
    {"id": "teal", "label": "Teal", "url": "/static/avatars/preset-teal.svg"},
    {"id": "amber", "label": "Amber", "url": "/static/avatars/preset-amber.svg"},
    {"id": "violet", "label": "Violet", "url": "/static/avatars/preset-violet.svg"},
    {"id": "slate", "label": "Slate", "url": "/static/avatars/preset-slate.svg"},
]

basic_scheme = HTTPBasic(auto_error=False)


# --------------------------------------------------------------------- models


class QuizStartRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    providers: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Name darf nicht leer sein.")
        return value

    @field_validator("providers")
    @classmethod
    def _valid_providers(cls, value: list[str]) -> list[str]:
        unknown = [slug for slug in value if slug not in PROVIDER_SLUGS]
        if unknown:
            raise ValueError(f"Unbekannte Streaming-Anbieter: {', '.join(unknown)}")
        return list(dict.fromkeys(value))


class SwipeRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64)
    tmdb_id: int = Field(..., gt=0)
    media_type: Literal["movie", "tv"] = "movie"
    action: Literal["like", "dislike", "skip"]


class NextCardsRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64)
    limit: int = Field(default=3, ge=1, le=10)


class QuizCompleteRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64)


class RecommendationRequest(BaseModel):
    users: list[str] | None = None
    media_type: Literal["movie", "tv"] = "movie"
    limit: int = Field(default=10, ge=1, le=30)

    @field_validator("users")
    @classmethod
    def _clean_users(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [item.strip() for item in value if item and item.strip()]
        return cleaned or None


class HouseholdUpdateRequest(BaseModel):
    providers: list[str] = Field(default_factory=list)

    @field_validator("providers")
    @classmethod
    def _validate(cls, value: list[str]) -> list[str]:
        unknown = [slug for slug in value if slug not in PROVIDER_SLUGS]
        if unknown:
            raise ValueError(f"Unbekannte Streaming-Anbieter: {', '.join(unknown)}")
        return list(dict.fromkeys(value))


class PresetAvatarRequest(BaseModel):
    preset: str = Field(..., min_length=1, max_length=40)


class RecommendedTitle(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    year: int | None = None
    overview: str = ""
    poster_path: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    vote_average: float = 0
    vote_count: int = 0
    popularity: float = 0
    genres: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    match_percent: int = 0
    score: float = 0
    reason: str = ""


class PresentUser(BaseModel):
    id: str
    name: str
    avatar_url: str | None = None
    providers: list[str] = Field(default_factory=list)
    provider_names: list[str] = Field(default_factory=list)
    source: str = "manual"
    ha_entity_id: str | None = None
    tracker_state: str | None = None


class ProviderRef(BaseModel):
    slug: str
    name: str


class RecommendationResponse(BaseModel):
    media_type: str
    generated_at: str
    presence_source: str
    present_users: list[PresentUser]
    active_providers: list[ProviderRef]
    household_providers: list[ProviderRef]
    excluded_titles: int
    candidates_checked: int
    genre_filter: list[str] = Field(default_factory=list)
    results: list[RecommendedTitle]


# ------------------------------------------------------------------- helpers


def _admin_guard(settings: Settings) -> None:
    if not settings.admin_enabled:
        raise HTTPException(
            status_code=503,
            detail="Admin ist deaktiviert: ADMIN_PASSWORD ist nicht gesetzt.",
        )


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_scheme),
) -> None:
    """HTTP-Basic-Auth gegen ADMIN_USER/ADMIN_PASSWORD (timing-safe Vergleich)."""
    settings: Settings = request.app.state.settings
    _admin_guard(settings)
    unauthorized = HTTPException(
        status_code=401,
        detail="Admin-Login erforderlich.",
        headers={"WWW-Authenticate": 'Basic realm="CineMatch Admin", charset="UTF-8"'},
    )
    if credentials is None:
        raise unauthorized
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.admin_password.encode("utf-8")
    )
    if not (user_ok and password_ok):
        raise unauthorized


def _provider_names(slugs: list[str]) -> list[str]:
    return [
        PROVIDER_BY_SLUG[slug]["name"]
        for slug in slugs
        if slug in PROVIDER_BY_SLUG
    ]


def _public_avatar(user: dict[str, Any]) -> str | None:
    return user.get("avatar_url")


def _admin_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    taste = user.get("taste") or {}
    return {
        "id": user["id"],
        "name": user["name"],
        "ip": user.get("ip"),
        "avatar_url": _public_avatar(user),
        "providers": user.get("providers", []),
        "provider_names": _provider_names(user.get("providers", [])),
        "top_genres": taste.get("top", []),
        "counts": {
            "liked": len(user.get("liked") or []),
            "disliked": len(user.get("disliked") or []),
            "skipped": len(user.get("skipped") or []),
            "seen": len(user.get("seen") or []),
        },
        "swipes": sum(
            len(user.get(field) or [])
            for field in ("liked", "disliked", "skipped", "seen")
        ),
        "ha_entity_id": user.get("ha_entity_id"),
        "custom_user_id": user.get("custom_user_id"),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def _present_payload(user: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "id": user["id"],
        "name": user["name"],
        "avatar_url": user.get("avatar_url"),
        "providers": user.get("providers", []),
        "provider_names": _provider_names(user.get("providers", [])),
        "source": source,
        "ha_entity_id": user.get("ha_entity_id"),
        "tracker_state": user.get("tracker_state"),
    }


def _resolve_users(store: Store, identifiers: list[str]) -> list[dict[str, Any]]:
    users = store.list_users()
    by_id = {user["id"]: user for user in users}
    by_name = {user["name"].casefold(): user for user in users}
    resolved: list[dict[str, Any]] = []
    unknown: list[str] = []
    for raw in identifiers:
        key = raw.strip()
        user = by_id.get(key) or by_name.get(key.casefold())
        if user is None:
            unknown.append(key)
            continue
        if all(existing["id"] != user["id"] for existing in resolved):
            resolved.append(user)
    if unknown:
        raise HTTPException(
            status_code=404,
            detail=f"Unbekannte Nutzer: {', '.join(sorted(unknown))}",
        )
    return resolved


async def _resolve_present_users(request: Request) -> list[dict[str, Any]]:
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    ha: HAClient = request.app.state.ha
    if not settings.ha_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Home Assistant ist nicht konfiguriert. Bitte HA_BASE_URL und HA_TOKEN "
                "setzen oder im Request 'users' uebergeben."
            ),
        )
    try:
        states = await ha.states()
    except HAError as exc:
        raise HTTPException(status_code=502, detail=f"Home Assistant: {exc}") from exc

    present: list[dict[str, Any]] = []
    for user in store.list_users():
        tracker = HAClient.match_user_tracker(states, user)
        if HAClient.tracker_is_home(tracker):
            assert tracker is not None
            present.append(
                {
                    **user,
                    "source": "home_assistant",
                    "ha_entity_id": tracker.get("entity_id"),
                    "tracker_state": tracker.get("state"),
                }
            )
    return present


async def _link_home_assistant(
    request: Request, user: dict[str, Any], ip: str
) -> dict[str, Any]:
    """Tracker per IP suchen, ``custom_user_id`` verknuepfen und Ergebnis melden."""
    settings: Settings = request.app.state.settings
    store: Store = request.app.state.store
    ha: HAClient = request.app.state.ha

    result: dict[str, Any] = {
        "configured": settings.ha_configured,
        "matched": False,
        "linked": False,
        "entity_id": None,
        "state": None,
        "custom_user_id": user.get("custom_user_id"),
        "message": "",
    }
    if not settings.ha_configured:
        result["message"] = (
            "Home Assistant nicht konfiguriert (HA_BASE_URL/HA_TOKEN fehlen)."
        )
        return result

    try:
        states = await ha.states()
    except HAError as exc:
        result["message"] = f"Home Assistant nicht erreichbar: {exc}"
        return result

    tracker = HAClient.find_tracker_by_ip(states, ip) if ip else None
    if tracker is None:
        tracker = HAClient.match_user_tracker(states, user)
    if tracker is None:
        result["message"] = (
            f"Kein device_tracker mit IP {ip or '—'} gefunden. "
            "Der Tracker braucht ein 'ip'-Attribut."
        )
        return result

    entity_id = str(tracker.get("entity_id"))
    result["matched"] = True
    result["entity_id"] = entity_id
    result["state"] = tracker.get("state")

    custom_user_id = user.get("custom_user_id") or f"cinematch-{uuid.uuid4().hex[:8]}"
    store.set_user_link(
        user["id"], custom_user_id=custom_user_id, ha_entity_id=entity_id
    )
    result["custom_user_id"] = custom_user_id

    try:
        service = await ha.link_custom_user(entity_id, custom_user_id)
        result["linked"] = True
        result["message"] = f"Profil erfolgreich gespeichert und mit Home Assistant verknuepft!"
    except HAError as exc:
        # HA-Verknüpfung ist optional - Profil ist trotzdem gespeichert
        # IP-basierte Wiedererkennung funktioniert weiterhin
        result["linked"] = False
        result["message"] = (
            f"Profil erfolgreich gespeichert! Automatische Wiedererkennung "
            f"erfolgt ueber IP-Adresse ({ip}). "
            f"HA-Attribut-Sync optional fehlgeschlagen (nicht kritisch)."
        )
    return result


def _avatar_extension(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _remove_uploaded_avatar(settings: Settings, avatar_url: str | None) -> None:
    """Loescht nur selbst hochgeladene Dateien - niemals Preset-SVGs."""
    if not avatar_url:
        return
    path = avatar_url.split("?", 1)[0]
    prefix = "/static/avatars/uploads/"
    if not path.startswith(prefix):
        return
    relative = path[len(prefix):]
    if not relative or ".." in Path(relative).parts:
        return
    candidate = (settings.avatar_upload_dir / relative).resolve()
    try:
        candidate.relative_to(settings.avatar_upload_dir.resolve())
    except ValueError:
        return
    if candidate.is_file():
        candidate.unlink(missing_ok=True)


# ------------------------------------------------------------------- factory


def create_app(
    settings: Settings | None = None,
    *,
    tmdb_transport: Any | None = None,
    ha_transport: Any | None = None,
) -> FastAPI:
    """App-Factory (erlaubt Test-Injection von Settings und HTTP-Transporten)."""
    settings = settings or get_settings()
    settings.ensure_directories()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.database_file)
        purged = store.purge_quiz_sessions()
        if purged:
            logger.info("%s abgelaufene Quiz-Sessions entfernt.", purged)

        tmdb = TMDBClient.from_settings(settings, transport=tmdb_transport)
        ha = HAClient(
            settings.ha_base_url,
            settings.ha_token,
            timeout=settings.ha_timeout_seconds,
            verify_ssl=settings.ha_verify_ssl,
            link_service=settings.ha_link_service,
            transport=ha_transport,
        )
        app.state.store = store
        app.state.tmdb = tmdb
        app.state.ha = ha
        app.state.quiz = QuizService(
            tmdb,
            store,
            benchmark_ids=settings.quiz_benchmark_movies,
            max_answers=settings.quiz_max_answers,
        )
        app.state.engine = RecommendationEngine(tmdb, region=settings.tmdb_region)

        if not settings.tmdb_configured:
            logger.warning("TMDB_API_KEY/TMDB_READ_ACCESS_TOKEN fehlt - Quiz deaktiviert.")
        if not settings.ha_configured:
            logger.warning("HA_BASE_URL/HA_TOKEN fehlt - Presence-Tracking deaktiviert.")
        if not settings.admin_enabled:
            logger.warning("ADMIN_PASSWORD fehlt - /admin ist gesperrt.")
        elif settings.admin_password in {"changeme", "change-me", "admin"}:
            logger.warning("ADMIN_PASSWORD ist ein Standardwert - bitte aendern.")
        try:
            yield
        finally:
            await tmdb.aclose()
            await ha.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Gruppen-Filmempfehlungen fuer Home Assistant mit adaptivem Swipe-Quiz.",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.mount(
        "/static",
        StaticFiles(directory=str(settings.static_dir)),
        name="static",
    )

    # ---------------------------------------------------------- error mapping

    @app.exception_handler(TMDBError)
    async def _tmdb_error_handler(request: Request, exc: TMDBError) -> JSONResponse:
        logger.warning("TMDB-Fehler: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(HAError)
    async def _ha_error_handler(request: Request, exc: HAError) -> JSONResponse:
        logger.warning("Home-Assistant-Fehler: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(QuizError)
    async def _quiz_error_handler(request: Request, exc: QuizError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(RecommendationError)
    async def _recommendation_error_handler(
        request: Request, exc: RecommendationError
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    # ------------------------------------------------------------- pages

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/onboard", status_code=307)

    @app.get("/onboard", include_in_schema=False)
    async def onboard_page() -> FileResponse:
        return FileResponse(settings.static_dir / "onboard.html")

    @app.get("/admin", include_in_schema=False, dependencies=[Depends(require_admin)])
    async def admin_page() -> FileResponse:
        return FileResponse(settings.static_dir / "admin.html")

    # -------------------------------------------------------------- health

    @app.get("/api/v1/health", tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": settings.app_version,
            "time": utc_now(),
            "region": settings.tmdb_region,
            "tmdb_configured": settings.tmdb_configured,
            "home_assistant_configured": settings.ha_configured,
            "admin_configured": settings.admin_enabled,
            "users": store.count_users(),
        }

    @app.get("/api/v1/providers", tags=["system"])
    async def providers() -> dict[str, Any]:
        return {"providers": provider_public_list()}

    # ---------------------------------------------------------------- users

    @app.get("/api/v1/users", tags=["users"])
    async def public_users(request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        return {
            "users": [Store.public_user(user) for user in store.list_users()],
            "count": store.count_users(),
        }

    # ----------------------------------------------------------------- quiz

    @app.post("/api/v1/quiz/start", tags=["quiz"])
    async def quiz_start(payload: QuizStartRequest, request: Request) -> dict[str, Any]:
        quiz: QuizService = request.app.state.quiz
        return await quiz.start(name=payload.name, providers=payload.providers)

    @app.post("/api/v1/quiz/swipe", tags=["quiz"])
    async def quiz_swipe(payload: SwipeRequest, request: Request) -> dict[str, Any]:
        quiz: QuizService = request.app.state.quiz
        return await quiz.swipe(
            session_id=payload.session_id,
            tmdb_id=payload.tmdb_id,
            media_type=payload.media_type,
            action=payload.action,
        )

    @app.post("/api/v1/quiz/next", tags=["quiz"])
    async def quiz_next(payload: NextCardsRequest, request: Request) -> dict[str, Any]:
        quiz: QuizService = request.app.state.quiz
        return await quiz.next_cards(session_id=payload.session_id, limit=payload.limit)

    @app.post("/api/v1/quiz/complete", tags=["quiz"])
    async def quiz_complete(
        payload: QuizCompleteRequest, request: Request
    ) -> dict[str, Any]:
        settings_local: Settings = request.app.state.settings
        store: Store = request.app.state.store
        quiz: QuizService = request.app.state.quiz

        profile = await quiz.complete(session_id=payload.session_id)
        ip = extract_client_ip(request, settings_local.trust_forwarded_for)

        user, created = store.upsert_user(
            name=profile["name"],
            ip=ip or None,
            providers=profile["providers"],
            taste=profile["taste"],
            liked=profile["liked"],
            disliked=profile["disliked"],
            skipped=profile["skipped"],
            seen=profile["seen"],
        )
        ha_result = await _link_home_assistant(request, user, ip)
        store.delete_quiz_session(payload.session_id)
        user = store.get_user(user["id"]) or user

        return {
            "user": {
                **_admin_user_payload(user),
                "taste": user.get("taste") or {},
            },
            "user_created": created,
            "client_ip": ip,
            "home_assistant": ha_result,
        }

    # ------------------------------------------------------------- presence

    @app.get("/api/v1/presence", tags=["presence"])
    async def presence(request: Request) -> dict[str, Any]:
        users = await _resolve_present_users(request)
        return {
            "present_users": [_present_payload(user, "home_assistant") for user in users],
            "count": len(users),
            "checked_at": utc_now(),
        }

    # -------------------------------------------------------- recommendations

    @app.post(
        "/api/v1/recommendations",
        response_model=RecommendationResponse,
        tags=["recommendations"],
    )
    async def recommendations(
        payload: RecommendationRequest, request: Request
    ) -> dict[str, Any]:
        settings_local: Settings = request.app.state.settings
        store: Store = request.app.state.store
        engine: RecommendationEngine = request.app.state.engine

        if payload.users:
            users = _resolve_users(store, payload.users)
            presence_source = "manual"
        else:
            users = await _resolve_present_users(request)
            presence_source = "home_assistant"

        if not users:
            raise HTTPException(
                status_code=404,
                detail="Keine anwesenden, registrierten Personen gefunden.",
            )

        household = store.get_household_providers()
        if household is None:
            household = settings_local.household_default_providers

        result = await engine.recommend(
            present_users=users,
            media_type=payload.media_type,
            limit=payload.limit,
            household_providers=household,
        )
        return {
            **result,
            "media_type": payload.media_type,
            "presence_source": presence_source,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "present_users": [
                _present_payload(user, presence_source) for user in users
            ],
        }

    # ---------------------------------------------------------------- admin

    @app.get(
        "/admin/api/overview", tags=["admin"], dependencies=[Depends(require_admin)]
    )
    async def admin_overview(request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        household = store.get_household_providers()
        if household is None:
            household = settings.household_default_providers
        return {
            "app": {"name": settings.app_name, "version": settings.app_version},
            "providers": provider_public_list(),
            "household_providers": household,
            "household_provider_names": _provider_names(household),
            "users": [_admin_user_payload(user) for user in store.list_users()],
            "presets": PRESET_AVATARS,
            "status": {
                "tmdb_configured": settings.tmdb_configured,
                "home_assistant_configured": settings.ha_configured,
                "admin_user": settings.admin_user,
                "ha_link_service": settings.ha_link_service,
                "region": settings.tmdb_region,
                "database": str(settings.database_file),
            },
            "server_time": utc_now(),
        }

    @app.post(
        "/admin/api/household",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )
    async def admin_update_household(
        payload: HouseholdUpdateRequest, request: Request
    ) -> dict[str, Any]:
        store: Store = request.app.state.store
        store.set_household_providers(payload.providers)
        return {
            "providers": payload.providers,
            "provider_names": _provider_names(payload.providers),
            "updated_at": utc_now(),
        }

    @app.post(
        "/admin/api/users/{user_id}/avatar",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )
    async def admin_upload_avatar(
        user_id: str, request: Request, file: UploadFile = File(...)
    ) -> dict[str, Any]:
        store: Store = request.app.state.store
        settings_local: Settings = request.app.state.settings
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")

        raw = await file.read(settings_local.max_avatar_bytes + 1)
        if len(raw) > settings_local.max_avatar_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Avatar ist zu gross (max. "
                    f"{settings_local.max_avatar_bytes // (1024 * 1024)} MB)."
                ),
            )
        extension = _avatar_extension(raw)
        if extension is None or not raw:
            raise HTTPException(
                status_code=415,
                detail="Nur PNG, JPEG und WebP werden unterstuetzt.",
            )

        filename = f"{user_id}-{int(time.time() * 1000)}{extension}"
        target = settings_local.avatar_upload_dir / filename
        target.write_bytes(raw)

        avatar_url = f"/static/avatars/uploads/{filename}?v={int(time.time())}"
        store.set_user_avatar(user_id, avatar_url)
        _remove_uploaded_avatar(settings_local, user.get("avatar_url"))

        updated = store.get_user(user_id)
        return {
            "avatar_url": avatar_url,
            "user": _admin_user_payload(updated) if updated else None,
        }

    @app.post(
        "/admin/api/users/{user_id}/avatar/preset",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )
    async def admin_choose_preset(
        user_id: str, payload: PresetAvatarRequest, request: Request
    ) -> dict[str, Any]:
        store: Store = request.app.state.store
        settings_local: Settings = request.app.state.settings
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
        preset = next(
            (item for item in PRESET_AVATARS if item["id"] == payload.preset), None
        )
        if preset is None:
            raise HTTPException(status_code=404, detail="Avatar-Vorlage nicht gefunden.")

        store.set_user_avatar(user_id, preset["url"])
        _remove_uploaded_avatar(settings_local, user.get("avatar_url"))
        updated = store.get_user(user_id)
        return {
            "avatar_url": preset["url"],
            "user": _admin_user_payload(updated) if updated else None,
        }

    @app.delete(
        "/admin/api/users/{user_id}",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )
    async def admin_delete_user(user_id: str, request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        settings_local: Settings = request.app.state.settings
        removed = store.delete_user(user_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
        _remove_uploaded_avatar(settings_local, removed.get("avatar_url"))
        return {"deleted": True, "id": user_id, "name": removed["name"]}

    @app.post(
        "/admin/api/users/{user_id}/reset",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )
    async def admin_reset_user(user_id: str, request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        user = store.reset_user_profile(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")
        return {"reset": True, "user": _admin_user_payload(user)}

    app.state.quiz_max_answers = settings.quiz_max_answers
    app.state.media_types = list(MEDIA_TYPES)
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
