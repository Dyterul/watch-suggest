"""Empfehlungs-Engine, adaptives Swipe-Quiz und TMDB-Client.

Enthalten:
* :class:`TMDBClient` - asynchroner TMDB-Zugriff inkl. TTL-Cache.
* :class:`QuizService` - Benchmark-Karten, Live-Nachladen ueber
  ``/movie/{id}/recommendations``, Genre-Vektor und Profilbildung.
* :class:`RecommendationEngine` - strikte Provider-Schnittmenge,
  Least-Misery-Scoring und Ausschluss bereits bewerteter Titel.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from config import (
    PROVIDER_BY_SLUG,
    PROVIDER_BY_TMDB_ID,
    Settings,
)

MEDIA_TYPES = ("movie", "tv")
MEDIA_LABELS = {"movie": "Film", "tv": "Serie"}
SWIPE_ACTIONS = ("like", "dislike", "skip")
ACTION_WEIGHTS = {"like": 1.0, "skip": 0.0, "dislike": -0.65}

#: Film-Genres, die es bei Serien unter anderer ID gibt.
MOVIE_TO_TV_GENRES = {
    28: 10759,   # Action -> Action & Abenteuer
    12: 10759,   # Abenteuer -> Action & Abenteuer
    878: 10765,  # Science Fiction -> Sci-Fi & Fantasy
    14: 10765,   # Fantasy -> Sci-Fi & Fantasy
    10752: 10768,  # Krieg -> Krieg & Politik
}


class TMDBError(RuntimeError):
    """TMDB-Aufruf fehlgeschlagen."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class QuizError(RuntimeError):
    """Ungueltige Quiz-Operation."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class RecommendationError(RuntimeError):
    """Empfehlung kann nicht berechnet werden (z. B. kein Haushalts-Abo aktiv)."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def title_key(entry: dict[str, Any]) -> str:
    return f"{entry.get('media_type', 'movie')}:{int(entry.get('tmdb_id') or entry.get('id') or 0)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class _TTLCache:
    """Minimaler In-Memory-Cache fuer TMDB-Antworten."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = max(1, int(ttl_seconds))
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._values[key] = (time.monotonic() + self.ttl, value)


class TMDBClient:
    """Duenner async-Wrapper um die TMDB-v3-API."""

    def __init__(
        self,
        *,
        api_key: str = "",
        read_access_token: str = "",
        base_url: str = "https://api.themoviedb.org/3",
        image_base: str = "https://image.tmdb.org/t/p",
        language: str = "de-DE",
        region: str = "DE",
        timeout: float = 12.0,
        cache_ttl: int = 900,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.read_access_token = read_access_token
        self.base_url = base_url.rstrip("/")
        self.image_base = image_base.rstrip("/")
        self.language = language
        self.region = region.upper()
        self.timeout = timeout
        self._cache = _TTLCache(cache_ttl)
        self._genre_cache: dict[str, dict[int, str]] = {}
        self._http: httpx.AsyncClient | None = None
        self._transport = transport

    @classmethod
    def from_settings(
        cls, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> "TMDBClient":
        return cls(
            api_key=settings.tmdb_api_key,
            read_access_token=settings.tmdb_read_access_token,
            base_url=settings.tmdb_base_url,
            image_base=settings.tmdb_image_base,
            language=settings.tmdb_language,
            region=settings.tmdb_region,
            timeout=settings.tmdb_timeout_seconds,
            cache_ttl=settings.tmdb_cache_ttl_seconds,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.read_access_token)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=f"{self.base_url}/",
                timeout=httpx.Timeout(self.timeout),
                headers={"Accept": "application/json"},
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    def image_url(self, path: str | None, size: str = "w500") -> str | None:
        if not path:
            return None
        return f"{self.image_base}/{size}{path}"

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise TMDBError(
                "TMDB ist nicht konfiguriert. Bitte TMDB_API_KEY (oder "
                "TMDB_READ_ACCESS_TOKEN) setzen."
            )
        query = {k: v for k, v in (params or {}).items() if v is not None}
        query.setdefault("language", self.language)
        cache_key = f"{path}?{urlencode(sorted(query.items()))}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        headers: dict[str, str] = {}
        if self.read_access_token:
            headers["Authorization"] = f"Bearer {self.read_access_token}"
        else:
            query["api_key"] = self.api_key

        client = await self._client()
        try:
            response = await client.get(path, params=query, headers=headers)
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDB nicht erreichbar: {exc}") from exc

        if response.status_code == 401:
            raise TMDBError(
                "TMDB-Authentifizierung fehlgeschlagen (401). API-Key/Token pruefen.", 401
            )
        if response.status_code == 404:
            raise TMDBError("TMDB-Ressource nicht gefunden (404).", 404)
        if response.status_code == 429:
            raise TMDBError("TMDB-Rate-Limit erreicht (429). Bitte kurz warten.", 429)
        if response.status_code >= 400:
            raise TMDBError(
                f"TMDB antwortet mit {response.status_code}: {response.text[:200]}",
                response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise TMDBError("TMDB lieferte ungueltiges JSON.") from exc
        if isinstance(data, dict):
            self._cache.set(cache_key, data)
        return data if isinstance(data, dict) else {}

    async def genres(self, media_type: str) -> dict[int, str]:
        """Genre-ID -> deutscher Name (gecacht fuer die Prozesslaufzeit)."""
        if media_type not in MEDIA_TYPES:
            raise ValueError(f"Unbekannter media_type: {media_type}")
        if media_type in self._genre_cache:
            return self._genre_cache[media_type]
        data = await self._request(f"genre/{media_type}/list")
        mapping = {
            int(item["id"]): str(item.get("name", ""))
            for item in data.get("genres", [])
            if item.get("id") is not None
        }
        if mapping:
            self._genre_cache[media_type] = mapping
        return mapping

    async def details(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        return await self._request(f"{media_type}/{tmdb_id}")

    async def recommendations(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        return await self._request(f"{media_type}/{tmdb_id}/recommendations", {"page": 1})

    async def discover(
        self,
        media_type: str,
        *,
        providers: list[int] | None = None,
        genres: list[int] | None = None,
        page: int = 1,
        sort_by: str = "popularity.desc",
        min_votes: int = 30,
        min_rating: float = 5.0,
        monetization: str = "flatrate",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "watch_region": self.region,
            "sort_by": sort_by,
            "page": page,
            "include_adult": "false",
            "vote_count.gte": min_votes,
            "vote_average.gte": min_rating,
        }
        if providers:
            params["with_watch_providers"] = ",".join(str(pid) for pid in providers)
            params["with_watch_monetization_types"] = monetization
        if genres:
            params["with_genres"] = "|".join(str(gid) for gid in genres)
        return await self._request(f"discover/{media_type}", params)

    async def watch_providers(self, media_type: str, tmdb_id: int) -> dict[str, Any]:
        return await self._request(f"{media_type}/{tmdb_id}/watch/providers")


class QuizService:
    """Serverseitige, adaptive Quiz-Logik auf Basis der TMDB-API."""

    def __init__(
        self,
        tmdb: TMDBClient,
        store: Any,
        *,
        benchmark_ids: list[int] | None = None,
        max_answers: int = 20,
    ) -> None:
        self.tmdb = tmdb
        self.store = store
        self.benchmark_ids = benchmark_ids or [27205, 120467, 129, 419430, 313369]
        self.max_answers = max(1, int(max_answers))

    # ------------------------------------------------------------- cards

    def _card(self, media_type: str, obj: dict[str, Any], genre_map: dict[int, str]) -> dict[str, Any]:
        raw_genres = obj.get("genres") or []
        if raw_genres:
            genre_ids = [
                int(item["id"]) for item in raw_genres if isinstance(item, dict) and item.get("id")
            ]
            names = [
                str(item.get("name") or genre_map.get(int(item.get("id", 0)), ""))
                for item in raw_genres
                if isinstance(item, dict)
            ]
        else:
            genre_ids = [int(gid) for gid in (obj.get("genre_ids") or [])]
            names = [genre_map.get(gid, "") for gid in genre_ids]
        names = [name for name in names if name]

        release = str(obj.get("release_date") or obj.get("first_air_date") or "")
        year: int | None = None
        if len(release) >= 4 and release[:4].isdigit():
            year = int(release[:4])
        title = obj.get("title") or obj.get("name") or "Unbekannter Titel"
        overview = (obj.get("overview") or "").strip()
        if len(overview) > 650:
            overview = overview[:647].rstrip() + "…"

        return {
            "tmdb_id": int(obj.get("id") or 0),
            "media_type": media_type,
            "media_label": MEDIA_LABELS[media_type],
            "title": title,
            "year": year,
            "overview": overview,
            "poster_path": obj.get("poster_path"),
            "poster_url": self.tmdb.image_url(obj.get("poster_path"), "w500"),
            "backdrop_url": self.tmdb.image_url(obj.get("backdrop_path"), "w1280"),
            "vote_average": round(float(obj.get("vote_average") or 0), 1),
            "vote_count": int(obj.get("vote_count") or 0),
            "popularity": round(float(obj.get("popularity") or 0), 1),
            "genre_ids": genre_ids,
            "genres": names[:3],
        }

    # ------------------------------------------------------------ session

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.store.get_quiz_session(session_id)
        if session is None:
            raise QuizError(
                "Quiz-Session nicht gefunden oder abgelaufen. Bitte neu starten.", 404
            )
        return session

    def _taste(self, answers: list[dict[str, Any]], genre_map: dict[int, str]) -> dict[str, Any]:
        vector: dict[int, float] = defaultdict(float)
        for answer in answers:
            factor = ACTION_WEIGHTS.get(str(answer.get("action")), 0.0)
            if factor == 0:
                continue
            for genre_id in answer.get("genre_ids") or []:
                vector[int(genre_id)] += factor
        if not vector:
            return {"genres": {}, "top": []}
        peak = max(abs(value) for value in vector.values()) or 1.0
        normalized = {
            str(gid): round(value / peak, 3)
            for gid, value in sorted(vector.items(), key=lambda item: -abs(item[1]))
        }
        top = [
            {"id": gid, "name": genre_map.get(gid, f"Genre {gid}"), "weight": round(value / peak, 3)}
            for gid, value in sorted(vector.items(), key=lambda item: -item[1])
            if value > 0
        ][:5]
        return {"genres": normalized, "top": top}

    def _taste_summary(self, taste: dict[str, Any], answers: int) -> dict[str, Any]:
        return {
            "swipes": answers,
            "top_genres": taste.get("top", []),
        }

    # --------------------------------------------------------------- flow

    async def start(self, *, name: str, providers: list[str]) -> dict[str, Any]:
        genre_map = await self.tmdb.genres("movie")
        results = await asyncio.gather(
            *(self.tmdb.details("movie", movie_id) for movie_id in self.benchmark_ids),
            return_exceptions=True,
        )
        cards: list[dict[str, Any]] = []
        errors: list[str] = []
        for movie_id, result in zip(self.benchmark_ids, results):
            if isinstance(result, Exception):
                errors.append(f"{movie_id}: {result}")
                continue
            card = self._card("movie", result, genre_map)
            if card["tmdb_id"]:
                cards.append(card)
        if not cards:
            detail = errors[0] if errors else "unbekannter Fehler"
            raise TMDBError(
                "Benchmark-Filme konnten nicht von TMDB geladen werden. "
                f"Bitte API-Key/PIN pruefen ({detail})."
            )
        cards = cards[:5]

        state: dict[str, Any] = {
            "answers": [],
            "queue": cards,
            "seen": [title_key(card) for card in cards],
            "taste": {"genres": {}, "top": []},
            "started_at": _utc_now(),
        }
        session_id = self.store.create_quiz_session(
            name=name, providers=providers, state=state
        )
        return {
            "session_id": session_id,
            "cards": cards,
            "max_answers": self.max_answers,
            "total_benchmarks": len(cards),
        }

    async def swipe(
        self,
        *,
        session_id: str,
        tmdb_id: int,
        media_type: str = "movie",
        action: str,
    ) -> dict[str, Any]:
        if action not in SWIPE_ACTIONS:
            raise QuizError("action muss 'like', 'dislike' oder 'skip' sein.", 422)
        if media_type not in MEDIA_TYPES:
            raise QuizError("media_type muss 'movie' oder 'tv' sein.", 422)

        session = self._require_session(session_id)
        state: dict[str, Any] = session["state"]
        key = f"{media_type}:{int(tmdb_id)}"
        if key not in state.get("seen", []):
            raise QuizError("Diese Karte gehoert nicht zu dieser Quiz-Session.", 409)

        card = next(
            (item for item in state.get("queue", []) if title_key(item) == key), None
        )
        if card is None:
            previous = next(
                (item for item in state.get("answers", []) if title_key(item) == key), None
            )
            if previous is None:
                raise QuizError("Karte wurde nicht gefunden.", 409)
            card = {k: v for k, v in previous.items() if k not in {"action", "answered_at"}}

        answer = {
            "tmdb_id": int(card["tmdb_id"]),
            "media_type": card["media_type"],
            "title": card["title"],
            "poster_url": card.get("poster_url"),
            "vote_average": card.get("vote_average", 0),
            "genre_ids": card.get("genre_ids", []),
            "genres": card.get("genres", []),
            "action": action,
            "answered_at": _utc_now(),
        }
        answers = [item for item in state.get("answers", []) if title_key(item) != key]
        answers.append(answer)
        state["answers"] = answers
        state["queue"] = [
            item for item in state.get("queue", []) if title_key(item) != key
        ]

        genre_map = await self.tmdb.genres("movie")
        state["taste"] = self._taste(answers, genre_map)
        self.store.update_quiz_session(session_id, state)

        return {
            "session_id": session_id,
            "swipes": len(answers),
            "remaining": len(state["queue"]),
            "done": len(answers) >= self.max_answers,
            "max_answers": self.max_answers,
            "taste": self._taste_summary(state["taste"], len(answers)),
        }

    async def next_cards(self, *, session_id: str, limit: int = 3) -> dict[str, Any]:
        session = self._require_session(session_id)
        state: dict[str, Any] = session["state"]
        answers = state.get("answers", [])
        if len(answers) >= self.max_answers:
            return {
                "cards": [],
                "swipes": len(answers),
                "done": True,
                "remaining": len(state.get("queue", [])),
                "taste": self._taste_summary(state.get("taste", {}), len(answers)),
            }

        target = max(limit, 4)
        await self._fill_queue(state, target)
        self.store.update_quiz_session(session_id, state)
        cards = state.get("queue", [])[: max(1, limit)]
        return {
            "cards": cards,
            "swipes": len(answers),
            "done": len(answers) >= self.max_answers,
            "remaining": len(state.get("queue", [])),
            "taste": self._taste_summary(state.get("taste", {}), len(answers)),
        }

    async def _fill_queue(self, state: dict[str, Any], target: int) -> None:
        """Fuellt die Warteschlange adaptiv nach.

        Quellen in dieser Reihenfolge:
        1. ``/{media_type}/{id}/recommendations`` fuer die zuletzt gelikten Titel.
        2. ``/discover/movie`` mit den positiv gewichteten Genres.
        3. Beliebte Filme als Fallback, damit das Quiz nie leer laeuft.
        """
        queue: list[dict[str, Any]] = state.get("queue", [])
        if len(queue) >= target:
            return
        genre_map = await self.tmdb.genres("movie")
        excluded = set(state.get("seen", [])) | {title_key(card) for card in queue}
        answers = state.get("answers", [])

        def fresh(obj: dict[str, Any]) -> dict[str, Any] | None:
            card = self._card("movie", obj, genre_map)
            if not card["tmdb_id"]:
                return None
            key = title_key(card)
            if key in excluded:
                return None
            excluded.add(key)
            return card

        liked = [item for item in answers if item.get("action") == "like"]

        # 1) Ähnliche Titel zu den letzten Likes.
        for seed in reversed(liked[-2:]):
            if len(queue) >= target:
                break
            try:
                data = await self.tmdb.recommendations(
                    seed.get("media_type", "movie"), int(seed["tmdb_id"])
                )
            except TMDBError:
                continue
            for obj in (data.get("results") or [])[:12]:
                if len(queue) >= target:
                    break
                card = fresh(obj)
                if card:
                    queue.append(card)
                    state.setdefault("seen", []).append(title_key(card))

        # 2) Genre-Vektor ausbauen.
        if len(queue) < target:
            taste = state.get("taste") or {}
            positive = [
                int(genre["id"])
                for genre in sorted(
                    taste.get("top", []), key=lambda item: -float(item.get("weight", 0))
                )
                if float(genre.get("weight", 0)) > 0
            ][:3]
            if positive:
                for page in (1, 2):
                    if len(queue) >= target:
                        break
                    try:
                        data = await self.tmdb.discover(
                            "movie",
                            genres=positive,
                            page=page,
                            min_votes=50,
                            min_rating=5.5,
                        )
                    except TMDBError:
                        continue
                    for obj in (data.get("results") or [])[:20]:
                        if len(queue) >= target:
                            break
                        card = fresh(obj)
                        if card:
                            queue.append(card)
                            state.setdefault("seen", []).append(title_key(card))

        # 3) Fallback: beliebte, gut bewertete Filme.
        if len(queue) < target:
            for page in (1, 2):
                if len(queue) >= target:
                    break
                try:
                    data = await self.tmdb.discover(
                        "movie", page=page, min_votes=100, min_rating=6.0
                    )
                except TMDBError:
                    continue
                for obj in (data.get("results") or [])[:20]:
                    if len(queue) >= target:
                        break
                    card = fresh(obj)
                    if card:
                        queue.append(card)
                        state.setdefault("seen", []).append(title_key(card))

        state["queue"] = queue

    async def complete(self, *, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        state = session["state"]
        answers = state.get("answers", [])
        if not answers:
            raise QuizError("Es wurden noch keine Karten bewertet.", 409)
        genre_map = await self.tmdb.genres("movie")
        taste = state.get("taste") or self._taste(answers, genre_map)

        def entries(action: str) -> list[dict[str, Any]]:
            return [
                {
                    "tmdb_id": int(item["tmdb_id"]),
                    "media_type": item.get("media_type", "movie"),
                    "title": item.get("title", ""),
                }
                for item in answers
                if item.get("action") == action
            ]

        return {
            "session_id": session_id,
            "name": session["name"],
            "providers": session["providers"],
            "taste": taste,
            "liked": entries("like"),
            "disliked": entries("dislike"),
            "skipped": entries("skip"),
            "seen": [],
            "swipes": len(answers),
        }


class RecommendationEngine:
    """Least-Misery-Gruppenempfehlungen mit striktem Haushalts-Provider-Filter."""

    def __init__(self, tmdb: TMDBClient, *, region: str = "DE") -> None:
        self.tmdb = tmdb
        self.region = region.upper()

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _entry_key(entry: Any, default_type: str = "movie") -> str:
        if isinstance(entry, dict):
            media_type = entry.get("media_type", default_type)
            tmdb_id = entry.get("tmdb_id") or entry.get("id")
            return f"{media_type}:{int(tmdb_id)}"
        return f"{default_type}:{int(entry)}"

    def _excluded_keys(self, users: list[dict[str, Any]]) -> set[str]:
        excluded: set[str] = set()
        for user in users:
            for field in ("seen", "disliked", "liked"):
                for entry in user.get(field) or []:
                    try:
                        excluded.add(self._entry_key(entry))
                    except (TypeError, ValueError):
                        continue
        return excluded

    def _taste_vector(self, user: dict[str, Any], media_type: str) -> dict[int, float]:
        taste = user.get("taste") or {}
        genres = taste.get("genres") or {}
        vector: dict[int, float] = {}
        for genre_id, weight in genres.items():
            try:
                gid = int(genre_id)
                value = float(weight)
            except (TypeError, ValueError):
                continue
            if media_type == "tv":
                gid = MOVIE_TO_TV_GENRES.get(gid, gid)
            vector[gid] = vector.get(gid, 0.0) + value
        return vector

    def _genre_filter(self, users: list[dict[str, Any]], media_type: str) -> list[int]:
        combined: dict[int, float] = defaultdict(float)
        for user in users:
            for gid, weight in self._taste_vector(user, media_type).items():
                if weight > 0:
                    combined[gid] += weight
        ranked = sorted(combined.items(), key=lambda item: -item[1])
        return [gid for gid, _ in ranked[:4]]

    @staticmethod
    def _affinity(vector: dict[int, float], genre_ids: list[int], vote_average: float) -> float:
        base = 0.5
        if vector and genre_ids:
            weights = [vector.get(gid, 0.0) for gid in genre_ids]
            affinity = sum(weights) / len(weights)
            base = _clamp(0.5 + affinity * 0.5)
        return _clamp(0.72 * base + 0.28 * _clamp(vote_average / 10.0))

    # ------------------------------------------------------------ fetching

    async def _collect(
        self,
        media_type: str,
        *,
        provider_ids: list[int],
        genres: list[int] | None,
        pages: int,
        excluded: set[str],
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        seen: set[int] = set()
        for page in range(1, max(1, pages) + 1):
            data = await self.tmdb.discover(
                media_type,
                providers=provider_ids,
                genres=genres,
                page=page,
                min_votes=40,
                min_rating=5.5,
            )
            for item in data.get("results") or []:
                try:
                    tmdb_id = int(item.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if tmdb_id <= 0 or tmdb_id in seen or item.get("adult"):
                    continue
                seen.add(tmdb_id)
                if f"{media_type}:{tmdb_id}" in excluded:
                    continue
                collected.append(dict(item))
            if page >= int(data.get("total_pages") or 1):
                break
        return collected

    async def _verify_providers(
        self,
        media_type: str,
        candidates: list[dict[str, Any]],
        *,
        active_provider_ids: set[int],
    ) -> tuple[list[tuple[dict[str, Any], list[str]]], int]:
        """Prueft jeden Kandidaten gegen die DE-Flatrate-Anbieter von TMDB.

        ``/discover`` filtert bereits, aber nur so ist garantiert, dass jeder
        ausgelieferte Titel wirklich bei einem aktiven Haushalts-Abo laeuft.
        """
        semaphore = asyncio.Semaphore(6)

        async def check(candidate: dict[str, Any]) -> tuple[dict[str, Any], list[str]] | None:
            async with semaphore:
                try:
                    data = await self.tmdb.watch_providers(media_type, int(candidate["id"]))
                except TMDBError:
                    return None
            region_data = (data.get("results") or {}).get(self.region) or {}
            names: list[str] = []
            for entry in region_data.get("flatrate") or []:
                try:
                    provider_id = int(entry.get("provider_id") or 0)
                except (TypeError, ValueError):
                    continue
                if provider_id not in active_provider_ids:
                    continue
                provider = PROVIDER_BY_TMDB_ID.get(provider_id)
                name = provider["name"] if provider else str(entry.get("provider_name") or "")
                if name and name not in names:
                    names.append(name)
            if not names:
                return None
            return candidate, names

        results = await asyncio.gather(*(check(candidate) for candidate in candidates))
        verified = [item for item in results if item is not None]
        return verified, len(candidates)

    # ------------------------------------------------------------- scoring

    @staticmethod
    def _score_title(
        candidate: dict[str, Any],
        present_users: list[dict[str, Any]],
        vectors: list[dict[int, float]],
        genre_map: dict[int, str],
        providers: list[str],
    ) -> dict[str, Any]:
        genre_ids = [int(gid) for gid in (candidate.get("genre_ids") or [])]
        vote_average = float(candidate.get("vote_average") or 0)
        vote_count = int(candidate.get("vote_count") or 0)
        popularity = float(candidate.get("popularity") or 0)

        per_user = [
            RecommendationEngine._affinity(vector, genre_ids, vote_average)
            for vector in vectors
        ] or [0.5]
        least = min(per_user)
        mean = sum(per_user) / len(per_user)
        popularity_norm = _clamp(math.log1p(popularity) / math.log1p(2000))
        vote_norm = _clamp(vote_average / 10)

        # Least Misery: der unzufriedenste Gast zaehlt am meisten.
        score = 0.52 * least + 0.26 * mean + 0.14 * vote_norm + 0.08 * popularity_norm
        genre_names = [genre_map[gid] for gid in genre_ids if gid in genre_map][:2]

        if len(present_users) > 1:
            reason = f"Bester Kompromiss für {len(present_users)} Personen"
        else:
            reason = f"Starker Match für {present_users[0]['name']}"
        if genre_names:
            reason += f" · {genre_names[0]}"
        if providers:
            reason += f" · auf {providers[0]}"

        release = str(candidate.get("release_date") or candidate.get("first_air_date") or "")
        year = int(release[:4]) if len(release) >= 4 and release[:4].isdigit() else None
        poster_path = candidate.get("poster_path")
        overview = (candidate.get("overview") or "").strip()

        return {
            "tmdb_id": int(candidate.get("id") or 0),
            "media_type": candidate.get("_media_type", "movie"),
            "title": candidate.get("title") or candidate.get("name") or "Unbekannter Titel",
            "year": year,
            "overview": overview[:650].rstrip() + ("…" if len(overview) > 650 else ""),
            "poster_path": poster_path,
            "poster_url": None,
            "backdrop_url": None,
            "vote_average": round(vote_average, 1),
            "vote_count": vote_count,
            "popularity": round(popularity, 1),
            "genres": genre_names,
            "providers": providers,
            "match_percent": int(round(_clamp(score) * 100)),
            "score": round(score, 4),
            "reason": reason,
        }

    # --------------------------------------------------------------- public

    async def recommend(
        self,
        *,
        present_users: list[dict[str, Any]],
        media_type: str,
        limit: int,
        household_providers: list[str],
    ) -> dict[str, Any]:
        if media_type not in MEDIA_TYPES:
            raise RecommendationError("media_type muss 'movie' oder 'tv' sein.", 422)
        if not present_users:
            raise RecommendationError(
                "Keine anwesenden Nutzer gefunden. Bitte Nutzer uebergeben oder "
                "Profile mit erreichbaren privaten IP-Adressen anlegen.",
                404,
            )

        household = list(
            dict.fromkeys(
                slug for slug in household_providers if slug in PROVIDER_BY_SLUG
            )
        )
        if not household:
            raise RecommendationError(
                "Im Admin-Dashboard sind keine Haushalts-Abos aktiviert.", 409
            )
        active_provider_ids = {
            int(PROVIDER_BY_SLUG[slug]["tmdb_id"]) for slug in household
        }
        profile_provider_slugs = {
            slug
            for user in present_users
            for slug in (user.get("providers") or [])
            if slug in PROVIDER_BY_SLUG
        }
        genre_map = await self.tmdb.genres(media_type)
        excluded = self._excluded_keys(present_users)
        genre_filter = self._genre_filter(present_users, media_type)

        candidates = await self._collect(
            media_type,
            provider_ids=sorted(active_provider_ids),
            genres=genre_filter or None,
            pages=2,
            excluded=excluded,
        )
        verified, checked = await self._verify_providers(
            media_type, candidates, active_provider_ids=active_provider_ids
        )
        verified_keys = {int(candidate["id"]) for candidate, _ in verified}

        # Falls der Genrefilter zu eng war: breitere Auswahl nachladen.
        if len(verified) < limit:
            extra_excluded = excluded | {f"{media_type}:{key}" for key in verified_keys}
            extra = await self._collect(
                media_type,
                provider_ids=sorted(active_provider_ids),
                genres=None,
                pages=2,
                excluded=extra_excluded,
            )
            more_verified, more_checked = await self._verify_providers(
                media_type, extra, active_provider_ids=active_provider_ids
            )
            verified.extend(more_verified)
            checked += more_checked

        vectors = [self._taste_vector(user, media_type) for user in present_users]
        scored: list[dict[str, Any]] = []
        for candidate, providers in verified:
            candidate = dict(candidate)
            candidate["_media_type"] = media_type
            scored.append(
                self._score_title(candidate, present_users, vectors, genre_map, providers)
            )
        scored = [
            result
            for result in scored
            if f"{media_type}:{result['tmdb_id']}" not in excluded
        ]
        for result in scored:
            result["poster_url"] = self.tmdb.image_url(result["poster_path"], "w500")
            result["backdrop_url"] = self.tmdb.image_url(
                next(
                    (
                        candidate.get("backdrop_path")
                        for candidate, _ in verified
                        if int(candidate.get("id") or 0) == result["tmdb_id"]
                    ),
                    None,
                ),
                "w1280",
            )
        scored.sort(
            key=lambda item: (item["score"], item["vote_average"], item["popularity"]),
            reverse=True,
        )
        results = scored[: max(1, int(limit))]

        return {
            "results": results,
            "active_providers": [
                {"slug": slug, "name": PROVIDER_BY_SLUG[slug]["name"]}
                for slug in PROVIDER_BY_SLUG
                if slug in profile_provider_slugs
            ],
            "household_providers": [
                {"slug": slug, "name": PROVIDER_BY_SLUG[slug]["name"]}
                for slug in household
            ],
            "excluded_titles": len(excluded),
            "candidates_checked": checked,
            "genre_filter": [genre_map.get(gid, str(gid)) for gid in genre_filter],
        }
